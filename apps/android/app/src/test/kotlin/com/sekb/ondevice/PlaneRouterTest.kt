package com.sekb.ondevice

import com.sekb.shared.model.Plane
import com.sekb.shared.route.EdgeRuntimeConfig
import com.sekb.shared.route.PlaneRouter
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 客户端决策逻辑：**口径必须与服务端一致**（否则两边的端侧完成率/升级率不可比）。
 * 服务端对应实现见 SEKB `backend/app/core/plane_router.py` 与 `tests/unit/test_plane_router.py`。
 */
class PlaneRouterTest {

    /** 生产默认值（不在这里重复写死数字：测试替身写死过一次，结果掩盖了真实默认值的问题）。 */
    private val defaults = EdgeRuntimeConfig(
        edgeBaseUrl = "http://10.0.2.2:11434/v1",
        sekbBaseUrl = "https://example.test/sekb",
    )

    private fun router(
        maxInput: Int = defaults.maxInputTokens,
        maxOutput: Int = defaults.maxOutputTokens,
        preferEdge: Boolean = true,
        deviceOnly: Set<String> = emptySet(),
        // R10：DEVICE_ONLY 是否可执行取决于端侧端点是否**本机**，所以端点必须可注入
        edgeBaseUrl: String = defaults.edgeBaseUrl,
    ) = PlaneRouter(
        EdgeRuntimeConfig(
            edgeBaseUrl = edgeBaseUrl,
            sekbBaseUrl = defaults.sekbBaseUrl,
            maxInputTokens = maxInput,
            maxOutputTokens = maxOutput,
            preferEdge = preferEdge,
            deviceOnlyRoles = deviceOnly,
        ),
    )

    @Test
    fun `short task stays on device`() {
        val d = router().decide("chat", listOf("端侧推理为什么省电？"))
        assertEquals(Plane.EDGE, d.plane)
        assertEquals("edge_preferred", d.reason)
        assertTrue(d.escalationAllowed())
    }

    @Test
    fun `default chat role must be able to run on device`() {
        // 回归：端侧输出预算一旦低于 chat 的预期输出（400），每一次聊天都会判给云端，
        // "端侧优先"就名存实亡。这条测试钉住这个默认值组合。
        val d = router().decide("chat", listOf("端侧推理为什么省电？"))
        assertEquals(Plane.EDGE, d.plane)
    }

    @Test
    fun `long expected output goes to cloud with explicit reason`() {
        val d = router().decide("news_report", listOf("写日报"))
        assertEquals(Plane.CLOUD, d.plane)
        assertTrue(d.reason.startsWith("output_over_edge_budget"))
    }

    @Test
    fun `oversized input goes to cloud`() {
        val d = router().decide("chat", listOf("字".repeat(3000)))
        assertEquals(Plane.CLOUD, d.plane)
        assertTrue(d.reason.startsWith("input_over_edge_budget"))
    }

    @Test
    fun `device only data never leaves the device`() {
        // 用**本机**端点：这才是"端侧"该有的样子（10.0.2.2 是开发机，属 R10 要拦的情况）
        val d = router(deviceOnly = setOf("chat"), edgeBaseUrl = "http://127.0.0.1:11434/v1")
            .decide("chat", listOf("我的联系人里有谁"), deviceData = true)
        assertEquals(Plane.EDGE, d.plane)
        assertEquals("device_only_data", d.reason)
        assertTrue(d.deviceOnly)
        // 硬边界：即使端侧答得不好也不许升级（RFC §5.2）
        assertFalse(d.escalationAllowed())
        assertFalse("本机端点不该被拦", d.isBlocked())
    }

    // ───────────── R10：DEVICE_ONLY 的"本机"判据（2026-09-21） ─────────────

    @Test
    fun `device only data is refused when edge endpoint is not this device`() {
        // 模拟器里 edgeBaseUrl=10.0.2.2（开发机）：DEVICE_ONLY 数据**一个请求都不该发**
        val d = router(deviceOnly = setOf("chat"), edgeBaseUrl = "http://10.0.2.2:11434/v1")
            .decide("chat", listOf("我的联系人里有谁"), deviceData = true)
        assertEquals(Plane.EDGE, d.plane)
        assertEquals("device_only_requires_local_runtime", d.reason)
        assertTrue(d.deviceOnly)
        assertTrue("非本机端点必须被拦", d.isBlocked())
        assertFalse(d.escalationAllowed())
    }

    @Test
    fun `device only refusal also applies to LAN and tailnet hosts`() {
        // 局域网 / 尾网 / 域名：一律当"非本机"（宁可拒绝，也不猜）
        for (remote in listOf(
            "http://192.168.1.20:11434/v1",
            "http://100.71.24.105:11434/v1",
            "http://edge-host.local:11434/v1",
            "http://0.0.0.0:11434/v1",
        )) {
            val d = router(deviceOnly = setOf("chat"), edgeBaseUrl = remote)
                .decide("chat", listOf("q"), deviceData = true)
            assertTrue("$remote 应被判为非本机", d.isBlocked())
        }
    }

