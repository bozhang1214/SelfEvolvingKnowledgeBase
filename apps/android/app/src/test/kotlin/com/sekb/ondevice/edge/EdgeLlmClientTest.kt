package com.sekb.ondevice.edge

import com.sekb.ondevice.FakeTransport
import com.sekb.shared.route.EdgeRuntimeConfig
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import com.sekb.shared.edge.*

/** 端侧 LLM 适配层：请求体约定（关思考 / JSON 模式）与 OpenAI 兼容流式解析。 */
class EdgeLlmClientTest {

    private val config = EdgeRuntimeConfig(
        edgeBaseUrl = "http://10.0.2.2:11434/v1",
        sekbBaseUrl = "https://example.test/sekb",
    )

    private fun client(t: FakeTransport) = OpenAiCompatibleEdgeLlm(
        transport = t, baseUrl = config.edgeBaseUrl, config = config, now = { 1_000 },
    )

    @Test
    fun `request disables thinking via reasoning_effort not think`() {
        // 踩过的坑：Ollama 原生字段是 think=false，但 OpenAI 兼容入口只认 reasoning_effort，
        // 传 think 会被客户端拒掉——所以这里必须两个都断言（前者在、后者不在）。
        val body = JSONObject(
            client(FakeTransport()).buildBody(
                "qwen3.5-2b", listOf(ChatMessage.user("hi")), 64, jsonMode = false, stream = true,
            ),
        )
        assertEquals("none", body.getString("reasoning_effort"))
        assertFalse(body.has("think"))
        assertEquals("qwen3.5-2b", body.getString("model"))
        assertTrue(body.getBoolean("stream"))
    }

    @Test
    fun `json mode adds response_format`() {
        val body = JSONObject(
            client(FakeTransport()).buildBody(
                "qwen3.5-4b", listOf(ChatMessage.user("hi")), 64, jsonMode = true, stream = false,
            ),
        )
        assertEquals("json_object", body.getJSONObject("response_format").getString("type"))
    }

    @Test
    fun `streaming collects deltas in order`() {
        val t = FakeTransport()
        t.enqueueStream(
            listOf(
                """data: {"choices":[{"delta":{"content":"端侧"}}]}""",
                "",
                """data: {"choices":[{"delta":{"content":"推理"}}]}""",
                "",
                "data: [DONE]",
                "",
            ),
        )
        val pieces = mutableListOf<String>()
        val completion = client(t).streamChat("qwen3.5-2b", listOf(ChatMessage.user("q")), 64, false) {
            pieces.add(it)
        }
        assertEquals(listOf("端侧", "推理"), pieces)
        assertEquals("端侧推理", completion.text)
        assertTrue(completion.ok)
        assertTrue(t.lastCall().url.endsWith("/chat/completions"))
        assertEquals("Bearer ollama", t.lastCall().headers["Authorization"])
    }

    @Test
    fun `http error is reported not thrown`() {
        val t = FakeTransport()
        t.enqueueStream(emptyList(), code = 500)
        val completion = client(t).streamChat("qwen3.5-2b", listOf(ChatMessage.user("q")), 64, false) {}
        assertFalse(completion.ok)
        assertTrue(completion.error.contains("500"))
    }

    @Test
    fun `transport exception becomes a failed completion`() {
        val boom = object : com.sekb.shared.net.HttpTransport {
            override fun postJson(url: String, headers: Map<String, String>, body: String, timeoutSeconds: Long) =
                throw java.io.IOException("Connection refused")
            override fun get(url: String, headers: Map<String, String>, timeoutSeconds: Long) =
                throw java.io.IOException("Connection refused")
            override fun postJsonStream(
                url: String, headers: Map<String, String>, body: String,
                timeoutSeconds: Long, onLine: (String) -> Unit,
            ): Int = throw java.io.IOException("Connection refused")
        }
        val completion = OpenAiCompatibleEdgeLlm(boom, config.edgeBaseUrl, config = config)
            .streamChat("qwen3.5-2b", listOf(ChatMessage.user("q")), 64, false) {}
        assertFalse(completion.ok)
        assertTrue(completion.error.contains("Connection refused"))
    }

    @Test
    fun `non streaming response is parsed`() {
        val t = FakeTransport()
        t.enqueueJson("""{"choices":[{"message":{"content":"{\"tool\":\"device_time\"}"}}]}""")
        val completion = client(t).complete("qwen3.5-2b", listOf(ChatMessage.user("q")), 32, true)
        assertTrue(completion.ok)
        assertTrue(completion.text.contains("device_time"))
    }

    @Test
    fun `model ids are listed for readiness display`() {
        val ids = OpenAiCompatibleEdgeLlm.parseModelIds(
            """{"data":[{"id":"qwen3.5-2b"},{"id":"qwen3.5-4b"}]}""",
        )
        assertEquals(listOf("qwen3.5-2b", "qwen3.5-4b"), ids)
    }
}
