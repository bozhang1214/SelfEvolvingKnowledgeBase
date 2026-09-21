package com.sekb.ondevice.net

// ── OkHttp 实现（Android/JVM 侧）─────────────────────────────────────────────
// 为什么要单独一个文件：`HttpTransport` 是四端共用的**端口**（要搬进 KMP shared），
// 而 OkHttp 是 JVM/Android 专有的实现。两者放在同一个文件里，抽 shared 时就得先拆文件——
// 不如现在拆开：接口文件里不出现任何 okhttp 符号，才能在 iOS/鸿蒙编译。
// iOS 侧对应实现是 NSURLSession，鸿蒙侧是 libcurl（见 docs/多端跨端-工程议题（网络层·包体·热修复）.md §1）。
import java.util.concurrent.TimeUnit
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

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
