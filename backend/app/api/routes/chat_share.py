"""
聊天会话分享 API 路由。

将某个会话（对话历史）通过只读分享链接暴露给其他已登录用户。

端点：
- ``POST   /api/v1/chat-share``          创建聊天会话分享（仅所有者，含消息快照）
- ``GET    /api/v1/chat-share/{share_id}`` 获取分享内容（任意已登录用户，只读）
- ``DELETE /api/v1/chat-share/{share_id}`` 撤销分享（仅所有者）

权限：
- 创建/撤销：仅会话所有者（``get_current_user`` 鉴权）
- 浏览：任意已登录用户（需分享有效且未过期），纯只读，不写入、不修改。

分享令牌复用 ``app.models.share.generate_share_id``（``secrets.token_urlsafe``）。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.server import get_app_context
from app.core.auth import get_current_user
from app.core.bootstrap import AppContext
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/chat-share", tags=["chat-share"])


def _fmt_dt(v: Any) -> str:
    """时间字段统一序列化：兼容 str 与 datetime。"""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    return v.isoformat()


class CreateChatShareRequest(BaseModel):
    """创建聊天会话分享请求。"""

    conv_id: str = Field(..., min_length=1, description="要分享的会话 ID")


# ============================================================
# 辅助
# ============================================================

def _require_share_storage(ctx: AppContext) -> Any:
    """获取聊天分享存储，未初始化则 500。"""
    if ctx.chat_share_storage is None:
        raise HTTPException(500, "聊天分享存储未初始化")
    return ctx.chat_share_storage


async def _get_valid_share(ctx: AppContext, share_id: str):
    """获取并校验分享有效性，返回 share。"""
    share_storage = _require_share_storage(ctx)
    share = await share_storage.get_share(share_id)
    if share is None:
        raise HTTPException(404, "分享不存在或已撤销")
    if not share.is_valid():
        raise HTTPException(403, "分享已失效或过期")
    return share


def _owner_display_name(ctx: AppContext, owner_user_id: str) -> str:
    """获取所有者展示名（脱敏，仅 name 或邮箱前缀）。"""
    if ctx.user_storage is None:
        return "对话所有者"
    try:
        user = ctx.user_storage.find_by_id(owner_user_id)
    except Exception:
        user = None
    if user is None:
        return "对话所有者"
    return user.name or (user.email.split("@")[0] if user.email else "对话所有者")


# ============================================================
# 路由
# ============================================================

@router.post("")
async def create_chat_share(
    request: CreateChatShareRequest,
    user_id: str = Depends(get_current_user),
):
    """创建聊天会话分享链接（仅会话所有者）。"""
    ctx = get_app_context()
    storage = ctx.storage
    if storage is None:
        raise HTTPException(500, "存储层未初始化")

    conv = await storage.get_conversation(request.conv_id)
    if conv is None:
        raise HTTPException(404, "会话不存在")
    if conv.get("user_id") != user_id:
        # 不暴露他人会话是否存在
        raise HTTPException(404, "会话不存在")

    messages = await storage.get_messages(request.conv_id)

    # 只快照 user / assistant 且有内容的消息，供只读展示
    snapshot: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content", "")
        if role not in ("user", "assistant") or not content:
            continue
        snapshot.append({
            "role": role,
            "content": content,
            "created_at": _fmt_dt(m.get("created_at")),
        })

    share_storage = _require_share_storage(ctx)
    share = await share_storage.create_share(
        owner_user_id=user_id,
        conv_id=request.conv_id,
        title=conv.get("title") or "对话",
        messages=snapshot,
    )

    logger.info(
        "聊天会话分享已创建",
        share_id=share.share_id,
        conv_id=request.conv_id,
        messages=len(snapshot),
    )
    return {
        "share_id": share.share_id,
        "title": share.title,
        "share_url": f"/share/chat/{share.share_id}",
        "message_count": len(snapshot),
        "created_at": _fmt_dt(share.created_at),
    }


@router.get("/{share_id}")
async def get_shared_conversation(
    share_id: str,
    user_id: str = Depends(get_current_user),
):
    """获取聊天会话分享内容（任意已登录用户，只读）。"""
    ctx = get_app_context()
    share = await _get_valid_share(ctx, share_id)

    return {
        "share_id": share.share_id,
        "title": share.title,
        "owner_name": _owner_display_name(ctx, share.owner_user_id),
        "is_owner": share.owner_user_id == user_id,
        "permission": share.permission,
        "created_at": _fmt_dt(share.created_at),
        "messages": share.messages,
    }


@router.delete("/{share_id}")
async def revoke_chat_share(
    share_id: str,
    user_id: str = Depends(get_current_user),
):
    """撤销分享（仅所有者）。"""
    ctx = get_app_context()
    share_storage = _require_share_storage(ctx)
    share = await share_storage.get_share(share_id)
    if share is None:
        raise HTTPException(404, "分享不存在")
    if share.owner_user_id != user_id:
        raise HTTPException(403, "无权操作")

    deleted = await share_storage.delete_share(share_id)
    return {"share_id": share_id, "deleted": deleted}
