"""定时任务**不再静默失败**：失败重试 + 状态落盘 + 接口可见。

背景（owner 报的 bug）：9/15 的日报任务在 08:00 确实触发了，但抛异常中断，
只在服务端日志里留了一行 APScheduler 的 "raised an exception"，**用户完全看不到**，
只能靠「咦今天怎么没有日报」发现。另外三个任务都没设 `misfire_grace_time`
（默认 1 秒）——容器恰好在触发时刻前后重启，这次运行会被**静默跳过**。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.scheduler.scheduler import NewsScheduler


def _config(tmp_path) -> SimpleNamespace:
    return SimpleNamespace(
        enabled=True,
        report_dir=str(tmp_path / "news"),
        daily_cron="0 8 * * *",
        weekly_cron="0 8 * * 1",
        monthly_cron="0 8 * 1 * *",
        timezone="Asia/Shanghai",
    )


def _scheduler(tmp_path) -> NewsScheduler:
    s = NewsScheduler(_config(tmp_path), news_agent=SimpleNamespace())
    s.RETRY_DELAY_S = 0  # 测试里不等 60 秒
    return s


# ---------- 状态落盘 ----------


def test_record_status_writes_file(tmp_path) -> None:
    s = _scheduler(tmp_path)
    s._record_status("daily", ok=True, period="2026-09-15", started_at="t0", duration_s=12.3)
    data = json.loads((tmp_path / "news" / "last_status.json").read_text(encoding="utf-8"))
    assert data["kind"] == "daily"
    assert data["ok"] is True
    assert data["period"] == "2026-09-15"
    assert data["duration_s"] == 12.3
    assert data["finished_at"]


def test_record_status_failure_has_error(tmp_path) -> None:
    s = _scheduler(tmp_path)
    s._record_status("weekly", ok=False, error="SSLError: 连接被重置")
    data = json.loads((tmp_path / "news" / "last_status.json").read_text(encoding="utf-8"))
    assert data["ok"] is False
    assert "SSLError" in data["error"]


def test_record_status_survives_unwritable_dir(tmp_path) -> None:
    """落盘失败不能把任务本身搞挂（例如目录权限异常）。"""
    cfg = _config(tmp_path)
    cfg.report_dir = "/proc/不可写/目录"
    s = NewsScheduler(cfg, news_agent=SimpleNamespace())
    s._record_status("daily", ok=True)  # 不应抛异常


# ---------- 失败重试 ----------


@pytest.mark.asyncio
async def test_run_guarded_retries_then_succeeds(tmp_path) -> None:
    """第一次失败、第二次成功 → 最终记为成功（网络抖动场景）。"""
    s = _scheduler(tmp_path)
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("TLS 读超时")
        return {"period": "2026-09-15"}

    await s._run_guarded("daily", flaky)
    data = json.loads((tmp_path / "news" / "last_status.json").read_text(encoding="utf-8"))
    assert calls["n"] == 2, "应当重试一次"
    assert data["ok"] is True
    assert data["period"] == "2026-09-15"


@pytest.mark.asyncio
async def test_run_guarded_records_failure_after_retries(tmp_path) -> None:
    """重试用尽仍失败 → 状态记为失败并带原因（这就是「今天没日报」的可见化）。"""
    s = _scheduler(tmp_path)

    async def always_fail():
        raise RuntimeError("LLM 调用失败: Request timed out.")

    await s._run_guarded("weekly", always_fail)
    data = json.loads((tmp_path / "news" / "last_status.json").read_text(encoding="utf-8"))
    assert data["ok"] is False
    assert "timed out" in data["error"]


# ---------- misfire 宽限 ----------


def test_job_kwargs_relax_misfire_grace(tmp_path) -> None:
    """默认 misfire_grace_time 只有 1 秒：容器在触发时刻重启就会静默跳过本次运行。"""
    kw = _scheduler(tmp_path)._job_kwargs()
    assert kw["misfire_grace_time"] == NewsScheduler.MISFIRE_GRACE_S
    assert kw["misfire_grace_time"] >= 600
    assert kw["coalesce"] is True
    assert kw["max_instances"] == 1


# ---------- 接口不被 catch-all 吃掉 ----------


def test_status_route_declared_before_periodic_catch_all() -> None:
    """`/status` 必须在 `/{report_type}` **之前**声明，否则会被后者匹配成 report_type。"""
    from app.api.routes.news import router

    get_paths = [
        getattr(r, "path", "") for r in router.routes if "GET" in getattr(r, "methods", set())
    ]
    assert "/api/v1/news/status" in get_paths
    assert get_paths.index("/api/v1/news/status") < get_paths.index("/api/v1/news/{report_type}")


def test_agent_read_status_missing_file(tmp_path) -> None:
    """没有状态文件时返回 None（首次运行/旧版本），不能报错。"""
    from app.agents.news.service import NewsAgent

    agent = NewsAgent.__new__(NewsAgent)  # 不跑 __init__（避免拉起 fetcher）
    agent._config = _config(tmp_path)
    assert agent.read_status() is None


def test_agent_read_status_parses(tmp_path) -> None:
    from app.agents.news.service import NewsAgent

    d = tmp_path / "news"
    d.mkdir(parents=True)
    (d / "last_status.json").write_text('{"ok": false, "error": "boom"}', encoding="utf-8")
    agent = NewsAgent.__new__(NewsAgent)
    agent._config = _config(tmp_path)
    assert agent.read_status() == {"ok": False, "error": "boom"}


def test_agent_read_status_corrupt_file(tmp_path) -> None:
    from app.agents.news.service import NewsAgent

    d = tmp_path / "news"
    d.mkdir(parents=True)
    (d / "last_status.json").write_text("{坏 JSON", encoding="utf-8")
    agent = NewsAgent.__new__(NewsAgent)
    agent._config = _config(tmp_path)
    assert agent.read_status() is None
