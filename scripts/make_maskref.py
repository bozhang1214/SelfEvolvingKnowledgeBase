#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把"掩码作输入"那个 ONNX 变体的**输入与参考输出**固化下来，供 `.ms` 数值比对用。

## 它在"掩码保真"这条链里的位置

```
model_to_ms_maskinput --check       ← 本文件（host，ORT）→ .tooling/.../maskref.json
model_to_ms_maskinput --check-ms    ← 容器内跑 .ms，与该 json 逐条比对
```

两段分开是因为**环境不同**：host 的 `backend/.venv` 有 onnxruntime，容器里有
`libmindspore-lite.so`；而 host 的 venv 是 macOS 产物、不能进 Linux 容器跑。
所以 host 生成"期望值"，容器只做"实测 + 比对"。

## 为什么同时生成 int64 / int32 两份参考

转换产物 `bge-maskinput.ms` 里 token 输入的 flatbuffer `dtype` 是 **INT32**
（见 `scripts/model_to_ms_maskfree.py --id-dtype` 的说明），而原型 ONNX 是 int64。
本文件用 onnxruntime 证明**两种声明数值等价**（同一套 id、同一个掩码下 CLS 逐元素差 ~0），
于是：

* `--check-ms` 用**与产物同声明**的那份做严格比对（容差 1e-5）；
* 同时报告两份参考之间的余弦（应 = 1），把"dtype 声明不影响语义"变成可复核的数字。

## 参考量的定义（必须与设备/Android 口径一致）

* `input_ids`：`[CLS] … [SEP]`，补位 **100**（`[UNK]`，与共享层 `BertWordPieceTokenizer`
  的 `padId=100` 一致）；
* `token_type_ids`：全 0（sentence-transformers 模板）；
* additive mask：可见位 `0`、被掩位 `-inf`，`[1,1,512,512]`，**只掩 key（列）**；
* 取 `output[0, 0]`（CLS pooling）并 L2 归一化。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import onnx

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / ".tooling" / "mindspore-lite" / "work"

TEXTS = ["端侧推理为什么省电", "北京今天多云转晴，适合骑行"]
MAXLEN = 512
PAD_ID = 100          # [UNK]：与共享层 BertWordPieceTokenizer 的 padId 一致
MASK_INPUT = "sekb_additive_mask_zero"


def load_tokenizer():
    from tokenizers import BertWordPieceTokenizer as FastBPE

    vocab = ROOT / ".tooling" / "models" / "bge-small-zh-v1.5" / "vocab.txt"
    tok = FastBPE(str(vocab), lowercase=False)

    def encode(text: str):
        enc = tok.encode(text)
        return [101] + list(enc.ids[1:-1])[: MAXLEN - 2] + [102]

    return encode


def pad_row(ids: list[int], width: int = MAXLEN) -> np.ndarray:
    row = np.full((1, width), PAD_ID, dtype=np.int64)
    row[0, : len(ids)] = ids
    return row


def additive(ids: list[int], width: int = MAXLEN, fill: float = -np.inf) -> np.ndarray:
    m = np.zeros((1, 1, width, width), dtype=np.float32)
    m[0, 0, :, len(ids):] = fill
    return m


def cls_of(sess, row: np.ndarray, mask: np.ndarray, id_dtype) -> np.ndarray:
    feed = {
        "input_ids": row.astype(id_dtype),
        "token_type_ids": np.zeros_like(row, dtype=id_dtype),
        MASK_INPUT: mask,
    }
    out = sess.run(None, feed)[0]
    v = np.asarray(out[0, 0], dtype=np.float64)
    return v / (np.linalg.norm(v) + 1e-12)


