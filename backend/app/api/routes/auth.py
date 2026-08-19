"""
用户鉴权 API 路由（Phase 3）
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import (
    create_jwt,
    get_current_user,
    hash_password,
    verify_password,
)
from app.models.user import (
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    User,
    UserPublic,
)
from app.storage.user_storage import UserStorage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _get_user_storage() -> UserStorage:
    """获取用户存储实例（依赖注入）。"""
    from app.core.bootstrap import get_app_context
    ctx = get_app_context()
    if ctx.user_storage is None:
        raise HTTPException(500, "用户存储未初始化")
    return ctx.user_storage


@router.post("/register", response_model=LoginResponse)
async def register(body: RegisterRequest):
    """用户注册"""
    storage = _get_user_storage()

    # 检查邮箱是否已注册
    existing = storage.find_by_email(body.email)
    if existing:
        raise HTTPException(400, "该邮箱已被注册")

    # 创建用户
    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        name=body.name or body.email.split("@")[0],
    )
    storage.create(user)

    # 生成 JWT
    token = create_jwt(user.user_id)
    logger.info("用户注册成功", extra={"user_id": user.user_id, "email": user.email})
    return LoginResponse(user=storage.to_public(user), token=token)


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest):
    """用户登录"""
    storage = _get_user_storage()

    user = storage.find_by_email(body.email)
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "邮箱或密码错误")

    token = create_jwt(user.user_id)
    logger.info("用户登录成功", extra={"user_id": user.user_id})
    return LoginResponse(user=storage.to_public(user), token=token)


@router.post("/logout")
async def logout(user_id: str = Depends(get_current_user)):
    """用户登出（客户端清除 token 即可）"""
    logger.info("用户登出", extra={"user_id": user_id})
    return {"status": "ok"}


@router.get("/me", response_model=UserPublic)
async def get_me(user_id: str = Depends(get_current_user)):
    """获取当前用户信息"""
    storage = _get_user_storage()
    user = storage.find_by_id(user_id)
    if not user:
        raise HTTPException(404, "用户不存在")
    return storage.to_public(user)


@router.patch("/me", response_model=UserPublic)
async def update_me(body: dict, user_id: str = Depends(get_current_user)):
    """更新当前用户信息"""
    storage = _get_user_storage()

    # 只允许更新特定字段
    allowed_fields = {"name", "avatar_url", "settings"}
    updates = {k: v for k, v in body.items() if k in allowed_fields}

    user = storage.update(user_id, updates)
    if not user:
        raise HTTPException(404, "用户不存在")
    return storage.to_public(user)