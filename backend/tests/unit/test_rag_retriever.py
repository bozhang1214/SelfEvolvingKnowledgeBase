"""
RAG 检索器模块的单元测试

测试内容：
- RAGRetriever 创建与配置解析
- vector_store 为 None 时返回 disabled
- chitchat / chat_simple 意图跳过 RAG
- kb_strict 意图返回 strict 模式
- kb_prefer 意图返回 prefer 模式
- web_default / task_plan 意图返回 auxiliary 模式
- 未识别意图跳过
- 重要性过滤
- format_rag_context 格式化输出
- 空结果时的 fallback_message

技术要点：
- 使用 unittest.mock.AsyncMock 模拟 DirectVectorStore
- @pytest.mark.asyncio 标记异步测试
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.tools.rag.retriever import (
    RAGResult,
    RAGRetriever,
    format_rag_context,
)

# ============================================================
# 辅助函数
# ============================================================

def _make_mock_vector_store() -> MagicMock:
    """构造一个 mock DirectVectorStore，search 为 AsyncMock"""
    vs = MagicMock()
    vs.search = AsyncMock()
    vs.count = AsyncMock(return_value=0)
    return vs


def _make_result_item(
    content: str = "知识内容",
    importance: float = 0.8,
    source: str = "conversation",
    source_id: str = "conv-1",
    entry_id: str = "e-1",
    topic: str = "",
    score: float = 0.85,
) -> dict:
    """构造一个 DirectVectorStore.search 返回格式的结果项"""
    return {
        "content": content,
        "importance": importance,
        "source": source,
        "source_id": source_id,
        "entry_id": entry_id,
        "topic": topic,
        "score": score,
    }


# ============================================================
# RAGRetriever 创建测试
# ============================================================

class TestRAGRetrieverCreation:
    """测试 RAGRetriever 创建与配置"""

    def test_create_with_defaults(self):
        """默认配置正确解析"""
        vs = _make_mock_vector_store()
        retriever = RAGRetriever(vs, config={})

        assert retriever.vector_store is vs
        assert retriever.retrieval_top_k == 5
        assert retriever.min_score == 0.3
        assert retriever.importance_threshold == 0.0
        # auxiliary_top_k = max(2, 5 // 2) = 2
        assert retriever.auxiliary_top_k == 2
        # auxiliary_min_score = min(0.9, 0.3 + 0.15) = 0.45
        assert retriever.auxiliary_min_score == pytest.approx(0.45)

    def test_create_with_custom_config(self):
        """自定义配置正确解析"""
        vs = _make_mock_vector_store()
        retriever = RAGRetriever(
            vs,
            config={
                "retrieval_top_k": 10,
                "min_score": 0.5,
                "importance_threshold": 0.4,
                "auxiliary_top_k": 3,
                "auxiliary_min_score": 0.7,
            },
        )

        assert retriever.retrieval_top_k == 10
        assert retriever.min_score == 0.5
        assert retriever.importance_threshold == 0.4
        assert retriever.auxiliary_top_k == 3
        assert retriever.auxiliary_min_score == 0.7

    def test_create_with_none_vector_store(self):
        """vector_store 为 None 时允许创建"""
        retriever = RAGRetriever(None, config={})
        assert retriever.vector_store is None

    def test_auxiliary_top_k_minimum_is_two(self):
        """auxiliary_top_k 至少为 2"""
        vs = _make_mock_vector_store()
        # retrieval_top_k=3 → 3//2=1 → max(2,1)=2
        retriever = RAGRetriever(vs, config={"retrieval_top_k": 3})
        assert retriever.auxiliary_top_k == 2


# ============================================================
# 意图路由测试
# ============================================================

class TestIntentRouting:
    """测试不同意图的检索策略"""

    async def test_chitchat_skips_rag(self):
        """chitchat 意图跳过 RAG"""
        vs = _make_mock_vector_store()
        retriever = RAGRetriever(vs, config={})

        result = await retriever.retrieve_for_query("你好", "u1", "chitchat")

        assert result.mode == "disabled"
        assert result.context is None
        assert result.retrieval_count == 0
        vs.search.assert_not_awaited()

    async def test_chat_simple_skips_rag(self):
        """chat_simple 意图跳过 RAG"""
        vs = _make_mock_vector_store()
        retriever = RAGRetriever(vs, config={})

        result = await retriever.retrieve_for_query("你好", "u1", "chat_simple")

        assert result.mode == "disabled"
        assert result.context is None
        vs.search.assert_not_awaited()

    async def test_none_vector_store_returns_disabled(self):
        """vector_store 为 None 时（非闲聊意图）返回 disabled 并提示"""
        retriever = RAGRetriever(None, config={})

        result = await retriever.retrieve_for_query("查询", "u1", "kb_strict")

        assert result.mode == "disabled"
        assert result.context is None
        assert result.fallback_message == "知识库未启用"

    async def test_kb_strict_returns_strict_mode(self):
        """kb_strict 意图返回 strict 模式"""
        vs = _make_mock_vector_store()
        vs.search.return_value = [_make_result_item()]
        retriever = RAGRetriever(vs, config={})

        result = await retriever.retrieve_for_query("查询", "u1", "kb_strict")

        assert result.mode == "strict"
        assert result.context is not None
        assert len(result.context) == 1
        # 验证参数透传：strict 用 retrieval_top_k 与 min_score
        vs.search.assert_awaited_once_with(
            query="查询", user_id="u1", top_k=5, min_score=0.3
        )

    async def test_kb_prefer_returns_prefer_mode(self):
        """kb_prefer 意图返回 prefer 模式"""
        vs = _make_mock_vector_store()
        vs.search.return_value = [_make_result_item()]
        retriever = RAGRetriever(vs, config={})

        result = await retriever.retrieve_for_query("查询", "u1", "kb_prefer")

        assert result.mode == "prefer"
        assert result.context is not None
        vs.search.assert_awaited_once_with(
            query="查询", user_id="u1", top_k=5, min_score=0.3
        )

    async def test_web_default_returns_auxiliary_mode(self):
        """web_default 意图返回 auxiliary 模式"""
        vs = _make_mock_vector_store()
        vs.search.return_value = [_make_result_item()]
        retriever = RAGRetriever(vs, config={})

        result = await retriever.retrieve_for_query("查询", "u1", "web_default")

        assert result.mode == "auxiliary"
        assert result.context is not None
        # auxiliary 用 auxiliary_top_k 与 auxiliary_min_score
        vs.search.assert_awaited_once_with(
            query="查询", user_id="u1", top_k=2, min_score=pytest.approx(0.45)
        )

    async def test_task_plan_returns_auxiliary_mode(self):
        """task_plan 意图返回 auxiliary 模式"""
        vs = _make_mock_vector_store()
        vs.search.return_value = [_make_result_item()]
        retriever = RAGRetriever(vs, config={})

        result = await retriever.retrieve_for_query("查询", "u1", "task_plan")

        assert result.mode == "auxiliary"
        assert result.context is not None
        vs.search.assert_awaited_once()

    async def test_unknown_intent_skips_rag(self):
        """未识别意图跳过 RAG"""
        vs = _make_mock_vector_store()
        retriever = RAGRetriever(vs, config={})

        result = await retriever.retrieve_for_query("查询", "u1", "unknown_intent")

        assert result.mode == "disabled"
        assert result.context is None
        vs.search.assert_not_awaited()


# ============================================================
# fallback_message 测试
# ============================================================

class TestFallbackMessage:
    """测试空结果时的 fallback_message"""

    async def test_strict_empty_results_fallback(self):
        """strict 模式无结果时返回提示"""
        vs = _make_mock_vector_store()
        vs.search.return_value = []
        retriever = RAGRetriever(vs, config={})

        result = await retriever.retrieve_for_query("查询", "u1", "kb_strict")

        assert result.context is None
        assert result.fallback_message == "知识库中暂无相关信息"

    async def test_prefer_empty_results_fallback(self):
        """prefer 模式无结果时返回提示"""
        vs = _make_mock_vector_store()
        vs.search.return_value = []
        retriever = RAGRetriever(vs, config={})

        result = await retriever.retrieve_for_query("查询", "u1", "kb_prefer")

        assert result.context is None
        assert result.fallback_message == "知识库中暂无相关信息，将结合通用能力回答"

    async def test_auxiliary_empty_results_no_fallback(self):
        """auxiliary 模式无结果时静默（无提示）"""
        vs = _make_mock_vector_store()
        vs.search.return_value = []
        retriever = RAGRetriever(vs, config={})

        result = await retriever.retrieve_for_query("查询", "u1", "web_default")

        assert result.context is None
        assert result.fallback_message == ""

    async def test_strict_with_results_no_fallback(self):
        """strict 模式有结果时无 fallback_message"""
        vs = _make_mock_vector_store()
        vs.search.return_value = [_make_result_item()]
        retriever = RAGRetriever(vs, config={})

        result = await retriever.retrieve_for_query("查询", "u1", "kb_strict")

        assert result.context is not None
        assert result.fallback_message == ""
        assert result.retrieval_count == 1


# ============================================================
# 重要性过滤测试
# ============================================================

class TestImportanceFilter:
    """测试重要性阈值过滤"""

    async def test_filters_low_importance_entries(self):
        """importance_threshold 过滤低于阈值的条目"""
        vs = _make_mock_vector_store()
        vs.search.return_value = [
            _make_result_item(content="高重要性", importance=0.8),
            _make_result_item(content="低重要性", importance=0.2),
        ]
        retriever = RAGRetriever(
            vs, config={"importance_threshold": 0.5}
        )

        result = await retriever.retrieve_for_query("查询", "u1", "kb_strict")

        assert result.context is not None
        assert len(result.context) == 1
        assert result.context[0]["content"] == "高重要性"
        assert result.retrieval_count == 1

    async def test_no_filter_when_threshold_zero(self):
        """importance_threshold=0 时不过滤"""
        vs = _make_mock_vector_store()
        vs.search.return_value = [
            _make_result_item(importance=0.1),
            _make_result_item(importance=0.9),
        ]
        retriever = RAGRetriever(vs, config={"importance_threshold": 0.0})

        result = await retriever.retrieve_for_query("查询", "u1", "kb_strict")

        assert result.context is not None
        assert len(result.context) == 2


# ============================================================
# latency_ms 测试
# ============================================================

class TestLatencyTracking:
    """测试检索耗时统计"""

    async def test_latency_ms_is_non_negative(self):
        """latency_ms 为非负整数"""
        vs = _make_mock_vector_store()
        vs.search.return_value = [_make_result_item()]
        retriever = RAGRetriever(vs, config={})

        result = await retriever.retrieve_for_query("查询", "u1", "kb_strict")

        assert isinstance(result.latency_ms, int)
        assert result.latency_ms >= 0


# ============================================================
# format_rag_context 测试
# ============================================================

class TestFormatRagContext:
    """测试 format_rag_context 格式化输出"""

    def test_empty_results_returns_empty_string(self):
        """空结果返回空字符串"""
        assert format_rag_context([]) == ""

    def test_none_results_returns_empty_string(self):
        """None 结果返回空字符串"""
        assert format_rag_context(None) == ""  # type: ignore[arg-type]

    def test_single_result_format(self):
        """单条结果格式化正确"""
        results = [_make_result_item(content="知识内容A", importance=0.85, source="conversation")]
        text = format_rag_context(results)

        assert "【知识库参考】" in text
        assert "以下是从您的知识库中检索到的相关信息：" in text
        assert "[参考1]" in text
        assert "重要性：0.85" in text
        assert "来源：对话" in text
        assert "知识内容A" in text

    def test_multiple_results_indexed(self):
        """多条结果按序号索引"""
        results = [
            _make_result_item(content="内容1", importance=0.8, source="document"),
            _make_result_item(content="内容2", importance=0.6, source="manual"),
        ]
        text = format_rag_context(results)

        assert "[参考1]" in text
        assert "[参考2]" in text
        assert "内容1" in text
        assert "内容2" in text
        assert "来源：文档" in text
        assert "来源：手工录入" in text

    def test_unknown_source_label_passthrough(self):
        """未知来源直接使用原值"""
        results = [_make_result_item(content="内容", source="custom_source")]
        text = format_rag_context(results)
        assert "来源：custom_source" in text

    def test_importance_formatted_two_decimals(self):
        """重要性分数保留两位小数"""
        results = [_make_result_item(importance=0.123456)]
        text = format_rag_context(results)
        assert "重要性：0.12" in text

    def test_empty_content_still_included(self):
        """空内容的条目仍被包含（content 为空字符串）"""
        results = [_make_result_item(content="")]
        text = format_rag_context(results)
        assert "[参考1]" in text


# ============================================================
# RAGResult 数据结构测试
# ============================================================

class TestRAGResult:
    """测试 RAGResult 数据结构"""

    def test_default_values(self):
        """RAGResult 默认值正确"""
        result = RAGResult(mode="strict", context=None)
        assert result.mode == "strict"
        assert result.context is None
        assert result.fallback_message == ""
        assert result.retrieval_count == 0
        assert result.latency_ms == 0

    def test_with_context(self):
        """带 context 的 RAGResult"""
        ctx = [_make_result_item()]
        result = RAGResult(
            mode="strict",
            context=ctx,
            retrieval_count=1,
            latency_ms=15,
        )
        assert result.context == ctx
        assert result.retrieval_count == 1
        assert result.latency_ms == 15
