package com.sekb.ondevice.rag

import com.sekb.shared.embed.DeterministicEmbedding
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import com.sekb.shared.rag.*

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
    fun `removing one document keeps the others intact`() {
        // 回归：早期实现用"清空整库"来实现删除，删一篇文档会把整个索引清掉
        val store = InMemoryVectorStore(provider.space)
        val index = KnowledgeIndex(provider, store)
        index.ingest("a", "端侧推理为什么省电")
        index.ingest("b", "北京今天多云转晴")
        index.ingest("c", "端侧 RAG 的隐私优势")

        assertEquals(1, index.remove("b"))
        assertEquals(2, store.size())                                  // 其余两篇必须还在
        assertEquals(listOf("a", "c"), store.all().map { it.sourceId }.sorted())
        assertEquals(0, index.remove("b"))                             // 幂等：再删返回 0
        assertEquals(2, store.size())
    }

    @Test
    fun `removed document is no longer retrievable`() {
        val store = InMemoryVectorStore(provider.space)
        val index = KnowledgeIndex(provider, store)
        index.ingest("keep", "端侧推理为什么省电")
        index.ingest("drop", "端侧 RAG 的隐私优势")
        index.remove("drop")
        val out = Retriever(provider, store, minScore = 0.1).retrieve("端侧 RAG 隐私", topK = 5)
        assertTrue(out.hits.none { it.sourceId == "drop" })
    }

    @Test
    fun `ingest registers the document for the UI`() {
        val store = InMemoryVectorStore(provider.space)
        val reg = InMemoryDocumentRegistry()
        val index = KnowledgeIndex(provider, store, registry = reg, nowMillis = { 1_700_000_000_000 })
        index.ingest("doc-1", "端侧推理为什么省电", name = "笔记.md", sizeBytes = 42, deviceOnly = true)

        val info = reg.get("doc-1")
        assertTrue(info != null)
        assertEquals("笔记.md", info!!.name)
        assertEquals(42L, info.sizeBytes)
        assertEquals(1, info.chunks)
        assertTrue(info.deviceOnly)
        assertEquals(1_700_000_000_000L, info.addedAtMillis)
    }

    @Test
    fun `documents are listed newest first`() {
        val store = InMemoryVectorStore(provider.space)
        var t = 1000L
        val index = KnowledgeIndex(provider, store, registry = InMemoryDocumentRegistry(), nowMillis = { t++ })
        index.ingest("old", "甲")
        index.ingest("new", "乙")
        assertEquals(listOf("new", "old"), index.documents().map { it.id })
    }

    @Test
    fun `removing a document also removes its registry entry`() {
        val store = InMemoryVectorStore(provider.space)
        val reg = InMemoryDocumentRegistry()
        val index = KnowledgeIndex(provider, store, registry = reg)
        index.ingest("a", "端侧推理为什么省电")
        index.ingest("b", "北京今天多云转晴")
        index.remove("a")
        assertEquals(listOf("b"), index.documents().map { it.id })
        assertTrue(reg.get("a") == null)
        assertEquals(1, store.size())
    }

    @Test
    fun `re-ingesting the same id updates instead of duplicating`() {
        val store = InMemoryVectorStore(provider.space)
        val index = KnowledgeIndex(provider, store, registry = InMemoryDocumentRegistry())
        index.ingest("same", "第一版内容。")
        index.ingest("same", "第二版内容，长一些，用来确认登记被更新。")
        assertEquals(1, index.documents().size)
        assertEquals(1, store.size())
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

    @Test
    fun `provider returning fewer vectors is padded instead of crashing`() {
        // 嵌入端口是**跨语言**的（Kotlin ↔ Swift/ArkTS）：契约是"返回同数量"，但各端实现未必守得住。
        // 破坏它的代价原本是**崩溃**（`vector = vectors[i]` 裸索引；iOS 实测 EXC_BREAKPOINT，
        // 栈顶 Kotlin_NSArrayAsKList_get）——比"一条错误消息"严重得多。
        // 现在：补齐零向量 + 在 IngestReport 里报出补齐条数（不允许静默）。
        val short = object : com.sekb.shared.embed.EmbeddingProvider {
            override val space = provider.space
            override val isOnDevice = true
            override fun embed(texts: List<String>): List<FloatArray> =
                texts.take(1).map { provider.embedOne(it) }   // 故意只回一条
        }
        val store = InMemoryVectorStore(provider.space)
        // 显式给小的分块尺寸，让"多片"这件事**确定**发生（默认 maxChars 较大，短文本只会出 1 片，
        // 那样这条测试就成了空跑——本轮实测踩到：第一版用 30 次重复仍只切出 1 片）。
        val index = KnowledgeIndex(short, store, maxChars = 40, overlapChars = 0)
        val report = index.ingest("short", "端侧推理为什么省电。".repeat(30))

        assertTrue("至少要切出多片才验得到这条", report.chunks >= 2)
        assertEquals("补齐条数必须报出来（静默补齐会掩盖真实缺陷）",
            report.chunks - 1, report.zeroFilled)
        assertEquals("全部切片都要入库，不能只剩第一条", report.chunks, store.size())

        // 被补零的切片：与任何向量余弦为 0 ⇒ 必然排在最后且不得分（安全失败，不是"意外命中"）
        val out = Retriever(provider, store, minScore = 0.0).retrieve("端侧推理为什么省电", topK = report.chunks)
        assertEquals("补齐的零向量切片必须垫底", 0.0, out.hits.last().score, 1e-9)
    }
}
