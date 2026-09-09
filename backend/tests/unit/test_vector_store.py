"""
DirectVectorStore 向量检索直连模块的单元测试

测试内容：
- DirectVectorStore 创建
- search 方法（使用 mock kb，验证返回格式与参数透传）
- add 方法（便捷添加，验证 kb.add 被调用）
- count 方法
- kb 为 None 时的处理（构造允许，操作抛错）

技术要点：
- 使用 unittest.mock.AsyncMock 模拟 KnowledgeBaseBackend
- 构造 mock KnowledgeEntry 验证 search 结果字段映射
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.memory.knowledge_entry import KnowledgeEntry
from app.tools.direct.vector_store import DirectVectorStore


# ============================================================
# 辅助函数
# ============================================================

def _make_mock_entry(
    content: str = "测试内容",
    source: str = "conversation",
    source_id: str = "conv-1",
    importance_score: float = 0.8,
    entry_id: str = "entry-1",
    topic: str = "python",
    distance: float = 0.2,
) -> KnowledgeEntry:
    """构造带 distance 的 mock KnowledgeEntry（模拟检索返回）"""
    entry = KnowledgeEntry(
        content=content,
        source=source,
        source_id=source_id,
        importance_score=importance_score,
        entry_id=entry_id,  # type: ignore[call-arg]
        topic=topic,
    )
    entry.metadata["distance"] = distance
    return entry


def _make_mock_kb() -> MagicMock:
    """构造一个 mock 知识库后端，所有异步方法返回 AsyncMock"""
    kb = MagicMock()
    kb.retrieve = AsyncMock()
    kb.add = AsyncMock()
    kb.count = AsyncMock()
    kb.delete = AsyncMock()
    return kb


# ============================================================
# 创建测试
# ============================================================

class TestDirectVectorStoreCreation:
    """测试 DirectVectorStore 创建"""

    def test_create_with_kb(self):
        """构造时保存 kb 引用"""
        kb = _make_mock_kb()
        store = DirectVectorStore(kb)
        assert store.kb is kb

    def test_create_accepts_none(self):
        """构造允许 kb 为 None（由调用方在调用时处理）"""
        store = DirectVectorStore(None)  # type: ignore[arg-type]
        assert store.kb is None


# ============================================================
# search 方法测试
# ============================================================

class TestDirectVectorStoreSearch:
    """测试 search 方法"""

    async def test_search_returns_empty_when_no_results(self):
        """kb 检索返回空列表时，search 返回空列表"""
        kb = _make_mock_kb()
        kb.retrieve.return_value = []
        store = DirectVectorStore(kb)

        results = await store.search("查询", user_id="u1", top_k=5, min_score=0.3)

        assert results == []

    async def test_search_returns_formatted_results(self):
        """检索结果正确映射为字典格式"""
        kb = _make_mock_kb()
        entry = _make_mock_entry(
            content="知识内容",
            source="document",
            source_id="file.pdf",
            importance_score=0.85,
            entry_id="e-1",
            topic="python",
            distance=0.2,
        )
        kb.retrieve.return_value = [entry]
        store = DirectVectorStore(kb)

        results = await store.search("查询", user_id="u1", top_k=5, min_score=0.3)

        assert len(results) == 1
        r = results[0]
        assert r["content"] == "知识内容"
        assert r["source"] == "document"
        assert r["source_id"] == "file.pdf"
        assert r["importance"] == 0.85
        assert r["entry_id"] == "e-1"
        assert r["topic"] == "python"
        # score = 1 - distance = 0.8
        assert r["score"] == pytest.approx(0.8)

    async def test_search_passes_parameters_to_kb(self):
        """search 参数正确透传给 kb.retrieve"""
        kb = _make_mock_kb()
        kb.retrieve.return_value = []
        store = DirectVectorStore(kb)

        await store.search("我的查询", user_id="user-99", top_k=10, min_score=0.5)

        kb.retrieve.assert_awaited_once_with(
            query="我的查询",
            user_id="user-99",
            top_k=10,
            min_score=0.5,
            category_l1=None,
            category_l2=None,
            category_l3=None,
        )

    async def test_search_default_parameters(self):
        """search 默认参数正确"""
        kb = _make_mock_kb()
        kb.retrieve.return_value = []
        store = DirectVectorStore(kb)

        await store.search("查询")

        kb.retrieve.assert_awaited_once()
        call_kwargs = kb.retrieve.await_args.kwargs
        assert call_kwargs["user_id"] == "default"
        assert call_kwargs["top_k"] == 5
        assert call_kwargs["min_score"] == 0.3

    async def test_search_multiple_results(self):
        """多条检索结果全部返回"""
        kb = _make_mock_kb()
        entries = [
            _make_mock_entry(content=f"内容{i}", entry_id=f"e-{i}", distance=0.1 * i)
            for i in range(3)
        ]
        kb.retrieve.return_value = entries
        store = DirectVectorStore(kb)

        results = await store.search("查询")

        assert len(results) == 3
        assert {r["entry_id"] for r in results} == {"e-0", "e-1", "e-2"}

    async def test_search_with_none_kb_raises(self):
        """kb 为 None 时 search 抛出 AttributeError"""
        store = DirectVectorStore(None)  # type: ignore[arg-type]
        with pytest.raises(AttributeError):
            await store.search("查询")


# ============================================================
# add 方法测试
# ============================================================

class TestDirectVectorStoreAdd:
    """测试 add 便捷方法"""

    async def test_add_calls_kb_add_and_returns_id(self):
        """add 调用 kb.add 并返回 entry_id"""
        kb = _make_mock_kb()
        kb.add.return_value = "new-entry-id"
        store = DirectVectorStore(kb)

        entry_id = await store.add(
            content="新知识",
            user_id="u1",
            source="document",
            source_id="file.pdf",
            importance_score=0.7,
            topic="topic1",
        )

        assert entry_id == "new-entry-id"
        kb.add.assert_awaited_once()
        # 传入的是 KnowledgeEntry
        added_entry = kb.add.await_args.args[0]
        assert isinstance(added_entry, KnowledgeEntry)
        assert added_entry.content == "新知识"
        assert added_entry.user_id == "u1"
        assert added_entry.source == "document"
        assert added_entry.source_id == "file.pdf"
        assert added_entry.importance_score == 0.7
        assert added_entry.topic == "topic1"

    async def test_add_default_parameters(self):
        """add 默认参数正确"""
        kb = _make_mock_kb()
        kb.add.return_value = "id-1"
        store = DirectVectorStore(kb)

        await store.add(content="内容")

        added_entry = kb.add.await_args.args[0]
        assert added_entry.user_id == "default"
        assert added_entry.source == "conversation"
        assert added_entry.source_id == ""
        assert added_entry.importance_score == 0.5
        assert added_entry.topic == ""

    async def test_add_with_none_kb_raises(self):
        """kb 为 None 时 add 抛出 AttributeError"""
        store = DirectVectorStore(None)  # type: ignore[arg-type]
        with pytest.raises(AttributeError):
            await store.add(content="内容")


# ============================================================
# count 方法测试
# ============================================================

class TestDirectVectorStoreCount:
    """测试 count 方法"""

    async def test_count_returns_kb_count(self):
        """count 返回 kb.count 的结果"""
        kb = _make_mock_kb()
        kb.count.return_value = 42
        store = DirectVectorStore(kb)

        total = await store.count()

        assert total == 42
        kb.count.assert_awaited_once_with(None)

    async def test_count_passes_user_id(self):
        """count 透传 user_id 参数"""
        kb = _make_mock_kb()
        kb.count.return_value = 5
        store = DirectVectorStore(kb)

        await store.count(user_id="user-1")

        kb.count.assert_awaited_once_with("user-1")

    async def test_count_default_user_id_is_none(self):
        """count 默认 user_id 为 None"""
        kb = _make_mock_kb()
        kb.count.return_value = 0
        store = DirectVectorStore(kb)

        await store.count()

        kb.count.assert_awaited_once_with(None)

    async def test_count_with_none_kb_raises(self):
        """kb 为 None 时 count 抛出 AttributeError"""
        store = DirectVectorStore(None)  # type: ignore[arg-type]
        with pytest.raises(AttributeError):
            await store.count()
