package com.sekb.ondevice.ui

/**
 * 极简 Markdown 解析器（**块级 + 少量行内**）。
 *
 * ## 为什么必须做
 *
 * 端侧/云端模型的回答**本来就是 Markdown**（实测回答里出现了 `# 标题`、`## 核心结论`、
 * `> ⚠️ **信息说明**`、`| 路径 | 环节 |`）。上一版直接 `Text(bubble.text)`，
 * 用户看到的是**源码**——`**粗体**`、`|---|` 这些符号原样糊在脸上，网页端却是渲染过的。
 * 这不是"锦上添花"，是"能不能读"的问题。
 *
 * ## 为什么不用现成的库
 *
 * `compose-markdown` 之类要引新依赖；本项目 Maven 走镜像、离线环境不稳定，
 * 而这里需要的子集很小（标题/列表/引用/代码块/表格/粗体/行内码）。
 * 自己实现 200 行、**并且有单测钉住**，比引一个会漂移的依赖更可控——
 * 这与仓库里 `BertWordPieceTokenizer` 的选择理由一致。
 *
 * ## 设计约束：**必须能处理"半句话"**
 *
 * 流式输出时每个增量都会调用一次解析，输入可能是 `**粗体没闭合`、` ``` 还没收尾`。
 * 所以解析器**不允许抛异常**、也不允许因为语法不完整而丢内容——一律降级成普通文本。
 */
object Markdown {

    sealed interface Block {
        data class Heading(val level: Int, val text: String) : Block
        data class Paragraph(val text: String) : Block
        data class Bullet(val items: List<String>) : Block
        data class Numbered(val items: List<String>) : Block
        data class Quote(val lines: List<String>) : Block
        data class Code(val lang: String, val code: String) : Block
        data class Table(val header: List<String>, val rows: List<List<String>>) : Block
        data object Divider : Block
    }

    private val HEADING = Regex("""^(#{1,6})\s+(.*)$""")
    private val BULLET = Regex("""^\s*[-*+]\s+(.*)$""")
    private val NUMBERED = Regex("""^\s*\d+[.)]\s+(.*)$""")
    private val FENCE = Regex("""^\s*```\s*([A-Za-z0-9+#-]*)\s*$""")
    private val DIVIDER = Regex("""^\s*(-{3,}|\*{3,}|_{3,})\s*$""")
    private val TABLE_SEP = Regex("""^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$""")

