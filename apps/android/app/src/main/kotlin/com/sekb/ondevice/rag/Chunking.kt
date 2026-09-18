package com.sekb.ondevice.rag

/**
 * 端侧切片（chunking）。**纯函数**，不依赖 Android，便于单测。
 *
 * 为什么按**字符**而不是 token：端侧不做子词统计（那是模型内部的事），
 * 中文 1 字 ≈ 1 token，用字符数做长度约束既准又省。
 *
 * 策略（刻意简单，可解释）：
 * 1. 先按空行分段（段落是语义的自然边界）；
 * 2. 段落不超过上限就整段成块；超过就硬切，并在块之间保留 [overlapChars] 个字符的重叠
 *    —— 重叠是为了避免"答案正好被切在缝上"；
 * 3. 丢弃空白块。
 */
object Chunking {

    data class Chunk(
        val sourceId: String,
        val index: Int,
        val text: String,
    )

    const val DEFAULT_MAX_CHARS = 512
    const val DEFAULT_OVERLAP_CHARS = 64

    fun split(
        text: String,
        sourceId: String,
        maxChars: Int = DEFAULT_MAX_CHARS,
        overlapChars: Int = DEFAULT_OVERLAP_CHARS,
    ): List<Chunk> {
        require(maxChars > 0) { "maxChars 必须为正" }
        require(overlapChars in 0 until maxChars) { "overlapChars 必须在 [0, maxChars) 内" }

        val paragraphs = text.replace("\r\n", "\n")
            .split(Regex("\n\\s*\n"))
            .map { it.trim() }
            .filter { it.isNotEmpty() }

        val chunks = mutableListOf<Chunk>()
        val current = StringBuilder()

        fun flush() {
            val t = current.toString().trim()
            if (t.isNotEmpty()) {
                chunks.add(Chunk(sourceId, chunks.size, t))
            }
            current.setLength(0)
        }

        for (p in paragraphs) {
            if (p.length > maxChars) {
                flush()
                for (piece in hardSplit(p, maxChars, overlapChars)) {
                    chunks.add(Chunk(sourceId, chunks.size, piece))
                }
                continue
            }
            // +1 是段落之间补的换行
            if (current.isNotEmpty() && current.length + p.length + 1 > maxChars) flush()
            if (current.isNotEmpty()) current.append('\n')
            current.append(p)
        }
        flush()
        return chunks
    }

    /** 硬切长段落：切成不超过 maxChars 的片段，相邻片段保留 overlap。 */
    private fun hardSplit(text: String, maxChars: Int, overlapChars: Int): List<String> {
        val out = mutableListOf<String>()
        var start = 0
        while (start < text.length) {
            val end = minOf(start + maxChars, text.length)
            val piece = text.substring(start, end).trim()
            if (piece.isNotEmpty()) out.add(piece)
            if (end >= text.length) break
            start = end - overlapChars
        }
        return out
    }
}
