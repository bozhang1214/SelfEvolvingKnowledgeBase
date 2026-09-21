package com.sekb.ondevice.ui

import com.sekb.shared.model.ToolResult
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class RetrievalSourcesTest {

    private fun tool(output: String, ok: Boolean = true) = ToolResult(ok = ok, output = output)

    @Test
    fun `parses sources with scores`() {
        val s = RetrievalSources.parse(
            listOf(tool("[0.696] doc-rag: 端侧 RAG 把知识索引放在设备上\n[0.512] doc-privacy: 设备专属数据永不出端")),
        )
        assertEquals(2, s.size)
        assertEquals("doc-rag", s[0].sourceId)          // 按分数降序
        assertEquals(0.696, s[0].score, 1e-9)
        assertTrue(s[0].snippet.contains("端侧 RAG"))
    }

    @Test
    fun `ignores non matching lines and failed results`() {
        assertTrue(RetrievalSources.parse(listOf(tool("本机资料里没有匹配内容"))).isEmpty())
        assertTrue(RetrievalSources.parse(listOf(tool("[0.9] doc-a: x", ok = false))).isEmpty())
    }

    @Test
    fun `summary is empty when nothing retrieved`() {
        assertEquals("", RetrievalSources.summary(emptyList()))
    }

    @Test
    fun `summary lists sources and scores`() {
        val s = RetrievalSources.parse(listOf(tool("[0.70] doc-a: 甲\n[0.60] doc-b: 乙")))
        val text = RetrievalSources.summary(s)
        assertTrue(text.contains("检索到 2 段"))
        assertTrue(text.contains("doc-a(0.70)"))
    }

    @Test
    fun `tolerates full width colon and leading spaces`() {
        val s = RetrievalSources.parse(listOf(tool("   [0.500] doc-x：中文冒号也要认")))
        assertEquals("doc-x", s[0].sourceId)
    }
}
