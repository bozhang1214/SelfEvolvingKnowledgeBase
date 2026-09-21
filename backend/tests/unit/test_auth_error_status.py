"""鉴权失败必须是 **401**，不能是 500。

背景（2026-09-21 线上实测）：`curl -H "Authorization: Bearer 1306266881@qq.com"
/api/v1/auth/me` 返回 500 + error_id，而 `_claims_or_401` 的注释早就写着
「缺失/无效统一 401」——**「无效」那一半没生效**。

根因：``AuthError`` 继承的是 ``Exception`` 而**不是** ``SEKBError``
（``app/core/exceptions.py``），于是它穿过了 SEKBError 处理器，也不是
HTTPException，最后落进兜底的 ``Exception`` 处理器 → 500。

为什么这不是小事：前端**只在 401 时**才判定 token 失效并跳登录页
（``frontend/src/services/api.ts:68-71``）。token 过期/被吊销/畸形时拿到 500，
用户看到的是「服务内部错误」——既不跳登录，也不清 token。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

from app.api.server import create_app
from app.core.auth import JWT_ALGORITHM, revoke_jwt

#: 必须满足 get_jwt_secret 的强度要求：≥32 字符，且不含 test/secret/change 等占位词
PROBE_KEY = "sekb-unit-probe-signing-key-0123456789abcdef"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", PROBE_KEY)
    monkeypatch.delenv("ALLOWED_EMAILS", raising=False)


@pytest.fixture
def client() -> TestClient:
    # raise_server_exceptions=False：500 也当普通响应看，便于断言"不是 500"
    return TestClient(create_app(), raise_server_exceptions=False)


def _token(**claims: object) -> str:
    payload = {"sub": "user-probe", "jti": "probe-jti", **claims}
    return pyjwt.encode(payload, PROBE_KEY, algorithm=JWT_ALGORITHM)


def test_malformed_bearer_token_is_401_not_500(client: TestClient) -> None:
    """把邮箱当 token 用（真实误用场景）必须 401。"""
    r = client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer 1306266881@qq.com"}
    )
    assert r.status_code == 401, f"畸形 token 应为 401，实际 {r.status_code}: {r.text}"
    assert "token" in r.json()["detail"]


def test_expired_token_is_401(client: TestClient) -> None:
    expired = _token(exp=datetime.now(timezone.utc) - timedelta(hours=1))
    r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401, r.text
    assert "过期" in r.json()["detail"]


def test_revoked_token_is_401(client: TestClient) -> None:
    token = _token(jti="probe-revoked-jti")
    revoke_jwt("probe-revoked-jti")
    r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401, r.text
    assert "失效" in r.json()["detail"]


def test_missing_token_is_401(client: TestClient) -> None:
    """没有 Authorization 头同样是 401（这条本来就对，一起钉住契约）。"""
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 401, r.text
