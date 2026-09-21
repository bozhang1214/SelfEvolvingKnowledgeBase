package com.sekb.shared.net

import com.sekb.shared.core.nowMillis

import com.sekb.shared.core.JsonX
import com.sekb.shared.model.ChatEvent
import com.sekb.shared.model.DeviceCredentials
import com.sekb.shared.model.ExecutionInfo
import com.sekb.shared.model.RouteEventPayload
import com.sekb.shared.core.JsonArray
import com.sekb.shared.core.JsonObject

/** 云端一次聊天的结果：执行位置（S3）+ 会话 ID（下次要带回去，否则每轮都是新会话）。 */
data class SekbChatResult(val execution: ExecutionInfo?, val conversationId: String?)

/** SEKB 调用失败。**不要把 token 放进 message**（日志/UI 都会显示它）。 */
class SekbApiException(val code: Int, val detail: String) :
    Exception("SEKB $code: $detail")

/**
 * SEKB 云端客户端（协议见 SEKB 仓库 `docs/ops/16-端云协同协议.md`）。
 *
 * 只有五个动作：换账号令牌 → 换设备凭证 → 轮换 → 上报路由事件 → 聊天（SSE）。
 * 每个动作的错误都**分类**抛出（401/403/404/429/503 的处理方式完全不同，见协议 §8）。
 */
