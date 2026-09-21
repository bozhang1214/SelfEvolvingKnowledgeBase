package com.sekb.ondevice

import com.sekb.shared.route.EdgeRuntimeConfig
import com.sekb.shared.route.PlaneRouter
import com.sekb.shared.route.StreamGuard
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 流式前缀守卫：**改道必须发生在用户看到任何字符之前**。
 * 服务端对应实现见 SEKB `LLMFactory._astream_guarded`。
 */
class StreamGuardTest {

    private fun guard(guardChars: Int = 60, responseFormat: String? = null) = StreamGuard(
        router = PlaneRouter(
            EdgeRuntimeConfig(edgeBaseUrl = "http://x/v1", sekbBaseUrl = "https://y"),
        ),
        guardChars = guardChars,
        responseFormat = responseFormat,
    )

    @Test
    fun `buffers until threshold`() {
        val g = guard(guardChars = 20)
        assertEquals(StreamGuard.Decision.Buffering, g.offer("端侧"))
        assertEquals(StreamGuard.Decision.Buffering, g.offer("推理"))
        assertEquals(4, g.bufferedChars)
    }

    @Test
    fun `clean prefix is released then passes through`() {
        val g = guard(guardChars = 10)
        val decision = g.offer("端侧推理通过降低计算复杂度来省电。")
        assertTrue(decision is StreamGuard.Decision.Release)
        assertEquals("端侧推理通过降低计算复杂度来省电。",
            (decision as StreamGuard.Decision.Release).prefix)
        assertTrue(g.isReleased)
        assertEquals(StreamGuard.Decision.Passthrough, g.offer("后续"))
    }

    @Test
    fun `degenerate prefix escalates and releases nothing`() {
        val g = guard(guardChars = 60)
        val decision = g.offer("。".repeat(80))
        assertTrue(decision is StreamGuard.Decision.Escalate)
        val esc = decision as StreamGuard.Decision.Escalate
        assertTrue(PlaneRouter.SIGNAL_DEGENERATE in esc.signals)
        assertTrue(esc.prefix.startsWith("。"))   // 前缀被丢弃（调用方不得外泄）
        assertTrue(!g.isReleased)
    }

    @Test
    fun `short output is evaluated in full at finish`() {
        val g = guard(guardChars = 60)
        assertEquals(StreamGuard.Decision.Buffering, g.offer("很省电。"))
        val decision = g.finish()
        assertTrue(decision is StreamGuard.Decision.Release)
    }

    @Test
    fun `json role prefix is not killed by incomplete json`() {
        // 这是最容易踩的坑：半截 JSON 必然不合法，若在前缀阶段判 json_invalid，
        // 所有 JSON 角色都会被误杀。
        val g = guard(guardChars = 10, responseFormat = "json")
        val decision = g.offer("""{"tool": "device_time", "args": {}}""")
        assertTrue(decision is StreamGuard.Decision.Release)
    }

    @Test
    fun `slow first token escalates via timeout signal`() {
        val g = guard(guardChars = 10)
        g.noteFirstToken(5000.0)
        val decision = g.offer("端侧推理通过降低计算复杂度来省电")
        assertTrue(decision is StreamGuard.Decision.Escalate)
        assertTrue(PlaneRouter.SIGNAL_TIMEOUT in
            (decision as StreamGuard.Decision.Escalate).signals)
    }

    @Test
    fun `zero guard chars releases on first chunk`() {
        val g = guard(guardChars = 0)
        assertTrue(g.offer("任意") is StreamGuard.Decision.Release)
    }
}
