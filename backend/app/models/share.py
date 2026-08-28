"""
知识库分享数据模型。

定义 SharedKnowledge——将某个用户的知识库通过分享链接暴露给其他用户
（只读浏览 + 基于该知识库的问答对话）。

- share_id: 分享令牌（用于生成访问链接，URL 安全）
- owner_user_id: 知识库所有者（被分享的知识库归属此用户）
- title: 分享标题（便于展示）
- permission: 权限粒度（当前固定为 read_chat：只读 + 可对话）
- created_at / expires_at: 创建与过期时间（过期后不可访问）
- is_active: 是否启用（所有者可主动撤销）
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from pydantic import BaseModel, Field


def _now_iso() -> datetime:
    return datetime.now(timezone.utc)


def generate_share_id() -> str:
    """生成 URL 安全的分享令牌（16 字节，22 字符）。"""
    return secrets.token_urlsafe(16)


class SharedKnowledge(BaseModel):
    """知识库分享记录。"""

    share_id: str = Field(default_factory=generate_share_id)
    owner_user_id: str
    title: str = ""
    permission: str = "read_chat"  # read_chat: 只读 + 可对话
    created_at: datetime = Field(default_factory=_now_iso)
    expires_at: datetime | None = None
    is_active: bool = True

    def is_valid(self, now: datetime | None = None) -> bool:
        """判断分享是否有效（启用且未过期）。"""
        if not self.is_active:
            return False
        if self.expires_at is not None:
            now = now or _now_iso()
            if now > self.expires_at:
                return False
        return True
