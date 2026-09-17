#!/usr/bin/env python3
"""M1 端到端冒烟：真实走一遍"端云双平面 + 路由 + 升级 + 路由日志"。

与单元测试的分工
----------------
单元测试用假工厂验证**逻辑**；本脚本用**真实端点**验证**接线**：
本机 Ollama（端侧 qwen3.5）与云端 DeepSeek（需 `DEEPSEEK_API_KEY`）。

跑法
----
```bash
export DEEPSEEK_API_KEY=sk-xxx
cd backend && .venv/bin/python ../scripts/edge_m1_smoke.py
```

断言三件事（对应 M1 的验收口径）
--------------------------------
1. **短任务走端侧**：`supervisor`（预期输出 ~32 token）→ 端侧，且返回合法 JSON；
2. **长输出走云端**：`planner`（预期 ~600 token > 端侧预算 300）→ 云端；
3. **两个北极星指标有数**：端侧完成率 / 升级率 + 每个事件都带"为什么走这边"与版本戳。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.config import load_config
from app.core.llm_factory import LLMFactory
from app.core.plane_router import PLANE_EDGE, RoutedLLM
from app.storage.edge_route_storage import EdgeRouteStore
from langchain_core.messages import HumanMessage, SystemMessage

CONFIG = os.environ.get("SEKB_CONFIG_PATH", "config.edge-cloud.yaml")


async def main() -> int:
    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("⚠️  未设置 DEEPSEEK_API_KEY：本脚本需要它来验证云端平面")
        return 2
    cfg = load_config(CONFIG)
    store = EdgeRouteStore(Path(tempfile.mkdtemp()) / "routes.jsonl")
    routed = RoutedLLM(LLMFactory(cfg), cfg, store=store)

    print(f"配置档: {CONFIG}  |  端侧: {routed.router.edge.base_url}  |  路由启用: {routed.router.enabled}")
    print(f"端侧预算: 输入≤{routed.router.edge.max_input_tokens} / 输出≤{routed.router.edge.max_output_tokens} / TTFT≤{routed.router.edge.max_ttft_ms}ms\n")

    cases = [
        ("supervisor", "短任务（意图分类）→ 期望端侧",
         [SystemMessage(content="你是意图分类器，只输出 JSON。"),
          HumanMessage(content='把「最近 AI 芯片出货量增长很快」分类为 '
                               '[job_analysis, news, knowledge, chitchat]，只输出 {"intent":"..."}')]),
        ("planner", "长输出任务 → 期望云端",
         [SystemMessage(content="你是任务规划器，输出 JSON 任务清单。"),
          HumanMessage(content="把「写一篇端侧 Agent 的调研」拆成 3 步，输出 JSON。")]),
    ]

    for role, desc, msgs in cases:
        resp, ev = await routed.ainvoke(role, msgs)
        text = (getattr(resp, "content", "") or "").replace("\n", " ")[:80]
        print(f"── {desc}")
        print(f"   role={role}  平面={ev.plane}  原因={ev.reason}  模型={ev.model}")
        print(f"   升级={ev.escalated}({ev.escalate_reason or '—'})  信号={ev.signals or '—'}  "
              f"延迟={ev.latency_ms:.0f}ms  输出≈{ev.output_tokens}tok")
        print(f"   版本戳={ev.versions}")
        print(f"   返回={text!r}\n")

    s = store.stats()
    print("── 路由统计（两个北极星指标）")
    print(f"   事件总数={s['total']}  端侧决策={s['edge_decided']}  端侧完成={s['edge_completed']}  升级={s['escalated']}")
    print(f"   端侧完成率={s['edge_completion_rate']:.0%}  升级率={s['escalation_rate']:.0%}")
    print(f"   按平面={s['by_plane']}  按原因={s['by_reason']}")

    print("\n── 交接摘要示例（端↔云切换时注入，作为「已建立背景」而非新指令）")
    sample = next(e for e in store.recent(10) if e.get("role") == "supervisor")
    from app.core.plane_router import RouteEvent
    note = routed.handoff_note(RouteEvent(**{k: sample[k] for k in
                                             ("role", "plane", "reason", "escalate_reason")
                                             if k in sample}),
                               "已确认用户关注端侧推理；已完成意图分类；未完成：调研拆解")
    print("   " + note.replace("\n", "\n   "))

    # ---- 主链路：挂载路由后，**既有调用点不改一行**就自动路由 ----
    print("── 主链路（factory.attach_router 后，用普通 ainvoke_with_stats 调用）")
    factory = LLMFactory(cfg)
    store2 = EdgeRouteStore(Path(tempfile.mkdtemp()) / "routes2.jsonl")
    attached = factory.attach_router(store=store2)
    print(f"   路由已挂载: {attached}")

    # 预热：消除端侧 ~6.5s 冷启动（RFC §2.4）
    import time as _t

    t0 = _t.perf_counter()
    warmed = await factory.warmup_edge(("short",))
    print(f"   预热 {warmed}: {(_t.perf_counter() - t0) * 1000:.0f}ms")

    t1 = _t.perf_counter()
    await factory.ainvoke_with_stats(
        "supervisor",
        [SystemMessage(content="你是意图分类器，只输出 JSON。"),
         HumanMessage(content='把「端侧推理很省」分类为 [job_analysis, news, knowledge, chitchat]，'
                              '只输出 {"intent":"..."}')])
    ms = (_t.perf_counter() - t1) * 1000
    ev_main = factory.last_route_event["supervisor"]
    print(f"   主链路调用: 平面={ev_main.plane} 模型={ev_main.model} 延迟={ms:.0f}ms"
          f"（对比未预热的冷启动 6547ms）")

    ok = (s["edge_decided"] >= 1 and s["total"] == 2
          and ev_main.plane == PLANE_EDGE and ms < 3000)
    print("\n✅ M1 冒烟通过：短任务落端侧、长输出落云端、事件与指标齐全" if ok
          else "\n❌ M1 冒烟未达预期，请检查上面的路由原因")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
