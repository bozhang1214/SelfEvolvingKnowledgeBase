package com.sekb.ondevice.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * 把 [Markdown] 解析出来的块渲染成 Compose 组件。
 *
 * 视觉上对齐网页端（AntD + 那份 `.markdown-content` 样式）：标题字号递减、
 * 行内代码用浅灰底 + 等宽、代码块浅灰面板、引用左侧竖线。**不追求完整 Markdown**，
 * 只覆盖模型实际会输出的那几类——多做的部分没人用，还会引入显示 bug。
 */
@Composable
fun MarkdownText(
    text: String,
    color: Color = MaterialTheme.colorScheme.onSurface,
    modifier: Modifier = Modifier,
) {
    val blocks = remember(text) { Markdown.parse(text) }
    Column(modifier = modifier, verticalArrangement = Arrangement.spacedBy(6.dp)) {
        blocks.forEach { block -> BlockView(block, color) }
    }
}

@Composable
private fun BlockView(block: Markdown.Block, color: Color) {
    // 一次取好、往下传：行内码底色与链接色在明暗两套主题下不同
    val codeBg = MaterialTheme.colorScheme.surfaceVariant
    val linkColor = MaterialTheme.colorScheme.primary
    when (block) {
        is Markdown.Block.Heading -> Text(
            inlineText(block.text, color, codeBg, linkColor),
            fontSize = when (block.level) {
                1 -> 18.sp
                2 -> 16.sp
                else -> 15.sp
            },
            fontWeight = FontWeight.SemiBold,
            color = color,
        )

        is Markdown.Block.Paragraph -> Text(
            inlineText(block.text, color, codeBg, linkColor),
            style = MaterialTheme.typography.bodyMedium,
            color = color,
        )

        is Markdown.Block.Bullet -> Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
            block.items.forEach { item ->
                Row {
                    Text("•  ", color = color, style = MaterialTheme.typography.bodyMedium)
                    Text(
                        inlineText(item, color, codeBg, linkColor),
                        style = MaterialTheme.typography.bodyMedium,
                        color = color,
                    )
                }
            }
        }

        is Markdown.Block.Numbered -> Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
            block.items.forEachIndexed { idx, item ->
                Row {
                    Text("${idx + 1}.  ", color = color, style = MaterialTheme.typography.bodyMedium)
                    Text(
                        inlineText(item, color, codeBg, linkColor),
                        style = MaterialTheme.typography.bodyMedium,
                        color = color,
                    )
                }
            }
        }

        is Markdown.Block.Quote -> Row(
            // IntrinsicSize.Min 让左侧竖线自动跟文字同高
            // （写死 dp 会在多行引用时露出"短一截"的竖线）
            modifier = Modifier.fillMaxWidth().height(androidx.compose.foundation.layout.IntrinsicSize.Min),
        ) {
            Box(
                modifier = Modifier
                    .width(3.dp)
                    .fillMaxHeight()
                    .background(MaterialTheme.colorScheme.outlineVariant),
            )
            Spacer(Modifier.width(8.dp))
            Text(
                inlineText(block.lines.joinToString("\n"), color, codeBg, linkColor),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }

        is Markdown.Block.Code -> Box(
            modifier = Modifier
                .fillMaxWidth()
                .background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(6.dp))
                .horizontalScroll(rememberScrollState())
                .padding(10.dp),
        ) {
            Text(
                block.code,
                fontFamily = FontFamily.Monospace,
                fontSize = 12.sp,
                color = color,
            )
        }

        is Markdown.Block.Table -> TableView(block, color)

        Markdown.Block.Divider -> HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
    }
}

/** 表格：横向可滚动（模型给的表格列数不定，硬挤会把字压成竖条）。 */
@Composable
private fun TableView(table: Markdown.Block.Table, color: Color) {
    val codeBg = MaterialTheme.colorScheme.surfaceVariant
    val linkColor = MaterialTheme.colorScheme.primary
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .horizontalScroll(rememberScrollState()),
    ) {
        Row {
            table.header.forEach { cell ->
                Text(
                    inlineText(cell, color, codeBg, linkColor),
                    fontSize = 12.sp,
                    fontWeight = FontWeight.SemiBold,
                    color = color,
                    modifier = Modifier.width(96.dp).padding(vertical = 4.dp, horizontal = 4.dp),
                )
            }
        }
        HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
        table.rows.forEach { row ->
            Row {
                row.forEach { cell ->
                    Text(
                        inlineText(cell, color, codeBg, linkColor),
                        fontSize = 12.sp,
                        color = color,
                        modifier = Modifier.width(96.dp).padding(vertical = 4.dp, horizontal = 4.dp),
                    )
                }
            }
        }
    }
}

/**
 * 行内片段 → AnnotatedString。
 *
 * ⚠️ 它**不是** `@Composable`（要在任意上下文里构造文本），所以行内码底色与链接色
 * 必须**由调用方传进来**——直接读 `MaterialTheme` 会编译失败
 * （"@Composable invocations can only happen from the context of a @Composable function"）。
 * 这也是好事：把"这段文本用什么底色"变成显式参数，而不是藏着一个隐式的主题读取。
 */
private fun inlineText(
    text: String,
    color: Color,
    codeBg: Color,
    linkColor: Color,
): AnnotatedString =
    buildAnnotatedString {
        Markdown.inline(text).forEach { span ->
            when (span) {
                is Markdown.Span.Text -> withStyle(SpanStyle(color = color)) { append(span.text) }
                is Markdown.Span.Bold -> withStyle(
                    SpanStyle(color = color, fontWeight = FontWeight.SemiBold),
                ) { append(span.text) }
                is Markdown.Span.Italic -> withStyle(
                    SpanStyle(color = color, fontStyle = FontStyle.Italic),
                ) { append(span.text) }
                is Markdown.Span.Code -> withStyle(
                    SpanStyle(
                        color = color,
                        fontFamily = FontFamily.Monospace,
                        background = codeBg,
                    ),
                ) { append(span.text) }
                // 链接先只显示文字 + 主题色：端侧场景里点开外链的机会很少，
                // 而 `LocalUriHandler` 在无浏览器/无网络的设备上会静默失败，不如不点
                is Markdown.Span.Link -> withStyle(
                    SpanStyle(color = linkColor),
                ) { append(span.text) }
            }
        }
    }
