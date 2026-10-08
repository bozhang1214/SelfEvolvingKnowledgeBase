package com.sekb.ondevice.embed

import com.sekb.ondevice.FakeTransport
import com.sekb.shared.rag.VectorMath
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import com.sekb.shared.embed.*

class EmbeddingProviderTest {

    @Test
    fun `deterministic stub is repeatable and normalized`() {
        val p = DeterministicEmbedding()
        val a = p.embedOne("端侧推理省电")
        val b = p.embedOne("端侧推理省电")
        assertEquals(a.size, p.space.dim)
        assertTrue(a.contentEquals(b))                       // 确定性
        assertEquals(1.0, VectorMath.cosine(a, a), 1e-6)      // 已归一化
    }

    @Test
    fun `shared wording scores higher than unrelated text`() {
        val p = DeterministicEmbedding()
        val q = p.embedOne("端侧推理为什么省电")
        val near = p.embedOne("端侧推理省电的原因是没有网络传输")
        val far = p.embedOne("北京今天多云转晴，适合骑行")
        assertTrue(VectorMath.cosine(q, near) > VectorMath.cosine(q, far))
    }

    @Test
    fun `stub space id can never be mistaken for real data`() {
        assertTrue(DeterministicEmbedding().space.id.startsWith("stub-"))
        assertFalse(DeterministicEmbedding().space == EmbeddingSpace.SERVER)
    }

    @Test
    fun `server space constant matches the server contract`() {
        // 与 SEKB backend/app/core/plane_router.py 的 EMBEDDING_SPACE 同一字符串
        assertEquals("BAAI/bge-small-zh-v1.5@512", EmbeddingSpace.SERVER_SPACE_ID)
        assertEquals(512, EmbeddingSpace.SERVER.dim)
    }

    @Test
    fun `host ollama embedding sends the right request and parses vectors`() {
        val t = FakeTransport()
        t.enqueueJson("""{"data":[{"embedding":[0.1,0.2,0.3]},{"embedding":[0.4,0.5,0.6]}]}""")
        val p = HostOllamaEmbedding(t, "http://10.0.2.2:11434/v1", "bge-m3", dim = 3)

        val vectors = p.embed(listOf("甲", "乙"))

        assertEquals(2, vectors.size)
        assertEquals(3, vectors[0].size)
        assertEquals(0.2f, vectors[0][1], 1e-6f)
        assertTrue(t.lastCall().url.endsWith("/embeddings"))
        val body = JSONObject(t.lastCall().body)
        assertEquals("bge-m3", body.getString("model"))
        assertEquals("甲", body.getJSONArray("input").getString(0))
    }

    @Test
    fun `host ollama embedding is explicitly not on device`() {
        // 这条决定"设备专属数据能不能用它"——必须是 false，否则隐私闸门失效
        val p = HostOllamaEmbedding(FakeTransport(), "http://x/v1", "bge-m3", dim = 3)
        assertFalse(p.isOnDevice)
        assertEquals("bge-m3@3", p.space.id)
    }

    @Test
    fun `host ollama http error degrades to zero vectors instead of throwing`() {
        // 2026-10-09 改：这条原来断言 `embed` **抛** IllegalStateException。那一版被推翻了——
        // 它让同一个接口出现两种失败语义（Apple 侧一直是"捕获→零向量+lastError"，而这里抛），
        // 而且会打断 `KnowledgeIndex.ingest` 的既有不变量（按切片数**索引**嵌入结果）。
        // 现在的契约：**失败返回同数量零向量**，详情进 `lastError`。
        val t = FakeTransport()
        t.enqueueJson("""{"error":"model not found"}""", code = 404)
        val p = HostOllamaEmbedding(t, "http://x/v1", "nope", dim = 3)

        val vectors = p.embed(listOf("甲", "乙"))   // 不抛

        assertEquals("条数必须与输入一致（短数组会让 ingest 下标越界）", 2, vectors.size)
        vectors.forEach { v ->
            assertEquals(3, v.size)
            assertTrue("失败时必须是零向量（余弦 0 ⇒ 检索不命中，安全失败）", v.all { it == 0f })
        }
        assertTrue("失败原因要留痕，否则自检/UI 看不到", p.lastError!!.contains("404"))
    }

    @Test
    fun `host ollama success clears lastError and keeps raw values`() {
        val t = FakeTransport()
        t.enqueueJson("""{"data":[{"embedding":[0.1,0.2,0.3]}]}""")
        val p = HostOllamaEmbedding(t, "http://x/v1", "bge-m3", dim = 3)
        val v = p.embed(listOf("甲"))
        assertEquals(null, p.lastError)
        assertEquals(0.2f, v[0][1], 1e-6f)
    }

    @Test
    fun `host ollama count mismatch is treated as failure, not as a short list`() {
        // 服务端少回一条：绝不能把短列表交给调用方（ingest 会按切片数索引）
        val t = FakeTransport()
        t.enqueueJson("""{"data":[{"embedding":[0.1,0.2,0.3]}]}""")
        val p = HostOllamaEmbedding(t, "http://x/v1", "bge-m3", dim = 3)
        val vectors = p.embed(listOf("甲", "乙", "丙"))
        assertEquals(3, vectors.size)
        assertTrue(p.lastError!!.contains("条数"))
    }
}
