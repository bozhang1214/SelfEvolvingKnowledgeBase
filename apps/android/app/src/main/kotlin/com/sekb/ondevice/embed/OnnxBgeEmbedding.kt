package com.sekb.ondevice.embed

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import java.io.File

/**
 * 端侧**真·**嵌入实现：ONNX 版 `bge-small-zh-v1.5`（512 维，CLS pooling + L2 归一化）。
 *
 * 为什么它是"目标实现"而不是"另一个选项"：它的向量空间与 SEKB 云端**完全一致**
 * （`BAAI/bge-small-zh-v1.5@512`），因此端侧检索结果**可以与云端融合**
 * （[Retriever.cloudCompatible] 为 true）。换成别的模型（哪怕维度相同）就不行。
 *
 * 导出与验证方式（可复现，见 `scripts/fetch_embedding_model.sh`）：
 * - 用本机 HF 缓存的权重导出 ONNX（torch 2.13 的旧导出器与 transformers 5.x 有
 *   `use_cache` 参数冲突，所以套一层把签名固定成 (input_ids, attention_mask, token_type_ids)）；
 * - 与 `sentence-transformers` 逐条比对：4 种长度（1/9/36/440 字符）余弦均 **1.000000**，
 *   批量与逐条最大差 1.5e-7 —— 这才敢说"与云端同空间"。
 *
 * ⚠️ 量化（int8）会让向量**轻微变化**：那时空间戳必须改成 `…-int8@512`，
 * 否则就是拿两套不同空间的向量混算（RFC §18.1）。
 */
