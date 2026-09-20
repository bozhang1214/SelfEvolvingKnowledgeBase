#!/usr/bin/env bash
# ============================================================
# 取端侧嵌入模型（ONNX 版 bge-small-zh-v1.5）到 .tooling/models/
# ============================================================
# 为什么是"导出"而不是"下载"：
#   1. 本机 HF 缓存里已经有 `BAAI/bge-small-zh-v1.5` 的权重（SEKB 云端用的就是它）；
#   2. 导出能保证**与云端完全同一套权重 + 同一个分词器**——这正是"向量空间一致"
#      （`BAAI/bge-small-zh-v1.5@512`）的前提；
#   3. 走国际下载链路（HF/ModelScope）要么慢要么断，导出是纯本地操作。
#
# 用法：
#   bash scripts/fetch_embedding_model.sh            # 导出（若已存在则跳过）
#   bash scripts/fetch_embedding_model.sh --force    # 强制重导出
#   bash scripts/fetch_embedding_model.sh --verify    # 与 sentence-transformers 比对余弦
#   bash scripts/fetch_embedding_model.sh --int8      # 额外产出 int8 量化模型（体积 ~1/4）
#
# ⚠️ **量化会改变向量空间**：int8 模型的向量与 fp32 不同，绝不能混用（RFC §18.1）。
#    端侧以 `BAAI/bge-small-zh-v1.5-int8@512` 作为独立空间标记，切换时自动原地重算索引。
#
# 产物（**不入库**，.tooling/ 已 gitignore；95MB fp32）：
#   .tooling/models/bge-small-zh-v1.5/{model.onnx,vocab.txt,tokenizer.json,...}
#
# 推到模拟器/真机：bash scripts/android.sh push-model
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/.tooling/models/bge-small-zh-v1.5"
PY="$ROOT/backend/.venv/bin/python"
FORCE=0; VERIFY=0; INT8=0
for a in "$@"; do
  [ "$a" = "--force" ] && FORCE=1
  [ "$a" = "--verify" ] && VERIFY=1
  [ "$a" = "--int8" ] && INT8=1
done

[ -x "$PY" ] || { echo "需要 SEKB 后端 venv（含 torch/transformers）：$PY 不存在" >&2; exit 1; }
mkdir -p "$OUT"

if [ "$FORCE" -eq 0 ] && [ -f "$OUT/model.onnx" ] && [ -f "$OUT/vocab.txt" ]; then
    echo "✅ 模型已存在：${OUT}（用 --force 重导出）"
else
    echo "导出 ONNX（离线，使用本机 HF 缓存）..."
    HF_HUB_OFFLINE=1 "$PY" - "$OUT" <<'PYEOF'
import glob, os, sys, torch
from transformers import AutoTokenizer, AutoModel

out = sys.argv[1]
snaps = glob.glob(os.path.expanduser("~/.cache/huggingface/hub/models--BAAI--bge-small-zh-v1.5/snapshots/*/"))
if not snaps:
    sys.exit("本机 HF 缓存里没有 bge-small-zh-v1.5；请先让 SEKB 后端跑一次嵌入以拉取，或改走 ModelScope 下载")
snap = snaps[0]
tok = AutoTokenizer.from_pretrained(snap, local_files_only=True)
# ⚠️ 必须显式 use_safetensors=False：本机缓存里的 model.safetensors 是**退化权重**
# （对两段无关文本的 CLS 余弦恰好 1.000000），而 pytorch_model.bin 是正常的。
# 用 safetensors 导出的 ONNX 会把"所有向量几乎一样"带进端侧，检索形同随机。
# 见 docs/RFC §18.6 的排查记录。
model = AutoModel.from_pretrained(snap, local_files_only=True, use_safetensors=False).eval()

class Wrapper(torch.nn.Module):
    """把签名固定成 (input_ids, attention_mask, token_type_ids) → last_hidden_state。

    torch 2.13 的旧导出器会把 transformers 5.x 的额外 kwargs（use_cache）传重，
    直接导出 BertModel 会报 "got multiple values for argument 'use_cache'"。
    """
    def __init__(self, m): super().__init__(); self.m = m
    def forward(self, input_ids, attention_mask, token_type_ids):
        return self.m(input_ids=input_ids, attention_mask=attention_mask,
                      token_type_ids=token_type_ids, return_dict=False)[0]

