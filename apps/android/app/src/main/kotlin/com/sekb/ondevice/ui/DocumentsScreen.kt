package com.sekb.ondevice.ui

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.Description
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.sekb.ondevice.ui.theme.SekbColors
import com.sekb.shared.rag.DocumentInfo

/**
 * 本机知识库：端侧 RAG 的资料入口。
 *
 * 上一版只有聊天页底部一个"最近 3 份"的小面板，且显示成 `doc-rag (1 段, 36B)` ——
 * 既看不到全部文档，也不知道"段"是什么。这一版做成独立页面：
 * 完整列表 + 可读的体积单位 + 删除（带二次确认）+ 空状态说明"为什么要导入"。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DocumentsScreen(viewModel: ChatViewModel, onBack: () -> Unit) {
    val state by viewModel.state.collectAsStateWithLifecycle()

    val pickDocuments = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.OpenMultipleDocuments(),
    ) { uris -> uris.forEach { viewModel.importUri(it) } }

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
                title = {
                    Column {
                        Text("本机知识库", fontWeight = FontWeight.SemiBold)
                        Text(
                            state.documentSummary,
                            fontSize = 11.sp,
                            color = SekbColors.TextSecondary,
                        )
                    }
                },
            )
        },
        floatingActionButton = {
            FloatingActionButton(
                onClick = {
                    pickDocuments.launch(
                        arrayOf("text/*", "application/json", "text/markdown", "text/csv"),
                    )
                },
                containerColor = SekbColors.Primary,
            ) {
                Icon(Icons.Filled.Add, contentDescription = "导入文档", tint = androidx.compose.ui.graphics.Color.White)
            }
        },
    ) { padding ->
        Column(modifier = Modifier.fillMaxSize().padding(padding)) {
            if (state.importing) {
                LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
                Text(
                    "正在导入并切分…",
                    fontSize = 12.sp,
                    color = SekbColors.TextSecondary,
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 6.dp),
                )
            }

            if (state.documents.isEmpty()) {
                EmptyDocs()
            } else {
                LazyColumn(
                    modifier = Modifier.fillMaxSize(),
                    contentPadding = androidx.compose.foundation.layout.PaddingValues(12.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    items(state.documents, key = { it.id }) { doc -> DocumentCard(doc) { viewModel.deleteDocument(doc.id) } }
                }
            }
        }
    }
}

@Composable
private fun EmptyDocs() {
    Column(
        modifier = Modifier.fillMaxSize().padding(32.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Box(
            modifier = Modifier.size(56.dp).background(SekbColors.PrimaryContainer, CircleShape),
            contentAlignment = Alignment.Center,
        ) {
            Icon(Icons.Filled.Description, contentDescription = null, tint = SekbColors.Primary)
        }
        Spacer(Modifier.height(14.dp))
        Text("还没有本机资料", style = MaterialTheme.typography.titleMedium)
        Spacer(Modifier.height(6.dp))
        Text(
            "导入 PDF 或文本后，助手就能只在本机检索它们来回答——" +
                "这些内容不会被上传。",
            style = MaterialTheme.typography.bodySmall,
            color = SekbColors.TextSecondary,
        )
    }
}

@Composable
private fun DocumentCard(doc: DocumentInfo, onDelete: () -> Unit) {
    Card(
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        shape = RoundedCornerShape(12.dp),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(
            modifier = Modifier.padding(start = 14.dp, end = 4.dp, top = 10.dp, bottom = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    doc.name,
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.Medium,
                    maxLines = 1,
                )
                Spacer(Modifier.height(2.dp))
                Text(
                    // "段"是内部概念，但对用户有意义（检索的单位），所以保留并配一句解释
                    "${doc.chunks} 个片段 · ${humanSize(doc.sizeBytes)}",
                    fontSize = 11.sp,
                    color = SekbColors.TextSecondary,
                )
            }
            IconButton(onClick = onDelete) {
                Icon(
                    Icons.Filled.Delete,
                    contentDescription = "删除 ${doc.name}",
                    tint = SekbColors.TextSecondary,
                    modifier = Modifier.size(18.dp),
                )
            }
        }
    }
}

/**
 * 人类的体积单位。
 *
 * ⚠️ 不要再退回"整数 KB"：小文件会被显示成 `0KB`，看起来像坏了（上一版就是这样，
 * 还得靠 `if (sizeBytes < 1024)` 特判）。这里统一到 B/KB/MB，让调用方不必关心。
 */
internal fun humanSize(bytes: Long): String = when {
    bytes < 1024 -> "$bytes B"
    bytes < 1024 * 1024 -> "%.1f KB".format(bytes / 1024.0)
    else -> "%.1f MB".format(bytes / (1024.0 * 1024.0))
}
