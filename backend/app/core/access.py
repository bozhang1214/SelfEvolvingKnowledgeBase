"""
访问控制：用户白名单（完整功能 vs 预览功能）。

- 白名单通过环境变量 ``ALLOWED_EMAILS``（逗号分隔的邮箱）配置。
- 未配置（空）→ 所有用户均为「完整功能」（向后兼容，不影响现有单用户）。
- 配置后：白名单内邮箱 = 完整功能；其余登录用户 = 预览功能。
  预览功能仅可访问「功能说明 + 科技资讯（只读）」，不能重新生成日报，
  也不能使用聊天 / 知识库 / 职位分析 / 分享等完整能力。
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import Depends, HTTPException, status

from app.core.auth import get_current_claims, get_current_user, is_device_token


def get_allowed_emails() -> set[str]:
    """读取白名单邮箱集合（小写化）；未配置返回空集合。"""
    raw = os.environ.get("ALLOWED_EMAILS", "").strip()
    if not raw:
        return set()
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def _resolve_email(user_id: str) -> str | None:
    """按 user_id 解析邮箱（用于白名单比对）；失败返回 None。"""
    try:
        from app.api.server import get_app_context

        ctx = get_app_context()
        if ctx.user_storage is None:
            return None
        user = ctx.user_storage.find_by_id(user_id)
        return (user.email or "").lower() if user else None
    except Exception:
        return None


def access_level(user_id: str) -> str:
    """返回当前用户访问级别：``full``（完整）或 ``preview``（预览）。"""
    allowed = get_allowed_emails()
    if not allowed:
        return "full"
    email = _resolve_email(user_id)
    return "full" if (email and email in allowed) else "preview"


async def require_full_access(user_id: str = Depends(get_current_user)) -> str:
    """FastAPI 依赖：仅白名单内用户放行，预览用户返回 403。

    挂到聊天/知识库/职位分析/分享等路由（router 级 dependencies），
    预览用户访问即被拦截。

    设备 token 的吊销/轮换校验在 ``auth._claims_checked`` 里完成，而
    ``get_current_user`` 正是它的调用方——所以本依赖自动覆盖设备凭证的生命周期，
    无需在此重复判断。
    """
    allowed = get_allowed_emails()
    if not allowed:
        return user_id
    email = _resolve_email(user_id)
    if email and email in allowed:
        return user_id
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="预览账号无权限执行此操作，仅可浏览科技资讯",
    )


async def require_user_account(
    user_id: str = Depends(require_full_access),
    claims: dict[str, Any] = Depends(get_current_claims),
) -> str:
    """FastAPI 依赖：要求**用户本人**（设备 token 一律 403）。

    为什么需要：设备 token 有效期长（默认 30 天）、存在客户端本地，一旦泄漏，
    攻击者能做的应该尽可能少。所以设备只能"替用户用"（聊天、上报、同步会话），
    不能"改用户的资产"——这条边界落在**写入型**路由上（知识库写入、公开分享创建）。
    """
    if is_device_token(claims):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="设备 token 无此权限，请在网页端以账号身份操作",
        )
    return user_id
