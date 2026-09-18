package com.sekb.ondevice.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Send
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.sekb.ondevice.model.Plane

/**
 * 端侧宿主主界面：聊天 + **执行位置** + 权限审计 + 端云配置。
 *
 * 界面上刻意保留三个"不讨喜但必要"的显示：
 * 1. 每条回答的**执行位置**（本机/已上云）——用户有权知道数据出没出端；
 * 2. 路由决策理由（如 `output_over_edge_budget(600>512)`）——出问题能自己看懂原因；
 * 3. 权限审计与被拦截次数——端侧 Agent 读了什么、被拦了什么，必须可见。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatScreen(viewModel: ChatViewModel) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val listState = rememberLazyListState()

    LaunchedEffect(Unit) { viewModel.probeEdge() }
    LaunchedEffect(state.bubbles.size) {
        if (state.bubbles.isNotEmpty()) listState.animateScrollToItem(state.bubbles.size - 1)
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text("SEKB 端侧", fontWeight = FontWeight.SemiBold)
                        Text(
                            when (state.edgeReachable) {
                                true -> "端侧就绪（${state.edgeUrl}）"
                                false -> "端侧不可达（${state.edgeUrl}）"
                                null -> "正在探测端侧…"
                            },
                            fontSize = 11.sp,
                        )
                    }
                },
            )
        },
    ) { padding ->
        Column(modifier = Modifier.fillMaxSize().padding(padding)) {

            if (state.busy) LinearProgressIndicator(modifier = Modifier.fillMaxWidth())

            LazyColumn(
                state = listState,
                modifier = Modifier.weight(1f).fillMaxWidth().padding(horizontal = 12.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                items(state.bubbles) { bubble -> BubbleRow(bubble) }
            }

            if (state.thinking.isNotEmpty() && state.busy) {
                Text("· ${state.thinking}", fontSize = 12.sp, modifier = Modifier.padding(horizontal = 16.dp))
            }
            state.error.takeIf { it.isNotEmpty() }?.let {
                Text(it, color = MaterialTheme.colorScheme.error, fontSize = 12.sp,
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp))
            }
            state.notice.takeIf { it.isNotEmpty() }?.let {
                Text(it, color = MaterialTheme.colorScheme.primary, fontSize = 12.sp,
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp))
            }
            if (state.lastReason.isNotEmpty()) {
                Text("最近决策：${state.lastReason}", fontSize = 11.sp,
                    modifier = Modifier.padding(horizontal = 16.dp))
            }

            Row(
                modifier = Modifier.fillMaxWidth().padding(12.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                OutlinedTextField(
                    value = state.input,
                    onValueChange = viewModel::onInputChange,
                    modifier = Modifier.weight(1f),
                    placeholder = { Text("问点什么…") },
                    maxLines = 4,
                )
                IconButton(onClick = viewModel::send, enabled = !state.busy) {
                    Icon(Icons.Filled.Send, contentDescription = "发送")
                }
            }

            HorizontalDivider()
            StatusPanel(viewModel, state)
        }
    }
}

@Composable
private fun BubbleRow(bubble: Bubble) {
    val bg = if (bubble.fromUser) MaterialTheme.colorScheme.primaryContainer
    else MaterialTheme.colorScheme.surfaceVariant
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = if (bubble.fromUser) Arrangement.End else Arrangement.Start,
    ) {
        Card(shape = RoundedCornerShape(12.dp)) {
            Column(modifier = Modifier.background(bg).padding(10.dp)) {
                Text(bubble.text, fontSize = 14.sp)
                val badge = ChatViewModel.badgeOf(bubble.execution)
                if (badge.isNotEmpty()) {
                    val isEdge = bubble.execution?.primaryPlane == Plane.EDGE.wire
                    Text(
                        badge + (bubble.execution?.model?.takeIf { it.isNotEmpty() }?.let { " · $it" } ?: ""),
                        fontSize = 10.sp,
                        color = if (isEdge) Color(0xFF1B5E20) else Color(0xFF8D6E00),
                        fontFamily = FontFamily.Monospace,
                    )
                }
            }
        }
    }
}

/** 端云配置 + 权限审计：把"看不见的机制"暴露给用户。 */
@Composable
private fun StatusPanel(viewModel: ChatViewModel, state: ChatUiState) {
    Column(modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 6.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("设备：", fontSize = 11.sp)
            Text(
                if (state.enrolled) state.deviceId else "未接入",
                fontSize = 11.sp, fontFamily = FontFamily.Monospace,
            )
            TextButton(onClick = { viewModel.refreshAudit() }) { Text("刷新", fontSize = 11.sp) }
        }
        OutlinedTextField(
            value = state.edgeUrl, onValueChange = viewModel::onEdgeUrlChange,
            label = { Text("端侧端点（模拟器里 10.0.2.2 = 宿主机）", fontSize = 11.sp) },
            modifier = Modifier.fillMaxWidth(), singleLine = true,
        )
        OutlinedTextField(
            value = state.sekbUrl, onValueChange = viewModel::onSekbUrlChange,
            label = { Text("云端 SEKB", fontSize = 11.sp) },
            modifier = Modifier.fillMaxWidth(), singleLine = true,
        )
        Row(verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(
                value = state.email, onValueChange = viewModel::onEmailChange,
                label = { Text("账号", fontSize = 11.sp) },
                modifier = Modifier.weight(1f), singleLine = true,
            )
            Column(modifier = Modifier.width(4.dp)) {}
            OutlinedTextField(
                value = state.password, onValueChange = viewModel::onPasswordChange,
                label = { Text("密码", fontSize = 11.sp) },
                modifier = Modifier.weight(1f), singleLine = true,
            )
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            Button(onClick = viewModel::applyConfig) { Text("应用地址", fontSize = 12.sp) }
            TextButton(onClick = viewModel::enroll) { Text("接入设备", fontSize = 12.sp) }
            TextButton(onClick = viewModel::clearAudit) { Text("清空审计", fontSize = 12.sp) }
        }
        Text(
            "权限审计：调用 ${state.audit.total} 次，拦截 ${state.audit.denied} 次" +
                "（越权拦截率 ${state.deniedRateText}）",
            fontSize = 11.sp,
        )
        Text(state.toolEvalSummary, fontSize = 11.sp)
    }
}
