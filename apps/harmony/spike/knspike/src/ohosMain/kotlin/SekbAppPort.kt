// H8（工作流 G）· 文档页 + 检索来源的**共享层宿主**。
//
// ## 这里做的事，与 `RagSelfTest.kt` 是**同一条链路**
//
// `KnowledgeIndex`（分块 + 登记）/`Retriever`（阈值 + 空间/隐私闸门）/`RdbVectorStore`
// （RDB NDK 持久化）全部来自共享层与工作流 F 的实现——本文件**没有**第二套检索逻辑。
// 它只是把这些能力按"界面要什么"重新组织成几个进程内单例：
//
//   open()       → 把模型 + RDB 索引就绪（失败**不抛**，返回 `ok=false` + 原因）
//   documents()  → 列表（名称/片段数/体积）
//   ingest()     → 导入（文本/Markdown；PDF 后置）
//   remove()     → **单篇删除**（走 `KnowledgeIndex.remove` → `deleteBySource`，不误清全库）
//   retrieve()   → 聊天页的"检索来源"
//
// ## 降级纪律（H8 验收明确要求）
//
// 工作流 F 的 RDB 链路当时**可能还不绿**。所以本文件的每一个入口都遵守：
// **失败不抛、不假装成功**，把原因如实回报给界面，由界面显示"未持久化 / 不可用 + 原因"。
// 判据：`ok=false` 时 `reason` 必须非空且能定位（含异常类名与消息），
// 绝不允许"看起来正常但库是空的"。
//
// ## 为什么嵌入是**串行**的
//
// `Index.ts` 的实测教训（`apps/harmony/README.md` 2026-10-25）：两个模型会话**并发**会崩在
// `mindspore::lite::MallocTensorData`。`MsSessionCache` 是进程内单例，所以这里用
// `lock` 把所有"要跑模型"的入口串行化，而不是依赖调用方自觉。
@file:OptIn(kotlinx.cinterop.ExperimentalForeignApi::class, kotlin.experimental.ExperimentalNativeApi::class)

package com.sekb.ohos.spike

import com.sekb.shared.core.Fmt
import com.sekb.shared.core.JsonObject
import com.sekb.shared.embed.EmbeddingSpace
import com.sekb.shared.rag.KnowledgeIndex
import com.sekb.shared.rag.Retriever
import com.sekb.shared.rag.VectorStore

/**
 * 文档页/来源列表需要的**全部**状态（一个对象，避免界面拿多个全局各读一半）。
 *
 * ⚠️ 名字里带 `LocalDoc` 而不是 `Rag`：工作流 F 在 `RagSelfTest.kt` 里已经有一个
 * `object RagSession`（分步 RAG 的进程内单例，是 F 交付的接口面）。Kotlin **同包内重名**
 * 会直接编译失败（`Redeclaration` + 一串"成员解析不到"的连锁错），而且我在本机没能复现
 * 那条错误（我的构建早于 F 的改动落地）——这类"两边各自能编、合起来编不过"的冲突
 * 就是协作纪律要防的东西：**看到重名先改名，不要动别人的文件**。
 */
private class LocalDocSession(
    val embed: MindSporeBgeEmbedding,
    /** 通用端口类型：**RDB 与文件型实现可以互换**（见 FileVectorStore.kt 头部说明）。 */
    val store: VectorStore,
    val index: KnowledgeIndex,
    val spaceId: String,
    val dim: Int,
    /** 这个句柄用的是哪个库（自检会临时换隔离库，界面必须能区分） */
    val dbName: String,
    /** 只有 RDB 实现有事务诊断；文件型没有事务，恒为空串。 */
    val lastTx: String = "",
    /** 当前用的是哪种存储（自检/设置页要如实显示：`rdb` 还是 `file`）。 */
    val backend: String = "rdb",
) {
    fun retriever(minScore: Double) = Retriever(embed, store, minScore = minScore)
}

