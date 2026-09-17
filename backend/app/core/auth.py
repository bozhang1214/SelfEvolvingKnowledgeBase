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
    """使用 PBKDF2-HMAC-SHA256（100000 次迭代 + 16 字节随机盐）哈希密码。

    后续计划迁移到 bcrypt/argon2id（见 BACKLOG 密钥加固项）。
    """
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


#: JWT 里的类型标记：区分"用户 token"与"设备 token"（RFC §4.5-H）
DEVICE_TOKEN_TYPE = "device"
#: 设备 token 的权限域（当前只有 edge：聊天/上报/设备能力，不含知识库写入）
DEVICE_SCOPE = "edge"


def create_device_jwt(user_id: str, device_id: str, *, expire_hours: int | None = None) -> str:
    """创建设备 token（长有效期 + 独立域，可单独吊销/轮换）。

    与用户 token 的关系：``sub`` 仍是 ``user_id``，所以设备**继承该用户的访问级别**；
    区别在多了 ``typ=device`` / ``device_id`` / ``scope`` 三个字段，
    服务端据此判断"这是设备在替用户做事"（不能改知识库、不能建公开分享），
    并按 ``(user, device)`` 分片统计（§4.5-I）。
    """
    config = get_config()
    hours = expire_hours or config.api.auth.device_token_expire_hours
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "typ": DEVICE_TOKEN_TYPE,
        "device_id": device_id,
        "scope": DEVICE_SCOPE,
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + timedelta(hours=hours),
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)


def is_device_token(payload: dict[str, Any]) -> bool:
    """payload 是否来自设备 token。"""
    return payload.get("typ") == DEVICE_TOKEN_TYPE and bool(payload.get("device_id"))


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


def _claims_or_401(
    credentials: HTTPAuthorizationCredentials | None,
) -> dict[str, Any]:
    """从请求头解析并校验 JWT；缺失/无效统一 401。"""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return verify_jwt(credentials.credentials)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> str:
    """FastAPI 依赖项：从请求头提取当前用户 ID。

    设备 token 的 ``sub`` 同样是 ``user_id``，所以设备**继承用户的访问级别**；
    要区分"这是设备"的地方用 :func:`get_current_claims`（见 ``require_user_account``）。

    设备 token 的吊销/轮换校验在这里一并完成（``_claims_checked``）——挂在
    ``get_current_user`` 上是刻意的：绝大多数路由只依赖它，校验放在这里才没有漏网。
    """
    return str(_claims_checked(credentials)["sub"])


def _claims_checked(
    credentials: HTTPAuthorizationCredentials | None,
) -> dict[str, Any]:
    """解析 + 校验 JWT，并对**设备 token** 追加生命周期校验。

    这是所有鉴权依赖（``get_current_user`` / ``get_current_claims``）唯一的入口，
    所以"设备已被吊销/轮换"这一条在**任何**受保护路由上都成立，不依赖各路由自觉。
    """
    claims = _claims_or_401(credentials)
    if is_device_token(claims):
        _ensure_device_token_valid(claims)
    return claims


def _device_store() -> Any:
    """取设备注册表（惰性导入避免 auth ↔ bootstrap 循环依赖；测试可覆盖此函数）。"""
    from app.core.bootstrap import get_app_context

    try:
        return getattr(get_app_context(), "device_storage", None)
    except Exception as e:  # noqa: BLE001 - 校验不可用时**失败关闭**，不放行
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="设备凭证校验暂不可用") from e


def _ensure_device_token_valid(claims: dict[str, Any]) -> None:
    """设备 token 的**额外**校验：设备是否已吊销、jti 是否已被轮换掉。

    为什么必须在**每次请求**都查：轮换/吊销的意义就是"旧凭证立刻作废"。
    只靠 JWT 的 ``exp``（默认 30 天）等于给泄漏的 token 留了一个月的窗口。
    用户 token 不受影响（只对 ``typ=device`` 生效），所以老路径零开销。
    """
    store = _device_store()
    if store is None:
        return                      # 未装配设备存储（如纯单机模式）→ 不存在设备凭证可言
    device_id = str(claims.get("device_id", ""))
    if store.is_jti_revoked(device_id, str(claims.get("jti", ""))):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="设备凭证已失效，请重新接入",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_claims(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict[str, Any]:
    """FastAPI 依赖项：返回完整 JWT payload（含 ``typ`` / ``device_id`` / ``scope``）。

    设备 token 会在这里顺带做一次"是否已吊销/已被轮换"的校验——所有接受设备
    凭证的端点（chat / conversations / edge 上报）因此**自动**获得该保护。
    """
    claims = _claims_or_401(credentials)
    if is_device_token(claims):
        _ensure_device_token_valid(claims)
    return claims


async def get_current_device(
    claims: dict[str, Any] = Depends(get_current_claims),
) -> str:
    """FastAPI 依赖项：要求**设备 token**，返回 ``device_id``；用户 token 一律 403。

    用于"只有设备会调"的端点（如设备上报），避免用户 token 伪造设备身份。
    """
    if not is_device_token(claims):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="该接口仅接受设备 token")
    return str(claims["device_id"])
