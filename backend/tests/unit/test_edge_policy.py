"""L1 端侧策略（热修）的契约测试。

口径见 `docs/多端跨端-工程议题（网络层·包体·热修复）.md` §3.2：
**白名单 + 签名 + 空间戳绑定 + 灰度**，其中"未知键整包拒绝"与"空间戳绑定"是两条硬约束
（前者防止悄悄改变端侧行为，后者防止热修把检索改坏）。
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from app.core.edge_policy import (
    POLICY_KEYS,
    PolicyError,
    applies_to_device,
    build_policy,
    canonical,
    policy_secret,
    sign_payload,
    validate_policy,
    verify_payload,
)
from app.core.plane_router import EMBEDDING_SPACE


def make_config(*, prefer: str = "edge", guard: int = 60):
    edge = SimpleNamespace(max_input_tokens=2048, max_output_tokens=512, max_ttft_ms=800,
                           models={"short": "qwen3.5-2b", "default": "qwen3.5-4b"})
    routing = SimpleNamespace(enabled=True, prefer=prefer, stream_guard_chars=guard,
                              escalate_on=["empty", "timeout"], available_tools=["kb_search"])
    llm = SimpleNamespace(planes=SimpleNamespace(edge=edge, routing=routing),
                          roles={}, jwt_secret="test-secret")
    return SimpleNamespace(llm=llm)


class TestPolicyShape:
    def test_payload_has_all_contracted_fields(self):
        p = build_policy(make_config(), now=1_700_000_000)
        for key in ("version", "issuedAt", "expiresAt", "embeddingSpace", "rollout", "policy", "signature"):
            assert key in p, key
        assert p["embeddingSpace"] == EMBEDDING_SPACE          # 空间戳绑定
        assert p["issuedAt"] == 1_700_000_000
        assert p["expiresAt"] > p["issuedAt"]
        assert p["deviceProfiles"] == {}                       # 预留字段（本期不实现）

    def test_baseline_comes_from_server_config(self):
        p = build_policy(make_config(prefer="cloud", guard=80))
        assert p["policy"]["streamGuardChars"] == 80
        assert p["policy"]["preferPlane"] == "cloud"
        assert p["policy"]["escalateOn"] == ["empty", "timeout"]
        assert p["policy"]["modelTier"]["default"] == "qwen3.5-4b"

    def test_version_is_content_hash_and_changes_with_content(self):
        a = build_policy(make_config(), now=1)[ "version"]
        b = build_policy(make_config(), now=2)["version"]        # 时间不影响内容哈希
        c = build_policy(make_config(guard=61), now=1)["version"]
        assert a == b
        assert a != c


class TestWhitelist:
    def test_unknown_key_is_rejected_whole_package(self):
        with pytest.raises(PolicyError, match="未登记"):
            validate_policy({"streamGuardChars": 60, "somethingElse": 1})

    def test_reserved_device_profiles_is_allowed_but_ignored(self):
        validate_policy({"streamGuardChars": 60, "deviceProfiles": {}})   # 不抛

    def test_out_of_range_values_rejected(self):
        for bad in ({"streamGuardChars": -1}, {"streamGuardChars": 9999},
                    {"retrievalMinScore": 1.5}, {"maxOutputTokens": 0}):
            with pytest.raises(PolicyError):
                validate_policy(bad)

    def test_every_key_in_policy_is_whitelisted(self):
        p = build_policy(make_config())
        assert set(p["policy"]) <= POLICY_KEYS


class TestSignature:
    def test_signature_verifies_with_same_secret(self):
        cfg = make_config()
        p = build_policy(cfg, now=1)
        assert verify_payload(p, policy_secret(cfg))

    def test_tampered_policy_fails_verification(self):
        cfg = make_config()
        p = build_policy(cfg, now=1)
        p["policy"]["streamGuardChars"] = 999          # 中间人改一个值
        assert not verify_payload(p, policy_secret(cfg))

    def test_tampered_rollout_fails_verification(self):
        cfg = make_config()
        p = build_policy(cfg, now=1, rollout_percent=0)
        p["rollout"]["percent"] = 100                  # 想跳过灰度 → 必须被发现
        assert not verify_payload(p, policy_secret(cfg))

    def test_changing_embedding_space_breaks_signature(self):
        """空间戳在签名覆盖范围内：否则"换个空间戳绕开阈值校验"就成了后门。"""
        cfg = make_config()
        p = build_policy(cfg, now=1)
        p["embeddingSpace"] = "some/other-model@512"
        assert not verify_payload(p, policy_secret(cfg))

    def test_missing_signature_is_false_not_exception(self):
        assert not verify_payload({"version": 1}, "secret")

    def test_signature_is_stable_for_same_input(self):
        assert sign_payload({"version": 1, "policy": {}}, "s") == sign_payload(
            {"policy": {}, "version": 1}, "s")          # 键序无关（canonical 排序）


class TestRollout:
    def test_full_rollout_applies_to_everyone(self):
        p = {"rollout": {"percent": 100, "salt": "x"}}
        assert all(applies_to_device(p, f"dev-{i}") for i in range(50))

    def test_zero_rollout_applies_to_nobody(self):
        p = {"rollout": {"percent": 0, "salt": "x"}}
        assert not any(applies_to_device(p, f"dev-{i}") for i in range(50))

    def test_partial_rollout_is_deterministic_and_bucketed(self):
        p = {"rollout": {"percent": 30, "salt": "2026w39"}}
        first = [applies_to_device(p, f"dev-{i}") for i in range(400)]
        second = [applies_to_device(p, f"dev-{i}") for i in range(400)]
        assert first == second, "同一设备同一次灰度必须稳定（不能用不稳定哈希）"
        ratio = sum(first) / len(first)
        assert 0.15 < ratio < 0.45, f"30% 灰度的实际比例 {ratio:.2f} 偏离过大"

    def test_different_salt_moves_devices(self):
        a = {"rollout": {"percent": 50, "salt": "w39"}}
        b = {"rollout": {"percent": 50, "salt": "w40"}}
        assert [applies_to_device(a, f"d{i}") for i in range(200)] != \
               [applies_to_device(b, f"d{i}") for i in range(200)]


class TestEndpoint:
    @pytest.mark.asyncio
    async def test_policy_endpoint_returns_signed_payload(self, monkeypatch):
        """端到端（TestClient 太重建在这里直接调路由函数）：返回体自校验必须通过。"""
        from app.api.routes import edge as edge_route
        from app.core import config as config_module

        cfg = make_config()
        monkeypatch.setattr(config_module, "get_config", lambda *a, **k: cfg)
        monkeypatch.setattr(edge_route, "get_config", lambda *a, **k: cfg)

        payload = await edge_route.get_policy(device_id="dev-1", rollout_percent=100)
        assert verify_payload(payload, policy_secret(cfg))
        assert payload["policy"]["streamGuardChars"] == 60
        assert payload["issuedAt"] <= int(time.time()) <= payload["expiresAt"]

    def test_canonical_json_is_stable(self):
        assert canonical({"b": 1, "a": [1, 2]}) == '{"a":[1,2],"b":1}'
