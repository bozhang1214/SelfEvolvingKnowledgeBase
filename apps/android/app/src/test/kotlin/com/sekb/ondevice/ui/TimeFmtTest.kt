package com.sekb.ondevice.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.ZoneId
import java.time.ZonedDateTime

/**
 * 相对时间文案的测试。
 *
 * 时区与"当前时间"都**注入**，所以结果与运行环境无关——否则这类测试会在 CI（UTC）与
 * 开发机（UTC+8）上给出不同答案，变成"本地绿、CI 红"的经典噪音。
 */
class TimeFmtTest {

    private val zone = ZoneId.of("Asia/Shanghai")

    private fun at(y: Int, mo: Int, d: Int, h: Int, mi: Int): Long =
        ZonedDateTime.of(y, mo, d, h, mi, 0, 0, zone).toInstant().toEpochMilli()

    private val now = at(2026, 9, 25, 15, 0)

    @Test
    fun `within a minute is just now`() {
        assertEquals("刚刚", TimeFmt.relative(now - 30_000, now, zone))
        // 设备时钟被往前调导致"未来时间"时，也不能出现"负几分钟前"
        assertEquals("刚刚", TimeFmt.relative(now + 5_000, now, zone))
    }

    @Test
    fun `within an hour counts minutes`() {
        assertEquals("5 分钟前", TimeFmt.relative(now - 5 * 60_000, now, zone))
        assertEquals("59 分钟前", TimeFmt.relative(now - 59 * 60_000, now, zone))
    }

    @Test
    fun `earlier today shows clock time`() {
        assertEquals("09:30", TimeFmt.relative(at(2026, 9, 25, 9, 30), now, zone))
    }

    @Test
    fun `yesterday is labelled`() {
        assertEquals("昨天 22:10", TimeFmt.relative(at(2026, 9, 24, 22, 10), now, zone))
    }

    @Test
    fun `within a week counts days`() {
        assertEquals("3 天前", TimeFmt.relative(at(2026, 9, 22, 10, 0), now, zone))
        assertEquals("6 天前", TimeFmt.relative(at(2026, 9, 19, 10, 0), now, zone))
    }

    /** 超过一周改用日期：这时"37 天前"反而不如一个具体日期直观。 */
    @Test
    fun `beyond a week falls back to a date`() {
        assertEquals("2026-09-01", TimeFmt.relative(at(2026, 9, 1, 8, 0), now, zone))
    }

    /** 跨月/跨年不能靠"天差"算错（用 LocalDate 而不是毫秒除法就是为了这个）。 */
    @Test
    fun `month and year boundaries are handled`() {
        val nye = at(2027, 1, 1, 9, 0)
        assertEquals("昨天 23:30", TimeFmt.relative(at(2026, 12, 31, 23, 30), nye, zone))
        assertEquals("2026-12-01", TimeFmt.relative(at(2026, 12, 1, 12, 0), nye, zone))
    }

    @Test
    fun `unknown timestamp yields empty string`() {
        assertEquals("", TimeFmt.relative(0, now, zone))
        assertEquals("", TimeFmt.relative(-1, now, zone))
    }

    @Test
    fun `result is never blank for valid input`() {
        for (daysAgo in 0..30) {
            val v = TimeFmt.relative(now - daysAgo * 86_400_000L, now, zone)
            assertTrue("daysAgo=$daysAgo 不该为空", v.isNotEmpty())
        }
    }
}
