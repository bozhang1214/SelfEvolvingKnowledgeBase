package com.sekb.ondevice.rag

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import com.sekb.ondevice.embed.EmbeddingSpace
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * SQLite 向量存储（端侧索引的**持久化**实现）。
 *
 * 三个设计决定，都是"不这么做就会埋雷"的那种：
 *
 * 1. **向量以 float32 小端 BLOB 存**（512 维 = 2KB/块；1 万块 ≈ 20MB）。用 BLOB 而不是
 *    JSON 文本：解析开销与体积都差一个数量级。
 * 2. **检索仍是 Kotlin 里的暴力余弦**：sqlite-vec 需要额外打包原生扩展，Android 上不是
 *    开箱即用；1 万块的暴力点积在本机是毫秒级（实测见自检输出）。升级触发条件写在
 *    [VectorStore] 的注释里——语料上万块或延迟进入几十毫秒量级时再换 ANN。
 * 3. **空间戳入库并在打开时校验**：索引文件的生命周期比 App 版本长。若将来把嵌入模型
 *    从桩换成 ONNX（不同空间），打开旧库必须**当场失败并要求重建**，而不是拿两种模型的
 *    向量混算余弦——那会返回"看起来相关但其实错"的结果（RFC §18.1）。
 */
class SqliteVectorStore(
    context: Context,
    override val space: EmbeddingSpace,
    dbName: String = "sekb_rag.db",
    /** false = 容忍"库里记的空间与当前模型不一致"地打开（用于**原地重嵌入**，见 [reembed]） */
    strictSpace: Boolean = true,
) : VectorStore, DocumentRegistry {

    private val helper = object : SQLiteOpenHelper(context.applicationContext, dbName, null, DB_VERSION) {
        override fun onCreate(db: SQLiteDatabase) {
            db.execSQL(
                "CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT NOT NULL)",
            )
            db.execSQL(
                """
                CREATE TABLE chunks (
                    id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    dim INTEGER NOT NULL,
                    vector BLOB NOT NULL
                )
                """.trimIndent(),
            )
            db.execSQL("CREATE INDEX idx_chunks_source ON chunks(source_id)")
            createDocumentsTable(db)
        }

        override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
            // 索引是可重建的派生数据：升级直接丢弃重建，不做迁移（简单且不会带错数据）
            db.execSQL("DROP TABLE IF EXISTS chunks")
            db.execSQL("DROP TABLE IF EXISTS meta")
            onCreate(db)
        }
    }

    init {
        verifySpace(strict = strictSpace)
    }

    /** 打开时校验空间；不一致直接抛（strict），避免两套向量混用。 */
    private fun verifySpace(strict: Boolean) {
        val db = helper.writableDatabase
        val cursor = db.rawQuery("SELECT v FROM meta WHERE k = 'space'", null)
        cursor.use {
            if (it.moveToFirst()) {
                val stored = it.getString(0)
                if (stored != space.id) {
                    check(!strict) {
                        "索引的空间是 $stored，当前嵌入模型是 ${space.id}：换模型必须**重算**（RFC §18.1）"
                    }
                }
            } else {
                db.insertWithOnConflict(
                    "meta", null,
                    ContentValues().apply {
                        put("k", "space")
                        put("v", space.id)
                    },
                    SQLiteDatabase.CONFLICT_REPLACE,
                )
                db.insertWithOnConflict(
                    "meta", null,
                    ContentValues().apply {
                        put("k", "dim")
                        put("v", space.dim.toString())
                    },
                    SQLiteDatabase.CONFLICT_REPLACE,
                )
            }
        }
    }

    /** 库里记录的空间（空库返回 null）。 */
    fun storedSpace(): String? = helper.readableDatabase
        .rawQuery("SELECT v FROM meta WHERE k = 'space'", null)
        .use { if (it.moveToFirst()) it.getString(0) else null }

    /**
     * **原地重嵌入**：换嵌入模型时不丢数据。
     *
     * 为什么不是"删库重建"：索引里的**文本是设备侧的唯一副本**（源文档可能只在设备上，
     * 甚至只在这一次会话里）。RFC §4.5-F 对这情况的原文就是"**重算，不要复用缓存**"——
     * 所以正确动作是用新模型把已有片段重算一遍，而不是丢弃。
     *
     * @return 重算的片段数
     */
    fun reembed(provider: com.sekb.ondevice.embed.EmbeddingProvider, batch: Int = 16): Int {
        val rows = all()
        if (rows.isEmpty()) {
            writeSpace()
            return 0
        }
        var done = 0
        for (chunk in rows.chunked(batch)) {
            val vectors = provider.embed(chunk.map { it.text })
            chunk.forEachIndexed { i, r ->
                require(vectors[i].size == space.dim) {
                    "新模型维度不符：期望 ${space.dim}，实际 ${vectors[i].size}"
                }
            }
            upsert(chunk.mapIndexed { i, r -> VectorRecord(r.id, r.sourceId, r.text, vectors[i]) })
            done += chunk.size
        }
        writeSpace()
        return done
    }

    private fun writeSpace() {
        val db = helper.writableDatabase
        for ((k, v) in listOf("space" to space.id, "dim" to space.dim.toString())) {
            db.insertWithOnConflict("meta", null,
                ContentValues().apply { put("k", k); put("v", v) },
                SQLiteDatabase.CONFLICT_REPLACE)
        }
    }

    override fun upsert(records: List<VectorRecord>) {
        if (records.isEmpty()) return
        val db = helper.writableDatabase
        db.beginTransaction()
        try {
            for (r in records) {
                require(r.vector.size == space.dim) {
                    "向量维度不符：期望 ${space.dim}，实际 ${r.vector.size}（换了嵌入模型就必须重建索引）"
                }
                db.insertWithOnConflict(
                    "chunks", null,
                    ContentValues().apply {
                        put("id", r.id)
                        put("source_id", r.sourceId)
                        put("text", r.text)
                        put("dim", r.vector.size)
                        put("vector", encode(r.vector))
                    },
                    SQLiteDatabase.CONFLICT_REPLACE,
                )
            }
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }
    }

    override fun deleteBySource(sourceId: String): Int =
        helper.writableDatabase.delete("chunks", "source_id = ?", arrayOf(sourceId))

    override fun search(query: FloatArray, topK: Int): List<VectorHit> {
        if (query.size != space.dim) return emptyList()
        // 全量读一次再算余弦：语料上万块前都够用（见类注释第 2 点）。
        // 只读一次 —— 之前写成"读两遍（一遍取向量、一遍建索引）"，两遍之间数据若被改就会错配。
        val records = all()
        val byId = records.associateBy { it.id }
        return VectorMath.topK(query, records.map { it.id to it.vector }, topK).mapNotNull { (id, score) ->
            byId[id]?.let { VectorHit(it.id, it.sourceId, it.text, score) }
        }
    }

    override fun size(): Int = helper.readableDatabase
        .rawQuery("SELECT COUNT(*) FROM chunks", null).use { if (it.moveToFirst()) it.getInt(0) else 0 }

    /**
     * 清空索引：**切片与文档元信息一起清**。
     *
     * `VectorStore.clear()` 与 `DocumentRegistry.clear()` 是同一个签名，所以只能有一份实现；
     * 语义上合成"清空索引"也更合理——留下一个指向已删切片的文档列表只会误导用户。
     */
    override fun clear() {
        val db = helper.writableDatabase
        db.beginTransaction()
        try {
            db.execSQL("DELETE FROM chunks")
            db.execSQL("DELETE FROM documents")
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }
    }

    override fun all(): List<VectorRecord> {
        val out = mutableListOf<VectorRecord>()
        helper.readableDatabase.rawQuery(
            "SELECT id, source_id, text, vector FROM chunks", null,
        ).use { c ->
            while (c.moveToNext()) {
                out.add(VectorRecord(c.getString(0), c.getString(1), c.getString(2), decode(c.getBlob(3))))
            }
        }
        return out
    }

    /** 立即释放句柄（测试/自检里模拟"App 重启"）。 */
    fun close() = helper.close()

    // ---------- DocumentRegistry ----------

    override fun upsert(info: DocumentInfo) {
        helper.writableDatabase.insertWithOnConflict(
            "documents", null,
            ContentValues().apply {
                put("id", info.id)
                put("name", info.name)
                put("size_bytes", info.sizeBytes)
                put("chunks", info.chunks)
                put("added_at", info.addedAtMillis)
                put("device_only", if (info.deviceOnly) 1 else 0)
            },
            SQLiteDatabase.CONFLICT_REPLACE,
        )
    }

    override fun list(): List<DocumentInfo> {
        val out = mutableListOf<DocumentInfo>()
        helper.readableDatabase.rawQuery(
            "SELECT id, name, size_bytes, chunks, added_at, device_only FROM documents ORDER BY added_at DESC",
            null,
        ).use { c ->
            while (c.moveToNext()) {
                out.add(
                    DocumentInfo(
                        id = c.getString(0), name = c.getString(1), sizeBytes = c.getLong(2),
                        chunks = c.getInt(3), addedAtMillis = c.getLong(4), deviceOnly = c.getInt(5) == 1,
                    ),
                )
            }
        }
        return out
    }

    override fun get(id: String): DocumentInfo? = list().firstOrNull { it.id == id }

    override fun delete(id: String): Boolean =
        helper.writableDatabase.delete("documents", "id = ?", arrayOf(id)) > 0

    companion object {
        private const val DB_VERSION = 2

        /** 文档元信息表（升级时用 `IF NOT EXISTS` 补建，保证不动已有切片）。 */
        fun createDocumentsTable(db: SQLiteDatabase) {
            db.execSQL(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    chunks INTEGER NOT NULL,
                    added_at INTEGER NOT NULL,
                    device_only INTEGER NOT NULL
                )
                """.trimIndent(),
            )
        }

        /** 打开结果：是否发生了重嵌入、旧空间是什么。 */
        data class OpenResult(val store: SqliteVectorStore, val reembedded: Int, val previousSpace: String?)

        /**
         * 打开索引；若库里记的空间与当前模型不一致 → **原地重嵌入**（不丢数据、不混用向量）。
         *
         * 这条路径就是 RFC §18.1 那个"空间戳"在真实 App 里的落地：桩 → ONNX、
         * 或 ONNX fp32 → int8，都会走到这里。
         */
        fun openOrReembed(
            context: Context,
            provider: com.sekb.ondevice.embed.EmbeddingProvider,
            dbName: String = "sekb_rag.db",
        ): OpenResult {
            val probe = SqliteVectorStore(context, provider.space, dbName, strictSpace = false)
            val stored = probe.storedSpace()
            if (stored == null || stored == provider.space.id) {
                return OpenResult(probe, 0, stored)
            }
            val n = probe.reembed(provider)
            return OpenResult(probe, n, stored)
        }

        fun encode(v: FloatArray): ByteArray {
            val buf = ByteBuffer.allocate(v.size * 4).order(ByteOrder.LITTLE_ENDIAN)
            for (x in v) buf.putFloat(x)
            return buf.array()
        }

        fun decode(b: ByteArray): FloatArray {
            val buf = ByteBuffer.wrap(b).order(ByteOrder.LITTLE_ENDIAN)
            val out = FloatArray(b.size / 4)
            for (i in out.indices) out[i] = buf.getFloat()
            return out
        }
    }
}