/**
 * 一个**最小**的可重入锁（Kotlin/Native 的 `kotlin.concurrent` 里没有 `synchronized`）。
 *
 * 为什么需要它：模型会话是**进程内单例**（`MsSessionCache`），两个并发的嵌入调用会崩在
 * `mindspore::lite::MallocTensorData`（2026-10-25 实测，见 `Index.ets` 里那段崩溃栈注释）。
 * H8 里"聊天页检索来源"与"文档页导入"可能几乎同时发生，所以串行化由端口自己保证。
 *
 * 语义：**可重入**（`open()` 等入口会组合调用同文件的其它入口，不可重入就会自锁）。
 * 这里没有真正的并行——所有 NAPI 调用都在 ArkTS 线程上同步发生——所以"重入即放行"是安全的；
 * 它防的是"将来某入口挪到后台线程"这类回归（那时这里要换成真正的互斥 + 队列）。
 */
private class NativeLock {
    private var busy: Boolean = false

    /**
     * `inline` 是**必须**的：下面各入口在块里用 `return` 提前返回，
     * 普通 lambda 里那会变成非局部返回（编译错误）。
     */
    inline fun <T> withLock(block: () -> T): T {
        // 单线程模型下的"重入即放行"：不用线程 id 也能成立，且不引入平台依赖。
        if (busy) return block()
        busy = true
        try {
            return block()
        } finally {
            busy = false
        }
    }
}

object SekbApp {

    private var session: LocalDocSession? = null
    private var openError: String = ""
    private var openMs: Long = -1
    private var lastIngestNote: String = ""

    /** 模型路径 / vocab / databaseDir：第一次 `open()` 时记住，供后续 reopen 用。 */
    private var modelPath: String = ""
    private var vocabText: String = ""
    private var databaseDir: String = ""

    private val lock = NativeLock()

    /** 本机文档库的固定名（**不是**自检那种每轮唯一的库，界面的库必须跨重启累积）。 */
    private const val DOC_DB = "sekb_docs.db"

    /** 当前句柄用的库名（自检可临时切到隔离库，见 [open] 的 dbName 参数）。 */
    private var activeDb: String = DOC_DB

    /** 检索阈值：与 Android `Retriever` 默认一致（0.40 是标定出来的，见 `Retriever` 类注释）。 */
    var minScore: Double = DEFAULT_MIN_SCORE
        private set

    const val DEFAULT_MIN_SCORE: Double = 0.40

    fun setMinScore(v: Double) {
        minScore = if (v.isNaN() || v <= 0.0 || v > 1.0) DEFAULT_MIN_SCORE else v
    }

