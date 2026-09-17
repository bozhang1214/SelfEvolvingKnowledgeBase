"""
FastAPI 应用入口模块

实现 ``create_app()`` 工厂函数，构建并返回配置完整的 FastAPI 实例：
- 配置 CORS 中间件（源从 ``config.api.cors_origins`` 读取）
- 注册全部路由（chat / conversations / health）
- 通过 lifespan 钩子完成应用初始化（``initialize_app``）与关闭（``shutdown_app``）
- 全局异常处理器将 ``SEKBError`` 子类映射为合适的 HTTP 状态码

由于 FastAPI 不支持直接依赖注入异步初始化的对象，使用模块级全局变量
``_context`` 持有 ``AppContext``，路由通过 ``Depends(get_app_context)`` 获取。

启动方式：
    uvicorn app.api.server:app --host 0.0.0.0 --port 8000

配置文件路径默认为 ``config.yaml``，可通过环境变量 ``SEKB_CONFIG_PATH`` 覆盖。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.api.middleware import RateLimitMiddleware, setup_cors
from app.core.bootstrap import AppContext, initialize_app, shutdown_app
from app.core.config import AppConfig, get_config
from app.core.exceptions import (
    BudgetExceededError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
    SecurityError,
    SEKBError,
    StorageError,
    ToolError,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

# ============================================================
# 全局 AppContext 持有
# ============================================================

_context: AppContext | None = None


def get_app_context() -> AppContext:
    """
    依赖注入入口：返回当前已初始化的 AppContext。

    供所有路由通过 ``Depends(get_app_context)`` 获取上下文。
    在 lifespan startup 阶段设置，shutdown 阶段清空。

    Returns:
        已初始化的 AppContext

    Raises:
        RuntimeError: 应用尚未初始化（startup 未完成或已 shutdown）
    """
    global _context
    if _context is None:
        raise RuntimeError("应用未初始化：AppContext 尚未装配（请检查 lifespan startup）")
    return _context


# ============================================================
# SEKBError → HTTP 状态码映射
# ============================================================

def _status_code_for_error(err: SEKBError) -> int:
    """
    将 SEKBError 子类映射为对应的 HTTP 状态码。

    映射规则：
        - LLMRateLimitError         → 429 Too Many Requests
        - LLMTimeoutError           → 504 Gateway Timeout
        - LLMError                  → 502 Bad Gateway
        - ToolError / MCPError      → 502 Bad Gateway
        - SecurityError             → 403 Forbidden
        - BudgetExceededError       → 429 Too Many Requests
        - StorageError（含"不存在"）→ 404 Not Found
        - StorageError（其他）       → 500 Internal Server Error
        - ConfigError               → 500 Internal Server Error
        - SEKBMemoError / 其他      → 500 Internal Server Error
    """
    if isinstance(err, LLMRateLimitError):
        return 429
    if isinstance(err, LLMTimeoutError):
        return 504
    if isinstance(err, LLMError):
        return 502
    if isinstance(err, ToolError):
        return 502
    if isinstance(err, SecurityError):
        return 403
    if isinstance(err, BudgetExceededError):
        return 429
    if isinstance(err, StorageError):
        return 404 if "不存在" in err.message else 500
    # ConfigError / SEKBMemoError / AgentError / EvaluationError / 其他 SEKBError
    return 500


# ============================================================
# 应用生命周期
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    应用生命周期管理。

    startup：
        - 读取配置路径（环境变量 ``SEKB_CONFIG_PATH`` 优先，默认 ``config.yaml``）
        - 调用 ``initialize_app`` 装配所有运行时组件
        - 将 AppContext 写入全局 ``_context``

    shutdown：
        - 调用 ``shutdown_app`` 释放资源
        - 清空全局 ``_context``
    """
    global _context

    config_path = os.getenv("SEKB_CONFIG_PATH", "config.yaml")

    logger.info("FastAPI 应用启动中", config_path=config_path)
    _context = await initialize_app(config_path)

    # 启动科技资讯定时调度（若启用）
    if _context.news_agent is not None:
        from app.scheduler.scheduler import NewsScheduler

        _context.news_scheduler = NewsScheduler(_context.config.news, _context.news_agent)
        _context.news_scheduler.start()

    logger.info(
        "FastAPI 应用启动完成",
        app=_context.config.app.name,
        version=_context.config.app.version,
        host=_context.config.api.host,
        port=_context.config.api.port,
    )

    try:
        yield
    finally:
        logger.info("FastAPI 应用关闭中")
        if _context is not None:
            if _context.news_scheduler is not None:
                _context.news_scheduler.shutdown()
            # 关闭常驻的内核 MCP 子进程（P4：分析链路走 jobcopilot-mcp）
            try:
                from app.agents.job.mcp_client import close_shared_kernel

                await close_shared_kernel()
            except Exception as e:  # noqa: BLE001
                logger.warning("关闭内核 MCP 连接失败（忽略）", error=str(e)[:120])
            await shutdown_app(_context)
        _context = None
        logger.info("FastAPI 应用已关闭")


