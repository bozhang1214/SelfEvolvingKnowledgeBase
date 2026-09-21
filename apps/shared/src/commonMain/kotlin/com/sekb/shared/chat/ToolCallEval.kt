package com.sekb.shared.chat

/**
 * "工具调用 JSON 合法率"的累加器（M2 的验收数字之一，RFC §9）。
 *
 * 定义：**合法次数 / 尝试次数**。分母用"尝试"而不是"全部回答"是刻意的——
 * 用后者的话，模型越是不敢用工具（全答普通文本），这个数字反而越好看。
 *
 * 约束解码（grammar / `response_format=json_object`）开与关各跑一遍同一批提示词，
 * 两组数字的差就是"语法约束值多少"的证据。
 */
class ToolCallEval {

    var attempts: Int = 0
        private set
    var legal: Int = 0
        private set
    var illegal: Int = 0
        private set
    /** 解析合法但工具名不存在（幻觉） */
    var hallucinated: Int = 0
        private set

    fun record(attempted: Boolean, legal: Boolean, hallucinated: Boolean = false) {
        if (!attempted) return
        attempts++
        if (legal) this.legal++ else illegal++
        if (hallucinated) this.hallucinated++
    }

    /** 合法率；没有尝试过时返回 0.0（而不是 NaN）。 */
    fun legalRate(): Double = if (attempts == 0) 0.0 else legal.toDouble() / attempts

    fun summary(): String = "工具调用：尝试 $attempts，合法 $legal，非法 $illegal" +
        (if (hallucinated > 0) "（其中工具名幻觉 $hallucinated）" else "") +
        "，合法率 ${(legalRate() * 100).toInt()}%"

    fun reset() {
        attempts = 0; legal = 0; illegal = 0; hallucinated = 0
    }
}
