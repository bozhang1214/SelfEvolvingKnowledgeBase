package com.sekb.ondevice.ui

import com.sekb.shared.model.ToolResult

/**
 * 从工具结果里解析出**检索来源**（用于在回答下方显示"这次引用了哪些文档、几分"）。
 *
 * 为什么值得单独一层：端侧 RAG 的答对/答错必须可追溯——用户看到"AI 说 X"时，
 * 有权知道 X 是从哪段本机资料里来的。这也是端侧 RAG 相对云端的卖点之一（来源就在设备上）。
 *
 * 输入形如（`Retriever.formatForModel` 的输出）：
 * ```
 * [0.696] doc-rag: 端侧 RAG 把知识索引放在设备上…
 * [0.612] doc-privacy: 设备专属数据永不出端…
 * ```
 */
object RetrievalSources {

    data class Source(val sourceId: String, val score: Double, val snippet: String)

    private val LINE = Regex("""^\[(\d+\.\d+)]\s*([^:：]+)[:：]\s*(.*)$""")

    fun parse(results: List<ToolResult>): List<Source> {
        val out = mutableListOf<Source>()
        for (r in results) {
            if (!r.ok) continue
            for (line in r.output.lines()) {
                val m = LINE.find(line.trim()) ?: continue
                out.add(
                    Source(
                        sourceId = m.groupValues[2].trim(),
                        score = m.groupValues[1].toDoubleOrNull() ?: 0.0,
                        snippet = m.groupValues[3].trim().take(80),
                    ),
                )
            }
        }
        return out.sortedByDescending { it.score }
    }

    /** 给界面用的一行摘要。 */
    fun summary(sources: List<Source>): String =
        if (sources.isEmpty()) "" else "检索到 ${sources.size} 段：" +
            sources.joinToString("、") { "${it.sourceId}(${"%.2f".format(it.score)})" }
}
