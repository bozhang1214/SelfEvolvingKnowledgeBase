// H8（工作流 G）· 聊天流式的**共享层宿主**：SSE 语义、路由决策、失败语义都在这里。
//
// ## 为什么需要这个文件（而不是让 ArkTS 直接读 JSON）
//
// H3 已经把口径钉死（见 `NetPort.kt` 与 `apps/harmony/README.md` 的 H3 一节）：
//
//   * **ArkTS 只搬字节**：HTTP 客户端 + 按 `\n` 切行；
//   * **SSE 语义只有一份**：共享层 `SseParser`（`data:` / 空行分隔 / 事件类型 / `done.meta`）；
//   * **路由决策只有一份**：共享层 `PlaneRouter`（token 估算、预算、原因码、DEVICE_ONLY 硬边界）。
//
// H8 的聊天页如果自己在 ArkTS 里 `JSON.parse` 增量、自己判"本机还是云端"，
// 就等于在鸿蒙侧造了**第二套事实**——那正是本仓库反复踩过的坑
// （H2 的"没断言也会全绿"、H6 的"cos<0.95 就算过"）。
//
// ## 与共享层 `ChatOrchestrator` 的关系（**必须读，否则会误以为这是重复实现**）
//
// `ChatOrchestrator.send()` 是**同步**的，它内部要 `HttpTransport`。
// 而鸿蒙上 HTTP 只能在 ArkTS 侧发（H3 的三条硬事实：无 libcurl、无 OpenSSL、无 C 级 HTTP 头），
// 且 Kotlin 是被 ArkTS **同步**调进来的——Kotlin 若等 ArkTS 的 `await` 就是结构性死锁
// （见 `NetPort.kt` 文件头）。所以这里的形状与 H3 完全一致：
//
//   ArkTS（HTTP + 切行） ──同步 NAPI──▶ Kotlin（**协议/语义/判定**）
//
// 于是"编排"被拆成两半，**没有任何逻辑被写第二遍**：
//
// | 事情 | 谁做 | 是不是共享层 |
// |---|---|---|
// | 决定这次该本机还是云端（预算/原因码/DEVICE_ONLY） | `PlaneRouter.decide` | ✅ 共享层同一份（H8 `route()`） |
// | 解析 SSE 行 → 事件（token/thinking/done/error） | `SseParser` | ✅ 共享层同一份（H8 `feedLine()`） |
// | 拼接正文增量 | 事件里的 `content`，由 `SseParser` 解出 | ✅ 共享层同一份 |
// | 发 HTTP、按 `\n` 切行 | ArkTS `ChatTransport` | 平台传输层（H3 已定） |
// | 端侧失败改道云端 / 前缀守卫 / 上报 | **未接线**（见文件末"未验"） | — |
//
// 也就是说：本文件是"共享层语义 + 平台传输"的**胶水**，胶水里只有字段名适配
// （Ollama 的 OpenAI 兼容 `delta.content` / `delta.reasoning` → 共享契约的 `token` / `thinking`）。
//
// ## 为什么用 Ollama 的 **OpenAI 兼容**端点（`/v1/chat/completions`）而不是 `/api/chat`
//
// `/api/chat` 是 **NDJSON**（一行一个 JSON，没有 `data:` 前缀、没有空行分隔）——
// 那会让"SSE 语义"这件事在鸿蒙侧失效（`SseParser` 收不到事件，等于绕过共享层）。
// `/v1/chat/completions` 是**真正的 SSE**（`data: {json}` + 空行 + `data: [DONE]`），
// 所以行泵喂进去的是标准 SSE 行，"解析"仍然由共享层做。
// 依据：2026-10-26 实测（`curl -N` 原始输出见 `apps/harmony/README.md` H8 一节）。
//
// `reasoning_effort:"none"` 对应共享配置 `EdgeRuntimeConfig.disableThinking = true`
// （理由见该字段注释：同一任务 2283ms → 89ms）。端侧若真的思考，事件也**不会丢**——
// 适配器把 `delta.reasoning` 映射成共享层的 `thinking` 事件，界面显示"思考中"。
@file:OptIn(kotlin.experimental.ExperimentalNativeApi::class)

package com.sekb.ohos.spike

import com.sekb.shared.core.JsonObject
import com.sekb.shared.core.JsonX
import com.sekb.shared.core.nowMillis
import com.sekb.shared.model.ChatEvent
import com.sekb.shared.model.DoneMeta
import com.sekb.shared.net.SseParser
import com.sekb.shared.route.EdgeRuntimeConfig
import com.sekb.shared.route.PlaneRouter

