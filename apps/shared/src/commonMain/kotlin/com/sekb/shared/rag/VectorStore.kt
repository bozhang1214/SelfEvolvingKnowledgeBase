package com.sekb.shared.rag

import com.sekb.shared.embed.EmbeddingSpace

/** 索引中的一条向量记录。 */
class VectorRecord(
    val id: String,
    val sourceId: String,
    val text: String,
    val vector: FloatArray,
)

/** 检索命中的一条。 */
data class VectorHit(
    val id: String,
    val sourceId: String,
    val text: String,
    val score: Double,
)

/**
 * 向量存储。
 *
 * **空间的唯一真相在这里**：索引里的向量是用哪个模型算出来的，由 [space] 记着。
 * 换嵌入模型必须重建索引——所以 [space] 是只读属性（构造时确定），
 * 而不是一个可以随手改的字段。
 */
interface VectorStore {
    val space: EmbeddingSpace

    fun upsert(records: List<VectorRecord>)

    /**
     * 删除某份文档的全部切片，返回删除条数。
     *
     * ⚠️ 接口里必须有这个方法。第一版没有它，`KnowledgeIndex.remove()` 用"清空整库"来凑，
     * 结果删一篇文档会把**整个索引**清掉（其余文档的文本是设备侧唯一副本，等于数据丢失）。
     * 教训：接口缺一个"删除"能力时，调用方一定会用一种危险的方式绕过去。
     */
    fun deleteBySource(sourceId: String): Int

    fun search(query: FloatArray, topK: Int): List<VectorHit>

    fun size(): Int

    fun clear()

    /** 列出全部记录（评测/导出用；大批量场景应改为流式，见实现注释）。 */
    fun all(): List<VectorRecord>
}

/**
 * 内存实现：单测与"小语料"场景用。
 *
 * Android 上会换成 SQLite 实现（`SqliteVectorStore`，下一轮）——两者接口一致，
 * 所以 [Retriever] 与工具层不需要知道用的是哪个。
 */
class InMemoryVectorStore(override val space: EmbeddingSpace) : VectorStore {

    private val records = LinkedHashMap<String, VectorRecord>()

    override fun upsert(records: List<VectorRecord>) {
        for (r in records) {
            require(r.vector.size == space.dim) {
                "向量维度不符：期望 ${space.dim}，实际 ${r.vector.size}（换了嵌入模型就必须重建索引）"
            }
            this.records[r.id] = r
        }
    }

    override fun deleteBySource(sourceId: String): Int {
        val doomed = records.values.filter { it.sourceId == sourceId }.map { it.id }
        doomed.forEach { records.remove(it) }
        return doomed.size
    }

    override fun search(query: FloatArray, topK: Int): List<VectorHit> {
        if (query.size != space.dim) return emptyList()
        val scored = VectorMath.topK(query, records.values.map { it.id to it.vector }, topK)
        return scored.mapNotNull { (id, score) ->
            records[id]?.let { VectorHit(it.id, it.sourceId, it.text, score) }
        }
    }

    override fun size(): Int = records.size

    override fun clear() = records.clear()

    override fun all(): List<VectorRecord> = records.values.toList()
}

/**
 * **既是向量库、又是文档登记簿**的存储（端侧两个生产实现都满足它）。
 *
 * 为什么需要这个组合接口：`KnowledgeIndex` 只要求 `VectorStore`，但"文档页"与 RAG 自检
 * 同时需要"切片存取"与"文档登记"两类能力。若变量只声明成 `VectorStore`，
 * 就看不到 `list()`/`upsert(DocumentInfo)`（编译期就报"类型不符"）。
 * 声明成这个组合类型后，调用方**不必在"文件型 / RDB"之间分支**——
 * 两个实现类各自 `: VectorStore, DocumentRegistry`，天然满足。
 */
interface VectorAndDocumentStore : VectorStore, DocumentRegistry {
    /** 打开时发生了什么（自检要如实报出来：新建 / 复用 / 空间戳冲突）。 */
    val openDiagnostics: String

    /** 逻辑"库名"（RDB 是库名，文件型是文件路径）——自检与设置页展示用。 */
    val dbNamePublic: String
}
