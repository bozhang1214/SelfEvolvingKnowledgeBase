"""
聊天会话分享数据模型。

定义 SharedConversation——将会话（对话历史）通过分享链接暴露为只读内容。

复用 ``app.models.share.generate_share_id`` 的 token 机制（``secrets.token_urlsafe``），
生成 URL 安全的分享令牌。分享为创建时的消息快照，只读、不可修改。

- share_id: 分享令牌（用于生成访问链接，URL 安全）
- owner_user_id: 会话所有者
- conv_id: 被分享的会话 ID
- title: 分享标题（取会话标题）
- messages: 创建时的消息快照（仅 user / assistant 的 role、content、created_at）
- created_at / expires_at: 创建与过期时间
- is_active: 是否启用（所有者可主动撤销）
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from app.models.share import generate_share_id


def _now_iso() -> datetime:
    return datetime.now(timezone.utc)


class SharedConversation(BaseModel):
    """聊天会话分享记录。"""

    share_id: str = Field(default_factory=generate_share_id)
    owner_user_id: str
    conv_id: str
    title: str = ""
    permission: str = "read_only"  # read_only: 只读浏览，不可对话
    created_at: datetime = Field(default_factory=_now_iso)
    expires_at: datetime | None = None
    is_active: bool = True
    # 消息快照：创建分享时固化，避免原会话后续变更影响分享内容
    messages: list[dict[str, Any]] = Field(default_factory=list)

    def is_valid(self, now: datetime | None = None) -> bool:
        """判断分享是否有效（启用且未过期）。"""
        if not self.is_active:
            return False
        if self.expires_at is not None:
            now = now or _now_iso()
            if now > self.expires_at:
                return False
        return True