/**
 * 一行流式数据的解析结果（跨 NAPI 返回给 ArkTS 的最小事实集）。
 *
 * 用**手工拼 JSON** 而不是 Kotlin 序列化：这里只有 5 个字段、且不能让 ArkTS 依赖一个新的
 * 序列化库（鸿蒙侧依赖面越小越好）。`JsonObject` 是共享层的那个（转义口径同一份）。
 */
internal class Packet private constructor(private val o: JsonObject) {
    override fun toString(): String = o.toString()

    companion object {
        fun of(kind: String, piece: String = "", done: Boolean = false, error: String = ""): String {
            val o = JsonObject()
            o.put("kind", kind)
            if (piece.isNotEmpty()) o.put("piece", piece)
            if (done) o.put("done", true)
            if (error.isNotEmpty()) o.put("error", error)
            return Packet(o).toString()
        }
    }
}

/**
 * 聊天流式的 SSE 行泵（**共享层 `SseParser` 是唯一的解析者**）。
 *
 * 线程模型：全部由 ArkTS 在 UI 线程同步调用（与 H3 的 `SseLinePump` 同一形状）。
 * 不持有任何 HTTP 状态——传输在 ArkTS。
 *
 * ⚠️ 为什么是 `object` + 文件末那层 `@CName` 顶层包装函数，而不是直接在方法上标 `@CName`：
 * 本 fork 的 `@CName` 只接受**顶层函数**（注解 target 不含 MEMBER_FUNCTION）。
 * 所以 NAPI 入口写成顶层函数，语义仍然只有 `ChatStream` 这一份实现。
 */
object ChatStream {

    /** 当前这一轮的行泵（`begin()` 时重建，避免上一轮的事件混进来造成假绿）。 */
    private var parser: SseParser? = null
    private var kinds: MutableList<String> = mutableListOf()
    private var gotDone: Boolean = false
    private var doneReason: String = ""
    private var errorText: String = ""
    private var pieces: Int = 0
    private var firstEventAt: Long = -1
    private var startedAt: Long = -1

    /** 开始一轮流式：重置计数与解析器。返回 `{"kind":"begin"}`（ArkTS 只用于确认链路）。 */
    fun begin(): String {
        parser = SseParser()
        kinds = mutableListOf()
        gotDone = false
        doneReason = ""
        errorText = ""
        pieces = 0
        firstEventAt = -1
        startedAt = nowMillis()
        return Packet.of("begin")
    }

    /**
     * 喂**一个完整行**（不含换行符）。
     *
     * 流程：① Ollama/OpenAI 的线格式 → 共享契约的线格式（**只有字段名适配**）
     *      ② 逐行进共享层 `SseParser` ③ 把该行解出的事件回报给 ArkTS。
     *
     * ⚠️ 适配器**不发明协议字段**：它只做 `choices[0].delta.content` → `content` 的搬名，
     * `finish_reason` → `type=done`，以及 `data: [DONE]` → 一个 `done` 事件
     * （共享层按契约把非 JSON 当错误，`[DONE]` 是传输层哨兵，不是共享契约的一部分）。
     */
    fun feedLine(line: String): String {
        val p = parser ?: return Packet.of("error", error = "stream_not_started")
        val events = mutableListOf<ChatEvent>()
        val sseLine = adaptToSharedWire(line, events)
        sseLine?.let { l -> p.feed(l)?.let { events += it } }
        return emit(events)
    }

    /**
     * 收尾：流结束时调用（共享层 `SseParser.finish()` 处理最后一段没被空行终止的数据）。
     *
     * 返回的 packet 里 `done=true` 时，ArkTS 应当把气泡定稿（徽章 + 来源）。
     */
    fun finish(): String {
        val p = parser ?: return Packet.of("error", error = "stream_not_started")
        val events = mutableListOf<ChatEvent>()
        p.finish()?.let { events += it }
        return emit(events, finishing = true)
    }

