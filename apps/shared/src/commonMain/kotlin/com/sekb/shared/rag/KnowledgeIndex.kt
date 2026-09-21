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
        val vectors = provider.embed(chunks.map { it.text })
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
        return IngestReport(sourceId, chunks.size, vectors.size, store.space.id)
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
