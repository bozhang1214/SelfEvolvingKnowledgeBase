#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**host 验收实验**：变体 ONNX（掩码作输入）与原型 ONNX（图内掩码链）在**带 padding** 输入上等价。

## 要回答的问题

鸿蒙端侧嵌入曾出现"余弦与 Android 基线差三倍"（设备 0.8154 vs Android 0.244）。
本轮查清根因**不在**模型变体、也**不在**转换器，而在**设备侧的喂数路径**
（把 int32 的 token 输入按 int64 写了 4096 B，越界 2048 B → 后半段 id 丢失；
证据与修法见 `apps/harmony/README.md` 的本轮小节）。所以这个实验的结论要重新表述：

> **变体图本身是对的**：带 padding 时它与原型图的 CLS 逐元素差 **~1e-7**（远小于 1e-5 判据），
> 两图算出的余弦都应落在 Android 基线 **0.244±0.02** 内。

上一版脚本把这句话写反了（`model_to_ms_maskfree.py` 头部曾说"带 padding 时预期不一致"），
而且判据写成"两个余弦必须一个 >0.5 一个 <0.5"——**那是把"设备上的错误现象"
当成了"模型应有的性质"**，于是正确的模型反而被判成"结论不明确"。本版把它纠正为：

1. **等价性**（硬判据）：每条文本上 `|CLS_proto − CLS_variant|_max ≤ 1e-5`；
2. **同空间**（硬判据）：两个余弦都满足 `|cos − 0.244| ≤ 0.02`；
3. **反向验证**（`--red`）：故意喂全零掩码 → 余弦必须**明显偏离** 0.244（证明判据会红）。

用法：

    backend/.venv/bin/python3 scripts/check_maskfree_padding_gap.py
    backend/.venv/bin/python3 scripts/check_maskfree_padding_gap.py --red   # 反证
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import onnxruntime as ort

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / ".tooling" / "mindspore-lite" / "work"
VOCAB = ROOT / ".tooling" / "models" / "bge-small-zh-v1.5" / "vocab.txt"
PROTO = WORK / "ms-static-1x512.onnx"
VARIANT = WORK / "ms-static-maskinput.onnx"

TEXTS = ["端侧推理为什么省电", "北京今天多云转晴，适合骑行"]
MAXLEN = 512
MASK_NAME = "sekb_additive_mask_zero"
ANDROID_COS = 0.244
COS_TOL = 0.02
EQ_TOL = 1e-5


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    a, b = a.ravel().astype(np.float64), b.ravel().astype(np.float64)
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))


def load_tokenizer():
    """与共享层 `BertWordPieceTokenizer` 同一套 WordPiece 规则（lowercase=False）。"""
    from tokenizers import BertWordPieceTokenizer as FastBPE

    tok = FastBPE(str(VOCAB), lowercase=False)

    def encode(text: str):
        enc = tok.encode(text)
        return [101] + list(enc.ids[1:-1])[: MAXLEN - 2] + [102]

    return encode, "tokenizers.BertWordPieceTokenizer（lowercase=False）"


def pad(ids: list[int]):
    row = np.zeros((1, MAXLEN), dtype=np.int64)
    att = np.zeros((1, MAXLEN), dtype=np.int64)
    row[0, : len(ids)] = ids
    att[0, : len(ids)] = 1
    return row, att


def additive(att: np.ndarray, fill: float = -np.inf) -> np.ndarray:
    """可见位 0、被掩位 [fill]，形状 [1,1,512,512]（只掩 key：按列）。"""
    n = att.shape[1]
    m = np.zeros((1, 1, n, n), dtype=np.float32)
    for j in range(n):
        if att[0, j] == 0:
            m[0, 0, :, j] = fill
    return m


def cls(sess, feeds) -> np.ndarray:
    out = sess.run(None, feeds)[0]
    v = np.asarray(out[0, 0], dtype=np.float64)
    return v / (np.linalg.norm(v) + 1e-12)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--red", action="store_true",
                    help="反证：把 additive mask 换成全零（= 不掩任何位），期望判据变红")
    a = ap.parse_args()

    for p in (PROTO, VARIANT):
        if not p.is_file():
            print(f"❌ 缺少 {p}（先跑 scripts/model_to_ms.sh convert-maskinput）", file=sys.stderr)
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

    fail = 0
    v_proto_all, v_var_all = [], []
    for label, t in zip(("A", "B"), TEXTS):
        row, att = pad(ids[0] if label == "A" else ids[1])
        # 原型：图内自己从 attention_mask 展开 additive mask
        v_proto = cls(s_proto, {
            "input_ids": row, "token_type_ids": np.zeros_like(row), "attention_mask": att,
        })
        # 生产变体：宿主算好 additive mask 喂进去
        mask = additive(att)
        if a.red:
            mask = np.zeros_like(mask)          # 反证：全零 = 不掩任何位
        v_var = cls(s_var, {
            "input_ids": row, "token_type_ids": np.zeros_like(row), MASK_NAME: mask,
        })
        v_proto_all.append(v_proto)
        v_var_all.append(v_var)
        d = float(np.max(np.abs(v_proto - v_var)))
        ok = d <= EQ_TOL
        fail += 0 if ok else 1
        print(f"[{label}] 原型 vs 变体（同一输入）CLS 最大逐元素差 = {d:.3e} "
              f"（{'PASS' if ok else 'FAIL'}，判据 ≤{EQ_TOL:g}）")

    cos_proto = cosine(v_proto_all[0], v_proto_all[1])
    cos_var = cosine(v_var_all[0], v_var_all[1])
    print()
    for name, c in (("原型图", cos_proto), ("生产变体", cos_var)):
        ok = abs(c - ANDROID_COS) <= COS_TOL
        fail += 0 if ok else 1
        print(f"cos({name}, 有 padding) = {c:.4f}  {'PASS' if ok else 'FAIL'}"
              f"（Android 基线 {ANDROID_COS}，容差 ±{COS_TOL}）")

    print()
    if a.red:
        if fail > 0:
            print(f"✅ 反证成立：全零掩码 → {fail} 条判据变红（判据不是摆设）")
            return 0
        print("❌ 反证失败：全零掩码竟然全绿 → 判据无效", file=sys.stderr)
        return 1

    if fail == 0:
        print("✅ host 验收通过：变体与原型在带 padding 时数值等价，且都落在 Android 基线同一区间。")
        print("   注意口径：**这条只证明模型变体是对的**；设备上是否真的用掩码，")
        print("   由 `bash scripts/harmony.sh embed` 的 embed_mask_effective / embed_sanity_cosine")
        print("   与 `.ms` 侧数值判据（model_to_ms.sh --check-ms）分别把关。")
        return 0
    print(f"❌ host 验收未通过：{fail} 条判据失败", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
