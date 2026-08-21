"""
客户端事件上报路由（Phase 4 监控系统扩展）。

接收前端 logger 批量上报的事件，转写为 Prometheus 指标。

设计要点：
- 路径 ``/api/v1/monitoring/client-event``，POST 批量事件
- 不强制鉴权（兼容未登录场景的 login_submit_failed 上报），
  生产环境建议通过 Nginx 限流 + 网络策略保护
- 单次最多 50 条事件，防止滥用
- 字段校验失败的事件跳过，不影响其他事件处理
- 处理失败一律返回 202（已接收），避免前端重试风暴
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.metrics import (
    client_event_reports_total,
    record_client_event,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/monitoring", tags=["monitoring"])

# 单批最多 50 条事件，避免前端误传大数据
MAX_EVENTS_PER_BATCH = 50


class ClientEvent(BaseModel):
    """单个客户端事件。"""

    ts: str | None = None       # ISO 时间戳（前端生成，仅作记录）
    level: str = Field(default="info")
    event: str
    fields: dict[str, Any] | None = None


class ClientEventBatch(BaseModel):
    """客户端事件批量上报。"""

    events: list[ClientEvent]


@router.post("/client-event", status_code=202)
async def report_client_events(batch: ClientEventBatch) -> dict:
    """
    接收前端批量上报的客户端事件。

    前端 ``logger.ts`` 在事件队列满或定时刷新时调用此接口，
    将 warn/error 级别的事件和 perf 性能数据上报到后端。

    返回 ``202 Accepted``（无论内部是否部分失败），避免前端重试。
    """
    accepted = 0
    rejected = 0
    total = len(batch.events)

    if total > MAX_EVENTS_PER_BATCH:
        # 截断：只处理前 MAX 条，其余计为 rejected
        events = batch.events[:MAX_EVENTS_PER_BATCH]
        rejected = total - MAX_EVENTS_PER_BATCH
        total = MAX_EVENTS_PER_BATCH
    else:
        events = batch.events

    for ev in events:
        try:
            record_client_event(
                event=ev.event,
                level=ev.level,
                fields=ev.fields,
            )
            accepted += 1
        except Exception as e:  # noqa: BLE001
            rejected += 1
            logger.warning("客户端事件处理失败", event=ev.event, error=str(e))

    # 上报链路健康度计数
    client_event_reports_total.labels(status="accepted").inc()
    if rejected > 0:
        client_event_reports_total.labels(status="rejected").inc()

    return {
        "status": "ok",
        "accepted": accepted,
        "rejected": rejected,
        "total": total,
    }
