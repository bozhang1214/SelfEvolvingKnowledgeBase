"""
评估流水线集成的测试

测试内容：
- MetricsCollector + AssertionEngine 联合工作
- 从 GraphState 提取指标 → 断言检查 → 生成结果
"""

from __future__ import annotations

import json

import pytest

from app.eval.assertion import AssertionEngine
from app.eval.metrics import MetricLevel, MetricsCollector
from app.graph.state import ReflectionResult, create_initial_state

# ============================================================
# 测试夹具
# ============================================================

@pytest.fixture
def collector():
    """创建指标收集器"""
    return MetricsCollector()


@pytest.fixture
def engine(sample_config):
    """创建断言引擎"""
    return AssertionEngine(sample_config)


def _make_full_state(
    intent: str = "web_default",
    confidence: float = 0.92,
    replan_count: int = 1,
    tool_calls: list | None = None,
    groundedness: float = 0.88,
    latency: int = 3000,
) -> dict:
    """构造完整的 GraphState，包含所有评估所需字段"""
    state = create_initial_state("测试输入", "conv-eval")
    state["intent"] = intent
    state["intent_confidence"] = confidence
    state["replan_count"] = replan_count
    state["task_steps"] = [{"step_id": 1}, {"step_id": 2}]
    state["evaluation"] = {
        "result": ReflectionResult.PASS.value,
        "groundedness_score": groundedness,
        "coherence_score": 8.0,
        "relevance_score": 0.85,
    }
    state["tool_calls"] = tool_calls or []
    state["metrics"] = {
        "intent_confidence": confidence,
        "intent_type": intent,
        "plan_step_count": 2,
        "replan_count": replan_count,
        "tool_success_rate": 1.0 if not tool_calls else 1.0,
        "answer_groundedness": groundedness,
        "critic_coherence_score": 8.0,
        "answer_relevance": 0.85,
        "e2e_latency_ms": latency,
        "total_input_tokens": 500,
        "total_output_tokens": 200,
        "total_cost_usd": 0.01,
        "model_used": ["deepseek-chat"],
    }
    return state


# ============================================================
# 评估流水线集成测试
# ============================================================

