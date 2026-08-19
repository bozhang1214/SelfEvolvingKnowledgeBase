"""
JWT 鉴权与密码哈希模块（Phase 3）
"""
from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_config
from app.core.exceptions import AuthError

security = HTTPBearer(auto_error=False)

JWT_ALGORITHM = "HS256"


def get_jwt_secret() -> str:
    """获取 JWT 密钥，支持环境变量覆盖。"""
    return os.environ.get("JWT_SECRET", "sekb-dev-secret-change-in-production")


def hash_password(password: str) -> str:
    """使用 SHA-256 + 随机盐值哈希密码（Phase 3 暂用，后续迁移到 bcrypt）。"""
    salt = secrets.token_hex(16)
    pwd_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000)
    return f"{salt}${pwd_hash.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """验证密码。"""
    try:
        salt, pwd_hash = stored_hash.split("$", 1)
        computed = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000)
        return computed.hex() == pwd_hash
    except (ValueError, AttributeError):
        return False


def create_jwt(user_id: str) -> str:
    """创建 JWT，有效期 72 小时。"""
    config = get_config()
    expire_hours = config.api.auth.token_expire_hours
    payload = {
        "sub": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(hours=expire_hours),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)


def verify_jwt(token: str) -> dict[str, Any]:
    """验证 JWT，返回 payload。"""
    try:
        payload = jwt.decode(token, get_jwt_secret(), algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise AuthError("登录已过期，请重新登录")
    except jwt.InvalidTokenError:
        raise AuthError("无效的 token")


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> str:
    """FastAPI 依赖项：从请求头提取当前用户 ID。"""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = verify_jwt(credentials.credentials)
    return payload["sub"]