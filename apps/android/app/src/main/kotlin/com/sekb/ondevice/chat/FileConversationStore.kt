package com.sekb.ondevice.chat

import com.sekb.shared.chat.Conversation
import com.sekb.shared.chat.ConversationCodec
import com.sekb.shared.chat.ConversationStore
import com.sekb.shared.chat.ConversationSummary
import java.io.File

/**
 * 会话落盘的 Android 实现：**每个会话一个 JSON 文件**。
 *
 * ## 为什么不是一个"大 JSON 全都会话"
 *
 * 单文件更简单，但**一次写坏 = 用户所有历史全没**。一文件一会话的失败面小得多：
 * 某个文件损坏只影响那一段对话，其余照常打开。
 *
 * ## 三条刻意的行为
 *
 * 1. **坏文件跳过而不是抛异常**——`list()` 遇到解不开的文件就跳过并计数。
 *    用户宁可少看到一段对话，也不该看到"历史列表打不开"。
 * 2. **上限裁剪**：超过 [MAX_CONVERSATIONS] 段就删最旧的。端侧存储有限，
 *    而"无限增长的聊天记录"是典型的隐性磁盘泄漏。
 * 3. **写盘用临时文件 + 原子重命名**：直接覆写时若进程被杀，会留下半截 JSON
 *    （再次打开就是一段坏数据）。先写 `.tmp` 再 `renameTo` 可避免。
 */
class FileConversationStore(
    private val dir: File,
    private val maxConversations: Int = MAX_CONVERSATIONS,
) : ConversationStore {

    init {
        if (!dir.exists()) dir.mkdirs()
    }

    /** 最近一次 [list] 遇到并跳过的坏文件数（自检/排障用）。 */
    var lastSkippedCorrupt: Int = 0
        private set

    private fun fileOf(id: String) = File(dir, "$id.json")

    override fun list(): List<ConversationSummary> {
        var skipped = 0
        val out = mutableListOf<ConversationSummary>()
        // 只认 "<id>.json"：临时文件（.tmp）与其它杂物一律忽略
        dir.listFiles { f -> f.isFile && f.name.endsWith(".json") }?.forEach { f ->
            val id = f.name.removeSuffix(".json")
            val c = runCatching { ConversationCodec.decode(f.readText(Charsets.UTF_8)) }.getOrNull()
            if (c == null) {
                skipped++
            } else {
                out.add(ConversationSummary(c.id.ifEmpty { id }, c.title, c.updatedAtMillis, c.messages.size))
            }
        }
        lastSkippedCorrupt = skipped
        return out.sortedByDescending { it.updatedAtMillis }
    }

    override fun load(id: String): Conversation? {
        val f = fileOf(id)
        if (!f.isFile) return null
        return runCatching { ConversationCodec.decode(f.readText(Charsets.UTF_8)) }.getOrNull()
    }

    override fun save(c: Conversation) {
        val json = ConversationCodec.encode(c)
        val target = fileOf(c.id)
        val tmp = File(dir, "${c.id}.json.tmp")
        // 先写临时文件再原子改名：直接覆写在进程被杀时会留下半截 JSON
        tmp.writeText(json, Charsets.UTF_8)
        if (!tmp.renameTo(target)) {
            // 有些文件系统 rename 会失败（目标存在）→ 退化为"删了再改名"，仍比直接覆写安全
            target.delete()
            tmp.renameTo(target)
        }
        trim()
    }

    override fun delete(id: String): Boolean = fileOf(id).delete()

    override fun clear() {
        dir.listFiles { f -> f.isFile && f.name.endsWith(".json") }?.forEach { it.delete() }
    }

    /** 只保留最近的 [maxConversations] 段。 */
    private fun trim() {
        val all = list()
        if (all.size <= maxConversations) return
        all.drop(maxConversations).forEach { delete(it.id) }
    }

    companion object {
        /** 50 段对话：按每段几十 KB 估，占用在个位数 MB，对端侧足够。 */
        const val MAX_CONVERSATIONS = 50

        /** 应用私有目录下的固定位置。 */
        fun defaultDir(filesDir: File): File = File(filesDir, "conversations")
    }
}
