package com.sekb.ondevice.eval

import com.sekb.shared.embed.DeterministicEmbedding
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import com.sekb.shared.eval.*

/**
 * 评测**工具本身**的测试（用确定性桩跑）：确认指标口径正确，别出现"评测器算错分"
 * 这种比被测对象更糟的事。
 */
class RetrievalEvalRunnerTest {

    private val provider = DeterministicEmbedding()

    @Test
    fun `dataset is well formed`() {
        assertEquals(12, RetrievalEvalSet.corpus.size)
        assertTrue("问题数应在 20–50 之间", RetrievalEvalSet.size in 20..50)
        assertTrue(RetrievalEvalSet.unanswerable >= 3)          // 必须有无答案的问题
        val ids = RetrievalEvalSet.corpus.map { it.id }.toSet()
        RetrievalEvalSet.questions.filter { it.expected != null }.forEach {
            assertTrue("标注指向了不存在的文档：${it.expected}", it.expected in ids)
        }
    }

    @Test
    fun `metrics have the right shape`() {
        val runner = RetrievalEvalRunner(provider)
        val (index, store) = runner.buildIndex()
        val report = runner.run(threshold = 0.2, index = index, store = store)

        assertEquals(RetrievalEvalSet.answerable, report.answerable)
        assertEquals(RetrievalEvalSet.unanswerable, report.unanswerable)
        assertTrue(report.hit1 <= report.answerable)
        assertTrue(report.hit3 >= report.hit1)                   // Hit@3 必须包含 Hit@1
        assertTrue(report.mrr in 0.0..1.0)
        assertEquals(report.rows.size, RetrievalEvalSet.size)
    }

    @Test
    fun `threshold monotonicity - raising it never increases hits nor false positives`() {
        // 这是评测的关键性质：阈值升高，召回只会变少、误召回只会变少。
        // 若这条不成立，说明指标算错了（例如把未命中也算成命中）。
        val runner = RetrievalEvalRunner(provider)
        val (index, store) = runner.buildIndex()
        val reports = runner.calibrate(index, store, thresholds = listOf(0.1, 0.3, 0.5, 0.7, 0.9))

        reports.zipWithNext { low, high ->
            assertTrue("Hit@1 应随阈值单调不增：${low.threshold}→${low.hit1}, ${high.threshold}→${high.hit1}",
                high.hit1 <= low.hit1)
            assertTrue("误召回应随阈值单调不增：${low.threshold}→${low.falsePositives}, ${high.threshold}→${high.falsePositives}",
                high.falsePositives <= low.falsePositives)
        }
    }

    @Test
    fun `low threshold answers everything and high threshold answers nothing`() {
        val runner = RetrievalEvalRunner(provider)
        val (index, store) = runner.buildIndex()
        val loose = runner.run(0.01, index, store)
        val strict = runner.run(0.999, index, store)
        assertTrue(loose.hit1 > 0)
        assertEquals(0, strict.hit1)
        assertEquals(0, strict.falsePositives)
        // 低阈值下"无答案"的问题也会被强行回答 → 必然产生误召回（见下一条测试）
        assertTrue(loose.falsePositives > 0)
    }

    @Test
    fun `unanswerable questions are counted as false positives when threshold is too low`() {
        val runner = RetrievalEvalRunner(provider)
        val (index, store) = runner.buildIndex()
        val loose = runner.run(0.01, index, store)
        assertEquals(RetrievalEvalSet.unanswerable, loose.falsePositives)
        assertEquals(1.0, loose.falsePositiveRate, 1e-9)
    }

    @Test
    fun `report text contains the curve and the recommendation`() {
        val runner = RetrievalEvalRunner(provider)
        val (index, store) = runner.buildIndex()
        val text = runner.format(runner.calibrate(index, store), "确定性桩")
        assertTrue(text.contains("推荐阈值"))
        assertTrue(text.contains("Hit@1"))
        assertTrue(text.contains("误召回"))
    }

    @Test
    fun `latency is aggregated per question`() {
        val runner = RetrievalEvalRunner(provider)
        val (index, store) = runner.buildIndex()
        val report = runner.run(0.2, index, store)
        assertTrue(report.embedMeanMs >= 0.0)
        assertTrue(report.searchMeanMs >= 0.0)
        assertTrue(report.embedP95Ms >= 0.0)
    }
}