    /**
     * 就绪（幂等）。**失败不抛**：把原因写进 `openError` 并在返回里带出来。
     *
     * @return `{"ok":true|false,"reason":"...","space":"...","dim":N,"millis":N,"db":"sekb_docs.db"}`
     */
    fun open(
        mp: String,
        vocab: String,
        dbDir: String,
        dbName: String = DOC_DB,
        /**
         * 应用私有 `filesDir`（ArkTS 侧 `ctx.filesDir`）。
         *
         * **为什么需要它**：鸿蒙 RDB 的 NDK C 路径被实测证明**读一致性不可靠**
         * （同一进程同一句柄相邻调用互相矛盾；事务/游标/列名/多句柄/聚合都已逐一排除），
         * 而**文件 I/O 已验证可靠**（聊天历史就是文件+rename，跨重装能恢复）。
         * 向量索引是可重建的派生数据，不需要数据库能力 → 默认改用文件型存储。
         * 非空时走 `FileVectorStore`；为空时退回 RDB（保留旧路径，便于对照/回归）。
         */
        filesDir: String = "",
    ): String {
        // ⚠️ **空库名必须归一成默认库**（2026-10-26 主线定位的真缺陷）：
        //    ArkTS 侧 `openRag()` 传的是 `''`（"用默认库"的约定），而这里原来直接用空串当 `storeName`
        //    → `OH_Rdb_GetOrOpen(storeName="")` 建出一个**没有名字的库**，写入落在 A 处、读取查 B 处，
        //    现象就是"ingest 报 ok/chunks≥1、文档登记成功，但列表显示 0 段、检索 0 条"。
        //    归一后 `activeDb` 与 `dbName` 都是真名，幂等判断（`activeDb == dbName`）也才成立。
        val wantDb = dbName.ifBlank { DOC_DB }
        return lock.withLock {
            modelPath = mp
            vocabText = vocab
            databaseDir = dbDir
            // 同一个库已经打开 → 直接返回（幂等）
            if (session != null && activeDb == wantDb) return okJson()
            // 换库（自检用隔离库）→ 先关掉旧句柄，避免两个句柄同时持锁
            if (session != null) {
                // 只有 RDB 实现需要显式关句柄；文件型没有句柄（数据已落盘）。
                runCatching {
                    val st = session?.store
                    if (st is RdbVectorStore) st.close()
                }
                session = null
            }
            activeDb = wantDb
            val t0 = com.sekb.shared.core.nowMillis()
            try {
                val emb = MindSporeBgeEmbedding(mp, vocab)
                emb.open()
                // 默认走**文件型**（理由见 `open` 的 `filesDir` 参数注释）；没有 filesDir 时退回 RDB。
                val useFile = filesDir.isNotEmpty()
                if (useFile) {
                    val vs = FileVectorStore("$filesDir/sekb_docs.index", emb.space)
                    session = LocalDocSession(
                        embed = emb, store = vs, index = KnowledgeIndex(emb, vs, registry = vs),
                        spaceId = emb.space.id, dim = emb.space.dim, dbName = "sekb_docs.index",
                        lastTx = "", backend = "file",
                    )
                } else {
                    // **每个库只开一个句柄**：这里开的这个交给 RdbVectorStore 注入使用
                    // （见 RdbVectorStore 构造函数的注释——多句柄会互相看不到写入，是自伤级缺陷）。
                    val (handle, herr) = RdbStore.open(dbDir, wantDb)
                    if (handle == null) error("打开 RDB 失败：errCode=$herr dir=$dbDir name=$wantDb")
                    val store = RdbVectorStore(dbDir, emb.space, wantDb, injected = handle)
                    session = LocalDocSession(
                        embed = emb, store = store, index = KnowledgeIndex(emb, store, registry = store),
                        spaceId = emb.space.id, dim = emb.space.dim, dbName = wantDb,
                        lastTx = store.lastTx, backend = "rdb",
                    )
                }
                openMs = com.sekb.shared.core.nowMillis() - t0
                openError = ""
                return okJson()
            } catch (e: Throwable) {
                openError = "${e::class.simpleName}: ${e.message}"
                session = null
                openMs = com.sekb.shared.core.nowMillis() - t0
                return errJson("open", openError)
            }
        }
    }

    /** 未就绪时的**完整状态**（设置页要显示"未持久化 + 为什么"）。 */
    fun status(): String {
        return lock.withLock {
            if (session == null) return errJson("status", openError.ifEmpty { "尚未初始化（未调用 open）" })
            return okJson()
        }
    }

    /** 文档列表：`{"ok":true,"docs":[{"id","name","chunks","bytes"}],"chunks":N,"space":"..","dim":N}` */
    fun documents(): String {
        return lock.withLock {
            val s = session ?: return errJson("documents", openError.ifEmpty { "尚未初始化" })
            return try {
                val docs = s.index.documents()
                val o = JsonObject()
                o.put("ok", true)
                o.put("space", s.spaceId)
                o.put("dim", s.dim)
                o.put("db", s.dbName)
                o.put("chunks", s.store.size())
                val arr = com.sekb.shared.core.JsonArray()
                for (d in docs) {
                    val e = JsonObject()
                    e.put("id", d.id)
                    e.put("name", d.name)
                    e.put("chunks", d.chunks)
                    e.put("bytes", d.sizeBytes)
                    e.put("addedAt", d.addedAtMillis)
                    e.put("deviceOnly", d.deviceOnly)
                    arr.put(e)
                }
                o.put("docs", arr)
                o.toString()
            } catch (e: Throwable) {
                errJson("documents", "${e::class.simpleName}: ${e.message}")
            }
        }
    }

