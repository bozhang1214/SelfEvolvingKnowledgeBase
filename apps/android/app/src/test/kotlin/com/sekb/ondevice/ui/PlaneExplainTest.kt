package com.sekb.ondevice.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 原因码翻译的测试。
 *
 * 最要紧的一条是 [unknown codes are surfaced verbatim]：**绝不返回"未知原因"这种兜底话术**——
 * 那会让将来新加的原因码静默消失，用户看到的是"未知"，开发者也发现不了。
 */
class PlaneExplainTest {

    @Test
    fun `edge preferred explains why it stayed local`() {
        val s = PlaneExplain.describe("edge_preferred")
        assertTrue(s, s.contains("本机"))
    }

    @Test
    fun `budget reasons keep the actual numbers`() {
        val inS = PlaneExplain.describe("input_over_edge_budget(600>512)")
        assertTrue(inS, inS.contains("600>512"))

        val outS = PlaneExplain.describe("output_over_edge_budget(600>512)")
        assertTrue(outS, outS.contains("600>512"))
    }

    @Test
    fun `device only reasons make the privacy guarantee explicit`() {
        assertTrue(PlaneExplain.describe("device_only_data").contains("设备专属"))
        // 被拦下（一个请求都没发）与"允许本机但不许升级"必须说清楚，两者后果完全不同
        val blocked = PlaneExplain.describe("escalation_blocked:device_only_requires_local_runtime")
        assertTrue(blocked, blocked.contains("没有发出"))
        val noEscalate = PlaneExplain.describe("escalation_blocked:device_only")
        assertTrue(noEscalate, noEscalate.contains("不升级"))
    }

    /** 端侧生成中被守卫拦下——用户最该知道的是"你还没看到任何内容"。 */
    @Test
    fun `escalation signals read as user facing sentences`() {
        val degenerate = PlaneExplain.describe("degenerate")
        assertTrue(degenerate, degenerate.contains("重复"))
        // ⚠️ 不要在这里断言"你还没看到任何内容"之类的话术：
        // 那取决于守卫是否已放行过内容，`PlaneExplain` 拿不到这个信息，
        // 断言它等于把一句**我们保证不了**的承诺写进测试。

        for (code in listOf("timeout", "low_confidence", "json_invalid", "empty", "tool_hallucination", "context_overflow")) {
            val s = PlaneExplain.describe(code)
            assertTrue("$code 应该有解释", s.isNotEmpty())
            assertTrue("$code 不应回落到原始码", !s.startsWith("原因："))
        }
    }

    @Test
    fun `multiple signals are joined`() {
        val s = PlaneExplain.describe("empty,timeout")
        assertTrue(s, s.contains("；"))
    }

    /** 真机上真实出现过的码，必须有翻译（否则用户又看到原始码）。 */
    @Test
    fun `codes observed on real device are translated`() {
        val s = PlaneExplain.describe("edge_unavailable")
        assertTrue(s, !s.startsWith("原因："))
        assertTrue(s, s.contains("端侧"))
    }

    /** 诚实性不变量：认不出的码原样透出，不吞掉。 */
    @Test
    fun `unknown codes are surfaced verbatim`() {
        val s = PlaneExplain.describe("some_future_signal")
        assertTrue(s, s.contains("some_future_signal"))
    }

    /**
     * **回归测试**：升级场景下解释绝不能与徽章矛盾。
     *
     * 第一版从 `decision.plane` 推结论，于是"计划本机 → 实际云端"的升级场景里，
     * 徽章说"已上云（端侧不达标）"、解释却说"这次在本机完成"。
     * 这里直接把那种输入钉死：**不许出现"在本机完成"，且必须说明升级原因**。
     */
    @Test
    fun `escalated outcome never claims it was completed on device`() {
        val s = PlaneExplain.outcome(
            completedOnDevice = false,
            escalated = true,
            decisionReason = "edge_preferred",
            escalateReason = "degenerate",
        )
        assertTrue("不能声称在本机完成：$s", !s.contains("在本机完成"))
        assertTrue("必须说明升级原因：$s", s.contains("重复"))
        assertTrue("必须说清已改由云端：$s", s.contains("云端"))
    }

    @Test
    fun `completed on device outcome says so`() {
        val s = PlaneExplain.outcome(
            completedOnDevice = true, escalated = false,
            decisionReason = "edge_preferred", escalateReason = "",
        )
        assertTrue(s, s.startsWith("这次在本机完成"))
    }

    @Test
    fun `cloud without escalation keeps the budget numbers`() {
        val s = PlaneExplain.outcome(
            completedOnDevice = false, escalated = false,
            decisionReason = "input_over_edge_budget(600>512)", escalateReason = "",
        )
        assertTrue(s, s.startsWith("这次在云端完成"))
        assertTrue(s, s.contains("600>512"))
    }

    /** 回归：` · ` 连接的组合码不能被前缀正则吞掉后半段。 */
    @Test
    fun `dotted composition is fully described not partially`() {
        val s = PlaneExplain.describe("edge_preferred · degenerate")
        assertTrue("应包含第一段：$s", s.contains("设备能力范围内"))
        assertTrue("不应吞掉第二段：$s", s.contains("重复"))
    }

    @Test
    fun `blank input yields blank output`() {
        assertEquals("", PlaneExplain.describe(""))
        assertEquals("", PlaneExplain.describe("  "))
    }
}