dummy = tok(["端侧推理为什么省电"], padding="max_length", truncation=True,
            max_length=512, return_tensors="pt")
torch.onnx.export(
    Wrapper(model).eval(),
    (dummy["input_ids"], dummy["attention_mask"], dummy["token_type_ids"]),
    os.path.join(out, "model.onnx"),
    input_names=["input_ids", "attention_mask", "token_type_ids"],
    output_names=["last_hidden_state"],
    dynamic_axes={"input_ids": {0: "batch", 1: "sequence"}, "attention_mask": {0: "batch", 1: "sequence"},
                  "token_type_ids": {0: "batch", 1: "sequence"}, "last_hidden_state": {0: "batch", 1: "sequence"}},
    opset_version=14, do_constant_folding=True, dynamo=False,
)
tok.save_pretrained(out)
# 旧导出器的 TracerWarning 会提示"动态形状可能被固化"——所以必须验证（见 --verify）。
print("model.onnx: %.1f MB" % (os.path.getsize(os.path.join(out, "model.onnx")) / 1e6))
PYEOF
fi

if [ "$INT8" -eq 1 ]; then
    INT8_DIR="$ROOT/.tooling/models/bge-small-zh-v1.5-int8"
    mkdir -p "$INT8_DIR"
    if [ "$FORCE" -eq 0 ] && [ -f "$INT8_DIR/model.onnx" ]; then
        echo "✅ int8 模型已存在：$INT8_DIR"
    else
        echo "int8 动态量化（只量化权重，激活在线量化）..."
        "$PY" - "$OUT/model.onnx" "$INT8_DIR/model.onnx" <<'PYEOF'
import os, sys
from onnxruntime.quantization import quantize_dynamic, QuantType
src, dst = sys.argv[1], sys.argv[2]
quantize_dynamic(src, dst, weight_type=QuantType.QInt8)
print("int8: %.1f MB（fp32 %.1f MB，压缩 %.1f 倍）" % (
    os.path.getsize(dst)/1e6, os.path.getsize(src)/1e6, os.path.getsize(src)/os.path.getsize(dst)))
PYEOF
        cp "$OUT/vocab.txt" "$INT8_DIR/vocab.txt"
    fi
fi

if [ "$VERIFY" -eq 1 ]; then
    echo "与 sentence-transformers 比对（这是"与云端同空间"的证据）..."
    HF_HUB_OFFLINE=1 "$PY" - "$OUT" <<'PYEOF'
import glob, os, sys, numpy as np, onnxruntime as ort
from transformers import AutoTokenizer
from sentence_transformers import SentenceTransformer
out = sys.argv[1]
snap = glob.glob(os.path.expanduser("~/.cache/huggingface/hub/models--BAAI--bge-small-zh-v1.5/snapshots/*/"))[0]
tok = AutoTokenizer.from_pretrained(snap, local_files_only=True)
ref = SentenceTransformer(snap, local_files_only=True, model_kwargs={"use_safetensors": False})
sess = ort.InferenceSession(os.path.join(out, "model.onnx"), providers=["CPUExecutionProvider"])
def enc(batch):
    e = tok(batch, padding=True, truncation=True, max_length=512, return_tensors="np")
    feeds = {i.name: np.asarray(e[i.name]) for i in sess.get_inputs() if i.name in e}
    h = sess.run(None, feeds)[0][:, 0]
    return h / np.clip(np.linalg.norm(h, axis=1, keepdims=True), 1e-12, None)
texts = ["端侧推理为什么省电", "短", "端侧 RAG 把知识索引放在设备上，检索不出网。", "很长" * 100]
got, want = enc(texts), ref.encode(texts, normalize_embeddings=True)
for t, g, w in zip(texts, got, want):
    cos = float(np.dot(g, w))
    print(f"  len={len(t):4d} 维度={g.shape[0]} 余弦={cos:.6f} {'OK' if cos > 0.999 else '❌'}")
# ⚠️ 只比"与参考实现一致"是不够的：两边都用同一份退化权重时也会 1.000000。
# 必须再验**区分度**：无关文本的余弦必须明显小于 1。
d = float(np.dot(got[0], got[2]))
print(f"  区分度检查：'端侧推理为什么省电' vs '端侧 RAG…' 余弦={d:.4f} {'OK' if d < 0.95 else '❌ 向量塌缩（权重有问题）'}")
PYEOF
fi
echo "下一步：bash scripts/android.sh push-model"