    fun parse(src: String): List<Block> {
        val out = mutableListOf<Block>()
        val lines = src.replace("\r\n", "\n").split('\n')

        var i = 0
        val para = mutableListOf<String>()
        val bullets = mutableListOf<String>()
        val numbered = mutableListOf<String>()
        val quote = mutableListOf<String>()

        fun flush() {
            if (para.isNotEmpty()) {
                out.add(Block.Paragraph(para.joinToString("\n").trim()))
                para.clear()
            }
            if (bullets.isNotEmpty()) {
                out.add(Block.Bullet(bullets.toList()))
                bullets.clear()
            }
            if (numbered.isNotEmpty()) {
                out.add(Block.Numbered(numbered.toList()))
                numbered.clear()
            }
            if (quote.isNotEmpty()) {
                out.add(Block.Quote(quote.toList()))
                quote.clear()
            }
        }

        while (i < lines.size) {
            val line = lines[i]

            // 代码块：遇到未闭合的 ``` 也要把剩余内容当代码块输出（流式时的常见状态）
            FENCE.find(line)?.let { m ->
                flush()
                val lang = m.groupValues[1]
                val body = mutableListOf<String>()
                i++
                while (i < lines.size && !FENCE.containsMatchIn(lines[i])) {
                    body.add(lines[i]); i++
                }
                if (i < lines.size) i++   // 跳过结束的 ```
                out.add(Block.Code(lang, body.joinToString("\n")))
                continue
            }

            when {
                line.isBlank() -> flush()

                HEADING.matches(line) -> {
                    flush()
                    val m = HEADING.find(line)!!
                    out.add(Block.Heading(m.groupValues[1].length, m.groupValues[2].trim()))
                }

                DIVIDER.matches(line) -> {
                    flush(); out.add(Block.Divider)
                }

                // 表格：当前行是 | a | b |，且下一行是分隔行
                line.contains('|') && i + 1 < lines.size && TABLE_SEP.matches(lines[i + 1]) -> {
                    flush()
                    val header = splitRow(line)
                    val rows = mutableListOf<List<String>>()
                    i += 2
                    while (i < lines.size && lines[i].contains('|') && lines[i].isNotBlank()) {
                        rows.add(splitRow(lines[i])); i++
                    }
                    out.add(Block.Table(header, rows))
                    continue
                }

                BULLET.matches(line) -> {
                    if (para.isNotEmpty() || numbered.isNotEmpty() || quote.isNotEmpty()) flush()
                    bullets.add(BULLET.find(line)!!.groupValues[1].trim())
                }

                NUMBERED.matches(line) -> {
                    if (para.isNotEmpty() || bullets.isNotEmpty() || quote.isNotEmpty()) flush()
                    numbered.add(NUMBERED.find(line)!!.groupValues[1].trim())
                }

                line.trimStart().startsWith(">") -> {
                    if (para.isNotEmpty() || bullets.isNotEmpty() || numbered.isNotEmpty()) flush()
                    quote.add(line.trimStart().removePrefix(">").trim())
                }

                else -> {
                    if (bullets.isNotEmpty() || numbered.isNotEmpty() || quote.isNotEmpty()) flush()
                    para.add(line)
                }
            }
            i++
        }
        flush()
        return out
    }

    /** 解析表格行：`| a | b |` → `[a, b]`（容忍首尾没有竖线）。 */
    private fun splitRow(line: String): List<String> {
        var s = line.trim()
        if (s.startsWith("|")) s = s.substring(1)
        if (s.endsWith("|")) s = s.dropLast(1)
        return s.split('|').map { it.trim() }
    }

    /** 行内片段：粗体 / 行内代码 / 斜体 / 链接。 */
    sealed interface Span {
        data class Text(val text: String) : Span
        data class Bold(val text: String) : Span
        data class Italic(val text: String) : Span
        data class Code(val text: String) : Span
        data class Link(val text: String, val url: String) : Span
    }

    // 顺序有讲究：**粗体** 必须在 *斜体* 之前匹配，否则 `**x**` 会被斜体规则吃掉
    private val INLINE = Regex(
        """`([^`]*)`""" +                       // 行内代码
            """|\*\*([^*]+)\*\*""" +             // 粗体
            """|\*([^*\n]+)\*""" +               // 斜体
            """|\[([^\]]+)]\(([^)]+)\)""",       // 链接
    )

    /**
     * 拆行内片段。**未闭合的标记按普通文本处理**（流式输出必然出现半截标记，
     * 例如 `**粗体没闭合`；这时正则不匹配，自然降级为文本，不会丢字）。
     */
    fun inline(text: String): List<Span> {
        val out = mutableListOf<Span>()
        var last = 0
        for (m in INLINE.findAll(text)) {
            if (m.range.first > last) out.add(Span.Text(text.substring(last, m.range.first)))
            val g = m.groupValues
            out.add(
                when {
                    g[1].isNotEmpty() || (g[1].isEmpty() && m.value.startsWith("`")) -> Span.Code(g[1])
                    g[2].isNotEmpty() -> Span.Bold(g[2])
                    g[3].isNotEmpty() -> Span.Italic(g[3])
                    g[4].isNotEmpty() -> Span.Link(g[4], g[5])
                    else -> Span.Text(m.value)
                },
            )
            last = m.range.last + 1
        }
        if (last < text.length) out.add(Span.Text(text.substring(last)))
        return out
    }
}
