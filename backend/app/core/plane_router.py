"""端云**平面路由**与一致性内核（M1）。

对应设计：`docs/RFC-端云协同与端侧Agent.md` §4（路由）与 §4.5（一致性）。

这一层解决三个问题
------------------
1. **每次请求用哪个平面**（端侧 Ollama / 云端 DeepSeek）。`LLMConfig.base_url` 是全局单点，
   所以平面选择必须发生在**请求级**而不是配置级 —— 这就是 :class:`PlaneRouter` 的职责。
2. **什么时候该升级到云**（§4.2 的 6 类信号）。端侧小模型答不好时不能就这么交给用户；
   要在**同一请求内**自动升级，并把"为什么升级"记下来。
3. **切换不丢上下文**（§4.5-C/E）。升级不是"重来"：要把端侧已得结论压成交接摘要
   （handoff）注入云端请求，并带上**版本戳**，否则端云切换后模型"忘了前面"。

为什么做成门面而不是改热路径
----------------------------
`LLMFactory.ainvoke_with_stats` 是聊天主链路的热路径，直接改它会把回归风险扩散到全部
841 个测试。所以这里用 :class:`RoutedLLM` **包住**工厂：工厂只增加"按平面取实例"的能力，
决策/评估/升级/记账都在门面里，可独立测试。

阈值来自实测
------------
端侧预算（输入 2048 / 输出 300 / TTFT 800ms）默认值取自 2026-09-17 在 M5 Pro 的实测
（`scripts/edge_bench.py`）：qwen3.5-2b decode 86–116 tok/s、923 token 输入 TTFT 427ms；
云端 deepseek-flash decode 158–218 tok/s、TTFT 845–1306ms。
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import BaseMessage

from app.core.config import AppConfig
from app.core.logging import get_logger

logger = get_logger(__name__)

#: 平面名
PLANE_EDGE = "edge"
PLANE_CLOUD = "cloud"

#: 档位顺序（与 `scripts/make_local_profile.py` 的档位一致）
TIERS = ("short", "default", "quality")

#: 向量空间版本戳。SEKB 的 embedding 由 `app/core/embedding.py` 固定为
#: `BAAI/bge-small-zh-v1.5`（512 维）。跨端交换检索结果时必须带这个戳：
#: 维度不同的向量不可比较（这是「端侧粗检索→云端精排」成立的前提）。
EMBEDDING_SPACE = "BAAI/bge-small-zh-v1.5@512"

#: 各角色的**典型输出规模**（token）。刻意不用 `role.max_tokens`：那是"允许上限"，
#: 而预算判断要的是"这次大概会输出多少"。首次跑测试就暴露了这个错误——
#: supervisor 的上限是 500 > 端侧预算 300，于是"最该端侧跑的意图分类"被判去了云端。
DEFAULT_EXPECTED_OUTPUT: dict[str, int] = {
    "supervisor": 32,        # 只输出 {"intent": "..."}
    "critic": 64,            # 评分 JSON
    "critic_complex": 128,
    "rerank": 16,            # 排序结果
    "ragas": 64,
    "chat_simple": 64,       # 会话标题
    "scribe": 400,
    "planner": 600,          # 任务清单
    "executor": 800,         # 草稿答案
    "job_analysis": 1500,    # 职位分析多段
    "news_report": 6000,     # 长报告 → 必走云端
}

#: 判定「输出退化」的阈值
_DEGEN_MIN_LEN = 40          # 太短不判退化（短答案正常）
_DEGEN_REPEAT_RUN = 3        # 连续重复行数


@dataclass(frozen=True)
class Decision:
    """一次请求的平面决策（写进路由日志，用户可查"为什么走这边"）。"""

    plane: str
    reason: str
    tier: str = "default"
    input_tokens: int = 0
    output_budget: int = 0

    @property
    def is_edge(self) -> bool:
        return self.plane == PLANE_EDGE


@dataclass
class RouteEvent:
    """一条路由事件（可观测的最小单元；§4.4）。"""

    role: str
    plane: str
    reason: str
    model: str = ""
    tier: str = "default"
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    escalated: bool = False
    escalate_reason: str = ""
    signals: list[str] = field(default_factory=list)
    versions: dict[str, str] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        d = {
            "ts": self.ts, "role": self.role, "plane": self.plane, "reason": self.reason,
            "model": self.model, "tier": self.tier, "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens, "latency_ms": round(self.latency_ms, 1),
            "escalated": self.escalated, "escalate_reason": self.escalate_reason,
            "signals": self.signals, "versions": self.versions,
        }
        return d


# ============================================================
# 工具：token 估算、JSON 抽取、退化检测
# ============================================================

_CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff\u3040-\u30ff]")


def estimate_tokens(text: str) -> int:
    """粗估 token 数（不引第三方分词器，够用于"是否超预算"的判断）。

    口径：CJK 字符约 1 token/字；其余按 4 字符 1 token 折算。
    实测校验：923 token 的 prompt 本函数给出 890–980 区间（±10%），
    对"是否超过 2048 预算"这类判断足够。
    """
    if not text:
        return 0
    cjk = len(_CJK.findall(text))
    other = len(text) - cjk
    return cjk + max(other // 4, 0)


def estimate_messages_tokens(messages: list[Any]) -> int:
    """估算消息列表的总 token（含少量结构开销）。"""
    total = 0
    for m in messages or []:
        content = m if isinstance(m, str) else getattr(m, "content", "")
        if isinstance(content, list):                      # 多模态 content 分片
            content = "".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
        total += estimate_tokens(str(content))
    return total + 8 * len(messages or [])                 # 每条消息的结构开销


_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S)


def extract_json(text: str) -> Any:
    """从模型输出里抽出 JSON（容忍 ```json 围栏与前后废话）。失败抛 ``ValueError``。"""
    if not text:
        raise ValueError("空输出")
    m = _FENCE.search(text)
    candidate = m.group(1) if m else text
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    start, end = candidate.find("{"), candidate.rfind("}")
    if start >= 0 and end > start:
        return json.loads(candidate[start:end + 1])
    raise ValueError("未找到合法 JSON")


def looks_degenerate(text: str) -> bool:
    """检测退化输出（循环重复 / 单字符刷屏）——端侧小模型的典型失败模式之一。"""
    t = (text or "").strip()
    if len(t) < _DEGEN_MIN_LEN:
        return False
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    if len(lines) >= _DEGEN_REPEAT_RUN:
        run = 1
        for prev, cur in zip(lines, lines[1:]):
            run = run + 1 if cur == prev else 1
            if run >= _DEGEN_REPEAT_RUN:
                return True
    head = t[:200]
    if head and head.count(head[0]) / len(head) > 0.5:
        return True
    return False


def derive_tier(role: str, max_tokens: int, models: dict[str, str] | None = None) -> str:
    """推断角色档位：先看平面是否**按角色**配了模型，再按 ``max_tokens`` 推定。

    与 `scripts/make_local_profile.py` 的档位口径保持一致（那里是显式映射，
    这里是运行时兜底）。
    """
    models = models or {}
    if role in models:
        # 反向找出该角色落在哪个档
        for tier in TIERS:
            if models.get(tier) == models[role]:
                return tier
        return "default"
    if max_tokens and max_tokens <= 600:
        return "short"
    if max_tokens and max_tokens <= 2500:
        return "default"
    return "quality"


# ============================================================
# 路由
# ============================================================

class PlaneRouter:
    """按「任务结构 + 数据分级 + 预算」决定请求落在哪个平面，并在必要时升级。"""

    def __init__(self, config: AppConfig) -> None:
        planes = getattr(config.llm, "planes", None)
        self._edge = getattr(planes, "edge", None) if planes else None
        self._routing = getattr(planes, "routing", None) if planes else None
        self._roles = config.llm.roles

    # ---------- 可用性 ----------

    @property
    def enabled(self) -> bool:
        """是否启用端云路由（未配置端侧平面或未开开关时为 False → 单平面行为）。"""
        return bool(self._routing and self._routing.enabled and self._edge
                    and self._edge.base_url)

    @property
    def edge(self) -> Any:
        return self._edge

    # ---------- 决策 ----------

    def _expected_output(self, role: str, role_max: int) -> int:
        """预期输出规模：显式配置 > 内置默认表 > 角色上限（未知角色保守判云端）。"""
        configured = getattr(self._routing, "expected_output", None) if self._routing else None
        if configured and role in configured:
            return int(configured[role])
        return DEFAULT_EXPECTED_OUTPUT.get(role, role_max)

    def decide(
        self,
        role: str,
        messages: list[Any],
        *,
        max_output_tokens: int | None = None,
        device_data: bool = False,
    ) -> Decision:
        """决定这次请求走端侧还是云端。**理由一定写清楚**，因为它会进路由日志。

        ``max_output_tokens`` 语义是**这次预期会输出多少**，不是角色的允许上限；
        调用方知道任务结构时应当显式传入（如"只回一个 JSON"→ 32）。
        """
        role_cfg = self._roles.get(role)
        role_max = getattr(role_cfg, "max_tokens", 0) or 0
        out_budget = max_output_tokens or self._expected_output(role, role_max)
        est_in = estimate_messages_tokens(messages)
        tier = derive_tier(role, role_max, getattr(self._edge, "models", None) if self._edge else None)

        if not self.enabled:
            return Decision(PLANE_CLOUD, "routing_disabled", tier, est_in, out_budget)

        # 数据分级优先于一切：DEVICE_ONLY 数据永不出端（§5.2）
        device_only = set(getattr(self._routing, "device_only_roles", []) or [])
        if device_data or role in device_only:
            return Decision(PLANE_EDGE, "device_only_data", tier, est_in, out_budget)

        if est_in > self._edge.max_input_tokens:
            return Decision(PLANE_CLOUD,
                            f"input_over_edge_budget({est_in}>{self._edge.max_input_tokens})",
                            tier, est_in, out_budget)
        if out_budget > self._edge.max_output_tokens:
            return Decision(PLANE_CLOUD,
                            f"output_over_edge_budget({out_budget}>{self._edge.max_output_tokens})",
                            tier, est_in, out_budget)

        if getattr(self._routing, "prefer", "edge") == "cloud":
            return Decision(PLANE_CLOUD, "prefer_cloud", tier, est_in, out_budget)
        return Decision(PLANE_EDGE, "edge_preferred", tier, est_in, out_budget)

    # ---------- 调用后评估 ----------

    def evaluate(
        self,
        text: str,
        *,
        response_format: str | None = None,
        ttft_ms: float = 0.0,
        latency_ms: float = 0.0,
    ) -> list[str]:
        """调用后评估：返回触发的升级信号（可能多个）。"""
        signals: list[str] = []
        if not (text or "").strip():
            signals.append("empty")
        if response_format == "json":
            try:
                extract_json(text)
            except ValueError:
                signals.append("json_invalid")
        if looks_degenerate(text):
            signals.append("degenerate")
        # 首 token 超预算：实测端侧 TTFT 76–427ms，800ms 以上说明这一跳不适合端侧
        if self._edge and ttft_ms and ttft_ms > self._edge.max_ttft_ms:
            signals.append("timeout")
        return signals

    def should_escalate(self, signals: list[str]) -> bool:
        """命中的信号里有任何一个在 ``routing.escalate_on`` 里 → 升级。"""
        if not signals or not self._routing:
            return False
        allowed = set(self._routing.escalate_on or [])
        return bool(set(signals) & allowed)

    # ---------- 版本戳 ----------

    def versions(self, role: str) -> dict[str, str]:
        """跨端交换产物必须带的版本戳（§4.5-F）：不一致时**重算**而不是静默复用。"""
        role_cfg = self._roles.get(role)
        tier = derive_tier(role, getattr(role_cfg, "max_tokens", 0) or 0,
                           getattr(self._edge, "models", None) if self._edge else None)
        edge_model = ""
        if self._edge:
            edge_model = (self._edge.models or {}).get(role) or (self._edge.models or {}).get(tier, "")
        v = {
            "embedding_space": EMBEDDING_SPACE,
            "cloud_model": getattr(role_cfg, "model", "") or "",
            "edge_model": edge_model,
            "tier": tier,
            "tool_schema": _tool_schema_version(),
        }
        return v


def _tool_schema_version() -> str:
    """工具 schema 的版本戳：用内核 commit 表示（工具集随内核变化）。"""
    try:
        from app.core.kernel_info import kernel_info

        return str(kernel_info().get("commit", "unknown"))[:12]
    except Exception:  # noqa: BLE001 - 版本戳取不到不能影响主流程
        return "unknown"


# ============================================================
# 门面：决策 → 调用 → 评估 → 升级 → 记账
# ============================================================

class RoutedLLM:
    """在 :class:`~app.core.llm_factory.LLMFactory` 之上做端云路由。

    用法（上层只需换这一行）::

        routed = RoutedLLM(factory, config)
        resp, event = await routed.ainvoke("supervisor", messages)

    ``event.escalated`` 为真表示这次实际是**端侧失败后云端补的**；事件会落路由日志，
    端侧完成率与升级率就是从这些事件里算出来的（§4.4 的两个核心指标）。
    """

    def __init__(self, factory: Any, config: AppConfig, store: Any = None) -> None:
        self._factory = factory
        self._config = config
        self.router = PlaneRouter(config)
        self._store = store

    async def ainvoke(
        self,
        role: str,
        messages: list[BaseMessage] | list[Any],
        *,
        device_data: bool = False,
        max_output_tokens: int | None = None,
        **kwargs: Any,
    ) -> tuple[Any, RouteEvent]:
        """路由调用：先按决策调用；命中升级信号则**在同一请求内**改用云端重做一次。"""
        decision = self.router.decide(role, messages,
                                      max_output_tokens=max_output_tokens,
                                      device_data=device_data)
        role_cfg = self._config.llm.roles.get(role)
        fmt = getattr(role_cfg, "response_format", None)
        versions = self.router.versions(role)

        t0 = time.perf_counter()
        resp = await self._factory.ainvoke_with_stats(
            role, messages, plane=decision.plane if self.router.enabled else None, **kwargs)
        latency_ms = (time.perf_counter() - t0) * 1000
        text = getattr(resp, "content", "") or ""

        event = RouteEvent(
            role=role, plane=decision.plane, reason=decision.reason,
            model=self._factory.get_actual_model(
                role, decision.plane if self.router.enabled else None),
            tier=decision.tier, input_tokens=decision.input_tokens,
            latency_ms=latency_ms, versions=versions,
        )

        # 只有"落在端侧"的请求才需要评估是否升级（云端已经是兜底平面）
        if self.router.enabled and decision.is_edge:
            signals = self.router.evaluate(text, response_format=fmt, latency_ms=latency_ms)
            event.signals = signals
            if self.router.should_escalate(signals):
                event.escalate_reason = ",".join(signals)
                logger.warning("端侧结果触发升级，改用云端重做",
                               role=role, signals=signals, edge_model=event.model)
                t1 = time.perf_counter()
                resp = await self._factory.ainvoke_with_stats(role, messages, plane=PLANE_CLOUD, **kwargs)
                event.escalated = True
                event.plane = PLANE_CLOUD
                event.model = self._factory.get_actual_model(role, PLANE_CLOUD)
                event.latency_ms = (time.perf_counter() - t1) * 1000
                text = getattr(resp, "content", "") or ""

        event.output_tokens = estimate_tokens(text)
        if self._store is not None:
            await self._store.append(event)
        logger.info("端云路由决策", role=role, plane=event.plane, reason=event.reason,
                    escalated=event.escalated, signals=event.signals,
                    latency_ms=round(event.latency_ms, 1))
        return resp, event

    def handoff_note(self, event: RouteEvent, summary: str) -> str:
        """把交接摘要包成**已建立背景**（不是新指令），供端↔云切换时注入前缀。

        放在前缀位置还有一个副作用（正好是我们要的）：能命中提供方的 prompt 前缀缓存，
        切换后不必重算全历史（§4.5-E）。
        """
        return (
            f'<handoff from="{event.plane}" reason="{event.escalate_reason or event.reason}">\n'
            f"{summary.strip()}\n"
            f"</handoff>\n"
            "（以上是本次会话已建立的背景，请直接续接，不要重复已完成的工作。）"
        )
