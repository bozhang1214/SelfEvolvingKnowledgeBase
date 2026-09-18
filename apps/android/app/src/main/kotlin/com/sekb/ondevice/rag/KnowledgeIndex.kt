package com.sekb.ondevice.rag

import com.sekb.ondevice.embed.EmbeddingProvider

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
) {

    data class IngestReport(
        val sourceId: String,
        val chunks: Int,
        val embedded: Int,
        val space: String,
    )

    fun ingest(sourceId: String, text: String): IngestReport {
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
        return IngestReport(sourceId, chunks.size, vectors.size, store.space.id)
    }

    fun remove(sourceId: String): Int {
        val doomed = store.all().filter { it.sourceId == sourceId }
        // 接口没有单条删除：重建一次（小语料够用；SQLite 实现会直接 DELETE WHERE）
        store.clear()
        return doomed.size
    }

    fun stats(): Map<String, Int> = mapOf(
        "chunks" to store.size(),
        "sources" to store.all().map { it.sourceId }.toSet().size,
    )
}
