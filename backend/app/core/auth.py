"""
JWT 鉴权与密码哈希模块（Phase 3）
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_config
from app.core.exceptions import AuthError

security = HTTPBearer(auto_error=False)

JWT_ALGORITHM = "HS256"

# jti 黑名单（SEC-02：登出后 token 立即失效）。单 worker 内存实现；
# 多 worker 部署需外置 Redis（与 L2 记忆同源），见 11-EVOLUTION。
# 存 {jti: 吊销时间戳}，revoke 时惰性清理超过 token 最长有效期的条目，避免无界增长。
_jti_blacklist: dict[str, float] = {}
_JTI_MAX_TTL_S = 90 * 24 * 3600  # token 最长 90 天，过期后吊销记录无意义


# 弱密钥占位词：命中即判定为不安全（SEC-01）
_WEAK_SECRET_MARKERS = (
    "test", "secret", "change", "default", "placeholder", "example",
    "changeme", "your-", "sekb-dev",
)


def get_jwt_secret() -> str:
    """
    获取 JWT 密钥，强制校验强度（SEC-01）。

    - 未配置 → 拒绝启动（移除硬编码回退，避免伪造任意 token）
    - 长度 < 32 → 拒绝
    - 含弱占位词（test/secret/change 等）→ 拒绝
    """
    secret = os.environ.get("JWT_SECRET", "").strip()
    if not secret:
        raise RuntimeError("JWT_SECRET 未配置，拒绝启动（SEC-01）")
    if len(secret) < 32:
        raise RuntimeError(f"JWT_SECRET 过短（{len(secret)}<32 字符），拒绝启动（SEC-01）")
    low = secret.lower()
    for marker in _WEAK_SECRET_MARKERS:
        if marker in low:
            raise RuntimeError("JWT_SECRET 疑似弱密钥（含占位词），拒绝启动（SEC-01）")
    return secret


def hash_password(password: str) -> str:
    """使用 SHA-256 + 随机盐值哈希密码（Phase 3 暂用，后续迁移到 bcrypt）。"""
    salt = secrets.token_hex(16)
    pwd_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000)
    return f"{salt}${pwd_hash.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """验证密码（恒定时间比较，防计时侧信道）。"""
    try:
        salt, pwd_hash = stored_hash.split("$", 1)
        computed = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000)
        return hmac.compare_digest(computed.hex(), pwd_hash)
    except (ValueError, AttributeError):
        return False


def create_jwt(user_id: str) -> str:
    """创建 JWT，有效期由配置决定（默认 90 天），含 jti 供登出吊销。"""
    config = get_config()
    expire_hours = config.api.auth.token_expire_hours
    payload = {
        "sub": user_id,
        "jti": uuid.uuid4().hex,
        "exp": datetime.now(timezone.utc) + timedelta(hours=expire_hours),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)


def revoke_jwt(jti: str) -> None:
    """把 jti 加入黑名单（登出后 token 立即失效）。

    惰性清理：超过 token 最长有效期（90 天）的吊销记录已无意义，顺手删除，
    避免黑名单在长跑进程中无界增长（每登出一次加一条）。
    """
    if not jti:
        return
    now = time.time()
    cutoff = now - _JTI_MAX_TTL_S
    if len(_jti_blacklist) > 1000:  # 仅在累积较多时清理，避免每次登出都全量扫描
        for stale_jti in [k for k, ts in _jti_blacklist.items() if ts < cutoff]:
            _jti_blacklist.pop(stale_jti, None)
    _jti_blacklist[jti] = now


def revoke_token(token: str) -> None:
    """解码 token 并吊销其 jti（登出用）；解码失败静默忽略。"""
    try:
        payload = jwt.decode(
            token,
            get_jwt_secret(),
            algorithms=[JWT_ALGORITHM],
            options={"verify_exp": False},  # 允许吊销已过期 token（无害）
        )
    except jwt.InvalidTokenError:
        return
    revoke_jwt(payload.get("jti", ""))


def _is_revoked(payload: dict[str, Any]) -> bool:
    """判断 token 的 jti 是否已被吊销。"""
    jti = payload.get("jti")
    return bool(jti) and jti in _jti_blacklist


def verify_jwt(token: str) -> dict[str, Any]:
    """验证 JWT，返回 payload；吊销或无效则抛 AuthError。"""
    try:
        payload = jwt.decode(token, get_jwt_secret(), algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise AuthError("登录已过期，请重新登录")
    except jwt.InvalidTokenError:
        raise AuthError("无效的 token")
    if _is_revoked(payload):
        raise AuthError("token 已失效，请重新登录")
    return payload


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
