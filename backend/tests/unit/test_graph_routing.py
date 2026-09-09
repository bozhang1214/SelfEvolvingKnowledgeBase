"""
Graph 路由的单元测试

测试内容：
- route_after_supervisor: chitchat → "chat_simple"
- route_after_supervisor: clarify → "clarify"
- route_after_supervisor: web_default → "planner"
- route_after_critic: should_replan=True → "planner"
- route_after_critic: should_replan=False → "scribe"
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.agents.strategies.always import AlwaysReflectStrategy
from app.graph.builder import route_after_critic, route_after_supervisor
from app.graph.state import IntentType, ReflectionResult, create_initial_state

# ============================================================
# route_after_supervisor 测试
# ============================================================

class TestRouteAfterSupervisor:
    """测试 Supervisor 后的路由决策"""

    def test_chitchat_routes_to_chat_simple(self):
        """测试闲聊意图路由到 chat_simple"""
        state = create_initial_state("你好", "conv-1")
        state["intent"] = IntentType.CHITCHAT.value
        state["needs_clarification"] = False

        result = route_after_supervisor(state)
        assert result == "chat_simple"

    def test_clarify_routes_to_clarify(self):
        """测试澄清意图路由到 clarify"""
        state = create_initial_state("什么意思", "conv-1")
        state["intent"] = IntentType.CLARIFY.value
        state["needs_clarification"] = False

        result = route_after_supervisor(state)
        assert result == "clarify"

    def test_needs_clarification_routes_to_clarify(self):
        """测试 needs_clarification=True 路由到 clarify（即使 intent 不是 clarify）"""
        state = create_initial_state("模糊输入", "conv-1")
        state["intent"] = IntentType.WEB_DEFAULT.value
        state["needs_clarification"] = True

        result = route_after_supervisor(state)
        assert result == "clarify"

    def test_web_default_routes_to_planner(self):
        """测试默认联网意图路由到 planner"""
        state = create_initial_state("最新新闻", "conv-1")
        state["intent"] = IntentType.WEB_DEFAULT.value
        state["needs_clarification"] = False

        result = route_after_supervisor(state)
        assert result == "planner"

    def test_kb_strict_routes_to_planner(self):
        """测试严格知识库意图路由到 planner"""
        state = create_initial_state("我的笔记", "conv-1")
        state["intent"] = IntentType.KB_STRICT.value
        state["needs_clarification"] = False

        result = route_after_supervisor(state)
        assert result == "planner"

    def test_kb_prefer_routes_to_planner(self):
        """测试优先知识库意图路由到 planner"""
        state = create_initial_state("帮我查一下", "conv-1")
        state["intent"] = IntentType.KB_PREFER.value
        state["needs_clarification"] = False

        result = route_after_supervisor(state)
        assert result == "planner"

    def test_task_plan_routes_to_planner(self):
        """测试复杂任务规划意图路由到 planner"""
        state = create_initial_state("帮我规划一个方案", "conv-1")
        state["intent"] = IntentType.TASK_PLAN.value
        state["needs_clarification"] = False

        result = route_after_supervisor(state)
        assert result == "planner"

    def test_empty_intent_routes_to_planner(self):
        """测试空意图默认路由到 planner"""
        state = create_initial_state("测试", "conv-1")
        state["intent"] = ""
        state["needs_clarification"] = False

        result = route_after_supervisor(state)
        assert result == "planner"

    def test_needs_clarification_takes_priority_over_chitchat(self):
        """测试 needs_clarification 优先于 chitchat 意图"""
        state = create_initial_state("模糊", "conv-1")
        state["intent"] = IntentType.CHITCHAT.value
        state["needs_clarification"] = True

        result = route_after_supervisor(state)
        assert result == "clarify"


# ============================================================
# route_after_critic 测试
# ============================================================

class TestRouteAfterCritic:
    """测试 Critic 后的路由决策"""

    def test_should_replan_true_routes_to_planner(self, sample_config):
        """测试 should_replan=True 时路由到 planner"""
        strategy = AlwaysReflectStrategy(sample_config)
        state = create_initial_state("测试", "conv-1")
        state["evaluation"] = {"result": ReflectionResult.NEEDS_REPLAN.value}
        state["replan_count"] = 0

        result = route_after_critic(state, strategy)
        assert result == "planner"

    def test_should_replan_false_routes_to_scribe(self, sample_config):
        """测试 should_replan=False 时路由到 scribe"""
        strategy = AlwaysReflectStrategy(sample_config)
        state = create_initial_state("测试", "conv-1")
        state["evaluation"] = {"result": ReflectionResult.PASS.value}
        state["replan_count"] = 0

        result = route_after_critic(state, strategy)
        assert result == "scribe"

    def test_replan_limit_reached_routes_to_scribe(self, sample_config):
        """测试重规划次数达上限时路由到 scribe"""
        strategy = AlwaysReflectStrategy(sample_config)
        state = create_initial_state("测试", "conv-1")
        state["evaluation"] = {"result": ReflectionResult.NEEDS_REPLAN.value}
        state["replan_count"] = 2  # 等于 max_replan

        result = route_after_critic(state, strategy)
        assert result == "scribe"

    def test_needs_rewrite_routes_to_scribe(self, sample_config):
        """测试 needs_rewrite 结果路由到 scribe（不重规划）"""
        strategy = AlwaysReflectStrategy(sample_config)
        state = create_initial_state("测试", "conv-1")
        state["evaluation"] = {"result": ReflectionResult.NEEDS_REWRITE.value}
        state["replan_count"] = 0

        result = route_after_critic(state, strategy)
        assert result == "scribe"

    def test_mock_strategy_replan_true(self):
        """测试使用 Mock 策略 should_replan 返回 True 时路由到 planner"""
        mock_strategy = MagicMock()
        mock_strategy.should_replan.return_value = True

        state = create_initial_state("测试", "conv-1")
        result = route_after_critic(state, mock_strategy)
        assert result == "planner"
        mock_strategy.should_replan.assert_called_once_with(state)

    def test_mock_strategy_replan_false(self):
        """测试使用 Mock 策略 should_replan 返回 False 时路由到 scribe"""
        mock_strategy = MagicMock()
        mock_strategy.should_replan.return_value = False

        state = create_initial_state("测试", "conv-1")
        result = route_after_critic(state, mock_strategy)
        assert result == "scribe"
