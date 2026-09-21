package com.sekb.ondevice

import com.sekb.shared.tools.PermissionAudit
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class PermissionAuditTest {

    @Test
    fun `empty audit has zero rate not NaN`() {
        val stats = PermissionAudit().stats()
        assertEquals(0, stats.total)
        assertEquals(0.0, stats.deniedRate, 0.0)
    }

    @Test
    fun `recent is newest first`() {
        val a = PermissionAudit()
        a.record("t1", true, "ok", nowMillis = 1)
        a.record("t2", false, "permission_denied:X", nowMillis = 2)
        assertEquals(listOf("t2", "t1"), a.recent(10).map { it.tool })
    }

    @Test
    fun `capacity trims oldest entries`() {
        val a = PermissionAudit(capacity = 3)
        repeat(5) { a.record("t$it", true, "ok", nowMillis = it.toLong()) }
        assertEquals(3, a.all().size)
        assertEquals(listOf("t2", "t3", "t4"), a.all().map { it.tool })
    }

    @Test
    fun `clear empties the log`() {
        val a = PermissionAudit()
        a.record("t", true, "ok")
        a.clear()
        assertTrue(a.all().isEmpty())
    }
}
