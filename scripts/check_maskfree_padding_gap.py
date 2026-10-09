#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""量化"去掉动态掩码链"那个模型变体在**带 padding** 时的等价性缺口（H6 的关键实验）。

## 要回答的问题（一句话）

鸿蒙端侧嵌入在设备上跑通了，但**余弦与 Android 基线差三倍**：
设备实测 `cos(端侧推理为什么省电, 北京今天多云转晴适合骑行) = 0.8154`，而 Android 基线是 **0.244**。
这到底是
  (a) 我的 Kotlin 输入/掩码构造写错了，还是
  (b) `bge-maskinput.ms` 这个"掩码改由宿主喂入"的变体在**有 padding** 时本身就不等价？

## 为什么这个实验能分辨两者

把**同一套 token id / 同一套 attention_mask** 分别喂给两张图，只看余弦：

| 图 | 输入 | 期望 |
|---|---|---|
| `ms-static-1x512.onnx`（原型，图内含动态掩码链） | input_ids / token_type_ids / **attention_mask** | 应 ≈ **0.244**（与 Android 对齐；也顺带验证分词口径） |
| `ms-static-maskinput.onnx`（生产变体，掩码作为输入） | input_ids / token_type_ids / **sekb_additive_mask_zero**（可见 0、被掩 -inf） | 若 ≈0.81 → **(b)**：变体在 padding 下不等价，Kotlin 没错；若 ≈0.24 → **(a)**：我的掩码/角色构造有问题 |

## 已有文档里的旁证（`scripts/model_to_ms_maskfree.py` 头部）

> 全零 additive mask **只在 attention_mask 全 1（无 padding）时与原型严格等价**；
> 带 padding 时**预期不一致**——那是用来把"这个变体不能用于生产"量化出来，而不是掩盖它。

本脚本就是把那句话**测出数字来**，并顺便给出"无 padding 时是否真的逐位相同"的复核。

## 用法

    backend/.venv/bin/python3 scripts/check_maskfree_padding_gap.py

不需要设备、不需要重新转换（用 `.tooling/mindspore-lite/work/` 下已有的 onnx）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import onnxruntime as ort

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / ".tooling/mindspore-lite/work"
VOCAB = ROOT / ".tooling/models/bge-small-zh-v1.5/vocab.txt"
PROTO = WORK / "ms-static-1x512.onnx"
VARIANT = WORK / "ms-static-maskinput.onnx"

TEXTS = ["端侧推理为什么省电", "北京今天多云转晴，适合骑行"]
MAXLEN = 512
MASK_NAME = "sekb_additive_mask_zero"


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    a, b = a.ravel().astype(np.float64), b.ravel().astype(np.float64)
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))


def load_tokenizer():
    """优先用 transformers（与共享层 `BertWordPieceTokenizer` 同一套 WordPiece 规则）。"""
    from tokenizers import BertWordPieceTokenizer as FastBPE  # tokenizers 与 transformers 同装

    tok = FastBPE(str(VOCAB), lowercase=False)

    def encode(text: str):
        enc = tok.encode(text)
        ids = [101] + list(enc.ids[1:-1])[: MAXLEN - 2] + [102]
        return ids

    return encode, "tokenizers.BertWordPieceTokenizer（lowercase=False）"


def pad(ids: list[int]):
    row = np.zeros((1, MAXLEN), dtype=np.int64)
    att = np.zeros((1, MAXLEN), dtype=np.int64)
    row[0, : len(ids)] = ids
    att[0, : len(ids)] = 1
    return row, att


def additive(att: np.ndarray) -> np.ndarray:
    """可见位 0、被掩位 -inf，形状 [1,1,512,512]（只掩 key：按列）。"""
    n = att.shape[1]
    m = np.zeros((1, 1, n, n), dtype=np.float32)   # 零 = 全部可见
    for j in range(n):
        if att[0, j] == 0:
            m[0, 0, :, j] = -np.inf
    return m


