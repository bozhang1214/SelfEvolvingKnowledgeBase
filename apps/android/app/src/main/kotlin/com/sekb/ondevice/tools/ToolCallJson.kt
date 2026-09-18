package com.sekb.ondevice.tools

import com.sekb.ondevice.model.ToolCall
import com.sekb.ondevice.model.ToolResult
import org.json.JSONObject

/**
 * 工具调用的**结构化解析与校验**（端侧 Agent 可用性的关键一环）。
 *
 * 小模型的典型失败模式是"工具名对但 JSON 不合法"或"JSON 合法但工具名不存在"。
 * 两种都要能被单独识别出来，否则既没法统计"工具调用 JSON 合法率"，
 * 也没法把它接到 `tool_hallucination` 升级信号上。
 */
object ToolCallJson {

    /** 解析结果：要么是合法调用，要么给出**可分类**的失败原因。 */
    sealed interface Parsed {
        data class Ok(val call: ToolCall) : Parsed
        data class Invalid(val reason: String, val raw: String) : Parsed
    }

    /**
     * 宽容解析：容忍 ```json 围栏、前后废话、单引号、尾随逗号。
     *
     * 为什么宽容：约束解码（grammar）没开时，2B 模型经常给"对的意图 + 不合法语法"。
     * 宽容解析能把"意图对不对"与"语法对不对"分开统计——这两件事的改进方向完全不同。
     */
    fun parse(text: String): Parsed {
        val candidate = extractJsonObject(text)
            ?: return Parsed.Invalid("no_json_object", text.take(200))
        val normalized = normalize(candidate)
        val obj = try {
            JSONObject(normalized)
        } catch (e: Exception) {
            return Parsed.Invalid("json_syntax:${e.message?.take(60) ?: "error"}", candidate.take(200))
        }
        val tool = obj.optString("tool", "").ifBlank { obj.optString("name", "") }
        if (tool.isBlank()) return Parsed.Invalid("missing_tool_name", candidate.take(200))
        val argsNode = obj.optJSONObject("args") ?: obj.optJSONObject("arguments")
        val args = LinkedHashMap<String, String>()
        if (argsNode != null) {
            for (k in argsNode.keys()) args[k] = argsNode.optString(k)
        }
        return Parsed.Ok(ToolCall(tool, args))
    }

    /** 从一段自由文本里取出第一个 `{...}`（用括号配对，能正确跳过字符串里的花括号）。 */
    fun extractJsonObject(text: String): String? {
        val start = text.indexOf('{')
        if (start < 0) return null
        var depth = 0
        var inString = false
        var escaped = false
        for (i in start until text.length) {
            val c = text[i]
            when {
                escaped -> escaped = false
                c == '\\' && inString -> escaped = true
                c == '"' -> inString = !inString
                !inString && c == '{' -> depth++
                !inString && c == '}' -> {
                    depth--
                    if (depth == 0) return text.substring(start, i + 1)
                }
            }
        }
        return null
    }

    /** 修掉最常见的两种语法错：单引号键值、对象/数组尾随逗号。 */
    fun normalize(json: String): String {
        var out = json.trim()
        if (out.startsWith("'") && out.endsWith("'") && out.length > 1) {
            out = "\"" + out.substring(1, out.length - 1) + "\""
        }
        out = out.replace('\'', '"')
        out = TRAILING_COMMA.replace(out, "$1")
        return out
    }

    private val TRAILING_COMMA = Regex(",(\\s*[}\\]])")

    /** 给模型的工具说明（进 system prompt）：名字 + 参数 + 权限，一样都不能少。 */
    fun schemaPrompt(tools: List<DeviceTool>): String = buildString {
        appendLine("你可以调用下列设备工具。需要时**只输出**一个 JSON 对象，形如")
        appendLine("""{"tool": "<名字>", "args": {<参数>}}""")
        appendLine("不要输出解释、不要用 Markdown 代码块。可用工具：")
        for (t in tools) {
            val args = if (t.args.isEmpty()) "无" else t.args.entries.joinToString(", ") { (k, v) -> "$k: $v" }
            appendLine("- ${t.name}（参数：$args）—— ${t.description}")
        }
    }

    /**
     * 模型这一轮是否**试图**调用工具。
     *
     * 判据刻意宽松：出现已注册工具名，或出现 `"tool"` 键。因为"合法率"的分母必须是
     * **尝试次数**而不是"总回答数"——否则模型越少尝试工具，这个指标看起来越漂亮。
     */
    fun looksLikeAttempt(text: String, knownTools: Set<String>): Boolean {
        if (knownTools.any { text.contains(it) }) return true
        return text.contains("\"tool\"") || text.contains("'tool'")
    }

    /** 未注册工具名的调用 → 交由 [ToolRegistry] 判为幻觉并走升级信号。 */
    fun isKnownTool(call: ToolCall, knownTools: Set<String>): Boolean = call.tool in knownTools

    /**
     * 缺必填参数的调用：不执行，返回可读原因（而不是把 null 传给工具）。
     *
     * 只看 [DeviceTool.requiredArgs]：声明过的**可选**参数不算缺（见那里的注释）。
     */
    fun missingRequired(call: ToolCall, tool: DeviceTool): List<String> =
        tool.requiredArgs.filter { it !in call.args }.toList()

    /** 把执行结果压成给模型看的一行（避免把整段 JSON 塞回上下文）。 */
    fun resultForModel(result: ToolResult): String = when {
        result.denied -> "工具被拒绝：${result.reason}"
        result.ok -> "工具结果：${result.output}"
        else -> "工具失败：${result.reason}"
    }
}
