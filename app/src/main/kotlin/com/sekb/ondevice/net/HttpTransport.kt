package com.sekb.ondevice.net

import java.util.concurrent.TimeUnit
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

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

/** OkHttp 实现（唯一的真实实现；测试用假传输）。 */
class OkHttpTransport(private val client: OkHttpClient = defaultClient()) : HttpTransport {

    override fun postJson(url: String, headers: Map<String, String>, body: String, timeoutSeconds: Long): HttpResponse {
        val request = Request.Builder()
            .url(url)
            .apply { headers.forEach { (k, v) -> header(k, v) } }
            .post(body.toRequestBody(JSON))
            .build()
        client.newBuilder().readTimeout(timeoutSeconds, TimeUnit.SECONDS).build()
            .newCall(request).execute().use { resp ->
                return HttpResponse(resp.code, resp.body?.string().orEmpty())
            }
    }

    override fun get(url: String, headers: Map<String, String>, timeoutSeconds: Long): HttpResponse {
        val request = Request.Builder()
            .url(url)
            .apply { headers.forEach { (k, v) -> header(k, v) } }
            .get()
            .build()
        client.newBuilder().readTimeout(timeoutSeconds, TimeUnit.SECONDS).build()
            .newCall(request).execute().use { resp ->
                return HttpResponse(resp.code, resp.body?.string().orEmpty())
            }
    }

    override fun postJsonStream(
        url: String,
        headers: Map<String, String>,
        body: String,
        timeoutSeconds: Long,
        onLine: (String) -> Unit,
    ): Int {
        val request = Request.Builder()
            .url(url)
            .apply {
                headers.forEach { (k, v) -> header(k, v) }
                // SSE 必须显式声明 Accept，否则某些网关会缓冲整个响应
                header("Accept", "text/event-stream")
            }
            .post(body.toRequestBody(JSON))
            .build()
        // readTimeout 对 SSE 要放大：两个 token 之间可能间隔很久（长回答/云端排队）
        val streaming = client.newBuilder()
            .readTimeout(timeoutSeconds, TimeUnit.SECONDS)
            .build()
        streaming.newCall(request).execute().use { resp ->
            val source = resp.body?.source()
            if (source != null) {
                while (true) {
                    val line = source.readUtf8Line() ?: break
                    onLine(line)
                }
            }
            return resp.code
        }
    }

    companion object {
        private val JSON = "application/json; charset=utf-8".toMediaType()

        /**
         * 默认客户端。
         *
         * `retryOnConnectionFailure(true)`：端侧网络（尤其切网时）抖动很常见，
         * 但**只重试连接失败**，不重试已发出的流式请求——那会重复消费 token。
         */
        fun defaultClient(): OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .writeTimeout(30, TimeUnit.SECONDS)
            .retryOnConnectionFailure(true)
            .build()
    }
}
