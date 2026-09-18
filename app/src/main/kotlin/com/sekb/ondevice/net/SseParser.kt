package com.sekb.ondevice.net

import com.sekb.ondevice.core.JsonX
import com.sekb.ondevice.model.ChatEvent
import com.sekb.ondevice.model.DoneMeta
import com.sekb.ondevice.model.ExecutionInfo

/**
 * SSE 行解析（纯函数，便于单测）。
 *
 * 服务端一行一个事件：`data: {json}`，事件之间以空行分隔，头部还有 `Cache-Control` 之类的注释行。
 * 这里刻意**不**用第三方 SSE 库：契约只有四种 `type`，而"解析必须对得上服务端"这件事
 * 需要能被单元测试钉住（见 `SseParserTest`）。
 */
class SseParser {

    private val dataBuffer = StringBuilder()

    /**
     * 喂一行（不含换行符），返回解析出的事件；该行不构成事件时返回 null。
     *
     * 空行 = 一个事件结束 → 解析 `dataBuffer` 并清空。
     */
    fun feed(line: String): ChatEvent? {
        val trimmed = line.trimEnd('\r')
        if (trimmed.isEmpty()) return flush()
        if (trimmed.startsWith(":")) return null          // 注释/心跳
        if (!trimmed.startsWith(DATA_PREFIX)) return null  // event:/id:/retry: 本契约不用
        dataBuffer.append(trimmed.removePrefix(DATA_PREFIX).trim())
        return null
    }

    /** 流结束时调用：处理最后一段没被空行终止的数据。 */
    fun finish(): ChatEvent? = flush()

    private fun flush(): ChatEvent? {
        if (dataBuffer.isEmpty()) return null
        val payload = dataBuffer.toString()
        dataBuffer.setLength(0)
        // 服务端未发送 [DONE] 这类哨兵；非 JSON 一律当错误处理而不是静默丢弃
        val obj = JsonX.parseObject(payload)
            ?: return ChatEvent.Error("无法解析的 SSE 数据: ${payload.take(200)}")
        return when (JsonX.string(obj, "type")) {
            "thinking" -> ChatEvent.Thinking(JsonX.string(obj, "content"))
            "token" -> ChatEvent.Token(JsonX.string(obj, "content"))
            "done" -> ChatEvent.Done(parseDone(JsonX.objectOrNull(obj, "meta")))
            "error" -> ChatEvent.Error(JsonX.string(obj, "detail", "未知错误"))
            else -> null
        }
    }

    private fun parseDone(metaObj: org.json.JSONObject?): DoneMeta {
        val execObj = metaObj?.optJSONObject("execution")
        val execution = execObj?.let {
            ExecutionInfo(
                primaryPlane = JsonX.string(it, "primary_plane"),
                primaryRole = JsonX.string(it, "primary_role"),
                model = JsonX.string(it, "model"),
                reason = JsonX.string(it, "reason"),
                tier = JsonX.string(it, "tier"),
                escalated = JsonX.int(it, "escalated"),
                byPlane = JsonX.intMap(it, "by_plane"),
                edgeDecided = JsonX.int(it, "edge_decided"),
                edgeCompleted = JsonX.int(it, "edge_completed"),
                latencyMs = JsonX.double(it, "latency_ms"),
            )
        }
        return DoneMeta(
            conversationId = JsonX.string(metaObj, "conversation_id"),
            intent = JsonX.string(metaObj, "intent"),
            execution = execution,
        )
    }

    companion object {
        private const val DATA_PREFIX = "data:"

        /**
         * 服务端落库/调试友好的一行式构造（端侧测试与 mock server 用）。
         * 与 `chat_stream` 的输出格式保持一致。
         */
        fun asDataLine(json: String): String = "$DATA_PREFIX $json"
    }
}
