#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把一个「掩码链」从静态 BERT 图里摘掉，用于**验证 M7 路线①的假说**。

## 背景（为什么需要这个脚本）

`ms-static-1x512.onnx` 输入已固定 1×512，但图里**仍带着完整的动态掩码展开链**
（Shape/ConstantOfShape/Range/Gather/Where…；`scripts/model_to_ms.sh` 头部有量化：
以 5 个 `Where` 输出反向切片共 67 节点，其中不被主路径共享的 18 节点）。
MindSpore Lite 转换出的 `.ms` 在 `benchmark` 里 **0 CPU 挂起**（不是 Flatten 问题，那个只属于动态产物）。

**假说**：挂起来自这条掩码链。本脚本造出"掩码链已被移除"的变体，交给 convert+benchmark 去证伪或证实。

## 变换（可证明正确）

1. 掩码链最终只产出 4 个张量：`/m/encoder/layer.{0..3}/attention/self/Where_output_0`，
   分别喂给各层 `attention/self/Add`。把那一输入**改接**到一个全零常量 `(1,1,512,512)`。
2. 然后做**迭代死代码消除**（从改接后的图出发反复删除"输出无人消费且非图输出"的节点），
   整条链会被自动清干净——不需要人去枚举要删哪些节点（手写删除清单非常容易漏）。

## 等价性边界（重要，不许含糊）

全零 additive mask **只在 `attention_mask` 全 1（无 padding）时与原型严格等价**。
- 脚本默认在随机输入 + **全 1 mask** 下比对两图输出，**必须逐位相同**才继续；
- 若传入 `--check-padded`，它会在**带 padding** 的输入上比对，并**预期不一致**——
  那是用来把"这个变体不能用于生产"这件事**量化出来**，而不是掩盖它。
"""
from __future__ import annotations

import argparse
import sys

import numpy as np
import onnx
import onnxruntime as ort
from onnx import numpy_helper


MASK_TARGETS = [f"/m/encoder/layer.{i}/attention/self/Where_output_0" for i in range(4)]
CONST_NAME = "sekb_additive_mask_zero"


def rewire_and_prune(model: onnx.ModelProto) -> tuple[onnx.ModelProto, int, int]:
    g = model.graph
    consumers: dict[str, list[str]] = {}
    for n in g.node:
        for i in n.input:
            consumers.setdefault(i, []).append(n.name)

    # ── 1) 重接：注意力 Add 的掩码输入 → 全零常量 ──
    # 顺序无关（Add 可交换），但保持原位替换，避免触碰其它语义。
    rewired = 0
    for n in g.node:
        if n.op_type == "Add" and "attention/self/Add" in n.name:
            for idx, i in enumerate(n.input):
                if i in MASK_TARGETS:
                    n.input[idx] = CONST_NAME
                    rewired += 1
    if rewired != len(MASK_TARGETS):
        raise SystemExit(f"❌ 只重接了 {rewired}/{len(MASK_TARGETS)} 处注意力掩码输入，图结构与预期不符，停止")

    # 加全零常量（若已存在则复用）
    if not any(t.name == CONST_NAME for t in g.initializer):
        g.initializer.append(
            numpy_helper.from_array(np.zeros((1, 1, 512, 512), dtype=np.float32), CONST_NAME)
        )

    # ── 2) 迭代死代码消除 ──
    # 反复删除"所有输出都无人消费"的节点；被删节点释放的输入可能让上游也可删 → 直到不动点。
    outputs = {o.name for o in g.output}
    removed = 0
    while True:
        live = {i for n in g.node for i in n.input}
        keep, dropped = [], []
        for n in g.node:
            # 节点存活条件：任一输出被消费，或本身是图输出
            if any(o in live or o in outputs for o in n.output):
                keep.append(n)
            else:
                dropped.append(n)
        if not dropped:
            break
        removed += len(dropped)
        del g.node[:]
        g.node.extend(keep)
    return model, rewired, removed


def run(path: str, feeds: dict[str, np.ndarray]) -> np.ndarray:
    sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
    return sess.run(None, feeds)[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", default=".tooling/mindspore-lite/work/ms-static-1x512.onnx")
    ap.add_argument("--out", dest="dst", default="/tmp/ms-static-nomask.onnx")
    ap.add_argument("--check-padded", action="store_true",
                    help="额外在带 padding 的输入上比对（**预期不一致**，用于量化等价边界）")
    a = ap.parse_args()

    model = onnx.load(a.src)
    before = len(model.graph.node)
    model, rewired, removed = rewire_and_prune(model)
    onnx.save(model, a.dst)
    print(f"  重接 {rewired} 处掩码输入；死代码消除删除 {removed} 个节点；"
          f"节点数 {before} → {len(model.graph.node)}")

    # ── 等价性：随机输入 + 全 1 mask，必须逐位相同 ──
    rng = np.random.default_rng(7)
    ids = rng.integers(0, 1000, size=(1, 512)).astype(np.int64)
    full = {"input_ids": ids,
            "attention_mask": np.ones((1, 512), dtype=np.int64),
            "token_type_ids": np.zeros((1, 512), dtype=np.int64)}
    y0, y1 = run(a.src, full), run(a.dst, full)
    same = np.array_equal(y0, y1)
    md = float(np.max(np.abs(y0 - y1)))
    print(f"  【全 1 mask】逐位相同={same} 最大绝对差={md:.3e}")
    if not same:
        print("  ❌ 无 padding 时都不同 → 变换有误，停止", file=sys.stderr)
        return 1

    if a.check_padded:
        padded = dict(full)
        padded["attention_mask"] = np.ones((1, 512), dtype=np.int64)
        padded["attention_mask"][0, 300:] = 0          # 后半段当作 padding
        z0, z1 = run(a.src, padded), run(a.dst, padded)
        d = float(np.max(np.abs(z0 - z1)))
        print(f"  【带 padding】最大绝对差={d:.6f}（**预期非 0**：本变体不含掩码，"
              f"所以它只能用于「无 padding」场景——生产须改由宿主喂入 additive mask）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
