#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fp32 vs int8 端侧嵌入模型对比（体积 / 延迟 / **检索质量**）。

为什么必须比质量：量化把权重压到 1/4，代价一定是精度；"能不能用"取决于
**排序有没有变**（检索只关心相对顺序），而不是逐元素误差。所以这里直接跑
端侧仓库里那套标注集（12 篇语料 / 33 条问题），对比 Hit@1 / Hit@3 / MRR / 误召回。

标注集直接从 Kotlin 源码里解析（`RetrievalEvalSet.kt`），保证**评测集只有一份**——
复制一份到 Python 里迟早会与端侧漂移。

用法：
    python3 scripts/eval_embedding_quantization.py [--json 输出路径]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

import numpy as np
import onnxruntime as ort

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SET_KT = os.path.join(ROOT, "apps/android/app/src/main/kotlin/com/sekb/ondevice/eval/RetrievalEvalSet.kt")
FP32 = os.path.join(ROOT, ".tooling/models/bge-small-zh-v1.5")
INT8 = os.path.join(ROOT, ".tooling/models/bge-small-zh-v1.5-int8")
THRESHOLD = 0.4          # 端侧标定出来的阈值（见 RETRIEVAL-EVAL.md）
TOP_K = 3

DOC_RE = re.compile(r'Doc\("([^"]+)",\s*"((?:[^"\\]|\\.)*)"\)')
Q_RE = re.compile(r'Question\("((?:[^"\\]|\\.)*)",\s*(null|"(?:[^"\\]|\\.)*")')


def unescape(s: str) -> str:
    """只处理 Kotlin 字符串里真正用到的转义。

    ⚠️ **不要用 `codecs.decode(s, "unicode_escape")`**：那会把 UTF-8 多字节序列按
    latin-1 解释，中文直接变乱码（本脚本第一版就是这么错的——主机算出来的 Hit@1=4/30
    其实是"拿乱码去检索"，与设备端的 26/30 差得离谱才发现）。
    """
    out = []
    i = 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            out.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\", "'": "'"}.get(nxt, nxt))
            i += 2
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def load_eval_set() -> tuple[list[tuple[str, str]], list[tuple[str, str | None]]]:
    src = open(SET_KT, encoding="utf-8").read()
    docs = [(m.group(1), unescape(m.group(2))) for m in DOC_RE.finditer(src)]
    questions = []
    for m in Q_RE.finditer(src):
        q = unescape(m.group(1))
        expected = None if m.group(2) == "null" else unescape(m.group(2).strip('"'))
        questions.append((q, expected))
    return docs, questions


def load_tok(model_dir: str):
    from transformers import AutoTokenizer
    return AutoTokenizer.from_pretrained(model_dir, local_files_only=True)


def session(model_dir: str) -> ort.InferenceSession:
    return ort.InferenceSession(os.path.join(model_dir, "model.onnx"), providers=["CPUExecutionProvider"])


def embed(sess, tok, texts: list[str]) -> np.ndarray:
    enc = tok(texts, padding=True, truncation=True, max_length=512, return_tensors="np")
    feeds = {i.name: np.asarray(enc[i.name]) for i in sess.get_inputs() if i.name in enc}
    hidden = sess.run(None, feeds)[0][:, 0]                      # CLS pooling
    return hidden / np.clip(np.linalg.norm(hidden, axis=1, keepdims=True), 1e-12, None)


def cos(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b))