class OnnxBgeEmbedding(
    modelDir: File,
    private val maxLength: Int = 512,
) : EmbeddingProvider, AutoCloseable {

    override val space: EmbeddingSpace = EmbeddingSpace.SERVER          // 与云端同一空间
    override val isOnDevice: Boolean = true

    private val tokenizer = BertWordPieceTokenizer(File(modelDir, VOCAB_FILE), maxLength)
    private val env: OrtEnvironment = OrtEnvironment.getEnvironment()
    private val session: OrtSession = env.createSession(
        File(modelDir, MODEL_FILE).absolutePath,
        OrtSession.SessionOptions().apply { setIntraOpNumThreads(2) },
    )
    private val inputNames: List<String> = session.inputNames.toList()

    override fun embed(texts: List<String>): List<FloatArray> {
        if (texts.isEmpty()) return emptyList()
        val (ids, mask) = tokenizer.encodeBatch(texts)
        val shape = longArrayOf(ids.size.toLong(), ids[0].size.toLong())
        val tensors = mutableListOf<OnnxTensor>()
        try {
            val feeds = HashMap<String, OnnxTensor>()
            // token_type_ids 恒为 0（sentence-transformers 的模板如此）
            val tokenTypes = Array(ids.size) { LongArray(ids[0].size) }
            for (name in inputNames) {
                val data = when (name) {
                    "input_ids" -> ids
                    "attention_mask" -> mask
                    "token_type_ids" -> tokenTypes
                    else -> throw IllegalStateException("ONNX 模型出现了未预期的输入：$name")
                }
                val t = longTensor(flatten(data), shape)
                tensors.add(t)
                feeds[name] = t
            }
            session.run(feeds).use { result ->
                @Suppress("UNCHECKED_CAST")
                val hidden = result[0].value as Array<Array<FloatArray>>   // [batch][seq][dim]
                return hidden.map { tokens -> l2Normalize(tokens[0].copyOf()) }  // CLS pooling
            }
        } finally {
            tensors.forEach { runCatching { it.close() } }
        }
    }

    /** 诊断用：把一段文本的 token id 暴露出来（"向量都一样"时分清是分词还是推理的锅）。 */
    fun debugTokenIds(text: String): List<Int> = tokenizer.encode(text)

    /** 诊断用：直接用给定 token id 跑一次推理，返回 CLS 向量。 */
    fun debugEmbedIds(ids: LongArray): FloatArray {
        val shape = longArrayOf(1, ids.size.toLong())
        val mask = LongArray(ids.size) { 1L }
        val types = LongArray(ids.size)
        val tensors = mutableListOf<OnnxTensor>()
        try {
            val feeds = HashMap<String, OnnxTensor>()
            for (name in inputNames) {
                val arr = when (name) {
                    "input_ids" -> ids
                    "attention_mask" -> mask
                    else -> types
                }
                val t = longTensor(arr, shape)
                tensors.add(t)
                feeds[name] = t
            }
            session.run(feeds).use { r ->
                @Suppress("UNCHECKED_CAST")
                val hidden = r[0].value as Array<Array<FloatArray>>
                return l2Normalize(hidden[0][0].copyOf())
            }
        } finally {
            tensors.forEach { runCatching { it.close() } }
        }
    }

    override fun close() {
        runCatching { session.close() }
    }

    /**
     * 建 int64 张量。
     *
     * ⚠️ 必须用**直接缓冲**（`allocateDirect`）：用 `LongBuffer.wrap(array)` 这种非直接缓冲时，
     * ORT 拿到的数据可能是空的——症状是"任何输入都产出同一个向量"（检索里表现为
     * 任何查询都命中同一篇且余弦 1.000），而不是报错，非常难查。实测踩到过。
     */
    private fun longTensor(data: LongArray, shape: LongArray): OnnxTensor {
        val buf = java.nio.ByteBuffer.allocateDirect(data.size * 8)
            .order(java.nio.ByteOrder.LITTLE_ENDIAN)
            .asLongBuffer()
        buf.put(data)
        buf.rewind()
        return OnnxTensor.createTensor(env, buf, shape)
    }

    private fun flatten(a: Array<LongArray>): LongArray {
        val out = LongArray(a.size * a[0].size)
        var i = 0
        for (row in a) for (v in row) out[i++] = v
        return out
    }

    private fun l2Normalize(v: FloatArray): FloatArray {
        var sum = 0.0
        for (x in v) sum += x.toDouble() * x
        val norm = Math.sqrt(sum)
        if (norm > 0) for (i in v.indices) v[i] = (v[i] / norm).toFloat()
        return v
    }

    companion object {
        const val MODEL_FILE = "model.onnx"
        const val VOCAB_FILE = "vocab.txt"

        /** 模型文件齐备才可用（缺任一就退回桩，并在 UI/自检里如实标注）。 */
        fun isAvailable(modelDir: File): Boolean =
            File(modelDir, MODEL_FILE).isFile && File(modelDir, VOCAB_FILE).isFile

        private const val REL = "models/bge-small-zh-v1.5"

        /**
         * 候选目录（**内部私有目录优先**）。
         *
         * 为什么不用 `adb push` 直接写外部私有目录：实测 push 进去的文件属主是 `shell`、
         * 目录权限 `drwxrws--- shell:ext_data_rw`，App 不在该组里 → **读不到**
         * （表现为"模型明明在，App 却说没找到"）。所以主路径改成
         * `adb shell run-as <pkg> sh -c 'cat > …'` 写进内部目录（见 `scripts/android.sh push-model`）。
         * 外部目录仍作为候选，方便运行时下载或手动放置。
         */
        fun candidateDirs(context: android.content.Context): List<File> = buildList {
            add(File(context.filesDir, REL))
            context.getExternalFilesDir(null)?.let { add(File(it, REL)) }
        }

        /** 解析实际使用的模型目录：优先"已有模型的那个"，都没有则返回内部目录（等推送/下载）。 */
        fun resolveDir(context: android.content.Context): File {
            val dirs = candidateDirs(context)
            return dirs.firstOrNull { isAvailable(it) } ?: dirs.first()
        }

        /** 诊断用：每个候选目录是否存在模型文件（自检里打印，避免"模型在哪"靠猜）。 */
        fun diagnostics(context: android.content.Context): String =
            candidateDirs(context).joinToString(" | ") { "${it.absolutePath}=${isAvailable(it)}" }
    }
}
