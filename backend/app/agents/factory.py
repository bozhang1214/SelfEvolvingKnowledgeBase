"""
Agent 工厂模块

AgentFactory 统一创建所有 Agent 节点，集中管理依赖注入：
- LLMFactory: 提供 LLM 实例
- AppConfig: 全局配置
- ToolRegistry: 工具注册表（Executor 依赖）
- ShortTermMemory: 短期记忆（Executor 依赖）

使用方式：
    from app.agents.factory import AgentFactory
    factory = AgentFactory(llm_factory, config, tool_registry, memory)
    supervisor = factory.create_supervisor()
    all_agents = factory.create_all()
"""

from __future__ import annotations

from app.agents.base import BaseAgent
from app.agents.critic import CriticAgent
from app.agents.executor import ExecutorAgent
from app.agents.planner import PlannerAgent
from app.agents.scribe import ScribeAgent
from app.agents.supervisor import SupervisorAgent
from app.core.config import AppConfig
from app.core.llm_factory import LLMFactory
from app.memory.short_term import ShortTermMemory
from app.tools.registry import ToolRegistry


class AgentFactory:
    """
    Agent 工厂：统一创建所有 Agent 节点。

    通过依赖注入的方式创建 Agent，避免每个 Agent 各自管理依赖。
    同一工厂实例创建的 Agent 共享同一组 LLMFactory / Config / 工具与记忆。

    Attributes:
        llm_factory: LLM 工厂实例
        config: 应用全局配置
        tool_registry: 工具注册表
        memory: 短期记忆实例
    """

    def __init__(
        self,
        llm_factory: LLMFactory,
        config: AppConfig,
        tool_registry: ToolRegistry,
        memory: ShortTermMemory,
    ) -> None:
        """
        初始化 Agent 工厂。

        Args:
            llm_factory: LLM 工厂实例
            config: 应用全局配置
            tool_registry: 工具注册表
            memory: 短期记忆实例
        """
        self.llm_factory: LLMFactory = llm_factory
        self.config: AppConfig = config
        self.tool_registry: ToolRegistry = tool_registry
        self.memory: ShortTermMemory = memory

    def create_supervisor(self) -> SupervisorAgent:
        """
        创建监督者 Agent。

        Returns:
            SupervisorAgent 实例
        """
        return SupervisorAgent(self.llm_factory, self.config)

    def create_planner(self) -> PlannerAgent:
        """
        创建规划者 Agent。

        Returns:
            PlannerAgent 实例
        """
        return PlannerAgent(self.llm_factory, self.config)

    def create_executor(self) -> ExecutorAgent:
        """
        创建执行器 Agent。

        Executor 需要额外的 ToolRegistry 与 ShortTermMemory 依赖。

        Returns:
            ExecutorAgent 实例
        """
        return ExecutorAgent(
            self.llm_factory,
            self.config,
            self.tool_registry,
            self.memory,
        )

    def create_critic(self) -> CriticAgent:
        """
        创建审查者 Agent。

        Returns:
            CriticAgent 实例
        """
        return CriticAgent(self.llm_factory, self.config)

    def create_scribe(self) -> ScribeAgent:
        """
        创建记录员 Agent。

        Returns:
            ScribeAgent 实例
        """
        return ScribeAgent(self.llm_factory, self.config)

    def create_all(self) -> dict[str, BaseAgent]:
        """
        创建所有 Agent 节点。

        Returns:
            字典：{"supervisor": ..., "planner": ..., "executor": ...,
                   "critic": ..., "scribe": ...}
        """
        return {
            "supervisor": self.create_supervisor(),
            "planner": self.create_planner(),
            "executor": self.create_executor(),
            "critic": self.create_critic(),
            "scribe": self.create_scribe(),
        }
