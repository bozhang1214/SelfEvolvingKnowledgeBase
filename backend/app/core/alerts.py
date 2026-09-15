"""告警外发：把「任务失败」这类**用户能感知**的问题推到飞书。

复用既有的 `deploy/feishu-webhook` 网关（FastAPI，`POST /webhook`，接受 Alertmanager
格式载荷并渲染成飞书卡片）——它原本只被 Alertmanager 使用，这里让应用侧也能用，
避免再写一份飞书签名逻辑。

网关侧的字段约定（读 `deploy/feishu-webhook/feishu_gateway.py` 得到，改动这里要同步）：

- ``labels.alertname``：卡片标题行；网关按它查「这是什么/建议」释义表；
- ``labels.severity``：``critical`` 渲染红色标题，其余渲染橙色；
- ``labels.source``：来源模块（``news`` 等），网关会展示并在有配置时给「查看资讯」按钮；
- ``labels.service``：服务名，卡片上展示；
- ``annotations.summary`` / ``annotations.description``：摘要与详情。

设计原则（很重要）：

- **绝不抛异常**：告警是旁路能力，发不出去不能影响主流程（生成报告本身）；
- **可配置且可关闭**：``NEWS_ALERT_WEBHOOK_URL`` 覆盖默认地址，设为 ``off``/``none``/``0`` 即关闭；
- **同一故障去重**：任务失败会重试（调度器 RETRY_TIMES），重试必然重复报同一错误，
  所以相同 (source, title, detail) 在 ``DEDUP_WINDOW_S`` 内只推一次，避免刷屏。
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

#: 默认指向监控栈里的飞书网关（backend 与它同网，实测可解析并返回 200）
DEFAULT_ALERT_WEBHOOK_URL = "http://sekb-feishu-webhook:5001/webhook"
#: 告警发送超时（秒）—— 短超时，避免拖慢任务收尾
ALERT_TIMEOUT_S = 5.0
#: 同一故障的去重窗口（秒）：重试/重复点击导致的同错只推一次
DEDUP_WINDOW_S = 300.0

#: 去重记忆：key -> 上次发送时间戳（进程内，多 worker 各自去重，够用）
_LAST_SENT: dict[str, float] = {}
#: 持有后台告警任务的强引用：`create_task` 的返回值只被弱引用，
#: 不持有的话任务可能在发送前就被 GC 掉（这是 asyncio 的已知坑）。
_PENDING: set[asyncio.Task[Any]] = set()
#: 视为「关闭告警」的取值。为什么要有这个：compose 里用的是
#: ``${NEWS_ALERT_WEBHOOK_URL:-默认网关}``，空串会被解析成默认值（不能用来关闭），
#: 于是给一个明确的关闭开关，比让人填一个坏 URL 靠谱。
_DISABLED_VALUES = {"off", "none", "false", "0", "disabled", "no"}


def alert_webhook_url() -> str:
    """当前生效的告警地址（``NEWS_ALERT_WEBHOOK_URL`` 优先，关闭则返回空串）。"""
    raw = os.environ.get("NEWS_ALERT_WEBHOOK_URL", DEFAULT_ALERT_WEBHOOK_URL).strip()
    return "" if raw.lower() in _DISABLED_VALUES else raw


def _dedup_key(source: str, title: str, detail: str) -> str:
    return f"{source}|{title}|{detail[:120]}"


def _should_send(key: str, now: float | None = None) -> bool:
    """去重判定：窗口内已发过则返回 False（同时更新记忆）。"""
    now = time.monotonic() if now is None else now
    last = _LAST_SENT.get(key)
    if last is not None and (now - last) < DEDUP_WINDOW_S:
        return False
    _LAST_SENT[key] = now
    # 记忆表很小，但长跑进程里 key 会随错误文本变化累积，做个上限清理
    if len(_LAST_SENT) > 200:
        for k, ts in sorted(_LAST_SENT.items(), key=lambda kv: kv[1])[:100]:
            _LAST_SENT.pop(k, None)
    return True


def reset_dedup() -> None:
    """清空去重记忆（测试用）。"""
    _LAST_SENT.clear()


def fire_and_forget(coro: Any) -> None:
    """在**同步**代码里安全地发起一个协程（没有事件循环就只记日志，不抛异常）。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("当前没有事件循环，告警已丢弃（不影响主流程）")
        coro.close()
        return
    task = loop.create_task(coro)
    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)


async def send_alert(
    title: str,
    detail: str = "",
    *,
    source: str = "sekb",
    severity: str = "critical",
) -> bool:
    """把一条告警推到飞书网关。

    Args:
        title: 告警标题（网关按它做 alertname → 释义表查找，建议写成稳定短语）。
        detail: 详细说明（失败原因等）。
        source: 来源模块（``news`` / ``job`` 等），网关会展示并给出对应入口按钮。
        severity: Alertmanager 标准取值：``critical``（红）/ ``warning`` / ``info``（橙）。

    Returns:
        是否发送成功；**失败只记日志，绝不抛出**。
    """
    key = _dedup_key(source, title, detail)
    if not _should_send(key):
        logger.info("同类告警已在窗口内发送过，跳过", title=title, source=source)
        return False

    url = alert_webhook_url()
    if not url:
        logger.info("告警已关闭（NEWS_ALERT_WEBHOOK_URL 为空）", title=title)
        return False

    payload: dict[str, Any] = {
        "version": "4",
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": title,
                    "severity": severity,
                    "source": source,
                    "service": "sekb",
                },
                "annotations": {
                    "summary": title,
                    "description": detail or title,
                },
            }
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=ALERT_TIMEOUT_S) as client:
            resp = await client.post(url, json=payload)
        if resp.status_code >= 400:
            logger.warning("告警发送失败", status=resp.status_code, title=title)
            return False
        logger.info("告警已发送", title=title, source=source)
        return True
    except Exception as e:  # noqa: BLE001 - 旁路能力，任何异常都不能影响主流程
        logger.warning("告警发送异常", title=title, error=f"{type(e).__name__}: {str(e)[:120]}")
        return False
