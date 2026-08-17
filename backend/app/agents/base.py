"""
Agent 基类模块

定义所有 Agent 节点的抽象基类 BaseAgent，统一约定：
- 接收 GraphState，返回部分 state 更新字典
- 依赖 LLMFactory 与 AppConfig
- 提供带容错的 JSON 响应解析
- 使用 structlog 结构化日志

所有具体 Agent（Supervisor / Planner / Executor / Critic / Scribe）
均继承 BaseAgent 并实现 __call__ 方法。
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any

from app.core.config import AppConfig
from app.core.exceptions import AgentError
from app.core.llm_factory import LLMFactory
from app.core.logging import get_logger
from app.graph.state import GraphState

# 匹配 ```json ... ``` 形式的代码块
_JSON_CODE_BLOCK_PATTERN = re.compile(
    r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```",
    re.DOTALL,
)


class BaseAgent(ABC):
    """
    所有 Agent 节点的抽象基类。

    子类必须实现 __call__ 方法，接收 GraphState 并返回 state 更新字典。
    基类提供 LLM 工厂、配置和 logger 的注入，以及 JSON 响应容错解析。

    Attributes:
        llm_factory: LLM 工厂实例，用于获取配置好的 LLM
        config: 应用全局配置
        logger: 结构化 logger
    """

    def __init__(self, llm_factory: LLMFactory, config: AppConfig) -> None:
        """
        初始化 Agent 基类。

        Args:
            llm_factory: LLM 工厂实例
            config: 应用全局配置
        """
        self.llm_factory: LLMFactory = llm_factory
        self.config: AppConfig = config
        self.logger = get_logger(self.__class__.__name__)

    @abstractmethod
    async def __call__(self, state: GraphState) -> dict[str, Any]:
        """
        执行 Agent 逻辑，返回 state 更新字典。

        作为 LangGraph 节点入口，由图运行时调用。

        Args:
            state: 当前全局 GraphState

        Returns:
            state 更新字典（仅包含需要更新的字段）

        Raises:
            AgentError: 执行失败时抛出（子类应在内部捕获并降级）
        """
        ...

    async def _parse_json_response(self, response: Any) -> dict:
        """
        解析 LLM 的 JSON 响应，容错处理。

        LLM 可能返回：
        1. 纯 JSON 字符串
        2. ```json ... ``` 包裹的代码块
        3. 带前后说明文字的 JSON
        4. 非法 JSON（此时抛出 AgentError）

        Args:
            response: LLM 响应对象（BaseMessage 或字符串）

        Returns:
            解析后的字典

        Raises:
            AgentError: JSON 解析失败
        """
        content = (
            response.content
            if hasattr(response, "content")
            else str(response)
        )
        if not isinstance(content, str):
            content = str(content)

        text = content.strip()

        # 1. 尝试直接解析
        try:
            result = json.loads(text)
            if isinstance(result, dict):
                return result
        except (json.JSONDecodeError, ValueError):
            pass

        # 2. 尝试从 ```json ... ``` 代码块提取
        match = _JSON_CODE_BLOCK_PATTERN.search(text)
        if match:
            try:
                result = json.loads(match.group(1))
                if isinstance(result, dict):
                    return result
            except (json.JSONDecodeError, ValueError):
                pass

        # 3. 兜底：截取第一个 { 到最后一个 } 之间的内容
        first_brace = text.find("{")
        last_brace = text.rfind("}")
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            candidate = text[first_brace : last_brace + 1]
            try:
                result = json.loads(candidate)
                if isinstance(result, dict):
                    return result
            except (json.JSONDecodeError, ValueError):
                pass

        self.logger.warning(
            "JSON 解析失败，返回空字典",
            raw_content_preview=text[:200],
        )
        raise AgentError(
            f"LLM 返回内容无法解析为 JSON: {text[:200]}",
            agent_name=self.__class__.__name__,
        )