def cls(sess, feeds) -> np.ndarray:
    out = sess.run(None, feeds)[0]           # [1, 512, 512]
    v = np.asarray(out[0, 0], dtype=np.float32)
    return v / (np.linalg.norm(v) + 1e-12)


def main() -> int:
    for p in (PROTO, VARIANT):
        if not p.is_file():
            print(f"❌ 缺少 {p}（先跑 scripts/model_to_ms.sh 生成）", file=sys.stderr)
            return 2

    encode, how = load_tokenizer()
    print(f"分词器：{how}")
    ids = [encode(t) for t in TEXTS]
    print(f"token 数：{[len(i) for i in ids]}（两段共 {MAXLEN} 上限）")

    s_proto = ort.InferenceSession(str(PROTO), providers=["CPUExecutionProvider"])
    s_var = ort.InferenceSession(str(VARIANT), providers=["CPUExecutionProvider"])
    print(f"原型图输入  ：{[i.name for i in s_proto.get_inputs()]}")
    print(f"变体图输入  ：{[i.name for i in s_var.get_inputs()]}")
    print()

    rows = []
    for label, t in zip(("A", "B"), TEXTS):
        row, att = pad(ids[0] if label == "A" else ids[1])
        # 原型：图内自己从 attention_mask 展开 additive mask
        v_proto = cls(s_proto, {
            "input_ids": row, "token_type_ids": np.zeros_like(row), "attention_mask": att,
        })
        # 生产变体：宿主算好 additive mask 喂进去
        v_var = cls(s_var, {
            "input_ids": row, "token_type_ids": np.zeros_like(row), MASK_NAME: additive(att),
        })
        rows.append((v_proto, v_var))
        # 单条上两图输出的差异（逐元素最大差）：这才是"同一输入两图是否等价"的直接判据
        d = float(np.max(np.abs(v_proto - v_var)))
        print(f"[{label}] 原型 vs 变体（同一输入）CLS 向量最大逐元素差 = {d:.3e}")

    cos_proto = cosine(rows[0][0], rows[1][0])
    cos_var = cosine(rows[0][1], rows[1][1])
    print()
    print(f"cos(原型图, 有 padding)      = {cos_proto:.4f}   ← Android 基线 0.244 应对上这个")
    print(f"cos(生产变体, 有 padding)    = {cos_var:.4f}   ← 设备实测 0.8154 应对上这个")
    print()

    # 无 padding 复核：脚本头声称"全 1 mask 时逐位相同"。用两段**恰好填满 512**的输入验证。
    full = ((ids[0] + [100] * MAXLEN)[:MAXLEN],)
    row = np.array([full[0]], dtype=np.int64)
    att1 = np.ones((1, MAXLEN), dtype=np.int64)
    v1 = cls(s_proto, {"input_ids": row, "token_type_ids": np.zeros_like(row), "attention_mask": att1})
    v2 = cls(s_var, {"input_ids": row, "token_type_ids": np.zeros_like(row), MASK_NAME: additive(att1)})
    d_nopad = float(np.max(np.abs(v1 - v2)))
    print(f"无 padding（attention_mask 全 1）时两图 CLS 向量最大逐元素差 = {d_nopad:.3e}")
    print("   → 若为 0 或 ~1e-7，说明'无 padding 严格等价'的说法成立，缺口确实只在 padding 上")

    print()
    if cos_var > 0.5 > cos_proto:
        print("✅ 结论：**缺口来自模型变体在 padding 下的不等价**（(b)），不是 Kotlin 的输入构造。")
        print("   端侧嵌入要拿到与 Android 相同的 0.244，必须换回'图内掩码'的模型，")
        print("   或在变体上重新标定阈值并接受'空间不再与云端一致'（那违反 RFC M3 不变量）——需 owner 决策。")
        return 0
    print("⚠️ 结论不明确：两个余弦都偏高或都低 → 需要再查（可能分词口径不同）。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
