package com.sekb.shared.rag

/**
 * 一份已导入文档的元信息（**不含**正文——正文在切片里）。
 *
 * 为什么需要单独一张表：切片是"检索用的碎片"，用户界面要的是"我导入了哪些文档、
 * 各多少切片、什么时候导的、能不能删"。把这两件事混在一张表里，UI 就只能靠扫描切片去猜。
 */
data class DocumentInfo(
    val id: String,
    /** 展示名（通常是文件名） */
    val name: String,
    val sizeBytes: Long,
    val chunks: Int,
    val addedAtMillis: Long,
    /** 是否按"设备专属"处理（本机导入的文档一律为 true，见 RFC §18.3） */
    val deviceOnly: Boolean = true,
)

/** 文档元信息注册表。 */
interface DocumentRegistry {
    fun upsert(info: DocumentInfo)

    /** 按导入时间倒序（最近导入的在最前）。 */
    fun list(): List<DocumentInfo>

    fun get(id: String): DocumentInfo?

    fun delete(id: String): Boolean

    fun clear()
}

/** 内存实现（单测与"不落盘"场景）。 */
class InMemoryDocumentRegistry : DocumentRegistry {
    private val docs = LinkedHashMap<String, DocumentInfo>()

    override fun upsert(info: DocumentInfo) {
        docs[info.id] = info
    }

    override fun list(): List<DocumentInfo> = docs.values.sortedByDescending { it.addedAtMillis }

    override fun get(id: String): DocumentInfo? = docs[id]

    override fun delete(id: String): Boolean = docs.remove(id) != null

    override fun clear() = docs.clear()
}
