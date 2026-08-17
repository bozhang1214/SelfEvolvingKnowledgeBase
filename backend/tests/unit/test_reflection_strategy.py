"""
反思策略的单元测试

测试内容：
- AlwaysReflectStrategy.should_reflect: chitchat 返回 False
- AlwaysReflectStrategy.should_reflect: clarify 返回 False
- AlwaysReflectStrategy.should_reflect: web_default 返回 True
- AlwaysReflectStrategy.should_replan: needs_replan + 未超限 返回 True
- AlwaysReflectStrategy.should_replan: needs_replan + 超限 返回 False
- AlwaysReflectStrategy.should_replan: pass 返回 False
- create_reflection_strategy: "always" 返回正确类型
- create_reflection_strategy: 未知策略抛出 ConfigError
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.agents.strategies.always import AlwaysReflectStrategy
from app.agents.strategies.base import ReflectionStrategy
from app.agents.strategies.factory import create_reflection_strategy
from app.core.exceptions import ConfigError
from app.graph.state import IntentType, ReflectionResult, create_initial_state


# ============================================================
# should_reflect 测试
# ============================================================

class TestShouldReflect:
    """测试 AlwaysReflectStrategy.should_reflect 方法"""

    def test_chitchat_returns_false(self, sample_config):
        """测试闲聊意图跳过反思"""
        strategy = AlwaysReflectStrategy(sample_config)
        state = create_initial_state("你好", "conv-1")
        state["intent"] = IntentType.CHITCHAT.value

        assert strategy.should_reflect(state) is False

    def test_clarify_returns_false(self, sample_config):
        """测试澄清意图跳过反思"""
        strategy = AlwaysReflectStrategy(sample_config)
        state = create_initial_state("什么意思", "conv-1")
        state["intent"] = IntentType.CLARIFY.value

        assert strategy.should_reflect(state) is False

    def test_web_default_returns_true(self, sample_config):
        """测试默认联网意图需要反思"""
        strategy = AlwaysReflectStrategy(sample_config)
        state = create_initial_state("最新新闻", "conv-1")
        state["intent"] = IntentType.WEB_DEFAULT.value

        assert strategy.should_reflect(state) is True

    def test_kb_strict_returns_true(self, sample_config):
        """测试严格知识库意图需要反思"""
        strategy = AlwaysReflectStrategy(sample_config)
        state = create_initial_state("我的笔记", "conv-1")
        state["intent"] = IntentType.KB_STRICT.value

        assert strategy.should_reflect(state) is True

    def test_task_plan_returns_true(self, sample_config):
        """测试复杂任务规划意图需要反思"""
        strategy = AlwaysReflectStrategy(sample_config)
        state = create_initial_state("帮我规划", "conv-1")
        state["intent"] = IntentType.TASK_PLAN.value

        assert strategy.should_reflect(state) is True

    def test_empty_intent_returns_true(self, sample_config):
        """测试空意图默认需要反思（非 chitchat/clarify）"""
        strategy = AlwaysReflectStrategy(sample_config)
        state = create_initial_state("测试", "conv-1")
        state["intent"] = ""

        assert strategy.should_reflect(state) is True


# ============================================================
# should_replan 测试
# ============================================================

class TestShouldReplan:
    """测试 AlwaysReflectStrategy.should_replan 方法"""

    def test_needs_replan_under_limit_returns_true(self, sample_config):
        """测试 needs_replan 且未超限时返回 True"""
        strategy = AlwaysReflectStrategy(sample_config)
        state = create_initial_state("测试", "conv-1")
        state["evaluation"] = {"result": ReflectionResult.NEEDS_REPLAN.value}
        state["replan_count"] = 0
        # max_replan 默认为 2

        assert strategy.should_replan(state) is True

    def test_needs_replan_at_limit_returns_false(self, sample_config):
        """测试 needs_replan 且已达上限时返回 False"""
        strategy = AlwaysReflectStrategy(sample_config)
        state = create_initial_state("测试", "conv-1")
        state["evaluation"] = {"result": ReflectionResult.NEEDS_REPLAN.value}
        state["replan_count"] = 2  # 等于 max_replan=2

        assert strategy.should_replan(state) is False

    def test_needs_replan_over_limit_returns_false(self, sample_config):
        """测试 needs_replan 且超过上限时返回 False"""
        strategy = AlwaysReflectStrategy(sample_config)
        state = create_initial_state("测试", "conv-1")
        state["evaluation"] = {"result": ReflectionResult.NEEDS_REPLAN.value}
        state["replan_count"] = 5  # 超过 max_replan=2

        assert strategy.should_replan(state) is False

    def test_pass_returns_false(self, sample_config):
        """测试 pass 结果不重规划"""
        strategy = AlwaysReflectStrategy(sample_config)
        state = create_initial_state("测试", "conv-1")
        state["evaluation"] = {"result": ReflectionResult.PASS.value}
        state["replan_count"] = 0

        assert strategy.should_replan(state) is False

    def test_needs_rewrite_returns_false(self, sample_config):
        """测试 needs_rewrite 结果不重规划"""
        strategy = AlwaysReflectStrategy(sample_config)
        state = create_initial_state("测试", "conv-1")
        state["evaluation"] = {"result": ReflectionResult.NEEDS_REWRITE.value}
        state["replan_count"] = 0

        assert strategy.should_replan(state) is False

    def test_empty_evaluation_returns_false(self, sample_config):
        """测试空评估结果不重规划"""
        strategy = AlwaysReflectStrategy(sample_config)
        state = create_initial_state("测试", "conv-1")
        state["evaluation"] = {}
        state["replan_count"] = 0

        assert strategy.should_replan(state) is False

    def test_replan_just_below_limit_returns_true(self, sample_config):
        """测试 replan_count 恰好小于 max_replan 时返回 True"""
        strategy = AlwaysReflectStrategy(sample_config)
        state = create_initial_state("测试", "conv-1")
        state["evaluation"] = {"result": ReflectionResult.NEEDS_REPLAN.value}
        state["replan_count"] = 1  # max_replan=2, 1 < 2

        assert strategy.should_replan(state) is True


# ============================================================
# create_reflection_strategy 工厂测试
# ============================================================

class TestCreateReflectionStrategy:
    """测试 create_reflection_strategy 工厂函数"""

    def test_always_returns_always_strategy(self, sample_config):
        """测试 'always' 策略返回 AlwaysReflectStrategy 实例"""
        sample_config.reflection.policy = "always"
        strategy = create_reflection_strategy(sample_config)
        assert isinstance(strategy, AlwaysReflectStrategy)
        assert isinstance(strategy, ReflectionStrategy)

    def test_adaptive_falls_back_to_always(self, sample_config):
        """测试 'adaptive' 策略降级为 AlwaysReflectStrategy"""
        sample_config.reflection.policy = "adaptive"
        strategy = create_reflection_strategy(sample_config)
        assert isinstance(strategy, AlwaysReflectStrategy)

    def test_sampling_falls_back_to_always(self, sample_config):
        """测试 'sampling' 策略降级为 AlwaysReflectStrategy"""
        sample_config.reflection.policy = "sampling"
        strategy = create_reflection_strategy(sample_config)
        assert isinstance(strategy, AlwaysReflectStrategy)

    def test_unknown_policy_raises_config_error(self):
        """测试未知策略名抛出 ConfigError"""
        # 使用 Mock 绕过 Pydantic 校验以测试未知策略路径
        mock_config = MagicMock()
        mock_config.reflection.policy = "unknown_policy"
        mock_config.reflection.max_replan = 2

        with pytest.raises(ConfigError, match="未知的反思策略"):
            create_reflection_strategy(mock_config)

    def test_strategy_inherits_max_replan(self, sample_config):
        """测试策略实例继承配置中的 max_replan"""
        sample_config.reflection.max_replan = 5
        strategy = create_reflection_strategy(sample_config)
        assert strategy.max_replan == 5

    def test_get_strategy_name(self, sample_config):
        """测试 get_strategy_name 返回类名"""
        strategy = AlwaysReflectStrategy(sample_config)
        assert strategy.get_strategy_name() == "AlwaysReflectStrategy"
