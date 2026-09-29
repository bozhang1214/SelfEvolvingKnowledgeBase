package com.sekb.shared.chat

import com.sekb.shared.core.JsonArray
import com.sekb.shared.core.JsonObject
import com.sekb.shared.core.JsonX

/**
 * 一条被持久化的引用来源（端侧 RAG 的可追溯性：答对答错都要能看出处）。
 *
 * 与 UI 层的 `RetrievalSources.Source` 字段一致，但**刻意不共用那个类型**：
 * 那个类型属于界面（可以随手改），而这个属于**落盘格式**（改了就读不出旧数据）。
 * 两者之间做一层显式映射，改 UI 就不会悄悄破坏已存的会话。
 */
data class StoredSource(
    val sourceId: String,
    val score: Double,
    val snippet: String,
)

/**
 * 一条被持久化的消息。
 *
 * ⚠️ **落盘格式属于兼容性契约**：字段可以加（缺省值兜底），但**不许改名或改语义**——
 * 那会让用户已有的会话读不出来。`decode` 对缺失字段一律取默认值，正是为此。
 */
data class StoredMessage(
    val fromUser: Boolean,
    val text: String,
    /** 实际完成的平面（`edge` / `cloud`）；空串表示未知（旧数据） */
    val plane: String = "",
    val model: String = "",
    /** 服务端回传的升级次数（>0 表示"已上云（端侧不达标）"） */
    val escalated: Int = 0,
    /** 路由阶段原因码（如 `edge_preferred`） */
    val reason: String = "",
    /** 升级阶段原因码（如 `degenerate`） */
    val escalateReason: String = "",
    val sources: List<StoredSource> = emptyList(),
)

/** 一次会话（用户视角的"一段对话"）。 */
data class Conversation(
    val id: String,
    val title: String,
    val updatedAtMillis: Long,
    val messages: List<StoredMessage> = emptyList(),
)

/** 会话列表用的轻量摘要（不加载全部消息）。 */
data class ConversationSummary(
    val id: String,
    val title: String,
    val updatedAtMillis: Long,
    val messageCount: Int,
)

/**
 * 会话落盘格式（纯逻辑，可被 JVM 单测直接跑）。
 *
 * ## 为什么手写编解码而不上序列化框架
 *
 * 共享层已经有 `kotlinx-serialization`（`EdgePolicy` 在用），但会话数据的读写频率很低
 * （一次对话一轮一次），而**手写解码能对"旧数据缺字段"给出显式兜底**——
 * 反序列化框架遇到不兼容的旧数据通常直接抛异常，那对用户就是"历史记录全没了"。
 * 这里的选择是：**宁可丢一个字段，也不能丢整段对话**。
 */
object ConversationCodec {

    /** 标题最长 24 个字符：够表达主题，又不至于在列表里折成三行。 */
    const val MAX_TITLE_CHARS = 24

    /**
     * 从第一条用户消息推标题。
     *
     * 空白归一化 + 截断 + 省略号：直接把用户输入当标题会在列表里撑破布局
     * （用户完全可能第一句就粘 500 字）。
     */
    fun deriveTitle(firstUserMessage: String): String {
        val flat = firstUserMessage.replace(Regex("\\s+"), " ").trim()
        if (flat.isEmpty()) return "新对话"
        return if (flat.length <= MAX_TITLE_CHARS) flat else flat.take(MAX_TITLE_CHARS) + "…"
    }

    fun encode(c: Conversation): String {
        val root = JsonObject()
            .put("v", 1)
            .put("id", c.id)
            .put("title", c.title)
            .put("updatedAt", c.updatedAtMillis)
        val arr = JsonArray()
        c.messages.forEach { m ->
            val o = JsonObject()
                .put("fromUser", m.fromUser)
                .put("text", m.text)
                .put("plane", m.plane)
                .put("model", m.model)
                .put("escalated", m.escalated)
                .put("reason", m.reason)
                .put("escalateReason", m.escalateReason)
            if (m.sources.isNotEmpty()) {
                val sa = JsonArray()
                m.sources.forEach { s ->
                    sa.put(JsonObject().put("sourceId", s.sourceId).put("score", s.score).put("snippet", s.snippet))
                }
                o.put("sources", sa)
            }
            arr.put(o)
        }
        root.put("messages", arr)
        return root.toString()
    }

