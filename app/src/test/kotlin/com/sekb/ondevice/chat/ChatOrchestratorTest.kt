package com.sekb.ondevice.chat

import com.sekb.ondevice.edge.ChatMessage
import com.sekb.ondevice.edge.EdgeCompletion
import com.sekb.ondevice.edge.EdgeLlm
import com.sekb.ondevice.model.ExecutionInfo
import com.sekb.ondevice.model.Plane
import com.sekb.ondevice.model.RouteEventPayload
import com.sekb.ondevice.route.EdgeRuntimeConfig
import com.sekb.ondevice.route.PlaneRouter
import com.sekb.ondevice.tools.DeviceTool
import com.sekb.ondevice.tools.PermissionAudit
import com.sekb.ondevice.tools.PermissionChecker
import com.sekb.ondevice.tools.ToolRegistry
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * **端云协同全流程**（无网、无模拟器）：
 * 决策 → 端侧流式 → 前缀守卫 → 必要时改道云端 → 上报。
 *
 * 这是本仓库最重要的测试：它把"改道必须发生在用户看到任何字符之前"
 * 这条产品级承诺变成了可执行的断言。
 */
class ChatOrchestratorTest {

    private val config = EdgeRuntimeConfig(
        edgeBaseUrl = "http://10.0.2.2:11434/v1",
        sekbBaseUrl = "https://example.test/sekb",
        guardChars = 40,
    )

    private class FakeEdge(
        private val pieces: List<String> = emptyList(),
        private val ok: Boolean = true,
        private val error: String = "",
        private val followUpText: String = "现在是 12:00。",
        private val ttft: Double = 42.0,
    ) : EdgeLlm {
        var streamCalls = 0
        var completeCalls = 0
        var lastModel = ""
        var lastFollowUpMessages: List<ChatMessage> = emptyList()

        override fun streamChat(
            model: String, messages: List<ChatMessage>, maxTokens: Int,
            jsonMode: Boolean, onToken: (String) -> Unit,
        ): EdgeCompletion {
            streamCalls++
            lastModel = model
            if (ok) pieces.forEach(onToken)
            return EdgeCompletion(pieces.joinToString(""), ttft, ttft * 2, ok, error)
        }

        override fun complete(
            model: String, messages: List<ChatMessage>, maxTokens: Int, jsonMode: Boolean,
        ): EdgeCompletion {
            completeCalls++
            lastFollowUpMessages = messages
            return EdgeCompletion(followUpText, 10.0, 20.0, true)
        }
    }

    private class Recorder {
        var cloudCalls = 0
        var lastCloudMessage = ""
        val tokens = mutableListOf<String>()
        val events = mutableListOf<RouteEventPayload>()
    }

    private fun orchestrator(
        edge: EdgeLlm,
        cloudAnswer: String = "这是云端的完整答案。",
        granted: Set<String> = emptySet(),
        rec: Recorder = Recorder(),
        deviceOnlyRoles: Set<String> = emptySet(),
        audit: PermissionAudit = PermissionAudit(),
    ): Triple<ChatOrchestrator, Recorder, PermissionAudit> {
        val router = PlaneRouter(config.copy(deviceOnlyRoles = deviceOnlyRoles))
        val tools = ToolRegistry(
            tools = listOf(
                DeviceTool("device_time", "当前时间", run = {
                    com.sekb.ondevice.model.ToolResult(ok = true, output = "2026-09-18 09:00")
                }),
                DeviceTool(
                    "device_contacts_search", "搜索联系人",
                    requiredPermission = "android.permission.READ_CONTACTS",
                    args = mapOf("query" to "关键字"),
                    run = { com.sekb.ondevice.model.ToolResult(ok = true, output = "张明") },
                ),
            ),
            checker = PermissionChecker { it in granted },
            audit = audit,
        )
        val cloud = CloudChat { message, _, onToken, _ ->
            rec.cloudCalls++
            rec.lastCloudMessage = message
            onToken(cloudAnswer)
            ExecutionInfo(primaryPlane = "cloud", escalated = 1, reason = "escalated")
        }
        val orch = ChatOrchestrator(
            config = config, router = router, edgeLlm = edge, cloud = cloud, tools = tools,
            reporter = { rec.events.add(it) },
            now = { 1_700_000_000_000 },
            newId = { "evt-1" },
        )
        return Triple(orch, rec, audit)
    }

    // ---------- 端侧成功 ----------

    @Test
    fun `clean edge answer stays on device`() {
        val edge = FakeEdge(pieces = listOf("端侧推理通过降低计算复杂度", "来省电。"))
        val (orch, rec, _) = orchestrator(edge)
        val out = orch.send("端侧推理为什么省电？", onToken = { rec.tokens.add(it) })

        assertEquals(Plane.EDGE, out.plane)
        assertEquals("端侧推理通过降低计算复杂度来省电。", out.text)
        assertEquals(0, rec.cloudCalls)
        assertFalse(out.escalated)
        assertEquals("本机完成", out.execution.badge())
        // 上报了且是端侧完成
        assertEquals(1, rec.events.size)
        assertEquals("edge", rec.events[0].plane)
        assertFalse(rec.events[0].escalated)
        // 用户看到的 token 与最终文本一致（没有重复、没有截断）
        assertEquals(out.text, rec.tokens.joinToString(""))
    }

    @Test
    fun `short answer is released by finish not killed by guard`() {
        val edge = FakeEdge(pieces = listOf("很省电。"))   // 远小于 guardChars=40
        val (orch, _, _) = orchestrator(edge)
        val out = orch.send("为什么省电")
        assertEquals(Plane.EDGE, out.plane)
        assertEquals("很省电。", out.text)
    }

