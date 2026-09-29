package com.sekb.ondevice.ui

import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.temporal.ChronoUnit

/**
 * 会话列表用的相对时间文案（纯逻辑，可被 JVM 单测直接跑——见 `TimeFmtTest`）。
 *
 * ## 为什么不直接显示完整时间戳
 *
 * 用户扫列表时判断"这是我什么时候聊的"靠的是**模糊的正确**（"刚刚"/"昨天"），
 * 不是精确到秒。而列出 `2026-09-25 15:42:07` 会让每一行都长得一样、眼睛抓不到重点。
 * 但超过一周后模糊就没意义了（"37 天前" 反而不如日期直观），所以做分段降级。
 *
 * `zone` 可注入：跨时区/跨端测试要能钉住结果，不能依赖运行环境的默认时区。
 */
object TimeFmt {

    private val HHMM = DateTimeFormatter.ofPattern("HH:mm")
    private val YMD = DateTimeFormatter.ofPattern("yyyy-MM-dd")

    /** @param epochMillis 目标时刻；<=0 视为"未知"（返回空串，让调用方决定怎么显示） */
    fun relative(epochMillis: Long, nowMillis: Long, zone: ZoneId = ZoneId.systemDefault()): String {
        if (epochMillis <= 0L) return ""
        val diff = nowMillis - epochMillis
        // 未来时间（设备时钟被调整过）不显示"负几分钟前"，按"刚刚"处理更不容易误解
        if (diff < 60_000L) return "刚刚"
        if (diff < 3_600_000L) return "${diff / 60_000L} 分钟前"

        val target = Instant.ofEpochMilli(epochMillis).atZone(zone)
        val today = Instant.ofEpochMilli(nowMillis).atZone(zone).toLocalDate()
        val date = target.toLocalDate()
        val days = ChronoUnit.DAYS.between(date, today)
        return when {
            days <= 0L -> target.format(HHMM)
            days == 1L -> "昨天 ${target.format(HHMM)}"
            days < 7L -> "$days 天前"
            else -> date.format(YMD)
        }
    }
}
