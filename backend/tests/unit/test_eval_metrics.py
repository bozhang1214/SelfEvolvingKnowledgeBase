"""
评估指标的单元测试

测试内容：
- MetricsCollector.from_state: 正确提取指标
- MetricsCollector.evaluate_threshold: good/warn/bad 等级
- MetricsCollector.to_jsonl: JSON 格式正确
- MetricsCollector.aggregate: 均值/中位数/P95 计算
"""

from __future__ import annotations

import json

import pytest

from app.core.config import MetricThreshold
from app.eval.metrics import MetricsCollector, MetricLevel
from app.graph.state import create_initial_state


# ============================================================
# from_state 测试
# ============================================================

class TestFromState:
    """测试 MetricsCollector.from_state 方法"""

    def test_extract_from_metrics_field(self):
        """测试从 state.metrics 字段提取指标"""
        collector = MetricsCollector()
        state = create_initial_state("测试", "conv-1")
        state["metrics"] = {
            "intent_confidence": 0.92,
            "intent_type": "web_default",
            "plan_step_count": 3,
            "replan_count": 1,
            "tool_success_rate": 0.8,
            "answer_groundedness": 0.85,
            "critic_coherence_score": 7.5,
            "answer_relevance": 0.9,
            "e2e_latency_ms": 3000,
            "total_input_tokens": 500,
            "total_output_tokens": 200,
            "total_cost_usd": 0.01,
            "model_used": ["deepseek-chat"],
        }

        metrics = collector.from_state(state)

        assert metrics["intent_confidence"] == 0.92
        assert metrics["intent_type"] == "web_default"
        assert metrics["plan_step_count"] == 3
        assert metrics["replan_count"] == 1
        assert metrics["tool_success_rate"] == 0.8
        assert metrics["answer_groundedness"] == 0.85
        assert metrics["e2e_latency_ms"] == 3000

    def test_extract_from_other_fields_when_metrics_empty(self):
        """测试 metrics 为空时从其他字段提取"""
        collector = MetricsCollector()
        state = create_initial_state("测试", "conv-1")
        state["intent"] = "chitchat"
        state["intent_confidence"] = 0.88
        state["task_steps"] = [{"step_id": 1}, {"step_id": 2}]
        state["replan_count"] = 1
        state["evaluation"] = {
            "groundedness_score": 0.9,
            "coherence_score": 8.0,
            "relevance_score": 0.85,
        }

        metrics = collector.from_state(state)

        assert metrics["intent_type"] == "chitchat"
        assert metrics["intent_confidence"] == 0.88
        assert metrics["plan_step_count"] == 2
        assert metrics["replan_count"] == 1
        assert metrics["answer_groundedness"] == 0.9
        assert metrics["critic_coherence_score"] == 8.0
        assert metrics["answer_relevance"] == 0.85

    def test_tool_success_rate_from_tool_calls(self):
        """测试从 tool_calls 计算工具成功率"""
        collector = MetricsCollector()
        state = create_initial_state("测试", "conv-1")
        state["tool_calls"] = [
            {"tool_name": "web_search", "success": True},
            {"tool_name": "web_search", "success": True},
            {"tool_name": "web_search", "success": False},
        ]

        metrics = collector.from_state(state)
        # 2/3 成功
        assert metrics["tool_success_rate"] == pytest.approx(2 / 3)

    def test_tool_success_rate_no_tool_calls(self):
        """测试无工具调用时成功率为 1.0"""
        collector = MetricsCollector()
        state = create_initial_state("测试", "conv-1")
        state["tool_calls"] = []

        metrics = collector.from_state(state)
        assert metrics["tool_success_rate"] == 1.0

    def test_non_dict_state_raises(self):
        """测试非 dict 类型的 state 抛出 EvaluationError"""
        collector = MetricsCollector()
        from app.core.exceptions import EvaluationError
        with pytest.raises(EvaluationError):
            collector.from_state("not a dict")  # type: ignore

    def test_default_values_for_empty_state(self):
        """测试空 state 返回默认值"""
        collector = MetricsCollector()
        state = create_initial_state("测试", "conv-1")

        metrics = collector.from_state(state)
        assert metrics["intent_confidence"] == 0.0
        assert metrics["intent_type"] == ""
        assert metrics["plan_step_count"] == 0
        assert metrics["replan_count"] == 0
        assert metrics["total_cost_usd"] == 0.0


