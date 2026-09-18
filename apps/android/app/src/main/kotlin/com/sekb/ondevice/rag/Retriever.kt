package com.sekb.ondevice.rag

import com.sekb.ondevice.embed.EmbeddingProvider
import com.sekb.ondevice.embed.EmbeddingSpace
import com.sekb.ondevice.model.ToolResult

/**
 * 检索结果。
 *
 * 把"能不能检索"与"能不能跨端复用"分成两个字段，是因为它们是**两件不同的事**
 * （RFC §18.1）：
 * - [searchable]：索引里的向量与当前嵌入模型是否同一空间 → 不同就**根本不能检索**（必须重建索引）；
 * - [cloudCompatible]：索引空间与云端空间是否一致 → 不一致只影响"能否与云端结果融合"，
 *   本机检索照常。
 */
data class RetrievalOutcome(
    val hits: List<VectorHit>,
    val space: EmbeddingSpace,
    val searchable: Boolean,
    val cloudCompatible: Boolean,
    val reason: String = "",
    val embedMillis: Double = 0.0,
    val searchMillis: Double = 0.0,
) {
    val isEmpty: Boolean get() = hits.isEmpty()
}

/**
 * 端侧检索器：查询 → 嵌入 → top-k → 阈值过滤。
 *
 * 三条硬规则（都做成代码分支，不靠调用方自觉）：
 *
 * 1. **空间不一致不许检索**：索引是用别的模型建的就直接返回不可检索 + 原因，
 *    而不是"用不同空间的向量算余弦"——那会返回看似相关、实则错误的结果。
 * 2. **设备专属数据必须用本机嵌入**：[retrieve] 的 `deviceOnly=true` 时，
 *    [EmbeddingProvider.isOnDevice] 为 false 一律拒绝（宁可说"本机资料里没有"）。
 * 3. **低分/空结果如实返回**：不为了让调用方满意而把不相关的内容塞进上下文；
 *    空结果是升级信号（非专属数据可上云），由编排器决定。
 */
class Retriever(
    private val provider: EmbeddingProvider,
    private val store: VectorStore,
    /**
     * 相似度阈值：低于它的命中直接丢弃。
     *
     * ⚠️ **默认值只是保守起点，必须用标注集校准**（RFC §18.4）：不同嵌入模型的余弦可比区间
     * 完全不同（真实 bge 上"相关"常在 0.4–0.7，确定性桩则在 0.2–0.6）。
     */
    private val minScore: Double = 0.2,
    /** 云端向量空间（用于判断能否跨端融合；默认取服务端口径） */
    private val cloudSpace: EmbeddingSpace = EmbeddingSpace.SERVER,
    private val nowMillis: () -> Long = { System.currentTimeMillis() },
) {

    /** 索引空间与当前嵌入模型是否一致——不一致必须重建索引。 */
    fun indexMatchesProvider(): Boolean = store.space == provider.space

    /** 索引空间与云端是否一致——决定能否与云端检索结果融合。 */
    fun cloudCompatible(): Boolean = store.space == cloudSpace

    fun retrieve(query: String, topK: Int = 5, deviceOnly: Boolean = false): RetrievalOutcome {
        if (!indexMatchesProvider()) {
            return RetrievalOutcome(
                hits = emptyList(),
                space = store.space,
                searchable = false,
                cloudCompatible = cloudCompatible(),
                reason = "embedding_space_mismatch:索引=${store.space.id} 当前模型=${provider.space.id}" +
                    "（换模型必须重建索引，绝不能混用）",
            )
        }
        if (deviceOnly && !provider.isOnDevice) {
            return RetrievalOutcome(
                hits = emptyList(),
                space = store.space,
                searchable = false,
                cloudCompatible = cloudCompatible(),
                reason = "device_only_requires_on_device_embedding:当前嵌入=${provider.space.id} 非本机计算",
            )
        }

        val t0 = nowMillis()
        val vector = try {
            provider.embedOne(query)
        } catch (e: Exception) {
            return RetrievalOutcome(
                hits = emptyList(), space = store.space, searchable = true,
                cloudCompatible = cloudCompatible(),
                reason = "embed_failed:${e.message?.take(80) ?: "unknown"}",
            )
        }
        val t1 = nowMillis()
        val raw = store.search(vector, topK)
        val t2 = nowMillis()

        return RetrievalOutcome(
            hits = raw.filter { it.score >= minScore },
            space = store.space,
            searchable = true,
            cloudCompatible = cloudCompatible(),
            reason = if (raw.isNotEmpty() && raw.all { it.score < minScore }) {
                "below_threshold:最高分=${"%.3f".format(raw.first().score)}<$minScore"
            } else {
                ""
            },
            embedMillis = (t1 - t0).toDouble(),
            searchMillis = (t2 - t1).toDouble(),
        )
    }

    /** 供模型消费的紧凑文本（不要把整段原文一股脑塞进上下文）。 */
    fun formatForModel(outcome: RetrievalOutcome, maxChars: Int = 1200): String {
        if (!outcome.searchable) return "本机检索不可用：${outcome.reason}"
        if (outcome.hits.isEmpty()) {
            return if (outcome.reason.startsWith("below_threshold")) {
                "本机资料里没有足够相关的内容（${outcome.reason}）"
            } else {
                "本机资料里没有匹配内容"
            }
        }
        val sb = StringBuilder()
        var used = 0
        for (h in outcome.hits) {
            val line = "[${"%.3f".format(h.score)}] ${h.sourceId}: ${h.text.replace("\n", " ")}\n"
            if (used + line.length > maxChars) break
            sb.append(line)
            used += line.length
        }
        return sb.toString().trim()
    }

    companion object {
        /** 检索结果 → 工具结果（`kb_search` 用）。 */
        fun toToolResult(outcome: RetrievalOutcome, retriever: Retriever): ToolResult = when {
            !outcome.searchable -> ToolResult(ok = false, denied = false, reason = outcome.reason)
            outcome.hits.isEmpty() -> ToolResult(ok = true, output = retriever.formatForModel(outcome))
            else -> ToolResult(ok = true, output = retriever.formatForModel(outcome))
        }
    }
}
