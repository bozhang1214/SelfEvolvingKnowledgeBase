"""
存储后端抽象接口模块

定义存储层统一抽象接口 StorageBackend，以及会话与消息的 Pydantic 数据模型。
Phase 1 使用本地 JSON 实现（见 json_storage.py），
Phase 2 可平滑切换到 PostgreSQL 等数据库后端。

使用方式：
    from app.storage.base import StorageBackend, ConversationMeta, MessageRecord
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from pydantic import BaseModel, Field


# ============================================================
# 工具函数
# ============================================================

def now_iso() -> str:
    """返回当前 UTC 时间的 ISO 格式字符串"""
    return datetime.now(timezone.utc).isoformat()


def new_uuid() -> str:
    """生成一个新的 UUID 字符串"""
    return str(uuid.uuid4())


# ============================================================
# 数据模型
# ============================================================

class ConversationMeta(BaseModel):
    """
    会话元信息模型。

    存储在会话索引文件中，记录会话的基本元数据与累计统计信息。
    """

    conv_id: str                              # 会话唯一 ID
    user_id: str                              # 所属用户 ID（Phase 1 固定为 "default"）
    title: str                                # 会话标题
    status: str = "active"                    # 状态：active / archived / deleted
    pinned: bool = False                     # 是否置顶
    created_at: str = Field(default_factory=now_iso)   # 创建时间（ISO）
    updated_at: str = Field(default_factory=now_iso)   # 最近更新时间（ISO）
    message_count: int = 0                    # 累计消息数
    total_input_tokens: int = 0               # 累计输入 token 数
    total_output_tokens: int = 0               # 累计输出 token 数
    total_cost_usd: float = 0.0               # 累计成本（美元）


class MessageRecord(BaseModel):
    """
    消息记录模型。

    存储单个对话消息及其调用元数据（token、延迟、trace 等）。
    """

    msg_id: str = Field(default_factory=new_uuid)       # 消息唯一 ID
    conv_id: str                                        # 所属会话 ID
    role: str                                           # 角色：user / assistant / system
    content: str                                        # 消息文本内容
    intent: str | None = None                          # 意图标签（可选）
    tokens_input: int = 0                               # 输入 token 数
    tokens_output: int = 0                              # 输出 token 数
    latency_ms: int = 0                                # 调用耗时（毫秒）
    trace_id: str | None = None                         # 关联的 trace ID
    created_at: str = Field(default_factory=now_iso)    # 创建时间（ISO）


# ============================================================
# 抽象存储后端
# ============================================================

class StorageBackend(ABC):
    """
    存储后端抽象基类。

    定义会话与消息的增删改查接口，以及会话级统计信息的更新接口。
    所有方法均为协程，实现方应使用异步 IO 或 asyncio.to_thread 包装阻塞 IO。
    """

    @abstractmethod
    async def create_conversation(self, user_id: str, title: str) -> str:
        """
        创建新会话。

        Args:
            user_id: 用户 ID
            title: 会话标题

        Returns:
            新创建会话的 conv_id

        Raises:
            StorageError: 创建失败
        """
        ...

    @abstractmethod
    async def get_conversation(self, conv_id: str) -> dict | None:
        """
        获取会话详情。

        Args:
            conv_id: 会话 ID

        Returns:
            会话元信息字典；若不存在返回 None
        """
        ...

    @abstractmethod
    async def list_conversations(
        self,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """
        列出指定用户的会话。

        Args:
            user_id: 用户 ID
            limit: 最多返回的会话数
            offset: 分页偏移量

        Returns:
            会话元信息字典列表（按 updated_at 倒序）
        """
        ...

    @abstractmethod
    async def update_conversation(self, conv_id: str, updates: dict) -> None:
        """
        更新会话元信息（标题、状态等）。

        Args:
            conv_id: 会话 ID
            updates: 待合并更新的字段字典

        Raises:
            StorageError: 会话不存在或更新失败
        """
        ...

    @abstractmethod
    async def delete_conversation(self, conv_id: str) -> None:
        """
        删除会话（含其所有消息）。

        Args:
            conv_id: 会话 ID

        Raises:
            StorageError: 会话不存在或删除失败
        """
        ...

    @abstractmethod
    async def append_message(self, conv_id: str, message: dict) -> str:
        """
        追加一条消息到会话。

        Args:
            conv_id: 会话 ID
            message: 消息字典（应包含 role、content 等字段）

        Returns:
            新消息的 msg_id

        Raises:
            StorageError: 会话不存在或写入失败
        """
        ...

    @abstractmethod
    async def get_messages(self, conv_id: str, limit: int = 100) -> list[dict]:
        """
        获取会话消息列表。

        Args:
            conv_id: 会话 ID
            limit: 最多返回的消息数（按时间倒序取最后 limit 条）

        Returns:
            消息字典列表（按时间正序排列）
        """
        ...

    @abstractmethod
    async def update_conversation_stats(self, conv_id: str, stats: dict) -> None:
        """
        更新会话统计信息（token 数、成本等）。

        Args:
            conv_id: 会话 ID
            stats: 待更新的统计字段字典

        Raises:
            StorageError: 会话不存在或更新失败
        """
        ...
