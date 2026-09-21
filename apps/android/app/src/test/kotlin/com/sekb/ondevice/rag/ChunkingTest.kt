package com.sekb.ondevice.rag

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import com.sekb.shared.rag.*

class ChunkingTest {

    @Test
    fun `short text stays one chunk`() {
        val chunks = Chunking.split("端侧推理省电，因为少了网络传输。", "doc-1")
        assertEquals(1, chunks.size)
        assertEquals("doc-1", chunks[0].sourceId)
        assertEquals(0, chunks[0].index)
    }

    @Test
    fun `paragraphs are kept whole when they fit`() {
        val text = "第一段。\n\n第二段。\n\n第三段。"
        val chunks = Chunking.split(text, "d", maxChars = 100)
        assertEquals(1, chunks.size)   // 三段加起来没超上限 → 合成一块
        assertTrue(chunks[0].text.contains("第二段"))
    }

    @Test
    fun `long paragraph is hard split with overlap`() {
        val text = "甲".repeat(100)
        val chunks = Chunking.split(text, "d", maxChars = 40, overlapChars = 10)
        assertEquals(3, chunks.size)                     // 40 + 30 + 30（含重叠）
        assertTrue(chunks.all { it.text.length <= 40 })
    }

    @Test
    fun `overlap keeps the seam inside a chunk`() {
        // 重叠的意义：答案正好被切在缝上时，仍能被完整检索到
        val text = "0123456789".repeat(10)   // 100 字符
        val chunks = Chunking.split(text, "d", maxChars = 40, overlapChars = 10)
        val tail = chunks[0].text.takeLast(10)
        assertTrue(chunks[1].text.startsWith(tail))
    }

    @Test
    fun `blank input yields no chunks`() {
        assertTrue(Chunking.split("   \n\n  \n", "d").isEmpty())
    }

    @Test
    fun `chunk indexes are sequential`() {
        val chunks = Chunking.split("段一。\n\n段二。", "d", maxChars = 4, overlapChars = 1)
        assertEquals(chunks.indices.toList(), chunks.map { it.index })
    }

    @Test
    fun `invalid parameters are rejected loudly`() {
        val e = runCatching { Chunking.split("x", "d", maxChars = 10, overlapChars = 10) }.exceptionOrNull()
        assertTrue(e is IllegalArgumentException)
    }
}
