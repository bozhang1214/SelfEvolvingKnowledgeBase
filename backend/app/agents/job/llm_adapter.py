"""JobCopilot 内核 ↔ SEKB LLM 工厂 的适配器。

内核的 ``LLMPort`` 只认「角色名 + 中性消息 → 文本」，SEKB 的
``LLMFactory`` 认的是 LangChain 消息对象。适配器做这一层转换，
让内核保持零依赖，同时 SEKB 继续享受现有的**模型路由 / 计费 / 重试 / 降级**。
"""
from __future__ import annotations

from typing import Any

from jobcopilot.core.messages import Message
from langchain_core.messages import HumanMessage, SystemMessage

# 消息角色 → LangChain 消息类
_LC_TYPES = {
    "system": SystemMessage,
    "user": HumanMessage,
}


def to_langchain(messages: list[Message]) -> list[Any]:
    """把内核的中性消息转成 LangChain 消息对象。

    未识别的角色一律按 user 处理（宁可用错角色，也不要让整条分析链断掉）。
    """
    return [_LC_TYPES.get(m.role, HumanMessage)(content=m.content) for m in messages]


class SekbLLMAdapter:
    """把 SEKB 的 ``LLMFactory`` 包装成内核的 ``LLMPort``。

    Args:
        llm_factory: SEKB 的 LLM 工厂（需提供 ``ainvoke_with_stats``）。
    """

    def __init__(self, llm_factory: Any) -> None:
        self._factory = llm_factory

    async def complete(self, role: str, messages: list[Message]) -> Any:
        """调用 SEKB 的带统计 LLM 调用，返回原始响应对象。

        直接返回响应对象即可——内核的 ``extract_text`` 会取 ``.content``，
        与抽取前的 ``resp.content if hasattr(resp, "content") else str(resp)`` 等价。
        """
        return await self._factory.ainvoke_with_stats(role, to_langchain(messages))
