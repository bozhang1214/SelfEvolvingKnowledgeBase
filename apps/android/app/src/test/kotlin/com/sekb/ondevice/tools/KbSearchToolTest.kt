package com.sekb.ondevice.tools

import com.sekb.ondevice.embed.DeterministicEmbedding
import com.sekb.ondevice.model.ToolCall
import com.sekb.ondevice.rag.InMemoryVectorStore
import com.sekb.ondevice.rag.KnowledgeIndex
import com.sekb.ondevice.rag.Retriever
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** `kb_search` 作为**设备工具**接入：过闸门、走检索、结果可读。 */
class KbSearchToolTest {

    private fun registry(
        onDevice: Boolean = true,
        seed: Boolean = true,
        deviceOnlyCollection: Boolean = false,
    ): ToolRegistry {
        val provider = DeterministicEmbedding(isOnDevice = onDevice)
        val store = InMemoryVectorStore(provider.space)
        if (seed) {
            KnowledgeIndex(provider, store).ingest(
                "doc-rag", "端侧 RAG 把知识索引放在设备上，检索不出网，隐私更好。",
            )
        }
        // 同 RetrieverTest：桩用 0.2，真实模型用标定出来的 0.4
        val retriever = Retriever(provider, store, minScore = 0.2)
        return ToolRegistry(
            tools = listOf(KbSearchTool.create(retriever, deviceOnly = deviceOnlyCollection)),
            checker = PermissionChecker { true },
        )
    }

    @Test
    fun `search returns retrieved snippet text`() {
        val res = registry().execute(ToolCall("kb_search", mapOf("query" to "端侧 RAG 隐私")))
        assertTrue(res.ok)
        assertTrue(res.output.contains("doc-rag"))
    }

    @Test
    fun `missing query is rejected before touching the index`() {
        val res = registry().execute(ToolCall("kb_search"))
        assertFalse(res.ok)
        assertTrue(res.reason.contains("query"))
    }

    @Test
    fun `top_k argument is honoured`() {
        val res = registry().execute(ToolCall("kb_search", mapOf("query" to "端侧", "top_k" to "1")))
        assertTrue(res.ok)
        assertTrue(res.output.lines().count { it.startsWith("[") } <= 1)
    }

    @Test
    fun `empty index answers honestly instead of hallucinating`() {
        val res = registry(seed = false).execute(ToolCall("kb_search", mapOf("query" to "端侧")))
        assertTrue(res.ok)
        assertTrue(res.output.contains("没有匹配内容"))
    }

    @Test
    fun `device only collection refuses non on-device embedding`() {
        // 工具是"设备专属集合"的入口：嵌入非本机时整条检索必须被拒绝
        val res = registry(onDevice = false, deviceOnlyCollection = true)
            .execute(ToolCall("kb_search", mapOf("query" to "端侧")))
        assertFalse(res.ok)
        assertTrue(res.reason.startsWith("device_only_requires_on_device_embedding"))
    }

    @Test
    fun `tool is registered under the name the model is told about`() {
        val r = registry()
        assertTrue(KbSearchTool.NAME in r.names())
        assertTrue(ToolCallJson.schemaPrompt(r.all()).contains(KbSearchTool.NAME))
    }
}
