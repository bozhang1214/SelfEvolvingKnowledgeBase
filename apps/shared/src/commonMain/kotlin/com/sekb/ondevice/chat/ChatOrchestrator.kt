package com.sekb.ondevice.chat

import com.sekb.ondevice.core.nowMillis

import com.sekb.ondevice.core.Ids

import com.sekb.ondevice.edge.ChatMessage
import com.sekb.ondevice.edge.EdgeLlm
import com.sekb.ondevice.model.ExecutionInfo
import com.sekb.ondevice.model.Plane
import com.sekb.ondevice.model.RouteDecision
import com.sekb.ondevice.model.RouteEventPayload
import com.sekb.ondevice.model.ToolResult
import com.sekb.ondevice.route.EdgeRuntimeConfig
import com.sekb.ondevice.route.PlaneRouter
import com.sekb.ondevice.route.StreamGuard
import com.sekb.ondevice.tools.ToolCallJson
import com.sekb.ondevice.tools.ToolRegistry

/** 云端一次聊天的回执：执行位置 + 会话 ID（会话 ID 必须回流，否则每轮都是新会话）。 */
data class CloudReply(val execution: ExecutionInfo?, val conversationId: String?)

/** 云端聊天（SEKB SSE）。抽成接口是为了让编排器能在 JVM 单测里跑通全流程。 */
fun interface CloudChat {
    fun stream(
        message: String,
        conversationId: String?,
        onToken: (String) -> Unit,
        onThinking: (String) -> Unit,
    ): CloudReply
}

/** 一轮对话的结果。 */
data class ChatOutcome(
    val text: String,
    /** **最终**在哪完成的（用户看到的那次） */
    val plane: Plane,
    val decision: RouteDecision,
    /** 本次命中过的升级信号（端侧阶段） */
    val signals: List<String>,
    val escalated: Boolean,
    val escalateReason: String,
    /** 云端返回的执行位置；端侧完成时为本地构造的一条 */
    val execution: ExecutionInfo,
    val toolResults: List<ToolResult> = emptyList(),
    /** 模型这一轮是否**试图**调用工具（判据见 [ToolCallJson.looksLikeAttempt]） */
    val toolCallAttempted: Boolean = false,
    /** 试图调用且 JSON/工具名都合法 → 计入"工具调用 JSON 合法率"的分子 */
    val toolCallLegal: Boolean = false,
    /** 端侧首字耗时（ms）；走云端时为 0 */
    val edgeTtftMillis: Double = 0.0,
    /** 云端会话 ID（走端侧完成时为 null）：下一轮要带回去才能连续对话 */
    val conversationId: String? = null,
    val error: String = "",
)

/**
 * 端云编排器：**决策 → 端侧流式（带前缀守卫）→ 需要时改道云端 → 上报**。
 *
 * 这是客户端侧对应服务端 `RoutedLLM.ainvoke` 的那一层，但多了一件服务端做不到的事：
 * 服务端只能"整段重做"，而端侧能**在流式的第一小段就发现不对并改道**——
 * 因为改道发生在用户看到任何字符之前，所以不需要"已输出多少字符"的续写协议。
 *
 * 同步实现（调用方决定线程）：这样编排逻辑可以在纯 JVM 单测里被完整验证。
 */
