// H5 · 端口实现：`VectorStore` + `DocumentRegistry` 的 **RDB（NDK C API）** 版本。
//
// ## 语义与 Android `SqliteVectorStore` **逐条对齐**（这是本文件最重要的约束）
//
// 共享层的 `KnowledgeIndex`/`Retriever` 只有一套逻辑（KMP 口径），所以两端实现必须是
// "同一个接口的两种落地"，而不是"两种行为"。逐条对照如下：
//
// | 行为 | Android（`SqliteVectorStore`） | 本文件 |
// |---|---|---|
// | schema | `chunks(id PK, source_id, text, dim, vector BLOB)` + `documents(...)` + `meta(k,v)` | 同表同列（见 [DDL]） |
// | 向量编码 | `ByteBuffer` **小端 float32** BLOB | 同口径（`encodeVector`/`decodeVector`，逐字节） |
// | `upsert` | `insertWithOnConflict(..., CONFLICT_REPLACE)` | `OH_Rdb_InsertWithConflictResolution(..., RDB_CONFLICT_REPLACE)` |
// | 维度不符 | `require(...) { "向量维度不符：期望 …" }` | **同一条消息**（`require`） |
// | `deleteBySource` | `DELETE FROM chunks WHERE source_id = ?`，返回删除条数 | predicates `equalTo`，同返回 |
// | `search` | 全量读一次 → Kotlin 暴力余弦 → `VectorMath.topK` | 同（**不做 ANN**，理由见类注释） |
// | `search` 维度不符 | **返回空列表**（不抛） | 同（返回 `emptyList()`） |
// | 空间戳 | `meta` 表里存 `space`/`dim`，打开时校验，不一致**抛** | 同 |
// | `clear` | **切片与文档元信息一起清** | 同 |
// | `list()` | `ORDER BY added_at DESC` | 同（SQL 里排序，不靠 Kotlin 再排） |
// | `get(id)` | `list().firstOrNull { it.id == id }` | 同语义（用 predicates，结果一致） |
//
// ## 检索为什么仍是**暴力余弦**（而不是让 RDB 算）
//
// 见 Android 那份的类注释第 2 点：`sqlite-vec` 需要额外的原生扩展，端侧不是开箱即用。
// 语料上万块之前，本机暴力点积是毫秒级；升级触发条件是"语料上万块或延迟进几十毫秒"。
// **两条实现一致**很重要：如果鸿蒙这边偷偷换了排序口径（例如浮点累加顺序不同），
// "同表"这条验收就会在排序细节上产生无法解释的差异。
//
// ⚠️ 唯一无法避免的差异：`VectorMath.topK` 里 `sortedByDescending` 对**同分**元素的
// 相对顺序取决于输入顺序（Kotlin 的 `sortedBy` 是稳定排序），而 RDB 的 `SELECT` 不带
// `ORDER BY` 时行序由引擎决定。**同分**在真实向量上几乎不出现（余弦是连续值），
// 但评测集里如果有两条完全相同的文本就会暴露——所以 `all()` 统一按 `id` 升序读，
// 让"输入顺序"在**两端都可复现**（Android 那份没显式排序，这是鸿蒙侧多做的一点）。
@file:OptIn(kotlinx.cinterop.ExperimentalForeignApi::class)

package com.sekb.ohos.spike

import com.sekb.shared.embed.EmbeddingSpace
import com.sekb.shared.rag.DocumentInfo
import com.sekb.shared.rag.DocumentRegistry
import com.sekb.shared.rag.VectorHit
import com.sekb.shared.rag.VectorMath
import com.sekb.shared.rag.VectorRecord
import com.sekb.shared.rag.VectorStore

/**
 * RDB 向量存储 + 文档登记（**一个类实现两个端口**）。
 *
 * 为什么合成一个类：与 Android 一样，`clear()` 在两个接口里签名相同、语义上就是
 * "清空索引"（切片 + 文档元信息一起清）。拆成两个类就得让它们共享同一个 `RdbStore`，
 * 反而多一层传递。
 *
 * @param databaseDir `UIAbilityContext.databaseDir`
 * @param space 向量空间的**唯一真相**：索引里的向量是哪个模型算的。换模型必须重建索引，
 *   所以它是构造参数（只读），并在打开时与库里存的戳**校验**。
 * @param strictSpace false = 容忍"库里记的空间与当前模型不一致"地打开（给原地重嵌入用）
 */
