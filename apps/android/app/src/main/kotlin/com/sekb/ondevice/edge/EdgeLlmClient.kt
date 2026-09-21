package com.sekb.ondevice.edge

import com.sekb.ondevice.core.JsonX
import com.sekb.ondevice.net.HttpTransport
import com.sekb.ondevice.net.OpenAiSseParser
import com.sekb.ondevice.route.EdgeRuntimeConfig
import com.sekb.ondevice.core.JsonArray
import com.sekb.ondevice.core.JsonObject

/**
 * 端侧 LLM 适配层（**可插拔**）。
 *
 * 当前实现走 OpenAI 兼容 HTTP 端点（模拟器里指向宿主机 Ollama）。
 * 真机阶段的 llama.cpp / MediaPipe 只需要再写一个实现类，
 * **上层（路由、守卫、工具、审计、上报）一行都不用改**——这是 M2 的架构约定。
 */
interface EdgeLlm {
    /**
     * 流式对话。
     *
     * @param onToken 每个增量文本的即时回调（**必须先经过流式前缀守卫**再给用户看，
     *   见 `ChatOrchestrator`）
     * @return 完整答案（拼好的），以及首字耗时（毫秒）
     */
    fun streamChat(
        model: String,
        messages: List<ChatMessage>,
        maxTokens: Int,
        jsonMode: Boolean,
        onToken: (String) -> Unit,
    ): EdgeCompletion

    /** 非流式（分类/工具调用这类短任务用，省一次连接开销）。 */
    fun complete(model: String, messages: List<ChatMessage>, maxTokens: Int, jsonMode: Boolean): EdgeCompletion
}

data class ChatMessage(val role: String, val content: String) {
    companion object {
        fun system(content: String) = ChatMessage("system", content)
        fun user(content: String) = ChatMessage("user", content)
        fun assistant(content: String) = ChatMessage("assistant", content)
    }
}

data class EdgeCompletion(
    val text: String,
    /** 首字耗时（ms）：端侧 TTFT 是升级信号 `timeout` 的判据 */
    val ttftMillis: Double,
    val totalMillis: Double,
    val ok: Boolean,
    val error: String = "",
    /** 推理内容（引擎把思维链单独返回时用于排查；默认关思考时应为空） */
    val reasoning: String = "",
)

