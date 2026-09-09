"""分享域公共助手（WP2 从 `share.py` / `chat_share.py` 去重）。

两个分享路由（知识分享 vs 聊天会话分享）各自复制了
`_get_valid_share` 与 `_owner_display_name`，此处收敛为单一实现：
- `get_valid_share`：按 share_id 取分享并校验（404/403）
- `owner_display_name`：所有者展示名脱敏（fallback 文案由调用方传入，
  知识分享用「知识库所有者」、聊天分享用「对话所有者」）
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.core.bootstrap import AppContext


def owner_display_name(ctx: AppContext, owner_user_id: str, fallback: str) -> str:
    """获取所有者展示名（脱敏，仅 name 或邮箱前缀），无则返回 fallback。"""
    if ctx.user_storage is None:
        return fallback
    try:
        user = ctx.user_storage.find_by_id(owner_user_id)
    except Exception:
        user = None
    if user is None:
        return fallback
    return user.name or (user.email.split("@")[0] if user.email else fallback)


async def get_valid_share(share_storage: Any, share_id: str) -> Any:
    """获取并校验分享有效性；不存在 → 404，失效/过期 → 403。"""
    share = await share_storage.get_share(share_id)
    if share is None:
        raise HTTPException(404, "分享不存在或已撤销")
    if not share.is_valid():
        raise HTTPException(403, "分享已失效或过期")
    return share