    /**
     * 导入一段文本（文档页的"导入"）。
     *
     * @param sourceId 文档 id（用文件名，与 Android 一致：同名重复导入视为更新）
     * @return `{"ok":true,"chunks":N,"zeroFilled":N,"millis":N,"note":"..."}`
     */
    fun ingest(sourceId: String, name: String, text: String, sizeBytes: Long): String {
        return lock.withLock {
            val s = session ?: return errJson("ingest", openError.ifEmpty { "尚未初始化" })
            if (sourceId.isBlank()) return errJson("ingest", "文档 id 为空")
            if (text.isBlank()) return errJson("ingest", "文档内容为空（未入库）")
            return try {
                val t0 = com.sekb.shared.core.nowMillis()
                val rep = s.index.ingest(sourceId, text, name = name.ifEmpty { sourceId },
                    sizeBytes = if (sizeBytes > 0) sizeBytes else text.length.toLong(), deviceOnly = true)
                val ms = com.sekb.shared.core.nowMillis() - t0
                lastIngestNote = "已导入「${name.ifEmpty { sourceId }}」：${rep.chunks} 段，嵌入耗时 ${ms}ms" +
                    if (rep.zeroFilled > 0) "（⚠️ zeroFilled=${rep.zeroFilled}：嵌入少返回，已补零向量）" else ""
                val o = JsonObject()
                o.put("ok", true)
                o.put("chunks", rep.chunks)
                o.put("zeroFilled", rep.zeroFilled)
                o.put("millis", ms)
                o.put("note", lastIngestNote)
                o.put("space", rep.space)
                // ⚠️ **写完立刻回读一次**（2026-10-26 主线加）：自检出现过
                //    `ingest 报 chunks=1 / ok=true` 但"列表 0 段、检索 0 条"的矛盾证据，
                //    而 Kotlin/Native 侧没有 hilog，只能靠返回值把事实带出去。
                //    `dbChunks` = 提交后 `size()` 的实测条数：
                //      · dbChunks=0 → **写入/可见性**问题（事务未提交或句柄不同源）
                //      · dbChunks≥1 → 写入没问题，问题在读取/检索那一侧
                //    这一条把"写错还是读错"从推测变成可读的数字，别再靠猜。
                // ⚠️ 失败时**必须带上原因**（2026-10-26 主线补）：第一版只把异常吞成 -1，
                //    结果只能知道"读不到"，不知道"为什么读不到"，白绕一轮。
                // ⚠️ **写后可见性有延迟**（2026-10-26 主线实测并据此改法）：
                //    同一次调用里出现过 `size()`=0 而紧接着 `all()`=1 —— 两个读法对同一张表给出不同答案，
                //    唯一自洽的解释是**写入在提交后还需要一点时间才对查询可见**。
                //    所以这里改成**有界轮询**：最多 4 次、每次间隔 40ms，直到读回条数 ≥ 本次写入的切片数
                //    （`rep.chunks`）；读到就停，读不到也如实返回（附 `pollRounds` 说明轮了几次）。
                //    这不是"把失败掩盖成成功"：期望值是**本次应当写入的条数**，达不到仍会带出真实数字。
                var dbChunks = -1
                var rounds = 0
                try {
                    for (k in 1..4) {
                        rounds = k
                        dbChunks = s.store.size()
                        if (dbChunks >= rep.chunks && dbChunks > 0) break
                        if (k < 4) platform.posix.usleep(40_000u)
                    }
                } catch (t: Throwable) {
                    dbChunks = -1
                    o.put("sizeErr", "${t::class.simpleName}: ${t.message}")
                }
                o.put("dbChunks", dbChunks)
                o.put("pollRounds", rounds)
                var allRows = -1
                try { allRows = s.store.all().size } catch (t: Throwable) {
                    allRows = -1
                    o.put("allErr", "${t::class.simpleName}: ${t.message}")
                }
                o.put("allRows", allRows)
                o.put("tx", s.lastTx)   // 事务诊断（文件型后端恒为空串）
                o.put("backend", s.backend)
                // ⚠️ **文档登记也要回读**（2026-10-26 主线加）：切片表读回来对了，
                //    但 `documents()` 仍报"0 篇"（自检 rag_ingest 因此红过）——同一类"写后读不到"，
                //    只是发生在登记表上。同样有界轮询，并把实测篇数/切片数带出去。
                var docsCount = -1
                var docsChunks = -1
                var docRounds = 0
                try {
                    for (k in 1..4) {
                        docRounds = k
                        val dl = s.index.documents()
                        docsCount = dl.size
                        docsChunks = dl.sumOf { it.chunks }
                        val mine = dl.firstOrNull { it.id == sourceId }
                        if (mine != null && mine.chunks >= rep.chunks) break
                        if (k < 4) platform.posix.usleep(40_000u)
                    }
                } catch (_: Throwable) { }
                o.put("docsCount", docsCount)
                o.put("docsChunks", docsChunks)
                o.put("docRounds", docRounds)
                o.toString()
            } catch (e: Throwable) {
                errJson("ingest", "${e::class.simpleName}: ${e.message}")
            }
        }
    }

