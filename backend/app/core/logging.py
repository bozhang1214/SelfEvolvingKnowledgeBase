"""
结构化日志模块

基于 structlog 提供结构化 JSON 日志输出，支持：
- 敏感字段脱敏
- 日志文件轮转
- trace_id 注入（与 LangSmith 联动）
- 上下文绑定（bind）

使用方式：
    from app.core.logging import get_logger
    logger = get_logger(__name__)
    logger.info("消息", user_id="xxx", action="chat")
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Any

import structlog

from app.core.config import AppConfig, get_config


def _redact_processor(redact_fields: set[str]):
    """创建脱敏处理器，遮蔽敏感字段"""

    def redact(_logger, _method_name, event_dict: dict[str, Any]) -> dict[str, Any]:
        for key in list(event_dict.keys()):
            if key.lower() in redact_fields:
                event_dict[key] = "***REDACTED***"
        return event_dict

    return redact


def setup_logging(config: AppConfig | None = None) -> None:
    """
    初始化全局日志配置。

    在应用启动时调用一次。支持 JSON 和 text 两种格式。
    同时输出到 stdout 和文件（带轮转）。

    Args:
        config: 应用配置，若为 None 则自动加载
    """
    if config is None:
        config = get_config()

    log_config = config.logging
    redact_fields = {f.lower() for f in log_config.redact_fields}

    # 配置 structlog 处理链
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        _redact_processor(redact_fields),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if log_config.format == "json":
        renderer = structlog.processors.JSONRenderer(ensure_ascii=False)
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=shared_processors + [renderer],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # 配置标准库 logging（用于第三方库的日志）
    level = getattr(logging, log_config.level.upper(), logging.INFO)

    # stdout handler
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(level)
    stream_handler.setFormatter(logging.Formatter("%(message)s"))

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers.clear()
    root_logger.addHandler(stream_handler)

    # 文件 handler（带轮转）
    log_dir = Path(log_config.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "app.log",
        maxBytes=log_config.max_file_size_mb * 1024 * 1024,
        backupCount=log_config.backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger.addHandler(file_handler)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """
    获取一个结构化 logger。

    Args:
        name: logger 名称，通常传 __name__

    Returns:
        structlog BoundLogger 实例
    """
    return structlog.get_logger(name)


def bind_context(**kwargs: Any) -> None:
    """
    绑定全局日志上下文（所有后续日志自动携带这些字段）。

    典型用法：在请求开始时绑定 trace_id、user_id 等。

    Args:
        **kwargs: 要绑定的上下文字段
    """
    structlog.contextvars.bind_contextvars(**kwargs)


def clear_context() -> None:
    """清除全局日志上下文（请求结束时调用）"""
    structlog.contextvars.clear_contextvars()