# ============================================================
# 应用工厂
# ============================================================

def create_app() -> FastAPI:
    """
    创建并配置 FastAPI 应用实例。

    流程：
        1. 加载配置以读取应用元信息与 CORS 源
        2. 创建 FastAPI 实例（title / version / description / lifespan）
        3. 配置 CORS 中间件
        4. 注册全局 SEKBError 异常处理器
        5. 注册所有路由（chat / conversations / health）

    Returns:
        配置完成的 FastAPI 实例
    """
    # 加载配置以获取元信息与 CORS 源（lru_cache 单例）
    config: AppConfig = get_config()

    app = FastAPI(
        title=config.app.name,
        version=config.app.version,
        description=(
            "自迭代个人知识库 Agent - 基于 LangGraph 的多智能体知识助手。\n\n"
            "提供聊天（含 SSE 流式）、会话管理与健康检查接口。"
        ),
        lifespan=lifespan,
    )

    # ---------- CORS 中间件 ----------
    setup_cors(app)

    # ---------- 限流中间件（纯 ASGI 实现，不缓冲 SSE 流式响应） ----------
    # 由 config.api.rate_limit.enabled 控制开关；默认关闭，开启前需验证 SSE 流式不受影响。
    if config.api.rate_limit.enabled:
        app.add_middleware(
            RateLimitMiddleware,
            default_limit=config.api.rate_limit.requests_per_minute,
            default_window_seconds=60,
            route_limits={
                "/api/v1/auth/": config.api.auth.rate_limit_login_per_minute,
                "/api/v1/share/": config.api.rate_limit.share_per_minute,
                "/api/v1/upload": config.api.rate_limit.upload_per_minute,
                "/api/v1/job/": config.api.rate_limit.job_per_minute,
                # 资讯读取：宽额度（前端会轮询 /status + 读列表正文）
                "/api/v1/news/": config.api.rate_limit.news_per_minute,
                # 资讯生成：窄额度（POST 才真的调 LLM；带方法的组优先匹配）
                "POST /api/v1/news/": config.api.rate_limit.news_generate_per_minute,
            },
        )


    # ---------- 全局异常处理器：SEKBError ----------
    @app.exception_handler(SEKBError)
    async def sekb_error_handler(_request: Request, exc: SEKBError) -> JSONResponse:
        """将 SEKBError 转换为带合适状态码的 JSON 响应。"""
        status_code = _status_code_for_error(exc)
        logger.warning(
            "请求处理抛出 SEKBError",
            error_type=type(exc).__name__,
            message=exc.message,
            status_code=status_code,
            details=exc.details,
        )
        return JSONResponse(
            status_code=status_code,
            content={
                "error": type(exc).__name__,
                "message": exc.message,
                "details": exc.details,
            },
        )

    # ---------- 全局异常处理器：HTTPException（5xx 出口脱敏） ----------
    @app.exception_handler(HTTPException)
    async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
        """`HTTPException` 的 5xx 只回通用文案 + error_id（SEC-04）。

        为什么在出口处做：业务路由里有 20+ 处 `raise HTTPException(500, f"... {e}")`，
        会把内部异常原文（文件路径/表名/组件名）直接返回客户端。逐个改调用点易漏，
        统一在这里脱敏即可根治。4xx 属业务语义（如「任务正在生成中」），保持原样。
        """
        if exc.status_code < 500:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

        import uuid as _uuid

        error_id = _uuid.uuid4().hex[:12]
        logger.warning(
            "请求处理返回 5xx（详情已脱敏）",
            status_code=exc.status_code,
            error_id=error_id,
            detail=str(exc.detail)[:300],
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": "服务内部错误，请稍后重试", "error_id": error_id},
        )

    # ---------- 全局兜底异常处理器 ----------
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        _request: Request, exc: Exception
    ) -> JSONResponse:
        """捕获所有未处理异常，返回 500 并记录日志。

        安全：响应只返回脱敏的 error_id，异常原文仅进日志，不外泄（SEC-04）。
        """
        import uuid as _uuid

        error_id = _uuid.uuid4().hex[:12]
        logger.error(
            "未处理异常",
            error_id=error_id,
            error=str(exc),
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": "InternalServerError",
                "error_id": error_id,
                "message": "服务内部错误，请稍后重试",
            },
        )

    # ---------- 注册路由 ----------
    # 延迟导入：避免在模块加载阶段触发路由对 get_app_context 的解析失败
    from app.api.routes.auth import router as auth_router
    from app.api.routes.chat import router as chat_router
    from app.api.routes.chat_share import router as chat_share_router
    from app.api.routes.conversations import router as conv_router
    from app.api.routes.edge import router as edge_router
    from app.api.routes.health import router as health_router
    from app.api.routes.job import router as job_router
    from app.api.routes.knowledge import router as knowledge_router
    from app.api.routes.metrics import router as metrics_router
    from app.api.routes.monitoring import router as monitoring_router
    from app.api.routes.news import router as news_router
    from app.api.routes.profile import router as profile_router
    from app.api.routes.share import router as share_router
    from app.api.routes.upload import router as upload_router

    app.include_router(auth_router)
    app.include_router(chat_router)
    app.include_router(chat_share_router)
    app.include_router(conv_router)
    app.include_router(edge_router)
    app.include_router(health_router)
    app.include_router(job_router)
    app.include_router(knowledge_router)
    app.include_router(metrics_router)
    app.include_router(monitoring_router)
    app.include_router(news_router)
    app.include_router(profile_router)
    app.include_router(share_router)
    app.include_router(upload_router)

    logger.info(
        "FastAPI 路由注册完成",
        routers=[
            "auth", "chat", "chat-share", "conversations", "edge", "health", "job",
            "knowledge", "metrics", "monitoring", "news", "profile", "share", "upload",
        ],
        cors_origins=config.api.cors_origins or ["*"],
    )

    return app


# ============================================================
# 全局 app 实例（延迟创建，避免导入时触发配置加载）
# ============================================================

# 修复 Qoder 4.14：模块加载时立即 create_app() 会触发 get_config()，
# 若 config.yaml 缺失或环境变量未设置，导入即失败，影响测试与工具命令。
# 改为延迟创建：首次访问 app 时才构建。

_app: FastAPI | None = None


def get_app() -> FastAPI:
    """
    获取（并在首次调用时创建）FastAPI 应用实例。

    延迟创建避免模块导入阶段触发配置加载与副作用，
    便于在缺少 config.yaml 的环境中导入模块（如测试、CLI 帮助渲染）。

    Returns:
        配置完成的 FastAPI 实例
    """
    global _app
    if _app is None:
        _app = create_app()
    return _app


def reset_app() -> None:
    """
    重置缓存的 app 实例（仅供测试使用）。

    下次调用 get_app() 时会重新创建。
    """
    global _app
    _app = None


# 兼容 uvicorn app.app 的导入约定：暴露名为 `app` 的属性，
# 但通过 __getattr__ 延迟求值，不在模块加载阶段触发 create_app()。
def __getattr__(name: str):
    if name == "app":
        return get_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
