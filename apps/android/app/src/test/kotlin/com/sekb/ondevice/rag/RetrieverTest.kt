package com.sekb.ondevice.rag

import com.sekb.ondevice.embed.DeterministicEmbedding
import com.sekb.ondevice.embed.EmbeddingSpace
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 端侧检索的三条硬规则（RFC §18.1/§18.3）。
 * 这些规则必须是**代码分支**：写在注释里等于没有。
 */
class RetrieverTest {

    private fun index(
        provider: DeterministicEmbedding = DeterministicEmbedding(),
        space: EmbeddingSpace = provider.space,
    ): Pair<Retriever, VectorStore> {
        val store = InMemoryVectorStore(space)
        val index = KnowledgeIndex(provider, store)
        index.ingest("doc-diet", "端侧推理省电的原因：没有网络传输，也没有云端排队等待。")
        index.ingest("doc-weather", "北京今天多云转晴，最高气温 26 度，适合骑行。")
        index.ingest("doc-rag", "端侧 RAG 把知识索引放在设备上，检索不出网，隐私更好。")
        // ⚠️ 显式给桩一个阈值：`Retriever` 的默认 0.4 是用**真实 ONNX 模型**标定出来的，
        // 而确定性桩的余弦落在更低区间（0.2–0.6）。阈值本来就是模型相关的，测试要把它写出来。
        return Retriever(provider, store, minScore = 0.2) to store
    }

    @Test
    fun `relevant chunk is retrieved first`() {
        val (retriever, _) = index()
        val out = retriever.retrieve("端侧推理为什么省电", topK = 2)
        assertTrue(out.searchable)
        assertTrue(out.hits.isNotEmpty())
        assertEquals("doc-diet", out.hits[0].sourceId)
    }

    @Test
    fun `retrieval does not leak unrelated docs into the top hit`() {
        val (retriever, _) = index()
        val out = retriever.retrieve("北京天气怎么样", topK = 1)
        assertEquals("doc-weather", out.hits[0].sourceId)
    }

    // ---------- 规则 1：空间不一致不许检索 ----------

    @Test
    fun `space mismatch disables retrieval instead of mixing vectors`() {
        // 索引是用 A 模型建的，现在换成 B 模型（维度相同、语义不同）→ 必须拒绝检索。
        // 若"照常算余弦"，返回的是看起来相关、实则错误的结果，比报错更危险。
        val other = DeterministicEmbedding(space = EmbeddingSpace("other-model@256", 256))
        val (retriever, _) = index(provider = other, space = EmbeddingSpace("stub-hash@256", 256))
        val out = retriever.retrieve("端侧推理为什么省电")

        assertFalse(out.searchable)
        assertTrue(out.hits.isEmpty())
        assertTrue(out.reason.startsWith("embedding_space_mismatch"))
    }

    @Test
    fun `server space index is marked cloud compatible`() {
        val serverLike = DeterministicEmbedding(space = EmbeddingSpace.SERVER)
        val (retriever, _) = index(provider = serverLike)
        assertTrue(retriever.indexMatchesProvider())
        assertTrue(retriever.cloudCompatible())          // 与服务端同空间 → 可融合
    }

    @Test
    fun `non server space is explicitly not cloud compatible`() {
        val (retriever, _) = index()                     // stub-hash@64 ≠ 服务端 512
        assertTrue(retriever.indexMatchesProvider())
        assertFalse(retriever.cloudCompatible())          // 本机可检索，但不许与云端融合
        val out = retriever.retrieve("端侧推理")
        assertTrue(out.searchable)
        assertFalse(out.cloudCompatible)
    }

    // ---------- 规则 2：设备专属必须本机嵌入 ----------

    @Test
    fun `device only refuses non on-device embedding provider`() {
        // 宿主 Ollama 的 isOnDevice=false：设备专属数据宁可答不出来，也不能送去外部算
        val remote = DeterministicEmbedding(isOnDevice = false)
        val (retriever, _) = index(provider = remote)
        val out = retriever.retrieve("端侧推理为什么省电", deviceOnly = true)

        assertFalse(out.searchable)
        assertTrue(out.reason.startsWith("device_only_requires_on_device_embedding"))
    }

    @Test
    fun `device only works with on device provider`() {
        val (retriever, _) = index()                      // 默认 isOnDevice=true
        val out = retriever.retrieve("端侧推理为什么省电", deviceOnly = true)
        assertTrue(out.searchable)
        assertTrue(out.hits.isNotEmpty())
    }

    // ---------- 规则 3：空结果/低分如实返回 ----------

    @Test
    fun `empty index returns no hits with honest wording`() {
        val provider = DeterministicEmbedding()
        val retriever = Retriever(provider, InMemoryVectorStore(provider.space))
        val out = retriever.retrieve("任何问题")
        assertTrue(out.searchable && out.hits.isEmpty())
        assertTrue(retriever.formatForModel(out).contains("没有匹配内容"))
    }

    @Test
    fun `low scores are filtered by threshold and reported`() {
        val provider = DeterministicEmbedding()
        val store = InMemoryVectorStore(provider.space)
        // 用"完全无关的提问" + 较高阈值：最相关的那条也会被过滤，且原因要写清楚。
        // （不能拿相同文本测过滤：得分≈1.0，任何阈值都拦不住）
        KnowledgeIndex(provider, store).ingest("d", "端侧推理省电")
        val retriever = Retriever(provider, store, minScore = 0.9)
        val out = retriever.retrieve("完全无关的话题")
        assertTrue(out.hits.isEmpty())
        assertTrue(out.reason.startsWith("below_threshold"))
        assertTrue(retriever.formatForModel(out).contains("没有足够相关"))
    }

    @Test
    fun `format for model is compact and bounded`() {
        val (retriever, _) = index()
        val out = retriever.retrieve("端侧", topK = 5)
        val text = retriever.formatForModel(out, maxChars = 120)
        assertTrue(text.length <= 200)
        assertTrue(text.contains("doc-"))
    }

    @Test
    fun `latency is measured separately for embed and search`() {
        var t = 0L
        val provider = DeterministicEmbedding()
        val store = InMemoryVectorStore(provider.space)
        KnowledgeIndex(provider, store).ingest("d", "端侧推理省电")
        val retriever = Retriever(provider, store, nowMillis = { t.also { t += 3 } })
        val out = retriever.retrieve("端侧")
        assertEquals(3.0, out.embedMillis, 0.001)
        assertEquals(3.0, out.searchMillis, 0.001)
    }
}
