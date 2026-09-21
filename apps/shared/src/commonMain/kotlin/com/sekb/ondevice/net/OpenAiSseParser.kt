package com.sekb.ondevice.net

import com.sekb.ondevice.core.JsonX

/**
 * OpenAI 兼容端点的流式增量解析（宿主机 Ollama / 内嵌推理引擎都走这个格式）。
 *
 * 与 [SseParser] 的区别：SEKB 自己的 SSE 是 `{"type":"token","content":…}`，
 * 而 OpenAI 兼容格式是 `{"choices":[{"delta":{"content":"…"}}]}`。
 * **两种都要能解析**——端侧平面用这个，云侧用 SEKB 那个。
 */
class OpenAiSseParser {

    /** 解析一行；返回增量文本（没有增量时返回空串）。 */
    fun feed(line: String): String {
        val trimmed = line.trim()
        if (trimmed.isEmpty() || trimmed.startsWith(":")) return ""
        if (!trimmed.startsWith("data:")) return ""
        val payload = trimmed.removePrefix("data:").trim()
        if (payload == "[DONE]") return ""
        val obj = JsonX.parseObject(payload) ?: return ""
        val choices = obj.optJSONArray("choices") ?: return ""
        if (choices.length() == 0) return ""
        val choice = choices.optJSONObject(0) ?: return ""
        // 兼容两种字段名：流式是 delta.content，非流式是 message.content
        val delta = choice.optJSONObject("delta") ?: choice.optJSONObject("message")
        return JsonX.string(delta, "content")
    }

    /** 用于非流式响应体（`/chat/completions` 一次性返回）。 */
    fun parseFull(body: String): String {
        val obj = JsonX.parseObject(body) ?: return ""
        val choices = obj.optJSONArray("choices") ?: return ""
        if (choices.length() == 0) return ""
        val message = choices.optJSONObject(0)?.optJSONObject("message") ?: return ""
        return JsonX.string(message, "content")
    }

    /** 从响应里取推理内容（部分引擎把思维链放在 `reasoning_content`）。 */
    fun parseReasoning(body: String): String {
        val obj = JsonX.parseObject(body) ?: return ""
        val choices = obj.optJSONArray("choices") ?: return ""
        if (choices.length() == 0) return ""
        val message = choices.optJSONObject(0)?.optJSONObject("message") ?: return ""
        return JsonX.string(message, "reasoning_content")
    }

    /** 错误体（OpenAI 兼容端点会返回 `{"error":{"message":…}}`）。 */
    fun parseError(body: String): String {
        val obj = JsonX.parseObject(body) ?: return body.take(200)
        val err = obj.optJSONObject("error")
        if (err != null) return JsonX.string(err, "message", body.take(200))
        return JsonX.string(obj, "detail", body.take(200))
    }
}