# ============================================================
# evaluate_threshold 测试
# ============================================================

class TestEvaluateThreshold:
    """测试 MetricsCollector.evaluate_threshold 方法"""

    def test_higher_is_better_good(self):
        """测试'越大越好'指标达到 good 等级"""
        collector = MetricsCollector()
        # good=0.9, warn=0.7（good > warn，越大越好）
        threshold = MetricThreshold(good=0.9, warn=0.7)
        result = collector.evaluate_threshold("intent_confidence", 0.95, threshold)
        assert result == MetricLevel.GOOD.value

    def test_higher_is_better_warn(self):
        """测试'越大越好'指标达到 warn 等级"""
        collector = MetricsCollector()
        threshold = MetricThreshold(good=0.9, warn=0.7)
        result = collector.evaluate_threshold("intent_confidence", 0.75, threshold)
        assert result == MetricLevel.WARN.value

    def test_higher_is_better_bad(self):
        """测试'越大越好'指标达到 bad 等级"""
        collector = MetricsCollector()
        threshold = MetricThreshold(good=0.9, warn=0.7)
        result = collector.evaluate_threshold("intent_confidence", 0.5, threshold)
        assert result == MetricLevel.BAD.value

    def test_lower_is_better_good(self):
        """测试'越小越好'指标达到 good 等级"""
        collector = MetricsCollector()
        # good=5000, warn=10000（good < warn，越小越好）
        threshold = MetricThreshold(good=5000, warn=10000)
        result = collector.evaluate_threshold("e2e_latency_ms", 3000, threshold)
        assert result == MetricLevel.GOOD.value

    def test_lower_is_better_warn(self):
        """测试'越小越好'指标达到 warn 等级"""
        collector = MetricsCollector()
        threshold = MetricThreshold(good=5000, warn=10000)
        result = collector.evaluate_threshold("e2e_latency_ms", 7000, threshold)
        assert result == MetricLevel.WARN.value

    def test_lower_is_better_bad(self):
        """测试'越小越好'指标达到 bad 等级"""
        collector = MetricsCollector()
        threshold = MetricThreshold(good=5000, warn=10000)
        result = collector.evaluate_threshold("e2e_latency_ms", 15000, threshold)
        assert result == MetricLevel.BAD.value

    def test_none_threshold_returns_good(self):
        """测试 None 阈值默认返回 good"""
        collector = MetricsCollector()
        result = collector.evaluate_threshold("any_metric", 0.5, None)
        assert result == MetricLevel.GOOD.value

    def test_dict_threshold(self):
        """测试 dict 类型的阈值"""
        collector = MetricsCollector()
        threshold = {"good": 0.9, "warn": 0.7}
        result = collector.evaluate_threshold("test", 0.95, threshold)
        assert result == MetricLevel.GOOD.value

    def test_value_equal_to_good_is_good(self):
        """测试值恰好等于 good 时为 good"""
        collector = MetricsCollector()
        threshold = MetricThreshold(good=0.9, warn=0.7)
        result = collector.evaluate_threshold("test", 0.9, threshold)
        assert result == MetricLevel.GOOD.value

    def test_value_equal_to_warn_is_warn(self):
        """测试值恰好等于 warn 时为 warn"""
        collector = MetricsCollector()
        threshold = MetricThreshold(good=0.9, warn=0.7)
        result = collector.evaluate_threshold("test", 0.7, threshold)
        assert result == MetricLevel.WARN.value

    def test_invalid_threshold_type_raises(self):
        """测试非法阈值类型抛出 EvaluationError"""
        collector = MetricsCollector()
        from app.core.exceptions import EvaluationError
        with pytest.raises(EvaluationError):
            collector.evaluate_threshold("test", 0.5, "invalid")  # type: ignore


# ============================================================
# to_jsonl 测试
# ============================================================

