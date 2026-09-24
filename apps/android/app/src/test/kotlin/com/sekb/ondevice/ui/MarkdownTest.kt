package com.sekb.ondevice.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Markdown 解析器的测试。
 *
 * 重点不在"支持多少语法"，而在两条**容易被忽略但会真出问题**的性质：
 * 1. **流式输入的每个前缀都不能丢字**——UI 每收一个增量就解析一次，
 *    半截标记（`**粗体没闭合`、未收尾的 ``` 代码块）必须降级为普通文本，而不是被吃掉；
 * 2. 解析器**不许抛异常**——它跑在每帧的 UI 线程上。
 */
class MarkdownTest {

    @Test
    fun `headings keep level`() {
        val b = Markdown.parse("# 一级\n## 二级\n### 三级")
        assertEquals(3, b.size)
        assertEquals(Markdown.Block.Heading(1, "一级"), b[0])
        assertEquals(Markdown.Block.Heading(2, "二级"), b[1])
        assertEquals(Markdown.Block.Heading(3, "三级"), b[2])
    }

    @Test
    fun `bullet list groups consecutive lines`() {
        val b = Markdown.parse("- 甲\n- 乙\n* 丙")
        assertEquals(1, b.size)
        assertEquals(Markdown.Block.Bullet(listOf("甲", "乙", "丙")), b[0])
    }

    @Test
    fun `numbered list groups and keeps order`() {
        val b = Markdown.parse("1. 第一\n2. 第二")
        assertEquals(Markdown.Block.Numbered(listOf("第一", "第二")), b.single())
    }

    @Test
    fun `quote collects lines and strips marker`() {
        val b = Markdown.parse("> 说明一\n> 说明二")
        assertEquals(Markdown.Block.Quote(listOf("说明一", "说明二")), b.single())
    }

    @Test
    fun `fenced code block keeps language and body`() {
        val b = Markdown.parse("```kotlin\nval x = 1\nprintln(x)\n```")
        assertEquals(Markdown.Block.Code("kotlin", "val x = 1\nprintln(x)"), b.single())
    }

    /** 流式时最常见：还没收到结束的 ```，内容也必须完整显示。 */
    @Test
    fun `unclosed fence still yields code block with remaining body`() {
        val b = Markdown.parse("```python\nprint(1)")
        assertEquals(Markdown.Block.Code("python", "print(1)"), b.single())
    }

    @Test
    fun `table needs a separator row and keeps cells`() {
        val b = Markdown.parse("| 路径 | 环节 |\n|---|---|\n| 云端 | 采集→上行 |\n| 端侧 | 直接推理 |")
        val t = b.single() as Markdown.Block.Table
        assertEquals(listOf("路径", "环节"), t.header)
        assertEquals(2, t.rows.size)
        assertEquals(listOf("端侧", "直接推理"), t.rows[1])
    }

    /** 没有分隔行的 `| a | b |` 不是表格，应当当普通段落（否则会把散文误判成表）。 */
    @Test
    fun `pipes without separator stay paragraph`() {
        val b = Markdown.parse("这行有 | 竖线 | 但不是表格")
        assertTrue(b.single() is Markdown.Block.Paragraph)
    }

    @Test
    fun `divider and paragraphs`() {
        val b = Markdown.parse("第一段\n\n---\n\n第二段")
        assertEquals(3, b.size)
        assertTrue(b[0] is Markdown.Block.Paragraph)
        assertEquals(Markdown.Block.Divider, b[1])
        assertTrue(b[2] is Markdown.Block.Paragraph)
    }

    @Test
    fun `inline bold code italic link`() {
        val s = Markdown.inline("**粗** 与 `码` 与 *斜* 与 [文字](https://x.y)")
        assertEquals(
            listOf(
                Markdown.Span.Bold("粗"),
                Markdown.Span.Text(" 与 "),
                Markdown.Span.Code("码"),
                Markdown.Span.Text(" 与 "),
                Markdown.Span.Italic("斜"),
                Markdown.Span.Text(" 与 "),
                Markdown.Span.Link("文字", "https://x.y"),
            ),
            s,
        )
    }

    /** 关键不变量：未闭合的标记必须原样保留，不能吞字。 */
    @Test
    fun `unclosed inline markers degrade to text without losing characters`() {
        for (src in listOf("**没闭合", "*没闭合", "`没闭合", "[文字](没闭合")) {
            val joined = Markdown.inline(src).joinToString("") {
                when (it) {
                    is Markdown.Span.Text -> it.text
                    is Markdown.Span.Bold -> it.text
                    is Markdown.Span.Italic -> it.text
                    is Markdown.Span.Code -> it.text
                    is Markdown.Span.Link -> it.text
                }
            }
            assertTrue("输入=$src 输出=$joined 丢字了", joined.contains("没闭合"))
        }
    }

    /**
     * 流式前缀不丢字：把一段真实回答逐字符喂进去，**每个前缀**解析后拼回文本
     * 都不能比原文少字符（允许 Markdown 标记被解释掉，但不允许正文字符消失）。
     */
    @Test
    fun `every streaming prefix keeps all visible characters`() {
        val full = "# 标题\n\n> **注意**：这是 `code` 与 [链接](http://a.b)\n\n- 甲\n- 乙\n\n| a | b |\n|---|---|\n| 1 | 2 |"
        val visible = full.filter { it !in "#*`>|-[]()\n " }
        for (len in 1..full.length) {
            val prefix = full.substring(0, len)
            val text = Markdown.parse(prefix).joinToString("") { block ->
                when (block) {
                    is Markdown.Block.Heading -> block.text
                    is Markdown.Block.Paragraph -> block.text
                    is Markdown.Block.Bullet -> block.items.joinToString("")
                    is Markdown.Block.Numbered -> block.items.joinToString("")
                    is Markdown.Block.Quote -> block.lines.joinToString("")
                    is Markdown.Block.Code -> block.code
                    is Markdown.Block.Table ->
                        (block.header + block.rows.flatten()).joinToString("")
                    Markdown.Block.Divider -> ""
                }
            }
            val expected = prefix.filter { it !in "#*`>|-[]()\n " }
            assertEquals("前缀长度 $len 丢字：$prefix", expected, text.filter { it !in "#*`>|-[]()\n " })
        }
        assertTrue(visible.isNotEmpty())
    }

    /** 解析器跑在 UI 线程上，任何输入都不许抛。 */
    @Test
    fun `parser never throws on odd input`() {
        for (src in listOf("", "\n", "```", "|", "|---|---|", "#", "-", ">", "***", "| a |\n| b |")) {
            Markdown.parse(src)
        }
    }
}