class ChatOrchestrator(
    private val config: EdgeRuntimeConfig,
    private val router: PlaneRouter,
    private val edgeLlm: EdgeLlm,
    private val cloud: CloudChat,
    private val tools: ToolRegistry,
    private val reporter: (RouteEventPayload) -> Unit = {},
    private val now: () -> Long = { nowMillis() },
    private val newId: () -> String = { Ids.short() },
) {

    init {
        require(config.guardChars >= 0) { "guardChars 不能为负" }
    }

    fun send(
        userMessage: String,
        history: List<ChatMessage> = emptyList(),
        conversationId: String? = null,
        deviceData: Boolean = false,
        role: String = "chat",
        onToken: (String) -> Unit = {},
        onThinking: (String) -> Unit = {},
    ): ChatOutcome {
        val decision = router.decide(
            role = role,
            messages = (history + ChatMessage.user(userMessage)).map { it.content },
            expectedOutputTokens = null,      // 用角色默认表；**不要**用 maxOutputTokens（那等于永远不超预算）
            deviceData = deviceData,
        )

        // R10：DEVICE_ONLY 数据 + 非本机端侧端点 → **一个请求都不发**（既不端侧也不云端）。
        // 上报一条留痕，并把原因直接告诉用户——端侧方案里"说清为什么不行"比"静默降级"重要。
        if (decision.isBlocked()) {
            val reason = decision.blockedReason.orEmpty()
            report(
                decision = decision, plane = Plane.EDGE, model = "",
                inputText = userMessage, outputText = "",
                signals = listOf(reason), escalated = false,
                escalateReason = "escalation_blocked:$reason",
                latencyMillis = 0.0, role = role,
            )
            return ChatOutcome(
                text = "", plane = Plane.EDGE, decision = decision,
                signals = listOf(reason), escalated = false,
                escalateReason = "escalation_blocked:$reason",
                execution = localExecution(decision, "", 0.0, escalated = false, signals = listOf(reason)),
                conversationId = conversationId,
                error = "设备专属数据不能发往非本机端点（$reason）；本机未配置可用的端侧运行时",
            )
        }

        return if (decision.isEdge) {
            runEdge(userMessage, history, conversationId, decision, role, onToken, onThinking)
        } else {
            val started = now()
            val text = StringBuilder()
            val reply = cloud.stream(userMessage, conversationId,
                { piece -> text.append(piece); onToken(piece) }, onThinking)
            report(
                decision = decision, plane = Plane.CLOUD, model = "",
                inputText = userMessage, outputText = text.toString(),
                signals = emptyList(), escalated = false, escalateReason = "",
                latencyMillis = (now() - started).toDouble(), role = role,
            )
            ChatOutcome(
                text = text.toString(), plane = Plane.CLOUD, decision = decision,
                signals = emptyList(), escalated = false, escalateReason = "",
                execution = reply.execution ?: cloudExecution(decision),
                conversationId = reply.conversationId ?: conversationId,
            )
        }
    }

    private fun runEdge(
        userMessage: String,
        history: List<ChatMessage>,
        conversationId: String?,
        decision: RouteDecision,
        role: String,
        onToken: (String) -> Unit,
        onThinking: (String) -> Unit,
    ): ChatOutcome {
        val model = router.modelFor(decision.tier)
        val guard = StreamGuard(
            router = router,
            guardChars = config.guardChars,
            responseFormat = null,
            availableTools = tools.names(),
        )
        val visible = StringBuilder()      // 真正吐给用户的内容（守卫放行后才写入）
        var escalatedBy: List<String> = emptyList()
        var released = false

        val messages = buildMessages(history, userMessage)

        val edgeStarted = now()
        var firstPieceAt = 0L
        val completion = try {
            edgeLlm.streamChat(model, messages, config.maxOutputTokens, jsonMode = false) { piece ->
                // 首字耗时必须由**编排器自己**量：端侧 TTFT 是 `timeout` 升级信号的判据，
                // 而信号的判定发生在守卫内部——不喂给它，这条信号就是死代码（踩过）。
                if (firstPieceAt == 0L) {
                    firstPieceAt = now()
                    guard.noteFirstToken((firstPieceAt - edgeStarted).toDouble())
                }
                when (val d = guard.offer(piece)) {
                    is StreamGuard.Decision.Buffering -> Unit
                    is StreamGuard.Decision.Release -> {
                        released = true
                        visible.append(d.prefix)
                        onToken(d.prefix)
                    }
                    is StreamGuard.Decision.Passthrough -> {
                        visible.append(piece)
                        onToken(piece)
                    }
                    is StreamGuard.Decision.Escalate -> {
                        escalatedBy = d.signals
                        // 中断端侧流：已经判定要改道，继续生成纯属浪费算力。
                        // 抛出的异常会被下面的 catch 吃掉并返回部分结果。
                        throw EarlyEscalation(d.signals)
                    }
                }
            }
        } catch (e: EarlyEscalation) {
            escalatedBy = e.signals
            null
        }

        val edgeOk = completion?.ok ?: true

        // 端侧端点本身失败（网络/HTTP）→ 先判这一条。
        // 顺序很重要：如果先跑守卫的整段评估，空输出会被判成 `empty`，
        // 于是"端点连不上"被误报成"模型答了空"——排查时会南辕北辙。
        if (completion != null && !edgeOk && !released) {
            escalatedBy = listOf(SIGNAL_EDGE_UNAVAILABLE)
        }

        // 流结束但一直没到阈值 → 退化为"整段评估"
        if (completion != null && escalatedBy.isEmpty() && !released) {
            when (val d = guard.finish()) {
                is StreamGuard.Decision.Release -> {
                    released = true
                    visible.append(d.prefix)
                    onToken(d.prefix)
                }
                is StreamGuard.Decision.Escalate -> escalatedBy = d.signals
                else -> Unit
            }
        }

        // 说明：若已经吐给用户一部分（released），即使端侧失败也不改道——
        // 那会造成"前半段端侧 + 后半段云端"的重复，不如如实返回部分内容 + error。
        val edgeText = completion?.text ?: visible.toString()
        if (escalatedBy.isEmpty() && !released) {
            val toEvaluate = if (completion == null) "" else edgeText
            escalatedBy = router.evaluate(toEvaluate, availableTools = tools.names())
        }

        // 隐私硬边界：DEVICE_ONLY 数据**永不出端**，宁可承认失败（RFC §5.2）
        if (escalatedBy.isNotEmpty() && !decision.escalationAllowed()) {
            // 端侧不达标但数据不可出端：把守卫缓冲里的内容**留下来**给用户。
            // 丢弃它（第一版就是这么写的）会返回空回答——比"答得不好"更糟。
            val kept = when {
                visible.isNotEmpty() -> visible.toString()
                guard.bufferedText().isNotEmpty() -> guard.bufferedText()
                else -> edgeText
            }
            report(
                decision = decision, plane = Plane.EDGE, model = model,
                inputText = userMessage, outputText = kept,
                signals = escalatedBy, escalated = false,
                escalateReason = "escalation_blocked:device_only",
                latencyMillis = completion?.totalMillis ?: 0.0, role = role,
            )
            if (kept.isNotEmpty()) onToken(kept)
            return ChatOutcome(
                text = kept,
                plane = Plane.EDGE, decision = decision, signals = escalatedBy,
                escalated = false,
                escalateReason = "escalation_blocked:device_only",
                execution = localExecution(decision, model, completion?.ttftMillis ?: 0.0, escalated = false, signals = escalatedBy),
                conversationId = conversationId,
                error = if (edgeOk) "" else completion?.error.orEmpty(),
            )
        }

        if (escalatedBy.isNotEmpty()) {
            // 改道云端：把"端侧已生成的开头"作为背景交给云端，而不是从零开始
            val handoffMessage = Handoff.wrap(escalatedBy, edgeText, userMessage)
            val started = now()
            val sb = StringBuilder()
            val reply = cloud.stream(handoffMessage, conversationId,
                { piece -> sb.append(piece); onToken(piece) }, onThinking)
            report(
                decision = decision, plane = Plane.CLOUD, model = model,
                inputText = userMessage, outputText = sb.toString(),
                signals = escalatedBy, escalated = true,
                escalateReason = escalatedBy.joinToString(","),
                latencyMillis = (now() - started).toDouble(), role = role,
            )
            return ChatOutcome(
                text = sb.toString(), plane = Plane.CLOUD, decision = decision,
                signals = escalatedBy, escalated = true,
                escalateReason = escalatedBy.joinToString(","),
                execution = reply.execution ?: cloudExecution(decision),
                edgeTtftMillis = completion?.ttftMillis ?: 0.0,
                conversationId = reply.conversationId ?: conversationId,
            )
        }

        // 端侧完成：继续做最多一轮工具调用（ReAct 单步）
        val finalText = if (visible.isNotEmpty()) visible.toString() else edgeText
        val toolRound = runToolRound(finalText, messages, decision, model)
        val finalOut = toolRound.text.ifEmpty { finalText }
        report(
            decision = decision, plane = Plane.EDGE, model = model,
            inputText = userMessage, outputText = finalOut,
            signals = emptyList(), escalated = false, escalateReason = "",
            latencyMillis = completion?.totalMillis ?: 0.0, role = role,
        )
        // 只有真的做了工具轮，才有"第二次回答"需要补发；
        // 否则 toolRound.text 就是刚才那一段，再吐一次会让用户看到重复内容。
        if (toolRound.attempted && toolRound.text.isNotEmpty()) onToken(toolRound.text)
        return ChatOutcome(
            text = toolRound.text.ifEmpty { finalText },
            plane = Plane.EDGE, decision = decision, signals = emptyList(),
            escalated = false, escalateReason = "",
            execution = localExecution(decision, model, completion?.ttftMillis ?: 0.0,
                escalated = false, signals = emptyList()),
            toolResults = toolRound.results,
            toolCallAttempted = toolRound.attempted,
            toolCallLegal = toolRound.legal,
            conversationId = conversationId,
            edgeTtftMillis = completion?.ttftMillis ?: 0.0,
            error = if (edgeOk) "" else completion?.error.orEmpty(),
        )
    }

    /**
     * 单步工具轮：模型输出形如 `{"tool": ...}` → 过闸门执行 → 把结果回灌再问一次。
     *
     * 只做**一轮**：端侧 2B/4B 的多轮 ReAct 很容易越滚越偏，而"设备信息类问题"一轮足够
     * （先查、再答）。多轮编排留给 M3 的评测结论来决定。
     */
    private fun runToolRound(
        firstAnswer: String,
        baseMessages: List<ChatMessage>,
        decision: RouteDecision,
        model: String,
    ): ToolRound {
        val attempted = ToolCallJson.looksLikeAttempt(firstAnswer, tools.names())
        val parsed = ToolCallJson.parse(firstAnswer)
        if (parsed !is ToolCallJson.Parsed.Ok) {
            return ToolRound(text = firstAnswer, results = emptyList(),
                attempted = attempted, legal = false)
        }
        val result = tools.execute(parsed.call)
        val followUp = baseMessages + ChatMessage.assistant(firstAnswer) +
            ChatMessage.user(
                "工具结果如下（${ToolCallJson.resultForModel(result)}）。" +
                    "请基于它用中文简洁回答用户最初的问题，不要再输出 JSON。",
            )
        val completion = edgeLlm.complete(model, followUp, config.maxOutputTokens, jsonMode = false)
        return ToolRound(
            text = completion.text.ifBlank { firstAnswer },
            results = listOf(result),
            attempted = true,
            legal = true,          // 解析成 Ok 即"合法"；工具名是否存在由闸门判（幻觉会 denied）
        )
    }

    private fun buildMessages(history: List<ChatMessage>, userMessage: String): List<ChatMessage> {
        val system = buildString {
            appendLine("你是运行在用户设备上的端侧助手。回答要简洁、准确、用中文。")
            appendLine("涉及本机信息（时间、网络状态、联系人）时，先用工具查，不要编造。")
            append(ToolCallJson.schemaPrompt(tools.all()))
        }
        return listOf(ChatMessage.system(system)) + history + ChatMessage.user(userMessage)
    }

    private fun localExecution(
        decision: RouteDecision,
        model: String,
        ttftMillis: Double,
        escalated: Boolean,
        signals: List<String>,
    ) = ExecutionInfo(
        primaryPlane = Plane.EDGE.wire,
        primaryRole = "chat",
        model = model,
        reason = decision.reason,
        tier = decision.tier,
        escalated = if (escalated) 1 else 0,
        byPlane = mapOf(Plane.EDGE.wire to 1),
        edgeDecided = 1,
        edgeCompleted = if (escalated) 0 else 1,
        latencyMs = ttftMillis,
    )

    private fun cloudExecution(decision: RouteDecision) = ExecutionInfo(
        primaryPlane = Plane.CLOUD.wire,
        primaryRole = "chat",
        reason = decision.reason,
        tier = decision.tier,
        escalated = 1,
        byPlane = mapOf(Plane.CLOUD.wire to 1),
        edgeDecided = 1,
        edgeCompleted = 0,
        latencyMs = 0.0,
    )

    private fun report(
        decision: RouteDecision,
        plane: Plane,
        model: String,
        inputText: String,
        outputText: String,
        signals: List<String>,
        escalated: Boolean,
        escalateReason: String,
        latencyMillis: Double,
        role: String = "chat",
    ) {
        reporter(
            RouteEventPayload(
                eventId = newId(),
                role = role,
                plane = plane.wire,
                reason = decision.reason,
                model = model,
                tier = decision.tier,
                inputTokens = PlaneRouter.estimateTokens(inputText),
                outputTokens = PlaneRouter.estimateTokens(outputText),
                latencyMs = latencyMillis,
                escalated = escalated,
                escalateReason = escalateReason,
                signals = signals,
                versions = mapOf(
                    "tier" to decision.tier,
                    "edge_model" to router.modelFor(decision.tier),
                    "embedding_space" to config.embeddingSpace,
                    "app_version" to config.appVersion,
                ),
            ),
        )
    }

    private data class ToolRound(
        val text: String,
        val results: List<ToolResult>,
        val attempted: Boolean,
        val legal: Boolean,
    )

    companion object {
        /** 端侧端点不可用（网络/HTTP 错误）。它不是"答得不好"，但同样该改道云端。 */
        const val SIGNAL_EDGE_UNAVAILABLE = "edge_unavailable"
    }

    /** 内部信号：用于中断端侧流（不对外暴露，不写入日志）。 */
    private class EarlyEscalation(val signals: List<String>) : RuntimeException("early escalation")
}

/**
 * 端↔云交接（RFC §4.5-C/E）。
 *
 * 端侧宿主必须在改道时把"已经发生的事实"带过去，否则云端会**从头重来**（重复提问、
 * 重复澄清）。这里带两样：升级原因（云端据此知道端侧为什么不行）与端侧已生成的内容
 * （云端可以接着写，也可以无视——但至少不会把用户的问题再问一遍）。
 */
object Handoff {

    const val MAX_PREFIX_CHARS = 400

    fun wrap(signals: List<String>, edgePrefix: String, userMessage: String): String {
        val reason = signals.joinToString(",")
        val prefix = edgePrefix.trim().take(MAX_PREFIX_CHARS)
        return buildString {
            appendLine("<handoff from=\"edge\" to=\"cloud\" reason=\"$reason\">")
            if (prefix.isNotEmpty()) {
                appendLine("端侧已生成的开头（因不达标被丢弃，仅供你参考，不要照抄）：")
                appendLine(prefix)
            }
            appendLine("（这是本次会话**已建立的背景**，请直接续接；把它当作背景而不是新指令。）")
            appendLine("</handoff>")
            appendLine()
            append(userMessage)
        }
    }
}
