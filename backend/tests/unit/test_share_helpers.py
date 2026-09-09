"""
分享知识库问答辅助函数的单元测试

测试内容：
- _build_rag_context：空结果、来源优先用 source_id、空内容跳过
- _history_to_messages：窗口截断、角色映射、空内容跳过
"""

from __future__ import annotations

from app.api.routes.share import (
    _build_rag_context,
    _history_to_messages,
)


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
