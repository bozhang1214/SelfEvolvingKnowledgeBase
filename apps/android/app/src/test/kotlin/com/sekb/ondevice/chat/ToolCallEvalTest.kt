package com.sekb.ondevice.chat

import org.junit.Assert.assertEquals
import org.junit.Test
import com.sekb.shared.chat.*

/** 工具调用合法率：分母是**尝试次数**，不是全部回答（否则模型越不敢用工具，数字越好看）。 */
class ToolCallEvalTest {

    @Test
    fun `no attempts yields zero rate not NaN`() {
        val e = ToolCallEval()
        assertEquals(0.0, e.legalRate(), 0.0)
        assertEquals(0, e.attempts)
    }

    @Test
    fun `non attempts do not dilute the rate`() {
        val e = ToolCallEval()
        e.record(attempted = false, legal = false)
        e.record(attempted = false, legal = false)
        assertEquals(0, e.attempts)
        e.record(attempted = true, legal = true)
        assertEquals(1.0, e.legalRate(), 0.0)
    }

    @Test
    fun `legal and illegal are counted separately`() {
        val e = ToolCallEval()
        e.record(attempted = true, legal = true)
        e.record(attempted = true, legal = false)
        e.record(attempted = true, legal = true, hallucinated = true)
        assertEquals(3, e.attempts)
        assertEquals(2, e.legal)
        assertEquals(1, e.illegal)
        assertEquals(1, e.hallucinated)
        assertEquals(2.0 / 3.0, e.legalRate(), 0.001)
        assertEquals("工具调用：尝试 3，合法 2，非法 1（其中工具名幻觉 1），合法率 66%", e.summary())
    }

    @Test
    fun `reset clears everything`() {
        val e = ToolCallEval()
        e.record(attempted = true, legal = true)
        e.reset()
        assertEquals(0, e.attempts)
        assertEquals(0.0, e.legalRate(), 0.0)
    }
}
