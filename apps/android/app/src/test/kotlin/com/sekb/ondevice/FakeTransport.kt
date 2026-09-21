package com.sekb.ondevice

import com.sekb.shared.net.HttpResponse
import com.sekb.shared.net.HttpTransport

/** 记录请求、按序返回响应的假传输（**不出网**，可在 JVM 单测里跑到完整协议流程）。 */
class FakeTransport : HttpTransport {

    data class Call(
        val method: String,
        val url: String,
        val headers: Map<String, String>,
        val body: String,
    )

    val calls = mutableListOf<Call>()

    /** 非流式响应队列（按顺序取；用完后重复最后一条）。 */
    private val queue = ArrayDeque<HttpResponse>()

    /** 流式响应：每行的列表（可多条，按顺序取）。 */
    private val streams = ArrayDeque<Pair<Int, List<String>>>()

    fun enqueue(response: HttpResponse) { queue.addLast(response) }

    fun enqueueJson(body: String, code: Int = 200) = enqueue(HttpResponse(code, body))

    fun enqueueStream(lines: List<String>, code: Int = 200) { streams.addLast(code to lines) }

    fun lastCall(): Call = calls.last()

    override fun postJson(url: String, headers: Map<String, String>, body: String, timeoutSeconds: Long): HttpResponse {
        calls.add(Call("POST", url, headers, body))
        return if (queue.isEmpty()) HttpResponse(200, "{}") else queue.removeFirst()
    }

    override fun get(url: String, headers: Map<String, String>, timeoutSeconds: Long): HttpResponse {
        calls.add(Call("GET", url, headers, ""))
        return if (queue.isEmpty()) HttpResponse(200, "{}") else queue.removeFirst()
    }

    override fun postJsonStream(
        url: String,
        headers: Map<String, String>,
        body: String,
        timeoutSeconds: Long,
        onLine: (String) -> Unit,
    ): Int {
        calls.add(Call("POST-STREAM", url, headers, body))
        val (code, lines) = if (streams.isEmpty()) 200 to emptyList() else streams.removeFirst()
        for (line in lines) onLine(line)
        return code
    }
}
