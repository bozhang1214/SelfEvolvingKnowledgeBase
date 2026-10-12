// H3 · 设备侧自检（网络端口）：`bash scripts/harmony.sh net`
//
// ## 形状：ArkTS 逐步驱动，Kotlin 只做"拿到数据之后"的事
//
// 死锁的根因是"Kotlin 等 ArkTS 的异步回调"（见 `NetPort.kt` 文件头：共享层 `HttpTransport`
// 是同步接口，而 Kotlin 是被 ArkTS 同步调进来的，`await` 的回调排不进被卡住的那条线程）。
// 所以这里每个入口都是 ArkTS 的一次**同步 NAPI 调用**，Kotlin 内部只做解析、喂 `SseParser`、
// 汇总判据。全程没有"Kotlin 等回调"的窗口。
//
// ## 判据由 ArkTS 侧算好、把**事实**传进来，Kotlin 只负责"判定 + 渲染"
//
// 为什么不让 Kotlin 自己发请求：那种形状就是上面说的死锁形状。所以 `h3Probe` 收的是
// **已观测到的事实**（`observedCode` / 行数 / 两侧字节数 / 字段是否解析出来），
// 由 Kotlin 做**判定**（期望值比对、`PASS`/`FAIL` 渲染）。这样"判据"仍然只有一份，
// 而"发请求"这件端侧专属的事留在 ArkTS。
@file:OptIn(kotlin.experimental.ExperimentalNativeApi::class)

package com.sekb.ohos.spike

import com.sekb.shared.model.ChatEvent
import com.sekb.shared.net.HttpResponse
import com.sekb.shared.net.SseParser

private class H3Report {
    val lines = mutableListOf<String>()
    var failed = 0
    var total = 0
    fun check(name: String, ok: Boolean, detail: String) {
        total++
        if (!ok) failed++
        lines += "${if (ok) "PASS" else "FAIL"} ${name} — ${detail}"
    }
    fun render(): String =
        (listOf("H3 ${if (failed == 0) "PASS" else "FAIL"} ${total - failed}/${total}") + lines)
            .joinToString("\n")
}

/**
 * 一段 SSE 夹具（与 `SseParserTest` 同一形状：`data:` 行 + 空行分隔 + 一条注释心跳）。
 *
 * 放在 Kotlin 侧而不是从 ArkTS 传进来：这条判据要证明的正是
 * **"SSE 语义只有共享层那一份"** —— 夹具只经过 `SseParser`，不经过 ArkTS。
 */
private val CANNED_SSE = listOf(
    ": ping",
    "data: {\"type\":\"thinking\",\"content\":\"想一想\"}",
    "",
    "data: {\"type\":\"token\",\"content\":\"你好\"}",
    "",
    "data: {\"type\":\"token\",\"content\":\"，世界\"}",
    "",
    "data: {\"type\":\"done\",\"meta\":{\"conversation_id\":\"conv-h3\",\"intent\":\"chat\"," +
        "\"execution\":{\"primary_plane\":\"cloud\",\"primary_role\":\"chat\",\"model\":\"qwen2.5:7b\"," +
        "\"reason\":\"edge_miss\",\"tier\":\"cloud\",\"escalated\":1,\"by_plane\":{\"cloud\":1}," +
        "\"edge_decided\":0,\"edge_completed\":0,\"latency_ms\":123.5}}}",
    "",
)

private fun eventKind(e: ChatEvent): String = when (e) {
    is ChatEvent.Thinking -> "thinking"
    is ChatEvent.Token -> "token"
    is ChatEvent.Done -> "done"
    is ChatEvent.Error -> "error"
}

/**
 * 一条探针的**已观测事实**（ArkTS 侧填）。
 *
 * @param name 判据名（与脚本/README 里的一致）
 * @param wantCode 期望状态码；`-1` 表示"期望**不是** 2xx"（反向验证用）
 * @param gotCode 实观测状态码（`-1` = 请求抛异常/连不上）
 * @param declaredBytes ArkTS 声明的响应体字节数（`-1` = 未测）
 * @param foundFields ArkTS 侧用共享层口径要检查的字段是否解析出来
 */