class TestEvalPipelineIntegration:
    """测试 MetricsCollector + AssertionEngine 联合工作"""

    def test_extract_metrics_then_assert_pass(self, collector, engine):
        """测试从 GraphState 提取指标后断言通过"""
        state = _make_full_state(
            intent="web_default",
            confidence=0.92,
            replan_count=1,
            groundedness=0.88,
        )

        # 1. 提取指标
        metrics = collector.from_state(state)
        assert metrics["intent_type"] == "web_default"
        assert metrics["intent_confidence"] == 0.92

        # 2. 断言检查
        result = engine.check(state, {
            "test_id": "test_pass",
            "intent": "web_default",
            "intent_confidence_min": 0.8,
            "max_replan": 2,
            "min_groundedness": 0.8,
        })

        assert result.passed is True
        assert result.error is None
        assert result.test_id == "test_pass"

    def test_extract_metrics_then_assert_fail(self, collector, engine):
        """测试从 GraphState 提取指标后断言失败"""
        state = _make_full_state(
            intent="chitchat",
            confidence=0.5,
            groundedness=0.4,
        )

        metrics = collector.from_state(state)
        assert metrics["intent_confidence"] == 0.5

        result = engine.check(state, {
            "test_id": "test_fail",
            "intent": "web_default",          # 不匹配
            "intent_confidence_min": 0.8,      # 0.5 < 0.8
            "min_groundedness": 0.8,           # 0.4 < 0.8
        })

        assert result.passed is False
        assert result.error is not None
        assert "intent 不匹配" in result.error
        assert "intent_confidence 过低" in result.error
        assert "answer_groundedness 过低" in result.error

    def test_metric_levels_collected_in_result(self, collector, engine):
        """测试断言结果中包含指标等级"""
        state = _make_full_state(confidence=0.92, latency=3000)

        result = engine.check(state, {"intent": "web_default"})

        # 验证 metric_results 包含各指标的值和等级
        assert "intent_confidence" in result.metric_results
        conf_value, conf_level = result.metric_results["intent_confidence"]
        assert conf_value == 0.92
        assert conf_level == MetricLevel.GOOD.value  # 0.92 >= good=0.9

        assert "e2e_latency_ms" in result.metric_results
        latency_value, latency_level = result.metric_results["e2e_latency_ms"]
        assert latency_value == 3000
        assert latency_level == MetricLevel.GOOD.value  # 3000 <= good=5000

    def test_pipeline_with_tool_calls(self, collector, engine):
        """测试带工具调用的流水线"""
        state = _make_full_state(
            tool_calls=[
                {"tool_name": "web_search", "success": True},
            ],
        )

        metrics = collector.from_state(state)
        assert metrics["tool_success_rate"] == 1.0

        # 期望调用了 web_search
        result = engine.check(state, {
            "should_call_web_search": True,
            "should_not_call_tools": False,
        })
        assert result.passed is True

    def test_pipeline_should_not_call_tools(self, collector, engine):
        """测试期望不调用工具的流水线"""
        state = _make_full_state(tool_calls=[])

        metrics = collector.from_state(state)
        assert metrics["tool_success_rate"] == 1.0

        result = engine.check(state, {"should_not_call_tools": True})
        assert result.passed is True

    def test_pipeline_multiple_states_batch(self, collector, engine):
        """测试多状态批量评估流水线"""
        states = [
            _make_full_state(intent="web_default", confidence=0.95),
            _make_full_state(intent="chitchat", confidence=0.5),
            _make_full_state(intent="web_default", confidence=0.88),
        ]
        expecteds = [
            {"test_id": "case1", "intent": "web_default", "intent_confidence_min": 0.8},
            {"test_id": "case2", "intent": "chitchat", "intent_confidence_min": 0.4},
            {"test_id": "case3", "intent": "web_default", "intent_confidence_min": 0.9},
        ]

        # 提取指标
        metrics_list = [collector.from_state(s) for s in states]

        # 聚合统计
        aggregated = collector.aggregate(metrics_list)
        assert "intent_confidence" in aggregated
        # (0.95 + 0.5 + 0.88) / 3 ≈ 0.7767
        assert aggregated["intent_confidence"]["mean"] == pytest.approx(0.776667, abs=0.01)

        # 批量断言
        results = engine.check_batch(states, expecteds)
        assert len(results) == 3
        assert results[0].passed is True   # confidence 0.95 >= 0.8
        assert results[1].passed is True   # chitchat matches, confidence 0.5 >= 0.4
        assert results[2].passed is False  # confidence 0.88 < 0.9

    def test_pipeline_jsonl_export(self, collector, engine):
        """测试指标导出为 JSONL 格式"""
        state = _make_full_state()
        metrics = collector.from_state(state)

        jsonl = collector.to_jsonl(metrics)
        parsed = json.loads(jsonl)

        assert parsed["intent_type"] == "web_default"
        assert parsed["intent_confidence"] == 0.92
        assert parsed["e2e_latency_ms"] == 3000

    def test_pipeline_replan_at_limit(self, collector, engine):
        """测试重规划次数达上限的评估"""
        state = _make_full_state(replan_count=2)

        # max_replan=2，replan_count=2，恰好等于上限，应通过
        result = engine.check(state, {"max_replan": 2})
        assert result.passed is True

        # max_replan=1，replan_count=2 > 1，应失败
        result = engine.check(state, {"max_replan": 1})
        assert result.passed is False
        assert "replan_count 超限" in result.error

    def test_pipeline_latency_threshold(self, collector, engine):
        """测试延迟阈值断言"""
        # 延迟 6000ms，在 warn(5000~10000) 范围
        state = _make_full_state(latency=6000)

        result = engine.check(state, {"max_latency_ms": 5000})
        assert result.passed is False
        assert "e2e_latency_ms 超限" in result.error

        # 延迟 4000ms，低于 max_latency_ms=5000，应通过
        state_low = _make_full_state(latency=4000)
        result = engine.check(state_low, {"max_latency_ms": 5000})
        assert result.passed is True

    def test_full_pipeline_end_to_end(self, collector, engine):
        """
        测试完整评估流水线：
        GraphState → from_state → evaluate_threshold → check → to_jsonl
        """
        state = _make_full_state(
            intent="web_default",
            confidence=0.95,
            replan_count=0,
            groundedness=0.92,
            latency=2000,
        )

        # 1. 提取指标
        metrics = collector.from_state(state)

        # 2. 评估各指标等级
        thresholds = {
            "intent_confidence": {"good": 0.9, "warn": 0.7},
            "answer_groundedness": {"good": 0.8, "warn": 0.6},
            "e2e_latency_ms": {"good": 5000, "warn": 10000},
        }

        levels = {}
        for metric_name, threshold in thresholds.items():
            value = metrics.get(metric_name, 0)
            level = collector.evaluate_threshold(metric_name, value, threshold)
            levels[metric_name] = (value, level)

        # 所有指标都应为 good
        assert levels["intent_confidence"][1] == MetricLevel.GOOD.value
        assert levels["answer_groundedness"][1] == MetricLevel.GOOD.value
        assert levels["e2e_latency_ms"][1] == MetricLevel.GOOD.value

        # 3. 断言检查
        result = engine.check(state, {
            "test_id": "e2e_test",
            "intent": "web_default",
            "intent_confidence_min": 0.9,
            "max_replan": 2,
            "min_groundedness": 0.8,
            "max_latency_ms": 5000,
        })
        assert result.passed is True

        # 4. 导出 JSONL
        jsonl = collector.to_jsonl(metrics)
        parsed = json.loads(jsonl)
        assert parsed["intent_confidence"] == 0.95
