package com.sekb.ondevice.embed

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * **金标准比对**：期望的 token id 由 HuggingFace 分词器（`BAAI/bge-small-zh-v1.5` 原配）
 * 在主机上算出，然后写死在这里。分词错一点，嵌入就悄悄错一片——所以必须钉住。
 *
 * 词表放在 `src/test/resources/`（108KB，是**规格**不是模型权重），这样测试在 CI 也能跑。
 */
class BertWordPieceTokenizerTest {

    private val vocabFile = File("src/test/resources/bge-small-zh-vocab.txt")

    private val tokenizer: BertWordPieceTokenizer by lazy {
        BertWordPieceTokenizer(BertWordPieceTokenizer.loadVocab(vocabFile.readText()))
    }

    @Test
    fun `vocab file is present and complete`() {
        assertTrue("词表缺失：${vocabFile.absolutePath}", vocabFile.isFile)
        assertEquals(21128, BertWordPieceTokenizer.loadVocab(vocabFile.readText()).size)
    }

    @Test
    fun `pure chinese is split per character`() {
        assertEquals(
            listOf(101, 4999, 904, 2972, 4415, 711, 784, 720, 4689, 4510, 102),
            tokenizer.encode("端侧推理为什么省电"),
        )
    }

    @Test
    fun `mixed chinese ascii and punctuation`() {
        assertEquals(
            listOf(101, 4999, 904, 100, 2828, 4761, 6399, 5164, 2471, 3123, 1762,
                6392, 1906, 677, 8024, 3466, 5164, 679, 1139, 5381, 511, 102),
            tokenizer.encode("端侧 RAG 把知识索引放在设备上，检索不出网。"),
        )
    }

    @Test
    fun `no lowercasing - Hello is unknown but world is not`() {
        // 这一条是"别照抄 sentence_bert_config.json 的 do_lower_case"的护栏：
        // 实际分词器 lowercase=false，"Hello"→[UNK]，"world"→8572，", "→117、"!"→106，"2026"→202+##6
        assertEquals(
            listOf(101, 100, 117, 8572, 106, 9707, 8158, 2399, 130, 3299, 102),
            tokenizer.encode("Hello, world! 2026 年 9 月"),
        )
    }

    @Test
    fun `english words use wordpiece continuations`() {
        assertEquals(
            listOf(101, 163, 8171, 12157, 8402, 8786, 8862, 8228, 11285, 8169, 9283, 8361, 102),
            tokenizer.encode("unbelievable tokenization"),
        )
    }

    @Test
    fun `chinese and ascii adjacent without space are separated`() {
        assertEquals(
            listOf(101, 3921, 1394, 100, 704, 3152, 8363, 8189, 8604, 102),
            tokenizer.encode("混合 ABC中文def 123"),
        )
    }

    @Test
    fun `long text truncates to max length keeping CLS and SEP`() {
        val ids = tokenizer.encode("很长".repeat(400))
        assertEquals(512, ids.size)
        assertEquals(listOf(101, 2523, 7270), ids.take(3))
        assertEquals(listOf(2523, 7270, 102), ids.takeLast(3))
    }

    @Test
    fun `unknown character becomes UNK`() {
        assertEquals(listOf(101, 100, 102), tokenizer.encode("\uD840\uDC00"))   // 非 BMP 字符
    }

    @Test
    fun `batch encoding pads and masks correctly`() {
        val (ids, mask) = tokenizer.encodeBatch(listOf("端侧", "端侧推理为什么省电"))
        assertEquals(2, ids.size)
        assertEquals(ids[0].size, ids[1].size)
        assertTrue(mask[0].sum() < mask[1].sum())         // 短的补了 pad
        assertEquals(0L, mask[0].last())                   // 尾部是 pad
        assertEquals(1L, mask[1].last())                   // [SEP] 计入
    }

    @Test
    fun `normalization wraps chinese and keeps case`() {
        val n = tokenizer.normalize("A端\tB\u0000C")
        assertTrue(n.contains(" 端 "))
        assertTrue(n.contains("A"))
        assertTrue(n.contains("B"))
        assertTrue(!n.contains("\u0000"))
    }
}
