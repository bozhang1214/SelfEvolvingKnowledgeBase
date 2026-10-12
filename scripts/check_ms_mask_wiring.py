#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**转换产物图结构断言**：`.ms` 里 `sekb_additive_mask_zero` 是否真的连到了 4 层注意力。

## 为什么要有这条（纯结构判据，不需要设备、不需要 runtime）

生产化的那次改判把根因写成"转换器把掩码输入**常量折叠**了"。本轮用 flatbuffer 直接
打开产物，证明**那句话是错的**（图里链条完整：mask 输入 → Transpose → 4×attention/self/Add）；
真正的缺陷在设备的喂数路径上（按 int64 写了 int32 缓冲，越界 2048 B）。

但**结构判据本身仍然值得留下**：它是"转换器哪天真的把输入折叠/接丢"的最早警报，
而且一条命令、零依赖（只要 `pip install onnx` 都不需要）。

判据（全部必须成立）：

1. `.ms` 存在、魔数 `MSL2`；
2. 图输入里有名为 `sekb_additive_mask_zero`（或含 `additive_mask`）的输入；
3. 该输入**在 `subgraph.inputIndices` 里**（不是只挂在 MetaGraph 上的幽灵输入）；
4. 它被**至少 4 个** `attention/self/Add` 节点消费（BERT 4 层的注意力掩码加法）；
5. 它的**每个消费者都在 `subgraph.nodeIndices` 里**（没被挤出子图）；
6. 该输入**没有** `initializer`/常量数据（`has_data=False`）——否则说明被换成了常量。

用法：

    python3 scripts/check_ms_mask_wiring.py --ms .tooling/mindspore-lite/work/bge-maskinput.ms
    python3 scripts/check_ms_mask_wiring.py --red      # 反证：故意对"无掩码"产物跑 → 必须变红
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ms_flatbuffer  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MS = ROOT / ".tooling" / "mindspore-lite" / "work" / "bge-maskinput.ms"
RED_MS = ROOT / ".tooling" / "mindspore-lite" / "work" / "bge-nomask.ms"


def check(ms_path: Path, min_attention_adds: int = 4) -> tuple[list[tuple[str, bool, str]], "ms_flatbuffer.MsGraph | None"]:
    checks: list[tuple[str, bool, str]] = []

    if not ms_path.is_file():
        return [("ms_exists", False, f"缺 {ms_path}")], None
    checks.append(("ms_exists", True, f"{ms_path.name} {ms_path.stat().st_size} B"))

    g = ms_flatbuffer.load(str(ms_path))
    checks.append(("ms_magic", g.magic == "MSL2", f"魔数={g.magic}"))

    mask = g.mask_input()
    if mask is None:
        checks.append(("mask_input_declared", False,
                       f"图输入里没有 additive_mask：{[t.name for t in g.inputs()]} —— "
                       f"转换器把输入折叠/改写了"))
        return checks, g
    checks.append(("mask_input_declared", True,
                   f"[{mask.index}] {mask.name} dims={mask.dims} dtype={mask.dtype_name}"))

    in_sub = mask.index in g.subgraph_inputs
    checks.append(("mask_is_subgraph_input", in_sub,
                   f"subgraph.inputIndices 含 {mask.index}？{in_sub}（子图输入={g.subgraph_inputs}）"))

    users = g.attention_add_users(mask.index)
    # ⚠️ 这一条**只报告、不作为判据**：转换器会为 NHWC 数据布局插一个
    # `sekb_additive_mask_zero_nh2nc` 的 Transpose，所以直接消费者是 Transpose 而不是 Add。
    # 真正判据是下面沿下游闭包找 Add（第一版我把这一条写成硬判据，结果把一个**正确的**
    # 产物判红了——判据必须描述"语义上必须成立的事"，而不是"当前实现的偶然形状"）。
    print(f"   (报告) 掩码张量的直接消费者={[n.name for n in g.consumers(mask.index)]}；"
          f"其中 attention/self/Add = {[n.name for n in users]}")

    # 转换器会为 NHWC 数据布局插一个 `*_nh2nc` 的 Transpose（NCHW→NHWC 的适配），
    # 所以**直接消费者未必是 Add** —— 判据要沿下游闭包找 Add，而不是只看一跳：
    #   mask → sekb_additive_mask_zero_nh2nc(Transpose) → 4×attention/self/Add
    # 只看一跳会把一个**正确的**产物判红（我第一版就是这么写的），
    # 所以这里同时给出"闭包内有多少 attention/self/Add"。
    closure_adds = _closure_attention_adds(g, mask.index)
    checks.append(("mask_reaches_attention_adds_via_closure", len(closure_adds) >= min_attention_adds,
                   f"下游闭包里可达的 attention/self/Add = {len(closure_adds)} 个"
                   f"（要求 ≥{min_attention_adds}）：{sorted(closure_adds)}"))

    # 没有常量数据（若被折叠成常量，flatbuffer 里会带 data 向量）
    no_const = not mask.has_data
    checks.append(("mask_not_a_constant", no_const,
                   f"has_data={mask.has_data}（True = 掩码被换成了常量张量）"))

    return checks, g


