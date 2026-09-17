"""端云路由的可观测接口（M1）。

对应 `docs/RFC-端云协同与端侧Agent.md` §4.4 与 §4.5-B/H：

- ``GET  /api/v1/edge/routes/stats``  端侧完成率 / 升级率 / 分布（**两个北极星指标**）
- ``GET  /api/v1/edge/routes``        最近的路由事件（含"为什么走这边"）
- ``POST /api/v1/edge/route-events``  外部端（Android，M2）上报自己的路由事件

为什么需要一个"上报"入口
------------------------
端侧自己跑的推理不经过服务端，服务端**看不到**它（§4.5-H：端侧成本/延迟也要记账）。
所以端侧把它自己的决策与结果上报过来，两端事件汇到同一个流水里，"端侧完成率/升级率"
才是完整口径；上报按 ``event_id`` 幂等，端侧离线重传不会污染统计（§4.5-B）。
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.access import require_full_access
from app.core.logging import get_logger
from app.storage.edge_route_storage import EdgeRouteStore

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/edge", tags=["edge"])

#: 进程内共享（路由事件是 append-only 流水，单 worker 部署下够用；
#: 多 worker / 多端汇聚需要换外部存储，见 RFC §11 风险）
_store: EdgeRouteStore | None = None


def get_store() -> EdgeRouteStore:
    global _store
    if _store is None:
        _store = EdgeRouteStore()
    return _store


class RouteEventIn(BaseModel):
    """端侧上报的一条路由事件（字段与 RouteEvent 对齐）。"""

    event_id: str = Field("", description="幂等键；同一事件重传不会重复入库")
    role: str = Field("", description="角色/任务名")
    plane: str = Field("edge", description="edge | cloud")
    reason: str = Field("", description="为什么这样决策")
    model: str = ""
    tier: str = "default"
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    escalated: bool = False
    escalate_reason: str = ""
    signals: list[str] = Field(default_factory=list)
    versions: dict[str, str] = Field(default_factory=dict)


@router.get("/routes/stats")
async def route_stats(_: str = Depends(require_full_access)) -> dict[str, Any]:
    """端云路由的两个核心指标与分布。

    - ``edge_completion_rate``：端侧处理且**没被升级**的比例 → 端侧够不够用
    - ``escalation_rate``：端侧处理但**失败升级**的比例 → 判得准不准
    """
    return get_store().stats()


@router.get("/routes")
async def list_routes(
    limit: int = Query(50, ge=1, le=500),
    _: str = Depends(require_full_access),
) -> dict[str, Any]:
    """最近的路由事件（倒序），用于"这次为什么走端/走云"的可解释性。"""
    rows = get_store().recent(limit=limit)
    return {"count": len(rows), "events": rows}


@router.post("/route-events")
async def report_route_event(
    body: RouteEventIn,
    _: str = Depends(require_full_access),
) -> dict[str, Any]:
    """端侧上报路由事件（幂等）。重复上报返回 ``duplicated=True`` 且不写库。"""
    if body.plane not in ("edge", "cloud"):
        raise HTTPException(422, "plane 只能是 edge 或 cloud")
    payload = body.model_dump()
    payload.setdefault("ts", time.time())
    payload["source"] = "device"          # 与"服务端自己产生的事件"区分开，便于排查
    written = await get_store().append(payload, event_id=body.event_id)
    if not written and body.event_id:
        return {"status": "ok", "duplicated": True, "event_id": body.event_id}
    return {"status": "ok", "duplicated": False, "event_id": body.event_id}
