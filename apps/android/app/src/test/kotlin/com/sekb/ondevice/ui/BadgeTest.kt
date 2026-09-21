package com.sekb.ondevice.ui

import com.sekb.shared.model.ExecutionInfo
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * 执行位置徽标的文案：**服务端升级**与**客户端升级**是两件事，文案不能混。
 */
class BadgeTest {

    private val edge = ExecutionInfo(primaryPlane = "edge", escalated = 0)
    private val cloud = ExecutionInfo(primaryPlane = "cloud", escalated = 0)
    private val cloudServerEscalated = ExecutionInfo(primaryPlane = "cloud", escalated = 1)

    @Test
    fun `edge answer is labelled as on device`() {
        assertEquals("本机完成", ChatViewModel.badgeOf(edge, clientEscalated = false))
    }

    @Test
    fun `plain cloud answer is labelled as cloud`() {
        assertEquals("云端完成", ChatViewModel.badgeOf(cloud, clientEscalated = false))
    }

    @Test
    fun `server side escalation is visible`() {
        assertEquals("已上云（端侧不达标）", ChatViewModel.badgeOf(cloudServerEscalated, false))
    }

    @Test
    fun `client side escalation is visible even when server reports none`() {
        // 这条是 UI 验收截图抓到的：客户端因 edge_unavailable 改道云端，
        // 而服务端的 execution.escalated=0 → 只看服务端字段会显示成"云端完成"，
        // 用户就不知道自己的问题其实在端侧失败过。
        assertEquals("已上云（端侧不达标）", ChatViewModel.badgeOf(cloud, clientEscalated = true))
    }

    @Test
    fun `unknown plane yields empty badge`() {
        assertEquals("", ChatViewModel.badgeOf(ExecutionInfo(), false))
        assertEquals("", ChatViewModel.badgeOf(null, false))
    }
}
