package com.sekb.shared.eval

import com.sekb.shared.embed.EmbeddingProvider
import com.sekb.shared.rag.InMemoryVectorStore
import com.sekb.shared.rag.KnowledgeIndex
import com.sekb.shared.rag.Retriever
import com.sekb.shared.core.Fmt

/**
 * 检索质量与延迟评测（RFC §18.4），并做**阈值标定**。
 *
 * 为什么必须标定阈值而不是拍一个：`Retriever.minScore` 决定"召回"与"误召回"的取舍，
 * 而这个可取区间**完全取决于嵌入模型**（确定性桩在 0.2 量级、ONNX bge 在 0.5 量级）。
 * 所以评测的输出不是"一个分数"，而是**一条曲线**：阈值 → 命中率 / 误召回率。
 * 挑阈值就是在这条曲线上按业务偏好选点（端侧 RAG 里"宁可说不知道"通常更重要）。
 */
class RetrievalEvalRunner(
    private val provider: EmbeddingProvider,
    private val corpus: List<RetrievalEvalSet.Doc> = RetrievalEvalSet.corpus,
    private val questions: List<RetrievalEvalSet.Question> = RetrievalEvalSet.questions,
) {

    data class Row(
        val question: String,
        val expected: String?,
        val got: String?,
        val score: Double,
        val hit1: Boolean,
        val hit3: Boolean,
        val rank: Int,          // 期望文档在结果里的位次（1-based，未命中为 0）
    )

    data class Report(
        val threshold: Double,
        val answerable: Int,
        val hit1: Int,
        val hit3: Int,
        val mrr: Double,
        val falsePositives: Int,
        val unanswerable: Int,
        val embedMeanMs: Double,
        val embedP95Ms: Double,
        val searchMeanMs: Double,
        val rows: List<Row>,
    ) {
        val hit1Rate: Double get() = if (answerable == 0) 0.0 else hit1.toDouble() / answerable
        val hit3Rate: Double get() = if (answerable == 0) 0.0 else hit3.toDouble() / answerable
        val falsePositiveRate: Double get() = if (unanswerable == 0) 0.0 else falsePositives.toDouble() / unanswerable

        /** 一行式摘要（自检/报告直接贴）。 */
        fun line(): String =
            // 历史坑（保留，提醒别退回 String.format）：第一版写成多段字面量再 `.format(...)`，
            // 而 `.format` 只作用于**紧邻的那一段字面量**，参数全部错位（实测报
            // "f != java.lang.Integer"，且前半段根本没被格式化）。现在用 Fmt 显式拼接，
            // 结构上不可能再犯——但"多段拼接 + 隐式格式化"这个坑值得记住。
            // 注：这里原来用 String.format（JVM 专有，KMP 编不过）→ 换成 Fmt，
            // 输出**逐字符一致**（含 `%2d`/`%3d` 的空格补齐），历史的日志对照仍然有效。
            "阈值=${Fmt.fixed(threshold, 2)}  " +
                "Hit@1=${Fmt.padStart(hit1, 2)}/$answerable(${Fmt.padStart(Fmt.pct(hit1Rate), 3)}%)  " +
                "Hit@3=${Fmt.padStart(hit3, 2)}/$answerable  MRR=${Fmt.fixed(mrr, 3)}  " +
                "误召回=$falsePositives/$unanswerable(${Fmt.padStart(Fmt.pct(falsePositiveRate), 3)}%)  " +
                "嵌入均 ${Fmt.fixed(embedMeanMs, 0)}ms(p95 ${Fmt.fixed(embedP95Ms, 0)}ms)  " +
                "检索均 ${Fmt.fixed(searchMeanMs, 1)}ms"
    }

    /** 用当前提供者把语料灌进**独立**索引（不碰 App 的真实索引）。 */
    fun buildIndex(): Pair<KnowledgeIndex, InMemoryVectorStore> {
        val store = InMemoryVectorStore(provider.space)
        val index = KnowledgeIndex(provider, store)
        corpus.forEach { index.ingest(it.id, it.text) }
        return index to store
    }

    fun run(
        threshold: Double,
        index: KnowledgeIndex,
        store: com.sekb.shared.rag.VectorStore,
        topK: Int = 3,
    ): Report {
        val retriever = Retriever(provider, store, minScore = threshold)
        var embedSum = 0.0
        var searchSum = 0.0
        val embedSamples = mutableListOf<Double>()
        val rows = mutableListOf<Row>()

        for (question in questions) {
            val outcome = retriever.retrieve(question.q, topK = topK, deviceOnly = true)
            embedSum += outcome.embedMillis
            searchSum += outcome.searchMillis
            embedSamples.add(outcome.embedMillis)

            val ids = outcome.hits.map { it.sourceId }
            val rank = question.expected?.let { exp -> ids.indexOf(exp) + 1 } ?: 0
            rows.add(
                Row(
                    question = question.q,
                    expected = question.expected,
                    got = ids.firstOrNull(),
                    score = outcome.hits.firstOrNull()?.score ?: 0.0,
                    hit1 = question.expected != null && rank == 1,
                    hit3 = question.expected != null && rank in 1..topK,
                    rank = rank,
                ),
            )
        }

        val n = questions.size.coerceAtLeast(1)
        val sorted = embedSamples.sorted()
        return Report(
            threshold = threshold,
            answerable = questions.count { it.expected != null },
            hit1 = rows.count { it.hit1 },
            hit3 = rows.count { it.hit3 },
            mrr = rows.filter { it.expected != null }.map { if (it.rank > 0) 1.0 / it.rank else 0.0 }
                .average().takeIf { !it.isNaN() } ?: 0.0,
            falsePositives = rows.count { it.expected == null && it.got != null },
            unanswerable = questions.count { it.expected == null },
            embedMeanMs = embedSum / n,
            embedP95Ms = sorted.getOrElse((sorted.size * 95 / 100).coerceAtMost(sorted.size - 1)) { 0.0 },
            searchMeanMs = searchSum / n,
            rows = rows,
        )
    }

    /** 阈值标定：跑一组阈值，给出"命中率 vs 误召回率"的取舍曲线。 */
    fun calibrate(
        index: KnowledgeIndex,
        store: com.sekb.shared.rag.VectorStore,
        thresholds: List<Double> = listOf(0.2, 0.3, 0.4, 0.5, 0.6),
    ): List<Report> = thresholds.map { run(it, index, store, topK = 3) }

    /** 完整报告文本（自检输出 + 存档）。 */
    fun format(reports: List<Report>, providerDesc: String): String = buildString {
        appendLine("=== 端侧检索评测（${providerDesc}）===")
        appendLine("语料 ${corpus.size} 篇 / 问题 ${questions.size} 条（有答案 ${RetrievalEvalSet.answerable}，无答案 ${RetrievalEvalSet.unanswerable}）")
        for (r in reports) appendLine("  " + r.line())
        val best = reports.maxByOrNull { it.hit1Rate - it.falsePositiveRate } ?: return@buildString
        appendLine("  推荐阈值：${Fmt.fixed(best.threshold, 2)}（Hit@1 ${Fmt.pct(best.hit1Rate)}%、误召回 ${Fmt.pct(best.falsePositiveRate)}% 的折中）")
        appendLine("--- 排序明细（阈值 ${Fmt.fixed(best.threshold, 2)}）---")
        for (row in best.rows.filter { it.expected != null && !it.hit3 }) {
            appendLine("  ✗ 未命中：${row.question} | 期望=${row.expected} 实得=${row.got} 分=${Fmt.fixed(row.score)}")
        }
        val ranked = best.rows.filter { it.expected != null && it.hit3 && !it.hit1 }
        if (ranked.isNotEmpty()) {
            appendLine("  ~ 命中但不在第 1 位（${ranked.size} 条，用于看排序质量）：")
            ranked.forEach { appendLine("     #${it.rank} ${it.question} | 期望=${it.expected} 实得=${it.got} 分=${Fmt.fixed(it.score)}") }
        }
        for (row in best.rows.filter { it.expected == null && it.got != null }) {
            appendLine("  ⚠ 误召回：${row.question} → ${row.got} 分=${Fmt.fixed(row.score)}")
        }
    }
}
