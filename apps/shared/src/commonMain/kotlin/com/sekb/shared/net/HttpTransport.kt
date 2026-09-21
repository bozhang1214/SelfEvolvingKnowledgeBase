package com.sekb.shared.net


/** 一次 HTTP 响应（只保留端侧用得到的两个字段）。 */
data class HttpResponse(val code: Int, val body: String) {
    val isOk: Boolean get() = code in 200..299
}

/**
 * HTTP 传输抽象。
 *
 * **为什么要这层**：端侧的两条链路（宿主机 Ollama / SEKB 云端）都要
 * "发 JSON、收 SSE 流"，而这一层是**唯一**碰网络的地方——把它抽出来之后，
 * 上层（客户端与编排器）就能用假传输在 JVM 单测里跑到完整的端云协同流程，
 * 不需要模拟器、不需要联网。这是本项目"功能与协议先在单测里验"的关键。
 *
 * 刻意做成**同步**接口：调用方（ViewModel）自己决定在哪个 Dispatcher 上跑，
 * 这样编排器可以保持纯同步、易测；异步只留在 UI 层。
 */
interface HttpTransport {
    fun postJson(url: String, headers: Map<String, String>, body: String, timeoutSeconds: Long = 60): HttpResponse

    fun get(url: String, headers: Map<String, String>, timeoutSeconds: Long = 30): HttpResponse

    /**
     * POST 并**逐行**回调响应体（SSE 用）。
     *
     * @param onLine 每收到一行就回调（含空行；SSE 的空行是事件分隔符，不能丢）
     * @return HTTP 状态码
     */
    fun postJsonStream(
        url: String,
        headers: Map<String, String>,
        body: String,
        timeoutSeconds: Long = 300,
        onLine: (String) -> Unit,
    ): Int
}
