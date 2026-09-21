package com.sekb.ondevice.core

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * [Fmt] 专测。
 *
 * **为什么值得单独测**：评测报告（`RetrievalEvalRunner` / `ToolCallEvalRunner`）与检索的
 * `below_threshold` 原因串都用它做"固定小数位 + 定宽对齐"。这些输出是对外可见的
 * **对照口径**（历史日志、文档里的实测数字都长这样），格式化一旦漂移，
 * 前后数字就没法比了——而 `String.format`（JVM 专有）在 KMP 里用不了，
 * 换成自己写的实现后，必须有测试钉住"与 `%.3f` / `%2d` 等价的输出"。
 */
class FmtTest {

    @Test
    fun `fixed matches printf-style decimal formatting`() {
        assertEquals("0.690", Fmt.fixed(0.69, 3))
        assertEquals("0.690", Fmt.fixed(0.6902, 3))
        assertEquals("0.691", Fmt.fixed(0.6906, 3))
        assertEquals("26", Fmt.fixed(26.0, 0))
        assertEquals("38", Fmt.fixed(37.6, 0))
        assertEquals("0.20", Fmt.fixed(0.2, 2))
        assertEquals("1.0", Fmt.fixed(1.0, 1))
        assertEquals("0.000", Fmt.fixed(0.0, 3))
    }

    @Test
    fun `fixed keeps sign and handles edge values`() {
        assertEquals("-0.500", Fmt.fixed(-0.5, 3))
        assertEquals("0.000", Fmt.fixed(-0.0001, 3))     // 舍入到 0 时不留 "-0.000"
        assertEquals("NaN", Fmt.fixed(Double.NaN, 3))
        assertEquals("Infinity", Fmt.fixed(Double.POSITIVE_INFINITY, 3))
    }

    @Test
    fun `pct renders integer percentages without the sign`() {
        assertEquals("87", Fmt.pct(0.8734))
        assertEquals("0", Fmt.pct(0.0))
        assertEquals("100", Fmt.pct(1.0))
        assertEquals("87.3", Fmt.pct(0.8734, 1))
    }

    @Test
    fun `padding matches printf widths`() {
        // %2d / %3d / %-14s
        assertEquals(" 7", Fmt.padStart(7, 2))
        assertEquals("26", Fmt.padStart(26, 2))
        assertEquals("  0", Fmt.padStart(0, 3))
        assertEquals("constrained   ", Fmt.padEnd("constrained", 14))
        assertEquals("free", Fmt.padEnd("free", 4))
    }

    @Test
    fun `eval summary line keeps its historical shape`() {
        // 与文档里记录的实测输出逐字符对齐（阈值=0.20  Hit@1=26/30( 87%) …）
        val line = "阈值=${Fmt.fixed(0.2, 2)}  " +
            "Hit@1=${Fmt.padStart(26, 2)}/30(${Fmt.padStart(Fmt.pct(26.0 / 30), 3)}%)  " +
            "Hit@3=${Fmt.padStart(30, 2)}/30  MRR=${Fmt.fixed(0.928, 3)}  " +
            "误召回=0/3(${Fmt.padStart(Fmt.pct(0.0), 3)}%)  " +
            "嵌入均 ${Fmt.fixed(38.0, 0)}ms(p95 ${Fmt.fixed(57.0, 0)}ms)  " +
            "检索均 ${Fmt.fixed(0.5, 1)}ms"
        assertEquals(
            "阈值=0.20  Hit@1=26/30( 87%)  Hit@3=30/30  MRR=0.928  " +
                "误召回=0/3(  0%)  嵌入均 38ms(p95 57ms)  检索均 0.5ms",
            line,
        )
    }
}