    /** 解析失败返回 `null`（**不抛异常**）：一条坏数据不该让整个历史列表打不开。 */
    fun decode(json: String): Conversation? {
        val root = runCatching { JsonX.parseObject(json) }.getOrNull() ?: return null
        val id = root.optString("id")
        if (id.isEmpty()) return null
        val msgs = mutableListOf<StoredMessage>()
        val arr = root.optJSONArray("messages")
        if (arr != null) {
            for (i in 0 until arr.length()) {
                val o = arr.optJSONObject(i) ?: continue
                val sources = mutableListOf<StoredSource>()
                o.optJSONArray("sources")?.let { sa ->
                    for (j in 0 until sa.length()) {
                        val so = sa.optJSONObject(j) ?: continue
                        sources.add(
                            StoredSource(
                                sourceId = so.optString("sourceId"),
                                score = so.optDouble("score"),
                                snippet = so.optString("snippet"),
                            ),
                        )
                    }
                }
                msgs.add(
                    StoredMessage(
                        fromUser = o.optBoolean("fromUser"),
                        text = o.optString("text"),
                        plane = o.optString("plane"),
                        model = o.optString("model"),
                        escalated = o.optInt("escalated"),
                        reason = o.optString("reason"),
                        escalateReason = o.optString("escalateReason"),
                        sources = sources,
                    ),
                )
            }
        }
        return Conversation(
            id = id,
            title = root.optString("title").ifEmpty { "新对话" },
            updatedAtMillis = runCatching { root.getString("updatedAt").toLong() }.getOrDefault(0L),
            messages = msgs,
        )
    }
}

/**
 * 会话存储端口。
 *
 * 与 `DocumentRegistry` / `VectorStore` 同一套路：`shared` 定接口 + 内存实现，
 * 平台端出落盘实现（Android 用应用私有目录的文件）。
 */
interface ConversationStore {
    /** 按最近更新倒序。 */
    fun list(): List<ConversationSummary>

    fun load(id: String): Conversation?

    fun save(c: Conversation)

    fun delete(id: String): Boolean

    fun clear()

    /** 最近一次更新的会话。 */
    fun latest(): Conversation? = list().firstOrNull()?.let { load(it.id) }

    /**
     * 记住"当前正在进行的会话是哪一个"。
     *
     * 为什么需要它：只按"最近更新"恢复是不够的——用户点了「新对话」之后，那段新会话**是空的、
     * 因而没有落盘**，于是重启时 `latest()` 会把**上一段**对话又拉回来，用户会以为"新对话没生效"。
     * 记住指针后：指针指向的新会话没有文件 → 启动就是干净的空对话（符合用户刚做的动作）。
     */
    fun saveCurrentId(id: String) {}

    /** 当前会话 id；从未记录过返回 null。 */
    fun currentId(): String? = null
}

/** 内存实现（单测与"不落盘"场景）。 */
class InMemoryConversationStore : ConversationStore {
    private val items = LinkedHashMap<String, Conversation>()

    override fun list(): List<ConversationSummary> = items.values
        .map { ConversationSummary(it.id, it.title, it.updatedAtMillis, it.messages.size) }
        .sortedByDescending { it.updatedAtMillis }

    override fun load(id: String): Conversation? = items[id]

    override fun save(c: Conversation) {
        items[c.id] = c
    }

    override fun delete(id: String): Boolean = items.remove(id) != null

    override fun clear() = items.clear()
}
