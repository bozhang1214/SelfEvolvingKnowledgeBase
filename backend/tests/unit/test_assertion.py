"""
断言引擎的单元测试

测试内容：
- 检查 intent 匹配
- 检查 intent_confidence_min
- 检查 should_not_call_tools
- 检查 max_replan
- 批量检查
"""

from __future__ import annotations

import pytest

from app.eval.assertion import AssertionEngine, AssertionResult
from app.graph.state import create_initial_state


# ============================================================
# 测试夹具
# ============================================================

@pytest.fixture
def assertion_engine(sample_config):
    """创建断言引擎实例"""
    return AssertionEngine(sample_config)


@pytest.fixture
def base_state(sample_config):
    """创建带完整指标的测试状态"""
    state = create_initial_state("测试输入", "conv-test")
    state["intent"] = "web_default"
    state["intent_confidence"] = 0.92
    state["task_steps"] = [{"step_id": 1}, {"step_id": 2}]
    state["replan_count"] = 1
    state["evaluation"] = {
        "groundedness_score": 0.88,
        "coherence_score": 8.0,
        "relevance_score": 0.85,
    }
    state["tool_calls"] = []
    state["metrics"] = {
        "intent_confidence": 0.92,
        "intent_type": "web_default",
        "plan_step_count": 2,
        "replan_count": 1,
        "tool_success_rate": 1.0,
        "answer_groundedness": 0.88,
        "critic_coherence_score": 8.0,
        "answer_relevance": 0.85,
        "e2e_latency_ms": 3000,
        "total_input_tokens": 500,
        "total_output_tokens": 200,
        "total_cost_usd": 0.01,
        "model_used": ["deepseek-chat"],
    }
    return state


# ============================================================
# intent 匹配测试
# ============================================================

class TestAssertIntent:
    """测试 intent 断言"""

    def test_intent_match_passes(self, assertion_engine, base_state):
        """测试 intent 匹配时通过"""
        result = assertion_engine.check(base_state, {"intent": "web_default"})
        assert result.passed is True

    def test_intent_mismatch_fails(self, assertion_engine, base_state):
        """测试 intent 不匹配时失败"""
        result = assertion_engine.check(base_state, {"intent": "chitchat"})
        assert result.passed is False
        assert "intent 不匹配" in result.error


# ============================================================
# intent_confidence_min 测试
# ============================================================

class TestAssertIntentConfidence:
    """测试 intent_confidence_min 断言"""

    def test_confidence_above_min_passes(self, assertion_engine, base_state):
        """测试置信度高于阈值时通过"""
        result = assertion_engine.check(base_state, {"intent_confidence_min": 0.8})
        assert result.passed is True

    def test_confidence_below_min_fails(self, assertion_engine, base_state):
        """测试置信度低于阈值时失败"""
        result = assertion_engine.check(base_state, {"intent_confidence_min": 0.95})
        assert result.passed is False
        assert "intent_confidence 过低" in result.error

    def test_confidence_equal_to_min_passes(self, assertion_engine, base_state):
        """测试置信度等于阈值时通过"""
        result = assertion_engine.check(base_state, {"intent_confidence_min": 0.92})
        assert result.passed is True


# ============================================================
# should_not_call_tools 测试
# ============================================================

class TestAssertShouldNotCallTools:
    """测试 should_not_call_tools 断言"""

    def test_no_tools_called_passes(self, assertion_engine, base_state):
        """测试未调用工具时通过"""
        base_state["tool_calls"] = []
        result = assertion_engine.check(base_state, {"should_not_call_tools": True})
        assert result.passed is True

    def test_tools_called_fails(self, assertion_engine, base_state):
        """测试调用了工具时失败"""
        base_state["tool_calls"] = [
            {"tool_name": "web_search", "success": True},
        ]
        result = assertion_engine.check(base_state, {"should_not_call_tools": True})
        assert result.passed is False
        assert "期望不调用工具" in result.error

    def test_should_not_call_tools_false_allows_tools(self, assertion_engine, base_state):
        """测试 should_not_call_tools=False 时允许调用工具"""
        base_state["tool_calls"] = [
            {"tool_name": "web_search", "success": True},
        ]
        result = assertion_engine.check(base_state, {"should_not_call_tools": False})
        assert result.passed is True


# ============================================================
# max_replan 测试
# ============================================================

class TestAssertMaxReplan:
    """测试 max_replan 断言"""

    def test_replan_under_max_passes(self, assertion_engine, base_state):
        """测试重规划次数低于上限时通过"""
        base_state["metrics"]["replan_count"] = 1
        result = assertion_engine.check(base_state, {"max_replan": 2})
        assert result.passed is True

    def test_replan_over_max_fails(self, assertion_engine, base_state):
        """测试重规划次数超过上限时失败"""
        base_state["metrics"]["replan_count"] = 3
        result = assertion_engine.check(base_state, {"max_replan": 2})
        assert result.passed is False
        assert "replan_count 超限" in result.error

    def test_replan_equal_to_max_passes(self, assertion_engine, base_state):
        """测试重规划次数等于上限时通过"""
        base_state["metrics"]["replan_count"] = 2
        result = assertion_engine.check(base_state, {"max_replan": 2})
        assert result.passed is True


