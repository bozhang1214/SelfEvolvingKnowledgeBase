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
) : VectorStore {

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
        }

        override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
            // 索引是可重建的派生数据：升级直接丢弃重建，不做迁移（简单且不会带错数据）
            db.execSQL("DROP TABLE IF EXISTS chunks")
            db.execSQL("DROP TABLE IF EXISTS meta")
            onCreate(db)
        }
    }

    init {
        verifySpace()
    }

    /** 打开时校验空间；不一致直接抛，避免两套向量混用。 */
    private fun verifySpace() {
        val db = helper.writableDatabase
        val cursor = db.rawQuery("SELECT v FROM meta WHERE k = 'space'", null)
        cursor.use {
            if (it.moveToFirst()) {
                val stored = it.getString(0)
                check(stored == space.id) {
                    "索引的空间是 $stored，当前嵌入模型是 ${space.id}：换模型必须重建索引（RFC §18.1）"
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

    override fun clear() {
        helper.writableDatabase.execSQL("DELETE FROM chunks")
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

    companion object {
        private const val DB_VERSION = 1

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
