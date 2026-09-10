"""
链路追踪模块（Phase 3：LangSmith）

启动时按配置初始化 LangSmith 追踪。本地 JSON trace（LocalTraceCollector）原为
无消费方的降级方案，按 D6 决策删除。

使用方式：
    from app.core.tracing import setup_tracing
    setup_tracing()  # 在应用启动时调用一次
"""
from __future__ import annotations

from typing import Any

from app.core.config import AppConfig, get_config
from app.core.logging import get_logger

logger = get_logger(__name__)


def setup_tracing(config: AppConfig | None = None) -> bool:
    """
    初始化链路追踪（LangSmith），返回是否真正启用。

    Args:
        config: 应用配置，若为 None 则自动加载

    Returns:
        True 表示 LangSmith 追踪已启用；False 表示未配置 API Key 或未启用。
    """
    if config is None:
        config = get_config()

    tracing_config = config.tracing

    if tracing_config.provider == "langsmith" and tracing_config.langsmith.api_key:
        try:
            import os

            os.environ["LANGSMITH_API_KEY"] = tracing_config.langsmith.api_key
            os.environ["LANGSMITH_PROJECT"] = tracing_config.langsmith.project
            os.environ["LANGSMITH_ENDPOINT"] = tracing_config.langsmith.endpoint
            os.environ["LANGCHAIN_TRACING_V2"] = "true"

            logger.info(
                "LangSmith 追踪已启用",
                project=tracing_config.langsmith.project,
            )
            return True
        except Exception as e:
            logger.warning("LangSmith 启用失败", error=str(e))
    else:
        logger.info("追踪未启用")
    return False


def get_trace_config() -> dict[str, Any]:
    """
    构造 LangChain RunnableConfig，把当前 trace_id / conversation_id 注入
    metadata / tags，使 LangSmith 里的 span 与业务 trace_id 关联（打通 trace）。

    从 structlog 上下文读取（由 ``bind_context(trace_id=...)`` 绑定）。
    """
    try:
        import structlog

        ctx = structlog.contextvars.get_contextvars()
    except Exception:  # noqa: BLE001 - 上下文读取失败仅降级为无 metadata
        ctx = {}

    config: dict[str, Any] = {"tags": ["sekb"]}
    trace_id = ctx.get("trace_id")
    if trace_id:
        metadata: dict[str, Any] = {"trace_id": str(trace_id)}
        conversation_id = ctx.get("conversation_id")
        if conversation_id:
            metadata["conversation_id"] = str(conversation_id)
        config["metadata"] = metadata
    return config
