"""设备身份 API（端云协同 S1，RFC §4.5-H / §7）。

四个动作，构成设备凭证的完整生命周期：

| 动作 | 端点 | 谁调用 | 用途 |
|---|---|---|---|
| 注册换证 | `POST /api/v1/device/enroll` | **用户** token（网页端登录后，或设备首次登录时） | 拿到长效 `device_token` |
| 轮换 | `POST /api/v1/device/refresh` | **设备** token | 到期前换新（旧 jti 立即失效） |
| 吊销 | `DELETE /api/v1/device/{device_id}` | **用户** token | 手机丢了唯一的止损手段 |
| 心跳 | `POST /api/v1/device/heartbeat` | **设备** token | 更新 `last_seen` + 上报版本戳 |

为什么注册要用**用户 token**而不是自己发明一套配对码
--------------------------------------------------
设备必须先证明"我是这台账号的主人"，否则任何人都能注册成你的设备。
M2 的做法是复用已有的登录流程（用户 token 换设备 token），少一套配对码的状态机；
配对码（在电视/车机上输入 6 位码）等到真正需要"无输入设备"的场景再引入（RFC §13 T4）。

**只回一次 token**：``device_token`` 只在 enroll / refresh 的响应里出现，注册表只存 jti。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.access import require_full_access
from app.core.audit import AuditAction, audit_log, get_client_ip
from app.core.auth import (
    create_device_jwt,
    get_current_claims,
    get_current_device,
    get_current_user,
    is_device_token,
    revoke_jwt,
)
from app.core.config import get_config
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/device", tags=["device"])


def _store() -> Any:
    """取设备注册表（依赖注入；未初始化时 500）。"""
    from app.core.bootstrap import get_app_context

    ctx = get_app_context()
    if getattr(ctx, "device_storage", None) is None:
        raise HTTPException(500, "设备存储未初始化")
    return ctx.device_storage


class EnrollRequest(BaseModel):
    """注册请求：设备自报家门（都只用于展示与排查，不参与鉴权决策）。"""

    name: str = Field("", max_length=64, description="设备显示名，如「我的 Pixel」")
    platform: str = Field("", max_length=32, description="android | ios | macos | harmony")
    app_version: str = Field("", max_length=32)
    embedding_space: str = Field("", max_length=64,
                                 description="端侧向量空间标识，跨端交换必须一致（§4.5-F）")


class DeviceTokenResponse(BaseModel):
    device_id: str
    device_token: str
    token_type: str = "Bearer"
    expires_in_hours: int
    scope: str
    plane_policy_version: str = ""


class HeartbeatRequest(BaseModel):
    app_version: str = Field("", max_length=32)
    embedding_space: str = Field("", max_length=64)


@router.post("/enroll", response_model=DeviceTokenResponse)
async def enroll(
    body: EnrollRequest,
    request: Request,
    user_id: str = Depends(require_full_access),
) -> DeviceTokenResponse:
    """用**用户 token** 换取设备凭证（设备首次接入时调一次）。"""
    store = _store()
    config = get_config()
    try:
        rec = store.register(user_id, name=body.name, platform=body.platform,
                            app_version=body.app_version,
                            embedding_space=body.embedding_space)
    except ValueError as e:
        raise HTTPException(429, str(e))
    token = create_device_jwt(user_id, rec.device_id)
    # 注册后把 jti 落库：吊销时才能判断"这个 token 是不是本设备当前的"
    store.rotate(rec.device_id, _jti_of(token))

    audit_log(AuditAction.DEVICE_ENROLL, success=True, ip=get_client_ip(request),
              detail={"device_id": rec.device_id, "platform": rec.platform})
    logger.info("设备接入", device_id=rec.device_id, platform=rec.platform)
    return DeviceTokenResponse(
        device_id=rec.device_id, device_token=token,
        expires_in_hours=config.api.auth.device_token_expire_hours,
        scope="edge")


@router.post("/refresh", response_model=DeviceTokenResponse)
async def refresh(
    device_id: str = Depends(get_current_device),
) -> DeviceTokenResponse:
    """设备 token 轮换：返回新 token，**旧 token 立即失效**（jti 进吊销列表）。

    为什么不用"到期前一直能用旧 token"：这样泄漏的旧 token 在有效期内仍可作恶，
    轮换就失去意义。所以 ``is_jti_revoked`` 判定 ``jti != 当前 jti`` 即失效。
    """
    store = _store()
    config = get_config()
    rec = store.get(device_id)
    if rec is None or rec.revoked:
        raise HTTPException(403, "设备已被吊销或不存在")
    token = create_device_jwt(rec.user_id, device_id)
    store.rotate(device_id, _jti_of(token))
    return DeviceTokenResponse(
        device_id=device_id, device_token=token,
        expires_in_hours=config.api.auth.device_token_expire_hours,
        scope="edge")


@router.post("/heartbeat")
async def heartbeat(
    body: HeartbeatRequest,
    device_id: str = Depends(get_current_device),
) -> dict[str, Any]:
    """心跳：更新 ``last_seen`` 与客户端版本戳（用于排查端侧版本不一致）。"""
    store = _store()
    store.touch(device_id, app_version=body.app_version,
                embedding_space=body.embedding_space)
    rec = store.get(device_id)
    return {"ok": True, "device_id": device_id,
            "last_seen_at": rec.last_seen_at if rec else 0.0}


@router.get("/list")
async def list_devices(
    claims: dict[str, Any] = Depends(get_current_claims),
    user_id: str = Depends(get_current_user),
) -> dict[str, Any]:
    """列出当前用户已注册的设备（设备 token 也能查自己所属用户，便于客户端自查）。"""
    store = _store()
    devices = [r.to_public() for r in store.list_for_user(user_id)]
    return {"total": len(devices), "devices": devices,
            "current_device_id": claims.get("device_id", "")}


@router.delete("/{device_id}")
async def revoke_device(
    device_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
    claims: dict[str, Any] = Depends(get_current_claims),
) -> dict[str, Any]:
    """吊销设备（**用户 token 专属**：设备不能吊销自己，否则被攻破后可自杀灭迹）。"""
    if is_device_token(claims):
        raise HTTPException(403, "设备 token 不能吊销设备，请在网页端操作")
    store = _store()
    rec = store.get(device_id)
    if rec is None:
        raise HTTPException(404, "设备不存在")
    if rec.user_id != user_id:
        # 不区分"不存在"与"不是你的"，避免探测他人 device_id 是否存在
        raise HTTPException(404, "设备不存在")
    revoked = store.revoke(device_id)
    if revoked and revoked.token_jti:
        revoke_jwt(revoked.token_jti)
    audit_log(AuditAction.DEVICE_REVOKE, success=True, ip=get_client_ip(request),
              detail={"device_id": device_id})
    return {"ok": True, "device_id": device_id, "revoked": True}


def _jti_of(token: str) -> str:
    """取 token 的 jti（不验签：token 是我们刚生成的，只为落库比对）。"""
    import jwt as _jwt

    try:
        return str(_jwt.decode(token, options={"verify_signature": False}).get("jti", ""))
    except Exception:  # noqa: BLE001 - 解不出就当作空 jti（吊销判定会保守处理）
        return ""
