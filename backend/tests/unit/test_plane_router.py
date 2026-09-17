"""端云平面路由与一致性内核的单元测试（M1）。

覆盖三块：
1. **决策矩阵**（`PlaneRouter.decide`）——用 M5 Pro 实测出来的预算阈值断言，而不是拍脑袋的常量；
2. **升级信号**（`evaluate` / `should_escalate`）——6 类里可自动判定的 4 类；
3. **两个北极星指标**（`EdgeRouteStore.stats`）——端侧完成率 / 升级率，
   特别验证"升级过的事件必须算作端侧决策过"（否则会把升级统计成成功，口径就反了）。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import edge as edge_route
from app.core.access import require_full_access
from app.core.auth import get_current_user
from app.core.config import (
    LLMConfig,
    LLMRoleConfig,
    PlaneEndpointConfig,
    PlanesConfig,
    RoutingConfig,
)
from app.core.plane_router import (
    EMBEDDING_SPACE,
    PLANE_CLOUD,
    PLANE_EDGE,
    PlaneRouter,
    RoutedLLM,
    RouteEvent,
    derive_tier,
    estimate_tokens,
    extract_json,
    looks_degenerate,
)
from app.storage.edge_route_storage import EdgeRouteStore

EDGE_URL = "http://127.0.0.1:11434/v1"


def make_config(*, routing: bool = True, prefer: str = "edge",
                device_only: list[str] | None = None,
                roles: dict[str, LLMRoleConfig] | None = None) -> SimpleNamespace:
    """构造一个够用的配置对象（不读真实 config.yaml，避免与生产配置耦合）。"""
    roles = roles or {
        "supervisor": LLMRoleConfig(model="deepseek-flash", temperature=0.1,
                                    max_tokens=500, response_format="json"),
        "planner": LLMRoleConfig(model="deepseek-flash", max_tokens=2000),
        "news_report": LLMRoleConfig(model="deepseek-flash", max_tokens=8000),
    }
    llm = LLMConfig(
        api_key="sk-test", base_url="https://api.deepseek.com/v1", roles=roles,
        planes=PlanesConfig(
            edge=PlaneEndpointConfig(
                base_url=EDGE_URL,
                models={"short": "qwen3.5-2b", "default": "qwen3.5-4b", "quality": "qwen3.5-9b"},
            ),
            routing=RoutingConfig(enabled=routing, prefer=prefer,
                                  device_only_roles=device_only or []),
        ),
    )
    # cost_control 供 LLMFactory 记账时读取（本测试不真的调用 LLM，但构造时要齐备）
    cost = SimpleNamespace(pricing={"deepseek-flash": SimpleNamespace(input=0.001, output=0.002)},
                           usd_to_cny=7.2)
    return SimpleNamespace(llm=llm, cost_control=cost)


# ============================================================
# 决策矩阵
# ============================================================

class TestDecide:
    def test_routing_disabled_falls_back_to_cloud(self):
        """未启用路由 → 一律云端（等价于改造前的单平面行为）。"""
        r = PlaneRouter(make_config(routing=False))
        assert r.enabled is False
        assert r.decide("supervisor", ["hi"]).plane == PLANE_CLOUD
        assert r.decide("supervisor", ["hi"]).reason == "routing_disabled"

    def test_no_edge_plane_means_disabled(self):
        """配了 routing.enabled 但没配 edge 端点 → 仍然不算启用（避免误以为在端上跑）。"""
        cfg = make_config()
        cfg.llm.planes.edge = None
        assert PlaneRouter(cfg).enabled is False

    def test_short_task_goes_edge(self):
        """短进短出（supervisor 500 token 预算）→ 端侧，这正是实测中端侧完胜的场景。"""
        d = PlaneRouter(make_config()).decide(
            "supervisor", [SimpleNamespace(content="把这句话分类，只输出 JSON")])
        assert d.plane == PLANE_EDGE
        assert d.reason == "edge_preferred"
        assert d.tier == "short"

    def test_output_over_budget_goes_cloud(self):
        """输出预算超端侧上限（300）→ 云端：实测 2B 生成 500 token 约 4.3s。"""
        d = PlaneRouter(make_config()).decide("planner", ["写一份长报告"])
        assert d.plane == PLANE_CLOUD
        assert d.reason.startswith("output_over_edge_budget")

    def test_input_over_budget_goes_cloud(self):
        """输入超端侧预算（2048）→ 云端。"""
        long_text = "端侧推理受内存带宽限制。" * 400          # 远超 2048 token
        d = PlaneRouter(make_config()).decide("supervisor", [long_text])
        assert d.plane == PLANE_CLOUD
        assert d.reason.startswith("input_over_edge_budget")

    def test_device_only_role_never_leaves_device(self):
        """数据分级优先于一切：DEVICE_ONLY 角色即使超预算也必须留端侧。"""
        r = PlaneRouter(make_config(device_only=["planner"]))
        d = r.decide("planner", ["写一份长报告"])            # 本会因超预算走云
        assert d.plane == PLANE_EDGE
        assert d.reason == "device_only_data"

    def test_device_only_flag_overrides_everything(self):
        d = PlaneRouter(make_config()).decide("news_report", ["x"], device_data=True)
        assert d.plane == PLANE_EDGE and d.reason == "device_only_data"

    def test_prefer_cloud(self):
        d = PlaneRouter(make_config(prefer="cloud")).decide("supervisor", ["短问题"])
        assert d.plane == PLANE_CLOUD and d.reason == "prefer_cloud"

    def test_expectation_not_ceiling_decides(self):
        """回归：**用"预期输出"而不是"允许上限"判预算**。

        supervisor 的上限是 500（> 端侧预算 300），但它实际只输出 ~30 token 的 JSON。
        早期实现拿上限当预估，导致"最该端侧跑的意图分类"被判去云端 —— 这条测试锁住该行为。
        """
        r = PlaneRouter(make_config())
        assert r.decide("supervisor", ["分类"]) .plane == PLANE_EDGE
        assert r.decide("news_report", ["写报告"]).plane == PLANE_CLOUD
        # 同一角色：显式给出"这次会输出很多"时应当改判云端
        assert r.decide("supervisor", ["分类"], max_output_tokens=2000).plane == PLANE_CLOUD

    def test_expected_output_can_be_overridden_by_config(self):
        cfg = make_config()
        cfg.llm.planes.routing.expected_output = {"supervisor": 5000}
        d = PlaneRouter(cfg).decide("supervisor", ["分类"])
        assert d.plane == PLANE_CLOUD and d.reason.startswith("output_over_edge_budget")

    @pytest.mark.parametrize("role,expect", [
        ("supervisor", "short"),      # max_tokens 500
        ("planner", "default"),       # 2000
        ("news_report", "quality"),   # 8000
    ])
    def test_tier_derivation(self, role, expect):
        cfg = make_config()
        rc = cfg.llm.roles[role]
        assert derive_tier(role, rc.max_tokens, cfg.llm.planes.edge.models) == expect


class TestEdgeModelWiring:
    """端侧平面的**接线**：角色 → 档位 → 本地模型名。

    背景：M1 首次端到端冒烟时，端侧把云端的模型名（deepseek-flash）发给了 Ollama，
    换来 "model not found" —— 因为单元测试用的是假工厂，盖不到这层映射。
    这条测试直接检查工厂解析出的实际模型名，防止再退化。
    """

    def test_edge_plane_resolves_tiered_model(self):
        from app.core.llm_factory import LLMFactory

        f = LLMFactory(make_config())
        f.get("supervisor", "edge")          # 只创建客户端对象，不发请求
        f.get("news_report", "edge")
        assert f.get_actual_model("supervisor", "edge") == "qwen3.5-2b"
        assert f.get_actual_model("news_report", "edge") == "qwen3.5-9b"
        # 云端平面仍旧用角色配置里的云端模型名，不被端侧映射污染
        f.get("supervisor", "cloud")
        assert f.get_actual_model("supervisor", "cloud") == "deepseek-flash"

    def _capture_create_kwargs(self, monkeypatch, cfg) -> dict:
        """捕获 `_create_llm` 传给客户端的 kwargs。

        `ChatOpenAI` 是在 `_create_llm` 内部按需导入的，所以要打桩**源模块**
        （`langchain_openai.ChatOpenAI`），而不是某个模块级名字。
        """
        captured: dict = {}

        class _Spy:
            def __init__(self, **kw):
                captured.update(kw)

        monkeypatch.setattr("langchain_openai.ChatOpenAI", _Spy)
        return captured

    def test_edge_plane_disables_thinking_by_default(self, monkeypatch):
        """端侧平面默认关思考：走 `reasoning_effort="none"`。

        实测同一意图分类任务：开思考 2283ms / 236 token，关思考 89ms / 7 token（**25 倍差**）。
        直接传 `think=False` 会被 OpenAI 客户端判为非法参数，故必须用这个字段。
        """
        from app.core.llm_factory import LLMFactory

        cfg = make_config()
        captured = self._capture_create_kwargs(monkeypatch, cfg)
        LLMFactory(cfg)._create_llm(cfg.llm.roles["supervisor"], plane="edge")
        assert captured.get("reasoning_effort") == "none"
        assert captured.get("model") == "qwen3.5-2b"

    def test_edge_plane_keeps_thinking_when_flag_off(self, monkeypatch):
        """显式关闭该优化 → 不注入 `reasoning_effort`（留给需要长推理的档位）。"""
        from app.core.llm_factory import LLMFactory

        cfg = make_config()
        cfg.llm.planes.edge.disable_thinking = False
        captured = self._capture_create_kwargs(monkeypatch, cfg)
        LLMFactory(cfg)._create_llm(cfg.llm.roles["supervisor"], plane="edge")
        assert "reasoning_effort" not in captured

    def test_cloud_plane_never_gets_edge_thinking_flag(self, monkeypatch):
        """云端平面不受端侧"关思考"影响（云端自己的模型行为由云端决定）。"""
        from app.core.llm_factory import LLMFactory

        cfg = make_config()
        captured = self._capture_create_kwargs(monkeypatch, cfg)
        LLMFactory(cfg)._create_llm(cfg.llm.roles["supervisor"], plane="cloud")
        assert "reasoning_effort" not in captured
        assert captured.get("base_url") == "https://api.deepseek.com/v1"

    def test_edge_plane_requires_endpoint(self):
        """没配端侧端点却要端侧实例 → 明确报错，而不是悄悄走云端。"""
        from app.core.exceptions import LLMError
        from app.core.llm_factory import LLMFactory

        cfg = make_config()
        cfg.llm.planes.edge = None
        with pytest.raises(LLMError):
            LLMFactory(cfg).get("supervisor", "edge")


# ============================================================
# 升级信号
# ============================================================

class TestEscalation:
    def setup_method(self):
        self.r = PlaneRouter(make_config())

    def test_empty_output_triggers(self):
        assert "empty" in self.r.evaluate("   ")

    def test_invalid_json_triggers(self):
        sig = self.r.evaluate("我觉得应该是 news 吧", response_format="json")
        assert "json_invalid" in sig

    def test_fenced_json_is_accepted(self):
        """带 ```json 围栏的合法 JSON 不应误判（端侧模型很爱加围栏）。"""
        sig = self.r.evaluate('```json\n{"intent":"news"}\n```', response_format="json")
        assert "json_invalid" not in sig

    def test_degenerate_repetition_triggers(self):
        text = "\n".join(["这一行是重复的"] * 5) * 2
        assert "degenerate" in self.r.evaluate(text)

    def test_ttft_over_budget_triggers_timeout(self):
        assert "timeout" in self.r.evaluate("正常答案", ttft_ms=1500)

    def test_normal_answer_has_no_signal(self):
        assert self.r.evaluate("端侧推理受内存带宽限制，KV cache 随上下文增长。",
                               response_format=None) == []

    def test_should_escalate_respects_config(self):
        assert self.r.should_escalate(["json_invalid"]) is True
        assert self.r.should_escalate([]) is False
        assert self.r.should_escalate(["some_unknown_signal"]) is False

    def test_low_confidence_from_abstention_marker(self):
        """模型自述"无法确定" → 低置信 → 该升级让云端试。"""
        assert "low_confidence" in self.r.evaluate("抱歉，我无法确定这份 JD 的薪资范围。")

    def test_low_confidence_from_json_self_report(self):
        """JSON 里自报 confidence=0.2 → 低置信（模型"知道自己不确定"时最有价值）。"""
        sig = self.r.evaluate('{"answer":"可能是支付方向","confidence":0.2}',
                              response_format="json")
        assert "low_confidence" in sig

    def test_high_confidence_json_does_not_trigger(self):
        sig = self.r.evaluate('{"answer":"支付方向","confidence":0.9}', response_format="json")
        assert "low_confidence" not in sig

    def test_tool_hallucination_in_valid_json(self):
        """规划器引用了 executor 派发不了的工具名 → 工具幻觉（端侧小模型高发）。"""
        sig = self.r.evaluate('{"steps":[{"tool":"web_search_plus","tool_input":{}}]}',
                              response_format="json")
        assert "tool_hallucination" in sig

    def test_known_tools_pass(self):
        sig = self.r.evaluate('{"steps":[{"tool":"web_search"},{"tool":"rag_retrieve"}]}',
                              response_format="json")
        assert "tool_hallucination" not in sig

    def test_tool_hallucination_also_caught_when_json_is_broken(self):
        """非法 JSON 也最容易同时伴随幻觉工具名——不能因为 json_invalid 就跳过工具检查。"""
        sig = self.r.evaluate('{"steps":[{"tool":"magic_tool",', response_format="json")
        assert "json_invalid" in sig and "tool_hallucination" in sig

    def test_no_tool_mention_no_signal(self):
        assert self.r.evaluate('{"intent":"news"}', response_format="json") == []

    def test_six_signal_coverage_is_declared(self):
        """目标要求"6 类升级信号"：这里显式锁住 5 类调用后信号 + 1 类调用前（输入预算）。

        前 5 类由 evaluate 判定；context_overflow 在 decide 阶段直接改判云端
        （长输入不该由端侧硬扛，见 test_input_over_budget_goes_cloud）。
        """
        after_call = {"empty", "json_invalid", "degenerate", "timeout",
                      "low_confidence", "tool_hallucination"}
        cfg = make_config()
        assert set(cfg.llm.planes.routing.escalate_on) == after_call
        decisions = {PlaneRouter(cfg).decide("supervisor", [txt]).reason.split("(")[0]
                     for txt in ("短问题", "端侧推理受内存带宽限制。" * 400)}
        assert "input_over_edge_budget" in decisions      # 第 6 类：调用前改判

    def test_extract_json_variants(self):
        assert extract_json('{"a":1}') == {"a": 1}
        assert extract_json('前言{"a":2}后语') == {"a": 2}
        with pytest.raises(ValueError):
            extract_json("没有 JSON")


# ============================================================
# 工具函数
# ============================================================

class TestHelpers:
    def test_estimate_tokens_cjk_heavier_than_ascii(self):
        assert estimate_tokens("中文十个字符测试一下") > estimate_tokens("abcdefghij")

    def test_estimate_tokens_empty(self):
        assert estimate_tokens("") == 0

    def test_looks_degenerate_ignores_short_text(self):
        assert looks_degenerate("好的") is False

    def test_versions_contain_four_stamps(self):
        v = PlaneRouter(make_config()).versions("supervisor")
        assert v["embedding_space"] == EMBEDDING_SPACE
        assert v["edge_model"] == "qwen3.5-2b"
        assert v["cloud_model"] == "deepseek-flash"
        assert "tool_schema" in v


# ============================================================
# 路由事件流水与两个北极星指标
# ============================================================

class TestRouteStore:
    @pytest.mark.asyncio
    async def test_append_recent_and_idempotency(self, tmp_path):
        store = EdgeRouteStore(tmp_path / "routes.jsonl")
        ev = RouteEvent(role="supervisor", plane=PLANE_EDGE, reason="edge_preferred")
        assert await store.append(ev, event_id="e1") is True
        assert await store.append(ev, event_id="e1") is False      # 幂等：重传不入库
        assert len(store.recent(10)) == 1

    @pytest.mark.asyncio
    async def test_rates_count_escalated_as_edge_decided(self, tmp_path):
        """口径验证：升级过的事件必须算进"端侧决策过"的分母。

        否则"端侧失败升级"会被统计成"端侧完成"，端侧完成率虚高——这是最容易搞反的地方。
        """
        store = EdgeRouteStore(tmp_path / "routes.jsonl")
        # 端侧成功 3 条
        for i in range(3):
            await store.append(RouteEvent(role="supervisor", plane=PLANE_EDGE,
                                          reason="edge_preferred"), event_id=f"ok{i}")
        # 端侧升级 1 条（升级后 plane 已被改写成 cloud）
        await store.append(RouteEvent(role="supervisor", plane=PLANE_CLOUD,
                                      reason="edge_preferred", escalated=True,
                                      escalate_reason="json_invalid"), event_id="esc1")
        # 一开始就判给云端 2 条（不该进端侧分母）
        for i in range(2):
            await store.append(RouteEvent(role="news_report", plane=PLANE_CLOUD,
                                          reason="output_over_edge_budget"), event_id=f"c{i}")

        s = store.stats()
        assert s["total"] == 6
        assert s["edge_decided"] == 4                 # 3 成功 + 1 升级
        assert s["edge_completed"] == 3
        assert s["escalated"] == 1
        assert s["edge_completion_rate"] == 0.75      # 3/4
        assert s["escalation_rate"] == 0.25           # 1/4
        assert s["escalate_reasons"] == {"json_invalid": 1}

    def test_stats_empty_store(self, tmp_path):
        s = EdgeRouteStore(tmp_path / "none.jsonl").stats()
        assert s["total"] == 0 and s["edge_completion_rate"] == 0.0

    def test_broken_lines_do_not_poison_stats(self, tmp_path):
        """半行/坏行必须被跳过，而不是让整个统计崩掉。"""
        p = tmp_path / "routes.jsonl"
        p.write_text('{"plane":"edge","role":"a"}\n这不是JSON\n\n', encoding="utf-8")
        s = EdgeRouteStore(p).stats()
        assert s["total"] == 1


# ============================================================
# API 端点
# ============================================================

@pytest.fixture
def client(tmp_path, monkeypatch):
    app = FastAPI()
    app.include_router(edge_route.router)
    app.dependency_overrides[get_current_user] = lambda: "u-test"
    app.dependency_overrides[require_full_access] = lambda: None
    monkeypatch.setattr(edge_route, "_store", EdgeRouteStore(tmp_path / "routes.jsonl"))
    return TestClient(app)


class TestEdgeApi:
    def test_report_event_is_idempotent(self, client):
        body = {"event_id": "d1", "role": "supervisor", "plane": "edge",
                "reason": "edge_preferred", "latency_ms": 222}
        first = client.post("/api/v1/edge/route-events", json=body)
        second = client.post("/api/v1/edge/route-events", json=body)
        assert first.status_code == 200 and first.json()["duplicated"] is False
        assert second.json()["duplicated"] is True
        assert client.get("/api/v1/edge/routes").json()["count"] == 1

    def test_reject_bad_plane(self, client):
        r = client.post("/api/v1/edge/route-events",
                        json={"event_id": "x", "role": "a", "plane": "somewhere"})
        assert r.status_code == 422

    def test_stats_endpoint_reports_both_rates(self, client):
        client.post("/api/v1/edge/route-events",
                    json={"event_id": "a", "role": "supervisor", "plane": "edge"})
        client.post("/api/v1/edge/route-events",
                    json={"event_id": "b", "role": "supervisor", "plane": "cloud",
                          "escalated": True, "escalate_reason": "empty"})
        s = client.get("/api/v1/edge/routes/stats").json()
        assert s["edge_completion_rate"] == 0.5
        assert s["escalation_rate"] == 0.5


# ============================================================
# 门面：升级路径（用假工厂，避免真的调 LLM）
# ============================================================

class _FakeResp:
    def __init__(self, content: str):
        self.content = content


class _FakeFactory:
    """按平面返回预设结果；记录调用顺序，用于断言"端侧失败 → 云端重做"。"""

    def __init__(self, edge_text: str, cloud_text: str = '{"intent":"news"}'):
        self.edge_text, self.cloud_text = edge_text, cloud_text
        self.calls: list[str] = []

    async def ainvoke_with_stats(self, role, messages, plane=None, **kw):
        self.calls.append(plane or "cloud")
        return _FakeResp(self.edge_text if plane == "edge" else self.cloud_text)

    def get_actual_model(self, role, plane=None):
        return {"edge": "qwen3.5-2b", "cloud": "deepseek-flash"}.get(plane or "cloud", "")


class TestRoutedLLM:
    @pytest.mark.asyncio
    async def test_edge_success_does_not_call_cloud(self, tmp_path):
        cfg = make_config()
        f = _FakeFactory(edge_text='{"intent":"news"}')
        routed = RoutedLLM(f, cfg, store=EdgeRouteStore(tmp_path / "r.jsonl"))
        _, ev = await routed.ainvoke("supervisor", ["分类这句话"])
        assert f.calls == ["edge"]
        assert ev.escalated is False and ev.plane == PLANE_EDGE

    @pytest.mark.asyncio
    async def test_invalid_json_escalates_to_cloud(self, tmp_path):
        """端侧吐了非法 JSON → 同一请求内改用云端重做，并记录升级原因。"""
        cfg = make_config()
        f = _FakeFactory(edge_text="大概是 news？")
        store = EdgeRouteStore(tmp_path / "r.jsonl")
        routed = RoutedLLM(f, cfg, store=store)
        resp, ev = await routed.ainvoke("supervisor", ["分类这句话"])
        assert f.calls == ["edge", "cloud"]           # 先端侧、后云端
        assert ev.escalated is True
        assert ev.plane == PLANE_CLOUD
        assert "json_invalid" in ev.escalate_reason
        assert getattr(resp, "content") == '{"intent":"news"}'
        assert store.stats()["escalation_rate"] == 1.0

    @pytest.mark.asyncio
    async def test_cloud_decided_request_never_escalates(self, tmp_path):
        """一开始就判给云端的请求不再二次升级（云端已是兜底平面）。"""
        cfg = make_config()
        f = _FakeFactory(edge_text="", cloud_text="ok")
        routed = RoutedLLM(f, cfg, store=EdgeRouteStore(tmp_path / "r.jsonl"))
        _, ev = await routed.ainvoke("news_report", ["写长报告"])
        assert f.calls == ["cloud"] and ev.escalated is False

    @pytest.mark.asyncio
    async def test_escalation_injects_handoff_into_cloud_request(self, tmp_path):
        """升级时必须把交接摘要**注入云端请求**（紧跟 system），否则云端等于从零开始。

        这是 RFC §4.5-C/E 的核心：不是"包装一段文字"，而是真的带过去。
        """
        from langchain_core.messages import HumanMessage, SystemMessage

        from app.core.plane_router import HandoffFacts

        cfg = make_config()

        class _CapturingFactory(_FakeFactory):
            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                self.captured: dict[str, list] = {}

            async def ainvoke_with_stats(self, role, messages, plane=None, **kw):
                self.captured[plane or "cloud"] = list(messages)
                return await super().ainvoke_with_stats(role, messages, plane=plane, **kw)

        f = _CapturingFactory(edge_text="我猜是 news 吧")      # 非法 JSON → 触发升级
        store = EdgeRouteStore(tmp_path / "r.jsonl")
        routed = RoutedLLM(f, cfg, store=store)
        facts = HandoffFacts(intent="给这份 JD 分类",
                             established=["用户在看支付方向"],
                             pending=["输出合法 JSON"])
        await routed.ainvoke("supervisor",
                             [SystemMessage(content="你是意图分类器。"),
                              HumanMessage(content="分类这句话")],
                             handoff_facts=facts)

        cloud_msgs = f.captured["cloud"]
        assert isinstance(cloud_msgs[0], SystemMessage)         # 原 system 仍在最前
        assert isinstance(cloud_msgs[1], SystemMessage)         # 交接块在 system 之后（前缀位）
        note = cloud_msgs[1].content
        assert "<handoff" in note and 'to="cloud"' in note
        assert "用户在看支付方向" in note and "输出合法 JSON" in note
        assert "已建立的背景" in note                            # 明确"是背景不是新指令"
        # 事件里留证：事后能看清"云端当时被告知了什么"
        ev = store.recent(1)[0]
        assert ev["handoff"] and "<handoff" in ev["handoff"]

    @pytest.mark.asyncio
    async def test_minimal_handoff_does_not_fabricate_completed_steps(self, tmp_path):
        """没有结构化事实时只交接"确实知道的"，并明确叫云端从头做——不编"已完成"。"""
        cfg = make_config()
        routed = RoutedLLM(_FakeFactory("x"), cfg)
        ev = RouteEvent(role="planner", plane=PLANE_EDGE, reason="edge_preferred",
                        escalate_reason="json_invalid")
        note = routed.handoff.from_event(ev, edge_text="{'steps':[")
        assert "任务角色：planner" in note
        assert "端侧未能完成" in note and "不要假装已完成" in note
        assert "已完成的步骤" not in note

    @pytest.mark.asyncio
    async def test_handoff_note_marks_background_not_instruction(self, tmp_path):
        cfg = make_config()
        routed = RoutedLLM(_FakeFactory("x"), cfg)
        note = routed.handoff_note(
            RouteEvent(role="supervisor", plane=PLANE_EDGE, reason="edge_preferred",
                       escalate_reason="timeout"), "已确认用户关注支付方向；未完成：市场分析")
        assert "<handoff" in note and 'reason="timeout"' in note
        assert "不要重复" in note                     # 明确"是背景不是新任务"
        assert "市场分析" in note


# ============================================================
# 主链路接入：工厂挂载路由后，**既有调用点不改一行**就自动路由
# ============================================================

class _FakeChatModel:
    """假的 LangChain 模型：只实现 ainvoke / astream，不出网。"""

    def __init__(self, text: str = '{"intent":"news"}'):
        self.text = text
        self.calls = 0

    async def ainvoke(self, messages, **kw):
        self.calls += 1
        return _FakeResp(self.text)

    async def astream(self, messages, **kw):
        self.calls += 1
        for piece in ("端侧", "回答"):
            yield _FakeResp(piece)


class TestMainPathRouting:
    """目标：证明「挂载即生效」——agents/graph 里那十余处 `ainvoke_with_stats(role, msgs)`
    调用**无需任何改动**就会经过端云路由（这正是 M1 的核心价值）。"""

    def _factory(self, monkeypatch, text: str = '{"intent":"news"}'):
        from app.core.llm_factory import LLMFactory

        f = LLMFactory(make_config())
        created: dict[str, _FakeChatModel] = {}

        def fake_create(role_config, plane=None, model_override=None):
            m = _FakeChatModel(text)
            created[f"{role_config.model}@{plane}"] = m
            return m

        monkeypatch.setattr(f, "_create_llm", fake_create)
        return f, created

    @pytest.mark.asyncio
    async def test_unmounted_factory_keeps_legacy_behaviour(self, monkeypatch):
        """没挂路由 → 走主配置单平面，不产生路由事件（改造前后完全一致）。"""
        f, _ = self._factory(monkeypatch)
        assert f.routing_enabled is False
        await f.ainvoke_with_stats("supervisor", ["分类"])
        assert f.last_route_event == {}

    @pytest.mark.asyncio
    async def test_mounted_factory_routes_short_task_to_edge(self, monkeypatch, tmp_path):
        """挂载后：同一个 `ainvoke_with_stats(role, msgs)` 调用 → 自动落端侧并记事件。"""
        f, created = self._factory(monkeypatch)
        assert f.attach_router(store=EdgeRouteStore(tmp_path / "r.jsonl")) is True
        assert f.routing_enabled is True

        await f.ainvoke_with_stats("supervisor", ["把这句话分类"])

        ev = f.last_route_event["supervisor"]
        assert ev.plane == PLANE_EDGE and ev.reason == "edge_preferred"
        assert ev.model == "qwen3.5-2b"                     # 端侧档位模型已解析
        assert "edge" in " ".join(created)                  # 确实在端侧平面建的实例

    @pytest.mark.asyncio
    async def test_mounted_factory_routes_long_output_to_cloud(self, monkeypatch, tmp_path):
        f, created = self._factory(monkeypatch)
        f.attach_router(store=EdgeRouteStore(tmp_path / "r.jsonl"))
        await f.ainvoke_with_stats("news_report", ["写日报"])
        ev = f.last_route_event["news_report"]
        assert ev.plane == PLANE_CLOUD and ev.reason.startswith("output_over_edge_budget")

    @pytest.mark.asyncio
    async def test_main_path_escalates_and_records(self, monkeypatch, tmp_path):
        """主链路里的端侧失败 → 自动升级到云，并在路由日志里留下升级原因。"""
        store = EdgeRouteStore(tmp_path / "r.jsonl")
        f, _ = self._factory(monkeypatch, text="我猜是 news 吧")   # 非法 JSON
        f.attach_router(store=store)
        await f.ainvoke_with_stats("supervisor", ["分类"])
        ev = f.last_route_event["supervisor"]
        assert ev.escalated is True and "json_invalid" in ev.escalate_reason
        assert store.stats()["escalation_rate"] == 1.0

    @pytest.mark.asyncio
    async def test_main_path_auto_generates_handoff_on_escalation(self, monkeypatch, tmp_path):
        """主链路升级时**自动**带交接摘要——调用方不需要知道 handoff 的存在。

        这是 ③ 的验收点：既有的十余处 `ainvoke_with_stats(role, msgs)` 调用点
        在端侧失败升级时，云端请求里会自动出现交接块。
        """
        from app.core.llm_factory import LLMFactory

        seen: list[tuple[str, list]] = []

        class _RecordingModel:
            """记录"哪个平面收到了哪批消息"，并固定返回非法 JSON（触发升级）。"""

            def __init__(self, plane: str):
                self.plane = plane

            async def ainvoke(self, messages, **kw):
                seen.append((self.plane, list(messages)))
                return _FakeResp('{"intent":"news"}' if self.plane == "cloud" else "我猜是 news")

        f = LLMFactory(make_config())
        monkeypatch.setattr(f, "_create_llm",
                            lambda role_config, plane=None, model_override=None:
                            _RecordingModel(plane or "cloud"))
        f.attach_router(store=EdgeRouteStore(tmp_path / "r.jsonl"))

        # 主链路调用：**不带 plane、不提 handoff**，就是普通的一行
        await f.ainvoke_with_stats("supervisor", ["把这句话分类"])

        planes = [p for p, _ in seen]
        assert planes == ["edge", "cloud"], f"应先端侧后云端，实际 {planes}"
        cloud_msgs = seen[1][1]
        assert any("<handoff" in str(getattr(m, "content", m)) for m in cloud_msgs)
        # 事件里留证
        assert f.last_route_event["supervisor"].handoff

    @pytest.mark.asyncio
    async def test_attach_router_is_noop_without_planes(self, monkeypatch):
        """未配置 planes → attach 返回 False，主链路保持单平面（生产环境即如此）。"""
        from app.core.llm_factory import LLMFactory

        cfg = make_config()
        cfg.llm.planes = None
        f = LLMFactory(cfg)
        assert f.attach_router() is False
        assert await f.warmup_edge() == []                  # 未挂路由时预热是空操作

    @pytest.mark.asyncio
    async def test_stream_path_decides_but_does_not_escalate(self, monkeypatch, tmp_path):
        """端侧流式的**短输出**：攒不满 guard_chars 就流完了 → 等价于全量评估后放行。

        这条覆盖的是"M1 之前的行为"在新实现下依然成立：正常短答案不会被前缀守卫误伤。
        """
        store = EdgeRouteStore(tmp_path / "r.jsonl")
        f, _ = self._factory(monkeypatch)
        f.attach_router(store=store)
        got = [c async for c in f.astream_with_stats("supervisor", ["流式问题"])]
        assert got == ["端侧", "回答"]
        ev = f.last_route_event["supervisor"]
        assert ev.plane == PLANE_EDGE
        assert "stream_guard" in ev.reason
        assert store.stats()["escalation_rate"] == 0.0


class TestStreamPrefixGuard:
    """流式前缀守卫：端侧 token 一旦吐给用户就收不回，所以先攒一小段再决定是否改道。

    验收点：改道发生在**用户看到任何字符之前** —— 端侧那段退化输出必须一个字符都不外泄。
    """

    def _factory(self, monkeypatch, *, edge_text, cloud_text, device_only=None):
        from app.core.llm_factory import LLMFactory

        f = LLMFactory(make_config(device_only=device_only))
        seen: list[tuple[str, list]] = []

        class _PlaneModel:
            def __init__(self, plane: str):
                self.plane = plane

            async def astream(self, messages, **kw):
                seen.append((self.plane, list(messages)))
                text = edge_text if self.plane == "edge" else cloud_text
                for i in range(0, len(text), 50):
                    yield _FakeResp(text[i:i + 50])

            async def ainvoke(self, messages, **kw):
                seen.append((self.plane, list(messages)))
                return _FakeResp(edge_text if self.plane == "edge" else cloud_text)

        monkeypatch.setattr(f, "_create_llm",
                            lambda role_config, plane=None, model_override=None:
                            _PlaneModel(plane or "cloud"))
        return f, seen

    @pytest.mark.asyncio
    async def test_degenerate_prefix_reroutes_before_any_token_is_shown(
            self, monkeypatch, tmp_path):
        """前缀退化 → 丢弃该前缀、改走云端，用户只看到云端的答案（端侧 0 字符外泄）。"""
        f, seen = self._factory(monkeypatch, edge_text="。" * 300,
                                cloud_text="这是云端的正确答案。")
        store = EdgeRouteStore(tmp_path / "r.jsonl")
        f.attach_router(store=store)

        got = [c async for c in f.astream_with_stats("supervisor", ["流式问题"])]
        joined = "".join(got)

        assert joined == "这是云端的正确答案。", "用户只应看到云端答案，端侧前缀 0 字符外泄"
        assert "。。。" not in joined, "端侧的退化前缀绝不能吐给用户"
        assert [p for p, _ in seen] == ["edge", "cloud"]
        ev = f.last_route_event["supervisor"]
        assert ev.plane == PLANE_CLOUD
        assert ev.reason == "stream_prefix_guard(degenerate)"

    @pytest.mark.asyncio
    async def test_reroute_injects_handoff_into_cloud_stream(self, monkeypatch, tmp_path):
        """改道时云端不是"从零开始"：交接摘要自动注入云端请求。"""
        f, seen = self._factory(monkeypatch, edge_text="。" * 300, cloud_text="云端答案")
        f.attach_router(store=EdgeRouteStore(tmp_path / "r.jsonl"))
        _ = [c async for c in f.astream_with_stats("supervisor", ["流式问题"])]
        cloud_msgs = [m for p, m in seen if p == "cloud"][0]
        blob = " ".join(str(getattr(m, "content", m)) for m in cloud_msgs)
        assert "<handoff" in blob
        assert "degenerate" in blob, "升级原因要写进交接块，云端才知道端侧发生了什么"

    @pytest.mark.asyncio
    async def test_device_only_never_reroutes_even_if_degenerate(
            self, monkeypatch, tmp_path):
        """隐私硬边界：DEVICE_ONLY 角色即使输出退化也不许改道云端（宁可承认失败）。"""
        f, seen = self._factory(monkeypatch, edge_text="。" * 300, cloud_text="云端答案",
                                device_only=["supervisor"])
        f.attach_router(store=EdgeRouteStore(tmp_path / "r.jsonl"))
        got = [c async for c in f.astream_with_stats("supervisor", ["含设备数据的流式问题"])]
        assert "".join(got).startswith("。")            # 端侧原样吐给用户
        assert {p for p, _ in seen} == {"edge"}, "任何情况下都不该出现云端调用"
        assert "stream_no_escalate" in f.last_route_event["supervisor"].reason

    @pytest.mark.asyncio
    async def test_guard_disabled_by_zero_falls_back_to_decide_only(
            self, monkeypatch, tmp_path):
        """`stream_guard_chars: 0` → 退回"只决策不升级"，退化前缀也照原样外泄（显式关闭）。"""
        f, seen = self._factory(monkeypatch, edge_text="。" * 300, cloud_text="云端答案")
        f.config.llm.planes.routing.stream_guard_chars = 0
        f.attach_router(store=EdgeRouteStore(tmp_path / "r.jsonl"))
        got = [c async for c in f.astream_with_stats("supervisor", ["流式问题"])]
        assert "".join(got).startswith("。")
        assert {p for p, _ in seen} == {"edge"}


class TestDeviceOnlyHardBoundary:
    """非流式路径同样必须守边界：device_only 数据永不出端（RFC §5.2）。"""

    @pytest.mark.asyncio
    async def test_invoke_does_not_escalate_device_only_data(self, monkeypatch, tmp_path):
        """端侧答成非法 JSON，但数据不可出端 → 放弃升级并留痕，而不是偷偷上云。"""
        store = EdgeRouteStore(tmp_path / "r.jsonl")
        f, created = TestMainPathRouting()._factory(monkeypatch, text="我猜是 news 吧")
        f.attach_router(store=store)
        f.config.llm.planes.routing.device_only_roles = ["supervisor"]

        await f.ainvoke_with_stats("supervisor", ["分类含设备数据的句子"])

        assert [k for k in created if "@edge" in k], "应仍在端侧执行"
        assert not [k for k in created if "@cloud" in k], "device_only 不该出现云端实例"
        ev = f.last_route_event["supervisor"]
        assert ev.escalated is False
        assert "escalation_blocked:device_only" in ev.reason
        assert store.stats()["escalation_rate"] == 0.0

    def test_escalation_allowed_matrix(self):
        from app.core.plane_router import Decision, PlaneRouter

        router = PlaneRouter(make_config())
        assert router.escalation_allowed(Decision(PLANE_EDGE, "edge_preferred")) is True
        assert router.escalation_allowed(
            Decision(PLANE_EDGE, "device_only_data", device_only=True)) is False
        assert router.escalation_allowed(Decision(PLANE_CLOUD, "prefer_cloud")) is False


class TestPartialEvaluation:
    """前缀评估必须只看"在半截输出上也有意义"的信号。"""

    def _router(self):
        from app.core.plane_router import PlaneRouter

        return PlaneRouter(make_config())

    def test_partial_prefix_does_not_flag_incomplete_json(self):
        """半截 JSON 必然不合法 → 用它判会误杀**所有** JSON 角色，故 partial 下排除。"""
        r = self._router()
        prefix = '{"intent": "news", "conf'
        assert "json_invalid" not in r.evaluate(prefix, response_format="json", partial=True)
        assert "json_invalid" in r.evaluate(prefix, response_format="json")

    def test_partial_still_catches_empty_low_confidence_and_degenerate(self):
        r = self._router()
        assert "empty" in r.evaluate("   ", partial=True)
        assert "degenerate" in r.evaluate("。" * 200, partial=True)
        assert "low_confidence" in r.evaluate("我不确定，可能无法回答这个问题。", partial=True)

    def test_partial_ignores_tool_names_not_yet_streamed(self):
        """半截输出里工具名可能还没出现 → 不能因此判幻觉（假阴性优于误杀）。"""
        r = self._router()
        tools = {"web_search", "rag_retrieve"}
        assert "tool_hallucination" not in r.evaluate(
            '{"tool": "web', response_format="json", available_tools=tools, partial=True)


def test_generated_dual_profile_documents_guard_and_all_signals():
    """生成的端云档必须与代码默认值一致（否则运维按文档调参会调出差异行为）。"""
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    out = subprocess.run(["python3", str(root / "scripts" / "make_local_profile.py"), "--check"],
                         capture_output=True, text=True, cwd=root)
    assert out.returncode == 0, out.stderr or out.stdout
    text = (root / "backend" / "config.edge-cloud.yaml").read_text(encoding="utf-8")
    for sig in ("json_invalid", "empty", "degenerate", "timeout",
                "low_confidence", "tool_hallucination"):
        assert sig in text, f"端云档缺升级信号 {sig}"
    assert "stream_guard_chars" in text


def test_stats_json_serializable(tmp_path):
    """统计要能直接进 HTTP 响应（不能带不可序列化对象）。"""
    s = EdgeRouteStore(tmp_path / "r.jsonl").stats()
    json.dumps(s, ensure_ascii=False)


# ============================================================
# S3：本次执行位置/理由（客户端可见性）
# ============================================================

class TestExecutionSummary:
    """`summarize_route_events` 是 S3 的唯一事实来源，必须与 pydantic 模型对得上。"""

    def _ev(self, role, plane, *, model="m", reason="edge_preferred", escalated=False,
            signals=None, latency_ms=1.0, versions=None):
        from app.core.plane_router import RouteEvent

        return RouteEvent(role=role, plane=plane, model=model, reason=reason,
                          escalated=escalated, signals=signals or [],
                          latency_ms=latency_ms, versions=versions or {"tier": "short"})

    def test_empty_when_no_events(self):
        from app.core.plane_router import summarize_route_events

        assert summarize_route_events({})["primary_plane"] == ""

    def test_primary_prefers_answer_producing_role(self):
        """executor 比 supervisor 更能代表"用户看到的那次推理"。"""
        from app.core.plane_router import summarize_route_events

        got = summarize_route_events({
            "supervisor": self._ev("supervisor", PLANE_EDGE, model="qwen3.5-2b"),
            "executor": self._ev("executor", PLANE_CLOUD, model="deepseek-flash",
                                 reason="output_over_edge_budget(600>300)"),
        })
        assert got["primary_plane"] == PLANE_CLOUD
        assert got["primary_role"] == "executor"
        assert got["model"] == "deepseek-flash"
        assert got["reason"].startswith("output_over_edge_budget")

    def test_counts_by_plane_and_escalation(self):
        from app.core.plane_router import summarize_route_events

        got = summarize_route_events({
            "supervisor": self._ev("supervisor", PLANE_EDGE),
            "planner": self._ev("planner", PLANE_CLOUD, escalated=True),
            "executor": self._ev("executor", PLANE_CLOUD, escalated=True),
        })
        assert got["by_plane"] == {PLANE_EDGE: 1, PLANE_CLOUD: 1} or \
               got["by_plane"] == {PLANE_EDGE: 1, PLANE_CLOUD: 2}
        assert got["escalated"] == 2
        # 端侧完成率的分母只算"判给端侧或发生过升级"的角色
        assert got["edge_decided"] == 3
        assert got["edge_completed"] == 1

    def test_roles_detail_is_sorted_and_carries_signals(self):
        from app.core.plane_router import summarize_route_events

        got = summarize_route_events({
            "supervisor": self._ev("supervisor", PLANE_EDGE),
            "planner": self._ev("planner", PLANE_CLOUD, escalated=True,
                                signals=["degenerate"]),
        })
        assert [r["role"] for r in got["roles"]] == ["planner", "supervisor"]
        planner = got["roles"][0]
        assert planner["escalated"] is True and planner["signals"] == ["degenerate"]

    def test_summary_fits_execution_info_model(self):
        """汇总结果必须能直接喂给 `ChatResponse.execution`（防字段漂移）。"""
        from app.api.routes.chat import ExecutionInfo
        from app.core.plane_router import summarize_route_events

        got = summarize_route_events({"executor": self._ev("executor", PLANE_EDGE)})
        info = ExecutionInfo(**got)
        assert info.primary_plane == PLANE_EDGE and info.versions == {"tier": "short"}


class TestRouteEventIsolation:
    """并发请求不得互相串台：这是"响应里的执行位置"可信的前提。"""

    @pytest.mark.asyncio
    async def test_two_collectors_do_not_mix(self):
        import asyncio

        from app.core.plane_router import (
            collect_route_events,
            record_route_event,
            summarize_route_events,
        )

        async def worker(role: str, plane: str, gate: asyncio.Event):
            with collect_route_events() as bucket:
                await gate.wait()
                record_route_event(role, RouteEvent(role=role, plane=plane,
                                                     reason="edge_preferred", model="m"))
                await asyncio.sleep(0)
                return summarize_route_events(bucket)

        gate = asyncio.Event()
        t1 = asyncio.create_task(worker("supervisor", PLANE_EDGE, gate))
        t2 = asyncio.create_task(worker("executor", PLANE_CLOUD, gate))
        gate.set()
        a, b = await asyncio.gather(t1, t2)
        assert a["primary_role"] == "supervisor" and a["by_plane"] == {PLANE_EDGE: 1}
        assert b["primary_role"] == "executor" and b["by_plane"] == {PLANE_CLOUD: 1}

    @pytest.mark.asyncio
    async def test_recording_without_collector_is_noop(self):
        from app.core.plane_router import record_route_event

        record_route_event("supervisor", RouteEvent(role="s", plane=PLANE_EDGE,
                                                    reason="edge_preferred", model="m"))

    @pytest.mark.asyncio
    async def test_main_path_publishes_into_collector(self, monkeypatch, tmp_path):
        """主链路：`ainvoke_with_stats` 在收集器打开时必须把事件投进去。"""
        from app.core.plane_router import collect_route_events, summarize_route_events

        f, _ = TestMainPathRouting()._factory(monkeypatch)
        f.attach_router(store=EdgeRouteStore(tmp_path / "r.jsonl"))
        with collect_route_events() as bucket:
            await f.ainvoke_with_stats("supervisor", ["分类"])
        got = summarize_route_events(bucket)
        assert got["primary_role"] == "supervisor"
        assert got["primary_plane"] == PLANE_EDGE
        assert got["model"] == "qwen3.5-2b"


class TestStreamChunkNormalization:
    """多模态 chunk 的 content 可能是 list——流式出口必须是 str，否则 SSE 会吐出数组。"""

    @pytest.mark.asyncio
    async def test_list_content_is_flattened(self, monkeypatch, tmp_path):
        from app.core.llm_factory import LLMFactory

        class _MultiModal:
            async def astream(self, messages, **kw):
                yield _FakeResp(["端侧", "推理"])
                yield _FakeResp("很省电")

        f = LLMFactory(make_config())
        monkeypatch.setattr(f, "_create_llm",
                            lambda role_config, plane=None, model_override=None: _MultiModal())
        f.attach_router(store=EdgeRouteStore(tmp_path / "r.jsonl"))
        got = [c async for c in f.astream_with_stats("supervisor", ["q"])]
        assert got == ["端侧推理", "很省电"]
        assert all(isinstance(x, str) for x in got)

    def test_chunk_text_fallback_for_plain_values(self):
        from app.core.llm_factory import LLMFactory

        assert LLMFactory._chunk_text(_FakeResp("文本")) == "文本"
        assert LLMFactory._chunk_text(_FakeResp(["a", "b"])) == "ab"