    /** 单篇删除（**不误清全库**：走 `deleteBySource`，返回删除的切片数）。 */
    fun remove(id: String): String {
        return lock.withLock {
            val s = session ?: return errJson("remove", openError.ifEmpty { "尚未初始化" })
            return try {
                val removed = s.index.remove(id)
                val o = JsonObject()
                o.put("ok", true)
                o.put("removed", removed)
                o.put("remaining", s.store.size())
                o.put("sources", s.index.documents().size)
                o.put("note", "已删除「${id}」的 ${removed} 段索引，剩余 ${s.store.size()} 段")
                o.toString()
            } catch (e: Throwable) {
                errJson("remove", "${e::class.simpleName}: ${e.message}")
            }
        }
    }

    /**
     * 检索（聊天页的"检索来源"）。
     *
     * `deviceOnly=true` 恒成立：本机导入的文档一律按设备专属处理（RFC §18.3），
     * 所以这条路径同时也在验"隐私闸门真的挂在 UI 走的这条路上"。
     *
     * @return `{"ok":true,"searchable":true,"hits":[{"source","score","snippet"}],"reason":".."}`
     */
    fun retrieve(query: String, topK: Int): String {
        return lock.withLock {
            val s = session ?: return errJson("retrieve", openError.ifEmpty { "尚未初始化" })
            return try {
                val out = s.retriever(minScore).retrieve(query, topK = topK, deviceOnly = true)
                val o = JsonObject()
                o.put("ok", true)
                o.put("searchable", out.searchable)
                o.put("reason", out.reason)
                o.put("space", out.space.id)
                o.put("cloudCompatible", out.cloudCompatible)
                o.put("embedMillis", out.embedMillis)
                o.put("searchMillis", out.searchMillis)
                o.put("threshold", minScore)
                // ⚠️ **查询侧嵌入的直接取证**（2026-10-26 主线加，用来终结"分数在 0.696/0.214/0.000 间摆"的猜测）：
                //    同一个查询**嵌入两次**，报出「是否全零 / 模长 / 两次是否一致 / 前三个分量」。
                //    判据：`qZero=true` ⇒ 嵌入返回了零向量（分数必然 0）；
                //          `qSelfCos≈1` ⇒ 嵌入是确定性的（摆动就出在打分/阈值，而不是嵌入）。
                run {
                    val q1 = runCatching { s.embed.embedOne(query) }.getOrNull()
                    val q2 = runCatching { s.embed.embedOne(query) }.getOrNull()
                    fun norm(v: FloatArray?): Double {
                        if (v == null) return -1.0
                        var a = 0.0
                        for (x in v) a += x.toDouble() * x.toDouble()
                        return kotlin.math.sqrt(a)
                    }
                    fun selfCos(a: FloatArray?, b: FloatArray?): Double {
                        if (a == null || b == null) return -2.0
                        var d = 0.0
                        var na = 0.0
                        var nb = 0.0
                        for (i in a.indices) {
                            d += a[i].toDouble() * b[i].toDouble()
                            na += a[i].toDouble() * a[i].toDouble()
                            nb += b[i].toDouble() * b[i].toDouble()
                        }
                        val den = kotlin.math.sqrt(na) * kotlin.math.sqrt(nb)
                        return if (den == 0.0) 0.0 else d / den
                    }
                    o.put("qDim", q1?.size ?: -1)
                    o.put("qNorm", Fmt.fixed(norm(q1), 6))
                    o.put("qZero", q1 != null && norm(q1) < 1e-9)
                    o.put("qSelfCos", Fmt.fixed(selfCos(q1, q2), 6))
                    o.put("qHead", q1?.take(3)?.joinToString(",") { Fmt.fixed(it.toDouble(), 6) } ?: "-")
                }
                // ⚠️ **命中行的入库向量也要看**（2026-10-26 主线加）：查询向量已确认正常
                //    （norm=1、两次自洽=1），所以"分数低"只可能出在**库里存的向量**上。
                //    报出首条命中那行的 dim/模长/头三个分量，以及**库里全部行的 id+模长**。
                run {
                    val rows = runCatching { s.store.all() }.getOrElse { emptyList() }
                    fun nrm(v: FloatArray): Double {
                        var a = 0.0
                        for (x in v) a += x.toDouble() * x.toDouble()
                        return kotlin.math.sqrt(a)
                    }
                    o.put("storedN", rows.size)
                    // **与查询向量的真实余弦**（不经 topK/阈值）：直接判定"存的向量到底像不像"。
                    // 有了它才能分清"检索打分坏了"与"语义确实不相似（分数天然低）"。
                    run {
                        val q = runCatching { s.embed.embedOne(query) }.getOrNull()
                        if (q != null) {
                            var best = -2.0
                            var bestId = "-"
                            for (r in rows) {
                                if (r.vector.size != q.size) continue
                                var d = 0.0
                                var nq = 0.0
                                var nr = 0.0
                                for (i in q.indices) {
                                    d += q[i].toDouble() * r.vector[i].toDouble()
                                    nq += q[i].toDouble() * q[i].toDouble()
                                    nr += r.vector[i].toDouble() * r.vector[i].toDouble()
                                }
                                val den = kotlin.math.sqrt(nq) * kotlin.math.sqrt(nr)
                                val c = if (den == 0.0) 0.0 else d / den
                                if (c > best) { best = c; bestId = r.id }
                            }
                            o.put("directTopCos", Fmt.fixed(best, 6))
                            o.put("directTopId", bestId)
                        }
                    }
                    o.put(
                        "stored",
                        rows.take(4).joinToString(" | ") {
                            "${it.id}:dim=${it.vector.size},norm=${Fmt.fixed(nrm(it.vector), 4)}" +
                                ",head=${it.vector.take(2).joinToString(",") { x -> Fmt.fixed(x.toDouble(), 4) }}"
                        },
                    )
                }
                // 文档登记内容（id 列表 + 每篇切片数）：rag_ingest 里"篇数/片段数"随轮次变，必须看到实际值
                run {
                    val dl = runCatching { s.index.documents() }.getOrElse { emptyList() }
                    o.put("docIds", dl.joinToString(",") { "${it.id}:${it.chunks}" })
                }
                val arr = com.sekb.shared.core.JsonArray()
                for (h in out.hits) {
                    val e = JsonObject()
                    e.put("source", h.sourceId)
                    // ⚠️ **带上命中的 id 与向量维度**（2026-10-26 主线加）：`source` 为空时，
                    //    只靠它分不清是"命中了错误/遗留的行"还是"向量是零向量"。
                    //    `id` 形如 `<sourceId>#<片号>`，一眼能看出命中的是哪篇文档的哪一片。
                    e.put("id", h.id)
                    e.put("dim", h.id.let { st -> s.store.all().firstOrNull { it.id == h.id }?.vector?.size ?: -1 })
                    e.put("score", h.score)
                    e.put("snippet", h.text.take(160))
                    arr.put(e)
                }
                o.put("hits", arr)
                o.toString()
            } catch (e: Throwable) {
                errJson("retrieve", "${e::class.simpleName}: ${e.message}")
            }
        }
    }

