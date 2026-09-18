package com.sekb.ondevice.embed

import com.sekb.ondevice.core.JsonX
import com.sekb.ondevice.net.HttpTransport
import org.json.JSONArray
import org.json.JSONObject

/**
 * 向量空间标识：`<模型>@<维度>`，与 SEKB 服务端的 `EMBEDDING_SPACE` 同一口径
 * （服务端是 `BAAI/bge-small-zh-v1.5@512`）。
 *
 * **为什么把它当类型而不是随手一个字符串**：两个不同模型产出的向量维度可能相同
 * （比如都是 512），但语义空间完全不同——混用不会报错，只会返回"看起来相关但其实错"的结果。
 * 把 id **和** 维度绑在一个值里，比只比维度安全。
 */
data class EmbeddingSpace(val id: String, val dim: Int) {
    companion object {
        /** 服务端的空间标识（RFC §4.5-F；跨端交换是否可复用的唯一判据） */
        const val SERVER_SPACE_ID = "BAAI/bge-small-zh-v1.5@512"
        val SERVER = EmbeddingSpace(SERVER_SPACE_ID, 512)
    }
}

/**
 * 端侧嵌入适配层（可插拔）。
 *
 * **必须能离线**：设备专属数据（RFC §5.2）绝不允许为了"答得更好"把原文送去云端嵌入，
 * 所以 [isOnDevice] 是一个显式属性——检索时用它做闸门，而不是靠调用方自觉。
 */
interface EmbeddingProvider {
    val space: EmbeddingSpace

    /** 是否在设备本机计算。`false`（如宿主 Ollama）时不得用于设备专属数据。 */
    val isOnDevice: Boolean

    /** 批量嵌入；实现不得改变返回顺序与数量。 */
    fun embed(texts: List<String>): List<FloatArray>

    fun embedOne(text: String): FloatArray = embed(listOf(text)).first()
}

/**
 * 确定性桩：**只用于测试**。
 *
 * 三个设计点都是踩出来的（第一版把中文整句当一个 token、且累加全为正 → **余弦被哈希碰撞主导**，
 * 零共享词的文档反而得分更高，检索单测因此假失败）：
 *
 * 1. **中文出单字 + 双字**，不把整句当一个 token——否则那个"整句 token"纯属噪声；
 * 2. **哈希带符号**（±1）：无关维度相互抵消，共享词才会真正抬高相似度；
 * 3. **维度给够**（默认 256）：64 维下碰撞太频繁，排序不可信。
 *
 * 空间 id 带 `stub-`，任何真实数据都不会误用它（也不会通过空间一致性检查）。
 */
class DeterministicEmbedding(
    override val space: EmbeddingSpace = EmbeddingSpace("stub-hash@256", 256),
    override val isOnDevice: Boolean = true,
) : EmbeddingProvider {

    override fun embed(texts: List<String>): List<FloatArray> = texts.map { text ->
        val v = FloatArray(space.dim)
        for (t in tokenize(text)) {
            val h = t.hashCode()
            val sign = if ((h and 0x10000) == 0) 1f else -1f
            v[Math.floorMod(h, space.dim)] += sign
            v[Math.floorMod(h * 31 + 7, space.dim)] += 0.5f * sign
        }
        l2Normalize(v)
        v
    }

    private fun tokenize(text: String): List<String> {
        val out = mutableListOf<String>()
        val run = StringBuilder()

        fun flush() {
            if (run.isEmpty()) return
            val s = run.toString()
            if (s.all { it.code > 0x2E80 }) {          // CJK：单字 + 双字
                for (c in s) out.add(c.toString())
                for (i in 0 until s.length - 1) out.add(s.substring(i, i + 2))
            } else {
                out.add(s.lowercase())                  // 拉丁/数字：整段作为一个词
            }
            run.setLength(0)
        }

        for (c in text) {
            if (c.isLetterOrDigit()) run.append(c) else flush()
        }
        flush()
        return out
    }

    private fun l2Normalize(v: FloatArray) {
        var sum = 0.0
        for (x in v) sum += x.toDouble() * x
        val norm = Math.sqrt(sum)
        if (norm > 0.0) for (i in v.indices) v[i] = (v[i] / norm).toFloat()
    }
}

/**
 * 宿主 Ollama 的嵌入（开发便利，**不是**设备本机）。
 *
 * 用途：端侧 ONNX 模型就位前，先把检索链路跑通。
 * 它的向量空间与云端**通常不同**（例如 `bge-m3@1024`），因此索引里会记下真实空间，
 * 跨端复用时被空间一致性检查拦下（RFC §18.1）。
 */
class HostOllamaEmbedding(
    private val transport: HttpTransport,
    private val baseUrl: String,
    private val model: String,
    private val dim: Int,
    private val apiKey: String = "ollama",
) : EmbeddingProvider {

    override val space: EmbeddingSpace = EmbeddingSpace("$model@$dim", dim)
    override val isOnDevice: Boolean = false

    override fun embed(texts: List<String>): List<FloatArray> {
        val body = JSONObject()
            .put("model", model)
            .put("input", JSONArray().apply { texts.forEach { put(it) } })
            .toString()
        val resp = transport.postJson(
            url = "$baseUrl/embeddings",
            headers = mapOf(
                "Content-Type" to "application/json",
                "Authorization" to "Bearer $apiKey",
            ),
            body = body,
            timeoutSeconds = 120,
        )
        if (!resp.isOk) throw IllegalStateException("嵌入失败：HTTP ${resp.code} ${resp.body.take(120)}")
        val data = JsonX.parseObject(resp.body)?.optJSONArray("data")
            ?: throw IllegalStateException("嵌入响应缺少 data 字段")
        return (0 until data.length()).map { i ->
            val arr = data.optJSONObject(i)?.optJSONArray("embedding")
                ?: throw IllegalStateException("嵌入响应缺少 embedding")
            FloatArray(arr.length()) { j -> arr.optDouble(j, 0.0).toFloat() }
        }
    }
}
