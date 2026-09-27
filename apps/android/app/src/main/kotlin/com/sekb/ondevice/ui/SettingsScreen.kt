package com.sekb.ondevice.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.PhoneAndroid
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.sekb.ondevice.BuildConfig
import com.sekb.ondevice.ui.theme.LocalSekbSemantic

/**
 * 设置页：把上一版摊在聊天页上的东西**分组收进来**。
 *
 * 分组顺序按"用户会不会动它"排：
 * 1. 账号与设备——最常见的动作（第一次用必须接入）；
 * 2. 连接地址——只有联调/自建后端才改，默认折叠；
 * 3. 隐私与权限审计——不是配置而是**知情权**，所以放在显眼处但只读；
 * 4. 关于。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(viewModel: ChatViewModel, onBack: () -> Unit) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    var advanced by remember { mutableStateOf(false) }

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            TopAppBar(
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.surface,
                ),
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.Filled.ArrowBack, contentDescription = "返回")
                    }
                },
                title = { Text("设置", fontWeight = FontWeight.SemiBold) },
            )
        },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .verticalScroll(rememberScrollState())
                .padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            AccountCard(viewModel, state)
            ConnectionCard(viewModel, state, advanced) { advanced = !advanced }
            PrivacyCard(viewModel, state)
            AboutCard(state)
        }
    }
}

@Composable
private fun SectionCard(title: String, content: @Composable () -> Unit) {
    Card(
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        shape = RoundedCornerShape(12.dp),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(modifier = Modifier.padding(14.dp)) {
            Text(
                title,
                fontSize = 12.sp,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(10.dp))
            content()
        }
    }
}

@Composable
private fun AccountCard(viewModel: ChatViewModel, state: ChatUiState) {
    SectionCard("账号与设备") {
        if (state.enrolled) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(
                    Icons.Filled.CheckCircle,
                    contentDescription = null,
                    tint = LocalSekbSemantic.current.onDevice,
                    modifier = Modifier.size(18.dp),
                )
                Spacer(Modifier.width(8.dp))
                Column {
                    Text("已接入", style = MaterialTheme.typography.bodyMedium)
                    Text(
                        // 设备 ID 是排障要用的，所以保留，但不占主视觉
                        "设备 ${state.deviceId.take(8)}…",
                        fontSize = 11.sp,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        fontFamily = FontFamily.Monospace,
                    )
                }
            }
        } else {
            Text(
                "接入后即可使用云端知识库；端侧推理与本地检索不需要接入。",
                fontSize = 12.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(10.dp))
            OutlinedTextField(
                value = state.email,
                onValueChange = viewModel::onEmailChange,
                label = { Text("账号") },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
            )
            Spacer(Modifier.height(8.dp))
            OutlinedTextField(
                value = state.password,
                onValueChange = viewModel::onPasswordChange,
                label = { Text("密码") },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
                // 必须打码：明文密码在截图/投屏/旁人一瞥下就直接泄漏
                visualTransformation = PasswordVisualTransformation(),
            )
            Spacer(Modifier.height(10.dp))
            Button(
                onClick = viewModel::enroll,
                enabled = !state.busy,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text(if (state.busy) "接入中…" else "接入设备")
            }
        }
    }
}

@Composable
private fun ConnectionCard(
    viewModel: ChatViewModel,
    state: ChatUiState,
    advanced: Boolean,
    onToggle: () -> Unit,
) {
    SectionCard("连接") {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(
                modifier = Modifier
                    .size(6.dp)
                    .background(
                        when (state.edgeReachable) {
                            true -> LocalSekbSemantic.current.onDevice
                            false -> LocalSekbSemantic.current.cloud
                            null -> MaterialTheme.colorScheme.onSurfaceVariant
                        },
                        CircleShape,
                    ),
            )
            Spacer(Modifier.width(8.dp))
            Text(
                when (state.edgeReachable) {
                    true -> "端侧可用：推理在本机完成"
                    false -> "端侧不可用：将改道云端"
                    null -> "正在检查端侧…"
                },
                fontSize = 13.sp,
                modifier = Modifier.weight(1f),
            )
            TextButton(onClick = { viewModel.probeEdge() }) { Text("重新检查", fontSize = 12.sp) }
        }

        TextButton(onClick = onToggle) {
            Text(if (advanced) "收起高级设置" else "高级设置", fontSize = 12.sp)
        }
        if (advanced) {
            OutlinedTextField(
                value = state.edgeUrl,
                onValueChange = viewModel::onEdgeUrlChange,
                label = { Text("端侧推理地址", fontSize = 12.sp) },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
            )
            Spacer(Modifier.height(8.dp))
            OutlinedTextField(
                value = state.sekbUrl,
                onValueChange = viewModel::onSekbUrlChange,
                label = { Text("云端知识库地址", fontSize = 12.sp) },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
            )
            Spacer(Modifier.height(8.dp))
            Button(onClick = viewModel::applyConfig, modifier = Modifier.fillMaxWidth()) {
                Text("应用地址")
            }
        }
    }
}

/** 隐私与权限审计：只读展示"端侧 Agent 做了什么、被拦了什么"。 */
@Composable
private fun PrivacyCard(viewModel: ChatViewModel, state: ChatUiState) {
    SectionCard("隐私与权限") {
        StatRow("设备工具调用", "${state.audit.total} 次")
        StatRow("被权限闸门拦截", "${state.audit.denied} 次")
        StatRow("越权拦截率", state.deniedRateText)
        Spacer(Modifier.height(6.dp))
        Text(state.toolEvalSummary, fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.height(4.dp))
        TextButton(onClick = viewModel::clearAudit) { Text("清空统计", fontSize = 12.sp) }
    }
}

@Composable
private fun StatRow(label: String, value: String) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(vertical = 3.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Text(label, fontSize = 13.sp)
        Text(value, fontSize = 13.sp, fontWeight = FontWeight.Medium)
    }
}

@Composable
private fun AboutCard(state: ChatUiState) {
    SectionCard("关于") {
        StatRow("版本", BuildConfig.VERSION_NAME)
        StatRow("端侧嵌入", "bge-small-zh-v1.5（512 维）")
        Text(
            "文档索引与向量都存在这台设备上；标记为「设备专属」的内容不会被上传。",
            fontSize = 11.sp,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(top = 6.dp),
        )
    }
}
