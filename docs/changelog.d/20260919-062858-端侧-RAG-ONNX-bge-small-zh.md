## 2026-09-18（端侧 RAG：ONNX 嵌入接入 + 一次"空洞验证"的教训）

- **改动**：
  - `embed/BertWordPieceTokenizer.kt`：自实现 BERT WordPiece（对齐 `tokenizer.json`：
    `lowercase=false`、CJK 逐字、`##` 续接、max 100 字符/词），用 **HuggingFace 金标准 token id**
    做测试（词表 108KB 放 `src/test/resources`，CI 也能跑）。
  - `embed/OnnxBgeEmbedding.kt`：ONNX Runtime 跑 bge-small-zh-v1.5（512 维、CLS pooling + L2），
    空间戳 `BAAI/bge-small-zh-v1.5@512` → **与云端同空间，可与云端结果融合**。
  - `SqliteVectorStore.reembed()` + `openOrReembed()`：换嵌入模型时**原地重算**（不删库）——
    索引里的文本是设备侧唯一副本，RFC §4.5-F 的原话就是"重算"。
  - `scripts/fetch_embedding_model.sh`（离线导出 + 验证）、`scripts/android.sh push-model`。
  - 自检新增：提供者/模型路径/区分度/token 诊断/重嵌入等项，**PASS=20 FAIL=0**。

- **重大排查（RFC §18.6）**：端侧检索出现"任何提问都命中同一篇、余弦恰好 1.000"。
  追下来是我这台 Mac 的 HF 缓存 `model.safetensors` **退化**（`pytorch_model.bin` 正常，
  0.243864，与云端 safetensors 完全一致；云端未受影响）。
  **教训**：主机上"ONNX 与 sentence-transformers 余弦 1.000000"曾被我当成导出成功的证据，
  其实是**空洞验证**——两边用了同一份坏权重。与参照实现一致 ≠ 正确；
  验证必须包含**可被证伪的性质**（现已在导出脚本里加"区分度检查"）。

- **环境约束**：ORT **1.30.0 在模拟器上 SIGILL**（缺 `i8mm`）→ 固定 **1.20.0**；
  模型不能 `adb push` 到外部私有目录（属主 shell、App 读不到）→ 改 `run-as` 写入内部目录。
