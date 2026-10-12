// H3 · 网络端口的**共享层宿主**：协议路径与 JSON 解析留在 Kotlin，HTTP 客户端留在 ArkTS
//
// ## 为什么端口注入走「方案 (ii)：网络调用留在 ArkTS」而不是「NAPI 回调注册给 Kotlin」(i)
//
// owner 的两条硬约束是 ① 传输用平台 `@ohos.net.http`（C2 已改判）② **协议路径与 JSON 字段解析
// 只能有一份**（现在在共享层 `SekbApi.kt`）。方案 (i)（Kotlin 实现 `HttpTransport`、内部回调 ArkTS）
// 在鸿蒙上有一个**结构性死锁**，这不是实现水平问题：
//
//   * 共享层 `HttpTransport` 是**同步**接口（`postJson`/`get`/`postJsonStream` 直接返回结果）；
//   * 要在 Kotlin 里实现它，Kotlin 必须"发起一次 ArkTS 异步请求并等它完成"；
//   * 而 Kotlin 是被 ArkTS **同步**调进来的（NAPI 调用在 ArkTS 线程上执行），
//     `await` 的回调只能排在这一条线程的任务队列里 —— 线程正卡在 NAPI 里等 → **回调永远排不到**。
//     iOS 能用 `DispatchSemaphore` 是因为 URLSession 的回调在**另一条线程**上；鸿蒙的
//     ArkTS 任务队列是单线程的，没有那个自由度。
//
// 所以本文件采用的是 **(ii) 的严格形态**：ArkTS 负责"发起请求 / 逐块收流"，
// **每一次请求结果、每一行流式数据都同步回调进 Kotlin**，由 Kotlin 侧完成
// 解析与协议处理。这样：
//   * 调用方向**始终是 ArkTS → Kotlin**（NAPI 同步调用），没有"Kotlin 等 JS"的窗口 → 无死锁；
//   * JSON 字段解析可以用共享层的 `JsonX`（同一份）；
//   * SSE 语义可以用共享层的 `SseParser`（同一份）——ArkTS 只搬行，不解释行。
//
// 与 iOS 的 `UrlSessionTransport.swift` 的差异（值得记一笔）：iOS 那份 Kotlin **确实**实现了
// `HttpTransport`；鸿蒙这份则在 UI/编排层把 `SekbApi` 的"传输参数"换成"ArkTS 已拿到的响应"。
// 代价：`HttpTransport` 接口在鸿蒙侧暂时没有实现类。**接口本身一行未改**，
// 将来若平台给出同步 HTTP（或 `rcp` 的同步形态），补一个 `HarmonyHttpTransport` 即可回到 (i)。
//
// ## 响应体跨 NAPI 边界的完整性
//
// ArkTS 回来的文本格式是
//     `"HTTP <code> <bodyUTF8Bytes>\n<响应体原文>"`
// Kotlin 侧解出 code / 声明字节数，并与**实际收到的响应体字节数**比对——
// 不一致就是"跨边界被截断"，直接 FAIL。这一条是防"看起来解析成功、其实少了一半"的。
@file:OptIn(kotlin.experimental.ExperimentalNativeApi::class)

package com.sekb.ohos.spike

import com.sekb.shared.core.JsonArray
import com.sekb.shared.core.JsonObject
import com.sekb.shared.core.JsonX
import com.sekb.shared.model.ChatEvent
import com.sekb.shared.net.SseParser

/** ArkTS 侧一次缓冲式 HTTP 调用的结果。 */
class NetResult(val code: Int, val body: String, val declaredBytes: Int, val rawHead: String) {
    val bodyBytes: Int get() = utf8Length(body)
    val intact: Boolean get() = declaredBytes < 0 || declaredBytes == bodyBytes
}

private fun utf8Length(s: String): Int {
    var n = 0
    for (c in s) {
        val cp = c.code
        n += when {
            cp < 0x80 -> 1
            cp < 0x800 -> 2
            cp < 0x10000 -> 3
            else -> 4
        }
    }
    return n
}

/** 解析 ArkTS 回来的 `"HTTP <code> <bytes>\n<body>"`。 */
fun parseNetResult(raw: String): NetResult {
    val nl = raw.indexOf('\n')
    if (nl < 0) return NetResult(-1, raw, -1, raw)
    val head = raw.substring(0, nl)
    val body = raw.substring(nl + 1)
    val parts = head.split(' ')
    val code = parts.getOrNull(1)?.toIntOrNull() ?: -1
    val bytes = parts.getOrNull(2)?.toIntOrNull() ?: -1
    return NetResult(code, body, bytes, head)
}