# ============================================================
# should_call_web_search 测试
# ============================================================

class TestAssertShouldCallWebSearch:
    """测试 should_call_web_search 断言"""

    def test_web_search_called_passes(self, assertion_engine, base_state):
        """测试调用了 web_search 时通过"""
        base_state["tool_calls"] = [
            {"tool_name": "web_search", "success": True},
        ]
        result = assertion_engine.check(base_state, {"should_call_web_search": True})
        assert result.passed is True

    def test_web_search_not_called_fails(self, assertion_engine, base_state):
        """测试未调用 web_search 时失败"""
        base_state["tool_calls"] = []
        result = assertion_engine.check(base_state, {"should_call_web_search": True})
        assert result.passed is False
        assert "期望调用 web_search" in result.error


# ============================================================
# min_groundedness 测试
# ============================================================

class TestAssertMinGroundedness:
    """测试 min_groundedness 断言"""

    def test_groundedness_above_min_passes(self, assertion_engine, base_state):
        """测试锚定度高于阈值时通过"""
        result = assertion_engine.check(base_state, {"min_groundedness": 0.8})
        assert result.passed is True

    def test_groundedness_below_min_fails(self, assertion_engine, base_state):
        """测试锚定度低于阈值时失败"""
        result = assertion_engine.check(base_state, {"min_groundedness": 0.95})
        assert result.passed is False
        assert "answer_groundedness 过低" in result.error


# ============================================================
# 批量检查测试
# ============================================================

class TestCheckBatch:
    """测试 check_batch 方法"""

    def test_batch_all_pass(self, assertion_engine, base_state):
        """测试批量检查全部通过"""
        states = [base_state, base_state]
        expecteds = [
            {"intent": "web_default"},
            {"intent_confidence_min": 0.8},
        ]
        results = assertion_engine.check_batch(states, expecteds)

        assert len(results) == 2
        assert all(r.passed for r in results)

    def test_batch_mixed_results(self, assertion_engine, base_state):
        """测试批量检查结果混合"""
        states = [base_state, base_state]
        expecteds = [
            {"intent": "web_default"},      # 通过
            {"intent": "chitchat"},          # 失败
        ]
        results = assertion_engine.check_batch(states, expecteds)

        assert len(results) == 2
        assert results[0].passed is True
        assert results[1].passed is False

    def test_batch_length_mismatch_raises(self, assertion_engine, base_state):
        """测试 states 与 expecteds 长度不一致时抛出异常"""
        from app.core.exceptions import EvaluationError

        states = [base_state]
        expecteds = [{"intent": "a"}, {"intent": "b"}]
        with pytest.raises(EvaluationError):
            assertion_engine.check_batch(states, expecteds)

    def test_batch_empty(self, assertion_engine):
        """测试空批量检查返回空列表"""
        results = assertion_engine.check_batch([], [])
        assert results == []

    def test_batch_preserves_order(self, assertion_engine, base_state):
        """测试批量检查结果顺序与输入一致"""
        states = [base_state, base_state, base_state]
        expecteds = [
            {"test_id": "case1", "intent": "web_default"},
            {"test_id": "case2", "intent": "chitchat"},
            {"test_id": "case3", "intent": "web_default"},
        ]
        results = assertion_engine.check_batch(states, expecteds)

        assert results[0].test_id == "case1"
        assert results[1].test_id == "case2"
        assert results[2].test_id == "case3"
        assert results[0].passed is True
        assert results[1].passed is False
        assert results[2].passed is True


# ============================================================
# AssertionResult 数据结构测试
# ============================================================

class TestAssertionResult:
    """测试 AssertionResult 数据类"""

    def test_result_has_test_id(self, assertion_engine, base_state):
        """测试结果包含 test_id"""
        result = assertion_engine.check(base_state, {"test_id": "my-test", "intent": "web_default"})
        assert result.test_id == "my-test"

    def test_result_default_test_id(self, assertion_engine, base_state):
        """测试未指定 test_id 时默认为 unknown"""
        result = assertion_engine.check(base_state, {"intent": "web_default"})
        assert result.test_id == "unknown"

    def test_result_contains_metric_levels(self, assertion_engine, base_state):
        """测试结果包含指标等级"""
        result = assertion_engine.check(base_state, {"intent": "web_default"})
        assert "intent_confidence" in result.metric_results
        value, level = result.metric_results["intent_confidence"]
        assert value == 0.92
        assert level in ("good", "warn", "bad")

    def test_result_error_none_when_passed(self, assertion_engine, base_state):
        """测试通过时 error 为 None"""
        result = assertion_engine.check(base_state, {"intent": "web_default"})
        assert result.passed is True
        assert result.error is None
