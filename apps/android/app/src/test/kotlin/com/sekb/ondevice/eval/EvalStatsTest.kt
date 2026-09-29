package com.sekb.ondevice.eval

import com.sekb.shared.eval.EvalStats
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Wilson 区间与稳定性统计的测试。
 *
 * 用**公开已知值**校验区间（10/10 的 Wilson 95% 下界约 0.722），而不是"跑一遍看结果对不对"——
 * 后者只能证明代码自洽，证明不了算法正确。
 */
class EvalStatsTest {

    @Test
    fun `wilson matches published values`() {
        val (lo10, hi10) = EvalStats.wilson(10, 10)
        assertEquals(0.722, lo10, 0.002)
        assertEquals(1.0, hi10, 1e-9)

        val (lo0, hi0) = EvalStats.wilson(0, 10)
        assertEquals(0.0, lo0, 1e-9)
        assertEquals(0.278, hi0, 0.002)

        val (lo5, hi5) = EvalStats.wilson(5, 10)
        assertEquals(0.237, lo5, 0.003)
        assertEquals(0.763, hi5, 0.003)
    }

    /**
     * **这条是整个模块存在的理由**：正态近似（Wald）在 10/10 时方差为 0，会给出 `[1, 1]`，
     * 恰好把"样本不够"这个最重要的事实藏起来。Wilson 必须给出一段有意义的区间。
     */
    @Test
    fun `wilson does not collapse to certainty on small perfect samples`() {
        val (lo, hi) = EvalStats.wilson(10, 10)
        assertFalse("10/10 不该声称 100% 有保证：[$lo, $hi]", lo > 0.9)
        assertEquals(1.0, hi, 1e-9)
    }

    @Test
    fun `interval tightens as n grows`() {
        val (loSmall, _) = EvalStats.wilson(10, 10)
        val (loBig, _) = EvalStats.wilson(1000, 1000)
        assertTrue("样本变大后下界应当上升：$loSmall → $loBig", loBig > loSmall)
        assertTrue(loBig > 0.99)
    }

    @Test
    fun `empty sample yields the full range instead of a point`() {
        assertEquals(0.0 to 1.0, EvalStats.wilson(0, 0))
    }

    @Test
    fun `formatRate carries the interval and n`() {
        val s = EvalStats.formatRate(10, 10)
        assertTrue(s, s.contains("95%CI"))
        assertTrue(s, s.contains("n=10"))
        assertEquals("n/a", EvalStats.formatRate(0, 0))
    }

    @Test
    fun `prompt stability flags inconsistent prompts and sorts worst first`() {
        val perPrompt = mapOf(
            "稳的" to listOf(true to true, true to true, true to true),
            "不稳的" to listOf(true to true, true to false, false to false),
            "全没尝试" to listOf(false to false, false to false, false to false),
        )
        val st = EvalStats.promptStability(perPrompt, repeats = 3)
        assertEquals(3, st.size)
        // 按合法率升序：全没尝试(0) 与 不稳的(1/3) 在前，稳的(3/3) 在后
        assertEquals("稳的", st.last().prompt)
        assertTrue(st.first().rate <= st.last().rate)

        val byName = st.associateBy { it.prompt }
        assertTrue(byName.getValue("稳的").consistent)
        assertFalse(byName.getValue("不稳的").consistent)
        // "从未尝试"也算稳定（结果一致），但尝试次数为 0 —— 不能被误当成"合法率 100%"
        assertTrue(byName.getValue("全没尝试").consistent)
        assertEquals(0, byName.getValue("全没尝试").attempts)
        assertEquals(0.0, byName.getValue("全没尝试").rate, 1e-9)
    }
}
