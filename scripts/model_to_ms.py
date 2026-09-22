#!/usr/bin/env python3
"""把 bge-small-zh 的 ONNX 改写成 MindSpore Lite 能解析的等价图，并**证明**等价。

## 为什么需要这一步

MindSpore Lite 的 onnxparser 不认 `IsNaN` 算子（实测报
`not support onnx data type IsNaN`，4 处，每层注意力一个），于是 `.ms` 转换整个失败。

这 4 个 `IsNaN` 来自 HuggingFace BERT 导出时的**注意力 softmax 数值保护**：

    Softmax ──► IsNaN ──┐
                         ├─► Where(cond, 0.0, softmax) ─► MatMul
    Constant(0.0) ───────┘

即 `nan_to_num(softmax)`：把 NaN 换成 0。**在本模型里这是死代码**——
掩码走的是"加上一个大负数"的加性掩码（不是 `-inf`），softmax 的输入恒为有限值，
输出不可能出现 NaN。所以 `Where(IsNaN(x), 0, x)` 恒等于 `x`，可以安全地短路掉。

## 为什么必须"证明"而不是"我觉得"

删算子是在改模型图。这个项目的既有纪律是"数字要能对上"，所以本脚本
**不只看转换成功**：它用 onnxruntime 把改写前后的图各跑一遍，
要求逐元素最大差为 0（不是"余弦接近 1"，是**逐位相同**）。
不满足就直接失败，不产出 .ms。

用法：
    python3 scripts/model_to_ms.py <in.onnx> <out.onnx> [--tolerance 0.0]
"""
from __future__ import annotations

import argparse
import sys

import numpy as np
import onnx
from onnx import numpy_helper


def rewrite_softmax_nan_guard(model: onnx.ModelProto) -> tuple[onnx.ModelProto, int]:
    """把 `Where(IsNaN(x), c, x)` 短路成 `x`，返回 (新模型, 改写处数)。"""
    graph = model.graph
    producer = {out: node for node in graph.node for out in node.output}

    replacements: dict[str, str] = {}   # Where 输出 -> 直接沿用的张量名
    drop: set[str] = set()              # 要删掉的节点名

    for node in graph.node:
        if node.op_type != "Where" or len(node.input) != 3:
            continue
        cond, x_name, y_name = node.input
        cond_node = producer.get(cond)
        if cond_node is None or cond_node.op_type != "IsNaN":
            continue
        # 条件必须恰好是 IsNaN(Y)（Y 是被保护的那一支）
        if cond_node.input[0] != y_name:
            continue
        # 另一支必须是**常量**：证明"NaN 换成某个常数"，而不是某种有语义的选择
        x_node = producer.get(x_name)
        if x_node is None or x_node.op_type != "Constant":
            continue
        replacements[node.output[0]] = y_name
        drop.add(node.name)
        drop.add(cond_node.name)
        drop.add(x_node.name)
        print(f"  短路 {node.name}: Where(IsNaN({y_name}), {x_name}, {y_name}) -> {y_name}")

    if not replacements:
        return model, 0

    # 重接消费者
    def fix(name: str) -> str:
        seen = set()
        while name in replacements and name not in seen:
            seen.add(name)
            name = replacements[name]
        return name

    for node in graph.node:
        for i, inp in enumerate(node.input):
            if inp in replacements:
                node.input[i] = fix(inp)
    for out in graph.output:
        if out.name in replacements:
            out.name = fix(out.name)

    kept = [n for n in graph.node if n.name not in drop]
    del graph.node[:]
    graph.node.extend(kept)

    # 常量若已没人用，一并删掉（否则会留下"没人引用的 initializer"告警）
    used = {i for n in graph.node for i in n.input}
    keep_init = [t for t in graph.initializer if t.name in used]
    removed_init = len(graph.initializer) - len(keep_init)
    if removed_init:
        print(f"  顺带删除 {removed_init} 个不再被引用的 initializer")
    del graph.initializer[:]
    graph.initializer.extend(keep_init)

    del model.opset_import[:]  # 不改 opset，只是清理重复项
    model.opset_import.extend([onnx.helper.make_opsetid("", 14)])
    return model, len(replacements)


def run_onnx(path: str, feeds: dict[str, np.ndarray]) -> np.ndarray:
    import onnxruntime as ort

    sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
    out = sess.run(None, feeds)
    return out[0]


def make_feeds(seed: int, batch: int, seq: int) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    ids = rng.integers(0, 20000, size=(batch, seq), dtype=np.int64)
    ids[:, 0] = 101          # [CLS]
    ids[:, seq - 1] = 102    # [SEP]
    mask = np.ones((batch, seq), dtype=np.int64)
    if seq > 8:              # 造一点真实 padding，确保掩码分支被走到
        mask[:, seq - 4:] = 0
        ids[:, seq - 4:] = 0
    return {
        "input_ids": ids,
        "attention_mask": mask,
        "token_type_ids": np.zeros((batch, seq), dtype=np.int64),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--tolerance", type=float, default=0.0,
                    help="改写前后逐元素最大允许差（默认 0：必须逐位相同）")
    args = ap.parse_args()

    print(f"① 读取 {args.src}")
    model = onnx.load(args.src)

    print("② 短路 softmax 的 NaN 保护")
    new_model, n = rewrite_softmax_nan_guard(model)
    if n == 0:
        print("   （没有可短路的节点——模型本来就不含 IsNaN，直接原样输出）")
        onnx.save(model, args.dst)
        return 0

    print("③ 检查产物里确实没有 IsNaN 了")
    if any(x.op_type == "IsNaN" for x in new_model.graph.node):
        print("❌ 仍有 IsNaN 残留", file=sys.stderr)
        return 1
    onnx.checker.check_model(new_model)
    onnx.save(new_model, args.dst)
    print("   ✅ 图检查通过，已写出")

    print("④ 用 onnxruntime 证明改写前后**数值等价**（这是本脚本存在的意义）")
    worst = 0.0
    for seed, batch, seq in ((1, 1, 16), (2, 1, 64), (3, 2, 33), (4, 1, 128)):
        feeds = make_feeds(seed, batch, seq)
        a = run_onnx(args.src, feeds)
        b = run_onnx(args.dst, feeds)
        diff = float(np.max(np.abs(a.astype(np.float64) - b.astype(np.float64))))
        # CLS 向量（端侧真正用的那一行）单独看一次
        cls_diff = float(np.max(np.abs(a[:, 0, :] - b[:, 0, :])))
        worst = max(worst, diff)
        print(f"   shape=({batch},{seq}) 全量最大差={diff:.3e} CLS最大差={cls_diff:.3e}")
        if diff > args.tolerance:
            print(f"❌ 超过容差 {args.tolerance}", file=sys.stderr)
            return 1

    print(f"✅ 等价性成立（全局最大差 {worst:.3e} ≤ 容差 {args.tolerance}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
