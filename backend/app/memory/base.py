"""
记忆系统抽象接口模块

定义三层记忆系统的统一抽象接口：
- L1 短期记忆（ShortTermMemoryBackend）：当前会话的对话历史管理
- L2 中期记忆（SessionMemoryBackend）：跨会话的用户偏好与近期话题（Phase 2）
- L3 长期知识库（KnowledgeBaseBackend）：持久化知识条目（Phase 2）

三层记忆协同工作：
    L1 提供即时上下文（滑动窗口 + 压缩摘要）
    L2 提供用户级偏好（个性化）
    L3 提供领域知识（RAG 检索）

使用方式：
    from app.memory.base import ShortTermMemoryBackend
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from langchain_core.messages import BaseMessage


# ============================================================
# L1 短期记忆抽象接口
# ============================================================

class ShortTermMemoryBackend(ABC):
    """
    L1 短期记忆抽象接口。

    负责单个会话内的对话历史管理，支持：
    - 滑动窗口：保留最近 N 轮对话
    - Token 计数：统计上下文 token 数
    - 压缩：超过 token 限制时将旧消息压缩为摘要
    - 上下文构建：拼接摘要 + 近期消息，供 LLM 使用
    """

    @abstractmethod
    async def get_messages(self, user_id: str, conv_id: str) -> list[BaseMessage]:
        """
        获取会话的对话历史（已应用滑动窗口）。

        Args:
            user_id: 用户 ID
            conv_id: 会话 ID

        Returns:
            LangChain BaseMessage 列表
        """
        ...

    @abstractmethod
    async def add_message(self, user_id: str, conv_id: str, message: BaseMessage) -> None:
        """
        追加一条消息到会话历史。

        Args:
            user_id: 用户 ID
            conv_id: 会话 ID
            message: LangChain BaseMessage 实例
        """
        ...

    @abstractmethod
    async def get_context(self, user_id: str, conv_id: str, max_tokens: int) -> str:
        """
        获取构建好的上下文字符串（摘要 + 近期消息）。

        Args:
            user_id: 用户 ID
            conv_id: 会话 ID
            max_tokens: 上下文最大 token 数

        Returns:
            拼接好的上下文字符串
        """
        ...

    @abstractmethod
    async def compress_if_needed(self, user_id: str, conv_id: str) -> bool:
        """
        检查并执行压缩。

        当对话历史 token 数超过 max_tokens 时，将旧消息压缩为摘要。

        Args:
            user_id: 用户 ID
            conv_id: 会话 ID

        Returns:
            True 表示触发了压缩，False 表示无需压缩
        """
        ...

    @abstractmethod
    async def clear(self, user_id: str, conv_id: str) -> None:
        """
        清空指定会话的对话历史。

        Args:
            user_id: 用户 ID
            conv_id: 会话 ID
        """
        ...


# ============================================================
# L2 中期记忆抽象接口（Phase 2）
# ============================================================

class SessionMemoryBackend(ABC):
    """
    L2 中期记忆抽象接口（Phase 2 实现）。

    负责跨会话的用户级信息维护，包括：
    - 用户偏好（如语言、风格、专业领域）
    - 近期话题（用于会话切换时的上下文恢复）
    """

    @abstractmethod
    async def get_preferences(self, user_id: str) -> list[dict]:
        """
        获取用户偏好列表。

        Args:
            user_id: 用户 ID

        Returns:
            偏好字典列表，每项包含 key、value、confidence 等字段
        """
        ...

    @abstractmethod
    async def upsert_preference(
        self,
        user_id: str,
        key: str,
        value: Any,
        confidence: float,
    ) -> None:
        """
        更新或插入用户偏好。

        Args:
            user_id: 用户 ID
            key: 偏好键（如 "language"）
            value: 偏好值
            confidence: 置信度（0.0~1.0）
        """
        ...

    @abstractmethod
    async def get_recent_topics(self, user_id: str, limit: int = 10) -> list[dict]:
        """
        获取用户近期话题。

        Args:
            user_id: 用户 ID
            limit: 最多返回的话题数

        Returns:
            话题字典列表，每项包含话题、时间、会话 ID 等字段
        """
        ...


# ============================================================
# L3 长期知识库抽象接口（Phase 2）
# ============================================================

class KnowledgeBaseBackend(ABC):
    """
    L3 长期知识库抽象接口（Phase 2 实现）。

    负责持久化知识条目，支持向量检索（RAG）。
    Phase 1 不启用，Phase 2 接入 ChromaDB。
    """

    @abstractmethod
    async def add(self, entry: Any) -> str:
        """
        添加知识条目。

        Args:
            entry: KnowledgeEntry 实例或可转换为 KnowledgeEntry 的字典

        Returns:
            新条目的 entry_id
        """
        ...

    @abstractmethod
    async def retrieve(
        self,
        query: str,
        user_id: str | None = None,
        top_k: int = 5,
        min_score: float = 0.3,
    ) -> list[Any]:
        """
        检索相关知识条目。

        Args:
            query: 查询文本
            user_id: 用户 ID（用于权限过滤，可选）
            top_k: 返回的最相关条目数
            min_score: 最小相似度阈值（0.0~1.0）

        Returns:
            KnowledgeEntry 列表，按相关性倒序排列
        """
        ...

    @abstractmethod
    async def delete(self, entry_id: str) -> None:
        """
        删除知识条目。

        Args:
            entry_id: 条目 ID
        """
        ...

    @abstractmethod
    async def count(self, user_id: str | None = None) -> int:
        """
        返回知识库中的条目总数。

        Args:
            user_id: 用户 ID（None 表示所有用户）

        Returns:
            条目总数
        """
        ...

    @abstractmethod
    async def get(self, entry_id: str) -> Any | None:
        """
        根据 ID 获取单个知识条目。

        Args:
            entry_id: 条目 ID

        Returns:
            KnowledgeEntry 实例，若条目不存在返回 None
        """
        ...

    @abstractmethod
    async def find_similar(
        self,
        query: str,
        user_id: str | None = None,
        threshold: float = 0.8,
        top_k: int = 10,
    ) -> list[Any]:
        """
        查找相似条目（用于冲突检测）。

        Args:
            query: 查询文本
            user_id: 用户 ID
            threshold: 最小相似度阈值
            top_k: 返回最大条目数

        Returns:
            相似度 >= threshold 的 KnowledgeEntry 列表
        """
        ...