/** OpenAI 兼容实现（宿主机 Ollama / 任何兼容端点）。 */
class OpenAiCompatibleEdgeLlm(
    private val transport: HttpTransport,
    private val baseUrl: String,
    private val apiKey: String = "ollama",
    private val config: EdgeRuntimeConfig,
    private val now: () -> Long = { System.currentTimeMillis() },
) : EdgeLlm {

    private val parser = OpenAiSseParser()

    override fun streamChat(
        model: String,
        messages: List<ChatMessage>,
        maxTokens: Int,
        jsonMode: Boolean,
        onToken: (String) -> Unit,
    ): EdgeCompletion {
        val start = now()
        var firstTokenAt = 0L
        val sb = StringBuilder()
        val code = try {
            transport.postJsonStream(
                url = "$baseUrl/chat/completions",
                headers = headers(),
                body = buildBody(model, messages, maxTokens, jsonMode, stream = true),
                timeoutSeconds = 300,
            ) { line ->
                val piece = parser.feed(line)
                if (piece.isNotEmpty()) {
                    if (firstTokenAt == 0L) firstTokenAt = now()
                    sb.append(piece)
                    onToken(piece)
                }
            }
        } catch (e: Exception) {
            val elapsed = (now() - start).toDouble()
            return EdgeCompletion("", elapsed, elapsed, ok = false, error = e.message ?: "网络异常")
        }
        val end = now()
        if (code !in 200..299) {
            return EdgeCompletion(sb.toString(), (firstTokenAt - start).toDouble().coerceAtLeast(0.0),
                (end - start).toDouble(), ok = false, error = "HTTP $code")
        }
        val ttft = if (firstTokenAt > 0) (firstTokenAt - start).toDouble() else (end - start).toDouble()
        return EdgeCompletion(sb.toString(), ttft, (end - start).toDouble(), ok = true)
    }

    override fun complete(model: String, messages: List<ChatMessage>, maxTokens: Int, jsonMode: Boolean): EdgeCompletion {
        val start = now()
        val resp = try {
            transport.postJson(
                url = "$baseUrl/chat/completions",
                headers = headers(),
                body = buildBody(model, messages, maxTokens, jsonMode, stream = false),
                timeoutSeconds = 120,
            )
        } catch (e: Exception) {
            val elapsed = (now() - start).toDouble()
            return EdgeCompletion("", elapsed, elapsed, ok = false, error = e.message ?: "网络异常")
        }
        val elapsed = (now() - start).toDouble()
        if (!resp.isOk) {
            return EdgeCompletion("", elapsed, elapsed, ok = false, error = parser.parseError(resp.body))
        }
        return EdgeCompletion(
            text = parser.parseFull(resp.body),
            ttftMillis = elapsed,
            totalMillis = elapsed,
            ok = true,
            reasoning = parser.parseReasoning(resp.body),
        )
    }

    private fun headers(): Map<String, String> = mapOf(
        "Content-Type" to "application/json",
        "Authorization" to "Bearer $apiKey",
    )

    /**
     * 请求体。两个与服务端一致的约定：
     *
     * 1. **关思考**：实测同一意图分类任务 2283ms → 89ms（25 倍）。
     *    Ollama 的原生字段是 `think:false`，但 OpenAI 兼容入口只认 `reasoning_effort`——
     *    服务端 M1 也踩过同一个坑（`think` 会被 OpenAPI 客户端拒掉）。
     * 2. **JSON 模式**：`response_format={"type":"json_object"}`，
     *    这是"约束解码开关"在 OpenAI 兼容协议里的对应物；合法率对比实验靠它来分组。
     */
    fun buildBody(model: String, messages: List<ChatMessage>, maxTokens: Int, jsonMode: Boolean, stream: Boolean): String {
        val arr = JsonArray()
        for (m in messages) {
            arr.put(JsonObject().put("role", m.role).put("content", m.content))
        }
        val body = JsonObject()
            .put("model", model)
            .put("messages", arr)
            .put("max_tokens", maxTokens)
            .put("stream", stream)
            .put("temperature", 0.2)
        if (jsonMode) body.put("response_format", JsonObject().put("type", "json_object"))
        if (config.disableThinking) body.put("reasoning_effort", "none")
        return body.toString()
    }

    /**
     * 预热：把模型加载进内存并保持（消除首次调用的权重加载开销）。
     *
     * 为什么必须做：SEKB 侧实测首次调用 ~6.5s（加载权重），之后 0.2–0.7s。
     * 不预热的话，"端侧赢延迟"在第一次调用上完全不成立，而首字超时信号
     * （`maxTtftMs`）会把这个冷启动误判成"端侧不行"从而改道云端。
     *
     * 走 Ollama 原生 `/api/generate`（空调起，不产生 token）+ `keep_alive`。
     */
    fun warmup(model: String, keepAlive: String = "30m"): Boolean = try {
        val nativeBase = baseUrl.removeSuffix("/v1").removeSuffix("/")
        transport.postJson(
            url = "$nativeBase/api/generate",
            headers = jsonHeaders(),
            body = JsonObject().put("model", model).put("prompt", "").put("keep_alive", keepAlive).toString(),
            timeoutSeconds = 180,
        ).isOk
    } catch (e: Exception) {
        false
    }

    private fun jsonHeaders(): Map<String, String> = mapOf(
        "Content-Type" to "application/json",
        "Authorization" to "Bearer $apiKey",
    )

    /** 端点是否可达（用于 UI 上显示"端侧就绪/不可用"，不参与决策）。 */
    fun isReachable(): Boolean = try {
        transport.get("$baseUrl/models", headers(), timeoutSeconds = 5).isOk
    } catch (e: Exception) {
        false
    }

    companion object {
        fun modelListProbe(baseUrl: String): String = "$baseUrl/models"
        fun parseModelIds(body: String): List<String> {
            val obj = JsonX.parseObject(body) ?: return emptyList()
            val arr = obj.optJSONArray("data") ?: return emptyList()
            return (0 until arr.length()).mapNotNull { arr.optJSONObject(it)?.optString("id") }
        }
    }
}
