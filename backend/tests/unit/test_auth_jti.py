"""JWT jti 黑名单（登出吊销）的单元测试。"""

from __future__ import annotations

import pytest

from app.core.auth import create_jwt, revoke_jwt, revoke_token, verify_jwt
from app.core.exceptions import AuthError

_STRONG_SECRET = "x" * 48


@pytest.fixture(autouse=True)
def _set_secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", _STRONG_SECRET)


class TestJtiBlacklist:
    def test_create_jwt_includes_jti(self):
        token = create_jwt("u1")
        payload = verify_jwt(token)
        assert "jti" in payload
        assert payload["jti"]

    def test_revoked_jti_fails_verify(self):
        token = create_jwt("u1")
        payload = verify_jwt(token)
        revoke_jwt(payload["jti"])
        with pytest.raises(AuthError):
            verify_jwt(token)

    def test_revoke_token_invalidates_token(self):
        token = create_jwt("u1")
        assert verify_jwt(token)["sub"] == "u1"
        revoke_token(token)
        with pytest.raises(AuthError):
            verify_jwt(token)

    def test_unrelated_token_not_revoked(self):
        t1 = create_jwt("u1")
        t2 = create_jwt("u2")
        revoke_token(t1)
        # t2 不受影响
        assert verify_jwt(t2)["sub"] == "u2"

    def test_revoke_token_bad_token_noop(self):
        # 非法 token 吊销静默忽略，不抛异常
        revoke_token("not-a-valid-token")
