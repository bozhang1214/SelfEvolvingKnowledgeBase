"""
AlwaysReflect 策略 - 默认策略

每次对话都执行反思，不跳过任何评估。
适用于质量优先场景，缺点是成本较高。
"""

from __future__ import annotations

from app.agents.strategies.base import ReflectionStrategy
from app.core.logging import get_logger
from app.graph.state import GraphState, IntentType, ReflectionResult

logger = get_logger(__name__)


class AlwaysReflectStrategy(ReflectionStrategy):
    """
    始终反思策略（默认）。

    - 对所有非澄清的对话执行 Critic 反思
    - Critic 不通过时，在 max_replan 范围内允许重规划
    """

    def should_reflect(self, state: GraphState) -> bool:
        """
        判断是否需要反思。

        跳过反思的情况：
        1. 意图为 clarify（澄清问题不需要反思）
        2. 意图为 chitchat（闲聊不需要反思）

        Args:
            state: 当前 GraphState

        Returns:
            True 如果应该执行反思
        """
        intent = state.get("intent", "")

        # 澄清和闲聊跳过反思
        if intent in (IntentType.CLARIFY.value, IntentType.CHITCHAT.value):
            logger.debug(
                "跳过反思",
                intent=intent,
                reason="clarify/chitchat 不需要反思",
            )
            return False

        return True

    def should_replan(self, state: GraphState) -> bool:
        """
        判断是否应该重规划。

        条件：
        1. Critic 评估结果为 needs_replan
        2. 已重规划次数 < max_replan

        Args:
            state: 当前 GraphState（包含 Critic 评估结果）

        Returns:
            True 如果应该重规划
        """
        evaluation = state.get("evaluation", {})
        result = evaluation.get("result", "")
        replan_count = state.get("replan_count", 0)

        if result == ReflectionResult.NEEDS_REPLAN.value:
            if replan_count < self.max_replan:
                logger.info(
                    "触发重规划",
                    replan_count=replan_count,
                    max_replan=self.max_replan,
                    reason=evaluation.get("reasoning", ""),
                )
                return True
            else:
                logger.warning(
                    "重规划次数已达上限，返回当前最佳答案",
                    replan_count=replan_count,
                    max_replan=self.max_replan,
                )
                return False

        return False
