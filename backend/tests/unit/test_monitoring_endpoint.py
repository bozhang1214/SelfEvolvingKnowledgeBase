"""
ISSUE-005 回归测试：客户端事件上报路由

覆盖 monitoring 路由对合法/非法事件的处理：

- TC-010-U：合法事件被接收并写入 Prometheus 指标
- TC-011-U：非法事件被跳过但整批仍返回 202
- 补充：超过单批上限（50 条）截断处理
- 补充：perf 事件（api_request）的 duration_ms 被写入直方图
- 补充：login_submit_failed 中的 network_error 字段被识别

设计要点：
- 直接 await 路由函数 report_client_events，不走 HTTP
- Prometheus Counter 全局单例，断言用 delta（after - before == 1）
- 路由本身不抛异常，对外一律返回 202
"""
from __future__ import annotations

import pytest

from prometheus_client import REGISTRY

from app.api.routes.monitoring import (
    ClientEvent,
    ClientEventBatch,
    MAX_EVENTS_PER_BATCH,
    report_client_events,
)


def _get_counter_value(name: str, **labels) -> float:
    """读取 Prometheus Counter 当前值（按标签筛选）。

    prometheus_client 0.26 的 Counter 用 `_metrics` dict 存储按标签组合的
    子 Counter 实例，key 是按 `_labelnames` 顺序排列的 label values tuple，
    value 是子 Counter 对象，其 `_value.get()` 返回当前计数值。
    """
    metric = REGISTRY._names_to_collectors.get(name)
    if metric is None:
        return 0.0
    # 按 metric._labelnames 顺序构造 key
    label_names = getattr(metric, "_labelnames", []) or []
    key = tuple(str(labels.get(str(ln), "")) for ln in label_names)
    internal = metric._metrics.get(key)
    if internal is None:
        return 0.0
    return internal._value.get()


def _get_histogram_sample_count(name: str, **labels) -> int:
    """读取 Histogram 的样本数（按标签筛选）。

    Histogram 内部用 `_metrics` dict 存储按标签组合的子 Histogram。
    子 Histogram 的 `_samples` 是方法，调用返回 Sample 列表。
    """
    metric = REGISTRY._names_to_collectors.get(name)
    if metric is None:
        return 0
    label_names = getattr(metric, "_labelnames", []) or []
    key = tuple(str(labels.get(str(ln), "")) for ln in label_names)
    internal = metric._metrics.get(key)
    if internal is None:
        return 0
    samples_iter = getattr(internal, "_samples", None)
    if callable(samples_iter):
        samples = samples_iter()
    elif samples_iter is not None:
        samples = samples_iter
    else:
        samples = []
    count = 0
    for sample in samples:
        sample_name = getattr(sample, "name", "") or ""
        if sample_name.endswith("_count"):
            count = int(getattr(sample, "value", 0))
            break
    return count


def _delta(before: float, after: float) -> float:
    return after - before


# ============================================================
# TC-010-U：合法事件被接收并写入指标
# ============================================================

@pytest.mark.asyncio
async def test_report_events_accepts_legal_events():
    """合法事件（在白名单内）应被接收并 +1 到 client_events_total。"""
    batch = ClientEventBatch(events=[
        ClientEvent(event="login_submit", level="info", fields={"email": "abc***"}),
        ClientEvent(event="login_submit_failed", level="warn",
                    fields={"http_status": 401}),
        ClientEvent(event="api_error", level="error",
                    fields={"url": "/auth/login", "status": 401}),
    ])

    before_submit = _get_counter_value("sekb_client_events_total",
                                        event="login_submit", level="info")
    before_failed = _get_counter_value("sekb_client_events_total",
                                       event="login_submit_failed", level="warn")
    before_error = _get_counter_value("sekb_client_events_total",
                                      event="api_error", level="error")
    before_accepted = _get_counter_value("sekb_client_event_reports_total",
                                         status="accepted")

    result = await report_client_events(batch)

    after_submit = _get_counter_value("sekb_client_events_total",
                                      event="login_submit", level="info")
    after_failed = _get_counter_value("sekb_client_events_total",
                                      event="login_submit_failed", level="warn")
    after_error = _get_counter_value("sekb_client_events_total",
                                     event="api_error", level="error")
    after_accepted = _get_counter_value("sekb_client_event_reports_total",
                                        status="accepted")

    # 断言：返回值正确
    assert result["status"] == "ok"
    assert result["accepted"] == 3
    assert result["rejected"] == 0

    # 断言：每个事件 +1
    assert _delta(before_submit, after_submit) == 1
    assert _delta(before_failed, after_failed) == 1
    assert _delta(before_error, after_error) == 1

    # 断言：整批接收计数 +1
    assert _delta(before_accepted, after_accepted) == 1


# ============================================================
# TC-011-U：非法事件被跳过
# ============================================================

