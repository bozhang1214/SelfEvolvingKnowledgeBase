"""设备身份（端云协同 S1）的单元测试：注册 / 轮换 / 吊销 / 最小权限边界。

覆盖两类风险：
1. **凭证生命周期**：轮换后旧 token 必须**立刻**失效（否则轮换毫无意义）、
   吊销后立刻失效（手机丢了这是唯一止损手段）、设备不能吊销自己（被攻破后灭迹）；
2. **最小权限**：设备 token 长效且存在客户端本地，必须不能读写知识资产。
"""
from __future__ import annotations

import jwt as _jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.routes import device as device_route
from app.core import auth
from app.core.access import require_full_access, require_user_account
from app.core.auth import (
    DEVICE_TOKEN_TYPE,
    create_device_jwt,
    create_jwt,
    get_current_claims,
    is_device_token,
    verify_jwt,
)
from app.storage.device_storage import MAX_DEVICES_PER_USER, DeviceStore

_STRONG_SECRET = "x" * 48


@pytest.fixture(autouse=True)
def _set_secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", _STRONG_SECRET)
    monkeypatch.delenv("ALLOWED_EMAILS", raising=False)


@pytest.fixture
def store(tmp_path) -> DeviceStore:
    return DeviceStore(tmp_path / "devices.json")


@pytest.fixture
def client(store, monkeypatch):
    """真实 JWT 校验 + 临时设备注册表（只替换存储，不替换鉴权）。"""
    monkeypatch.setattr(device_route, "_store", lambda: store)
    monkeypatch.setattr(auth, "_device_store", lambda: store)
    app = FastAPI()
    app.include_router(device_route.router)
    return TestClient(app)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _enroll(client: TestClient, user_token: str, **body) -> dict:
    resp = client.post("/api/v1/device/enroll", json=body or {"name": "我的 Pixel",
                                                              "platform": "android"},
                       headers=_auth(user_token))
    assert resp.status_code == 200, resp.text
    return resp.json()


# ============================================================
# 注册表本身
# ============================================================

class TestDeviceStore:
    def test_register_and_get(self, store):
        rec = store.register("u1", name="Pixel", platform="android", token_jti="j1")
        assert rec.device_id and rec.user_id == "u1"
        assert store.get(rec.device_id).platform == "android"
        assert store.get("nope") is None

    def test_per_user_device_cap(self, store):
        for i in range(MAX_DEVICES_PER_USER):
            store.register("u1", name=f"d{i}")
        with pytest.raises(ValueError):
            store.register("u1", name="overflow")
        store.register("u2", name="其他用户不受影响")

    def test_rotate_invalidates_old_jti_only(self, store):
        rec = store.register("u1", token_jti="old")
        store.rotate(rec.device_id, "new")
        assert store.is_jti_revoked(rec.device_id, "old") is True
        assert store.is_jti_revoked(rec.device_id, "new") is False

    def test_revoke_invalidates_everything(self, store):
        rec = store.register("u1", token_jti="old")
        store.rotate(rec.device_id, "new")
        store.revoke(rec.device_id)
        assert store.is_jti_revoked(rec.device_id, "new") is True
        assert store.is_jti_revoked(rec.device_id, "old") is True
        assert store.get(rec.device_id).revoked is True

    def test_unknown_device_is_treated_as_revoked(self, store):
        """查不到记录 → 一律当失效（失败关闭），避免删表即绕过校验。"""
        assert store.is_jti_revoked("ghost", "any") is True

    def test_persisted_across_instances(self, tmp_path):
        path = tmp_path / "devices.json"
        rec = DeviceStore(path).register("u1", name="Pixel", token_jti="j1")
        again = DeviceStore(path).get(rec.device_id)
        assert again is not None and again.name == "Pixel" and again.token_jti == "j1"

    def test_corrupt_registry_does_not_crash(self, tmp_path):
        path = tmp_path / "devices.json"
        path.write_text("{ 这不是 JSON", encoding="utf-8")
        s = DeviceStore(path)
        assert s.list_for_user("u1") == []
        s.register("u1", name="仍可注册")

    def test_public_view_hides_credentials(self, store):
        rec = store.register("u1", token_jti="secret-jti")
        public = rec.to_public()
        assert "token_jti" not in public and "revoked_jtis" not in public
        assert "secret-jti" not in str(public)

    def test_touch_updates_last_seen_and_stamps(self, store):
        rec = store.register("u1")
        before = rec.last_seen_at
        store.touch(rec.device_id, app_version="0.1.0", embedding_space="bge@512")
        after = store.get(rec.device_id)
        assert after.last_seen_at >= before
        assert after.app_version == "0.1.0" and after.embedding_space == "bge@512"

    def test_touch_ignores_revoked(self, store):
        rec = store.register("u1")
        store.revoke(rec.device_id)
        seen = store.get(rec.device_id).last_seen_at
        store.touch(rec.device_id, app_version="9.9.9")
        assert store.get(rec.device_id).app_version == ""
        assert store.get(rec.device_id).last_seen_at == seen


