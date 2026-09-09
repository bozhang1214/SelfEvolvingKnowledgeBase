"""
反思策略接口定义

定义反思策略的抽象基类，所有具体策略需继承此类。
策略模式使得反思行为可通过配置文件切换，无需修改代码。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.core.config import AppConfig
from app.graph.state import GraphState


class ReflectionStrategy(ABC):
    """
    反思策略抽象基类。

    策略决定是否对当前对话执行反思（Critic 评估）。
    通过 config.reflection.policy 选择具体策略。
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self.max_replan = config.reflection.max_replan

    @abstractmethod
    def should_reflect(self, state: GraphState) -> bool:
        """
        判断当前对话是否需要执行反思。

        Args:
            state: 当前 GraphState

        Returns:
            True 如果应该执行反思
        """
        ...

    @abstractmethod
    def should_replan(self, state: GraphState) -> bool:
        """
        判断是否应该重规划（在 Critic 不通过时）。

        Args:
            state: 当前 GraphState（包含 Critic 评估结果）

        Returns:
            True 如果应该重规划
        """
        ...

    def get_strategy_name(self) -> str:
        """返回策略名称（用于日志）"""
        return self.__class__.__name__
