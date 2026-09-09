"""
链路追踪模块（Phase 3：LangSmith）

启动时按配置初始化 LangSmith 追踪。本地 JSON trace（LocalTraceCollector）原为
无消费方的降级方案，按 D6 决策删除。

使用方式：
    from app.core.tracing import setup_tracing
    setup_tracing()  # 在应用启动时调用一次
"""
from __future__ import annotations

from app.core.config import AppConfig, get_config
from app.core.logging import get_logger

logger = get_logger(__name__)


def setup_tracing(config: AppConfig | None = None) -> None:
    """
    初始化链路追踪（LangSmith）。

    Args:
        config: 应用配置，若为 None 则自动加载
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
        except Exception as e:
            logger.warning("LangSmith 启用失败", error=str(e))
    else:
        logger.info("追踪未启用")
