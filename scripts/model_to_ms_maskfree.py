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

⚠️ **这里说的"预期不一致"只适用于本脚本不带 `--mask-input` 的默认形态（掩码被烘焙成全零常量）**，
**不适用于 `--mask-input` 的产物**。2026-10-26 实测：`--mask-input` 变体在**带 padding** 输入上
与原型图的 CLS 逐元素差只有 **1.07e-07**（判据 ≤1e-5），两图余弦都是 **0.2439**
（见 `scripts/check_maskfree_padding_gap.py`）。上一轮把这两者混为一谈，导致
"带 padding 时预期不一致"被当成了生产变体的性质——那是错的，也正是缺陷得以存活的原因之一。

全零 additive mask **只在 `attention_mask` 全 1（无 padding）时与原型严格等价**。
- 脚本默认在随机输入 + **全 1 mask** 下比对两图输出，**必须逐位相同**才继续；
- 若传入 `--check-padded`，它会在**带 padding** 的输入上比对，并**预期不一致**——
  那是用来把"这个变体不能用于生产"这件事**量化出来**，而不是掩盖它。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort
from onnx import numpy_helper


MASK_TARGETS = [f"/m/encoder/layer.{i}/attention/self/Where_output_0" for i in range(4)]
CONST_NAME = "sekb_additive_mask_zero"


def rewire_and_prune(model: onnx.ModelProto, mask_input: bool = False,
                     id_dtype: int = onnx.TensorProto.INT64) -> tuple[onnx.ModelProto, int, int]:
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

    # ── 1b) token 输入的数据类型：默认保持 int64，可改为 int32 ─────────────────
    #
    # **为什么需要这个选项**（这是本轮缺陷的直接产物，2026-10-26）：
    # 转换出来的 `bge-maskinput.ms` 里，`input_ids`/`token_type_ids` 的 flatbuffer `dtype`
    # 是 **34 = INT32**（不是 ONNX 的 INT64），运行期也只给它们分配 **512×4=2048 B**。
    # 而宿主按 int64 写 4096 B → **越界 2048 B**，后半段 id 静默丢失，
    # 设备上表现为"余弦 0.8154 / 掩码取值不影响结果"（Linux runtime 上直接堆崩溃）。
    #
    # 于是"转换产物到底是什么 dtype"必须能被**host 侧复现并证明数值等价**：
    # 这里提供 int32 版本，供 `make_maskref.py` 生成 int32 声明的参考输出，
    # 与 int64 版本逐元素比对（Gather 两种索引类型都支持，数值必须相同）。
    if id_dtype != onnx.TensorProto.INT64:
        changed = 0
        for vi in g.input:
            if vi.name in ("input_ids", "token_type_ids"):
                vi.type.tensor_type.elem_type = id_dtype
                changed += 1
        if changed != 2:
            raise SystemExit(f"❌ 只把 {changed}/2 个 token 输入改成 {id_dtype}，图结构与预期不符，停止")

    if mask_input:
        # **生产形态**：additive mask 作为**模型输入**，由宿主按实际 padding 计算后喂入。
        # 实测该张量在带 padding 时取值为 {-inf, 0}（四层完全相同 → 单个输入即够）。
        g.input.append(
            onnx.helper.make_tensor_value_info(CONST_NAME, onnx.TensorProto.FLOAT, [1, 1, 512, 512])
        )
    else:
        # 诊断形态：全零常量（只在无 padding 时严格等价）
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

    # ── 3) 清理图元数据（**这一步是转换能否通过的关键**）──
    # 只删节点、不管元数据会留下两类"幽灵"：
    #   · `value_info` 里指向已不存在张量的形状记录；
    #   · 不再被任何节点引用的 `initializer`（onnxruntime 会为此刷一屏 warning）。
    # 转 MindSpore Lite 时 `SetMetaGraphInput failed` 很可能就来自这种不一致——
    # 图结构自身"看起来"没问题，但元数据与节点对不上。
    live_inputs = {i for n in g.node for i in n.input}
    live_names = {o for n in g.node for o in n.output} | outputs | {i.name for i in g.input}

    keep_vi = [v for v in g.value_info if v.name in live_names]
    dropped_vi = len(g.value_info) - len(keep_vi)
    del g.value_info[:]
    g.value_info.extend(keep_vi)

    keep_init = [t for t in g.initializer if t.name in live_inputs]
    dropped_init = len(g.initializer) - len(keep_init)
    del g.initializer[:]
    g.initializer.extend(keep_init)

    # ── 4) 裁掉"没有消费者"的图输入 ──
    # 这一步是转换能通过的关键：掩码链被删后，`attention_mask` **不再被任何节点使用**
    # （它原本唯一的作用就是展开成 additive mask）。留着它会留下"声明了但没人用"的输入，
    # 而 MindSpore 的转换器会按内部名去找它并失败：
    #     SetMetaGraphInput] input Parameter_1 not found in graph
    # 对"无掩码"这个变体而言，模型本来就只该吃 input_ids（+ token_type_ids），
    # 所以把它从图输入里去掉才是**语义自洽**的做法，而不是为了哄转换器加一个假消费者。
    keep_inputs = [i for i in g.input
                   if i.name in live_inputs or i.name in outputs or i.name == CONST_NAME]
    dropped_in = [i.name for i in g.input if i not in keep_inputs]
    del g.input[:]
    g.input.extend(keep_inputs)

    print(f"  元数据清理：value_info -{dropped_vi}，initializer -{dropped_init}，"
          f"无消费者的图输入 -{len(dropped_in)} {dropped_in}")
    return model, rewired, removed


