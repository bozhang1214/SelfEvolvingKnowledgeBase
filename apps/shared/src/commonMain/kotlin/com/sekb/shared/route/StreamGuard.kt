package com.sekb.shared.route

/**
 * 流式**前缀守卫**（服务端 `LLMFactory._astream_guarded` 的客户端版）。
 *
 * 为什么需要：token 一旦显示给用户就收不回。所以端侧流式输出**先攒 `guardChars`
 * 个字符**，在这段前缀上判断是否该改道云端——此时用户什么都还没看到，等于"免费改道"。
 *
 * 攒不满就说明输出本来就短：那就退化成"整段评估一次"，语义与 `evaluate(partial=false)` 一致。
 */
class StreamGuard(
    private val router: PlaneRouter,
    private val guardChars: Int = 60,
    private val responseFormat: String? = null,
    private val availableTools: Set<String> = router.config.availableTools,
) {

    private val buffer = StringBuilder()
    private var released = false
    private var ttftMillis = 0.0

    /** 前缀已放行：后续 chunk 直接透传。 */
    val isReleased: Boolean get() = released

    val bufferedChars: Int get() = buffer.length

    /**
     * 已缓冲但**尚未放行**的前缀。
     *
     * 用途只有一个：判定"该改道"但**不允许改道**时（DEVICE_ONLY 数据永不出端，RFC §5.2），
     * 不能把端侧已经算出来的东西白白丢掉——用户宁可看到一段不完美的本机回答，
     * 也不该看到空白。
     */
    fun bufferedText(): String = buffer.toString()

    fun noteFirstToken(millis: Double) {
        if (ttftMillis == 0.0) ttftMillis = millis
    }

    /**
     * 喂入一个 chunk，返回下一步动作。
     *
     * - [Decision.Buffering]：继续攒（还没到阈值）
     * - [Decision.Release]：放行（含已缓冲内容；调用方必须先吐 buffered 再吐后续 chunk）
     * - [Decision.Escalate]：丢弃已缓冲内容，改道云端（用户未见到任何 token）
     */
    fun offer(piece: String): Decision {
        if (released) return Decision.Passthrough
        buffer.append(piece)
        if (buffer.length < guardChars) return Decision.Buffering
        return judge(buffer.toString())
    }

    /** 流结束时调用：若始终没攒够阈值，就用"整段评估"收尾。 */
    fun finish(): Decision {
        if (released) return Decision.Passthrough
        return judge(buffer.toString(), partial = false)
    }

    private fun judge(prefix: String, partial: Boolean = true): Decision {
        val signals = router.evaluate(
            prefix, responseFormat = responseFormat,
            ttftMillis = ttftMillis, availableTools = availableTools, partial = partial,
        )
        return if (router.shouldEscalate(signals)) {
            Decision.Escalate(signals, prefix)
        } else {
            released = true
            Decision.Release(prefix)
        }
    }

    sealed interface Decision {
        /** 还没攒够，继续等。 */
        data object Buffering : Decision

        /** 已达阈值且通过：调用方放行 [prefix]，之后 `offer` 返回 [Passthrough]。 */
        data class Release(val prefix: String) : Decision

        /** 已放行过：直接透传当前 chunk（不能再改道）。 */
        data object Passthrough : Decision

        /** 前缀不达标：丢弃 [prefix] 并改道云端。 */
        data class Escalate(val signals: List<String>, val prefix: String) : Decision
    }
}