class TestToJsonl:
    """测试 MetricsCollector.to_jsonl 方法"""

    def test_valid_json_output(self):
        """测试输出是合法 JSON"""
        collector = MetricsCollector()
        metrics = {
            "intent_confidence": 0.9,
            "intent_type": "chitchat",
            "plan_step_count": 0,
        }
        result = collector.to_jsonl(metrics)
        parsed = json.loads(result)
        assert parsed["intent_confidence"] == 0.9
        assert parsed["intent_type"] == "chitchat"

    def test_single_line_output(self):
        """测试输出是单行（无换行符）"""
        collector = MetricsCollector()
        metrics = {"intent_confidence": 0.9}
        result = collector.to_jsonl(metrics)
        assert "\n" not in result

    def test_preserves_chinese(self):
        """测试中文字符正确保留"""
        collector = MetricsCollector()
        metrics = {"intent_type": "闲聊"}
        result = collector.to_jsonl(metrics)
        assert "闲聊" in result

    def test_empty_metrics(self):
        """测试空指标字典"""
        collector = MetricsCollector()
        result = collector.to_jsonl({})
        assert json.loads(result) == {}


# ============================================================
# aggregate 测试
# ============================================================

class TestAggregate:
    """测试 MetricsCollector.aggregate 方法"""

    def test_empty_list_returns_empty(self):
        """测试空列表返回空字典"""
        collector = MetricsCollector()
        result = collector.aggregate([])
        assert result == {}

    def test_mean_calculation(self):
        """测试均值计算"""
        collector = MetricsCollector()
        metrics_list = [
            {"intent_confidence": 0.8},
            {"intent_confidence": 0.9},
            {"intent_confidence": 1.0},
        ]
        result = collector.aggregate(metrics_list)
        assert result["intent_confidence"]["mean"] == pytest.approx(0.9)

    def test_median_calculation(self):
        """测试中位数计算"""
        collector = MetricsCollector()
        metrics_list = [
            {"intent_confidence": 0.8},
            {"intent_confidence": 0.9},
            {"intent_confidence": 1.0},
        ]
        result = collector.aggregate(metrics_list)
        assert result["intent_confidence"]["median"] == pytest.approx(0.9)

    def test_p95_calculation(self):
        """测试 P95 计算"""
        collector = MetricsCollector()
        # 10 个值：0.1 到 1.0
        metrics_list = [
            {"intent_confidence": 0.1 * (i + 1)} for i in range(10)
        ]
        result = collector.aggregate(metrics_list)
        # P95 nearest-rank: ceil(0.95 * 10) - 1 = 9（索引），即第 10 个值 = 1.0
        assert result["intent_confidence"]["p95"] == pytest.approx(1.0)

    def test_single_item(self):
        """测试单个指标的聚合"""
        collector = MetricsCollector()
        metrics_list = [{"intent_confidence": 0.85}]
        result = collector.aggregate(metrics_list)
        assert result["intent_confidence"]["mean"] == pytest.approx(0.85)
        assert result["intent_confidence"]["median"] == pytest.approx(0.85)
        assert result["intent_confidence"]["p95"] == pytest.approx(0.85)

    def test_multiple_fields(self):
        """测试多个字段同时聚合"""
        collector = MetricsCollector()
        metrics_list = [
            {"intent_confidence": 0.8, "e2e_latency_ms": 1000},
            {"intent_confidence": 0.9, "e2e_latency_ms": 2000},
        ]
        result = collector.aggregate(metrics_list)
        assert "intent_confidence" in result
        assert "e2e_latency_ms" in result
        assert result["e2e_latency_ms"]["mean"] == pytest.approx(1500)

    def test_skips_non_numeric_fields(self):
        """测试跳过非数值字段"""
        collector = MetricsCollector()
        metrics_list = [
            {"intent_confidence": 0.8, "intent_type": "chitchat"},
            {"intent_confidence": 0.9, "intent_type": "web_default"},
        ]
        result = collector.aggregate(metrics_list)
        # intent_type 不在 NUMERIC_METRIC_FIELDS 中，不会被聚合
        assert "intent_type" not in result
        assert "intent_confidence" in result

    def test_skips_missing_fields(self):
        """测试跳过缺失的字段"""
        collector = MetricsCollector()
        metrics_list = [
            {"intent_confidence": 0.8},
            {"e2e_latency_ms": 1000},
        ]
        result = collector.aggregate(metrics_list)
        # intent_confidence 只有一个值
        assert result["intent_confidence"]["mean"] == pytest.approx(0.8)
        assert result["e2e_latency_ms"]["mean"] == pytest.approx(1000)
