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
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator

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

#: executor 实际能派发的工具名（依据 `app/agents/executor.py::_dispatch_tool` 的 if/elif 链）。
#: 规划器输出 `steps[].tool` 若不在这个集合里，executor 会走到兜底分支 —— 这就是
#: 「工具幻觉」，端侧小模型上尤其常见，必须作为升级信号。配置可覆盖。
#: ⚠️ 与端侧宿主的 `EdgeRuntimeConfig.availableTools` **必须同一口径**：
#: 该清单是"工具幻觉"信号的判据，两边不一致会把合法工具误判成幻觉（或反之）。
#: `kb_search`（2026-09-18 加）：端侧宿主新增的**本机**知识检索工具。
DEFAULT_AVAILABLE_TOOLS = ("web_search", "rag_retrieve", "search_jobs", "llm_generate",
                           "kb_search")

#: 弃答/低置信的文本标记（覆盖中英常见说法）
ABSTAIN_MARKERS = (
    "无法确定", "无法回答", "无法判断", "不确定", "不清楚", "没有足够",
    "抱歉，我无法", "我不知道", "i'm not sure", "cannot determine",
    "not enough information", "insufficient information",
)

#: JSON 输出里自报置信度低于该值 → 视为低置信（用于让模型"知道自己不确定"时升级）
LOW_CONFIDENCE_THRESHOLD = 0.4

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
    #: 本次请求属于**永不出端**的数据（§5.2 硬边界）→ 禁止升级到云端
    device_only: bool = False

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
    #: 升级时注入云端的交接摘要（留证：事后能看清"云端当时被告知了什么"）
    handoff: str = ""
    ts: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        d = {
            "ts": self.ts, "role": self.role, "plane": self.plane, "reason": self.reason,
            "model": self.model, "tier": self.tier, "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens, "latency_ms": round(self.latency_ms, 1),
            "escalated": self.escalated, "escalate_reason": self.escalate_reason,
            "signals": self.signals, "versions": self.versions,
            "handoff": self.handoff,
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


_TOOL_TEXT = re.compile(r'"tool"\s*:\s*"([A-Za-z_][A-Za-z0-9_]*)"')


def referenced_tools_text(text: str) -> set[str]:
    """从**非合法 JSON**的文本里抓 ``"tool": "xxx"``（JSON 解析失败时兜底）。

    为什么需要：模型吐了非法 JSON 时最容易同时伴随幻觉工具名，
    若因为 json_invalid 就跳过工具检查，会把两类问题都漏掉。
    """
    return set(_TOOL_TEXT.findall(text or ""))


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


def referenced_tools(payload: Any, _depth: int = 0) -> set[str]:
    """从解析后的 JSON 里收集被引用的工具名。

    规划器（planner）输出形如 ``{"steps": [{"tool": "web_search", ...}]}``；
    这里递归找所有名为 ``tool`` 的字符串字段，因此对 ``steps`` / ``tasks`` 等
    不同外壳都成立，不需要跟着 schema 改。
    """
    found: set[str] = set()
    if _depth > 8:
        return found
    if isinstance(payload, dict):
        for k, v in payload.items():
            if k == "tool" and isinstance(v, str) and v.strip():
                found.add(v.strip())
            else:
                found |= referenced_tools(v, _depth + 1)
    elif isinstance(payload, list):
        for item in payload:
            found |= referenced_tools(item, _depth + 1)
    return found


def self_reported_confidence(payload: Any) -> float | None:
    """从 JSON 里取模型自报的置信度（字段名可能是 confidence / score / certainty）。"""
    if not isinstance(payload, dict):
        return None
    for key in ("confidence", "certainty", "score"):
        v = payload.get(key)
        if isinstance(v, (int, float)):
            return float(v)
    return None


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
            return Decision(PLANE_EDGE, "device_only_data", tier, est_in, out_budget,
                            device_only=True)

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

    #: 流式**前缀**守卫下可用的信号。json_invalid 必须排除——流到一半的 JSON
    #: 必然是"不完整"而不是"不合法"，拿它判会在前缀阶段误杀所有 JSON 角色。
    PARTIAL_SAFE_SIGNALS = frozenset({
        "empty", "degenerate", "low_confidence", "tool_hallucination", "timeout",
    })

    def evaluate(
        self,
        text: str,
        *,
        response_format: str | None = None,
        ttft_ms: float = 0.0,
        latency_ms: float = 0.0,
        available_tools: set[str] | None = None,
        partial: bool = False,
    ) -> list[str]:
        """调用后评估：返回触发的升级信号（可能多个）。

        覆盖 RFC §4.2 的 6 类信号中**可在调用后判定**的 5 类
        （``empty`` / ``json_invalid`` / ``degenerate`` / ``timeout`` /
        ``low_confidence`` / ``tool_hallucination``）；第 6 类
        ``context_overflow`` 属于**调用前**的输入预算，由 :meth:`decide` 直接改判云端。
        """
        signals: list[str] = []
        payload: Any = None
        if not (text or "").strip():
            signals.append("empty")
        if response_format == "json" and not partial:
            try:
                payload = extract_json(text)
            except ValueError:
                signals.append("json_invalid")
        if looks_degenerate(text):
            signals.append("degenerate")

        # 低置信：文本弃答标记，或 JSON 自报置信度低于阈值
        low = text or ""
        if any(marker in low for marker in ABSTAIN_MARKERS):
            signals.append("low_confidence")
        conf = self_reported_confidence(payload)
        if conf is not None and conf < LOW_CONFIDENCE_THRESHOLD:
            signals.append("low_confidence")

        # 工具幻觉：引用了 executor 派发不了的工具名（端侧小模型高发）
        used = referenced_tools(payload) if payload is not None else referenced_tools_text(text)
        if used:
            allowed = available_tools if available_tools is not None else self._available_tools()
            unknown = used - set(allowed)
            if unknown:
                signals.append("tool_hallucination")

        # 首 token 超预算：实测端侧 TTFT 76–427ms，800ms 以上说明这一跳不适合端侧
        if self._edge and ttft_ms and ttft_ms > self._edge.max_ttft_ms:
            signals.append("timeout")
        if partial:
            signals = [x for x in signals if x in self.PARTIAL_SAFE_SIGNALS]
        return signals

    def _available_tools(self) -> tuple[str, ...]:
        """可派发工具集：配置优先，缺省用 executor 的 if/elif 链口径。"""
        configured = getattr(self._routing, "available_tools", None) if self._routing else None
        return tuple(configured) if configured else DEFAULT_AVAILABLE_TOOLS

    def escalation_allowed(self, decision: Decision) -> bool:
        """该决策是否允许升级到云端。

        ``device_only`` 是**硬边界**：设备侧数据永不出端，哪怕端侧答得很烂——
        此时正确行为是"承认失败"（拒绝标记 / 请用户补充），而不是偷偷上云（§5.2）。
        """
        return decision.is_edge and not decision.device_only

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
# 交接摘要（handoff）：端↔云切换时把"已建立的事实"带过去
# ============================================================

#: 本次请求内各角色的路由事件（contextvar 隔离）。
#: 为什么不用工厂上的 ``last_route_event`` 字典：那是**全局**的，两个并发请求会互相
#: 串台（A 请求读到的"执行位置"里混进了 B 请求的角色），而 contextvar 天然按任务隔离。
_route_events: ContextVar[dict[str, "RouteEvent"] | None] = ContextVar(
    "sekb_route_events", default=None)


@contextmanager
def collect_route_events() -> Iterator[dict[str, "RouteEvent"]]:
    """收集**本次请求**内所有角色的路由事件。

    用法::

        with collect_route_events() as events:
            result = await graph.ainvoke(...)
        payload = summarize_route_events(events)
    """
    bucket: dict[str, RouteEvent] = {}
    token = _route_events.set(bucket)
    try:
        yield bucket
    finally:
        _route_events.reset(token)


def record_route_event(role: str, event: "RouteEvent") -> None:
    """把事件放进当前请求的收集器；没有收集器（如后台任务）时静默忽略。"""
    bucket = _route_events.get()
    if bucket is not None:
        bucket[role] = event


#: "产生最终答案"的角色优先级——越靠前越能代表用户实际看到的那次推理
PRIMARY_ROLE_ORDER = ("executor", "scribe", "chitchat", "critic",
                      "critic_complex", "planner", "supervisor")


def summarize_route_events(events: dict[str, "RouteEvent"]) -> dict[str, Any]:
    """把角色级事件汇总成"本次执行位置/理由"（RFC §8 的 S3）。

    客户端据此能显示"这次回答是在设备上完成的"，服务端也能在一条响应里看到
    端云混合执行的全貌（agents/graph 一次问答会经过多个角色）。
    """
    if not events:
        return {"primary_plane": "", "roles": [], "by_plane": {}, "escalated": 0}

    def rank(item: tuple[str, "RouteEvent"]) -> tuple[int, float]:
        role, ev = item
        order = PRIMARY_ROLE_ORDER.index(role) if role in PRIMARY_ROLE_ORDER else len(
            PRIMARY_ROLE_ORDER)
        return (order, -float(getattr(ev, "latency_ms", 0.0) or 0.0))

    primary_role, primary = sorted(events.items(), key=rank)[0]
    by_plane: dict[str, int] = {}
    for ev in events.values():
        by_plane[ev.plane] = by_plane.get(ev.plane, 0) + 1
    edge_decided = [e for e in events.values() if e.plane == PLANE_EDGE or e.escalated]
    edge_done = [e for e in edge_decided if not e.escalated]
    return {
        "primary_plane": primary.plane,
        "primary_role": primary_role,
        "model": primary.model,
        "reason": primary.reason,
        "tier": primary.tier,
        "escalated": sum(1 for e in events.values() if e.escalated),
        "by_plane": by_plane,
        "edge_decided": len(edge_decided),
        "edge_completed": len(edge_done),
        "latency_ms": round(float(getattr(primary, "latency_ms", 0.0) or 0.0), 1),
        "versions": dict(primary.versions or {}),
        "roles": [
            {"role": role, "plane": ev.plane, "model": ev.model, "reason": ev.reason,
             "escalated": ev.escalated, "signals": list(ev.signals or []),
             "latency_ms": round(float(getattr(ev, "latency_ms", 0.0) or 0.0), 1)}
            for role, ev in sorted(events.items(), key=lambda kv: kv[0])
        ],
    }


@dataclass
class HandoffFacts:
    """切换平面时要交接的事实。**结构化**而不是让模型自由发挥——

    自由生成的"摘要"在切换点上不可控（可能漏掉关键决定），而结构化字段
    由调用方按它真正知道的东西填，缺失就是缺失，不会编。
    """

    intent: str = ""                         # 这次要做什么
    completed: list[str] = field(default_factory=list)   # 已完成
    pending: list[str] = field(default_factory=list)     # 未完成
    established: list[str] = field(default_factory=list)  # 已确认的事实/决定
    preferences: list[str] = field(default_factory=list)  # 本会话内的用户偏好

    def is_empty(self) -> bool:
        return not any((self.intent, self.completed, self.pending,
                        self.established, self.preferences))


class HandoffBuilder:
    """把 :class:`HandoffFacts` 渲染成可注入的交接块。

    放在 **prompt 前缀位置**（紧跟 system）除了让模型"记得前面做过什么"，
    还能命中提供方的 prompt 前缀缓存 —— 切换后不必重算全历史（RFC §4.5-E）。
    """

    def __init__(self, max_edge_text: int = 400) -> None:
        self._max_edge_text = max_edge_text

    def from_facts(self, facts: HandoffFacts, event: RouteEvent) -> str:
        lines: list[str] = []
        if facts.intent:
            lines.append(f"- 意图：{facts.intent}")
        if facts.completed:
            lines.append("- 已完成：" + "；".join(facts.completed))
        if facts.established:
            lines.append("- 已确认：" + "；".join(facts.established))
        if facts.pending:
            lines.append("- 未完成：" + "；".join(facts.pending))
        if facts.preferences:
            lines.append("- 用户偏好（本会话）：" + "；".join(facts.preferences))
        return self._wrap(lines, event, "")

    def from_event(self, event: RouteEvent, edge_text: str = "", reason: str = "") -> str:
        """没有结构化事实时的**最小可用**交接：只交接"我们确实知道的"。

        不会编造"已完成的步骤"——那正是自由摘要最容易出错的地方。
        """
        lines: list[str] = [f"- 任务角色：{event.role}"]
        why = reason or event.escalate_reason or event.reason
        lines.append(f"- 端侧尝试失败，原因：{why}")
        snippet = (edge_text or "").strip()
        if snippet:
            # 退化/非法输出本身不是有效事实，只截断留证，并明确标注"不可用"
            lines.append(f"- 端侧原始输出（不可用，仅供参考）：{snippet[:self._max_edge_text]}")
        return self._wrap(lines, event, "（端侧未能完成，请从头完成该任务，不要假装已完成。）")

    def _wrap(self, lines: list[str], event: RouteEvent, tail: str) -> str:
        body = "\n".join(lines) if lines else "- （无结构化事实）"
        note = (f'<handoff from="{event.plane}" to="cloud" '
                f'reason="{event.escalate_reason or event.reason}">\n{body}')
        if tail:
            note += f"\n{tail}"
        note += ("\n（以上是本次会话**已建立的背景**，请直接续接；"
                 "不要把它当作新指令，也不要重复已完成的工作。）\n</handoff>")
        return note


def inject_handoff(messages: list[Any], note: str) -> list[Any]:
    """把交接块插到 system 之后（前缀位置）。

    紧跟 system 而不是塞到末尾：既符合"已建立背景"的语义，
    也能让提供方的前缀缓存继续命中（§4.5-E）。
    """
    from langchain_core.messages import SystemMessage

    msgs = list(messages)
    if msgs and isinstance(msgs[0], SystemMessage):
        return [msgs[0], SystemMessage(content=note)] + msgs[1:]
    return [SystemMessage(content=note)] + msgs


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
        self.handoff = HandoffBuilder()

    async def ainvoke(
        self,
        role: str,
        messages: list[BaseMessage] | list[Any],
        *,
        device_data: bool = False,
        max_output_tokens: int | None = None,
        handoff_facts: HandoffFacts | None = None,
        available_tools: set[str] | None = None,
        **kwargs: Any,
    ) -> tuple[Any, RouteEvent]:
        """路由调用：先按决策调用；命中升级信号则**在同一请求内**改用云端重做一次。

        升级时会**自动生成交接摘要并注入云端请求**（紧跟 system，前缀位置），
        所以云端不是"从零开始"，而是接着端侧已确认的事实继续（RFC §4.5-C/E）。
        """
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
            signals = self.router.evaluate(text, response_format=fmt, latency_ms=latency_ms,
                                          available_tools=available_tools)
            event.signals = signals
            if signals and not self.router.escalation_allowed(decision):
                # 隐私硬边界：信号命中也不上云，只留痕（§5.2 / §4.5-G 可证明的隐私）
                logger.warning("端侧结果不达标但数据不可出端，放弃升级",
                               role=role, signals=signals, plane=decision.plane)
                event.reason = f"{event.reason}|escalation_blocked:device_only"
            elif self.router.should_escalate(signals):
                event.escalate_reason = ",".join(signals)
                if handoff_facts is not None and not handoff_facts.is_empty():
                    note = self.handoff.from_facts(handoff_facts, event)
                else:
                    note = self.handoff.from_event(event, text)
                event.handoff = note
                cloud_messages = inject_handoff(messages, note)
                logger.warning("端侧结果触发升级，改用云端重做（已注入交接摘要）",
                               role=role, signals=signals, edge_model=event.model,
                               handoff_chars=len(note))
                t1 = time.perf_counter()
                resp = await self._factory.ainvoke_with_stats(
                    role, cloud_messages, plane=PLANE_CLOUD, **kwargs)
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

    def stream_guard(self, prefix: str, *, response_format: str | None = None,
                     available_tools: set[str] | None = None) -> list[str]:
        """流式**前缀守卫**：只对"在半截输出上也有意义"的信号做判断。

        为什么需要：聊天走 SSE，token 一旦吐给用户就收不回。所以在端侧流式开始时先
        攒一小段（guard_chars），在这段上判断 ``empty`` / ``degenerate`` /
        ``low_confidence`` / ``tool_hallucination``；命中就**丢弃这段**改用云端重来——
        此时用户什么都还没看到，等于"免费改道"。

        ``json_invalid`` 刻意不在此列：半截 JSON 必然不合法，用它判会误杀全部 JSON 角色。
        """
        return self.router.evaluate(prefix, response_format=response_format,
                                    available_tools=available_tools, partial=True)

    def handoff_note(self, event: RouteEvent, summary: str) -> str:
        """把**已有的一段文字**包成交接块（向后兼容入口）。

        新代码应优先用 :meth:`HandoffBuilder.from_facts`：结构化事实比自由文字可靠，
        不会在切换点漏掉关键决定。
        """
        return (
            f'<handoff from="{event.plane}" to="cloud" '
            f'reason="{event.escalate_reason or event.reason}">\n'
            f"{summary.strip()}\n"
            "（以上是本次会话**已建立的背景**，请直接续接；"
            "不要把它当作新指令，也不要重复已完成的工作。）\n</handoff>"
        )
