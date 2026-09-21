package com.sekb.ondevice

import com.sekb.shared.model.ChatEvent
import com.sekb.shared.net.SseParser
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * SSE 解析必须**严格对齐服务端**（SEKB `chat_stream`）：契约错一点，端侧就完全收不到答案。
 */
class SseParserTest {

    private fun parseAll(vararg lines: String): List<ChatEvent> {
        val parser = SseParser()
        val out = mutableListOf<ChatEvent>()
        for (l in lines) parser.feed(l)?.let { out.add(it) }
        parser.finish()?.let { out.add(it) }
        return out
    }

    @Test
    fun `parses thinking token and done`() {
        val events = parseAll(
            """data: {"type":"thinking","content":"正在思考..."}""", "",
            """data: {"type":"token","content":"端"}""", "",
            """data: {"type":"token","content":"侧"}""", "",
            """data: {"type":"done","meta":{"conversation_id":"c-1","intent":"chitchat"}}""", "",
        )
        assertEquals(4, events.size)
        assertEquals("正在思考...", (events[0] as ChatEvent.Thinking).content)
        assertEquals("端", (events[1] as ChatEvent.Token).content)
        assertEquals("侧", (events[2] as ChatEvent.Token).content)
        val done = events[3] as ChatEvent.Done
        assertEquals("c-1", done.meta.conversationId)
        assertEquals("chitchat", done.meta.intent)
    }

    @Test
    fun `done carries execution info (S3)`() {
        val events = parseAll(
            """data: {"type":"done","meta":{"execution":{"primary_plane":"edge",""" +
                """"primary_role":"executor","model":"qwen3.5-4b","reason":"edge_preferred",""" +
                """"tier":"default","escalated":0,"by_plane":{"edge":2,"cloud":1},""" +
                """"edge_decided":2,"edge_completed":2,"latency_ms":412.5}}}""",
            "",
        )
        val exec = (events[0] as ChatEvent.Done).meta.execution!!
        assertEquals("edge", exec.primaryPlane)
        assertEquals("qwen3.5-4b", exec.model)
        assertEquals(mapOf("edge" to 2, "cloud" to 1), exec.byPlane)
        assertEquals(412.5, exec.latencyMs, 0.01)
        assertEquals("本机完成", exec.badge())
    }

    @Test
    fun `escalated cloud answer is labelled as such`() {
        val events = parseAll(
            """data: {"type":"done","meta":{"execution":{"primary_plane":"cloud","escalated":1}}}""",
            "",
        )
        assertEquals("已上云（端侧不达标）",
            (events[0] as ChatEvent.Done).meta.execution!!.badge())
    }

    @Test
    fun `ignores comments and unknown fields`() {
        val events = parseAll(
            ": keep-alive", "",
            "event: message", "",
            """data: {"type":"token","content":"x"}""", "",
        )
        assertEquals(1, events.size)
        assertTrue(events[0] is ChatEvent.Token)
    }

    @Test
    fun `error event carries detail`() {
        val events = parseAll("""data: {"type":"error","detail":"该会话正在回复中"}""", "")
        assertEquals("该会话正在回复中", (events[0] as ChatEvent.Error).detail)
    }

    @Test
    fun `non json payload becomes error not silence`() {
        val events = parseAll("data: 这不是 JSON", "")
        assertTrue(events[0] is ChatEvent.Error)
    }

    @Test
    fun `trailing data without blank line is flushed at finish`() {
        val parser = SseParser()
        assertEquals(null, parser.feed("""data: {"type":"token","content":"尾"}"""))
        assertTrue(parser.finish() is ChatEvent.Token)
    }
}