class RdbVectorStore(
    databaseDir: String,
    override val space: EmbeddingSpace,
    private val dbName: String = "sekb_rag.db",
    private val strictSpace: Boolean = true,
) : VectorStore, DocumentRegistry {

    private val store: RdbStore

    /** 打开时发生了什么（自检要报出来：是不是新建、有没有空间戳冲突）。 */
    val openDiagnostics: String

    init {
        val (s, err) = RdbStore.open(databaseDir, dbName)
        require(s != null) { "打开 RDB 失败：errCode=$err dir=$databaseDir name=$dbName" }
        store = s
        var diag = "errCode=$err 库=$dbName"
        // 先建 meta（空间戳表），再建其余表：即使空间校验决定要报错，戳也得先能读出来
        require(store.execute(DDL_META) == RdbCode.OK) { "建表 meta 失败（RDB rc≠0）" }
        val stored = readMeta("space")
        if (stored == null) {
            writeMeta("space", space.id)
            writeMeta("dim", space.dim.toString())
            diag += " 新建索引（写入空间戳 ${space.id}）"
        } else if (stored != space.id) {
            // 与 Android 同口径：不一致**当场失败**，绝不拿两种模型的向量混算余弦
            check(!strictSpace) {
                "索引的空间是 $stored，当前嵌入模型是 ${space.id}：换模型必须**重算**（RFC §18.1）"
            }
            diag += " ⚠️ 空间戳 $stored ≠ 当前 ${space.id}（strictSpace=false，容忍打开）"
        } else {
            diag += " 复用已有索引（空间戳=$stored）"
        }
        require(store.execute(DDL_CHUNKS) == RdbCode.OK) { "建表 chunks 失败（RDB rc≠0）" }
        require(store.execute(DDL_CHUNKS_INDEX) == RdbCode.OK) { "建 chunks 索引失败" }
        require(store.execute(DDL_DOCUMENTS) == RdbCode.OK) { "建表 documents 失败" }
        openDiagnostics = diag
    }

    /** 库里记录的空间（空库返回 null）。 */
    fun storedSpace(): String? = readMeta("space")

    // ── 元信息 ──────────────────────────────────────────────────────────────
    private fun readMeta(k: String): String? {
        val cur = store.query("SELECT v FROM meta WHERE k = '$k'") ?: return null
        var v: String? = null
        if (cursorNext(cur) == 0) v = cursorText(cur, "v")
        cursorDestroy(cur)
        return v
    }

    private fun writeMeta(k: String, v: String) {
        // 纯 SQL（理由见 RdbStore.insertReplace 的注释：Kotlin 侧 ValuesBucket 实测不可用）
        val rc = store.execute(
            "INSERT OR REPLACE INTO meta (k, v) VALUES (${sqlQuote(k)}, ${sqlQuote(v)})",
        )
        check(rc == RdbCode.OK) { "写 meta($k) 失败 rc=$rc" }
    }

    // ── VectorStore ─────────────────────────────────────────────────────────

    override fun upsert(records: List<VectorRecord>) {
        if (records.isEmpty()) return
        // 与 Android 相同的两道闸：① 维度必须与空间一致 ② 整批在一个事务里
        for (r in records) {
            require(r.vector.size == space.dim) {
                "向量维度不符：期望 ${space.dim}，实际 ${r.vector.size}（换了嵌入模型就必须重建索引）"
            }
        }
        // ⚠️ **事务三个函数的返回值必须检查**（2026-10-26 主线加）：原来 `beginTransaction()`/`commit()`
        //    不看返回值，而实测出现过"insert rc=0 但提交后仍读不到/读到旧数据"的怪象。
        //    把三个 rc 记进 `lastTx` 并带进返回值，一眼能判定"事务是不是没生效"。
        val rcb = store.beginTransaction()
        if (rcb != RdbCode.OK) error("beginTransaction 失败 rc=$rcb")
        val before = runCatching { size() }.getOrElse { -1 }
        try {
            for (r in records) {
                val rc = store.execute(
                    "INSERT OR REPLACE INTO chunks (id, source_id, text, dim, vector) VALUES (" +
                        "${sqlQuote(r.id)}, ${sqlQuote(r.sourceId)}, ${sqlQuote(r.text)}, " +
                        "${r.vector.size}, ${sqlBlob(encodeVector(r.vector))})",
                )
                if (rc != RdbCode.OK) error("upsert 失败：id=${r.id} rc=$rc")
            }
            val mid = runCatching { size() }.getOrElse { -1 }   // 事务内应能看到新行
            val rcc = store.commit()
            val after = runCatching { size() }.getOrElse { -1 }  // 提交后也应看得到
            lastTx = "begin=$rcb before=$before inTx=$mid commit=$rcc after=$after"
            if (rcc != RdbCode.OK) error("commit 失败 rc=$rcc（数据未落盘）")
        } catch (e: Throwable) {
            store.rollBack()
            lastTx = "$lastTx rollback=1 err=${e.message}"
            throw e
        }
    }

    /** 最近一次写入的事务诊断（`begin/before/inTx/commit/after` 五个数，一眼判定事务是否生效）。 */
    var lastTx: String = ""
        private set

    /**
     * 删一份文档的全部切片，返回**删除条数**。
     *
     * ⚠️ 这里**不能**用 `DELETE ... ; SELECT changes()` 的两步写法吗？可以——[RdbStore.execChanges]
     * 就是它。用 SQL 而不是 `OH_Predicates`，与写入路径同一理由（Kotlin 侧结构体函数指针那条路
     * 实测不可用，见 `RdbStore.insertReplace` 的注释）。
     * 接口契约（`VectorStore.deleteBySource` 的注释）要求"返回删除条数"，所以必须用 `changes()`，
     * 不能只判 `rc == 0`。
     */
    override fun deleteBySource(sourceId: String): Int =
        store.execChanges("DELETE FROM chunks WHERE source_id = ${sqlQuote(sourceId)}")

    override fun search(query: FloatArray, topK: Int): List<VectorHit> {
        // 与 Android 同口径：维度不符**返回空列表**（不抛）——
        // 调用方（Retriever）已在上游用 `space` 判过空间，这里是防御性的第二道。
        if (query.size != space.dim) return emptyList()
        // 只读一次（Android 那份的注释记着教训：读两遍时若数据被改就会 id↔向量错配）
        //
        // ⚠️ **但这一次读必须"读得到"**（2026-10-26 主线实测）：设备上出现过
        //    `size()`>0、同一进程稍后 `all()` 也读得到，而**检索当刻的这一次 `all()` 返回空**
        //    → 分数算不出来、检索 0 条（`reason` 为空、`最高分` 都没有）。
        //    与写入侧的可见性延迟是同一类现象，所以这里用**同一个办法**：有界轮询
        //    （最多 4 次 × 40ms）；只要拿到非空就立刻用第一次的结果（仍然只"采用一次"读数，
        //    不会出现"读两遍导致 id↔向量错配"的老问题）。
        var records = all()
        if (records.isEmpty()) {
            val n = size()
            if (n > 0) {
                for (k in 1..3) {
                    platform.posix.usleep(40_000u)
                    records = all()
                    if (records.isNotEmpty()) break
                }
            }
        }
        val byId = records.associateBy { it.id }
        return VectorMath.topK(query, records.map { it.id to it.vector }, topK).mapNotNull { (id, score) ->
            byId[id]?.let { VectorHit(it.id, it.sourceId, it.text, score) }
        }
    }

    override fun size(): Int {
        val cur = store.query("SELECT COUNT(*) AS n FROM chunks") ?: return 0
        var n = 0
        if (cursorNext(cur) == 0) n = (cursorInt64(cur, "n") ?: 0L).toInt()
        cursorDestroy(cur)
        return n
    }

    /**
     * 清空索引：**切片与文档元信息一起清**（与 Android 注释里同一条理由——
     * 留下指向已删切片的文档列表只会误导用户）。
     */
    override fun clear() {
        store.beginTransaction()
        try {
            store.execute("DELETE FROM chunks")
            store.execute("DELETE FROM documents")
            store.commit()
        } catch (e: Throwable) {
            store.rollBack()
            throw e
        }
    }

    /** 列出全部记录（评测/导出用）。按 id 升序，让"输入顺序"两端可复现（见文件头注释）。 */
    override fun all(): List<VectorRecord> {
        val cur = store.query(
            "SELECT id, source_id, text, vector FROM chunks ORDER BY id ASC",
        ) ?: return emptyList()
        val out = mutableListOf<VectorRecord>()
        while (cursorNext(cur) == 0) {
            val id = cursorText(cur, "id") ?: continue
            val src = cursorText(cur, "source_id") ?: ""
            val text = cursorText(cur, "text") ?: ""
            val blob = cursorBlob(cur, "vector") ?: ByteArray(0)
            out += VectorRecord(id, src, text, decodeVector(blob))
        }
        cursorDestroy(cur)
        return out
    }

    // ── DocumentRegistry ────────────────────────────────────────────────────

    override fun upsert(info: DocumentInfo) {
        val rc = store.execute(
            "INSERT OR REPLACE INTO documents " +
                "(id, name, size_bytes, chunks, added_at, device_only) VALUES (" +
                "${sqlQuote(info.id)}, ${sqlQuote(info.name)}, ${info.sizeBytes}, ${info.chunks}, " +
                "${info.addedAtMillis}, ${if (info.deviceOnly) 1 else 0})",
        )
        if (rc != RdbCode.OK) error("登记文档失败：id=${info.id} rc=$rc")
    }

    /** 按导入时间倒序（最近导入的在最前）——**在 SQL 里排**，与 Android 的 `ORDER BY added_at DESC` 一致。 */
    override fun list(): List<DocumentInfo> {
        // ⚠️ **切片数从 `chunks` 表算，而不是信 `documents.chunks` 列**（2026-10-26 主线实测后改）：
        //    设备上出现"文档登记成功（列表里能看到这一篇）但 `chunks` 列恒为 0"，
        //    且**轮询 4 次读回来仍是 0**（`docs=1篇/0段(轮4)`）——而同一时刻 `chunks` 表里确实有切片。
        //    切片表才是**事实来源**（检索/删除都按它走），`documents.chunks` 只是冗余缓存；
        //    顺着事实来源算，既修好这个显示，也避免两处计数长期不一致。
        //    `LEFT JOIN` + `COUNT` 保证"没有切片的文档"仍会出现（计数 0），不会因为 INNER JOIN 丢行。
        val cur = store.query(
            "SELECT d.id AS id, d.name AS name, d.size_bytes AS size_bytes, " +
                "d.added_at AS added_at, d.device_only AS device_only, " +
                "COUNT(c.id) AS chunks FROM documents d " +
                "LEFT JOIN chunks c ON c.source_id = d.id " +
                "GROUP BY d.id, d.name, d.size_bytes, d.added_at, d.device_only " +
                "ORDER BY d.added_at DESC",
        ) ?: return emptyList()
        val out = mutableListOf<DocumentInfo>()
        while (cursorNext(cur) == 0) {
            val id = cursorText(cur, "id") ?: continue
            out += DocumentInfo(
                id = id,
                name = cursorText(cur, "name") ?: id,
                sizeBytes = cursorInt64(cur, "size_bytes") ?: 0L,
                chunks = (cursorInt64(cur, "chunks") ?: 0L).toInt(),
                addedAtMillis = cursorInt64(cur, "added_at") ?: 0L,
                deviceOnly = (cursorInt64(cur, "device_only") ?: 1L) == 1L,
            )
        }
        cursorDestroy(cur)
        return out
    }

    override fun get(id: String): DocumentInfo? = list().firstOrNull { it.id == id }

    /**
     * 删一条登记。与 Android 同语义：返回"是否真的删到了行"。
     *
     * ⚠️ `OH_Rdb_Delete` 只回状态码、不回行数，所以"是否删到"必须先查一次。
     * 这里没有走 `OH_Rdb_DeleteStore` 的 predicates 路径，是因为 predicates 的
     * `equalTo` 需要 `OH_VObject`（见 [RdbStore.deleteBySourceColumn]）——
     * 为了一个 `id` 主键查询多绕一层不值得；`id` 由共享层生成（`"$sourceId#${i}"`），
     * 不含引号，直接拼字面量的风险面比 `sourceId` 小得多。
     */
    override fun delete(id: String): Boolean {
        // 与 Android 同语义：返回"是否真的删到了行"。
        // `OH_Rdb_Execute` 不回行数 → 用 `changes()`（见 RdbStore.execChanges）。
        val n = store.execChanges("DELETE FROM documents WHERE id = ${sqlQuote(id)}")
        return n > 0
    }

    /** 关掉底层句柄（自检里模拟"App 重启"：必须真的关，否则读到的可能是同一份内存缓存）。 */
    fun close(): Int = store.close()

    /**
     * 本实例用的库名（自检要"关掉再重开同一个库"来证明落盘，需要它）。
     *
     * 为什么不留一个"重新打开自己"的方法：那会把 `open` 的参数缓存在对象里，
     * 与 Android 的 `openOrReembed(probe, ...)` 两段式形态不一致。给个库名，
     * 自检自己再 `RdbVectorStore(dir, space, dbNamePublic)` 一次即可。
     */
    val dbNamePublic: String get() = dbName

    private companion object {
        const val DDL_META = "CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL)"
        const val DDL_CHUNKS =
            "CREATE TABLE IF NOT EXISTS chunks (" +
                "id TEXT PRIMARY KEY, source_id TEXT NOT NULL, text TEXT NOT NULL, " +
                "dim INTEGER NOT NULL, vector BLOB NOT NULL)"
        const val DDL_CHUNKS_INDEX = "CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source_id)"
        const val DDL_DOCUMENTS =
            "CREATE TABLE IF NOT EXISTS documents (" +
                "id TEXT PRIMARY KEY, name TEXT NOT NULL, size_bytes INTEGER NOT NULL, " +
                "chunks INTEGER NOT NULL, added_at INTEGER NOT NULL, device_only INTEGER NOT NULL)"
    }
}
