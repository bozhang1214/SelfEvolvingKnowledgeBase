"""
知识库自迭代引擎模块的单元测试

测试内容：
- KnowledgeIngester 创建
- knowledge_base 为 None 时返回 disabled
- 重要性评分低于阈值时返回 skipped
- _calculate_importance 评分计算
- IngestResult 和 ConflictResult 数据结构
- 使用 mock LLM 和 mock KB 测试完整流程（inserted / merged / coexist）
- _parse_facts_response 多格式解析
- _detect_conflicts 冲突检测

技术要点：
- 使用 unittest.mock.AsyncMock / MagicMock 模拟 LLMFactory 与 KnowledgeBaseBackend
- 使用 sample_config 夹具（importance_threshold 默认 0.3）
- @pytest.mark.asyncio 标记异步测试
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.knowledge_ingestor import (
    ConflictResult,
    IngestResult,
    KnowledgeIngester,
)
from app.memory.knowledge_entry import KnowledgeEntry


# ============================================================
# 全局夹具：patch FACT_EXTRACTION_PROMPT
# ============================================================

@pytest.fixture(autouse=True)
def _patch_fact_extraction_prompt():
    """
    自动 patch 模块级 FACT_EXTRACTION_PROMPT。

    PromptTemplate.from_template 创建的对象在当前 langchain 版本下无
    format_messages 方法（该方法属于 ChatPromptTemplate）。
    这里用 MagicMock 替换，使 format_messages 返回消息列表，
    让 _extract_facts 能正常流转到 LLM 调用与响应解析。
    """
    mock_prompt = MagicMock()
    mock_prompt.format_messages.return_value = [MagicMock()]
    with patch(
        "app.agents.knowledge_ingestor.FACT_EXTRACTION_PROMPT",
        mock_prompt,
    ):
        yield


# ============================================================
# 辅助函数
# ============================================================

def _make_llm_response(content: str) -> MagicMock:
    """构造带 content 属性的 mock LLM 响应"""
    resp = MagicMock()
    resp.content = content
    return resp


_DEFAULT_FACTS_CONTENT = '{"facts": ["测试事实"]}'


def _make_mock_llm_factory(facts_content: str | None = None):
    """
    构造 mock LLMFactory，ainvoke_with_stats 返回包含 facts 的响应。

    Args:
        facts_content: LLM 响应的 content 字符串（JSON 格式）；
                       为 None 时使用默认事实内容
    """
    if facts_content is None:
        facts_content = _DEFAULT_FACTS_CONTENT
    factory = MagicMock()
    factory.ainvoke_with_stats = AsyncMock(
        return_value=_make_llm_response(facts_content)
    )
    return factory


def _make_mock_kb() -> MagicMock:
    """构造 mock 知识库后端"""
    kb = MagicMock()
    kb.add = AsyncMock(return_value="new-entry-id")
    kb.delete = AsyncMock(return_value=None)
    kb.retrieve = AsyncMock(return_value=[])
    kb.find_similar = AsyncMock(return_value=[])
    kb.count = AsyncMock(return_value=0)
    return kb


def _make_similar_entry(
    entry_id: str = "old-entry-1",
    content: str = "旧知识内容",
    distance: float = 0.02,
    version: int = 1,
) -> KnowledgeEntry:
    """构造带 similarity_score 的旧条目（用于冲突检测）"""
    entry = KnowledgeEntry(
        content=content,
        entry_id=entry_id,
        version=version,
        user_id="default",
    )
    entry.metadata["distance"] = distance
    return entry


# ============================================================
# KnowledgeIngester 创建测试
# ============================================================

class TestKnowledgeIngesterCreation:
    """测试 KnowledgeIngester 创建"""

    def test_create_with_dependencies(self, sample_config):
        """构造时保存 llm_factory 与 config 引用"""
        factory = MagicMock()
        ingester = KnowledgeIngester(factory, sample_config)

        assert ingester.llm_factory is factory
        assert ingester.config is sample_config

    def test_logger_initialized(self, sample_config):
        """logger 已初始化"""
        factory = MagicMock()
        ingester = KnowledgeIngester(factory, sample_config)
        assert ingester.logger is not None


# ============================================================
# disabled 与 skipped 路径测试
# ============================================================

class TestDisabledAndSkipped:
    """测试降级与跳过路径"""

    async def test_kb_none_returns_disabled(self, sample_config):
        """knowledge_base 为 None 时返回 disabled"""
        factory = _make_mock_llm_factory()
        ingester = KnowledgeIngester(factory, sample_config)

        result = await ingester.ingest_conversation(
            user_input="问题",
            final_answer="回答",
            summary="摘要",
            importance_score=0.9,
            conv_id="conv-1",
            user_id="u1",
            knowledge_base=None,
        )

        assert result.status == "disabled"
        assert result.reason == "L3 知识库未启用"
        # LLM 不应被调用
        factory.ainvoke_with_stats.assert_not_awaited()

    async def test_low_importance_returns_skipped(self, sample_config):
        """重要性评分低于阈值时返回 skipped"""
        factory = _make_mock_llm_factory()
        kb = _make_mock_kb()
        ingester = KnowledgeIngester(factory, sample_config)

        # importance=0.0, summary="" → has_factual_info=False
        # refined = 0.25*0 + 0.20*1 + 0.55*0 = 0.20 < 0.3（阈值）
        result = await ingester.ingest_conversation(
            user_input="问题",
            final_answer="回答",
            summary="",
            importance_score=0.0,
            conv_id="conv-1",
            user_id="u1",
            knowledge_base=kb,
        )

        assert result.status == "skipped"
        assert result.importance_score < 0.3
        assert "低于阈值" in result.reason
        # LLM 不应被调用（重要性过滤在前）
        factory.ainvoke_with_stats.assert_not_awaited()

    async def test_no_facts_returns_skipped(self, sample_config):
        """LLM 未提取到事实时返回 skipped"""
        factory = _make_mock_llm_factory('{"facts": []}')
        kb = _make_mock_kb()
        ingester = KnowledgeIngester(factory, sample_config)

        result = await ingester.ingest_conversation(
            user_input="问题",
            final_answer="回答",
            summary="有价值的摘要",
            importance_score=0.8,
            conv_id="conv-1",
            user_id="u1",
            knowledge_base=kb,
        )

        assert result.status == "skipped"
        assert "未提取到有价值的事实" in result.reason

    async def test_extract_facts_failure_returns_skipped(self, sample_config):
        """事实提取抛异常时返回 skipped"""
        factory = MagicMock()
        factory.ainvoke_with_stats = AsyncMock(side_effect=RuntimeError("LLM 不可用"))
        kb = _make_mock_kb()
        ingester = KnowledgeIngester(factory, sample_config)

        result = await ingester.ingest_conversation(
            user_input="问题",
            final_answer="回答",
            summary="摘要",
            importance_score=0.8,
            conv_id="conv-1",
            user_id="u1",
            knowledge_base=kb,
        )

        assert result.status == "skipped"
        assert "事实提取失败" in result.reason


# ============================================================
# _calculate_importance 评分计算测试
# ============================================================

class TestCalculateImportance:
    """测试 _calculate_importance 评分算法"""

    def test_with_factual_info_and_high_score(self, sample_config):
        """含事实信息 + 高原始分 → 评分提升"""
        factory = MagicMock()
        ingester = KnowledgeIngester(factory, sample_config)

        # score = 0.25*1 + 0.20*1 + 0.55*0.8 = 0.25 + 0.20 + 0.44 = 0.89
        score = ingester._calculate_importance(
            importance_score=0.8, summary="摘要", has_factual_info=True
        )
        assert score == pytest.approx(0.89)

    def test_with_factual_info_and_zero_score(self, sample_config):
        """含事实信息 + 零原始分 → 仍有一定评分"""
        factory = MagicMock()
        ingester = KnowledgeIngester(factory, sample_config)

        # score = 0.25*1 + 0.20*1 + 0.55*0 = 0.45
        score = ingester._calculate_importance(
            importance_score=0.0, summary="摘要", has_factual_info=True
        )
        assert score == pytest.approx(0.45)

    def test_without_factual_info(self, sample_config):
        """无事实信息 → 事实权重为 0"""
        factory = MagicMock()
        ingester = KnowledgeIngester(factory, sample_config)

        # score = 0.25*0 + 0.20*1 + 0.55*0.5 = 0.475
        score = ingester._calculate_importance(
            importance_score=0.5, summary="", has_factual_info=False
        )
        assert score == pytest.approx(0.475)

    def test_without_factual_info_and_zero_score(self, sample_config):
        """无事实信息 + 零原始分 → 仅时效性"""
        factory = MagicMock()
        ingester = KnowledgeIngester(factory, sample_config)

        # score = 0 + 0.20 + 0 = 0.20
        score = ingester._calculate_importance(
            importance_score=0.0, summary="", has_factual_info=False
        )
        assert score == pytest.approx(0.20)

    def test_max_score_clamped_to_one(self, sample_config):
        """评分上限裁剪到 1.0"""
        factory = MagicMock()
        ingester = KnowledgeIngester(factory, sample_config)

        score = ingester._calculate_importance(
            importance_score=1.0, summary="摘要", has_factual_info=True
        )
        # 0.25 + 0.20 + 0.55 = 1.0
        assert score == pytest.approx(1.0)

    def test_negative_score_clamped_to_zero(self, sample_config):
        """评分下限裁剪到 0.0"""
        factory = MagicMock()
        ingester = KnowledgeIngester(factory, sample_config)

        score = ingester._calculate_importance(
            importance_score=-1.0, summary="", has_factual_info=False
        )
        # 0 + 0.20 + 0.55*(-1) = -0.35 → clamped to 0.0
        assert score == 0.0

    def test_threshold_boundary_below(self, sample_config):
        """评分刚好低于阈值（0.3）→ skipped"""
        # importance=0.18, no factual info → 0.20 + 0.55*0.18 = 0.299 < 0.3
        factory = MagicMock()
        ingester = KnowledgeIngester(factory, sample_config)

        score = ingester._calculate_importance(
            importance_score=0.18, summary="", has_factual_info=False
        )
        assert score < 0.3


# ============================================================
# 完整入库流程测试
# ============================================================

class TestIngestFlow:
    """测试完整入库流程（inserted / merged / coexist）"""

    async def test_inserted_when_no_conflict(self, sample_config):
        """无冲突时新增入库（status=inserted）"""
        factory = _make_mock_llm_factory('{"facts": ["新事实1", "新事实2"]}')
        kb = _make_mock_kb()
        kb.find_similar.return_value = []  # 无相似条目
        kb.add.return_value = "inserted-id"
        ingester = KnowledgeIngester(factory, sample_config)

        result = await ingester.ingest_conversation(
            user_input="问题",
            final_answer="回答",
            summary="有价值的摘要",
            importance_score=0.8,
            conv_id="conv-123",
            user_id="u1",
            knowledge_base=kb,
        )

        assert result.status == "inserted"
        assert result.entry_id == "inserted-id"
        assert result.merged_entry_id == ""
        assert result.conflict_count == 0
        assert "新增" in result.reason
        # 验证 add 被调用
        kb.add.assert_awaited_once()
        # 验证 delete 未被调用
        kb.delete.assert_not_awaited()
        # 验证写入的条目字段
        added_entry = kb.add.await_args.args[0]
        assert added_entry.source == "conversation"
        assert added_entry.source_id == "conv-123"
        assert added_entry.user_id == "u1"
        assert added_entry.version == 1
        assert added_entry.supersedes is None

    async def test_merged_when_high_similarity(self, sample_config):
        """高相似度（>0.95）时合并替换（status=merged）"""
        factory = _make_mock_llm_factory('{"facts": ["更新后的事实"]}')
        kb = _make_mock_kb()
        old_entry = _make_similar_entry(
            entry_id="old-id", distance=0.02, version=2
        )  # sim = 0.98 > 0.95
        kb.find_similar.return_value = [old_entry]
        kb.add.return_value = "merged-new-id"
        ingester = KnowledgeIngester(factory, sample_config)

        result = await ingester.ingest_conversation(
            user_input="问题",
            final_answer="回答",
            summary="摘要",
            importance_score=0.8,
            conv_id="conv-1",
            user_id="u1",
            knowledge_base=kb,
        )

        assert result.status == "merged"
        assert result.entry_id == "merged-new-id"
        assert result.merged_entry_id == "old-id"
        assert result.conflict_count == 1
        assert "合并" in result.reason
        # 验证先 add 后 delete
        kb.add.assert_awaited_once()
        kb.delete.assert_awaited_once_with("old-id")
        # 验证新条目版本递增且 supersedes 指向旧条目
        added_entry = kb.add.await_args.args[0]
        assert added_entry.version == 3  # old.version(2) + 1
        assert added_entry.supersedes == "old-id"

    async def test_coexist_when_medium_similarity(self, sample_config):
        """中相似度（0.80~0.95）时并存（status=coexist）"""
        factory = _make_mock_llm_factory('{"facts": ["并存事实"]}')
        kb = _make_mock_kb()
        old_entry = _make_similar_entry(
            entry_id="old-coexist", distance=0.15, version=1
        )  # sim = 0.85
        kb.find_similar.return_value = [old_entry]
        kb.add.return_value = "coexist-new-id"
        ingester = KnowledgeIngester(factory, sample_config)

        result = await ingester.ingest_conversation(
            user_input="问题",
            final_answer="回答",
            summary="摘要",
            importance_score=0.8,
            conv_id="conv-1",
            user_id="u1",
            knowledge_base=kb,
        )

        assert result.status == "coexist"
        assert result.entry_id == "coexist-new-id"
        assert result.merged_entry_id == ""
        assert result.conflict_count == 1
        assert "并存" in result.reason
        # 并存模式不删除旧条目
        kb.add.assert_awaited_once()
        kb.delete.assert_not_awaited()
        # 验证新条目 version=1，supersedes=None
        added_entry = kb.add.await_args.args[0]
        assert added_entry.version == 1
        assert added_entry.supersedes is None

    async def test_write_failure_returns_skipped(self, sample_config):
        """知识库写入失败时返回 skipped"""
        factory = _make_mock_llm_factory('{"facts": ["事实"]}')
        kb = _make_mock_kb()
        kb.find_similar.return_value = []
        kb.add.side_effect = RuntimeError("写入失败")
        ingester = KnowledgeIngester(factory, sample_config)

        result = await ingester.ingest_conversation(
            user_input="问题",
            final_answer="回答",
            summary="摘要",
            importance_score=0.8,
            conv_id="conv-1",
            user_id="u1",
            knowledge_base=kb,
        )

        assert result.status == "skipped"
        assert "知识写入失败" in result.reason

    async def test_conflict_detection_failure_returns_skipped(self, sample_config):
        """冲突检测失败时返回 skipped"""
        factory = _make_mock_llm_factory('{"facts": ["事实"]}')
        kb = _make_mock_kb()
        kb.find_similar.side_effect = RuntimeError("检索失败")
        ingester = KnowledgeIngester(factory, sample_config)

        result = await ingester.ingest_conversation(
            user_input="问题",
            final_answer="回答",
            summary="摘要",
            importance_score=0.8,
            conv_id="conv-1",
            user_id="u1",
            knowledge_base=kb,
        )

        assert result.status == "skipped"
        assert "冲突检测失败" in result.reason

    async def test_content_built_from_facts(self, sample_config):
        """入库内容由事实拼接为 "- 事实" 列表"""
        factory = _make_mock_llm_factory('{"facts": ["事实A", "事实B"]}')
        kb = _make_mock_kb()
        kb.find_similar.return_value = []
        ingester = KnowledgeIngester(factory, sample_config)

        await ingester.ingest_conversation(
            user_input="问题",
            final_answer="回答",
            summary="摘要",
            importance_score=0.8,
            conv_id="conv-1",
            user_id="u1",
            knowledge_base=kb,
        )

        added_entry = kb.add.await_args.args[0]
        assert "- 事实A" in added_entry.content
        assert "- 事实B" in added_entry.content


# ============================================================
# _detect_conflicts 测试
# ============================================================

class TestDetectConflicts:
    """测试 _detect_conflicts 冲突检测"""

    async def test_no_candidates_returns_insert(self, sample_config):
        """无相似条目返回 insert 动作"""
        factory = MagicMock()
        ingester = KnowledgeIngester(factory, sample_config)
        kb = _make_mock_kb()
        kb.find_similar.return_value = []

        conflict = await ingester._detect_conflicts("内容", "u1", kb)

        assert conflict.action == "insert"
        assert conflict.old_entry is None

    async def test_high_similarity_returns_replace(self, sample_config):
        """相似度 > 0.95 返回 replace 动作"""
        factory = MagicMock()
        ingester = KnowledgeIngester(factory, sample_config)
        kb = _make_mock_kb()
        kb.find_similar.return_value = [
            _make_similar_entry(distance=0.02)  # sim=0.98
        ]

        conflict = await ingester._detect_conflicts("内容", "u1", kb)

        assert conflict.action == "replace"
        assert conflict.old_entry is not None

    async def test_medium_similarity_returns_coexist(self, sample_config):
        """相似度 0.80~0.95 返回 coexist 动作"""
        factory = MagicMock()
        ingester = KnowledgeIngester(factory, sample_config)
        kb = _make_mock_kb()
        kb.find_similar.return_value = [
            _make_similar_entry(distance=0.10)  # sim=0.90
        ]

        conflict = await ingester._detect_conflicts("内容", "u1", kb)

        assert conflict.action == "coexist"
        assert conflict.old_entry is not None

    async def test_picks_highest_similarity_candidate(self, sample_config):
        """多个候选时取相似度最高的"""
        factory = MagicMock()
        ingester = KnowledgeIngester(factory, sample_config)
        kb = _make_mock_kb()
        kb.find_similar.return_value = [
            _make_similar_entry(entry_id="low", distance=0.15),   # sim=0.85
            _make_similar_entry(entry_id="high", distance=0.02),  # sim=0.98
        ]

        conflict = await ingester._detect_conflicts("内容", "u1", kb)

        assert conflict.action == "replace"
        assert conflict.old_entry.entry_id == "high"

    async def test_fallback_to_retrieve_when_no_find_similar(self, sample_config):
        """kb 无 find_similar 方法时降级使用 retrieve"""
        factory = MagicMock()
        ingester = KnowledgeIngester(factory, sample_config)
        kb = _make_mock_kb()
        # 显式置为 None 模拟未实现 find_similar
        kb.find_similar = None
        kb.retrieve.return_value = [
            _make_similar_entry(distance=0.02)  # sim=0.98
        ]

        conflict = await ingester._detect_conflicts("内容", "u1", kb)

        assert conflict.action == "replace"
        assert conflict.old_entry is not None
        kb.retrieve.assert_awaited_once()


# ============================================================
# _parse_facts_response 测试
# ============================================================

class TestParseFactsResponse:
    """测试 _parse_facts_response 多格式解析"""

    def test_parse_pure_json(self, sample_config):
        """纯 JSON 字符串解析"""
        factory = MagicMock()
        ingester = KnowledgeIngester(factory, sample_config)

        resp = _make_llm_response('{"facts": ["事实1", "事实2"]}')
        facts = ingester._parse_facts_response(resp)

        assert facts == ["事实1", "事实2"]

    def test_parse_json_code_block(self, sample_config):
        """```json 代码块包裹的 JSON 解析"""
        factory = MagicMock()
        ingester = KnowledgeIngester(factory, sample_config)

        resp = _make_llm_response('```json\n{"facts": ["代码块事实"]}\n```')
        facts = ingester._parse_facts_response(resp)

        assert facts == ["代码块事实"]

    def test_parse_json_with_surrounding_text(self, sample_config):
        """带前后说明文字的 JSON 解析"""
        factory = MagicMock()
        ingester = KnowledgeIngester(factory, sample_config)

        resp = _make_llm_response('以下是提取结果：{"facts": ["事实A"]} 以上就是。')
        facts = ingester._parse_facts_response(resp)

        assert facts == ["事实A"]

    def test_parse_empty_facts(self, sample_config):
        """空 facts 数组返回空列表"""
        factory = MagicMock()
        ingester = KnowledgeIngester(factory, sample_config)

        resp = _make_llm_response('{"facts": []}')
        facts = ingester._parse_facts_response(resp)

        assert facts == []

    def test_parse_invalid_json_returns_empty(self, sample_config):
        """非法 JSON 返回空列表"""
        factory = MagicMock()
        ingester = KnowledgeIngester(factory, sample_config)

        resp = _make_llm_response("这不是 JSON 内容")
        facts = ingester._parse_facts_response(resp)

        assert facts == []

    def test_parse_facts_not_list_returns_empty(self, sample_config):
        """facts 字段非列表时返回空"""
        factory = MagicMock()
        ingester = KnowledgeIngester(factory, sample_config)

        resp = _make_llm_response('{"facts": "不是列表"}')
        facts = ingester._parse_facts_response(resp)

        assert facts == []

    def test_parse_strips_whitespace(self, sample_config):
        """事实字符串去除首尾空白"""
        factory = MagicMock()
        ingester = KnowledgeIngester(factory, sample_config)

        resp = _make_llm_response('{"facts": ["  带空格的事实  "]}')
        facts = ingester._parse_facts_response(resp)

        assert facts == ["带空格的事实"]

    def test_parse_filters_empty_strings(self, sample_config):
        """空字符串事实被过滤"""
        factory = MagicMock()
        ingester = KnowledgeIngester(factory, sample_config)

        resp = _make_llm_response('{"facts": ["有效事实", "", "  "]}')
        facts = ingester._parse_facts_response(resp)

        assert facts == ["有效事实"]

    def test_parse_missing_facts_key(self, sample_config):
        """缺少 facts 键时返回空列表"""
        factory = MagicMock()
        ingester = KnowledgeIngester(factory, sample_config)

        resp = _make_llm_response('{"other": "value"}')
        facts = ingester._parse_facts_response(resp)

        assert facts == []


# ============================================================
# 数据结构测试
# ============================================================

class TestIngestResult:
    """测试 IngestResult 数据结构"""

    def test_default_values(self):
        """IngestResult 默认值"""
        result = IngestResult(status="inserted")
        assert result.status == "inserted"
        assert result.entry_id == ""
        assert result.merged_entry_id == ""
        assert result.conflict_count == 0
        assert result.importance_score == 0.0
        assert result.reason == ""

    def test_inserted_result(self):
        """inserted 结果字段"""
        result = IngestResult(
            status="inserted",
            entry_id="e-1",
            importance_score=0.7,
            reason="无冲突，新增入库",
        )
        assert result.status == "inserted"
        assert result.entry_id == "e-1"
        assert result.merged_entry_id == ""
        assert result.conflict_count == 0
        assert result.importance_score == 0.7

    def test_merged_result(self):
        """merged 结果字段"""
        result = IngestResult(
            status="merged",
            entry_id="new-1",
            merged_entry_id="old-1",
            conflict_count=1,
            importance_score=0.8,
            reason="已合并替换",
        )
        assert result.status == "merged"
        assert result.merged_entry_id == "old-1"
        assert result.conflict_count == 1

    def test_disabled_result(self):
        """disabled 结果字段"""
        result = IngestResult(status="disabled", reason="L3 知识库未启用")
        assert result.status == "disabled"
        assert result.reason == "L3 知识库未启用"


class TestConflictResult:
    """测试 ConflictResult 数据结构"""

    def test_insert_action(self):
        """insert 动作无旧条目"""
        result = ConflictResult(action="insert", old_entry=None)
        assert result.action == "insert"
        assert result.old_entry is None

    def test_replace_action(self):
        """replace 动作带旧条目"""
        old = KnowledgeEntry(content="旧内容")
        result = ConflictResult(action="replace", old_entry=old)
        assert result.action == "replace"
        assert result.old_entry is old

    def test_coexist_action(self):
        """coexist 动作带旧条目"""
        old = KnowledgeEntry(content="旧内容")
        result = ConflictResult(action="coexist", old_entry=old)
        assert result.action == "coexist"
        assert result.old_entry is old

    def test_default_old_entry_is_none(self):
        """默认 old_entry 为 None"""
        result = ConflictResult(action="insert")
        assert result.old_entry is None