    @Test
    fun `localhost spellings count as local`() {
        for (local in listOf(
            "http://127.0.0.1:11434/v1",
            "http://localhost:11434/v1",
            "http://[::1]:11434/v1",
            "http://127.0.0.1:11434",
        )) {
            val d = router(deviceOnly = setOf("chat"), edgeBaseUrl = local)
                .decide("chat", listOf("q"), deviceData = true)
            assertFalse("$local 应被判为本机", d.isBlocked())
            assertEquals("device_only_data", d.reason)
        }
    }

    @Test
    fun `non device-only traffic is unaffected by endpoint locality`() {
        // 普通数据打到远端端点是**正常**的（端云协同就是这么用的），不能被 R10 误伤
        val d = router(edgeBaseUrl = "http://10.0.2.2:11434/v1").decide("chat", listOf("hi"))
        assertEquals(Plane.EDGE, d.plane)
        assertEquals("edge_preferred", d.reason)
        assertFalse(d.isBlocked())
    }

    @Test
    fun `prefer cloud forces cloud`() {
        val d = router(preferEdge = false).decide("chat", listOf("hi"))
        assertEquals(Plane.CLOUD, d.plane)
        assertEquals("prefer_cloud", d.reason)
    }

    @Test
    fun `tier follows output budget`() {
        val r = router()
        assertEquals("short", r.tierFor("supervisor", null))
        assertEquals("default", r.tierFor("chat", 400))
        assertEquals("quality", r.tierFor("job_analysis", 1500))
    }

    @Test
    fun `token estimate matches server semantics`() {
        // CJK 1 token/字；非 CJK 4 字符 1 token
        assertEquals(4, PlaneRouter.estimateTokens("端侧推理"))
        assertEquals(1, PlaneRouter.estimateTokens("abcd"))
        assertEquals(0, PlaneRouter.estimateTokens(""))
    }

    // ---------- 6 类升级信号 ----------

    @Test
    fun `empty output is a signal`() {
        assertTrue(PlaneRouter.SIGNAL_EMPTY in router().evaluate("   "))
    }

    @Test
    fun `degenerate loop is a signal`() {
        val signals = router().evaluate("。" .repeat(120))
        assertTrue(PlaneRouter.SIGNAL_DEGENERATE in signals)
    }

    @Test
    fun `short output is never judged degenerate`() {
        // 与服务端一致：40 字符以下不判退化（短答案是正常的）
        assertFalse(PlaneRouter.SIGNAL_DEGENERATE in router().evaluate("。".repeat(20)))
    }

    @Test
    fun `abstain wording is low confidence`() {
        assertTrue(PlaneRouter.SIGNAL_LOW_CONFIDENCE in
            router().evaluate("我不确定，需要更多信息才能判断。"))
    }

    @Test
    fun `json role with broken json is a signal`() {
        val signals = router().evaluate("我猜是 news 吧", responseFormat = "json")
        assertTrue(PlaneRouter.SIGNAL_JSON_INVALID in signals)
    }

    @Test
    fun `slow first token is a timeout signal`() {
        val signals = router().evaluate("正常回答", ttftMillis = 1500.0)
        assertTrue(PlaneRouter.SIGNAL_TIMEOUT in signals)
    }

    @Test
    fun `unknown tool name is a hallucination signal`() {
        val signals = router().evaluate("""{"tool": "device_send_sms", "args": {}}""")
        assertTrue(PlaneRouter.SIGNAL_TOOL_HALLUCINATION in signals)
    }

    @Test
    fun `registered tool name is not a hallucination`() {
        val signals = router().evaluate("""{"tool": "device_time", "args": {}}""")
        assertFalse(PlaneRouter.SIGNAL_TOOL_HALLUCINATION in signals)
    }

    // ---------- 前缀评估（流式守卫用） ----------

    @Test
    fun `partial prefix never flags incomplete json`() {
        val r = router()
        val prefix = """{"tool": "device_ti"""
        assertTrue(PlaneRouter.SIGNAL_JSON_INVALID in r.evaluate(prefix, responseFormat = "json"))
        assertFalse(PlaneRouter.SIGNAL_JSON_INVALID in
            r.evaluate(prefix, responseFormat = "json", partial = true))
    }

    @Test
    fun `partial prefix still catches degenerate and abstain`() {
        val r = router()
        assertTrue(PlaneRouter.SIGNAL_DEGENERATE in r.evaluate("。".repeat(100), partial = true))
        assertTrue(PlaneRouter.SIGNAL_LOW_CONFIDENCE in r.evaluate("我不确定这个问题", partial = true))
    }

    @Test
    fun `should escalate respects configured signals`() {
        val r = router()
        assertTrue(r.shouldEscalate(listOf(PlaneRouter.SIGNAL_DEGENERATE)))
        assertFalse(r.shouldEscalate(emptyList()))
    }
}
