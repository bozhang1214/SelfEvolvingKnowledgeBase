package com.sekb.ondevice

import com.sekb.shared.tools.ToolCallJson
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 工具调用解析：把"意图对"与"语法对"分开——两者改进方向完全不同
 * （前者靠提示词/模型档位，后者靠约束解码）。
 */
class ToolCallJsonTest {

    @Test
    fun `parses clean call`() {
        val p = ToolCallJson.parse("""{"tool":"device_time","args":{"tz":"Asia/Shanghai"}}""")
        assertTrue(p is ToolCallJson.Parsed.Ok)
        val call = (p as ToolCallJson.Parsed.Ok).call
        assertEquals("device_time", call.tool)
        assertEquals("Asia/Shanghai", call.args["tz"])
    }

    @Test
    fun `parses fenced json with prose around it`() {
        val p = ToolCallJson.parse(
            "好的，我来查一下：\n```json\n{\"tool\": \"device_network\", \"args\": {}}\n```\n",
        )
        assertTrue(p is ToolCallJson.Parsed.Ok)
    }

    @Test
    fun `accepts single quotes and trailing comma`() {
        val p = ToolCallJson.parse("{'tool': 'device_time', 'args': {'tz': 'UTC',},}")
        assertTrue(p is ToolCallJson.Parsed.Ok)
        assertEquals("UTC", (p as ToolCallJson.Parsed.Ok).call.args["tz"])
    }

    @Test
    fun `accepts name and arguments aliases`() {
        val p = ToolCallJson.parse("""{"name":"device_time","arguments":{}}""")
        assertEquals("device_time", (p as ToolCallJson.Parsed.Ok).call.tool)
    }

    @Test
    fun `missing tool name is invalid`() {
        val p = ToolCallJson.parse("""{"args":{}}""")
        assertTrue(p is ToolCallJson.Parsed.Invalid)
        assertEquals("missing_tool_name", (p as ToolCallJson.Parsed.Invalid).reason)
    }

    @Test
    fun `no json at all is reported distinctly`() {
        val p = ToolCallJson.parse("我不知道该怎么查。")
        assertEquals("no_json_object", (p as ToolCallJson.Parsed.Invalid).reason)
    }

    @Test
    fun `extract handles braces inside strings`() {
        val got = ToolCallJson.extractJsonObject("""前缀 {"tool":"x","args":{"q":"a}b{c"}} 后缀""")
        assertEquals("""{"tool":"x","args":{"q":"a}b{c"}}""", got)
    }

    @Test
    fun `unbalanced json is not extracted`() {
        assertEquals(null, ToolCallJson.extractJsonObject("""{"tool":"x""""))
    }

    @Test
    fun `known tool check distinguishes hallucination`() {
        val known = setOf("device_time", "device_network")
        assertTrue(ToolCallJson.isKnownTool(com.sekb.shared.model.ToolCall("device_time"), known))
        assertFalse(ToolCallJson.isKnownTool(com.sekb.shared.model.ToolCall("device_sms"), known))
    }
}