    /**
     * 路由决策（**共享层 `PlaneRouter`**）。
     *
     * 返回 `{"plane":"edge|cloud","reason":"<原因码>","tier":..,"model":..,"inputTokens":..,
     * "outputBudget":..,"deviceOnly":true|false,"blocked":true|false,"blockedReason":".."}`。
     *
     * `reason` 原样透出（**不翻译**）：界面用共享层口径的 `explainReason` 渲染，
     * 认不出的码原样显示 `原因：<code>`——这条纪律三端一致（见两端 `Types.ets`/`PlaneExplain.kt`）。
     */
    fun route(question: String, history: String, deviceData: Boolean, edgeBaseUrl: String,
              sekbBaseUrl: String): String {
        val cfg = EdgeRuntimeConfig(edgeBaseUrl = edgeBaseUrl, sekbBaseUrl = sekbBaseUrl)
        val router = PlaneRouter(cfg)
        val msgs = history.split('\u0000').filter { it.isNotEmpty() } + question
        val decision = router.decide(
            role = "chat",
            messages = msgs,
            // 与编排器同一口径：**不要**传 maxOutputTokens（那等于永远不超预算）
            expectedOutputTokens = null,
            deviceData = deviceData,
        )
        val o = JsonObject()
        o.put("plane", decision.plane.wire)
        o.put("reason", decision.reason)
        o.put("tier", decision.tier)
        o.put("model", router.modelFor(decision.tier))
        o.put("inputTokens", decision.inputTokens)
        o.put("outputBudget", decision.outputBudget)
        o.put("deviceOnly", decision.deviceOnly)
        o.put("blocked", decision.isBlocked())
        o.put("blockedReason", decision.blockedReason.orEmpty())
        return o.toString()
    }

    /** 本轮流式的汇总（自检与界面"来源/事件"审计用）。 */
    fun summary(): String {
        val o = JsonObject()
        o.put("events", kinds.size)
        o.put("kinds", kinds.joinToString("/"))
        o.put("pieces", pieces)
        o.put("done", gotDone)
        o.put("doneReason", doneReason)
        o.put("error", errorText)
        o.put("ttftMillis", if (firstEventAt < 0) -1L else firstEventAt - startedAt)
        o.put("totalMillis", if (startedAt < 0) -1L else nowMillis() - startedAt)
        return o.toString()
    }

    // ────────────────────────── 内部 ──────────────────────────

    private fun emit(events: List<ChatEvent>, finishing: Boolean = false): String {
        if (events.isEmpty()) return Packet.of(if (finishing) "idle" else "none")
        val kindNames = events.map { kindOf(it) }
        kinds += kindNames
        for (e in events) {
            when (e) {
                is ChatEvent.Token -> {
                    pieces++
                    if (firstEventAt < 0) firstEventAt = nowMillis()
                }
                is ChatEvent.Thinking -> if (firstEventAt < 0) firstEventAt = nowMillis()
                is ChatEvent.Done -> gotDone = true
                is ChatEvent.Error -> errorText = e.detail
            }
        }
        // 一行最多解出一个事件（共享契约里一个事件 = 一个空行终止的 data 块），
        // 但接口按"复数"回报，避免将来契约放宽时又要改 NAPI 形状。
        val head = events.first()
        val o = JsonObject()
        o.put("kind", kindNames.joinToString("/"))
        when (head) {
            is ChatEvent.Token -> {
                o.put("piece", head.content)
                o.put("role", "token")
            }
            is ChatEvent.Thinking -> {
                o.put("piece", head.content)
                o.put("role", "thinking")
            }
            is ChatEvent.Done -> {
                o.put("done", true)
                val meta = head.meta
                o.put("conversationId", meta?.conversationId.orEmpty())
                o.put("executionPlane", meta?.execution?.primaryPlane.orEmpty())
                o.put("executionModel", meta?.execution?.model.orEmpty())
                o.put("escalated", meta?.execution?.escalated ?: 0)
                o.put("executionReason", meta?.execution?.reason.orEmpty())
            }
            is ChatEvent.Error -> {
                o.put("error", head.detail)
            }
        }
        return o.toString()
    }

    private fun kindOf(e: ChatEvent): String = when (e) {
        is ChatEvent.Thinking -> "thinking"
        is ChatEvent.Token -> "token"
        is ChatEvent.Done -> "done"
        is ChatEvent.Error -> "error"
    }

