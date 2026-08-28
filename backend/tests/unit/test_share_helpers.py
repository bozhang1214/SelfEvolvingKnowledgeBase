"""
分享知识库问答辅助函数的单元测试

测试内容：
- _extract_stream_text：str / list（content block）/ None / 其他类型
- _build_rag_context：空结果、来源优先用 source_id、空内容跳过
- _history_to_messages：窗口截断、角色映射、空内容跳过
"""

from __future__ import annotations

from types import SimpleNamespace

from app.api.routes.share import (
    _build_rag_context,
    _extract_stream_text,
    _history_to_messages,
)


class TestExtractStreamText:
    def test_str_content(self):
        chunk = SimpleNamespace(content="你好")
        assert _extract_stream_text(chunk) == "你好"

    def test_list_content_blocks(self):
        chunk = SimpleNamespace(content=[{"type": "text", "text": "ABC"}])
        assert _extract_stream_text(chunk) == "ABC"

    def test_list_mixed_types(self):
        chunk = SimpleNamespace(content=[{"type": "text", "text": "a"}, "b"])
        assert _extract_stream_text(chunk) == "ab"

    def test_none_content(self):
        assert _extract_stream_text(SimpleNamespace(content=None)) == ""
        assert _extract_stream_text(SimpleNamespace()) == ""

    def test_other_content_type(self):
        chunk = SimpleNamespace(content=123)
        assert _extract_stream_text(chunk) == "123"


class TestBuildRagContext:
    def test_empty(self):
        assert _build_rag_context([]) == "（未检索到相关知识）"

    def test_uses_source_id_first(self):
        retrieved = [{"content": "正文", "source_id": "a.pdf", "source": "document"}]
        ctx = _build_rag_context(retrieved)
        assert "来源:a.pdf" in ctx
        assert "来源:document" not in ctx

    def test_falls_back_to_source(self):
        retrieved = [{"content": "正文", "source": "document"}]
        ctx = _build_rag_context(retrieved)
        assert "来源:document" in ctx

    def test_skips_empty_content(self):
        retrieved = [
            {"content": "   ", "source": "document"},
            {"content": "有效内容", "source_id": "b.txt"},
        ]
        ctx = _build_rag_context(retrieved)
        assert "[1]" in ctx
        assert "有效内容" in ctx


class TestHistoryToMessages:
    def test_role_mapping(self):
        history = [
            {"role": "user", "content": "Q"},
            {"role": "assistant", "content": "A"},
        ]
        msgs = _history_to_messages(history)
        assert len(msgs) == 2
        assert msgs[0].content == "Q"
        assert msgs[1].content == "A"

    def test_skips_empty_content(self):
        history = [{"role": "user", "content": ""}, {"role": "assistant", "content": "A"}]
        msgs = _history_to_messages(history)
        assert len(msgs) == 1
        assert msgs[0].content == "A"