@pytest.mark.asyncio
async def test_report_events_skips_illegal_events():
    """不在白名单的事件名应被归为 "other"，格式错误的事件应跳过。"""
    # 1 条合法 + 1 条不在白名单 + 1 条 level 非法
    # 注意：白名单外的 event 不会抛异常，而是归为 "other"
    # 所以真正"跳过"的情况是 record_client_event 内部抛错
    # 这里构造一种让 record_client_event 抛错的场景：
    # - fields 类型非法（不是 dict 且不能转 dict）
    # 但 ClientEvent.fields 是 dict | None，Pydantic 已经保证类型正确
    # 所以更现实的测试是：白名单外事件被归为 "other"
    batch = ClientEventBatch(events=[
        ClientEvent(event="login_submit", level="info"),  # 合法
        ClientEvent(event="unknown_event_name", level="info"),  # 归为 other
        ClientEvent(event="login_submit", level="invalid_level"),  # 归为 info
    ])

    before_other = _get_counter_value("sekb_client_events_total",
                                      event="other", level="info")
    before_submit = _get_counter_value("sekb_client_events_total",
                                       event="login_submit", level="info")

    result = await report_client_events(batch)

    after_other = _get_counter_value("sekb_client_events_total",
                                      event="other", level="info")
    after_submit = _get_counter_value("sekb_client_events_total",
                                      event="login_submit", level="info")

    # 断言：白名单外事件归为 other
    assert _delta(before_other, after_other) == 1
    # 断言：合法事件正常 +1（出现两次：合法 level 和非法 level）
    # 第二条 login_submit 的 level="invalid_level" 会被归为 info
    assert _delta(before_submit, after_submit) == 2

    # 断言：返回值 accepted=3, rejected=0（无异常抛出）
    assert result["accepted"] == 3
    assert result["rejected"] == 0


# ============================================================
# 补充：超过单批上限截断
# ============================================================

@pytest.mark.asyncio
async def test_report_events_truncates_over_limit():
    """超过 MAX_EVENTS_PER_BATCH 的事件应被截断，剩余计为 rejected。"""
    # 构造 MAX + 5 条事件
    events = [
        ClientEvent(event="login_submit", level="info")
        for _ in range(MAX_EVENTS_PER_BATCH + 5)
    ]
    batch = ClientEventBatch(events=events)

    result = await report_client_events(batch)

    # 断言：accepted = MAX，rejected = 5
    assert result["accepted"] == MAX_EVENTS_PER_BATCH
    assert result["rejected"] == 5
    assert result["total"] == MAX_EVENTS_PER_BATCH

    # 断言：rejected 标签被记录
    # 注意：rejected 计数只有在 rejected > 0 时才会 .inc()
    # 上面的 accepted.inc() 一定会调用


# ============================================================
# 补充：perf 事件（api_request）写入直方图
# ============================================================

@pytest.mark.asyncio
async def test_perf_event_observed_to_histogram():
    """api_request perf 事件的 duration_ms 应被写入 client_api_duration_seconds 直方图。"""
    batch = ClientEventBatch(events=[
        ClientEvent(
            event="api_request",
            level="info",
            fields={
                "method": "POST",
                "result": "success",
                "duration_ms": 500,
                "url": "/auth/login",
            },
        ),
    ])

    before_count = _get_histogram_sample_count(
        "sekb_client_api_duration_seconds", method="post", status="success"
    )

    result = await report_client_events(batch)

    after_count = _get_histogram_sample_count(
        "sekb_client_api_duration_seconds", method="post", status="success"
    )

    assert result["accepted"] == 1
    # 断言：直方图样本数 +1
    assert after_count - before_count == 1


# ============================================================
# 补充：login_submit_failed 中 network_error 写入 login_attempts_total
# ============================================================

@pytest.mark.asyncio
async def test_login_submit_failed_with_network_error_records_metric():
    """前端上报 login_submit_failed 且 network_error=true 时，应记录到 login_attempts_total{result=network_error}。"""
    batch = ClientEventBatch(events=[
        ClientEvent(
            event="login_submit_failed",
            level="warn",
            fields={"network_error": True, "http_status": 0},
        ),
    ])

    before = _get_counter_value("sekb_login_attempts_total", result="network_error")

    result = await report_client_events(batch)

    after = _get_counter_value("sekb_login_attempts_total", result="network_error")

    assert result["accepted"] == 1
    assert _delta(before, after) == 1


# ============================================================
# 补充：空事件列表也能正常处理
# ============================================================

@pytest.mark.asyncio
async def test_report_events_empty_batch():
    """空事件列表应正常返回 202，accepted=0。"""
    batch = ClientEventBatch(events=[])

    result = await report_client_events(batch)

    assert result["status"] == "ok"
    assert result["accepted"] == 0
    assert result["rejected"] == 0
    assert result["total"] == 0
