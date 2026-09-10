"""混合检索（BM25 / RRF 融合 / LLM 重排解析）的单元测试。"""

from __future__ import annotations

import pytest

from app.tools.rag.bm25 import BM25Index, tokenize
from app.tools.rag.hybrid import HybridRetriever
from app.tools.rag.reranker import build_rerank_prompt, parse_ranking


# ============================================================
# tokenize 测试
# ============================================================
class TestTokenize:
    def test_english_lowercased_and_split(self):
        tokens = tokenize("Python Decorator")
        assert "python" in tokens
        assert "decorator" in tokens

    def test_chinese_jieba_segmentation(self):
        tokens = tokenize("装饰器是函数的高级特性")
        assert any("装饰" in t for t in tokens)
        assert any("函数" in t for t in tokens)

    def test_mixed_cjk_keeps_english_word(self):
        tokens = tokenize("Python 装饰器")
        assert "python" in tokens
        assert any("装饰" in t for t in tokens)

    def test_empty_returns_empty(self):
        assert tokenize("") == []
        assert tokenize("   ") == []


# ============================================================
# BM25Index 测试
# ============================================================
class TestBM25Index:
    def _docs(self):
        return [
            {"entry_id": "e1", "content": "Python 装饰器是函数的高级特性"},
            {"entry_id": "e2", "content": "数据库事务的隔离级别与并发控制"},
            {"entry_id": "e3", "content": "装饰器 decorator 是语法糖"},
        ]

    def test_search_returns_relevant_docs(self):
        idx = BM25Index()
        idx.build(self._docs())
        hits = idx.search("装饰器", top_k=2)
        assert hits
        hit_ids = {d["entry_id"] for d, _ in hits}
        # 两条含「装饰器」的文档都应命中，且分数 > 0
        assert hit_ids == {"e1", "e3"}

    def test_search_no_match_returns_empty(self):
        idx = BM25Index()
        idx.build(self._docs())
        # 「量子纠缠」与三条语料无任何共享词元，应无命中
        assert idx.search("量子纠缠", top_k=5) == []

    def test_empty_build_search_returns_empty(self):
        idx = BM25Index()
        idx.build([])
        assert idx.doc_count == 0
        assert idx.search("任何查询") == []


# ============================================================
# RRF 融合测试
# ============================================================
class TestRRFFusion:
    def test_fusion_dedup_and_order(self):
        vec = [
            {"entry_id": "a", "content": "A", "score": 0.9},
            {"entry_id": "b", "content": "B", "score": 0.8},
        ]
        bm25 = [
            {"entry_id": "c", "content": "C", "score": 3.0},
            {"entry_id": "a", "content": "A", "score": 2.0},
        ]
        fused = HybridRetriever._rrf_fuse(vec, bm25)
        ids = [x["entry_id"] for x in fused]
        # a 出现在两路 → 融合分最高；c（bm25 第 1）> b（vec 第 2）
        assert ids[0] == "a"
        assert set(ids) == {"a", "b", "c"}

    def test_fusion_keeps_vector_score(self):
        vec = [{"entry_id": "a", "content": "A", "score": 0.77}]
        fused = HybridRetriever._rrf_fuse(vec, [])
        assert fused[0]["entry_id"] == "a"
        assert fused[0]["score"] == pytest.approx(0.77)

    def test_bm25_only_gets_pseudo_score(self):
        bm25 = [{"entry_id": "x", "content": "X", "score": 5.0}]
        fused = HybridRetriever._rrf_fuse([], bm25)
        assert fused[0]["entry_id"] == "x"
        # rank 0 → 1/(0+2) = 0.5
        assert fused[0]["score"] == pytest.approx(0.5)


# ============================================================
# 重排提示词 / 解析测试
# ============================================================
class TestRerankHelpers:
    def test_build_prompt_contains_query_and_indices(self):
        docs = [{"content": "文档甲"}, {"content": "文档乙"}]
        prompt = build_rerank_prompt("查询", docs)
        assert "查询" in prompt
        assert "[1]" in prompt
        assert "[2]" in prompt
        assert "文档甲" in prompt

    def test_build_prompt_truncates_long_content(self):
        docs = [{"content": "长" * 5000}]
        prompt = build_rerank_prompt("q", docs, max_chars=100)
        assert "…" in prompt

    def test_parse_json_ranking(self):
        assert parse_ranking('{"ranking": [3, 1, 2]}', 3) == [3, 1, 2]

    def test_parse_bare_list(self):
        assert parse_ranking("[2, 1]", 2) == [2, 1]

    def test_parse_filters_out_of_range_and_dedup(self):
        assert parse_ranking('{"ranking": [1, 4, 2]}', 3) == [1, 2]
        assert parse_ranking('{"ranking": [1, 1, 2]}', 2) == [1, 2]

    def test_parse_invalid_returns_empty(self):
        assert parse_ranking("无法解析的内容", 3) == []
        assert parse_ranking("", 3) == []