/**
 * 共享层 `JsonX` 的**字段解析演示**（H3 自检用）。
 *
 * 它存在的意义不是"解析 JSON"这件事本身，而是证明：
 * **响应体跨 NAPI 回来之后，解析它的是共享层那一份代码**（`JsonX`/`JsonObject`），
 * ArkTS 侧不另立一套。
 */
object NetParse {

    /** `GET /api/tags` → `models[].name` 的有序列表。 */
    fun ollamaModelNames(body: String): List<String> {
        val obj = JsonX.parseObject(body) ?: return emptyList()
        val arr = obj.optJSONArray("models") ?: return emptyList()
        return (0 until arr.length()).mapNotNull { i ->
            arr.optJSONObject(i)?.let { JsonX.string(it, "name") }
        }
    }

    /**
     * 用一个**共享层真实存在的解析路径**去消费响应体：`SekbApi.parseCredentials` 用的是
     * `JsonX.string(obj,"device_id")` + `JsonX.string(obj,"device_token")`，这里照同一个口径
     * 解析一个样例响应，证明"共享层的字段口径在设备上可跑"。
     */
    fun credentialsFields(json: String): Triple<String, String, Long>? {
        val obj = JsonX.parseObject(json) ?: return null
        val id = JsonX.string(obj, "device_id")
        val token = JsonX.string(obj, "device_token")
        val ttl = JsonX.int(obj, "expires_in_hours", 24).toLong()
        return Triple(id, token, ttl)
    }

    /** 把 `Map` 编成 JSON（作者：共享层的 `JsonObject`，不是 ArkTS 的 `JSON.stringify`）。 */
    fun headersJson(headers: Map<String, String>): String {
        val o = JsonObject()
        headers.forEach { (k, v) -> o.put(k, v) }
        return o.toString()
    }

    /** 调试用：`JsonArray` 也确实是共享层的那个类型。 */
    fun arraySize(json: String): Int = JsonArray.parse(json)?.length() ?: -1
}

/**
 * SSE 行泵：ArkTS 每收到**一个完整行**就同步调一次，Kotlin 侧把行喂给共享层 `SseParser`。
 *
 * 这是"H3 流式"与"H8 聊天"共用的形状：ArkTS 不做任何 SSE 语义判断（`data:` / 空行 / 事件类型），
 * 只负责按 `\n` 切行与搬运。
 */
object SseLinePump {
    private var parser: SseParser? = null
    private val events = mutableListOf<ChatEvent>()
    private var lines = 0
    private var nonEmpty = 0
    private var dataLines = 0

    fun begin() {
        parser = SseParser()
        events.clear()
        lines = 0
        nonEmpty = 0
        dataLines = 0
    }

    /** 喂一行（不含换行符）。返回本行解出的事件类型简名（无事件时为空串）。 */
    fun feed(line: String): String {
        val p = parser ?: return ""
        lines++
        if (line.isNotBlank()) nonEmpty++
        if (line.trimStart().startsWith("data:")) dataLines++
        val e = p.feed(line) ?: return ""
        events += e
        return kind(e)
    }

    /** 结束：处理最后一段没被空行终止的数据。 */
    fun finish(): String {
        val tail = parser?.finish() ?: return ""
        events += tail
        return kind(tail)
    }

    fun summary(): String =
        "行=${lines}（非空=${nonEmpty}，data:=${dataLines}）事件=${events.size}" +
            "：${events.joinToString("/") { kind(it) }}"

    fun eventCount(): Int = events.size
    fun lineCount(): Int = lines
    fun kinds(): List<String> = events.map { kind(it) }

    private fun kind(e: ChatEvent): String = when (e) {
        is ChatEvent.Thinking -> "thinking"
        is ChatEvent.Token -> "token"
        is ChatEvent.Done -> "done"
        is ChatEvent.Error -> "error"
    }
}

/**
 * 把共享层 `SseApi` 的 URL/鉴权头拼装留在 Kotlin 侧（`SekbApi` 就是这么做的），
 * ArkTS 只拿到"要请求哪个 URL、带哪些头"的结果 —— 避免 ArkTS 里出现第二套路径。
 */
object ProtocolUrls {
    fun chatStream(baseUrl: String): String = "$baseUrl/api/v1/chat/stream"
    fun deviceEnroll(baseUrl: String): String = "$baseUrl/api/v1/device/enroll"
    fun deviceRefresh(baseUrl: String): String = "$baseUrl/api/v1/device/refresh"
    fun authHeaders(token: String): Map<String, String> =
        mapOf("Content-Type" to "application/json", "Authorization" to "Bearer $token")
}
