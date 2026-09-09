"""
会话管理 API 路由（Phase 3 增强：多用户 + 鉴权）

提供会话（conversation）的增删改查与消息反馈 HTTP 端点：

- ``GET    /api/v1/conversations/``            列出当前用户的会话
- ``POST   /api/v1/conversations/``            创建新会话
- ``GET    /api/v1/conversations/{conv_id}``   获取会话详情（含消息历史）
- ``PATCH  /api/v1/conversations/{conv_id}``   更新会话（重命名）
- ``DELETE /api/v1/conversations/{conv_id}``   删除会话
- ``POST   /api/v1/conversations/{conv_id}/rate``  消息反馈（thumbs up/down）

所有操作通过 AppContext 注入的 JSONStorage 完成，错误统一映射为 HTTP 状态码。
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.core.access import require_full_access
from app.core.auth import get_current_user
from app.core.bootstrap import get_app_context
from app.core.exceptions import SEKBError, StorageError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"],
    dependencies=[Depends(require_full_access)],
)


# ============================================================
# 请求模型
# ============================================================

class UpdateConversationRequest(BaseModel):
    """更新会话请求（支持重命名和置顶）。"""

    title: str | None = Field(None, min_length=1, max_length=200, description="新的会话标题")
    pinned: bool | None = Field(None, description="是否置顶")


class RateRequest(BaseModel):
    """消息反馈请求。"""

    msg_id: str = Field(..., description="被反馈的消息 ID")
    rating: str = Field(..., description="反馈类型：thumbs_up / thumbs_down")
    comment: str | None = Field(None, max_length=1000, description="可选文字反馈")


# ============================================================
# 辅助函数
# ============================================================

def _not_found(conv_id: str) -> HTTPException:
    """构造 404 会话不存在的 HTTPException。"""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"会话不存在: {conv_id}",
    )


def _handle_sekb_error(e: SEKBError, conv_id: str | None = None) -> HTTPException:
    """
    将 SEKBError 转换为对应的 HTTPException。

    对于 StorageError，若消息含 "不存在" 则映射为 404，否则 500。
    其他 SEKBError 子类统一映射为 500。
    """
    if isinstance(e, StorageError):
        if "不存在" in e.message:
            return _not_found(conv_id or "")
        return HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"存储错误: {e.message}",
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=e.message,
    )


# ============================================================
# 路由
# ============================================================

@router.get("")
async def list_conversations(
    user_id: str = Depends(get_current_user),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """获取会话列表"""
    ctx = get_app_context()
    storage = ctx.storage
    if storage is None:
        raise HTTPException(500, "存储层未初始化")

    try:
        convs = await storage.list_conversations(user_id=user_id, limit=limit, offset=offset)
    except SEKBError as e:
        raise _handle_sekb_error(e) from e

    return convs


@router.post("")
async def create_conversation(
    user_id: str = Depends(get_current_user),
    title: str = "新会话",
):
    """创建新会话"""
    ctx = get_app_context()
    storage = ctx.storage
    if storage is None:
        raise HTTPException(500, "存储层未初始化")

    try:
        conv_id = await storage.create_conversation(user_id=user_id, title=title)
    except SEKBError as e:
        raise _handle_sekb_error(e) from e

    conv = await storage.get_conversation(conv_id)
    logger.info("新会话已创建", extra={"conv_id": conv_id, "user_id": user_id})
    return conv or {"conv_id": conv_id, "user_id": user_id, "title": title}


@router.get("/{conv_id}")
async def get_conversation(
    conv_id: str,
    user_id: str = Depends(get_current_user),
):
    """获取会话详情（含消息历史）"""
    ctx = get_app_context()
    storage = ctx.storage
    if storage is None:
        raise HTTPException(500, "存储层未初始化")

    try:
        conv = await storage.get_conversation(conv_id)
    except SEKBError as e:
        raise _handle_sekb_error(e, conv_id) from e

    if conv is None:
        raise _not_found(conv_id)
    if conv.get("user_id") != user_id:
        raise _not_found(conv_id)

    try:
        messages = await storage.get_messages(conv_id)
    except SEKBError as e:
        raise _handle_sekb_error(e, conv_id) from e

    return {"conversation": conv, "messages": messages}


@router.get("/{conv_id}/messages")
async def get_messages(
    conv_id: str,
    user_id: str = Depends(get_current_user),
):
    """获取会话消息列表"""
    ctx = get_app_context()
    storage = ctx.storage
    if storage is None:
        raise HTTPException(500, "存储层未初始化")

    try:
        conv = await storage.get_conversation(conv_id)
    except SEKBError as e:
        raise _handle_sekb_error(e, conv_id) from e

    if conv is None:
        raise _not_found(conv_id)
    if conv.get("user_id") != user_id:
        raise _not_found(conv_id)

    try:
        msgs = await storage.get_messages(conv_id)
    except SEKBError as e:
        raise _handle_sekb_error(e, conv_id) from e

    return msgs


@router.patch("/{conv_id}")
async def update_conversation(
    conv_id: str,
    request: UpdateConversationRequest,
    user_id: str = Depends(get_current_user),
):
    """更新会话（重命名）"""
    ctx = get_app_context()
    storage = ctx.storage
    if storage is None:
        raise HTTPException(500, "存储层未初始化")

    try:
        conv = await storage.get_conversation(conv_id)
    except SEKBError as e:
        raise _handle_sekb_error(e, conv_id) from e

    if conv is None:
        raise _not_found(conv_id)
    if conv.get("user_id") != user_id:
        raise _not_found(conv_id)

    try:
        updates: dict[str, Any] = {}
        if request.title is not None:
            updates["title"] = request.title
        if request.pinned is not None:
            updates["pinned"] = request.pinned
        if updates:
            await storage.update_conversation(conv_id, updates)
        updated = await storage.get_conversation(conv_id)
    except SEKBError as e:
        raise _handle_sekb_error(e, conv_id) from e

    return updated or {"conv_id": conv_id}


@router.delete("/{conv_id}")
async def delete_conversation(
    conv_id: str,
    user_id: str = Depends(get_current_user),
):
    """删除会话"""
    ctx = get_app_context()
    storage = ctx.storage
    if storage is None:
        raise HTTPException(500, "存储层未初始化")

    try:
        conv = await storage.get_conversation(conv_id)
    except SEKBError as e:
        raise _handle_sekb_error(e, conv_id) from e

    if conv is None:
        raise _not_found(conv_id)
    if conv.get("user_id") != user_id:
        raise _not_found(conv_id)

    try:
        await storage.delete_conversation(conv_id)
    except SEKBError as e:
        raise _handle_sekb_error(e, conv_id) from e

    logger.info("会话已删除", extra={"conv_id": conv_id, "user_id": user_id})
    return {"conv_id": conv_id, "deleted": True}


@router.post("/{conv_id}/rate")
async def rate_message(
    conv_id: str,
    request: RateRequest,
    user_id: str = Depends(get_current_user),
):
    """消息反馈（thumbs up / thumbs down）"""
    ctx = get_app_context()
    storage = ctx.storage
    if storage is None:
        raise HTTPException(500, "存储层未初始化")

    try:
        conv = await storage.get_conversation(conv_id)
    except SEKBError as e:
        raise _handle_sekb_error(e, conv_id) from e

    if conv is None:
        raise _not_found(conv_id)
    if conv.get("user_id") != user_id:
        raise _not_found(conv_id)

    # 校验消息存在性
    try:
        messages = await storage.get_messages(conv_id)
    except SEKBError as e:
        raise _handle_sekb_error(e, conv_id) from e

    msg_ids = {m.get("msg_id") for m in messages}
    if request.msg_id not in msg_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"消息不存在: {request.msg_id}",
        )

    # 以 feedback 消息形式持久化反馈记录
    feedback_msg = {
        "role": "feedback",
        "content": request.comment or "",
        "target_msg_id": request.msg_id,
        "rating": request.rating,
    }
    try:
        feedback_id = await storage.append_message(conv_id, feedback_msg)
    except SEKBError as e:
        raise _handle_sekb_error(e, conv_id) from e

    return {
        "conv_id": conv_id,
        "msg_id": request.msg_id,
        "rating": request.rating,
        "feedback_id": feedback_id,
        "recorded": True,
    }
