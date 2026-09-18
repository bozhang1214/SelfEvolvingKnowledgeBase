package com.sekb.ondevice.rag

import com.sekb.ondevice.embed.DeterministicEmbedding
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class KnowledgeIndexTest {

    private val provider = DeterministicEmbedding()

    @Test
    fun `ingest chunks and records the real space`() {
        val store = InMemoryVectorStore(provider.space)
        val report = KnowledgeIndex(provider, store)
            .ingest("doc-1", "第一段。\n\n" + "长".repeat(600))

        assertTrue(report.chunks >= 2)
        assertEquals(report.chunks, report.embedded)
        assertEquals(provider.space.id, report.space)   // 空间戳来自实现，不是写死的
        assertEquals(report.chunks, store.size())
    }

    @Test
    fun `empty text ingests nothing`() {
        val store = InMemoryVectorStore(provider.space)
        val report = KnowledgeIndex(provider, store).ingest("doc-empty", "   ")
        assertEquals(0, report.chunks)
        assertEquals(0, store.size())
    }

    @Test
    fun `stats count chunks and distinct sources`() {
        val store = InMemoryVectorStore(provider.space)
        val index = KnowledgeIndex(provider, store)
        index.ingest("a", "甲。")
        index.ingest("b", "乙。")
        val stats = index.stats()
        assertEquals(2, stats["chunks"])
        assertEquals(2, stats["sources"])
    }

    @Test
    fun `vector dim mismatch is rejected loudly`() {
        // 换模型后忘了重建索引时，必须在写入那一刻就炸，而不是等检索出错误结果
        val store = InMemoryVectorStore(provider.space)
        val e = runCatching {
            store.upsert(listOf(VectorRecord("id", "s", "t", FloatArray(3))))
        }.exceptionOrNull()
        assertTrue(e is IllegalArgumentException)
        assertTrue(e!!.message!!.contains("维度不符"))
    }
}