def cos(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--onnx-i64", default=str(WORK / "ms-static-maskinput.onnx"))
    ap.add_argument("--onnx-i32", default=str(WORK / "ms-static-maskinput-i32.onnx"))
    ap.add_argument("--out", default=str(WORK / "maskref.json"))
    ap.add_argument("--require-tol", type=float, default=1e-5,
                    help="i64/i32 两份参考的 CLS 最大逐元素差上限")
    a = ap.parse_args()

    for p in (a.onnx_i64, a.onnx_i32):
        if not Path(p).is_file():
            print(f"❌ 缺 {p}（先跑 scripts/model_to_ms.sh convert-maskinput）", file=sys.stderr)
            return 2

    decl = {}
    for tag, p in (("i64", a.onnx_i64), ("i32", a.onnx_i32)):
        m = onnx.load(p)
        decl[tag] = {i.name: onnx.TensorProto.DataType.Name(i.type.tensor_type.elem_type)
                     for i in m.graph.input}
    print(f"变体图 i64 输入：{decl['i64']}")
    print(f"变体图 i32 输入：{decl['i32']}")
    if MASK_INPUT not in decl["i64"]:
        print(f"❌ 变体图没有 {MASK_INPUT} 输入", file=sys.stderr)
        return 2

    encode = load_tokenizer()
    ids = [encode(t) for t in TEXTS]
    print(f"token（共享层同一套 WordPiece 规则，pad={PAD_ID}）：{[len(i) for i in ids]}")

    import onnxruntime as ort

    s64 = ort.InferenceSession(a.onnx_i64, providers=["CPUExecutionProvider"])
    s32 = ort.InferenceSession(a.onnx_i32, providers=["CPUExecutionProvider"])

    cases, ref64, ref32 = [], [], []
    for label, row_ids in zip(("A", "B"), ids):
        cases.append({
            "label": label,
            "text": TEXTS[0] if label == "A" else TEXTS[1],
            "token_ids": row_ids,
            "input_ids": [int(x) for x in pad_row(row_ids)[0]],
            "token_type_ids": [0] * MAXLEN,
            "mask_masked_from": len(row_ids),
        })
        ref64.append(cls_of(s64, pad_row(row_ids), additive(row_ids), np.int64))
        ref32.append(cls_of(s32, pad_row(row_ids), additive(row_ids), np.int32))

    d_dtype = float(max(np.max(np.abs(x - y)) for x, y in zip(ref64, ref32)))
    cos_dtype = cos(ref64[0], ref32[0])
    print(f"ORT：int64 声明 vs int32 声明的 CLS 最大逐元素差 = {d_dtype:.3e}，"
          f"余弦 = {cos_dtype:.9f}")

    # 语义自查：全零掩码必须给出明显不同的结果（"掩码真的参与计算"的 host 基线）
    vA = cls_of(s64, pad_row(ids[0]), additive(ids[0]), np.int64)
    vB = cls_of(s64, pad_row(ids[1]), additive(ids[1]), np.int64)
    zA = cls_of(s64, pad_row(ids[0]), np.zeros((1, 1, MAXLEN, MAXLEN), np.float32), np.int64)
    zB = cls_of(s64, pad_row(ids[1]), np.zeros((1, 1, MAXLEN, MAXLEN), np.float32), np.int64)
    host_cos = {"proper": cos(vA, vB), "zero": cos(zA, zB)}

    # padding 口径敏感性：把 pad id 从 100 改成 0（会被掩掉，故应几乎无差异）
    vA0 = cls_of(s64, pad_row(ids[0]).astype(np.int64), additive(ids[0]), np.int64)
    pad_rows = pad_row(ids[0]).copy()
    pad_rows[0, len(ids[0]):] = 0
    vA_pad0 = cls_of(s64, pad_rows, additive(ids[0]), np.int64)
    print(f"host ORT 语义基线：正确掩码 cos(A,B)={host_cos['proper']:.4f}，"
          f"全零掩码 cos(A,B)={host_cos['zero']:.4f}，差={abs(host_cos['proper']-host_cos['zero']):.4f}")
    print(f"   pad_id 100→0（被掩位）对 CLS 的影响：{float(np.max(np.abs(vA - vA_pad0))):.3e}"
          f"（被掩位不参与注意力，故应 ≈0）")

    ok = True
    if d_dtype > a.require_tol:
        print(f"❌ 两种 dtype 声明数值不一致（{d_dtype:.3e} > {a.require_tol}）", file=sys.stderr)
        ok = False
    if abs(host_cos["proper"] - host_cos["zero"]) < 0.05:
        print("❌ host 上掩码都没起作用——ONNX 变体本身有问题", file=sys.stderr)
        ok = False
    if not ok:
        return 1

    payload = {
        "source_onnx": {"i64": Path(a.onnx_i64).name, "i32": Path(a.onnx_i32).name},
        "mask_input": MASK_INPUT,
        "maxlen": MAXLEN,
        "pad_id": PAD_ID,
        "tokenizer": "tokenizers.BertWordPieceTokenizer(lowercase=False)",
        "mask_convention": "可见位 0 / 被掩位 -inf，[1,1,512,512]，只掩 key（列）",
        "cases": cases,
        "reference_cls": {
            "i64": [list(map(float, v)) for v in ref64],
            "i32": [list(map(float, v)) for v in ref32],
        },
        "host_cosine": host_cos,
        "dtype_equivalence": {"max_abs_diff": d_dtype, "cosine": cos_dtype},
    }
    Path(a.out).write_text(json.dumps(payload), encoding="utf-8")
    print(f"✅ 已写参考用例：{a.out}（{len(cases)} 条 × 2 种 dtype 声明）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
