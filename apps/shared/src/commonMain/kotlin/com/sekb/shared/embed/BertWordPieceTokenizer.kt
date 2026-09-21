package com.sekb.shared.embed


/**
 * BERT WordPiece 分词器（`bge-small-zh-v1.5` 用的就是这一套）。
 *
 * **为什么不用现成的 Java 分词库**：Android 上能用的 BERT 分词库要么带 JVM-only 依赖、
 * 要么版本漂移；而这里的规则是**可核对**的（tokenizer.json + vocab.txt + 金标准 id 测试），
 * 自己实现 120 行反而更可控——分词错一点，嵌入就悄悄错一片，所以必须能被测试钉住。
 *
 * 规则严格对齐 `tokenizer.json`（用金标准 token id 验证）：
 * - `BertNormalizer`：clean_text（去控制字符、空白归一）、handle_chinese_chars（CJK 逐字加空格）、
 *   **`lowercase: false`**（⚠️ 注意：`sentence_bert_config.json` 里的 `do_lower_case: true`
 *   是 sentence-transformers 的字段，**实际分词器的正常化配置是 false**——
 *   "Hello" 会变 `[UNK]`，而 "world" 命中词表。按 do_lower_case 去实现就会算出不同向量）；
 * - `strip_accents`: null 且 lowercase=false → **不去重音**；
 * - `BertPreTokenizer`：按空白切分，再把标点单独拆开；
 * - WordPiece：贪心最长匹配，续接前缀 `##`，单词超 100 字符 → `[UNK]`。
 */
class BertWordPieceTokenizer(
    private val vocab: Map<String, Int>,
    private val maxLength: Int = 512,
    private val maxCharsPerWord: Int = 100,
) {


    private val clsId = vocab["[CLS]"] ?: 101
    private val sepId = vocab["[SEP]"] ?: 102
    private val unkId = vocab["[UNK]"] ?: 100

    /** 单条编码：`[CLS] … [SEP]`，超长按 maxLength 截断（保尾部 [SEP]）。 */
    fun encode(text: String): List<Int> {
        val ids = mutableListOf(clsId)
        for (tok in preTokenize(normalize(text))) {
            for (id in wordPiece(tok)) {
                if (ids.size >= maxLength - 1) break
                ids.add(id)
            }
            if (ids.size >= maxLength - 1) break
        }
        ids.add(sepId)
        return ids
    }

    /** 批量编码并 padding（返回 ids 矩阵与 attention mask 矩阵）。 */
    fun encodeBatch(texts: List<String>): Pair<Array<LongArray>, Array<LongArray>> {
        val encoded = texts.map { encode(it) }
        val width = encoded.maxOf { it.size }
        val ids = Array(encoded.size) { i ->
            LongArray(width) { j -> encoded[i].getOrElse(j) { 0 }.toLong() }
        }
        val mask = Array(encoded.size) { i ->
            LongArray(width) { j -> if (j < encoded[i].size) 1L else 0L }
        }
        return ids to mask
    }

    /** BertNormalizer：clean_text + handle_chinese_chars（**不小写**）。 */
    fun normalize(text: String): String {
        val sb = StringBuilder(text.length + 16)
        for (c in text) {
            val code = c.code
            when {
                // 控制字符（保留 \t \n \r）：直接丢弃
                code == 0 || code == 0xFFFD || isControl(code) -> Unit
                c.isWhitespace() -> sb.append(' ')
                isChineseChar(code) -> sb.append(' ').append(c).append(' ')
                else -> sb.append(c)
            }
        }
        return sb.toString()
    }

    /** BertPreTokenizer：空白切分 + 标点独立成 token。 */
    fun preTokenize(normalized: String): List<String> {
        val out = mutableListOf<String>()
        for (chunk in normalized.split(' ')) {
            if (chunk.isEmpty()) continue
            val cur = StringBuilder()
            for (c in chunk) {
                if (isPunctuation(c)) {
                    if (cur.isNotEmpty()) {
                        out.add(cur.toString())
                        cur.setLength(0)
                    }
                    out.add(c.toString())
                } else {
                    cur.append(c)
                }
            }
            if (cur.isNotEmpty()) out.add(cur.toString())
        }
        return out
    }

    /** WordPiece 贪心最长匹配。 */
    fun wordPiece(token: String): List<Int> {
        if (token.length > maxCharsPerWord) return listOf(unkId)
        val out = mutableListOf<Int>()
        var start = 0
        while (start < token.length) {
            var end = token.length
            var matched: Int? = null
            while (start < end) {
                val piece = if (start > 0) "##" + token.substring(start, end) else token.substring(start, end)
                vocab[piece]?.let {
                    matched = it
                    break
                }
                end--
            }
            if (matched == null) return listOf(unkId)      // 有一个子词匹配不上 → 整词 [UNK]（与 HF 一致）
            out.add(matched)
            start = end
        }
        return out
    }

    companion object {
        /**
         * 从**词表文本**建表（不是从 File）。
         *
         * 为什么改成吃文本：`java.io.File` 是 JVM 专有，而分词规则本身是纯逻辑（要进 KMP `shared`）。
         * 读文件这件事交给平台端口（见 `core/PlatformFiles.kt`），传进来的只是字符串——
         * 这样分词器在 iOS/鸿蒙上能原样编译，行为也完全一致。
         */
        fun loadVocab(vocabText: String): Map<String, Int> {
            val map = HashMap<String, Int>(32768)
            vocabText.lineSequence().forEach { line ->
                val t = line.trimEnd('\n', '\r')
                if (t.isNotEmpty() || map.isEmpty()) if (!map.containsKey(t)) map[t] = map.size
            }
            return map
        }

        /** CJK 判定：与 HuggingFace `is_chinese_char` 同一区间集合。 */
        fun isChineseChar(code: Int): Boolean =
            (code in 0x4E00..0x9FFF) || (code in 0x3400..0x4DBF) ||
                (code in 0x20000..0x2A6DF) || (code in 0x2A700..0x2B73F) ||
                (code in 0x2B740..0x2B81F) || (code in 0x2B820..0x2CEAF) ||
                (code in 0xF900..0xFAFF) || (code in 0x2F800..0x2FA1F)

        private fun isControl(code: Int): Boolean =
            (code in 0x00..0x1F && code != 0x09 && code != 0x0A && code != 0x0D) ||
                (code in 0x7F..0x9F)

        /** 标点判定：ASCII 标点 + Unicode 里的 P* 类（与 BERT 的 `_is_punctuation` 等价）。 */
        fun isPunctuation(c: Char): Boolean {
            val code = c.code
            if ((code in 33..47) || (code in 58..64) || (code in 91..96) || (code in 123..126)) return true
            // 用 Kotlin 的 CharCategory（common 可用）替代 JVM 的 `Character.getType`
            return when (c.category) {
                CharCategory.CONNECTOR_PUNCTUATION,
                CharCategory.DASH_PUNCTUATION,
                CharCategory.START_PUNCTUATION,
                CharCategory.END_PUNCTUATION,
                CharCategory.INITIAL_QUOTE_PUNCTUATION,
                CharCategory.FINAL_QUOTE_PUNCTUATION,
                CharCategory.OTHER_PUNCTUATION,
                -> true
                else -> false
            }
        }
    }
}
