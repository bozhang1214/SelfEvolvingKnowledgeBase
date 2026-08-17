"""
会话管理路由模块

提供会话（conversation）的增删改查与消息反馈 HTTP 端点：

- ``GET    /api/v1/conversations/``            列出当前用户的会话
- ``GET    /api/v1/conversations/{conv_id}``   获取会话详情（含消息历史）
- ``PATCH  /api/v1/conversations/{conv_id}``   更新会话（重命名）
- ``DELETE /api/v1/conversations/{conv_id}``   删除会话
- ``POST   /api/v1/conversations/{conv_id}/rate``  消息反馈（thumbs up/down）

所有操作通过 AppContext 注入的 JSONStorage 完成，错误统一映射为 HTTP 状态码。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.server import get_app_context
from app.core.bootstrap import AppContext
from app.core.exceptions import SEKBError, StorageError

router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])


# ============================================================
# 请求模型
# ============================================================

class UpdateConversationRequest(BaseModel):
    """更新会话请求（目前仅支持重命名）。"""

    title: str = Field(..., min_length=1, max_length=200, description="新的会话标题")


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

@router.get("/")
async def list_conversations(
    user_id: str = "default",
    limit: int = 50,
    ctx: AppContext = Depends(get_app_context),
) -> list[dict]:
    """
    列出指定用户的会话。

    Args:
        user_id: 用户 ID，默认 "default"
        limit: 最多返回的会话数，默认 50

    Returns:
        会话元信息字典列表（按 updated_at 倒序）
    """
    try:
        return await ctx.storage.list_conversations(user_id=user_id, limit=limit)
    except SEKBError as e:
        raise _handle_sekb_error(e) from e


@router.get("/{conv_id}")
async def get_conversation(
    conv_id: str,
    ctx: AppContext = Depends(get_app_context),
) -> dict:
    """
    获取会话详情（含消息历史）。

    Args:
        conv_id: 会话 ID

    Returns:
        ``{"conversation": <meta>, "messages": [<message>, ...]}``

    Raises:
        HTTPException 404: 会话不存在
    """
    try:
        conv = await ctx.storage.get_conversation(conv_id)
    except SEKBError as e:
        raise _handle_sekb_error(e, conv_id) from e

    if conv is None:
        raise _not_found(conv_id)

    try:
        messages = await ctx.storage.get_messages(conv_id)
    except SEKBError as e:
        raise _handle_sekb_error(e, conv_id) from e

    return {"conversation": conv, "messages": messages}


@router.patch("/{conv_id}")
async def update_conversation(
    conv_id: str,
    request: UpdateConversationRequest,
    ctx: AppContext = Depends(get_app_context),
) -> dict:
    """
    更新会话（重命名）。

    Args:
        conv_id: 会话 ID
        request: 更新请求体（含新标题）

    Returns:
        更新后的会话元信息字典

    Raises:
        HTTPException 404: 会话不存在
    """
    try:
        conv = await ctx.storage.get_conversation(conv_id)
    except SEKBError as e:
        raise _handle_sekb_error(e, conv_id) from e

    if conv is None:
        raise _not_found(conv_id)

    try:
        await ctx.storage.update_conversation(conv_id, {"title": request.title})
        updated = await ctx.storage.get_conversation(conv_id)
    except SEKBError as e:
        raise _handle_sekb_error(e, conv_id) from e

    return updated or {"conv_id": conv_id, "title": request.title}


@router.delete("/{conv_id}")
async def delete_conversation(
    conv_id: str,
    ctx: AppContext = Depends(get_app_context),
) -> dict:
    """
    删除会话（软删除：索引中标记为 deleted，并物理删除消息文件）。

    Args:
        conv_id: 会话 ID

    Returns:
        ``{"conv_id": <conv_id>, "deleted": true}``

    Raises:
        HTTPException 404: 会话不存在
    """
    try:
        conv = await ctx.storage.get_conversation(conv_id)
    except SEKBError as e:
        raise _handle_sekb_error(e, conv_id) from e

    if conv is None:
        raise _not_found(conv_id)

    try:
        await ctx.storage.delete_conversation(conv_id)
    except SEKBError as e:
        raise _handle_sekb_error(e, conv_id) from e

    return {"conv_id": conv_id, "deleted": True}


@router.post("/{conv_id}/rate")
async def rate_message(
    conv_id: str,
    request: RateRequest,
    ctx: AppContext = Depends(get_app_context),
) -> dict:
    """
    消息反馈（thumbs up / thumbs down）。

    校验会话与消息存在性后，将反馈记录追加到会话消息文件
    （以 ``role=feedback`` 的特殊消息形式持久化），便于后续分析。

    Args:
        conv_id: 会话 ID
        request: 反馈请求体

    Returns:
        ``{"conv_id": <conv_id>, "msg_id": <msg_id>, "rating": <rating>, "recorded": true}``

    Raises:
        HTTPException 404: 会话或消息不存在
    """
    try:
        conv = await ctx.storage.get_conversation(conv_id)
    except SEKBError as e:
        raise _handle_sekb_error(e, conv_id) from e

    if conv is None:
        raise _not_found(conv_id)

    # 校验消息存在性
    try:
        messages = await ctx.storage.get_messages(conv_id)
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
        feedback_id = await ctx.storage.append_message(conv_id, feedback_msg)
    except SEKBError as e:
        raise _handle_sekb_error(e, conv_id) from e

    return {
        "conv_id": conv_id,
        "msg_id": request.msg_id,
        "rating": request.rating,
        "feedback_id": feedback_id,
        "recorded": True,
    }