@CName("sekb_spike_h3_probe")
fun sekbSpikeH3Probe(
    name: String,
    url: String,
    wantCode: String,
    gotCode: String,
    declaredBytes: String,
    bodyChars: String,
    foundFields: String,
): String {
    val r = H3Report()
    val want = wantCode.toIntOrNull() ?: -1
    val got = gotCode.toIntOrNull() ?: -1
    val bytes = declaredBytes.toIntOrNull() ?: -1
    val chars = bodyChars.toIntOrNull() ?: -1
    val fields = foundFields == "1" || foundFields == "true"

    val codeOk = if (want < 0) got !in 200..299 else got == want
    r.check(
        name,
        codeOk,
        if (want < 0) "GET ${url} → code=${got}（期望**非 2xx**；若变绿说明这条判据是摆设）"
        else "GET ${url} → code=${got}（期望 ${want}）",
    )
    // ⚠️ `__body` / `__fields` **只对 2xx 期望值断言**。
    //    第一版对"期望 404"的反向验证也加这两条 → `__fields` 必然 FAIL（404 的响应体里没有字段），
    //    于是整段报 `H3 FAIL 2/3`。而当时的脚本判定只看 `tail -1` 的头部行，
    //    把这段的 FAIL **藏起来了**（判据"看起来绿"）。修了两处：
    //      ① 这里不再对非 2xx 期望值断言 body/fields（语义上本就不适用）；
    //      ② `scripts/harmony.sh net` 改为取**全部** `H3 (PASS|FAIL) n/N` 头部行并显式拒绝任何 FAIL。
    if (want in 200..299) {
        r.check(
            "${name}__body",
            bytes < 0 || (bytes > 0 && chars > 0),
            "响应体：ArkTS 声明 ${bytes}B、Kotlin 侧 ${chars} 字符" +
                "（0 = 响应体丢了；响应体的**逐字节**完整性另由 sse 行数判据钉住）",
        )
        r.check(
            "${name}__fields",
            fields,
            "共享层口径的字段解析出来=${fields}（device_id/device_token/expires_in_hours）",
        )
    }
    return r.render()
}

/**
 * 流式判据（ArkTS pump 完之后调用）。
 *
 * @param arkLines ArkTS 按 `\n` 切出的完整行数
 * @param kotlinLines Kotlin 经 NAPI 收到的行数（`SseLinePump`）
 */
@CName("sekb_spike_h3_stream")
fun sekbSpikeH3Stream(url: String, code: String, arkLines: String, kotlinLines: String): String {
    val r = H3Report()
    val c = code.toIntOrNull() ?: -1
    val a = arkLines.toIntOrNull() ?: -1
    val k = kotlinLines.toIntOrNull() ?: -1
    r.check(
        "transport_stream_lines",
        a > 0 && k > 0,
        "${url} → code=${c}；ArkTS 切出 **${a} 行**、Kotlin 经 NAPI 收到 **${k} 行**" +
            "（两者都必须 >0；流式逐块到达才有这个形状）",
    )
    // SSE 语义确实在共享层：ArkTS 只搬行
    val parser = SseParser()
    val events = mutableListOf<ChatEvent>()
    CANNED_SSE.forEach { line -> parser.feed(line)?.let { events += it } }
    parser.finish()?.let { events += it }
    r.check(
        "transport_sse_shared_parser",
        events.map { eventKind(it) } == listOf("thinking", "token", "token", "done"),
        "共享层 SseParser 解析 ${CANNED_SSE.size} 行夹具 → ${events.size} 个事件：" +
            "${events.joinToString("/") { eventKind(it) }}（期望 thinking/token/token/done）",
    )
    val done = events.filterIsInstance<ChatEvent.Done>().firstOrNull()
    r.check(
        "transport_sse_event_fields",
        done?.meta?.conversationId == "conv-h3" && done?.meta?.execution?.primaryPlane == "cloud",
        "done 事件字段：conversation_id=${done?.meta?.conversationId}、" +
            "execution.primary_plane=${done?.meta?.execution?.primaryPlane}（期望 conv-h3 / cloud）",
    )
    return r.render()
}

/**
 * H3 流式入口：ArkTS **每收到一个完整行**就同步调一次。
 *
 * 这是"共享层拥有 SSE 语义"的落点：ArkTS 只做 `\n` 切行与搬运，
 * `data:` / 空行分隔 / 事件类型 / `done.meta` 全部由 `SseParser` 解释。
 */
@CName("sekb_spike_h3_stream_line")
fun sekbSpikeH3StreamLine(line: String): String = SseLinePump.feed(line)

/**
 * H3 流式：开始新一轮行泵（清空计数与已有事件）。
 *
 * 为什么必须有它：`SseLinePump` 是进程内单例（H8 聊天链路会长期持有同一个 parser），
 * 不重置的话上一轮的行数会混进来，"行数 > 0" 就成了永远为真的假绿。
 */
@CName("sekb_spike_h3_stream_begin")
fun sekbSpikeH3StreamBegin(): String {
    SseLinePump.begin()
    return "pump reset"
}

/**
 * 当前行泵收到的行数（ArkTS 侧取它来填 `h3Stream` 的 `kotlinLines`）。
 *
 * 为什么不让 ArkTS 自己数：判据要证明的是"行真的跨 NAPI 到了 Kotlin"，
 * 所以这个数字必须由**Kotlin 侧**给出，ArkTS 自己数的只能当对照。
 */
@CName("sekb_spike_h3_pump_lines")
fun sekbSpikeH3PumpLines(): String = SseLinePump.lineCount().toString()

/** 只用于确认共享层 `HttpResponse` 的口径没被绕过（编译期引用）。 */
internal fun h3SelfCheckTypeAnchor(): HttpResponse = HttpResponse(-1, "")
