"""
ChromaKnowledgeBase 多字段过滤与计数的单元测试

测试内容：
- _build_where_filter：单字段 / 多字段 $and / 空 → None
- count_entries：无过滤走 collection.count，有过滤走 get(include=[]) 并计数
- list_entries：where 过滤 + limit/offset 正确透传、offset 跳过
- 过滤字段：user_id / source / category_l1~l3
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.memory.knowledge_base import ChromaKnowledgeBase, _build_where_filter


def _make_kb() -> ChromaKnowledgeBase:
    """构造一个已初始化、collection 为 mock 的实例（不触发真实 chromadb）。"""
    kb = ChromaKnowledgeBase(
        persist_path="/tmp/unused",
        embedding_fn=lambda texts: [[0.0, 0.0] for _ in texts],
    )
    kb._collection = MagicMock()
    kb._initialized = True
    return kb


class TestBuildWhereFilter:
    def test_all_empty_returns_none(self):
        assert _build_where_filter() is None

    def test_single_user_id(self):
        assert _build_where_filter(user_id="u1") == {"user_id": "u1"}

    def test_single_source(self):
        assert _build_where_filter(source="document") == {"source": "document"}

    def test_multiple_fields_use_and(self):
        result = _build_where_filter(user_id="u1", source="document")
        assert result == {"$and": [{"user_id": "u1"}, {"source": "document"}]}

    def test_category_fields_included(self):
        result = _build_where_filter(
            user_id="u1", category_l1="技术开发", category_l2="编程语言", category_l3="Python",
        )
        assert result == {
            "$and": [
                {"user_id": "u1"},
                {"category_l1": "技术开发"},
                {"category_l2": "编程语言"},
                {"category_l3": "Python"},
            ]
        }


class TestCountEntries:
    @pytest.mark.asyncio
    async def test_no_filter_uses_collection_count(self):
        kb = _make_kb()
        kb._collection.count = MagicMock(return_value=7)
        assert await kb.count_entries() == 7
        kb._collection.count.assert_called_once()

    @pytest.mark.asyncio
    async def test_filter_uses_get_with_include_empty(self):
        kb = _make_kb()
        kb._collection.get = MagicMock(return_value={"ids": ["a", "b", "c"]})
        assert await kb.count_entries(user_id="u1") == 3
        # 有过滤时应走 get(where=..., include=[]) 而非 count
        kb._collection.get.assert_called_once_with(where={"user_id": "u1"}, include=[])
        kb._collection.count.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_result_returns_zero(self):
        kb = _make_kb()
        kb._collection.get = MagicMock(return_value={"ids": []})
        assert await kb.count_entries(category_l1="技术开发") == 0


class TestListEntriesFiltering:
    @pytest.mark.asyncio
    async def test_passes_where_limit_offset(self):
        kb = _make_kb()
        kb._collection.get = MagicMock(return_value={"ids": [], "documents": [], "metadatas": []})
        await kb.list_entries(user_id="u1", source="document", limit=20, offset=40)
        # 原生 offset/limit 透传（避免 limit+offset 再手动 skip 的 O(n²)）
        kb._collection.get.assert_called_once_with(
            where={"$and": [{"user_id": "u1"}, {"source": "document"}]},
            limit=20,
            offset=40,
            include=["documents", "metadatas"],
        )

    @pytest.mark.asyncio
    async def test_offset_uses_native_chroma(self):
        kb = _make_kb()
        # 模拟 chroma 原生 offset：get(limit=2, offset=1) 直接返回该窗口的记录
        ids = ["e2", "e3"]
        docs = ["c2", "c3"]
        metas = [
            {"user_id": "u1", "source": "document"},
            {"user_id": "u1", "source": "document"},
        ]
        kb._collection.get = MagicMock(return_value={"ids": ids, "documents": docs, "metadatas": metas})

        entries = await kb.list_entries(user_id="u1", limit=2, offset=1)
        assert [e.entry_id for e in entries] == ["e2", "e3"]
        kb._collection.get.assert_called_once_with(
            where={"user_id": "u1"},
            limit=2,
            offset=1,
            include=["documents", "metadatas"],
        )

    @pytest.mark.asyncio
    async def test_category_filter_passed(self):
        kb = _make_kb()
        kb._collection.get = MagicMock(return_value={"ids": [], "documents": [], "metadatas": []})
        await kb.list_entries(user_id="u1", category_l3="Python")
        kb._collection.get.assert_called_once_with(
            where={"$and": [{"user_id": "u1"}, {"category_l3": "Python"}]},
            limit=100,
            offset=0,
            include=["documents", "metadatas"],
        )