class SekbApi(
    private val transport: HttpTransport,
    private val baseUrl: String,
    private val now: () -> Long = { nowMillis() },
) {

    private val sse = SseParser()

    // ---------- 账号与设备凭证 ----------

    fun login(email: String, password: String): String {
        val body = JsonObject().put("email", email).put("password", password).toString()
        val resp = transport.postJson("$baseUrl/api/v1/auth/login", jsonHeaders(), body)
        if (!resp.isOk) throw SekbApiException(resp.code, errorDetail(resp.body))
        // 字段名以服务端 LoginResponse 为准：`{"user": {...}, "token": "..."}`。
        // 第一版按 OAuth 习惯猜了 `access_token` → 明明 200 OK 却被判成"登录失败"，
        // 是模拟器真机 E2E 抓出来的（服务端日志有 200，客户端说失败）。
        val obj = JsonX.parseObject(resp.body)
        val token = JsonX.string(obj, "token").ifBlank { JsonX.string(obj, "access_token") }
        if (token.isBlank()) throw SekbApiException(resp.code, "登录响应里没有 token")
        return token
    }

    /** 用**用户 token** 换设备凭证（设备首次接入调一次）。 */
    fun enroll(userToken: String, name: String, platform: String = "android",
               appVersion: String, embeddingSpace: String): DeviceCredentials {
        val body = JsonObject()
            .put("name", name)
            .put("platform", platform)
            .put("app_version", appVersion)
            .put("embedding_space", embeddingSpace)
            .toString()
        val resp = transport.postJson("$baseUrl/api/v1/device/enroll", authHeaders(userToken), body)
        if (!resp.isOk) throw SekbApiException(resp.code, errorDetail(resp.body))
        return parseCredentials(resp.body)
    }

    /** 设备 token 轮换：**旧 token 立即失效**（服务端语义），所以必须落盘新凭证。 */
    fun refresh(deviceToken: String): DeviceCredentials {
        val resp = transport.postJson("$baseUrl/api/v1/device/refresh", authHeaders(deviceToken), "{}")
        if (!resp.isOk) throw SekbApiException(resp.code, errorDetail(resp.body))
        return parseCredentials(resp.body)
    }

    fun heartbeat(deviceToken: String, appVersion: String, embeddingSpace: String): Boolean {
        val body = JsonObject().put("app_version", appVersion).put("embedding_space", embeddingSpace).toString()
        val resp = transport.postJson("$baseUrl/api/v1/device/heartbeat", authHeaders(deviceToken), body)
        return resp.isOk
    }

    fun listDevices(token: String): List<Map<String, String>> {
        val resp = transport.get("$baseUrl/api/v1/device/list", authHeaders(token))
        if (!resp.isOk) throw SekbApiException(resp.code, errorDetail(resp.body))
        val arr = JsonX.parseObject(resp.body)?.optJSONArray("devices") ?: return emptyList()
        return (0 until arr.length()).mapNotNull { idx ->
            arr.optJSONObject(idx)?.let { o ->
                o.keys().asSequence().associateWith { k -> o.optString(k) }
            }
        }
    }

    // ---------- 聊天（SSE） ----------

    /**
     * 云端聊天。返回执行位置（`done.meta.execution`），这是 S3 的客户端入口。
     *
     * 服务端的 `token` 已在调用前逐段交给 [onToken]，这里只回最终信息。
     */
    fun chatStream(
        deviceToken: String,
        message: String,
        conversationId: String?,
        onToken: (String) -> Unit,
        onThinking: (String) -> Unit = {},
    ): SekbChatResult {
        val body = JsonObject().put("message", message)
        if (!conversationId.isNullOrBlank()) body.put("conversation_id", conversationId)
        var execution: ExecutionInfo? = null
        var convId: String? = conversationId
        var error: String? = null
        val code = transport.postJsonStream(
            url = "$baseUrl/api/v1/chat/stream",
            headers = authHeaders(deviceToken),
            body = body.toString(),
            timeoutSeconds = 600,
        ) { line ->
            when (val event = sse.feed(line)) {
                is ChatEvent.Token -> onToken(event.content)
                is ChatEvent.Thinking -> onThinking(event.content)
                is ChatEvent.Done -> {
                    execution = event.meta.execution
                    if (event.meta.conversationId.isNotBlank()) convId = event.meta.conversationId
                }
                is ChatEvent.Error -> error = event.detail
                null -> Unit
            }
        }
        // 结束前把最后一段没被空行终止的数据也处理掉
        when (val tail = sse.finish()) {
            is ChatEvent.Token -> onToken(tail.content)
            is ChatEvent.Done -> {
                execution = tail.meta.execution
                if (tail.meta.conversationId.isNotBlank()) convId = tail.meta.conversationId
            }
            is ChatEvent.Error -> error = tail.detail
            else -> Unit
        }
        if (error != null) throw SekbApiException(code, error!!)
        if (code !in 200..299) throw SekbApiException(code, "流式请求失败")
        return SekbChatResult(execution, convId)
    }

    // ---------- 路由事件上报 ----------

    /**
     * 上报一条路由事件（幂等：`event_id` 唯一，重传不会重复统计）。
     *
     * **为什么端侧非要上报**：端侧自己完成的推理不经过服务端，不上报的话
     * "端侧完成率/升级率"永远只统计到一半（协议 §4）。
     */
    fun reportRouteEvent(deviceToken: String, event: RouteEventPayload): Boolean {
        val body = JsonObject()
            .put("event_id", event.eventId)
            .put("role", event.role)
            .put("plane", event.plane)
            .put("reason", event.reason)
            .put("model", event.model)
            .put("tier", event.tier)
            .put("input_tokens", event.inputTokens)
            .put("output_tokens", event.outputTokens)
            .put("latency_ms", event.latencyMs)
            .put("escalated", event.escalated)
            .put("escalate_reason", event.escalateReason)
            .put("signals", JsonArray().apply { event.signals.forEach { put(it) } })
            .put("versions", JsonObject().apply { event.versions.forEach { (k, v) -> put(k, v) } })
            .toString()
        val resp = transport.postJson("$baseUrl/api/v1/edge/route-events", authHeaders(deviceToken), body)
        // 403（设备 token 无权限）与 401（凭证失效）都要让上层知道，但不能因此丢事件：
        // 返回值只表示"服务端收下了没有"，重传由调用方按 event_id 幂等地做。
        return resp.isOk
    }

    /** 两个北极星指标（给 UI 的"端云协同"面板用）。 */
    fun routeStats(token: String): Map<String, String> {
        val resp = transport.get("$baseUrl/api/v1/edge/routes/stats", authHeaders(token))
        if (!resp.isOk) throw SekbApiException(resp.code, errorDetail(resp.body))
        val obj = JsonX.parseObject(resp.body) ?: return emptyMap()
        return obj.keys().asSequence().associateWith { obj.optString(it) }
    }

    // ---------- 内部 ----------

    private fun parseCredentials(body: String): DeviceCredentials {
        val obj = JsonX.parseObject(body) ?: throw SekbApiException(200, "响应不是 JSON")
        val deviceId = JsonX.string(obj, "device_id")
        val token = JsonX.string(obj, "device_token")
        if (deviceId.isBlank() || token.isBlank()) throw SekbApiException(200, "响应缺少 device_id/device_token")
        val ttlHours = JsonX.int(obj, "expires_in_hours", DeviceCredentials.DEFAULT_TTL_HOURS.toInt()).toLong()
        return DeviceCredentials.fromTtl(deviceId, token, now(), ttlHours)
    }

    private fun errorDetail(body: String): String = JsonX.string(JsonX.parseObject(body), "detail", body.take(200))

    private fun jsonHeaders(): Map<String, String> = mapOf("Content-Type" to "application/json")

    private fun authHeaders(token: String): Map<String, String> =
        mapOf("Content-Type" to "application/json", "Authorization" to "Bearer $token")
}