    /** 端侧嵌入状态（设置页"模型/空间戳/维度"）。不加载模型时也能给出配置事实。 */
    fun embedStatus(): String {
        return lock.withLock {
            val o = JsonObject()
            val s = session
            o.put("ok", s != null)
            o.put("modelPath", modelPath)
            o.put("modelFile", modelPath.substringAfterLast('/'))
            o.put("space", s?.spaceId ?: EmbeddingSpace.SERVER_SPACE_ID)
            o.put("dim", s?.dim ?: 512)
            o.put("onDevice", true)
            o.put("openMillis", openMs)
            o.put("diagnostics", s?.embed?.diagnostics ?: openError.ifEmpty { "未打开" })
            o.toString()
        }
    }

    /** 一段可复制的诊断（设置页/自检表用；**只回一行**，界面不放长文）。 */
    fun summaryLine(): String {
        return lock.withLock {
            val s = session ?: return@withLock "文档库未就绪：${openError.ifEmpty { "尚未初始化" }}"
            val d = s.index.documents()
            "文档库 ${DOC_DB}：${d.size} 篇 / ${s.store.size()} 段 空间=${s.spaceId} 阈值=${Fmt.fixed(minScore, 2)}"
        }
    }

    // ── 内部 ──

    private fun okJson(): String {
        val s = session
        val o = JsonObject()
        o.put("ok", s != null)
        o.put("space", s?.spaceId ?: "")
        o.put("dim", s?.dim ?: 0)
        o.put("millis", openMs)
        o.put("db", activeDb)
        o.put("reason", if (s == null) openError.ifEmpty { "尚未初始化" } else "")
        return o.toString()
    }

