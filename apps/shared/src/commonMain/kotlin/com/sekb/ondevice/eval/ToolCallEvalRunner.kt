package com.sekb.ondevice.eval

import com.sekb.ondevice.chat.ToolCallEval
import com.sekb.ondevice.edge.ChatMessage
import com.sekb.ondevice.edge.EdgeLlm
import com.sekb.ondevice.route.PlaneRouter
import com.sekb.ondevice.tools.ToolCallJson
import com.sekb.ondevice.tools.ToolRegistry

/**
 * 工具调用 JSON 合法率的**对照实验**（M2 的验收数字，RFC §9）。
 *
 * 设计要点（都是为了让这个数字**不能被自己骗过去**）：
 * - 分母是"尝试次数"而不是"全部回答"：否则模型越不敢用工具，合法率越好看；
 * - 同一批提示词、同一模型、同一温度，只切换**约束解码开关**
 *   （OpenAI 兼容协议里的 `response_format={"type":"json_object"}`），
 *   两组数字的差就是"语法约束值多少"的证据；
 * - 顺带记延迟：约束解码常常更慢，"合法率换延迟"也必须如实报出来。
 */
class ToolCallEvalRunner(
    private val edge: EdgeLlm,
    private val router: PlaneRouter,
    private val tools: ToolRegistry,
    private val model: String,
    private val maxTokens: Int = 96,
    /**
     * 难度开关。
     *
     * 易档提示词带"只调用工具"明示指令 → 两个档位都是 100% 合法率，**看不出约束解码的作用**
     * （天花板效应）。所以必须有一组"不说怎么做、只说想要什么"的难档：
     * 小模型在这种提示下才会开始加解释、写 Markdown 围栏、给多个调用——那才是
     * 语法约束真正要解决的问题。
     */
    val hard: Boolean = false,
) {

    /** 固定提示词集：全部是"必须查设备才能答"的问题（否则模型不会尝试工具调用）。 */
    val prompts: List<String> = listOf(
        "现在几点了？只调用工具。",
        "今天几号？只调用工具。",
        "当前网络是 WiFi 还是蜂窝？只调用工具。",
        "帮我查一下联系人里有没有叫张伟的。只调用工具。",
        "当前处于哪个时区？只调用工具。",
        "设备现在联网了吗？只调用工具。",
        "帮我搜索联系人中名字含「李」的人。只调用工具。",
        "现在的时间戳是多少？只调用工具。",
        "当前网络是否按流量计费？只调用工具。",
        "联系人里有没有叫王芳的？只调用工具。",
    )

    /** 难档提示词：不给"只输出 JSON"的明示指令，要求模型自己选工具并组织参数。 */
    private val hardPrompts: List<String> = listOf(
        "我需要知道当前时间，顺便说一句现在是不是工作时间。",
        "我手机现在是什么网络？按流量算贵吗？",
        "帮我找一下联系人里姓张的人，我想给他打电话。",
        "现在几点了？另外今天网络状况怎么样？",
        "我要给李娜发消息，先帮我确认一下她的号码在不在通讯录里。",
        "设备当前时区和联网状态分别是什么？用一句话汇总。",
        "看看现在的情况：时间、网络、有没有王芳这个联系人。",
        "我想知道这台设备现在能不能上网，以及现在几点。",
        "联系人里有叫刘强的人吗？如果有，把号码告诉我。",
        "现在网络是按流量计费的那种吗？如果是我就不下载了。",
    )

    data class ModeResult(
        val mode: String,
        val eval: ToolCallEvalState,
        val latencyMillis: Long,
    )

    data class ToolCallEvalState(
        val attempts: Int,
        val legal: Int,
        val illegal: Int,
        val hallucinated: Int,
        val rate: Double,
    )

    data class Report(
        val model: String,
        val runs: List<ModeResult>,
        val samples: List<Sample>,
    ) {
        fun summary(): String = buildString {
            appendLine("模型 $model，提示词 ${runs.firstOrNull()?.eval?.let { "" } ?: ""}")
            for (r in runs) {
                appendLine(
                    "  ${r.mode}：尝试 ${r.eval.attempts}/${promptsCount} " +
                        "合法 ${r.eval.legal} 非法 ${r.eval.illegal} " +
                        "幻觉 ${r.eval.hallucinated} → 合法率 " +
                        "${(r.eval.rate * 100).toInt()}%，平均延迟 ${r.latencyMillis}ms",
                )
            }
        }

        var promptsCount: Int = 0
    }

    data class Sample(val prompt: String, val mode: String, val attempted: Boolean, val legal: Boolean, val head: String)

    private fun activePrompts(): List<String> = if (hard) hardPrompts else prompts

    /** 跑一遍对照实验（提示词 × 两种模式）。 */
    fun run(onProgress: (String) -> Unit = {}): Report {
        val runs = mutableListOf<ModeResult>()
        val samples = mutableListOf<Sample>()
        for (jsonMode in listOf(true, false)) {
            val mode = if (jsonMode) "约束解码 ON" else "约束解码 OFF"
            var attempts = 0
            var legal = 0
            var illegal = 0
            var hallucinated = 0
            var totalMillis = 0L
            for (prompt in activePrompts()) {
                val messages = listOf(
                    ChatMessage.system(
                        "你是设备助手。用户要求调用工具时，只输出 JSON，" +
                            "形如 {\"tool\":\"<名字>\",\"args\":{...}}，不要解释。\n" +
                            ToolCallJson.schemaPrompt(tools.all()),
                    ),
                    ChatMessage.user(prompt),
                )
                val completion = edge.complete(model, messages, maxTokens, jsonMode)
                totalMillis += completion.totalMillis.toLong()
                val attempted = ToolCallJson.looksLikeAttempt(completion.text, tools.names())
                val parsed = ToolCallJson.parse(completion.text)
                val isLegal = parsed is ToolCallJson.Parsed.Ok
                val isHallucinated = isLegal &&
                    !ToolCallJson.isKnownTool((parsed as ToolCallJson.Parsed.Ok).call, tools.names())
                if (attempted) {
                    attempts++
                    if (isLegal) legal++ else illegal++
                    if (isHallucinated) hallucinated++
                }
                samples.add(
                    Sample(prompt, mode, attempted, isLegal, completion.text.replace("\n", " ").take(60)),
                )
                onProgress("[$mode] ${prompt.take(12)}… attempted=$attempted legal=$isLegal")
            }
            runs.add(
                ModeResult(
                    mode = mode,
                    eval = ToolCallEvalState(attempts, legal, illegal, hallucinated,
                        rate = if (attempts == 0) 0.0 else legal.toDouble() / attempts),
                    latencyMillis = if (activePrompts().isEmpty()) 0 else totalMillis / activePrompts().size,
                ),
            )
        }
        return Report(model, runs, samples).also { it.promptsCount = activePrompts().size }
    }

    /** 把对照结果写成一行行可粘贴的文本。 */
    fun format(report: Report): String = buildString {
        appendLine("=== 工具调用 JSON 合法率对照（$model，${if (hard) "难档" else "易档"}，" +
            "${activePrompts().size} 条提示词）===")
        for (r in report.runs) {
            appendLine(
                "%-14s 尝试 %2d/%d  合法 %2d  非法 %2d  工具名幻觉 %d  合法率 %3d%%  平均延迟 %dms".format(
                    r.mode, r.eval.attempts, activePrompts().size, r.eval.legal, r.eval.illegal,
                    r.eval.hallucinated, (r.eval.rate * 100).toInt(), r.latencyMillis,
                ),
            )
        }
        appendLine("--- 明细（仅列未按预期产出的样本）---")
        for (s in report.samples) {
            if (!s.legal) appendLine("[${s.mode}] 未产出合法调用 attempted=${s.attempted} ← ${s.prompt}｜输出=${s.head}")
        }
    }
}