    // ---------- 前缀守卫改道 ----------

    @Test
    fun `degenerate prefix reroutes before any token is shown`() {
        val edge = FakeEdge(pieces = listOf("。".repeat(80)))
        val (orch, rec, _) = orchestrator(edge)
        val out = orch.send("讲讲端侧推理", onToken = { rec.tokens.add(it) })

        assertEquals(Plane.CLOUD, out.plane)
        assertTrue(out.escalated)
        assertTrue(out.signals.contains(PlaneRouter.SIGNAL_DEGENERATE))
        assertEquals("这是云端的完整答案。", out.text)
        // **产品级承诺**：端侧那段退化内容一个字符都没给用户看
        assertEquals(listOf("这是云端的完整答案。"), rec.tokens)
        assertFalse(rec.tokens.joinToString("").contains("。。。"))   // 端侧那串刷屏没外泄
        // 改道不是"从零开始"：云端收到了交接块
        assertTrue(rec.lastCloudMessage.contains("<handoff"))
        assertTrue(rec.lastCloudMessage.contains("degenerate"))
        assertTrue(rec.lastCloudMessage.endsWith("讲讲端侧推理"))
        // 上报的是"升级"事件
        assertEquals("cloud", rec.events[0].plane)
        assertTrue(rec.events[0].escalated)
    }

    @Test
    fun `edge network failure escalates to cloud`() {
        val edge = FakeEdge(pieces = emptyList(), ok = false, error = "Connection refused")
        val (orch, rec, _) = orchestrator(edge)
        val out = orch.send("你好")
        assertEquals(Plane.CLOUD, out.plane)
        assertEquals(listOf(ChatOrchestrator.SIGNAL_EDGE_UNAVAILABLE), out.signals)
        assertEquals(1, rec.cloudCalls)
    }

    // ---------- 隐私硬边界 ----------

    @Test
    fun `device only data never escalates even when edge output is bad`() {
        val edge = FakeEdge(pieces = listOf("。".repeat(80)))
        val (orch, rec, _) = orchestrator(edge, deviceOnlyRoles = setOf("chat"))
        val out = orch.send("我的联系人里有谁", deviceData = true)

        assertEquals(Plane.EDGE, out.plane)
        assertFalse(out.escalated)
        assertEquals("escalation_blocked:device_only", out.escalateReason)
        assertTrue(out.text.startsWith("。"))          // 端侧原样给出（宁可承认失败）
        assertEquals(0, rec.cloudCalls)                 // 云端**一次都没被调用**
        assertFalse(rec.events[0].escalated)
    }

    // ---------- 工具调用 ----------

    @Test
    fun `tool call round executes and answers with the result`() {
        val edge = FakeEdge(pieces = listOf("""{"tool":"device_time","args":{}}"""))
        val (orch, rec, audit) = orchestrator(edge)
        val out = orch.send("现在几点？")

        assertEquals(1, edge.completeCalls)
        assertEquals("现在是 12:00。", out.text)
        assertEquals(1, out.toolResults.size)
        assertTrue(out.toolResults[0].ok)
        assertTrue(out.toolCallAttempted && out.toolCallLegal)
        // 工具结果被回灌给模型（ReAct 单步）
        assertTrue(edge.lastFollowUpMessages.any { it.content.contains("工具结果") })
        assertTrue(audit.stats().allowed == 1)
        assertEquals(1, rec.events.size)
    }

    @Test
    fun `tool call without permission is blocked and audited`() {
        val edge = FakeEdge(pieces = listOf("""{"tool":"device_contacts_search","args":{"query":"张"}}"""))
        val (orch, _, audit) = orchestrator(edge)   // 什么权限都没给
        val out = orch.send("帮我找张明的电话")

        assertEquals(1, out.toolResults.size)
        assertTrue(out.toolResults[0].denied)
        assertTrue(audit.stats().denied == 1)
        assertEquals(1.0, audit.stats().deniedRate, 0.0)
    }

    @Test
    fun `plain answer is not counted as a tool attempt`() {
        val edge = FakeEdge(pieces = listOf("端侧推理通过降低计算复杂度来省电，因为内存带宽是瓶颈。"))
        val (orch, _, _) = orchestrator(edge)
        val out = orch.send("为什么省电")
        assertFalse(out.toolCallAttempted)
        assertFalse(out.toolCallLegal)
    }

    // ---------- 直接判给云端 ----------

    @Test
    fun `long role goes straight to cloud without touching edge`() {
        val edge = FakeEdge(pieces = listOf("不该被调用"))
        val (orch, rec, _) = orchestrator(edge)
        val out = orch.send("写一篇长报告", role = "news_report")

        assertEquals(Plane.CLOUD, out.plane)
        assertEquals(0, edge.streamCalls)          // 端侧一次都没调用
        assertEquals(1, rec.cloudCalls)
        assertTrue(out.decision.reason.startsWith("output_over_edge_budget"))
    }

    // ---------- 交接摘要 ----------

    @Test
    fun `handoff carries reason and truncates the discarded prefix`() {
        val wrapped = Handoff.wrap(listOf("degenerate", "timeout"), "x".repeat(1000), "原始问题")
        assertTrue(wrapped.contains("reason=\"degenerate,timeout\""))
        assertTrue(wrapped.contains("已建立的背景"))
        assertTrue(wrapped.endsWith("原始问题"))
        // 截断：不能把端侧几千字的垃圾全塞进云端上下文
        assertTrue(wrapped.length < 1000)
    }
}