    /**
     * 线格式适配：Ollama/OpenAI 的 SSE 行 → **共享契约**的 SSE 行（`data: {"type":...}`）。
     *
     * 为什么必须有这一层：共享契约（`SseParser` + `ChatEvent`）定义的是 SEKB 服务端的
     * `/api/v1/chat/stream` 形状（`type` 为 `thinking|token|done|error`），
     * 而端侧宿主这里连的是本机 Ollama（OpenAI 兼容形状，`choices[0].delta.*`）。
     * **两者只能在一个地方对齐**，否则 ArkTS 与 Kotlin 各理解一半。
     *
     * 返回 null 表示"这一行不是数据行"（注释/心跳/空行本身由共享层处理）。
     *
     * @param out 由适配器**直接产出**的事件（`data: [DONE]` 这条传输层哨兵没有对应 JSON，
     *   共享层按契约会把它当错误——所以在这里翻成 `done` 事件，而不是让共享层猜）。
     */
    private fun adaptToSharedWire(line: String, out: MutableList<ChatEvent>): String? {
        val trimmed = line.trim()
        if (trimmed.isEmpty()) return ""            // 空行 = 事件边界，交给共享层
        if (trimmed.startsWith(":")) return trimmed // 注释/心跳，交给共享层
        if (!trimmed.startsWith("data:")) return trimmed // event:/id:/retry: 本契约不用
        val payload = trimmed.removePrefix("data:").trim()
        if (payload == "[DONE]") {
            // 传输层哨兵：翻成共享契约的 done（空的 DoneMeta，与 SEKB 服务端"不发明字段"一致）
            out += ChatEvent.Done(DoneMeta())
            return null
        }
        // OpenAI 兼容形状 → 共享契约形状
        val obj = JsonX.parseObject(payload) ?: return line   // 解不出就让共享层按契约报错
        val choices = obj.optJSONArray("choices")
        val choice = if (choices != null && choices.length() > 0) choices.optJSONObject(0) else null
        if (choice == null) {
            // 非 chat.completion.chunk（例如 Ollama 的错误体）→ 原样交给共享层按契约处理
            return line
        }
        val delta = choice.optJSONObject("delta")
        val content = JsonX.string(delta, "content")
        val reasoning = JsonX.string(delta, "reasoning")
        val finish = JsonX.string(choice, "finish_reason")
        val type = when {
            content.isNotEmpty() -> "token"
            reasoning.isNotEmpty() -> "thinking"
            finish.isNotEmpty() -> "done"
            else -> return null   // 纯角色帧（delta.role 只有）→ 不产生事件
        }
        val shared = JsonObject()
        shared.put("type", type)
        if (type == "thinking") shared.put("content", reasoning) else shared.put("content", content)
        if (type == "done") {
            // 端侧完成时执行位置由**共享层路由决策**给出（不走服务端），
            // 这里只回传服务端的 `model`/`finish_reason`，plane 由 UI 用 route() 的结果填。
            val meta = JsonObject()
            meta.put("conversation_id", "")
            val exec = JsonObject()
            exec.put("model", JsonX.string(obj, "model"))
            exec.put("primary_plane", "edge")
            meta.put("execution", exec)
            shared.put("meta", meta)
        }
        return "data: ${shared}"
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// NAPI 入口（`@CName` 顶层函数；`sekb_chat_napi.cpp` 按这些 C 符号取用）
//
// 注意符号名前缀 `sekb_chat_`（**不带** `_spike_`）：聊天是 H8 的产品能力，不是探针。
// ─────────────────────────────────────────────────────────────────────────────

/** 路由决策（共享层 `PlaneRouter`）。5 个参数见 C++ 侧同名入口。 */
@CName("sekb_chat_route")
fun sekbChatRoute(
    question: String,
    history: String,
    deviceData: String,
    edgeBaseUrl: String,
    sekbBaseUrl: String,
): String = ChatStream.route(
    question = question,
    history = history,
    deviceData = deviceData == "1" || deviceData == "true",
    edgeBaseUrl = edgeBaseUrl,
    sekbBaseUrl = sekbBaseUrl,
)

/** 开始一轮流式。 */
@CName("sekb_chat_stream_begin")
fun sekbChatStreamBegin(): String = ChatStream.begin()

/** 喂一个完整行（ArkTS 已按 `\n` 切好）。 */
@CName("sekb_chat_stream_line")
fun sekbChatStreamLine(line: String): String = ChatStream.feedLine(line)

/** 流结束收尾（共享层 `SseParser.finish()`）。 */
@CName("sekb_chat_stream_finish")
fun sekbChatStreamFinish(): String = ChatStream.finish()

/** 本轮汇总（事件数/首字延迟；自检与审计用）。 */
@CName("sekb_chat_stream_summary")
fun sekbChatStreamSummary(): String = ChatStream.summary()
