package com.sekb.ondevice.policy

import com.sekb.shared.core.JsonObject
import com.sekb.shared.net.HttpResponse
import com.sekb.shared.net.HttpTransport
import com.sekb.shared.policy.EdgePolicy
import com.sekb.shared.policy.InMemoryPolicyStore
import com.sekb.shared.policy.PolicyAuditEntry
import com.sekb.shared.policy.PolicyFetcher
import com.sekb.shared.policy.PolicyRefreshResult
import com.sekb.shared.route.EdgeRuntimeConfig
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 策略拉取/应用/回滚/审计的测试。
 *
 * 这一层最容易出的错不是"算错签名"，而是**失败路径改坏了状态**：
 * 拉不到、被拒、灰度跳过时，端侧必须**保持原配置**并留下可排查的审计记录。
 * 所以这里重点钉失败路径。
 */
class PolicyFetcherTest {

    private val key = "test-key"
    private val space = "BAAI/bge-small-zh-v1.5-int8@512"
    // 由服务端 Python 实现生成（key=test-key）；与上面 EdgePolicyTest 的那份是**不同 payload**，
    // 两份都硬编码，防止"用 Kotlin 自己签、自己验"的循环验证。
    private val serverSignature = "cd1cd32f4da8e90fedbe55f8c4673e28f0cc43b3c05e72ee02559b047120c76c"

    private val validPayload = """
        {"version":123456,"issuedAt":1700000000,"expiresAt":1700086400,
         "embeddingSpace":"$space","rollout":{"percent":100,"salt":"2026w39"},
         "policy":{"streamGuardChars":80,"maxOutputTokens":600,"preferPlane":"edge"},
         "deviceProfiles":{},"signature":"$serverSignature"}
    """.trimIndent()

    private fun config() = EdgeRuntimeConfig(
        edgeBaseUrl = "http://127.0.0.1:11434/v1",
        sekbBaseUrl = "https://example.test/sekb",
        embeddingSpace = space,
    )

    private class FakeTransport(
        private val code: Int = 200,
        private val body: String = "",
        private val throwOnGet: Boolean = false,
    ) : HttpTransport {
        var calls = 0
        var lastUrl = ""
        var lastAuth = ""
        override fun postJson(url: String, headers: Map<String, String>, body: String, timeoutSeconds: Long) =
            HttpResponse(404, "")
        override fun get(url: String, headers: Map<String, String>, timeoutSeconds: Long): HttpResponse {
            calls++; lastUrl = url; lastAuth = headers["Authorization"].orEmpty()
            if (throwOnGet) throw java.io.IOException("boom")
            return HttpResponse(code, body)
        }
        override fun postJsonStream(url: String, headers: Map<String, String>, body: String,
                                    timeoutSeconds: Long, onLine: (String) -> Unit): Int = 404
    }

    private fun fetcher(t: HttpTransport, store: InMemoryPolicyStore = InMemoryPolicyStore(),
                        audits: MutableList<PolicyAuditEntry> = mutableListOf()) =
        Triple(PolicyFetcher(t, "https://example.test/sekb", key, store,
            now = { 1700000001 }, audit = { audits += it }), store, audits)

    @Test
    fun `successful refresh applies policy and persists it`() {
        val (f, store, audits) = fetcher(FakeTransport(200, validPayload))
        val out = f.refresh("dtok", config(), deviceId = "dev-1")
        assertTrue("应成功应用：$out", out is PolicyRefreshResult.Applied)
        val applied = out as PolicyRefreshResult.Applied
        assertEquals(123456, applied.version)
        assertEquals(80, applied.config.guardChars)
        assertEquals(600, applied.config.maxOutputTokens)
        assertEquals(validPayload, store.loadActive())          // 落盘（下次启动无需再拉）
        assertEquals("fetch_ok", audits.single().action)
        assertEquals(123456, audits.single().version)
        val urlProbe = FakeTransport(200, validPayload)
        val (f2, _, _) = fetcher(urlProbe)
        f2.refresh("dtok", config())
        assertTrue("请求应打到策略端点：${urlProbe.lastUrl}",
            urlProbe.lastUrl.endsWith("/api/v1/edge/policy"))
    }

    @Test
    fun `refresh sends device bearer token`() {
        val t = FakeTransport(200, validPayload)
        val (f, _, _) = fetcher(t)
        f.refresh("device-abc", config())
        assertEquals("Bearer device-abc", t.lastAuth)
    }

    @Test
    fun `http error keeps previous config and audits reason`() {
        val (f, store, audits) = fetcher(FakeTransport(503, "upstream down"))
        val before = config()
        val out = f.refresh("dtok", before)
        assertEquals("policy_http_503", (out as PolicyRefreshResult.Skipped).reason)
        assertNull("失败不得落盘", store.loadActive())
        assertEquals("fetch_http_error", audits.single().action)
    }

