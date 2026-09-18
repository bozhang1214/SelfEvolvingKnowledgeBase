package com.sekb.ondevice.net

import com.sekb.ondevice.FakeTransport
import com.sekb.ondevice.model.RouteEventPayload
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** SEKB 客户端：每个动作的**请求形状**与**错误分类**都要对得上协议（§2–§4）。 */
class SekbApiTest {

    private val now = 1_700_000_000_000L
    private val base = "https://example.test/sekb"
    private fun api(t: FakeTransport) = SekbApi(t, base, now = { now })

    @Test
    fun `login returns access token`() {
        val t = FakeTransport()
        t.enqueueJson("""{"access_token":"user-token-1"}""")
        assertEquals("user-token-1", api(t).login("a@b.c", "pw"))
        assertTrue(t.lastCall().url.endsWith("/api/v1/auth/login"))
    }

    @Test
    fun `enroll uses user token and honours returned ttl`() {
        val t = FakeTransport()
        t.enqueueJson("""{"device_id":"dev-1","device_token":"tok-1","expires_in_hours":168}""")
        val cred = api(t).enroll("user-token", "我的 Pixel", appVersion = "0.1.0",
            embeddingSpace = "bge@512")

        assertEquals("dev-1", cred.deviceId)
        assertEquals("tok-1", cred.deviceToken)
        assertEquals("Bearer user-token", t.lastCall().headers["Authorization"])
        val body = JSONObject(t.lastCall().body)
        assertEquals("android", body.getString("platform"))
        assertEquals("我的 Pixel", body.getString("name"))
        // TTL 用服务端给的 168，而不是端侧猜的默认值
        assertEquals(now + 168L * 3600 * 1000, cred.expiresAtMillis)
        assertFalse(cred.needsRotation(now))
    }

    @Test
    fun `enroll failure is a typed exception`() {
        val t = FakeTransport()
        t.enqueueJson("""{"detail":"设备数超上限"}""", code = 429)
        val e = runCatching { api(t).enroll("u", "d", appVersion = "1", embeddingSpace = "x") }
            .exceptionOrNull()
        assertTrue(e is SekbApiException)
        assertEquals(429, (e as SekbApiException).code)
        assertTrue(e.detail.contains("超上限"))
    }

    @Test
    fun `refresh uses device token`() {
        val t = FakeTransport()
        t.enqueueJson("""{"device_id":"dev-1","device_token":"tok-2","expires_in_hours":720}""")
        val cred = api(t).refresh("tok-1")
        assertEquals("tok-2", cred.deviceToken)
        assertEquals("Bearer tok-1", t.lastCall().headers["Authorization"])
        assertTrue(t.lastCall().url.endsWith("/api/v1/device/refresh"))
    }

    @Test
    fun `route event body matches protocol`() {
        val t = FakeTransport()
        t.enqueueJson("""{"status":"ok"}""")
        val ok = api(t).reportRouteEvent(
            "tok",
            RouteEventPayload(
                eventId = "evt-9", role = "chat", plane = "edge", reason = "edge_preferred",
                model = "qwen3.5-4b", tier = "default", inputTokens = 12, outputTokens = 30,
                latencyMs = 888.0, escalated = false, signals = listOf("degenerate"),
                versions = mapOf("app_version" to "0.1.0"),
            ),
        )
        assertTrue(ok)
        val body = JSONObject(t.lastCall().body)
        assertEquals("evt-9", body.getString("event_id"))   // 幂等键
        assertEquals("edge", body.getString("plane"))
        assertEquals("degenerate", body.getJSONArray("signals").getString(0))
        assertEquals("0.1.0", body.getJSONObject("versions").getString("app_version"))
    }

    @Test
    fun `route event failure is reported as false not thrown`() {
        val t = FakeTransport()
        t.enqueueJson("""{"detail":"仅接受设备 token"}""", code = 403)
        assertFalse(
            api(t).reportRouteEvent("tok", RouteEventPayload("e", "chat", "edge", "r")),
        )
    }

    @Test
    fun `chat stream forwards tokens and returns execution`() {
        val t = FakeTransport()
        t.enqueueStream(
            listOf(
                """data: {"type":"thinking","content":"正在检索"}""", "",
                """data: {"type":"token","content":"端"}""", "",
                """data: {"type":"token","content":"侧"}""", "",
                """data: {"type":"done","meta":{"conversation_id":"c-1",""" +
                    """"execution":{"primary_plane":"edge","model":"qwen3.5-4b","escalated":0}}}""", "",
            ),
        )
        val tokens = mutableListOf<String>()
        val thinking = mutableListOf<String>()
        val result = api(t).chatStream("dtok", "问题", null,
            onToken = { tokens.add(it) }, onThinking = { thinking.add(it) })

        assertEquals(listOf("端", "侧"), tokens)
        assertEquals(listOf("正在检索"), thinking)
        assertEquals("edge", result.execution!!.primaryPlane)
        assertEquals("qwen3.5-4b", result.execution.model)
        assertEquals("c-1", result.conversationId)      // 会话 ID 必须回流
        assertEquals("Bearer dtok", t.lastCall().headers["Authorization"])
        assertTrue(t.lastCall().headers["Accept"].isNullOrEmpty().not() ||
            t.lastCall().url.endsWith("/api/v1/chat/stream"))
    }

    @Test
    fun `chat stream error event becomes exception`() {
        val t = FakeTransport()
        t.enqueueStream(listOf("""data: {"type":"error","detail":"该会话正在回复中"}""", ""))
        val e = runCatching {
            api(t).chatStream("dtok", "问题", null, onToken = {})
        }.exceptionOrNull()
        assertTrue(e is SekbApiException)
        assertTrue(e!!.message!!.contains("正在回复中"))
    }
}
