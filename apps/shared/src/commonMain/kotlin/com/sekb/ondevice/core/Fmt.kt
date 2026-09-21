package com.sekb.ondevice.core

import kotlin.math.abs
import kotlin.math.round

/**
 * 数字/字符串格式化（评测报告与日志用）。
 *
 * **为什么需要它**：`String.format` / `"%.3f".format(x)` 是 JVM 专有，
 * `commonMain` 编不过（同样是 iOS 编译器查出来的）。而评测报告又必须要"固定小数位"的数字，
 * 否则报告在不同端长得不一样，对比起来全靠眼力。
 *
 * 刻意只做**够用的几种**（固定小数位、百分比、定宽对齐），不实现 printf 全集：
 * 需要更复杂排版时，宁可显式拼字符串，也不要在这里长出一个半成品格式化引擎。
 */
object Fmt {

    /** 固定小数位（四舍五入），如 `fixed(0.8734, 3)` → `"0.873"`。 */
    fun fixed(value: Double, digits: Int = 3): String {
        if (value.isNaN()) return "NaN"
        if (value.isInfinite()) return if (value > 0) "Infinity" else "-Infinity"
        val factor = pow10(digits)
        val scaled = round(abs(value) * factor).toLong()
        val sign = if (value < 0 && scaled != 0L) "-" else ""
        if (digits == 0) return "$sign$scaled"
        val intPart = scaled / factor
        val fracPart = scaled % factor
        return "$sign$intPart.${fracPart.toString().padStart(digits, '0')}"
    }

    /** 百分比（**不带** % 号）：`pct(0.873)` → `"87"`。 */
    fun pct(rate: Double, digits: Int = 0): String = fixed(rate * 100, digits)

    /** 左对齐补空格（对应 `%-14s`）。 */
    fun padEnd(s: String, width: Int): String =
        if (s.length >= width) s else s + " ".repeat(width - s.length)

    /** 右对齐补空格（对应 `%2d` / `%3d`）。 */
    fun padStart(s: String, width: Int): String =
        if (s.length >= width) s else " ".repeat(width - s.length) + s

    fun padStart(value: Int, width: Int): String = padStart(value.toString(), width)

    private fun pow10(digits: Int): Long {
        var r = 1L
        repeat(digits.coerceAtLeast(0)) { r *= 10 }
        return r
    }
}