    private fun errJson(op: String, reason: String): String {
        val o = JsonObject()
        o.put("ok", false)
        o.put("op", op)
        o.put("reason", reason)
        return o.toString()
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// NAPI 入口（`@CName` 顶层函数；`sekb_chat_napi.cpp` 按这些 C 符号取用）
// ─────────────────────────────────────────────────────────────────────────────

/** 打开文档库（模型 + RDB 索引）。**失败不抛**，返回 `{"ok":false,"reason":".."}`。 */
@CName("sekb_rag_open")
fun sekbRagOpen(
    modelPath: String,
    vocabText: String,
    dbDir: String,
    dbName: String,
    filesDir: String,
): String = SekbApp.open(modelPath, vocabText, dbDir, dbName.ifEmpty { "sekb_docs.db" }, filesDir)

/** 当前状态（设置页显示"已持久化 / 未持久化 + 原因"）。 */
@CName("sekb_rag_status")
fun sekbRagStatus(): String = SekbApp.status()

/** 文档列表（名称/片段数/体积）。 */
@CName("sekb_rag_documents")
fun sekbRagDocuments(): String = SekbApp.documents()

/** 导入一段文本。`sizeBytes` 走字符串（NAPI 侧统一按字符串取参，避免类型分支）。 */
@CName("sekb_rag_ingest")
fun sekbRagIngest(sourceId: String, name: String, text: String, sizeBytes: String): String =
    SekbApp.ingest(sourceId, name, text, sizeBytes.toLongOrNull() ?: -1L)

/** 单篇删除（**不误清全库**）。 */
@CName("sekb_rag_remove")
fun sekbRagRemove(id: String): String = SekbApp.remove(id)

/** 检索（聊天页的"检索来源"；deviceOnly 恒为 true）。 */
@CName("sekb_rag_retrieve")
fun sekbRagRetrieve(query: String, topK: String): String =
    SekbApp.retrieve(query, topK.toIntOrNull() ?: 3)

/** 端侧嵌入状态（模型/空间戳/维度）。 */
@CName("sekb_rag_embed_status")
fun sekbRagEmbedStatus(): String = SekbApp.embedStatus()

/** 一行摘要（自检表/设置页）。 */
@CName("sekb_rag_summary")
fun sekbRagSummary(): String = SekbApp.summaryLine()

/** 设置检索阈值（0<v≤1，非法值回落 0.40）。 */
@CName("sekb_rag_set_threshold")
fun sekbRagSetThreshold(v: String): String {
    SekbApp.setMinScore(v.toDoubleOrNull() ?: Double.NaN)
    return SekbApp.minScore.toString()
}