    @Test
    fun `network exception is a skip not a crash`() {
        val (f, store, audits) = fetcher(FakeTransport(throwOnGet = true))
        val out = f.refresh("dtok", config())
        assertEquals("policy_fetch_exception", (out as PolicyRefreshResult.Skipped).reason)
        assertNull(store.loadActive())
        assertEquals("fetch_exception", audits.single().action)
    }

    @Test
    fun `bad signature keeps previous config`() {
        val tampered = validPayload.replace("\"streamGuardChars\":80", "\"streamGuardChars\":300")
        val (f, store, audits) = fetcher(FakeTransport(200, tampered))
        val out = f.refresh("dtok", config())
        assertEquals("policy_bad_signature", (out as PolicyRefreshResult.Skipped).reason)
        assertNull(store.loadActive())
        assertEquals("rejected", audits.single().action)
    }

    @Test
    fun `grey rollout skip keeps previous config`() {
        // 用一份签名正确的 percent=0 策略（现场用 EdgePolicy 现算签名，保证与校验同源）
        val obj = JsonObject.parse(validPayload)!!
        obj.put("rollout", JsonObject().put("percent", 0).put("salt", "x"))
        obj.put("signature", EdgePolicy.signatureOf(obj, key))
        val (f, store, audits) = fetcher(FakeTransport(200, obj.toString()))
        val out = f.refresh("dtok", config(), deviceId = "dev-1")
        assertEquals("policy_rollout_skip", (out as PolicyRefreshResult.Skipped).reason)
        assertNull(store.loadActive())
        assertEquals("rejected", audits.single().action)
    }

    @Test
    fun `second save pushes first into previous slot and rollback restores it`() {
        val store = InMemoryPolicyStore()
        val audits = mutableListOf<PolicyAuditEntry>()
        // 第一份：guard=80
        val (f1, _, _) = fetcher(FakeTransport(200, validPayload), store, audits)
        val first = (f1.refresh("t", config()) as PolicyRefreshResult.Applied).config
        assertEquals(80, first.guardChars)

        // 第二份：guard=120（现算签名）
        val obj = JsonObject.parse(validPayload)!!
        obj.put("policy", JsonObject().put("streamGuardChars", 120))
        obj.put("signature", EdgePolicy.signatureOf(obj, key))
        val (f2, _, _) = fetcher(FakeTransport(200, obj.toString()), store, audits)
        val second = (f2.refresh("t", first) as PolicyRefreshResult.Applied).config
        assertEquals(120, second.guardChars)
        assertEquals("上一份应进 previous 槽", validPayload, store.loadPrevious())

        // 回滚 → 回到 guard=80
        val (f3, _, _) = fetcher(FakeTransport(), store, audits)
        val rolled = f3.rollback(second, deviceId = "dev-1")
        assertTrue("应能回滚：$rolled", rolled is PolicyRefreshResult.Applied)
        assertEquals(80, (rolled as PolicyRefreshResult.Applied).config.guardChars)
        assertEquals(listOf("fetch_ok", "fetch_ok", "rolled_back"), audits.map { it.action })
    }

    @Test
    fun `rollback without previous slot is a clean skip`() {
        val (f, _, audits) = fetcher(FakeTransport())
        val out = f.rollback(config())
        assertEquals("policy_no_previous", (out as PolicyRefreshResult.Skipped).reason)
        assertTrue(audits.isEmpty() || audits.single().action == "rollback_rejected")
    }

    @Test
    fun `active version and retrieval threshold are readable for self-test and wiring`() {
        val store = InMemoryPolicyStore()
        val (f, _, _) = fetcher(FakeTransport(200, validPayload), store)
        assertEquals(0, f.activeVersion())                  // 没拉过 → 0
        f.refresh("t", config())
        assertEquals(123456, f.activeVersion())
        // 这份 payload 没带 retrievalMinScore → null（回落到本地默认 0.5）
        assertNull(f.activeRetrievalMinScore())
    }

    @Test
    fun `audit entries never contain the policy body`() {
        val (f, _, audits) = fetcher(FakeTransport(200, validPayload))
        f.refresh("t", config())
        val text = audits.joinToString { it.toString() }
        // 审计**允许**出现键名（appliedKeys 就是给排查用的），但不该出现策略正文（JSON 结构/取值）
        assertTrue("审计里不该出现策略正文", !text.contains("\"policy\"") && !text.contains("{"))
        assertTrue("appliedKeys 里应有键名（这是排查依据）", text.contains("streamGuardChars"))
    }
}
