package com.sekb.ondevice.chat

import com.sekb.shared.chat.Conversation
import com.sekb.shared.chat.StoredMessage
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.File

/**
 * 文件会话存储的测试（用真实文件系统，不用 mock）。
 *
 * 这里验的是**"重启之后历史还在吗"**这件事本身，而不是某个函数的返回值——
 * 所以关键用例是 [restarting the app still finds the latest conversation]：
 * 它用**新的 store 实例**指向同一目录（等价于杀进程重启）。
 */
class FileConversationStoreTest {

    private lateinit var dir: File

    @Before
    fun setUp() {
        dir = File(System.getProperty("java.io.tmpdir"), "sekb-conv-test-${System.nanoTime()}")
        dir.mkdirs()
    }

    @After
    fun tearDown() {
        dir.deleteRecursively()
    }

    private fun conv(id: String, title: String, at: Long, text: String = "你好") = Conversation(
        id = id,
        title = title,
        updatedAtMillis = at,
        messages = listOf(
            StoredMessage(fromUser = true, text = text),
            StoredMessage(fromUser = false, text = "在的", plane = "edge", model = "qwen3.5-2b"),
        ),
    )

    @Test
    fun `save then load round trips`() {
        val s = FileConversationStore(dir)
        s.save(conv("c1", "第一段", 100))
        val back = s.load("c1")
        assertNotNull(back)
        assertEquals("第一段", back!!.title)
        assertEquals(2, back.messages.size)
        assertEquals("edge", back.messages[1].plane)
    }

    /** 核心用例：等价于"杀掉 App 再打开"。 */
    @Test
    fun `restarting the app still finds the latest conversation`() {
        FileConversationStore(dir).apply {
            save(conv("old", "旧对话", 100))
            save(conv("new", "新对话", 300))
            save(conv("mid", "中间", 200))
        }
        // 新实例 = 重启
        val afterRestart = FileConversationStore(dir)
        assertEquals("新对话", afterRestart.latest()?.title)
        assertEquals(listOf("new", "mid", "old"), afterRestart.list().map { it.id })
        assertEquals(2, afterRestart.latest()?.messages?.size)
    }

    /** 一个坏文件不该让整个历史列表打不开。 */
    @Test
    fun `corrupt file is skipped instead of breaking the list`() {
        val s = FileConversationStore(dir)
        s.save(conv("good", "好会话", 100))
        File(dir, "broken.json").writeText("{ 这不是合法 JSON", Charsets.UTF_8)

        val listed = s.list()
        assertEquals(listOf("good"), listed.map { it.id })
        assertEquals("应当记录跳过了几个坏文件，便于排障", 1, s.lastSkippedCorrupt)
        // 坏的那段单独取也返回 null 而不是抛
        assertNull(s.load("broken"))
    }

    /** 临时文件与其它杂物不许被当成会话。 */
    @Test
    fun `non conversation files are ignored`() {
        val s = FileConversationStore(dir)
        s.save(conv("c1", "会话", 100))
        File(dir, "c1.json.tmp").writeText("半截", Charsets.UTF_8)
        File(dir, "readme.txt").writeText("随便什么", Charsets.UTF_8)
        assertEquals(listOf("c1"), s.list().map { it.id })
    }

    /** 上限裁剪：端侧存储有限，"无限增长的聊天记录"是隐性磁盘泄漏。 */
    @Test
    fun `oldest conversations are trimmed beyond the cap`() {
        val s = FileConversationStore(dir, maxConversations = 3)
        (1..5).forEach { s.save(conv("c$it", "第$it 段", it * 100L)) }
        val ids = s.list().map { it.id }
        assertEquals(listOf("c5", "c4", "c3"), ids)
        assertFalse(File(dir, "c1.json").exists())
        assertFalse(File(dir, "c2.json").exists())
    }

    /** 覆盖保存同一个 id 不该留下重复文件。 */
    @Test
    fun `saving same id overwrites in place`() {
        val s = FileConversationStore(dir)
        s.save(conv("c1", "旧标题", 100))
        s.save(conv("c1", "新标题", 200))
        assertEquals(1, s.list().size)
        assertEquals("新标题", s.load("c1")?.title)
        assertEquals(1, dir.listFiles { f -> f.name.endsWith(".json") }!!.size)
    }

    @Test
    fun `delete and clear remove files`() {
        val s = FileConversationStore(dir)
        s.save(conv("a", "甲", 100))
        s.save(conv("b", "乙", 200))
        assertTrue(s.delete("a"))
        assertFalse(File(dir, "a.json").exists())
        s.clear()
        assertTrue(s.list().isEmpty())
    }

    /** 写盘应留下完整文件、且不留 `.tmp` 残留（否则会被当成杂物或被误读）。 */
    @Test
    fun `saving leaves no temp file behind`() {
        val s = FileConversationStore(dir)
        s.save(conv("c1", "会话", 100))
        val leftovers = dir.listFiles { f -> f.name.endsWith(".tmp") }?.toList().orEmpty()
        assertTrue("不应留下临时文件：$leftovers", leftovers.isEmpty())
    }
}
