package com.sekb.shared.eval

import com.sekb.shared.chat.ToolCallEval
import com.sekb.shared.edge.ChatMessage
import com.sekb.shared.edge.EdgeLlm
import com.sekb.shared.route.PlaneRouter
import com.sekb.shared.tools.ToolCallJson
import com.sekb.shared.tools.ToolRegistry
import com.sekb.shared.core.Fmt

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
    /**
     * 每条提示词重复跑几次。
     *
     * 为什么必须能重复：§1.4 那轮每条只跑了 **1 次**，40/40「合法」就被写成了结论，
     * 而 10/10 全对时真实合法率的 95% 置信下界只有约 0.72——**点估计撑不住结论的语气**。
     * 重复采样后才能给出区间与按提示词的稳定性（见 `EvalStats`）。
     */
    val repeats: Int = 1,
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
        /** 按提示词看的稳定性（`repeats > 1` 时有意义）。 */
        val stability: List<EvalStats.Stability> = emptyList(),
        /** 合法率的 95% 置信区间（Wilson）。分母 = 尝试次数，与 `eval.rate` 同口径。 */
        val ciLow: Double = 0.0,
        val ciHigh: Double = 0.0,
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
                    "  ${r.mode}：尝试 ${r.eval.attempts} 合法 ${r.eval.legal} " +
                        "非法 ${r.eval.illegal} 幻觉 ${r.eval.hallucinated} " +
                        "→ 合法率 ${EvalStats.formatRate(r.eval.legal, r.eval.attempts)}" +
                        "，平均延迟 ${r.latencyMillis}ms",
                )
                val unstable = r.stability.filter { !it.consistent }
                if (unstable.isNotEmpty()) {
                    appendLine("    ⚠️ 不稳定的提示词（同一条多次重复结果不一致）：")
                    unstable.forEach {
                        appendLine("      ${it.legal}/${it.attempts} 「${it.prompt.take(24)}」")
                    }
                } else if (r.stability.isNotEmpty()) {
                    appendLine("    ✓ ${r.stability.size} 条提示词在重复采样下结果全部一致")
                }
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
            var calls = 0
            val perPrompt = LinkedHashMap<String, MutableList<Pair<Boolean, Boolean>>>()
            for (round in 0 until repeats) {
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
                perPrompt.getOrPut(prompt) { mutableListOf() }.add(attempted to isLegal)
                calls++
                onProgress("[$mode] r${round + 1}/$repeats ${prompt.take(12)}… attempted=$attempted legal=$isLegal")
            }
            }
            runs.add(
                ModeResult(
                    mode = mode,
                    eval = ToolCallEvalState(attempts, legal, illegal, hallucinated,
                        rate = if (attempts == 0) 0.0 else legal.toDouble() / attempts),
                    latencyMillis = if (calls == 0) 0 else totalMillis / calls,
                    stability = EvalStats.promptStability(perPrompt, repeats),
                    // 合法率是按"尝试次数"算的，所以区间也按尝试次数给（分母口径必须一致）
                    ciLow = EvalStats.wilson(legal, attempts).first,
                    ciHigh = EvalStats.wilson(legal, attempts).second,
                ),
            )
        }
        return Report(model, runs, samples).also { it.promptsCount = activePrompts().size }
    }

    /** 把对照结果写成一行行可粘贴的文本。 */
    fun format(report: Report): String = buildString {
        appendLine("=== 工具调用 JSON 合法率对照（$model，${if (hard) "难档" else "易档"}，" +
            "${activePrompts().size} 条提示词${if (repeats > 1) " × 每条重复 $repeats 次" else ""}）===")
        for (r in report.runs) {
            appendLine(
                // 原来用 String.format（JVM 专有）→ Fmt；`%-14s`/`%2d`/`%3d` 的对齐效果保持一致
                Fmt.padEnd(r.mode, 14) +
                    " 尝试 ${Fmt.padStart(r.eval.attempts, 2)}/${activePrompts().size}" +
                    "  合法 ${Fmt.padStart(r.eval.legal, 2)}  非法 ${Fmt.padStart(r.eval.illegal, 2)}" +
                    "  工具名幻觉 ${r.eval.hallucinated}  合法率 ${Fmt.padStart(Fmt.pct(r.eval.rate), 3)}%" +
                    "  平均延迟 ${r.latencyMillis}ms",
            )
            // 区间与稳定性：没有它们，"100%" 这句话的样本量撑不住它自己的语气
            // （10/10 的 95% 下界只有 72%，n=50 才到 93%）
            appendLine("   95%CI [" + Fmt.pct(r.ciLow * 100) + "%, " + Fmt.pct(r.ciHigh * 100) + "%]（n=${r.eval.attempts}）")
            val unstable = r.stability.filter { !it.consistent }
            if (unstable.isNotEmpty()) {
                appendLine("   ⚠️ 不稳定提示词 ${unstable.size}/${r.stability.size}：")
                unstable.forEach {
                    appendLine("      ${it.legal}/${it.attempts} 「${it.prompt.take(24)}」")
                }
            } else if (r.stability.isNotEmpty()) {
                appendLine("   ✓ ${r.stability.size} 条提示词在重复采样下结果全部一致")
            }
        }
        appendLine("--- 明细（仅列未按预期产出的样本）---")
        for (s in report.samples) {
            if (!s.legal) appendLine("[${s.mode}] 未产出合法调用 attempted=${s.attempted} ← ${s.prompt}｜输出=${s.head}")
        }
    }
}
