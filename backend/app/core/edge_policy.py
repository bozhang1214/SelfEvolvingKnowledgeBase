"""端侧策略（L1 热修）的**生成、签名与校验**。

对应 `docs/多端跨端-工程议题（网络层·包体·热修复）.md` §3.2（L1 本期实现的规格）。

为什么要有这一层
----------------
端侧上的一些行为参数（升级信号开关、前缀守卫长度、端侧预算、模型档位、提示词、工具白名单、
检索阈值）**不该靠发版来改**：调错一个阈值就要重新发版，代价太高；但把它们做成"服务端随便下发"
又会绕过版本守卫，风险更大。

所以这里的口径是：**白名单 + 签名 + 空间戳绑定 + 可灰度 + 可回滚**——

1. **白名单**：只有 [POLICY_KEYS] 里登记过的键允许出现。**多一个未知键 → 整包拒绝**
   （不是"忽略未知键"）：否则一次误配置就能悄悄改变端侧行为，而没人知道为什么；
2. **签名**：HMAC-SHA256 覆盖 `version + embeddingSpace + rollout + policy`，
   端侧校验失败即整包丢弃、继续用上一份（防篡改与中间人）；
3. **空间戳绑定**：策略里带 `embeddingSpace`。**改检索阈值必须与空间戳同源**——
   M3 已经吃过教训（换 int8 后阈值要从 0.4 重标到 0.5），端侧空间不符就拒绝应用，
   避免"热修把检索改坏"；
4. **灰度**：`rollout = {percent, salt}`，端侧按 `hash(deviceId + salt) % 100 < percent` 决定是否应用；
5. **版本**：`version` 由策略内容哈希得出（内容不变则版本不变），便于端侧比对与回滚。

⚠️ 本期**不实现**「设备适配」类策略（机型/SoC/内存档位映射）：schema 里保留 `deviceProfiles`
空对象占位，端侧白名单校验会明确跳过它（见工程议题文档 §3.3）。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Any

from app.core.logging import get_logger
from app.core.plane_router import EMBEDDING_SPACE

logger = get_logger(__name__)

#: 允许下发的可变项（L1 白名单）。**新增项必须先加到这里**，否则端侧会整包拒绝。
POLICY_KEYS: frozenset[str] = frozenset({
    "streamGuardChars",
    "escalateOn",
    "maxInputTokens",
    "maxOutputTokens",
    "maxTtftMs",
    "preferPlane",
    "modelTier",
    "promptPacks",
    "toolWhitelist",
    "retrievalMinScore",
})

#: 已登记但**本期不启用**的保留键：schema 里留着占位，端侧跳过、不读取。
RESERVED_KEYS: frozenset[str] = frozenset({"deviceProfiles"})

#: 签名密钥的环境变量名；未配置时用 jwt_secret 派生（本地开发不至于完全没签名）。
ENV_SECRET = "SEKB_EDGE_POLICY_SECRET"


class PolicyError(ValueError):
    """策略不合法（未知键 / 越界值 / 签名不符）。"""


def policy_secret(config: Any = None) -> str:
    """取签名密钥：环境变量优先，其次由 ``llm.jwt_secret`` 派生，最后是开发用默认值。

    派生而不是直接用 jwt_secret：两者用途不同，泄露一个不该连坐另一个。
    """
    env = os.environ.get(ENV_SECRET, "").strip()
    if env:
        return env
    base = ""
    try:
        base = str(getattr(config.llm, "jwt_secret", "") or "") if config is not None else ""
    except Exception:  # noqa: BLE001 - 配置结构变化不该让端点 500
        base = ""
    if base:
        return hashlib.sha256(f"edge-policy:{base}".encode()).hexdigest()
    logger.warning("未配置策略签名密钥，使用开发默认值（生产必须设 %s）", ENV_SECRET)
    return hashlib.sha256(b"edge-policy:dev-only").hexdigest()


def canonical(payload: dict[str, Any]) -> str:
    """规范化 JSON（键排序 + 紧凑分隔符）：签名与哈希都基于它，保证跨语言一致。"""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def validate_policy(policy: dict[str, Any]) -> None:
    """白名单校验：未知键**整包拒绝**；保留键（本期不启用）忽略但不报错。"""
    unknown = set(policy) - POLICY_KEYS - RESERVED_KEYS
    if unknown:
        raise PolicyError(f"策略包含未登记的可变项：{sorted(unknown)}（白名单外一律拒绝整包）")
    if "streamGuardChars" in policy:
        v = policy["streamGuardChars"]
        if not isinstance(v, int) or v < 0 or v > 400:
            raise PolicyError(f"streamGuardChars 越界：{v!r}（允许 0–400）")
    if "retrievalMinScore" in policy:
        v = policy["retrievalMinScore"]
        if not isinstance(v, (int, float)) or not 0.0 <= float(v) <= 1.0:
            raise PolicyError(f"retrievalMinScore 越界：{v!r}（允许 0–1）")
    if "maxOutputTokens" in policy:
        v = policy["maxOutputTokens"]
        if not isinstance(v, int) or not 1 <= v <= 32768:
            raise PolicyError(f"maxOutputTokens 越界：{v!r}")


def build_policy(
    config: Any,
    *,
    overrides: dict[str, Any] | None = None,
    rollout_percent: int = 100,
    rollout_salt: str | None = None,
    now: float | None = None,
    ttl_seconds: int = 24 * 3600,
) -> dict[str, Any]:
    """从当前配置生成一份策略包（含版本、有效期、空间戳、灰度与签名）。

    ``overrides`` 是本次要下发的可变项（通常来自配置或人工调参），会与"从配置推导的基线"合并；
    合并结果**先过白名单再签名**——顺序不能反，否则会签出一份自己都不该认的包。
    """
    base = _baseline_from_config(config)
    merged = {**base, **(overrides or {})}
    validate_policy(merged)

    version = int(hashlib.sha256(canonical(merged).encode()).hexdigest()[:8], 16)
    issued = int(now if now is not None else time.time())
    payload: dict[str, Any] = {
        "version": version,
        "issuedAt": issued,
        "expiresAt": issued + int(ttl_seconds),
        "embeddingSpace": EMBEDDING_SPACE,
        "rollout": {"percent": max(0, min(100, int(rollout_percent))),
                    "salt": rollout_salt or time.strftime("%Yw%W")},
        "policy": merged,
        # 预留：本期不实现设备适配类策略（端侧白名单校验会跳过它）
        "deviceProfiles": {},
    }
    payload["signature"] = sign_payload(payload, policy_secret(config))
    return payload


def sign_payload(payload: dict[str, Any], secret: str) -> str:
    """对 `version + embeddingSpace + rollout + policy + deviceProfiles + 有效期` 签名。"""
    signed = {
        k: payload[k]
        for k in ("version", "issuedAt", "expiresAt", "embeddingSpace", "rollout", "policy", "deviceProfiles")
        if k in payload
    }
    return hmac.new(secret.encode(), canonical(signed).encode(), hashlib.sha256).hexdigest()


def verify_payload(payload: dict[str, Any], secret: str) -> bool:
    """端侧同款校验（服务端也用它做自测/联调）：签名不符或结构缺失即 False。"""
    sig = payload.get("signature")
    if not isinstance(sig, str) or not sig:
        return False
    try:
        expected = sign_payload(payload, secret)
    except KeyError:
        return False
    return hmac.compare_digest(sig, expected)


def applies_to_device(payload: dict[str, Any], device_id: str) -> bool:
    """灰度判定：`hash(deviceId + salt) % 100 < percent`。

    用 sha256 前 8 位而不是内置 `hash()`：内置哈希跨进程/跨语言不稳定，
    同一个设备今天命中、明天不命中，灰度就失去意义。
    """
    rollout = payload.get("rollout") or {}
    percent = int(rollout.get("percent", 100))
    if percent >= 100:
        return True
    if percent <= 0:
        return False
    salt = str(rollout.get("salt", ""))
    digest = hashlib.sha256(f"{device_id}:{salt}".encode()).hexdigest()
    return int(digest[:8], 16) % 100 < percent


def _baseline_from_config(config: Any) -> dict[str, Any]:
    """从服务端现有端云配置推导基线（端侧拿到的应与服务端"同一口径"）。"""
    planes = getattr(getattr(config, "llm", None), "planes", None) if config is not None else None
    edge = getattr(planes, "edge", None) if planes else None
    routing = getattr(planes, "routing", None) if planes else None

    baseline: dict[str, Any] = {
        "streamGuardChars": int(getattr(routing, "stream_guard_chars", 60) or 0),
        "escalateOn": list(getattr(routing, "escalate_on", None) or [
            "json_invalid", "empty", "degenerate", "timeout", "low_confidence", "tool_hallucination",
        ]),
        "maxInputTokens": int(getattr(edge, "max_input_tokens", 2048) or 2048),
        # ⚠️ 端侧输出预算与服务端不同源：服务端的 300 是"长答案本来就该上云"的前提，
        # 端侧主功能就是聊天，默认 512（见 apps/shared 的 EdgeRuntimeConfig 注释）
        "maxOutputTokens": int(getattr(edge, "max_output_tokens", 512) or 512),
        "maxTtftMs": int(getattr(edge, "max_ttft_ms", 800) or 800),
        "preferPlane": str(getattr(routing, "prefer", "edge") or "edge"),
        "modelTier": dict(getattr(edge, "models", None) or {}),
        "toolWhitelist": list(getattr(routing, "available_tools", None) or []),
    }
    # 空值不出现在策略里：端侧对"缺失"的处理就是"用本地默认"，比下发空数组安全
    return {k: v for k, v in baseline.items() if v not in (None, "", [], {})}
