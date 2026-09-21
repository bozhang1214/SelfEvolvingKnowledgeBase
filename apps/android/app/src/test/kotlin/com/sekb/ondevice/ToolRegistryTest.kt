package com.sekb.ondevice

import com.sekb.shared.model.ToolCall
import com.sekb.shared.model.ToolResult
import com.sekb.shared.tools.DeviceTool
import com.sekb.shared.tools.PermissionAudit
import com.sekb.shared.tools.PermissionChecker
import com.sekb.shared.tools.ToolRegistry
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 三道闸门：**未注册**（工具幻觉）、**缺参数**、**未授权**。
 * 越权拦截率这个验收数字就出自这里。
 */
class ToolRegistryTest {

    private val executed = mutableListOf<String>()

    private fun tool(name: String, permission: String? = null, args: Map<String, String> = emptyMap()) =
        DeviceTool(
            name = name,
            description = "测试工具",
            requiredPermission = permission,
            args = args,
            run = { executed.add(name); ToolResult(ok = true, output = "$name-ok") },
        )

    private fun registry(vararg granted: String): ToolRegistry {
        val checker = PermissionChecker { it in granted.toSet() }
        return ToolRegistry(
            tools = listOf(
                tool("device_time"),
                tool("device_network", "android.permission.ACCESS_NETWORK_STATE"),
                tool("device_contacts_search", "android.permission.READ_CONTACTS",
                    args = mapOf("query" to "姓名关键字")),
            ),
            checker = checker,
            audit = PermissionAudit(),
            now = { 1_000L },
        )
    }

    @Test
    fun `permissionless tool runs`() {
        val r = registry()
        assertEquals("device_time-ok", r.execute(ToolCall("device_time")).output)
        assertEquals(listOf("device_time"), executed)
    }

    @Test
    fun `unregistered tool is denied and flagged as hallucination`() {
        val r = registry()
        val res = r.execute(ToolCall("device_send_sms"))
        assertFalse(res.ok)
        assertTrue(res.denied)
        assertTrue(res.reason.startsWith("tool_hallucination"))
        assertTrue(executed.isEmpty())
        assertEquals("unregistered_tool", r.auditEntries()[0].reason)
    }

    @Test
    fun `missing required args never reaches the tool`() {
        val r = registry("android.permission.READ_CONTACTS")
        val res = r.execute(ToolCall("device_contacts_search"))
        assertFalse(res.ok)
        assertFalse(res.denied)          // 是"参数问题"，不是"越权"
        assertTrue(res.reason.contains("query"))
        assertTrue(executed.isEmpty())
    }

    @Test
    fun `ungranted permission blocks execution and is audited`() {
        val r = registry()   // 什么都没授权
        val res = r.execute(ToolCall("device_contacts_search", mapOf("query" to "张")))
        assertFalse(res.ok)
        assertTrue(res.denied)
        assertTrue(executed.isEmpty())
        val entry = r.auditEntries()[0]
        assertFalse(entry.allowed)
        assertEquals("permission_denied:android.permission.READ_CONTACTS", entry.reason)
    }

    @Test
    fun `granted permission executes and records allow`() {
        val r = registry("android.permission.READ_CONTACTS")
        val res = r.execute(ToolCall("device_contacts_search", mapOf("query" to "张")))
        assertTrue(res.ok)
        assertEquals(listOf("device_contacts_search"), executed)
        assertTrue(r.auditEntries()[0].allowed)
    }

    @Test
    fun `denied rate reflects blocking`() {
        val r = registry()
        r.execute(ToolCall("device_time"))                                  // 允许
        r.execute(ToolCall("device_contacts_search", mapOf("query" to "张"))) // 拦截
        r.execute(ToolCall("device_ghost"))                                  // 拦截（幻觉）
        val stats = r.auditStats()
        assertEquals(3, stats.total)
        assertEquals(1, stats.allowed)
        assertEquals(2, stats.denied)
        assertEquals(2.0 / 3.0, stats.deniedRate, 0.001)
    }

    @Test
    fun `tool exception becomes a result not a crash`() {
        val r = ToolRegistry(
            tools = listOf(DeviceTool("boom", "总是抛", run = { throw IllegalStateException("坏了") })),
            checker = PermissionChecker { true },
        )
        val res = r.execute(ToolCall("boom"))
        assertFalse(res.ok)
        assertTrue(res.reason.contains("执行异常"))
    }

    @Test
    fun `schema prompt lists every tool and its args`() {
        val prompt = com.sekb.shared.tools.ToolCallJson.schemaPrompt(registry().all())
        assertTrue(prompt.contains("device_time"))
        assertTrue(prompt.contains("device_contacts_search"))
        assertTrue(prompt.contains("query"))
        assertTrue(prompt.contains("只输出"))
    }
}