# ============================================================
# token 域
# ============================================================

class TestDeviceTokenDomain:
    def test_payload_carries_device_domain(self):
        token = create_device_jwt("u1", "dev-1")
        payload = verify_jwt(token)
        assert payload["typ"] == DEVICE_TOKEN_TYPE
        assert payload["device_id"] == "dev-1"
        assert payload["scope"] == "edge"
        assert is_device_token(payload) is True

    def test_user_token_is_not_device_token(self):
        assert is_device_token(verify_jwt(create_jwt("u1"))) is False

    def test_expiry_from_config(self):
        token = create_device_jwt("u1", "dev-1", expire_hours=1)
        payload = _jwt.decode(token, options={"verify_signature": False})
        assert 3500 < payload["exp"] - payload["iat"] <= 3600

    def test_rotated_jti_rejected_by_dependency(self, store, monkeypatch):
        """轮换后旧 token 在 auth 层被拒（这是"轮换"有意义的唯一证明）。"""
        monkeypatch.setattr(auth, "_device_store", lambda: store)
        rec = store.register("u1", token_jti="old")
        store.rotate(rec.device_id, "new")
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as e:
            auth._ensure_device_token_valid({"device_id": rec.device_id, "jti": "old"})
        assert e.value.status_code == 401


# ============================================================
# HTTP 生命周期
# ============================================================

class TestDeviceLifecycleApi:
    def test_enroll_requires_user_token(self, client):
        assert client.post("/api/v1/device/enroll", json={}).status_code == 401

    def test_enroll_returns_long_lived_edge_token(self, client):
        body = _enroll(client, create_jwt("u1"), name="Pixel", platform="android")
        assert body["device_id"] and body["device_token"]
        assert body["scope"] == "edge" and body["token_type"] == "Bearer"
        payload = verify_jwt(body["device_token"])
        assert payload["device_id"] == body["device_id"]
        assert payload["scope"] == "edge"

    def test_two_devices_can_coexist(self, client, store):
        t = create_jwt("u1")
        _enroll(client, t, name="手机", platform="android")
        _enroll(client, t, name="Mac", platform="macos")
        listed = client.get("/api/v1/device/list", headers=_auth(t)).json()
        assert listed["total"] == 2
        assert {d["name"] for d in listed["devices"]} == {"手机", "Mac"}

    def test_refresh_rotates_and_kills_old_token(self, client):
        enrolled = _enroll(client, create_jwt("u1"))
        old = enrolled["device_token"]
        rotated = client.post("/api/v1/device/refresh", headers=_auth(old))
        assert rotated.status_code == 200, rotated.text
        new = rotated.json()["device_token"]
        assert new != old

        # 新 token 可用（心跳成功），旧 token 立刻失效
        assert client.post("/api/v1/device/heartbeat", json={"app_version": "0.2.0"},
                           headers=_auth(new)).status_code == 200
        assert client.post("/api/v1/device/heartbeat", json={},
                           headers=_auth(old)).status_code == 401

    def test_heartbeat_requires_device_token(self, client):
        """用户 token 不能冒充设备（否则 last_seen 与版本戳全是假数据）。"""
        resp = client.post("/api/v1/device/heartbeat", json={},
                           headers=_auth(create_jwt("u1")))
        assert resp.status_code == 403

    def test_list_reports_current_device(self, client):
        enrolled = _enroll(client, create_jwt("u1"))
        listed = client.get("/api/v1/device/list",
                            headers=_auth(enrolled["device_token"])).json()
        assert listed["current_device_id"] == enrolled["device_id"]

    def test_revoke_kills_device_token(self, client):
        enrolled = _enroll(client, create_jwt("u1"))
        device_token = enrolled["device_token"]
        resp = client.delete(f"/api/v1/device/{enrolled['device_id']}",
                             headers=_auth(create_jwt("u1")))
        assert resp.status_code == 200, resp.text
        assert client.post("/api/v1/device/heartbeat", json={},
                           headers=_auth(device_token)).status_code == 401

    def test_device_cannot_revoke_itself(self, client):
        """被攻破的设备不能"自杀灭迹"——吊销只能由用户在网页端发起。"""
        enrolled = _enroll(client, create_jwt("u1"))
        resp = client.delete(f"/api/v1/device/{enrolled['device_id']}",
                             headers=_auth(enrolled["device_token"]))
        assert resp.status_code == 403
        assert client.post("/api/v1/device/heartbeat", json={},
                           headers=_auth(enrolled["device_token"])).status_code == 200

    def test_cannot_revoke_someone_elses_device(self, client):
        enrolled = _enroll(client, create_jwt("u1"))
        resp = client.delete(f"/api/v1/device/{enrolled['device_id']}",
                             headers=_auth(create_jwt("u2")))
        assert resp.status_code == 404            # 不区分"不存在"与"不是你的"
        assert client.post("/api/v1/device/heartbeat", json={},
                           headers=_auth(enrolled["device_token"])).status_code == 200


