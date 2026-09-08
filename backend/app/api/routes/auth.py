"""
用户鉴权 API 路由（Phase 3）
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.audit import AuditAction, audit_log, get_client_ip
from app.core.auth import (
    create_jwt,
    get_current_user,
    hash_password,
    verify_password,
)
from app.core.metrics import record_login_attempt, record_register_attempt
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
async def register(body: RegisterRequest, request: Request):
    """用户注册"""
    storage = _get_user_storage()
    client_ip = get_client_ip(request)
    user_agent = request.headers.get("user-agent", "unknown")

    # 检查邮箱是否已注册
    existing = storage.find_by_email(body.email)
    if existing:
        audit_log(AuditAction.REGISTER, success=False, ip=client_ip,
                  detail={"email": body.email, "reason": "email_exists"})
        logger.warning(
            "注册失败：邮箱已存在",
            extra={
                "event": "register_failed",
                "reason": "email_exists",
                "email_prefix": body.email[:3] + "***",  # 脱敏：仅保留前 3 字符
                "ip": client_ip,
                "user_agent": user_agent,
            },
        )
        record_register_attempt("email_exists")
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
    logger.info(
        "用户注册成功",
        extra={
            "event": "register_success",
            "user_id": user.user_id,
            "email": user.email,
            "ip": client_ip,
        },
    )
    record_register_attempt("success")
    audit_log(AuditAction.REGISTER, user_id=user.user_id, success=True, ip=client_ip,
              detail={"email": user.email})
    return LoginResponse(user=storage.to_public(user), token=token)


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, request: Request):
    """用户登录"""
    storage = _get_user_storage()
    client_ip = get_client_ip(request)
    user_agent = request.headers.get("user-agent", "unknown")

    user = storage.find_by_email(body.email)

    # 失败原因细分（仅日志和审计使用，对外响应统一为"邮箱或密码错误"以防用户枚举）
    if not user:
        audit_log(AuditAction.LOGIN_FAILED, success=False, ip=client_ip,
                  detail={"email": body.email, "reason": "user_not_found"})
        logger.warning(
            "登录失败：用户不存在",
            extra={
                "event": "login_failed",
                "reason": "user_not_found",
                "email_prefix": body.email[:3] + "***",  # 脱敏
                "ip": client_ip,
                "user_agent": user_agent,
                "path": request.url.path,
            },
        )
        record_login_attempt("user_not_found")
        raise HTTPException(401, "邮箱或密码错误")

    if not verify_password(body.password, user.password_hash):
        audit_log(AuditAction.LOGIN_FAILED, success=False, ip=client_ip,
                  detail={"email": body.email, "reason": "password_mismatch"})
        logger.warning(
            "登录失败：密码错误",
            extra={
                "event": "login_failed",
                "reason": "password_mismatch",
                "user_id": user.user_id,
                "email_prefix": body.email[:3] + "***",  # 脱敏
                "ip": client_ip,
                "user_agent": user_agent,
                "path": request.url.path,
            },
        )
        record_login_attempt("password_mismatch")
        raise HTTPException(401, "邮箱或密码错误")

    token = create_jwt(user.user_id)
    logger.info(
        "用户登录成功",
        extra={
            "event": "login_success",
            "user_id": user.user_id,
            "email": user.email,
            "ip": client_ip,
        },
    )
    record_login_attempt("success")
    audit_log(AuditAction.LOGIN, user_id=user.user_id, success=True, ip=client_ip)
    return LoginResponse(user=storage.to_public(user), token=token)


@router.post("/logout")
async def logout(request: Request, user_id: str = Depends(get_current_user)):
    """用户登出（客户端清除 token 即可）"""
    client_ip = get_client_ip(request)
    logger.info("用户登出", extra={"user_id": user_id})
    audit_log(AuditAction.LOGOUT, user_id=user_id, success=True, ip=client_ip)
    return {"status": "ok"}


@router.post("/refresh", response_model=LoginResponse)
async def refresh(request: Request, user_id: str = Depends(get_current_user)):
    """
    滑动续租：用当前仍有效的 token 换取一个新的 90 天 token。

    前端在 token 临近过期时自动调用，实现「90 天有效期 + 到期自动续租」，
    用户只要在 90 天内使用过系统，就无需重新登录。
    """
    storage = _get_user_storage()
    user = storage.find_by_id(user_id)
    if not user:
        raise HTTPException(404, "用户不存在")
    token = create_jwt(user.user_id)
    client_ip = get_client_ip(request)
    logger.info("token 滑动续租", extra={"user_id": user_id})
    return LoginResponse(user=storage.to_public(user), token=token)


@router.get("/me", response_model=UserPublic)
async def get_me(user_id: str = Depends(get_current_user)):
    """获取当前用户信息（含访问级别 access_level）"""
    from app.core.access import access_level

    storage = _get_user_storage()
    user = storage.find_by_id(user_id)
    if not user:
        raise HTTPException(404, "用户不存在")
    public = storage.to_public(user)
    return {**public.model_dump(), "access_level": access_level(user_id)}


@router.patch("/me", response_model=UserPublic)
async def update_me(body: dict, request: Request, user_id: str = Depends(get_current_user)):
    """更新当前用户信息"""
    storage = _get_user_storage()
    client_ip = get_client_ip(request)

    # 只允许更新特定字段
    allowed_fields = {"name", "avatar_url", "settings"}
    updates = {k: v for k, v in body.items() if k in allowed_fields}

    user = storage.update(user_id, updates)
    if not user:
        raise HTTPException(404, "用户不存在")
    audit_log(AuditAction.USER_UPDATE, user_id=user_id, success=True, ip=client_ip,
              detail={"fields": list(updates.keys())})
    return storage.to_public(user)