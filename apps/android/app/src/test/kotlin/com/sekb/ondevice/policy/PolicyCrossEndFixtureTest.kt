package com.sekb.ondevice.policy

import com.sekb.ondevice.BuildConfig
import com.sekb.shared.core.sha256Hex
import com.sekb.shared.policy.EdgePolicy
import com.sekb.shared.route.EdgeRuntimeConfig
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * **跨端策略夹具**：用服务端代码签的包，端侧必须认得。
 *
 * ## 为什么这个测试非有不可（它抓到了一个真缺陷）
 *
 * `EdgePolicyTest` 里的跨语言向量测试用的是自选密钥 `"test-key"`——它验证了
 * **规范化 JSON 与 HMAC 算法**两侧一致，但**验不到默认密钥的值**。这个缝里真的漏过东西：
 *
 * - 服务端未配置 `SEKB_EDGE_POLICY_SECRET` 时返回 `sha256("edge-policy:dev-only").hexdigest()`
 *   （`8d1c82bf…`）；
 * - 端侧 `BuildConfig.DEFAULT_EDGE_POLICY_KEY` 是原文字面量 `"edge-policy:dev-only"`；
 * - `build.gradle.kts` 的注释还写着"服务端未配时会派生同一个开发密钥"——**这句话是错的**。
 *
 * 后果：默认配置下，端侧会把**所有真正由服务端签发的策略**判为 `policy_bad_signature`，
 * 也就是说 M4.5 的验收③（改阈值 → 不发版、不重启也生效）**从未真正端到端跑通过**。
 * 而两侧的既有测试**结构上都不可能发现**：后端是自签自验，端侧用的是自选 key。
 *
 * 所以这里放一份**由 `backend/app/core/edge_policy.py` 生成的真实签名包**
 * （`src/test/resources/policy/server-signed-dev.json`，用开发默认密钥签），
 * 断言端侧用 `BuildConfig` 的默认密钥能验过。
 * 任一侧单方面改动密钥，这条就会红。
 */
class PolicyCrossEndFixtureTest {

    /** 服务端产出的夹具：`retrievalMinScore = 0.7`，空间戳 `BAAI/bge-small-zh-v1.5@512`。 */
    private fun fixture(): String =
        checkNotNull(javaClass.classLoader?.getResourceAsStream("policy/server-signed-dev.json")) {
            "找不到跨端夹具 policy/server-signed-dev.json（资源打包规则变了？）"
        }.bufferedReader().use { it.readText() }

    private fun config() = EdgeRuntimeConfig(
        edgeBaseUrl = "http://127.0.0.1:11434/v1",
        sekbBaseUrl = "https://example.test/sekb",
        embeddingSpace = "BAAI/bge-small-zh-v1.5@512",
    )

    @Test
    fun `endpoint default key is the documented literal`() {
        // 这条把"端侧默认值"本身钉住：服务端那边的守卫测试会来读这个字面量
        assertEquals("edge-policy:dev-only", BuildConfig.DEFAULT_EDGE_POLICY_KEY)
    }

    @Test
    fun `policy signed by the server with the dev default key is accepted`() {
        val out = EdgePolicy.apply(
            payloadJson = fixture(),
            key = BuildConfig.DEFAULT_EDGE_POLICY_KEY,
            current = config(),
            deviceId = "dev-1",
            // ⚠️ 必须用**真实的毫秒**时间戳。第一版这里我写了秒级的 1_800_000_001，
            // 于是"服务端 expiresAt 是秒"这个致命单位缺陷**恰好**没暴露出来——
            // 测试里用了不真实的时间，就等于没测时间语义。
            nowMillis = System.currentTimeMillis(),
        )
        assertTrue(
            "端侧必须认得服务端用开发默认密钥签出的策略，否则验收③不可能跑通：$out",
            out is EdgePolicy.Outcome.Applied,
        )
        val applied = out as EdgePolicy.Outcome.Applied
        assertEquals(2048, applied.config.maxInputTokens)
        // 阈值不在 EdgeRuntimeConfig 里 → 如实进 ignored，由调用方按 retrievalMinScore 单独取
        assertTrue(applied.ignoredKeys.contains("retrievalMinScore"))
        assertEquals(0.7, EdgePolicy.retrievalMinScore(fixture())!!, 1e-9)
    }

    /**
     * **反向守卫**：证明上面那条不是"夹具恰好通过"。
     *
     * 用历史上那个错误的派生密钥（sha256 而非原文）去验，必须被判为签名错误——
     * 这既证明夹具真的走了验签，也把这个缺陷本身固化成回归测试。
     */
    @Test
    fun `the old broken derived dev key is rejected as bad signature`() {
        val brokenKey = sha256Hex("edge-policy:dev-only")
        assertEquals(64, brokenKey.length)   // 它就是当年服务端返回的东西
        val out = EdgePolicy.apply(
            payloadJson = fixture(),
            key = brokenKey,
            current = config(),
            deviceId = "dev-1",
            // ⚠️ 必须用**真实的毫秒**时间戳。第一版这里我写了秒级的 1_800_000_001，
            // 于是"服务端 expiresAt 是秒"这个致命单位缺陷**恰好**没暴露出来——
            // 测试里用了不真实的时间，就等于没测时间语义。
            nowMillis = System.currentTimeMillis(),
        )
        assertEquals(EdgePolicy.Outcome.Rejected("policy_bad_signature"), out)
    }
}
