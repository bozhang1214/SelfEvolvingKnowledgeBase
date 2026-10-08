package com.sekb.shared.rag

import com.sekb.shared.core.nowMillis

import com.sekb.shared.embed.EmbeddingProvider

/**
 * 端侧知识索引：文档 → 切片 → 嵌入 → 入库。
 *
 * 只做"把文本变成可检索的向量"，**不做**文件解析（PDF/Word 解析属于 M3 后续，
 * 那部分要靠 Android 的 SAF 与解析库）。
 */
class KnowledgeIndex(
    private val provider: EmbeddingProvider,
    private val store: VectorStore,
    private val maxChars: Int = Chunking.DEFAULT_MAX_CHARS,
    private val overlapChars: Int = Chunking.DEFAULT_OVERLAP_CHARS,
    /** 文档元信息（UI 列表/删除用）。传 null 时不做登记，纯检索场景够用。 */
    private val registry: DocumentRegistry? = null,
    private val nowMillis: () -> Long = { com.sekb.shared.core.nowMillis() },
) {

    data class IngestReport(
        val sourceId: String,
        val chunks: Int,
        val embedded: Int,
        val space: String,
        /**
         * 因嵌入条数不符而**被补成零向量**的切片数（正常为 0）。
         *
         * 存在的理由：嵌入端口是**跨语言**的（Kotlin ↔ Swift/ArkTS），"返回数量必须一致"
         * 这条契约各端实现未必守得住；一旦少返回，下面的索引就按切片数**越界崩溃**
         * （iOS 实测 `EXC_BREAKPOINT`，栈顶 `Kotlin_NSArrayAsKList_get`）。
         * 这里防御性补齐，并把降级次数**报出来**——静默补齐会掩盖真实缺陷。
         */
        val zeroFilled: Int = 0,
    )

    fun ingest(
        sourceId: String,
        text: String,
        name: String = sourceId,
        sizeBytes: Long = text.length.toLong(),
        deviceOnly: Boolean = true,
    ): IngestReport {
        val chunks = Chunking.split(text, sourceId, maxChars, overlapChars)
        if (chunks.isEmpty()) return IngestReport(sourceId, 0, 0, store.space.id)
        val embeddedRaw = provider.embed(chunks.map { it.text })
        // ⚠️ 防御：**条数不符时补零向量，绝不做 `vectors[i]` 式的裸索引**。
        // 契约是"实现返回同数量"（见 `EmbeddingProvider.embed`），但这条契约跨语言、跨实现，
        // 破坏它的代价是**崩溃**而不是一条错误消息。零向量与任何向量余弦为 0 ⇒ 检索不命中，
        // 属于安全失败；同时把补齐条数放进 `IngestReport.zeroFilled` 供自检断言（不允许静默）。
        val zeroFilled = (chunks.size - embeddedRaw.size).coerceAtLeast(0)
        val vectors: List<FloatArray> =
            if (zeroFilled == 0) embeddedRaw
            else embeddedRaw + List(zeroFilled) { FloatArray(provider.space.dim) }
        store.upsert(
            chunks.mapIndexed { i, c ->
                VectorRecord(
                    id = "$sourceId#${c.index}",
                    sourceId = c.sourceId,
                    text = c.text,
                    vector = vectors[i],
                )
            },
        )
        registry?.upsert(
            DocumentInfo(
                id = sourceId, name = name, sizeBytes = sizeBytes, chunks = chunks.size,
                addedAtMillis = nowMillis(), deviceOnly = deviceOnly,
            ),
        )
        return IngestReport(sourceId, chunks.size, vectors.size, store.space.id, zeroFilled)
    }

    /**
     * 删除一份文档及其全部切片。
     *
     * 这里**只能**调用存储层的 `deleteBySource`：早期版本因为接口没有删除能力，
     * 用"清空整库"糊了过去，删一篇文档会把整个索引清空（其余文档的文本是设备侧唯一副本）。
     */
    fun remove(sourceId: String): Int {
        val removed = store.deleteBySource(sourceId)
        registry?.delete(sourceId)
        return removed
    }

    /**
     * 重嵌入某个文档（换嵌入模型后由 [SqliteVectorStore.reembed] 批量做；这里给单文档用）。
     *
     * 注意：这需要**原文**，而原文只在切片里（已切碎）。所以整库级的换模型重算是
     * 逐切片重算（`reembed`），不是"重新切片"——切片边界不变，只是向量换空间。
     */
    fun documents(): List<DocumentInfo> = registry?.list() ?: emptyList()

    fun stats(): Map<String, Int> = mapOf(
        "chunks" to store.size(),
        "sources" to store.all().map { it.sourceId }.toSet().size,
    )
}
