package com.sekb.shared.rag

import kotlin.math.sqrt

/**
 * 向量相似度（纯函数）。
 *
 * 检索首批用**暴力余弦**：10k 段 × 512 维 ≈ 20MB，一次全量点积在本机是毫秒级。
 * 等语料上万段再上 sqlite-vec / HNSW——那时换的是 [VectorStore] 的实现，
 * 这里的语义（余弦 + top-k + 阈值）不变。
 */
object VectorMath {

    /**
     * 余弦相似度，值域 [-1, 1]。
     *
     * 维度不一致或存在零向量时返回 `0.0`：这是"无法比较"的保守取值，
     * 而不是抛异常——检索链路上不该因为一条脏数据整条链路挂掉。
     */
    fun cosine(a: FloatArray, b: FloatArray): Double {
        if (a.size != b.size || a.isEmpty()) return 0.0
        var dot = 0.0
        var na = 0.0
        var nb = 0.0
        for (i in a.indices) {
            dot += a[i].toDouble() * b[i]
            na += a[i].toDouble() * a[i]
            nb += b[i].toDouble() * b[i]
        }
        if (na == 0.0 || nb == 0.0) return 0.0
        return dot / (sqrt(na) * sqrt(nb))
    }

    /** top-k：按相似度降序；分数相同时按 id 升序（保证结果稳定可测）。 */
    fun topK(query: FloatArray, items: List<Pair<String, FloatArray>>, k: Int): List<Pair<String, Double>> {
        if (k <= 0) return emptyList()
        return items
            .map { (id, v) -> id to cosine(query, v) }
            .sortedWith(compareByDescending<Pair<String, Double>> { it.second }.thenBy { it.first })
            .take(k)
    }
}
