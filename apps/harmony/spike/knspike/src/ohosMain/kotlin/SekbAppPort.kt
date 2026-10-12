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
    val store: RdbVectorStore,
    val index: KnowledgeIndex,
    val spaceId: String,
    val dim: Int,
    /** 这个句柄用的是哪个库（自检会临时换隔离库，界面必须能区分） */
    val dbName: String,
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
    fun open(mp: String, vocab: String, dbDir: String, dbName: String = DOC_DB): String {
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
                runCatching { session?.store?.close() }
                session = null
            }
            activeDb = wantDb
            val t0 = com.sekb.shared.core.nowMillis()
            try {
                val emb = MindSporeBgeEmbedding(mp, vocab)
                emb.open()
                val store = RdbVectorStore(dbDir, emb.space, wantDb)
                session = LocalDocSession(
                    embed = emb, store = store, index = KnowledgeIndex(emb, store, registry = store),
                    spaceId = emb.space.id, dim = emb.space.dim, dbName = wantDb,
                )
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
                val arr = com.sekb.shared.core.JsonArray()
                for (h in out.hits) {
                    val e = JsonObject()
                    e.put("source", h.sourceId)
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
fun sekbRagOpen(modelPath: String, vocabText: String, dbDir: String, dbName: String): String =
    SekbApp.open(modelPath, vocabText, dbDir, dbName.ifEmpty { "sekb_docs.db" })

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