def evaluate(matrix: np.ndarray, ids: list[str], questions, threshold: float = THRESHOLD):
    hit1 = hit3 = 0
    rr = 0.0
    answerable = unanswerable = false_pos = 0
    for q, expected in questions:
        qv = matrix_q.setdefault(q, None) if False else None
        del qv
    return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    docs, questions = load_eval_set()
    # 自证：解析出来的必须是**真中文**而不是乱码（第一版这里翻过车）
    assert any("端侧" in t for _, t in docs), "语料解析异常（疑似乱码）：%r" % docs[0][1][:40]
    assert any("为什么" in q for q, _ in questions), "问题解析异常（疑似乱码）：%r" % questions[0][0][:40]
    print(f"评测集：语料 {len(docs)} 篇 / 问题 {len(questions)} 条"
          f"（有答案 {sum(1 for _, e in questions if e)}，无答案 {sum(1 for _, e in questions if not e)}）")

    results = {}
    emb_by_model = {}
    for tag, path in (("fp32", FP32), ("int8", INT8)):
        if not os.path.isdir(path):
            print(f"⚠️ 缺少模型目录：{path}")
            return 1
        tok = load_tok(FP32)                                      # 两个模型共用同一分词器
        sess = session(path)
        doc_ids = [d for d, _ in docs]
        t0 = time.perf_counter()
        doc_mat = embed(sess, tok, [t for _, t in docs])
        doc_ms = (time.perf_counter() - t0) * 1000 / max(len(docs), 1)

        qs = [q for q, _ in questions]
        t0 = time.perf_counter()
        q_mat = embed(sess, tok, qs)
        q_ms = (time.perf_counter() - t0) * 1000 / max(len(qs), 1)

        emb_by_model[tag] = (doc_ids, doc_mat, q_mat)

        hit1 = hit3 = fp = 0
        rr = 0.0
        answerable = sum(1 for _, e in questions if e)
        unanswerable = sum(1 for _, e in questions if not e)
        for i, (q, expected) in enumerate(questions):
            sims = doc_mat @ q_mat[i]
            order = np.argsort(-sims)
            ranked = [(doc_ids[j], float(sims[j])) for j in order if sims[j] >= THRESHOLD]
            if expected is None:
                if ranked:
                    fp += 1
                continue
            rank = next((k + 1 for k, (d, _) in enumerate(ranked) if d == expected), 0)
            hit1 += 1 if rank == 1 else 0
            hit3 += 1 if 1 <= rank <= TOP_K else 0
            rr += (1.0 / rank) if rank else 0.0
        results[tag] = dict(
            size_mb=os.path.getsize(os.path.join(path, "model.onnx")) / 1e6,
            hit1=hit1, hit3=hit3, mrr=rr / max(answerable, 1),
            false_positives=fp, answerable=answerable, unanswerable=unanswerable,
            embed_doc_ms=doc_ms, embed_query_ms=q_ms,
        )

    # 空间一致性：同一文本在两个模型下的余弦（"一致"不等于"相同"——量化必然有偏移）
    _, doc_fp32, q_fp32 = emb_by_model["fp32"]
    _, doc_int8, q_int8 = emb_by_model["int8"]
    per_text = [cos(a, b) for a, b in zip(doc_fp32, doc_int8)]
    per_query = [cos(a, b) for a, b in zip(q_fp32, q_int8)]

    # 区分度：语料两两余弦的**平均值**（单挑一对很容易被"这两篇本来就相近"误导——
    # 第一版用 doc[0] vs doc[2]，两篇都在讲端侧，0.93 其实是合理的）
    def discrimination(mat):
        m = mat @ mat.T
        n = m.shape[0]
        off = m[~np.eye(n, dtype=bool)]
        return float(np.mean(off))

    print("\n%-6s %8s %7s %7s %7s %8s %10s %10s" %
          ("模型", "体积MB", "Hit@1", "Hit@3", "MRR", "误召回", "语料嵌入ms", "问题嵌入ms"))
    for tag in ("fp32", "int8"):
        r = results[tag]
        print("%-6s %8.1f %5d/%-2d %5d/%-2d %7.3f %5d/%-2d %10.1f %10.1f" % (
            tag, r["size_mb"], r["hit1"], r["answerable"], r["hit3"], r["answerable"],
            r["mrr"], r["false_positives"], r["unanswerable"], r["embed_doc_ms"], r["embed_query_ms"]))

    print("\n跨模型一致性（同一文本在两个模型下的余弦；1.0=完全相同）")
    print("  语料均 %.4f（最低 %.4f）｜问题均 %.4f（最低 %.4f）" % (
        float(np.mean(per_text)), float(np.min(per_text)),
        float(np.mean(per_query)), float(np.min(per_query))))
    print("区分度探针（语料两两余弦均值，越低说明向量越有区分度）：fp32 %.4f ｜ int8 %.4f" % (
        discrimination(doc_fp32), discrimination(doc_int8)))

    # 阈值标定曲线：量化会**整体抬高分数分布**（区分度下降），所以固定阈值必须重标
    print("\n阈值标定曲线（Hit@1 / 误召回）：")
    for tag in ("fp32", "int8"):
        doc_ids, doc_mat, q_mat = emb_by_model[tag]
        row = []
        for th in (0.4, 0.5, 0.6, 0.65, 0.7):
            h1 = h3 = fp = 0
            ans = sum(1 for _, e in questions if e)
            una = sum(1 for _, e in questions if not e)
            for i, (q, expected) in enumerate(questions):
                sims = doc_mat @ q_mat[i]
                ranked = [(doc_ids[j], float(sims[j])) for j in np.argsort(-sims) if sims[j] >= th]
                if expected is None:
                    fp += 1 if ranked else 0
                    continue
                rank = next((k + 1 for k, (d, _) in enumerate(ranked) if d == expected), 0)
                h1 += 1 if rank == 1 else 0
                h3 += 1 if 1 <= rank <= TOP_K else 0
            row.append("  %.2f→%2d/%d、误召回%d/%d" % (th, h1, ans, fp, una))
        print("  %-5s%s" % (tag, "".join(row)))

    cmp = results["fp32"]["hit1"] - results["int8"]["hit1"]
    print("\n结论：int8 体积 %.1f→%.1f MB（%.1f 倍）" % (
        results["fp32"]["size_mb"], results["int8"]["size_mb"],
        results["fp32"]["size_mb"] / results["int8"]["size_mb"]))
    print("      Hit@1 %d→%d（差 %+d）、Hit@3 %d→%d、误召回 %d→%d" % (
        results["fp32"]["hit1"], results["int8"]["hit1"], -cmp,
        results["fp32"]["hit3"], results["int8"]["hit3"],
        results["fp32"]["false_positives"], results["int8"]["false_positives"]))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(dict(results=results, mean_doc_cos=float(np.mean(per_text)),
                           min_doc_cos=float(np.min(per_text)),
                           discrimination_fp32=discrimination(doc_fp32),
                           discrimination_int8=discrimination(doc_int8)), fh,
                      ensure_ascii=False, indent=2)
        print("已写出", args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