# ============================================================
# 最小权限边界
# ============================================================

class TestDeviceLeastPrivilege:
    def _app(self) -> FastAPI:
        app = FastAPI()

        @app.get("/write")
        async def write(_: str = Depends(require_user_account)) -> dict:
            return {"ok": True}

        @app.get("/chat-like")
        async def chat_like(_: str = Depends(require_full_access)) -> dict:
            return {"ok": True}

        @app.get("/claims")
        async def claims(c: dict = Depends(get_current_claims)) -> dict:
            return {"device_id": c.get("device_id", ""), "typ": c.get("typ", "")}

        return app

    def test_device_token_rejected_on_asset_routes(self, store, monkeypatch):
        monkeypatch.setattr(auth, "_device_store", lambda: store)
        rec = store.register("u1", token_jti="j1")
        token = create_device_jwt("u1", rec.device_id)
        store.rotate(rec.device_id, _jti(token))
        c = TestClient(self._app())
        assert c.get("/write", headers=_auth(create_jwt("u1"))).status_code == 200
        assert c.get("/write", headers=_auth(token)).status_code == 403

    def test_asset_routers_use_the_device_guard(self):
        """接线回归：三条资产路由必须挂着设备闸门（防止有人改回 require_full_access）。"""
        from app.api.routes import knowledge, share, upload

        for mod in (upload, knowledge, share):
            deps = [getattr(d, "dependency", None) for d in mod.router.dependencies]
            assert require_user_account in deps, f"{mod.__name__} 缺少设备 token 闸门"

    def test_chat_and_edge_routers_still_accept_devices(self):
        """反向接线：设备**必须**能聊天与上报，否则端侧就是死的。"""
        from app.api.routes import chat, conversations

        for mod in (chat, conversations):
            deps = [getattr(d, "dependency", None) for d in mod.router.dependencies]
            assert require_user_account not in deps, f"{mod.__name__} 不该挡设备 token"

    def test_revoked_device_token_cannot_chat(self, store, monkeypatch):
        """**咽喉点回归**：吊销/轮换的校验必须在 `require_full_access` 这条路上生效。

        聊天路由用的是 `require_full_access`（不是 `require_user_account`）。如果校验
        只挂在后者上，被吊销的设备 token 照样能聊天，"吊销"就成了摆设。
        """
        monkeypatch.setattr(auth, "_device_store", lambda: store)
        rec = store.register("u1", token_jti="j1")
        token = create_device_jwt("u1", rec.device_id)
        store.rotate(rec.device_id, _jti(token))
        c = TestClient(self._app())
        assert c.get("/chat-like", headers=_auth(token)).status_code == 200

        store.revoke(rec.device_id)
        assert c.get("/chat-like", headers=_auth(token)).status_code == 401
        assert c.get("/write", headers=_auth(token)).status_code == 401

    def test_claims_expose_device_identity(self, store, monkeypatch):
        monkeypatch.setattr(auth, "_device_store", lambda: store)
        rec = store.register("u1", token_jti="j1")
        token = create_device_jwt("u1", rec.device_id)
        store.rotate(rec.device_id, _jti(token))
        c = TestClient(self._app())
        got = c.get("/claims", headers=_auth(token)).json()
        assert got["typ"] == "device" and got["device_id"] == rec.device_id


def _jti(token: str) -> str:
    return str(_jwt.decode(token, options={"verify_signature": False})["jti"])
