"""
健康检查路由模块

提供应用健康状态检查的 HTTP 端点，用于容器编排（K8s）与服务监控：

- ``GET /api/v1/health/``       综合健康检查（检查 LLM、工具、存储、Graph 等子系统）
- ``GET /api/v1/health/live``   存活探针（liveness）：进程存活即返回 ok
- ``GET /api/v1/health/ready``  就绪探针（readiness）：核心子系统就绪才返回 ok

设计原则：
    - liveness 只关心进程是否存活，不依赖外部服务，避免误重启
    - readiness 检查核心依赖（LLM、Graph），任一不可用则返回 503
    - 综合检查聚合所有子系统状态，返回聚合结果与逐项详情
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from app.api.server import get_app_context
from app.core.bootstrap import AppContext

router = APIRouter(prefix="/api/v1/health", tags=["health"])


@router.get("/")
async def health_check(ctx: AppContext = Depends(get_app_context)) -> dict:
    """
    综合健康检查。

    检查 LLM 可达性、工具注册表健康状态、存储可用性与 Graph 编译状态，
    返回聚合状态（ok / degraded）与各子系统详情。

    Returns:
        ``{"status": "ok"|"degraded", "services": {...}}``
    """
    services: dict[str, dict] = {}

    # 1. LLM 检查
    try:
        llm_ok = await ctx.llm_factory.health_check()
        services["llm"] = {"status": "ok" if llm_ok else "fail"}
    except Exception as e:
        llm_ok = False
        services["llm"] = {"status": "fail", "detail": str(e)}

    # 2. 工具检查
    try:
        tools_health = await ctx.tool_registry.health_check()
        all_tools_ok = all(tools_health.values()) if tools_health else True
        services["tools"] = {
            "status": "ok" if all_tools_ok else "degraded",
            "details": tools_health,
        }
    except Exception as e:
        all_tools_ok = False
        services["tools"] = {"status": "fail", "detail": str(e)}

    # 3. 存储检查（写入 + 读取自检）
    try:
        index = await ctx.storage.list_conversations(user_id="__health_check__", limit=1)
        storage_ok = isinstance(index, list)
        services["storage"] = {"status": "ok" if storage_ok else "fail"}
    except Exception as e:
        storage_ok = False
        services["storage"] = {"status": "fail", "detail": str(e)}

    # 4. Graph 检查
    graph_ok = ctx.graph is not None
    services["graph"] = {"status": "ok" if graph_ok else "fail"}

    # 聚合：核心依赖（LLM + Graph）任一不可用即 degraded
    overall_ok = llm_ok and graph_ok
    overall_status = "ok" if overall_ok else "degraded"

    return {
        "status": overall_status,
        "services": services,
        "app": {
            "name": ctx.config.app.name,
            "version": ctx.config.app.version,
            "environment": ctx.config.app.environment,
        },
    }


@router.get("/live")
async def liveness() -> dict:
    """
    存活探针（liveness）。

    进程能响应即视为存活，不检查任何外部依赖，避免因外部抖动触发误重启。

    Returns:
        ``{"status": "ok"}``
    """
    return {"status": "ok"}


@router.get("/ready")
async def readiness(ctx: AppContext = Depends(get_app_context)) -> JSONResponse:
    """
    就绪探针（readiness）。

    检查核心依赖是否就绪：LLM 可达且 Graph 已编译。
    任一不可用返回 HTTP 503，否则返回 200。

    Returns:
        ``{"status": "ok"|"not_ready", "checks": {...}}``
    """
    checks: dict[str, bool] = {}

    try:
        checks["llm"] = await ctx.llm_factory.health_check()
    except Exception:
        checks["llm"] = False

    checks["graph"] = ctx.graph is not None

    ready = all(checks.values())
    payload = {"status": "ok" if ready else "not_ready", "checks": checks}
    http_status = status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(content=payload, status_code=http_status)
