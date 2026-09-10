"""链路追踪（trace_id 注入）的单元测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

import structlog

from app.core.tracing import get_trace_config, setup_tracing


def _clear() -> None:
    structlog.contextvars.clear_contextvars()


class TestGetTraceConfig:
    def test_no_context_returns_tags_only(self):
        _clear()
        cfg = get_trace_config()
        assert cfg["tags"] == ["sekb"]
        assert "metadata" not in cfg

    def test_trace_id_injected_into_metadata(self):
        _clear()
        structlog.contextvars.bind_contextvars(trace_id="t-123", user_id="u1")
        cfg = get_trace_config()
        assert cfg["metadata"]["trace_id"] == "t-123"
        # user_id 不注入（只注入 trace_id / conversation_id）
        assert "user_id" not in cfg["metadata"]

    def test_conversation_id_injected(self):
        _clear()
        structlog.contextvars.bind_contextvars(trace_id="t-1", conversation_id="c-9")
        cfg = get_trace_config()
        assert cfg["metadata"]["trace_id"] == "t-1"
        assert cfg["metadata"]["conversation_id"] == "c-9"


class TestSetupTracing:
    def test_returns_false_without_api_key(self):
        # 无 LANGSMITH_API_KEY 时（默认空），返回 False
        cfg = MagicMock()
        cfg.tracing.provider = "langsmith"
        cfg.tracing.langsmith.api_key = ""
        assert setup_tracing(cfg) is False  # type: ignore[arg-type]