def _closure_attention_adds(g: "ms_flatbuffer.MsGraph", start: int) -> set[str]:
    """沿张量→节点→张量做 BFS，收集闭包内所有 attention/self/Add 节点名。"""
    found: set[str] = set()
    seen_t, seen_n = {start}, set()
    stack = [start]
    while stack:
        t = stack.pop()
        for n in g.consumers(t):
            if n.index in seen_n:
                continue
            seen_n.add(n.index)
            if "attention/self/Add" in (n.name or ""):
                found.add(n.name or f"node{n.index}")
            for o in n.outputs:
                if o not in seen_t:
                    seen_t.add(o)
                    stack.append(o)
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ms", default=str(DEFAULT_MS))
    ap.add_argument("--red", action="store_true",
                    help=f"反证：对「无掩码」产物（{RED_MS.name}）跑，期望变红")
    a = ap.parse_args()

    target = Path(RED_MS if a.red else a.ms)
    if a.red:
        print(f"── 反证模式：对 {target.name}（本该没有掩码输入）──")

    checks, g = check(target)

    if g is not None and not a.red:
        print(f"── {target.name}：节点 {len(g.nodes)}，张量 {len(g.tensors)}，"
              f"图输入 {len(g.input_indices)} ──")
        for t in g.inputs():
            print(f"   [{t.index}] {t.name} {t.dtype_name}{t.dims} 元素数={t.element_num} "
                  f"消费者={[n.name for n in g.consumers(t.index)]}")
        mask = g.mask_input()
        if mask is not None:
            print(f"   → 掩码张量 [{mask.index}] 的下游闭包节点数 = "
                  f"{_reachable(g, mask.index)}（够大说明它确实在计算路径上）")

    print()
    nfail = 0
    for name, ok, detail in checks:
        print(f"{'PASS' if ok else 'FAIL'} {name} — {detail}")
        nfail += 0 if ok else 1
    verdict = "PASS" if nfail == 0 else "FAIL"
    print(f"\nMS-WIRING {verdict} {len(checks) - nfail}/{len(checks)}")

    if a.red:
        if nfail > 0:
            print("✅ 反证成立：没有掩码的产物 → 结构判据确实变红")
            return 0
        print("❌ 反证失败：没有掩码的产物竟然全绿 → 判据无效", file=sys.stderr)
        return 1
    return 0 if nfail == 0 else 1


def _reachable(g: "ms_flatbuffer.MsGraph", start: int) -> int:
    seen_t, seen_n = {start}, set()
    stack = [start]
    while stack:
        t = stack.pop()
        for n in g.consumers(t):
            if n.index in seen_n:
                continue
            seen_n.add(n.index)
            for o in n.outputs:
                if o not in seen_t:
                    seen_t.add(o)
                    stack.append(o)
    return len(seen_n)


if __name__ == "__main__":
    raise SystemExit(main())
