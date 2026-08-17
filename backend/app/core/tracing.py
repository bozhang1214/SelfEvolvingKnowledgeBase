"""
链路追踪模块

支持两种 tracing 后端：
1. LangSmith（默认，需 API Key）
2. 本地 JSON（降级方案）

启动时根据配置自动选择，LangSmith 不可用时自动降级。

使用方式：
    from app.core.tracing import setup_tracing
    setup_tracing()  # 在应用启动时调用一次
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import AppConfig, get_config
from app.core.logging import get_logger

logger = get_logger(__name__)


class LocalTraceCollector:
    """
    本地 JSON trace 收集器（降级方案）。

    将 trace 事件以 JSONL 格式写入本地文件。
    """

    def __init__(self, trace_dir: str):
        self.trace_dir = Path(trace_dir)
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self._current_trace: list[dict[str, Any]] = []

    def start_trace(self, name: str, metadata: dict[str, Any] | None = None) -> str:
        """开始一个新的 trace，返回 trace_id"""
        trace_id = str(uuid.uuid4())
        event = {
            "trace_id": trace_id,
            "event": "trace_start",
            "name": name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metadata": metadata or {},
        }
        self._current_trace.append(event)
        return trace_id

    def add_event(
        self,
        trace_id: str,
        name: str,
        data: dict[str, Any] | None = None,
        parent_id: str | None = None,
    ) -> None:
        """向 trace 添加事件"""
        event = {
            "trace_id": trace_id,
            "event": name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data or {},
        }
        if parent_id:
            event["parent_id"] = parent_id
        self._current_trace.append(event)

    def end_trace(self, trace_id: str, status: str = "ok") -> None:
        """结束 trace 并写入文件"""
        event = {
            "trace_id": trace_id,
            "event": "trace_end",
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._current_trace.append(event)

        # 写入 JSONL 文件
        trace_file = self.trace_dir / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"
        with open(trace_file, "a", encoding="utf-8") as f:
            for evt in self._current_trace:
                if evt["trace_id"] == trace_id:
                    f.write(json.dumps(evt, ensure_ascii=False) + "\n")

        # 清理已写入的事件
        self._current_trace = [e for e in self._current_trace if e["trace_id"] != trace_id]


def setup_tracing(config: AppConfig | None = None) -> LocalTraceCollector | None:
    """
    初始化链路追踪。

    优先配置 LangSmith，如果 API Key 未设置或连接失败，
    则自动降级到本地 JSON trace。

    Args:
        config: 应用配置，若为 None 则自动加载

    Returns:
        LocalTraceCollector 实例（降级时），或 None（LangSmith 模式）
    """
    if config is None:
        config = get_config()

    tracing_config = config.tracing

    # 尝试 LangSmith
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
            return None
        except Exception as e:
            logger.warning(
                "LangSmith 启用失败，降级到本地 JSON trace",
                error=str(e),
            )
            if not tracing_config.fallback_to_local_on_failure:
                return None

    # 降级到本地 JSON trace
    if tracing_config.provider in ("local_json", "langsmith"):
        collector = LocalTraceCollector(tracing_config.local_json.trace_dir)
        logger.info("本地 JSON 追踪已启用", trace_dir=tracing_config.local_json.trace_dir)
        return collector

    logger.info("追踪未启用")
    return None


def generate_trace_id() -> str:
    """生成唯一的 trace ID"""
    return str(uuid.uuid4())