def run(path: str, feeds: dict[str, np.ndarray], exact: bool = False) -> np.ndarray:
    """exact=True 时**关闭 ORT 图优化**。

    判据必须用 exact：默认优化下 ONNX Runtime 会做融合/重排，浮点归约顺序一变就会出现
    1e-6 量级差异（上一轮实测 5.2e-06）。**那是融合噪声，不是语义差异**——
    用 `array_equal` 在默认优化下判等，会把一个本来正确的变换判成"有误"（我上一轮就差点如此）。
    """
    so = ort.SessionOptions()
    if exact:
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    sess = ort.InferenceSession(path, so, providers=["CPUExecutionProvider"])
    return sess.run(None, feeds)[0]


def dump_maskinput(src: str, out_i64: str, out_i32: str) -> int:
    """一次产出**两份**"掩码作输入"的 ONNX（token 输入分别声明 int64 / int32）并证明数值一致。

    为什么要有这个动作（而不是手敲两遍 `--id-dtype`）：

    * 转换出来的 `.ms` 里 token 输入实测是 **int32**（见本文件 `--id-dtype` 的说明）；
    * 但原型 ONNX（`ms-static-1x512.onnx`）声明的是 int64，本仓库既有的
      `ms-static-maskinput.onnx` 也是 int64；
    * 于是"生产变体到底该按哪种 dtype 喂"这件事必须**有 host 侧证据**，
      否则只能在设备上试错。这里用 onnxruntime 证明：同一套 id、同一个掩码下，
      两种声明给出的 CLS **逐元素差 ≈ 0**（差异只在运行期的缓冲宽度）。
    """
    model = onnx.load(src)
    for dst, dt, name in ((out_i64, onnx.TensorProto.INT64, "int64"),
                          (out_i32, onnx.TensorProto.INT32, "int32")):
        m2, rewired, removed = rewire_and_prune(onnx.load(src), mask_input=True, id_dtype=dt)
        onnx.save(m2, dst)
        print(f"  → {dst}（token 输入={name}；重接 {rewired} 处掩码，删 {removed} 个节点，"
              f"节点 {len(model.graph.node)} → {len(m2.graph.node)}）")

    # ── 等价性 1：原型（图内掩码链）vs 变体（掩码作输入），带 padding ──
    rng = np.random.default_rng(11)
    ids = rng.integers(0, 1000, size=(1, 512)).astype(np.int64)
    att = np.ones((1, 512), dtype=np.int64)
    att[0, 300:] = 0                                   # 后半段是 padding
    proto_feeds = {"input_ids": ids, "token_type_ids": np.zeros_like(ids), "attention_mask": att}
    mask_add = np.zeros((1, 1, 512, 512), dtype=np.float32)
    mask_add[0, 0, :, 300:] = -np.inf                  # 只掩 key（列）
    var_feeds_i64 = {"input_ids": ids, "token_type_ids": np.zeros_like(ids), CONST_NAME: mask_add}

    so = ort.SessionOptions()
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    s_proto = ort.InferenceSession(src, so, providers=["CPUExecutionProvider"])
    s_i64 = ort.InferenceSession(out_i64, so, providers=["CPUExecutionProvider"])
    s_i32 = ort.InferenceSession(out_i32, so, providers=["CPUExecutionProvider"])

    o_proto = s_proto.run(None, proto_feeds)[0]
    o_i64 = s_i64.run(None, var_feeds_i64)[0]
    var_feeds_i32 = dict(var_feeds_i64)
    var_feeds_i32["input_ids"] = ids.astype(np.int32)
    var_feeds_i32["token_type_ids"] = np.zeros_like(ids, dtype=np.int32)
    o_i32 = s_i32.run(None, var_feeds_i32)[0]

    d_proto = float(np.max(np.abs(o_proto - o_i64)))
    d_dtype = float(np.max(np.abs(o_i64 - o_i32)))

    def cls(v):
        x = np.asarray(v[0, 0], dtype=np.float64)
        return x / (np.linalg.norm(x) + 1e-12)

    cos_dtype = float(cls(o_i64) @ cls(o_i32))

    print(f"  【带 padding｜关闭优化】原型 vs 变体(int64) 最大逐元素差 = {d_proto:.3e}")
    print(f"  【带 padding｜关闭优化】变体 int64 vs 变体 int32 最大逐元素差 = {d_dtype:.3e}，"
          f"CLS 余弦 = {cos_dtype:.9f}")

    ok = True
    if d_proto > 1e-5:
        print("  ❌ 原型与变体在带 padding 时不一致（掩码语义被改坏了）", file=sys.stderr)
        ok = False
    if d_dtype > 1e-5:
        print("  ❌ int64/int32 两种声明数值不一致——不能拿 int32 版做对照", file=sys.stderr)
        ok = False
    if ok:
        print("  ✅ 两份变体都与原型一致，且两种 dtype 声明数值等价")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", default=".tooling/mindspore-lite/work/ms-static-1x512.onnx")
    ap.add_argument("--out", dest="dst", default="/tmp/ms-static-nomask.onnx")
    ap.add_argument("--mask-input", action="store_true",
                    help="把 additive mask 作为模型输入（生产形态），而不是全零常量")
    ap.add_argument("--id-dtype", choices=["int64", "int32"], default="int64",
                    help="token 输入（input_ids/token_type_ids）的声明类型；"
                         "转换产物实测是 int32，用 int32 可生成与之同声明的对照图")
    ap.add_argument("--dump-mask-input", metavar="DIR", default="",
                    help="一次产出两份「掩码作输入」的 ONNX（int64/int32）并证明数值等价，"
                         "写入 DIR/ms-static-maskinput.onnx 与 DIR/ms-static-maskinput-i32.onnx")
    ap.add_argument("--check-padded", action="store_true",
                    help="额外在带 padding 的输入上比对（**预期不一致**，用于量化等价边界）")
    a = ap.parse_args()

    if a.dump_mask_input:
        d = Path(a.dump_mask_input)
        d.mkdir(parents=True, exist_ok=True)
        return dump_maskinput(a.src, str(d / "ms-static-maskinput.onnx"),
                             str(d / "ms-static-maskinput-i32.onnx"))

    id_dtype = onnx.TensorProto.INT64 if a.id_dtype == "int64" else onnx.TensorProto.INT32
    model = onnx.load(a.src)
    before = len(model.graph.node)
    model, rewired, removed = rewire_and_prune(model, mask_input=a.mask_input, id_dtype=id_dtype)
    onnx.save(model, a.dst)
    print(f"  重接 {rewired} 处掩码输入；死代码消除删除 {removed} 个节点；"
          f"节点数 {before} → {len(model.graph.node)}；token 输入声明={a.id_dtype}")

    # ── 等价性：随机输入 + 全 1 mask，必须逐位相同 ──
    rng = np.random.default_rng(7)
    ids = rng.integers(0, 1000, size=(1, 512)).astype(np.int64)
    full = {"input_ids": ids,
            "attention_mask": np.ones((1, 512), dtype=np.int64),
            "token_type_ids": np.zeros((1, 512), dtype=np.int64)}
    # 变体已把 attention_mask 从图输入里裁掉（它的唯一消费者就是掩码链），
    # 所以两个模型要喂**不同的**输入集合——这不是取巧，而是变体的语义本就如此。
    var = {k: v for k, v in full.items() if k != "attention_mask"}

    # 正确性判据：关闭优化的逐位相同
    e0, e1 = run(a.src, full, exact=True), run(a.dst, var, exact=True)
    exact_same = np.array_equal(e0, e1)
    # 参考信息：默认优化下的差异（预期非 0，属融合噪声）
    d0, d1 = run(a.src, full), run(a.dst, var)
    md = float(np.max(np.abs(d0 - d1)))
    print(f"  【全 1 mask】关闭优化逐位相同={exact_same}（判据）"
          f"；默认优化最大差={md:.3e}（融合噪声，非语义差异）")
    if not exact_same:
        print("  ❌ 无 padding 且关闭优化时仍不同 → 变换有误，停止", file=sys.stderr)
        return 1

    if a.check_padded:
        padded = dict(full)
        padded["attention_mask"] = np.ones((1, 512), dtype=np.int64)
        padded["attention_mask"][0, 300:] = 0          # 后半段当作 padding
        z0 = run(a.src, padded)
        z1 = run(a.dst, {k: v for k, v in padded.items() if k != "attention_mask"})
        d = float(np.max(np.abs(z0 - z1)))
        print(f"  【带 padding】最大绝对差={d:.6f}（**预期非 0**：本变体不含掩码，"
              f"所以它只能用于「无 padding」场景——生产须改由宿主喂入 additive mask）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
