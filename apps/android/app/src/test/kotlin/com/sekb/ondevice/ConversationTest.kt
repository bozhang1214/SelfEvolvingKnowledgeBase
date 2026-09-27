package com.sekb.ondevice

import com.sekb.shared.chat.Conversation
import com.sekb.shared.chat.ConversationCodec
import com.sekb.shared.chat.InMemoryConversationStore
import com.sekb.shared.chat.StoredMessage
import com.sekb.shared.chat.StoredSource
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 会话落盘格式的测试。
 *
 * 最要紧的两条：
 * 1. **旧数据缺字段必须还能读出来**——落盘格式是兼容性契约，
 *    反序列化抛异常对用户就是"历史记录全没了"；
 * 2. **一条坏数据不许拖垮整个列表**（`decode` 返回 null 而不是抛）。
 */
class ConversationTest {

    private fun sample() = Conversation(
        id = "c1",
        title = "端侧推理为什么更省电",
        updatedAtMillis = 1_700_000_000_000,
        messages = listOf(
            StoredMessage(fromUser = true, text = "端侧推理为什么更省电？"),
            StoredMessage(
                fromUser = false,
                text = "因为省掉了数据搬运。",
                plane = "edge",
                model = "qwen3.5-2b",
                escalated = 0,
                reason = "edge_preferred",
                escalateReason = "",
                sources = listOf(StoredSource("doc-rag", 0.69, "端侧 RAG 把索引放在设备上")),
            ),
        ),
    )

    @Test
    fun `round trip preserves everything`() {
        val original = sample()
        val restored = ConversationCodec.decode(ConversationCodec.encode(original))
        assertNotNull(restored)
        assertEquals(original, restored)
    }

    @Test
    fun `sources survive the round trip`() {
        val restored = ConversationCodec.decode(ConversationCodec.encode(sample()))!!
        val assistant = restored.messages.last()
        assertEquals(1, assistant.sources.size)
        assertEquals("doc-rag", assistant.sources[0].sourceId)
        assertEquals(0.69, assistant.sources[0].score, 1e-9)
    }

    /** 兼容性契约：字段缺失/多出都要能读，且**已存的正文不能丢**。 */
    @Test
    fun `old data missing newer fields still loads with all messages`() {
        val legacy = """{"v":1,"id":"old","title":"旧会话","updatedAt":123,
            "messages":[{"fromUser":true,"text":"你好"},
                        {"fromUser":false,"text":"在的"}]}"""
        val c = ConversationCodec.decode(legacy)
        assertNotNull("旧格式必须还能读", c)
        assertEquals(2, c!!.messages.size)
        assertEquals("你好", c.messages[0].text)
        assertEquals("在的", c.messages[1].text)
        // 新字段取默认值，而不是让整段对话作废
        assertEquals("", c.messages[1].plane)
        assertEquals(0, c.messages[1].escalated)
        assertTrue(c.messages[1].sources.isEmpty())
    }

    @Test
    fun `broken data returns null instead of throwing`() {
        for (bad in listOf("", "不是 JSON", "{", "[]", """{"title":"没有 id"}""")) {
            assertNull("输入=$bad 应当返回 null 而不是抛异常", ConversationCodec.decode(bad))
        }
    }

    @Test
    fun `title collapses whitespace and truncates`() {
        assertEquals("新对话", ConversationCodec.deriveTitle("   "))
        assertEquals("你好 世界", ConversationCodec.deriveTitle("你好\n\n  世界"))
        val long = "一".repeat(50)
        val t = ConversationCodec.deriveTitle(long)
        assertEquals(ConversationCodec.MAX_TITLE_CHARS + 1, t.length)   // 含省略号
        assertTrue(t.endsWith("…"))
    }

    @Test
    fun `store lists most recent first and supports latest`() {
        val s = InMemoryConversationStore()
        s.save(sample().copy(id = "a", updatedAtMillis = 100))
        s.save(sample().copy(id = "b", updatedAtMillis = 300))
        s.save(sample().copy(id = "c", updatedAtMillis = 200))

        assertEquals(listOf("b", "c", "a"), s.list().map { it.id })
        assertEquals("b", s.latest()?.id)
        assertEquals(2, s.list().first().messageCount)

        assertTrue(s.delete("b"))
        assertEquals("c", s.latest()?.id)
        s.clear()
        assertTrue(s.list().isEmpty())
        assertNull(s.latest())
    }

    @Test
    fun `saving the same id overwrites rather than duplicating`() {
        val s = InMemoryConversationStore()
        s.save(sample().copy(id = "a", title = "旧标题", updatedAtMillis = 1))
        s.save(sample().copy(id = "a", title = "新标题", updatedAtMillis = 2))
        assertEquals(1, s.list().size)
        assertEquals("新标题", s.load("a")?.title)
    }
}
