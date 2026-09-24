package com.sekb.ondevice.ui

import androidx.activity.compose.BackHandler
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.lifecycle.compose.collectAsStateWithLifecycle

/** 端侧宿主的三个页面。 */
enum class SekbScreen { Chat, Documents, Settings }

/**
 * 极简导航壳。
 *
 * ## 为什么不上 navigation-compose
 *
 * 只有三个页面、层级扁平（聊天 ⇄ 文档/设置），**没有深链接、没有回退栈语义**。
 * 为此引入一个导航库 + 路由字符串，是把"以后可能需要"提前付成本：
 * 既多一个依赖，又让 back 行为变得需要学。这里用一个 `enum` + `when` 就够了。
 * 真长出第四个层级页面时再换（那时才有回退栈需求）。
 */
@Composable
fun AppShell(viewModel: ChatViewModel) {
    var screen by remember { mutableStateOf(SekbScreen.Chat) }
    val state by viewModel.state.collectAsStateWithLifecycle()

    // 进文档页时刷新一次：用户可能刚在系统文件管理器里删过文件
    LaunchedEffect(screen) {
        if (screen == SekbScreen.Documents) viewModel.refreshDocuments()
    }

    // 子页面必须响应系统返回键，否则用户按返回会直接退出应用（很突兀）
    BackHandler(enabled = screen != SekbScreen.Chat) { screen = SekbScreen.Chat }

    when (screen) {
        SekbScreen.Chat -> ChatScreen(
            viewModel = viewModel,
            documentCount = state.documents.size,
            onOpenDocuments = { screen = SekbScreen.Documents },
            onOpenSettings = { screen = SekbScreen.Settings },
        )

        SekbScreen.Documents -> DocumentsScreen(viewModel) { screen = SekbScreen.Chat }

        SekbScreen.Settings -> SettingsScreen(viewModel) { screen = SekbScreen.Chat }
    }
}
