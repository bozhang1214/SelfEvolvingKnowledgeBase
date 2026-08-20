"""
Prometheus 指标暴露路由（Phase 4 监控系统）。

提供 ``GET /metrics`` 端点，返回 Prometheus 文本格式指标数据，
供 Prometheus 抓取。

路由设计：
    - 路径为 ``/metrics``（不带 /api/v1 前缀），符合 Prometheus 默认约定
    - 响应 Content-Type 为 ``text/plain; version=0.0.4; charset=utf-8``
    - 不需要鉴权（监控端点），但建议在生产环境通过网络策略限制访问
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST

from app.core.metrics import get_metrics

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
async def metrics() -> Response:
    """
    Prometheus 指标端点。

    返回当前进程所有已注册指标的文本格式数据，
    供 Prometheus server 定期抓取。

    Returns:
        ``text/plain; version=0.0.4; charset=utf-8`` 格式的指标数据
    """
    return Response(
        content=get_metrics(),
        media_type=CONTENT_TYPE_LATEST,
    )
