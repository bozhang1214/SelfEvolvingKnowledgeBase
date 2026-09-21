package com.sekb.ondevice.policy

import com.sekb.shared.core.hmacSha256Hex
import com.sekb.shared.core.sha256Hex
import com.sekb.shared.policy.EdgePolicy
import com.sekb.shared.route.EdgeRuntimeConfig
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 端侧策略（L1）的校验与应用测试。
 *
 * **最关键的是跨语言向量**：策略包的签名由**服务端 Python 实现**生成（见
 * `backend/app/core/edge_policy.py`），端侧必须算出**同一个**签名。两边只要规范化 JSON
 * 或 HMAC 有一点不一致，"验签通过"就变成自欺欺人——所以这里把服务端算出的签名硬编码进来，
 * 而不是"用 Kotlin 自己算一份再自己验"。
 */
class EdgePolicyTest {

    /** 由服务端 Python 实现生成（key=test-key）；改服务端 canonical/sign 必须同步改这里。 */
    private val serverSignature = "38a26c8c8b7c9502c1053f41ae48e973006bea2c1fbb56cfa60779dc984d679d"
    private val key = "test-key"
    private val localSpace = "BAAI/bge-small-zh-v1.5-int8@512"

    private val payload = """
        {"version":123456,"issuedAt":1700000000,"expiresAt":1700086400,
         "embeddingSpace":"$localSpace",
         "rollout":{"percent":100,"salt":"2026w39"},
         "policy":{"streamGuardChars":80,"escalateOn":["empty","timeout"],
                   "maxInputTokens":2048,"maxOutputTokens":600,"maxTtftMs":900,
                   "preferPlane":"edge",
                   "modelTier":{"default":"qwen3.5-4b","short":"qwen3.5-2b"},
                   "toolWhitelist":["kb_search","device_time"],
                   "retrievalMinScore":0.5},
         "deviceProfiles":{},
         "signature":"$serverSignature"}
    """.trimIndent()

    private fun config() = EdgeRuntimeConfig(
        edgeBaseUrl = "http://127.0.0.1:11434/v1",
        sekbBaseUrl = "https://example.test/sekb",
        embeddingSpace = localSpace,
    )

    // ── 跨语言一致性（最重要）──────────────────────────────────────────────

    @Test
    fun `kotlin signature matches the server-generated signature`() {
        val obj = com.sekb.shared.core.JsonObject.parse(payload)!!
        assertEquals(
            "端侧算出的签名必须与服务端 Python 实现一致（规范化 JSON + HMAC 都要一致）",
            serverSignature, EdgePolicy.signatureOf(obj, key),
        )
    }

    @Test
    fun `hmac matches rfc4231-style known vector`() {
        // 广为引用的向量：key="key"，message="The quick brown fox jumps over the lazy dog"
        assertEquals(
            "f7bc83f430538424b13298e6aa6fb143ef4d59a14946175997479dbc2d1a3cd8",
            hmacSha256Hex("key", "The quick brown fox jumps over the lazy dog"),
        )
    }

    @Test
    fun `sha256 matches known vector`() {
        assertEquals(
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            sha256Hex("abc"),
        )
    }

    // ── 拒绝路径 ──────────────────────────────────────────────────────────

    @Test
    fun `valid policy is applied to local config`() {
        val out = EdgePolicy.apply(payload, key, config(), deviceId = "dev-1", nowMillis = 1700000001)
        assertTrue("应通过校验：$out", out is EdgePolicy.Outcome.Applied)
        val applied = out as EdgePolicy.Outcome.Applied
        assertEquals(123456, applied.version)
        assertEquals(80, applied.config.guardChars)
        assertEquals(600, applied.config.maxOutputTokens)
        assertEquals(900, applied.config.maxTtftMs)
        assertEquals(setOf("empty", "timeout"), applied.config.escalateOn)
        assertEquals("qwen3.5-4b", applied.config.models["default"])
        assertEquals(setOf("kb_search", "device_time"), applied.config.availableTools)
        assertEquals(listOf("escalateOn", "maxInputTokens", "maxOutputTokens", "maxTtftMs",
            "modelTier", "preferPlane", "streamGuardChars", "toolWhitelist"), applied.appliedKeys)
        // 阈值与提示词包不在 EdgeRuntimeConfig 里：如实列进 ignored，不装作已应用
        assertEquals(listOf("retrievalMinScore"), applied.ignoredKeys)
        assertEquals(0.5, EdgePolicy.retrievalMinScore(payload)!!, 1e-9)
    }

    @Test
    fun `tampered policy is rejected`() {
        val tampered = payload.replace("\"streamGuardChars\":80", "\"streamGuardChars\":300")
        val out = EdgePolicy.apply(tampered, key, config(), nowMillis = 1700000001)
        assertEquals("policy_bad_signature", (out as EdgePolicy.Outcome.Rejected).reason)
    }

