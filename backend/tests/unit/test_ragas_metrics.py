"""RAGAS 式检索质量指标（提示词构建 / 分数解析）的单元测试。"""

from __future__ import annotations

from app.eval.rag_runner import RagEvalCase, RagEvalReport
from app.eval.ragas_metrics import (
    build_answer_relevance_prompt,
    build_context_precision_prompt,
    build_context_recall_prompt,
    build_faithfulness_prompt,
    parse_judge_score,
)


# ============================================================
# parse_judge_score 测试
# ============================================================
class TestParseJudgeScore:
    def test_json_dict(self):
        assert parse_judge_score('{"score": 0.8, "reason": "ok"}') == 0.8

    def test_bare_float(self):
        assert parse_judge_score("0.65") == 0.65

    def test_score_field_regex_with_text(self):
        assert parse_judge_score('说明文字 {"score": 0.42} 结尾') == 0.42

    def test_clamp_out_of_range(self):
        assert parse_judge_score('{"score": 1.5}') == 1.0
        assert parse_judge_score('{"score": -0.3}') == 0.0

    def test_invalid_returns_none(self):
        assert parse_judge_score("无法解析") is None
        assert parse_judge_score("") is None

    def test_int_score(self):
        assert parse_judge_score('{"score": 1}') == 1.0


# ============================================================
# 提示词构建测试
# ============================================================
class TestBuildPrompts:
    def test_context_recall_prompt(self):
        p = build_context_recall_prompt("查询", ["上下文A"], "标准答案")
        assert "查询" in p
        assert "[1] 上下文A" in p
        assert "标准答案" in p

    def test_context_precision_prompt_numbers_contexts(self):
        p = build_context_precision_prompt("q", ["甲", "乙"])
        assert "[1] 甲" in p
        assert "[2] 乙" in p

    def test_faithfulness_prompt_contains_answer(self):
        p = build_faithfulness_prompt("q", ["ctx"], "答案内容")
        assert "答案内容" in p

    def test_answer_relevance_prompt(self):
        p = build_answer_relevance_prompt("问题", "回答")
        assert "问题" in p
        assert "回答" in p


# ============================================================
# RagEvalReport 测试
# ============================================================
class TestRagEvalReport:
    def test_to_markdown_contains_headers_and_values(self):
        report = RagEvalReport(
            cases=[
                RagEvalCase(
                    test_id="R1",
                    query="q",
                    context_recall=0.9,
                    context_precision=0.8,
                    faithfulness=0.7,
                    answer_relevance=0.6,
                )
            ],
            aggregated={"context_recall": 0.9},
        )
        md = report.to_markdown()
        assert "# RAG 检索质量评测报告" in md
        assert "context_recall" in md
        assert "0.900" in md

    def test_none_scores_render_dash(self):
        report = RagEvalReport(
            cases=[
                RagEvalCase(
                    test_id="R1",
                    query="q",
                    context_recall=None,
                    context_precision=None,
                    faithfulness=None,
                    answer_relevance=None,
                )
            ]
        )
        md = report.to_markdown()
        assert "| — | — | — | — |" in md
