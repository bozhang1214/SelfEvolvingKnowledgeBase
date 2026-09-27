package com.sekb.ondevice.ui

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Cloud
import androidx.compose.material.icons.filled.Description
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material.icons.filled.PhoneAndroid
import androidx.compose.material.icons.filled.Send
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.SmartToy
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.IconButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextField
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.sekb.ondevice.ui.theme.LocalSekbSemantic
import com.sekb.shared.model.Plane

/**
 * 端侧宿主主界面（**用户版**）。
 *
 * ## 与上一版（调试台）的区别
 *
 * 上一版把端点地址、设备 ID、审计统计、工具调用合法率全铺在聊天页上，是给开发者看的。
 * 这一版按"用户只会聊天"来组织：
 * - **首页只有聊天**：配置、审计、文档列表各自进独立页面（右上角与顶部入口）；
 * - **不出现裸 URL / 裸设备 ID**：状态用一句话 + 一个圆点表达（"端侧就绪"/"仅云端可用"）；
 * - **执行位置仍然可见但不刺眼**：做成气泡下方的小徽章（"本机完成"/"云端完成"/"已上云"）——
 *   它是产品承诺（设备专属数据不出端），不是调试信息，所以**不能删**，只能弱化；
 * - **报错与提示走 Snackbar**，不再在输入框上方堆一行行小字。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatScreen(
    viewModel: ChatViewModel,
    documentCount: Int = 0,
    onOpenDocuments: () -> Unit = {},
    onOpenSettings: () -> Unit = {},
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val listState = rememberLazyListState()
    val snackbar = remember { SnackbarHostState() }

    // 文档页负责选择文件；聊天页也留一个快捷入口（用户第一反应往往是"先喂资料再问"）
    val pickDocuments = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.OpenMultipleDocuments(),
    ) { uris -> uris.forEach { viewModel.importUri(it) } }

    LaunchedEffect(Unit) { viewModel.probeEdge() }
    LaunchedEffect(state.bubbles.size, state.busy) {
        if (state.bubbles.isNotEmpty()) listState.animateScrollToItem(state.bubbles.lastIndex)
    }
    // 提示与错误统一走 Snackbar：调试台那种"在输入框上方叠小字"用户读不到也看不懂
    LaunchedEffect(state.notice) {
        if (state.notice.isNotEmpty()) snackbar.showSnackbar(state.notice)
    }
    LaunchedEffect(state.error) {
        if (state.error.isNotEmpty()) snackbar.showSnackbar(state.error)
    }

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = {
            TopAppBar(
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.surface,
                ),
                title = {
                    Column {
                        Text(
                            state.conversationTitle.ifEmpty { "SEKB" },
                            fontWeight = FontWeight.SemiBold,
                            maxLines = 1,
                        )
                        EdgeStatusLine(state.edgeReachable, state.enrolled)
                    }
                },
                actions = {
                    // 已有对话时才给"新对话"：空对话给这个按钮没有意义
                    if (state.bubbles.isNotEmpty()) {
                        IconButton(onClick = viewModel::newConversation) {
                            Icon(Icons.Filled.Add, contentDescription = "新对话")
                        }
                    }
                    BadgedBox(
                        badge = {
                            if (documentCount > 0) Badge { Text("$documentCount") }
                        },
                    ) {
                        IconButton(onClick = onOpenDocuments) {
                            Icon(Icons.Filled.Description, contentDescription = "本机知识库")
                        }
                    }
                    IconButton(onClick = onOpenSettings) {
                        Icon(Icons.Filled.Settings, contentDescription = "设置")
                    }
                },
            )
        },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .imePadding()
                .navigationBarsPadding(),
        ) {
            if (state.bubbles.isEmpty()) {
                // ⚠️ 必须 `weight(1f)` 而不是让空状态 `fillMaxSize()`：
                // 后者会在 Column 里吃掉全部高度，把底部**输入栏挤出屏幕**
                // （实测：装到真机后整屏只有空状态，用户没法输入）。
                EmptyState(modifier = Modifier.weight(1f), onPick = { viewModel.onInputChange(it) })
            } else {
                LazyColumn(
                    state = listState,
                    modifier = Modifier.weight(1f).fillMaxWidth(),
                    contentPadding = androidx.compose.foundation.layout.PaddingValues(
                        horizontal = 12.dp, vertical = 12.dp,
                    ),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    items(state.bubbles) { bubble -> BubbleRow(bubble) }
                }
            }

            if (state.busy) ThinkingRow(state.thinking)

            InputBar(
                value = state.input,
                busy = state.busy,
                onChange = viewModel::onInputChange,
                onSend = viewModel::send,
                onAttach = {
                    pickDocuments.launch(
                        arrayOf("text/*", "application/json", "text/markdown", "text/csv"),
                    )
                },
            )
        }
    }
}

/** 一句话状态 + 圆点：不给用户看 URL，但必须让他知道"端侧到底能不能用"。 */
@Composable
private fun EdgeStatusLine(reachable: Boolean?, enrolled: Boolean) {
    val (dot, text) = when {
        !enrolled -> MaterialTheme.colorScheme.onSurfaceVariant to "未接入云端账号"
        reachable == true -> LocalSekbSemantic.current.onDevice to "端侧就绪 · 数据不出本机"
        reachable == false -> LocalSekbSemantic.current.cloud to "端侧不可用 · 将使用云端"
        else -> MaterialTheme.colorScheme.onSurfaceVariant to "正在检查端侧…"
    }
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(
            modifier = Modifier
                .size(6.dp)
                .background(dot, CircleShape),
        )
        Spacer(Modifier.width(6.dp))
        Text(text, fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

/**
 * 空状态：给"该问什么"一点提示。
 *
 * 这三个问题**都是这台设备真的答得了的**（分别对应端侧模型知识、本机知识库、设备工具），
 * 不是装饰性文案——点了就会填进输入框，用户能立刻得到一次成功体验。
 */
@Composable
private fun EmptyState(modifier: Modifier = Modifier, onPick: (String) -> Unit) {
    val samples = listOf(
        "端侧推理为什么更省电？",
        "我本机的知识库里有什么？",
        "现在几点了？",
    )
    Column(
        modifier = modifier.fillMaxWidth().padding(24.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Box(
            modifier = Modifier.size(56.dp).background(MaterialTheme.colorScheme.primaryContainer, CircleShape),
            contentAlignment = Alignment.Center,
        ) {
            Icon(Icons.Filled.SmartToy, contentDescription = null, tint = MaterialTheme.colorScheme.primary)
        }
        Spacer(Modifier.height(14.dp))
        Text("SEKB 端侧助手", style = MaterialTheme.typography.titleMedium)
        Spacer(Modifier.height(6.dp))
        Text(
            "文档与推理都在这台设备上完成，设备专属内容不会离开本机。",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.height(20.dp))
        samples.forEach { q ->
            Surface(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(vertical = 4.dp)
                    .clickable { onPick(q) },
                shape = RoundedCornerShape(10.dp),
                color = MaterialTheme.colorScheme.surface,
                border = androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
            ) {
                Text(
                    q,
                    modifier = Modifier.padding(horizontal = 14.dp, vertical = 11.dp),
                    style = MaterialTheme.typography.bodyMedium,
                )
            }
        }
    }
}

@Composable
private fun BubbleRow(bubble: Bubble) {
    val expanded = remember { mutableStateMapOf<String, Boolean>() }
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = if (bubble.fromUser) Arrangement.End else Arrangement.Start,
    ) {
        if (!bubble.fromUser) {
            Avatar(Icons.Filled.SmartToy, MaterialTheme.colorScheme.primaryContainer, MaterialTheme.colorScheme.primary)
            Spacer(Modifier.width(8.dp))
        }
        Column(
            modifier = Modifier.widthIn(max = 300.dp),
            horizontalAlignment = if (bubble.fromUser) Alignment.End else Alignment.Start,
        ) {
            Surface(
                shape = RoundedCornerShape(
                    topStart = 14.dp, topEnd = 14.dp,
                    bottomStart = if (bubble.fromUser) 14.dp else 4.dp,
                    bottomEnd = if (bubble.fromUser) 4.dp else 14.dp,
                ),
                color = if (bubble.fromUser) MaterialTheme.colorScheme.primary
                else MaterialTheme.colorScheme.surface,
                border = if (bubble.fromUser) null
                else androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
            ) {
                // 用户自己的话按纯文本显示；**助手的回答必须渲染 Markdown**——
                // 模型输出里带 `# 标题` / `**粗体**` / 表格，原样显示就是给用户看源码
                if (bubble.fromUser) {
                    Text(
                        bubble.text.ifEmpty { " " },
                        modifier = Modifier.padding(horizontal = 13.dp, vertical = 10.dp),
                        style = MaterialTheme.typography.bodyMedium,
                        color = Color.White,
                    )
                } else {
                    MarkdownText(
                        text = bubble.text.ifEmpty { " " },
                        color = MaterialTheme.colorScheme.onSurface,
                        modifier = Modifier.padding(horizontal = 13.dp, vertical = 10.dp),
                    )
                }
            }

            // 引用来源：默认收起，只给一句"引用了 N 段本机资料"——展开才能看到出处与分数
            if (bubble.sources.isNotEmpty()) {
                val key = "src"
                val open = expanded[key] == true
                SourcesRow(bubble.sources, open) { expanded[key] = !open }
            }

            ExecutionBadge(bubble)
        }
        if (bubble.fromUser) {
            Spacer(Modifier.width(8.dp))
            Avatar(Icons.Filled.PhoneAndroid, MaterialTheme.colorScheme.surfaceVariant, MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun Avatar(
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    bg: Color,
    tint: Color,
) {
    Box(
        modifier = Modifier.size(30.dp).background(bg, CircleShape),
        contentAlignment = Alignment.Center,
    ) {
        Icon(icon, contentDescription = null, tint = tint, modifier = Modifier.size(17.dp))
    }
}

/**
 * 执行位置徽章——**产品承诺的可视化**。
 *
 * 绿色 = 在本机算的；金色 = 用到了云端。这个区分是端侧产品的核心卖点，
 * 所以每一条回答都要标，但做成小字弱化，不抢内容。
 */
@Composable
private fun ExecutionBadge(bubble: Bubble) {
    if (bubble.fromUser) return
    val badge = ChatViewModel.badgeOf(bubble.execution, bubble.escalated)
    if (badge.isEmpty()) return
    val isEdge = bubble.execution?.primaryPlane == Plane.EDGE.wire
    val color = if (isEdge) LocalSekbSemantic.current.onDevice else LocalSekbSemantic.current.cloud
    val model = bubble.execution?.model?.takeIf { it.isNotEmpty() }

    // 徽章必须能自己解释"为什么"：只给"已上云（端侧不达标）"这种结论，
    // 用户（正确地）会追问"我的数据出去了吗、为什么"——而这正是端侧产品的核心承诺。
    var open by remember(bubble.reason, bubble.escalateReason) { mutableStateOf(false) }
    // 与徽章**同源**的事实（isEdge 来自 execution.primaryPlane）——不能从 decision.plane 推，
    // 否则升级场景下解释会与徽章互相打脸（第一版就是这么错的）
    val explain = remember(bubble.reason, bubble.escalateReason, isEdge, bubble.escalated) {
        PlaneExplain.outcome(
            completedOnDevice = isEdge,
            escalated = bubble.escalated || (bubble.execution?.escalated ?: 0) > 0,
            decisionReason = bubble.reason,
            escalateReason = bubble.escalateReason,
        )
    }

    Column(modifier = Modifier.padding(top = 4.dp)) {
        Row(
            modifier = Modifier.clickable(enabled = explain.isNotEmpty()) { open = !open },
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Icon(
                if (isEdge) Icons.Filled.PhoneAndroid else Icons.Filled.Cloud,
                contentDescription = null, tint = color, modifier = Modifier.size(12.dp),
            )
            Spacer(Modifier.width(4.dp))
            Text(
                badge + (model?.let { " · $it" } ?: ""),
                fontSize = 11.sp,
                color = color,
            )
            if (explain.isNotEmpty()) {
                Icon(
                    if (open) Icons.Filled.ExpandLess else Icons.Filled.ExpandMore,
                    contentDescription = if (open) "收起原因" else "为什么",
                    tint = color,
                    modifier = Modifier.size(14.dp),
                )
            }
        }
        if (open && explain.isNotEmpty()) {
            Text(
                explain,
                fontSize = 11.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 3.dp, start = 16.dp),
            )
        }
    }
}

@Composable
private fun SourcesRow(
    sources: List<RetrievalSources.Source>,
    expanded: Boolean,
    onToggle: () -> Unit,
) {
    Column(modifier = Modifier.padding(top = 4.dp)) {
        Row(
            modifier = Modifier.clickable(onClick = onToggle),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                "引用 ${sources.size} 段本机资料",
                fontSize = 11.sp,
                color = MaterialTheme.colorScheme.primary,
            )
            Icon(
                if (expanded) Icons.Filled.ExpandLess else Icons.Filled.ExpandMore,
                contentDescription = if (expanded) "收起" else "展开",
                tint = MaterialTheme.colorScheme.primary,
                modifier = Modifier.size(14.dp),
            )
        }
        if (expanded) {
            Card(
                modifier = Modifier.padding(top = 4.dp),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                border = androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
                shape = RoundedCornerShape(10.dp),
            ) {
                sources.forEach { s ->
                    Column(modifier = Modifier.padding(10.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(s.sourceId, fontSize = 12.sp, fontWeight = FontWeight.Medium)
                            Spacer(Modifier.width(6.dp))
                            Text(
                                "%.2f".format(s.score),
                                fontSize = 11.sp,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        Text(
                            s.snippet,
                            fontSize = 11.sp,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            maxLines = 2,
                        )
                    }
                }
            }
        }
    }
}

/** 生成中的提示：让"在算"这件事可见（端侧推理没有网络往返，不给反馈会让人以为卡住）。 */
@Composable
private fun ThinkingRow(thinking: String) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        CircularProgressIndicator(modifier = Modifier.size(13.dp), strokeWidth = 1.5.dp)
        Spacer(Modifier.width(8.dp))
        Text(
            thinking.ifEmpty { "正在本机生成…" },
            fontSize = 12.sp,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun InputBar(
    value: String,
    busy: Boolean,
    onChange: (String) -> Unit,
    onSend: () -> Unit,
    onAttach: () -> Unit,
) {
    Surface(color = MaterialTheme.colorScheme.surface) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 10.dp, vertical = 8.dp),
            verticalAlignment = Alignment.Bottom,
        ) {
            IconButton(onClick = onAttach) {
                Icon(
                    Icons.Filled.Description,
                    contentDescription = "导入本机文档",
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            TextField(
                value = value,
                onValueChange = onChange,
                modifier = Modifier.weight(1f),
                placeholder = { Text("问点什么…", fontSize = 14.sp) },
                maxLines = 4,
                shape = RoundedCornerShape(20.dp),
                colors = TextFieldDefaults.colors(
                    focusedContainerColor = MaterialTheme.colorScheme.surfaceVariant,
                    unfocusedContainerColor = MaterialTheme.colorScheme.surfaceVariant,
                    focusedIndicatorColor = Color.Transparent,
                    unfocusedIndicatorColor = Color.Transparent,
                ),
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                keyboardActions = KeyboardActions(onSend = { if (value.isNotBlank() && !busy) onSend() }),
            )
            Spacer(Modifier.width(6.dp))
            FilledIconButton(
                onClick = onSend,
                enabled = value.isNotBlank() && !busy,
                shape = CircleShape,
                colors = IconButtonDefaults.filledIconButtonColors(
                    containerColor = MaterialTheme.colorScheme.primary,
                ),
            ) {
                Icon(Icons.Filled.Send, contentDescription = "发送", modifier = Modifier.size(18.dp))
            }
        }
    }
}