    @Test
    fun `wrong key is rejected`() {
        val out = EdgePolicy.apply(payload, "other-key", config(), nowMillis = 1700000001)
        assertEquals("policy_bad_signature", (out as EdgePolicy.Outcome.Rejected).reason)
    }

    @Test
    fun `space mismatch is rejected`() {
        val out = EdgePolicy.apply(
            payload, key, config().copy(embeddingSpace = "BAAI/bge-small-zh-v1.5@512"),
            nowMillis = 1700000001,
        )
        val reason = (out as EdgePolicy.Outcome.Rejected).reason
        assertTrue("空间不符必须拒绝且说明双方空间：$reason", reason.startsWith("policy_space_mismatch"))
    }

    @Test
    fun `expired policy is rejected`() {
        val out = EdgePolicy.apply(payload, key, config(), nowMillis = 1700086401)
        assertEquals("policy_expired", (out as EdgePolicy.Outcome.Rejected).reason)
    }

    @Test
    fun `unknown key rejects the whole package`() {
        // 手工构造一份"签名正确但含未登记键"的包：模拟误配置
        val obj = com.sekb.shared.core.JsonObject.parse(payload)!!
        val body = obj.optJSONObject("policy")!!.put("sneakyFlag", true)
        obj.put("policy", body)
        obj.put("signature", EdgePolicy.signatureOf(obj, key))
        val out = EdgePolicy.apply(obj.toString(), key, config(), nowMillis = 1700000001)
        val reason = (out as EdgePolicy.Outcome.Rejected).reason
        assertTrue("白名单外一律拒绝整包：$reason", reason.startsWith("policy_unknown_key"))
        assertTrue(reason.contains("sneakyFlag"))
    }

    @Test
    fun `reserved deviceProfiles is allowed but not read`() {
        val obj = com.sekb.shared.core.JsonObject.parse(payload)!!
        // deviceProfiles 在顶层（预留字段），放一个非空内容也不该让整包被拒
        obj.put("deviceProfiles", com.sekb.shared.core.JsonObject().put("pixel", 1))
        obj.put("signature", EdgePolicy.signatureOf(obj, key))
        val out = EdgePolicy.apply(obj.toString(), key, config(), nowMillis = 1700000001)
        assertTrue("预留字段允许出现（不读取）：$out", out is EdgePolicy.Outcome.Applied)
        assertFalse((out as EdgePolicy.Outcome.Applied).appliedKeys.contains("deviceProfiles"))
    }

    @Test
    fun `rollout skip leaves config untouched`() {
        val obj = com.sekb.shared.core.JsonObject.parse(payload)!!
        obj.put("rollout", com.sekb.shared.core.JsonObject().put("percent", 0).put("salt", "x"))
        obj.put("signature", EdgePolicy.signatureOf(obj, key))
        val out = EdgePolicy.apply(obj.toString(), key, config(), deviceId = "dev-1",
            nowMillis = 1700000001)
        assertEquals("policy_rollout_skip", (out as EdgePolicy.Outcome.Rejected).reason)
    }

    @Test
    fun `rollout bucketing is deterministic and matches server formula`() {
        val obj = com.sekb.shared.core.JsonObject.parse(payload)!!
        obj.put("rollout", com.sekb.shared.core.JsonObject().put("percent", 30).put("salt", "2026w39"))
        val first = (0 until 200).map { EdgePolicy.appliesToDevice(obj, "dev-$it") }
        val second = (0 until 200).map { EdgePolicy.appliesToDevice(obj, "dev-$it") }
        assertEquals("同一设备必须稳定命中", first, second)
        val ratio = first.count { it } / 200.0
        assertTrue("30% 灰度实际比例 $ratio 偏离过大", ratio > 0.15 && ratio < 0.45)
    }

    @Test
    fun `malformed json is rejected without throwing`() {
        assertEquals("policy_not_json",
            (EdgePolicy.apply("not json at all", key, config()) as EdgePolicy.Outcome.Rejected).reason)
        assertEquals("policy_not_json",
            (EdgePolicy.apply("", key, config()) as EdgePolicy.Outcome.Rejected).reason)
    }

    @Test
    fun `canonical json matches python formatting rules`() {
        val o = com.sekb.shared.core.JsonObject()
            .put("b", 1).put("a", com.sekb.shared.core.JsonArray().put(1).put(2))
            .put("s", "中文 \"引号\"")
        assertEquals("""{"a":[1,2],"b":1,"s":"中文 \"引号\""}""", EdgePolicy.canonicalJson(o))
    }
}
