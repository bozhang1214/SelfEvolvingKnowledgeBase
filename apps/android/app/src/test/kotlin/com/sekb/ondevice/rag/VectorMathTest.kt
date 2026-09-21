package com.sekb.ondevice.rag

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import com.sekb.shared.rag.*

class VectorMathTest {

    @Test
    fun `identical vectors score one`() {
        val v = floatArrayOf(0.6f, 0.8f)
        assertEquals(1.0, VectorMath.cosine(v, v), 1e-9)
    }

    @Test
    fun `orthogonal vectors score zero`() {
        assertEquals(0.0, VectorMath.cosine(floatArrayOf(1f, 0f), floatArrayOf(0f, 1f)), 1e-9)
    }

    @Test
    fun `dimension mismatch is zero not exception`() {
        // 检索链路上不该因为一条脏数据整条挂掉；但也不能给出"看起来像分数"的值
        assertEquals(0.0, VectorMath.cosine(floatArrayOf(1f, 0f), floatArrayOf(1f, 0f, 0f)), 0.0)
    }

    @Test
    fun `zero vector is zero not NaN`() {
        val s = VectorMath.cosine(floatArrayOf(0f, 0f), floatArrayOf(1f, 1f))
        assertEquals(0.0, s, 0.0)
        assertTrue(!s.isNaN())
    }

    @Test
    fun `topK sorts desc and breaks ties by id`() {
        val items = listOf(
            "b" to floatArrayOf(0f, 1f),
            "a" to floatArrayOf(0f, 1f),
            "c" to floatArrayOf(1f, 0f),
        )
        val top = VectorMath.topK(floatArrayOf(0f, 1f), items, k = 3)
        assertEquals(listOf("a", "b", "c"), top.map { it.first })   // 同分按 id 升序 → 结果稳定
        assertEquals(1.0, top[0].second, 1e-9)
    }

    @Test
    fun `topK with zero or negative k is empty`() {
        assertTrue(VectorMath.topK(floatArrayOf(1f), listOf("a" to floatArrayOf(1f)), 0).isEmpty())
        assertTrue(VectorMath.topK(floatArrayOf(1f), listOf("a" to floatArrayOf(1f)), -1).isEmpty())
    }
}
