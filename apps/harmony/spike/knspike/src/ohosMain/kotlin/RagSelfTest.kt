// H5 · 端侧 RAG 自检（**走共享层**，与 Android `SelfTest.kt` 的 rag_* 项同名同口径）
//
// ## 为什么是一次"整链路"自检而不是几个单点断言
//
// RAG 这条链路有四段（导入 → 分块 → 嵌入 → 入库 → 检索 → 删除），任一段坏了，
// 现象都长一个样：**检索不给东西**或**给错东西**。Android 那边踩过的两个具体教训：
//   · 早期 `KnowledgeIndex.remove()` 用"清空整库"糊过去（接口没有 `deleteBySource`），
//     删一篇文档会把整个索引清掉——**其他文档的文本是设备侧唯一副本，等于数据丢失**；
//   · 嵌入端口返回短数组会让 `ingest` 越界崩溃（iOS 实测 `EXC_BREAKPOINT`）。
// 所以下面每一条判据都对应一个**已经真实发生过**的缺陷，不是"顺手多测几个"。
//
// ## 判据与"为什么不是假绿"
//
// | 项 | 判据 | 反向验证 |
// |---|---|---|
// | `rag_ingest` | 每篇块数 ≥1 且 `zeroFilled == 0` | `zeroFilled>0` 会让它红（嵌入少返回） |
// | `rag_retrieve` | 首条命中 `doc-rag`（分 ~0.63 量级） | 分数/命中任一不符即红 |
// | `rag_delete` | 删 `doc-weather` 后它**不再可检索**，且其余仍在 | 删错（清库）→ 其余数量不符即红 |
// | `rag_delete_keeps_others` | 删除后 `size == 删前 - 该文档块数` | 见上 |
// | `rag_threshold_refuses` | 库里没有的问题在 0.5 阈值下**必须 0 命中** | 阈值失效 → 命中即红（幻觉源） |
// | `rag_device_only_gate` | `isOnDevice=false` 的嵌入**不许**碰设备专属数据 | 去掉闸门 → `searchable=true` 即红 |
// | `rag_dim_mismatch_refused` | 错维度 upsert **必须抛**且消息含"维度不符" | 不抛即红 |
// | `rag_persist_across_reopen` | 关闭重开（新句柄）后条数与首条仍在 | 没落盘 → 重开为 0 即红 |
//
// 其中 `rag_delete` + `rag_delete_keeps_others` 是**反向验证**的正式形态：
// "删掉的东西确实检索不到"这条判据如果永远绿，就说明它根本没在断言。
@file:OptIn(kotlinx.cinterop.ExperimentalForeignApi::class, kotlin.experimental.ExperimentalNativeApi::class)

package com.sekb.ohos.spike

import com.sekb.shared.core.Fmt
import com.sekb.shared.embed.EmbeddingProvider
import com.sekb.shared.embed.EmbeddingSpace
import com.sekb.shared.eval.RetrievalEvalSet
import com.sekb.shared.rag.KnowledgeIndex
import com.sekb.shared.rag.Retriever
import com.sekb.shared.rag.VectorRecord

/** 报告（与 `EmbedReport`/`RdbReport` 同形状：首行 `RAG PASS|FAIL n/N`）。 */
/**
 * 让出主线程的一小段时间（给 ArkTS 侧在重活之间调用）。
 *
 * **为什么必须有它**（实测）：
 * RAG 自检在设备上是**纯同步**的（NAPI 调用 → Kotlin 一路跑完），
 * 12 篇 ingest + 33 问 ×5 档评测 ≈ 90s 的**连续同步**工作会撞上 OHOS 的
 * `THREAD_BLOCK_6S` 看门狗（`/data/log/faultlog/faultlogger/appfreeze-*.log`），
 * 表现是**整段结果都拿不到**（进程被看门狗处理掉）。
 * 所以 `RagSelfTest.kt` 暴露"分步"入口，ArkTS 在每一步之间 `await` 一次微任务，
 * 让主线程有喘息机会。判据与逻辑**都在 Kotlin 侧**，ArkTS 只负责"什么时候继续"。
 */
@CName("sekb_spike_rag_step")
fun sekbSpikeRagStep(
    step: Int,
    modelPath: String,
    vocabText: String,
    databaseDir: String,
    filesDir: String,
): String = RagSession.step(step, modelPath, vocabText, databaseDir, filesDir)

/** 分步自检的会话状态（进程内单例：模型会话本来就只该有一个，见 MsSessionCache）。 */
object RagSession {
    private var acc: RagAcc? = null

    fun reset() {
        acc = null
    }

    fun step(
        step: Int,
        modelPath: String,
        vocabText: String,
        databaseDir: String,
        filesDir: String = "",
    ): String {
        val a = acc ?: RagAcc(modelPath, vocabText, databaseDir, filesDir).also { acc = it }
        return a.run(step)
    }
}

/**
 * 按"有没有 filesDir"选存储实现：**非空走文件型**，空则退回 RDB。
 *
 * 提成顶层函数是因为**两个入口都要用**：分步版（`RagAcc`）与整段版（`sekbSpikeRagSelfTest`）。
 * 之前这里是 `RagAcc` 的私有方法，于是整段版里那两处构造仍然硬编码 `RdbVectorStore`——
 * 换实现时漏改就是"测的不是新实现"的隐蔽假失败（本轮真踩过）。
 */
private fun newStoreFor(
    filesDir: String,
    databaseDir: String,
    name: String,
    space: com.sekb.shared.embed.EmbeddingSpace,
): com.sekb.shared.rag.VectorAndDocumentStore =
    if (filesDir.isNotEmpty()) {
        // ⚠️ **`name` 可能是绝对路径**（`dbNamePublic` 返回的就是文件全路径，见
        //    `FileVectorStore.dbNamePublic`）。无条件拼 `"$filesDir/$name"` 会得到
        //    `…/files//data/storage/…/files/x.db` 这种**双前缀路径**——文件"存在= true"
        //    （在真正的路径上），而重开时读到的是那个不存在的双前缀路径 → size=0 的假"没落盘"。
        //    本轮实测就是这个（`重开diag=新建（文件不存在）…/files//data/…`）。判据：以 `/` 开头就当全路径。
        val path = if (name.startsWith("/")) name else "$filesDir/$name"
        FileVectorStore(path, space)
    } else RdbVectorStore(databaseDir, space, name)

private class RagAcc(
    private val modelPath: String,
    private val vocabText: String,
    private val databaseDir: String,
    /**
     * 应用私有 filesDir。非空时**整条评估链路走 `FileVectorStore`**
     * （RDB 的 NDK C 路径读一致性不可靠，见 FileVectorStore.kt 头部；2026-10-26 主线）。
     */
    private val filesDir: String = "",
) {
    /** 按 filesDir 是否给出，构造对应的存储实现（端口可互换，调用处无分支）。 */
    /** 关掉一个存储实现（文件型无句柄，`close()` 是空操作；见 FileVectorStore）。 */
    private fun closeStore(s: com.sekb.shared.rag.VectorAndDocumentStore) {
        if (s is RdbVectorStore) s.close()
    }

    private fun newStore(name: String, space: com.sekb.shared.embed.EmbeddingSpace):
        com.sekb.shared.rag.VectorAndDocumentStore = newStoreFor(filesDir, databaseDir, name, space)
    private val r = RagReport()
    private var emb: MindSporeBgeEmbedding? = null
    // 类型用**端口接口**（文件型与 RDB 可互换）；`close`/`dbNamePublic`/`openDiagnostics`
    // 两个实现都提供了，所以调用处不需要分支（见 FileVectorStore.kt）。
    private var store: com.sekb.shared.rag.VectorAndDocumentStore? = null
    private var reopened: com.sekb.shared.rag.VectorAndDocumentStore? = null
    private var index: KnowledgeIndex? = null
    private var reports: List<KnowledgeIndex.IngestReport> = emptyList()
    private var sizeBefore = 0
    private var done = false

    /** 已完成的步数（ArkTS 用它决定还要不要继续调）。 */
    var stepCount = 0
        private set

    /** 每一步都返回**当前累计报告**；ArkTS 只在最后一步把结果打 hilog。 */
    fun run(step: Int): String {
        if (done) return r.render()
        when (step) {
            0 -> stepOpenAndIngest()
            1 -> stepRetrieveAndGates()
            2 -> stepPersistAndDelete()
            3 -> stepEval()
            else -> { done = true; emb?.close() }
        }
        stepCount++
        return r.render()
    }

    private fun stepOpenAndIngest() {
        val e = MindSporeBgeEmbedding(modelPath, vocabText)
        try {
            e.open()
        } catch (t: Throwable) {
            r.check("rag_embed_open", false, "打开模型失败：${t::class.simpleName}: ${t.message}")
            done = true
            return
        }
        r.check("rag_embed_open", true, e.diagnostics)
        emb = e
        val t0 = com.sekb.shared.core.nowMillis()
        val dbName = "sekb_rg_${t0 % 1000000}.db"
        r.note("自检库=$dbName（每轮唯一；后缀末 6 位=库名过长会 errCode=14800037）")
        RdbStore.delete(databaseDir, dbName)
        val st = try {
            newStore(dbName, e.space)
        } catch (t: Throwable) {
            r.check("rag_store_open", false, "打开 RDB 索引失败：${t::class.simpleName}: ${t.message}")
            done = true
            return
        }
        r.check("rag_store_open", true, st.openDiagnostics)
        store = st
        val ix = KnowledgeIndex(e, st, registry = st)
        index = ix
        val docs = listOf(
            "doc-diet" to "端侧推理省电的原因：计算留在本机，没有网络传输，也没有云端排队等待。",
            "doc-weather" to "北京今天多云转晴，最高气温 26 度，适合骑行。",
            "doc-rag" to "端侧 RAG 把知识索引放在设备上，检索不出网，因此隐私更好、延迟更低。",
        )
        reports = docs.map { (id, text) -> ix.ingest(id, text, name = "$id.txt") }
        r.check(
            "rag_ingest",
            reports.all { it.chunks >= 1 } && reports.all { it.zeroFilled == 0 },
            reports.joinToString(" ") { "${it.sourceId}:${it.chunks}块(zeroFilled=${it.zeroFilled})" } +
                " 空间=${e.space.id} 本机=${e.isOnDevice}",
        )
        r.check(
            "rag_registry_list",
            ix.documents().size == docs.size,
            "登记表条数=${ix.documents().size}（期望 ${docs.size}）顺序=${ix.documents().map { it.id }}",
        )
        r.check(
            "rag_stats",
            ix.stats()["chunks"] == reports.sumOf { it.chunks } && ix.stats()["sources"] == docs.size,
            "stats=${ix.stats()}（期望 chunks=${reports.sumOf { it.chunks }} sources=${docs.size}）",
        )
    }

    private fun stepRetrieveAndGates() {
        val e = emb ?: run { done = true; return }
        val st = store ?: run { done = true; return }
        val ix = index ?: run { done = true; return }
        val retriever = Retriever(e, st, minScore = 0.5)
        val hit = retriever.retrieve("端侧 RAG 为什么隐私更好", topK = 3)
        val top = hit.hits.firstOrNull()
        r.check(
            "rag_retrieve",
            top?.sourceId == "doc-rag",
            "命中=${top?.sourceId} 分=${top?.let { s -> Fmt.fixed(s.score, 3) }} " +
                "条数=${hit.hits.size}（Android/iOS 同源首条分 ~0.630 量级）",
        )
        r.check(
            "rag_space_guard",
            hit.cloudCompatible == (e.space.id == EmbeddingSpace.SERVER_SPACE_ID),
            "索引空间=${hit.space.id} 与云端一致=${hit.cloudCompatible}",
        )
        val miss = retriever.retrieve("我上个月的信用卡账单是多少？", topK = 3)
        val rawTop = st.search(e.embedOne("我上个月的信用卡账单是多少？"), 1).firstOrNull()
        r.check(
            "rag_threshold_refuses",
            miss.hits.isEmpty(),
            "库外问题的命中数=${miss.hits.size}（期望 0）reason='${miss.reason}' " +
                "未过滤前最高分=${rawTop?.let { s -> Fmt.fixed(s.score, 3) }}（阈值=${retriever.minScore}）",
        )
        val sameSpaceRemote = object : EmbeddingProvider {
            override val space: EmbeddingSpace = e.space
            override val isOnDevice: Boolean = false
            override fun embed(texts: List<String>): List<FloatArray> = e.embed(texts)
        }
        val denied = Retriever(sameSpaceRemote, st).retrieve("端侧 RAG", deviceOnly = true)
        r.check(
            "rag_device_only_gate",
            !denied.searchable && denied.hits.isEmpty(),
            "deviceOnly=true + 非本机嵌入 → searchable=${denied.searchable} 原因='${denied.reason}'",
        )
        val allowed = Retriever(sameSpaceRemote, st).retrieve("端侧 RAG", deviceOnly = false)
        r.check(
            "rag_device_only_gate_allows_nond_evice",
            allowed.searchable,
            "deviceOnly=false 时 searchable=${allowed.searchable}（构造上不允许「一律拒绝」）",
        )
        val ex = runCatching {
            st.upsert(listOf(com.sekb.shared.rag.VectorRecord("x#0", "x", "t", FloatArray(3))))
        }.exceptionOrNull()
        r.check(
            "rag_dim_mismatch_refused",
            ex is IllegalArgumentException && (ex.message?.contains("维度不符") == true),
            "错维度 upsert 抛=${ex?.let { it::class.simpleName }} 消息='${ex?.message?.take(60)}'",
        )
    }

    private fun stepPersistAndDelete() {
        val st = store ?: run { done = true; return }
        val ix = index ?: run { done = true; return }
        sizeBefore = st.size()
        closeStore(st)
        val re = newStore(st.dbNamePublic, st.space)
        reopened = re
        r.check(
            "rag_persist_across_reopen",
            re.size() == sizeBefore && sizeBefore > 0,
            "关闭前 size=$sizeBefore 重开后 size=${re.size()}（0 说明没落盘）" +
                " 库=${st.dbNamePublic} 文件存在=${platform.posix.access(st.dbNamePublic, 0) == 0}" +
                " 重开diag=${re.openDiagnostics}",
        )
        val reIndex = KnowledgeIndex(emb!!, re, registry = re)
        val doomed = reports.first { it.sourceId == "doc-weather" }.chunks
        val before = re.all().map { it.sourceId }.toSet()
        val removed = reIndex.remove("doc-weather")
        val after = re.all().map { it.sourceId }.toSet()
        r.check("rag_delete", removed == doomed, "删除返回 $removed（期望 $doomed）")
        val expectSources = before.filterNot { it == "doc-weather" }.toSet()
        val sizeNow = re.all().size
        val expectSize = sizeBefore - doomed
        r.check(
            "rag_delete_keeps_others",
            after == expectSources && sizeNow == expectSize,
            "删前 sources=$before 删后 sources=$after size=$sizeNow（期望 $expectSize）",
        )
        val wide = Retriever(emb!!, re, minScore = 0.0)
            .retrieve("北京今天多云转晴 最高气温", topK = 10)
        r.check(
            "rag_delete_not_retrievable",
            wide.hits.none { it.sourceId == "doc-weather" },
            "阈值=0 宽召回 ${wide.hits.size} 条，doc-weather 出现 " +
                "${wide.hits.count { it.sourceId == "doc-weather" }} 次（期望 0）",
        )
        r.check(
            "rag_delete_idempotent",
            reIndex.remove("doc-weather") == 0,
            "再删一次返回 ${reIndex.remove("doc-weather")}（期望 0）",
        )
        r.check(
            "rag_registry_delete",
            re.get("doc-weather") == null && re.list().size == before.size - 1,
            "registry 剩余=${re.list().map { it.id }}",
        )
    }

    private var evalRows: List<Pair<String?, List<Pair<String, Double>>>>? = null
    private var evalChunks = 0
    private var evalSources = 0
    private var buildMs = 0L
    private var evalMs = 0L

    private fun stepEval() {
        val e = emb ?: run { done = true; return }
        if (evalRows == null) {
            val t0 = com.sekb.shared.core.nowMillis()
            val evalDb = "sekb_reval_${t0 % 1000000}.db"
            RdbStore.delete(databaseDir, evalDb)
            val es = newStore(evalDb, e.space)
            val eix = KnowledgeIndex(e, es)
            val errs = mutableListOf<String>()
            val b0 = com.sekb.shared.core.nowMillis()
            for (doc in RetrievalEvalSet.corpus) {
                try {
                    val rep = eix.ingest(doc.id, doc.text)
                    if (rep.chunks < 1 || rep.zeroFilled != 0) {
                        errs += "${doc.id}(chunks=${rep.chunks},zeroFilled=${rep.zeroFilled})"
                    }
                } catch (t: Throwable) {
                    errs += "${doc.id}(${t::class.simpleName})"
                }
            }
            buildMs = com.sekb.shared.core.nowMillis() - b0
            evalChunks = es.size()
            evalSources = es.all().map { it.sourceId }.toSet().size
            r.check(
                "rag_eval_index_built",
                evalChunks == RetrievalEvalSet.corpus.size &&
                    evalSources == RetrievalEvalSet.corpus.size && errs.isEmpty(),
                "评测索引：chunks=$evalChunks sources=$evalSources（期望各 ${RetrievalEvalSet.corpus.size}）" +
                    " 失败=${if (errs.isEmpty()) "无" else errs.joinToString("; ")} 用时=${buildMs}ms",
            )
            val t1 = com.sekb.shared.core.nowMillis()
            val rows = mutableListOf<Pair<String?, List<Pair<String, Double>>>>()
            for (q in RetrievalEvalSet.questions) {
                val out = Retriever(e, es, minScore = 0.0).retrieve(q.q, topK = 3, deviceOnly = true)
                rows += q.expected to out.hits.map { it.sourceId to it.score }
            }
            evalMs = com.sekb.shared.core.nowMillis() - t1
            evalRows = rows
            closeStore(es)
            // ⚠️ 这里原来写 `return`（"下一轮调用再出表"）。分步驱动确实会再调一次，
            //    但**表是在下一次调用的报告里才拼出来的**，而驱动只保留最后一份报告 →
            //    `EVAL-TABLE` 五档表从来没被打出来过（18/18 里就没有它）。
            //    改成**不早退**：只跳过"重建索引"，直接落到下面的出表逻辑。
        }
        // 第二段：出表 + 断言
        val rows = evalRows!!
        val answerable = RetrievalEvalSet.answerable
        val unanswerable = RetrievalEvalSet.unanswerable
        fun table(th: Double): Triple<Int, Int, Double> {
            val ranks = rows.map { (exp, ranked) ->
                if (exp == null) 0 else ranked.filter { it.second >= th }.indexOfFirst { it.first == exp } + 1
            }
            val h1 = rows.indices.count { rows[it].first != null && ranks[it] == 1 }
            val h3 = rows.indices.count { rows[it].first != null && ranks[it] in 1..3 }
            val mrr = rows.indices.filter { rows[it].first != null }
                .map { if (ranks[it] > 0) 1.0 / ranks[it] else 0.0 }
                .average().takeIf { !it.isNaN() } ?: 0.0
            return Triple(h1, h3, mrr)
        }
        fun fp(th: Double): Int = rows.count { (exp, ranked) -> exp == null && ranked.any { it.second >= th } }
        r.note(
            "检索评测：语料 ${RetrievalEvalSet.corpus.size} 篇 建索引=${buildMs}ms " +
                "33 问单趟嵌入=${evalMs}ms（阈值后处理，与共享层 calibrate 数值等价）",
        )
        for (th in listOf(0.2, 0.3, 0.4, 0.5, 0.6)) {
            val (h1, h3, mrr) = table(th)
            r.note(
                "EVAL-TABLE 阈值=${Fmt.fixed(th, 2)} Hit@1=$h1/$answerable Hit@3=$h3/$answerable " +
                    "MRR=${Fmt.fixed(mrr, 3)} 误召回=${fp(th)}/$unanswerable",
            )
        }
        val (a1, a3, am) = table(0.4)
        r.check(
            "rag_eval_threshold_0_40_matches_android",
            a1 == 26 && a3 == 30 && kotlin.math.abs(am - 0.928) < 0.0005 && fp(0.4) == 0,
            "Hit@1=$a1/$answerable Hit@3=$a3/$answerable MRR=${Fmt.fixed(am, 3)} " +
                "误召回=${fp(0.4)}/$unanswerable（Android 基线 26/30、30/30、0.928、0/3）",
        )
        evalRows = null
        done = true
        emb?.close()
        r.note("总用时 ${com.sekb.shared.core.nowMillis() - t0Global}ms")
    }

    private val t0Global = com.sekb.shared.core.nowMillis()
}

private class RagReport {
    val lines = mutableListOf<String>()
    var failed = 0
    var total = 0
    var skipped = 0

    fun check(name: String, ok: Boolean, detail: String) {
        total++
        if (!ok) failed++
        lines += "${if (ok) "PASS" else "FAIL"} ${name} — ${detail}"
    }

    /** 只在"环境限制"下用（与 Android 纪律一致：SKIP 必须带明确理由，且不算失败）。 */
    fun skip(name: String, reason: String) {
        skipped++
        lines += "SKIP ${name} — ${reason}"
    }

    fun note(detail: String) {
        lines += "NOTE $detail"
    }

    fun render(): String = (
        listOf(
            "RAG ${if (failed == 0) "PASS" else "FAIL"} ${total - failed}/${total}" +
                if (skipped > 0) "（另 SKIP ${skipped}）" else "",
        ) + lines
        ).joinToString("\n")
}

/**
 * 设备侧 RAG 自检 + 检索评测（H5 的验收入口）。
 *
 * @param modelPath `.ms` 的真实文件路径
 * @param vocabText `vocab.txt` 的内容
 * @param databaseDir `UIAbilityContext.databaseDir`
 */
@CName("sekb_spike_rag_selftest")
fun sekbSpikeRagSelfTest(modelPath: String, vocabText: String, databaseDir: String): String {
    val r = RagReport()
    val t0 = com.sekb.shared.core.nowMillis()

    // ── 0. 嵌入端口（失败即降级：同数量零向量）─────────────────────────────
    val emb = MindSporeBgeEmbedding(modelPath, vocabText)
    try {
        emb.open()
    } catch (e: Throwable) {
        r.check("rag_embed_open", false, "打开模型失败：${e::class.simpleName}: ${e.message}")
        return r.render()
    }
    r.check("rag_embed_open", true, emb.diagnostics)
    // 自检用的库名带时间戳后缀：每次跑都是**干净索引**，否则"上一轮的文档"会污染命中数
    // 每轮唯一库名（理由见评测段）：跨轮残留的数据会让"块数/来源数"不可解释
    // ⚠️ 库名长度有上限（实测：`sekb_rag_selftest_1791779491000.db` → `errCode=14800037` 打不开）。
    //    所以后缀只取时间戳**末 6 位**（够用：自检一轮几十秒，绝不重复），保持库名短。
    val dbName = "sekb_rag_${t0 % 1000000}.db"
    r.note("自检库=$dbName（每轮唯一，避免跨轮残留污染判据；后缀末 6 位=库名过长会 errCode=14800037）")
    RdbStore.delete(databaseDir, dbName)

    // 需要"进程重启后索引仍在"的证据 → 先关掉，从头打开
    // ⚠️ 整段版（同步跑 ~90s，会撞 THREAD_BLOCK_6S）**没有 filesDir 参数**，
    //    因此这里仍是 RDB；H5 的正式入口是**分步版** `rag_step`（它接收 filesDir 并可走文件型）。
    //    保留整段版是为了"对照/历史命令可重放"。
    var store: com.sekb.shared.rag.VectorAndDocumentStore
    try {
        store = newStoreFor("", databaseDir, dbName, emb.space)
    } catch (e: Throwable) {
        r.check("rag_store_open", false, "打开 RDB 索引失败：${e::class.simpleName}: ${e.message}")
        return r.render()
    }
    r.check("rag_store_open", true, store.openDiagnostics)

    val index = KnowledgeIndex(emb, store, registry = store)
    val retriever = Retriever(emb, store, minScore = 0.5)

    // ── 1. 导入（与 Android 同一批文档 id/文本，便于逐项对照）───────────────
    val docs = listOf(
        "doc-diet" to "端侧推理省电的原因：计算留在本机，没有网络传输，也没有云端排队等待。",
        "doc-weather" to "北京今天多云转晴，最高气温 26 度，适合骑行。",
        "doc-rag" to "端侧 RAG 把知识索引放在设备上，检索不出网，因此隐私更好、延迟更低。",
    )
    val reports = docs.map { (id, text) -> index.ingest(id, text, name = "$id.txt") }
    r.check(
        "rag_ingest",
        reports.all { it.chunks >= 1 } && reports.all { it.zeroFilled == 0 },
        reports.joinToString(" ") { "${it.sourceId}:${it.chunks}块(zeroFilled=${it.zeroFilled})" } +
            " 空间=${emb.space.id} 本机=${emb.isOnDevice}",
    )
    r.check(
        "rag_registry_list",
        index.documents().size == docs.size,
        "登记表条数=${index.documents().size}（期望 ${docs.size}）顺序=${index.documents().map { it.id }}",
    )
    r.check(
        "rag_stats",
        index.stats()["chunks"] == reports.sumOf { it.chunks } &&
            index.stats()["sources"] == docs.size,
        "stats=${index.stats()}（期望 chunks=${reports.sumOf { it.chunks }} sources=${docs.size}）",
    )

    // ── 2. 检索（首条必须是 doc-rag）──────────────────────────────────────
    val hit = retriever.retrieve("端侧 RAG 为什么隐私更好", topK = 3)
    val top = hit.hits.firstOrNull()
    r.check(
        "rag_retrieve",
        top?.sourceId == "doc-rag",
        "命中=${top?.sourceId} 分=${top?.let { s -> Fmt.fixed(s.score, 3) }} " +
            "条数=${hit.hits.size} 嵌入=${hit.embedMillis.toInt()}ms 检索=${hit.searchMillis.toInt()}ms " +
            "（Android/iOS 同源首条分 ~0.630 量级）",
    )
    r.check(
        "rag_space_guard",
        hit.cloudCompatible == (emb.space.id == EmbeddingSpace.SERVER_SPACE_ID),
        "索引空间=${hit.space.id} 与云端一致=${hit.cloudCompatible}" +
            "（期望 ${emb.space.id == EmbeddingSpace.SERVER_SPACE_ID}：只有与云端同空间才允许融合）",
    )

    // ── 3. 阈值闸门：库里没有的问题**必须**被拒绝（不做幻觉）────────────────
    run {
        val miss = retriever.retrieve("我上个月的信用卡账单是多少？", topK = 3)
        val rawTop = store.search(emb.embedOne("我上个月的信用卡账单是多少？"), 1).firstOrNull()
        r.check(
            "rag_threshold_refuses",
            miss.hits.isEmpty(),
            "库外问题的命中数=${miss.hits.size}（期望 0）reason='${miss.reason}' " +
                "未过滤前最高分=${rawTop?.let { Fmt.fixed(it.score, 3) }}（阈值=${retriever.minScore}）",
        )
    }

    // ── 4. 设备专属闸门：**同一空间**但非本机计算的嵌入不许碰设备专属数据 ────
    //    ⚠️ 必须同空间，否则先触发的是"空间不一致"那条规则，就测不到隐私闸门本身
    //    （Android 第一版就是这么写错的，注释留在 SelfTest.kt:293）。
    run {
        val sameSpaceRemote = object : EmbeddingProvider {
            override val space: EmbeddingSpace = emb.space
            override val isOnDevice: Boolean = false
            override fun embed(texts: List<String>): List<FloatArray> = emb.embed(texts)
        }
        val denied = Retriever(sameSpaceRemote, store).retrieve("端侧 RAG", deviceOnly = true)
        r.check(
            "rag_device_only_gate",
            !denied.searchable && denied.hits.isEmpty(),
            "deviceOnly=true + 非本机嵌入 → searchable=${denied.searchable} 命中=${denied.hits.size} " +
                "reason='${denied.reason}'",
        )
        // 非 deviceOnly 时同一条查询应当允许（否则说明闸门被写成了"一律拒绝"）
        val allowed = Retriever(sameSpaceRemote, store).retrieve("端侧 RAG", deviceOnly = false)
        r.check(
            "rag_device_only_gate_allows_nond_evice",
            allowed.searchable,
            "deviceOnly=false 时 searchable=${allowed.searchable}（构造上不允许「一律拒绝」）",
        )
    }

    // ── 5. 错维度必须**大声失败**（Android 断言："维度不符"）──────────────────
    run {
        val e = runCatching {
            store.upsert(listOf(VectorRecord("x#0", "x", "t", FloatArray(3))))
        }.exceptionOrNull()
        r.check(
            "rag_dim_mismatch_refused",
            e is IllegalArgumentException && (e.message?.contains("维度不符") == true),
            "错维度 upsert 抛=${e?.let { it::class.simpleName }} 消息='${e?.message?.take(60)}'",
        )
    }

    // ── 6. 持久化：**关闭 → 重新打开**（新句柄）→ 数据仍在 ──────────────────
    val sizeBefore = store.size()
    if (store is RdbVectorStore) store.close()
    // ⚠️ 必须走 `newStore`：这里原来**硬编码 `RdbVectorStore`**，于是"持久化"这项
    //    在文件型后端下测的其实是 RDB（构造出来的新库当然是空的 → 报"没落盘"的假失败）。
    //    这类"测试自己没跟着实现切换"的缺陷很隐蔽——**换实现时要把所有构造点一起改**。
    val reopened = newStoreFor("", databaseDir, dbName, emb.space)
    r.check(
        "rag_persist_across_reopen",
        reopened.size() == sizeBefore && sizeBefore > 0,
        "关闭前 size=$sizeBefore 重开后 size=${reopened.size()}（0 说明没落盘）" +
            "diag=${reopened.openDiagnostics}",
    )
    val reIndex = KnowledgeIndex(emb, reopened, registry = reopened)
    val reRetriever = Retriever(emb, reopened, minScore = 0.5)

    // ── 7. **反向验证**：删一篇 → 它不再可检索，且其余**不误伤** ─────────────
    run {
        val doomedChunks = reports.first { it.sourceId == "doc-weather" }.chunks
        val beforeSources = reopened.all().map { it.sourceId }.toSet()
        val removed = reIndex.remove("doc-weather")
        val afterSources = reopened.all().map { it.sourceId }.toSet()
        r.check(
            "rag_delete",
            removed == doomedChunks,
            "删除返回 $removed（期望 $doomedChunks = doc-weather 的块数）",
        )
        // 期望值先算成局部变量再比较：一是可读，二是避开 Kotlin 2.x 对
        // `Set.minus` / `List.size` 混在一个 `&&` 里的重载推断歧义（实测会报
        // `Cannot infer type for type parameter 'T'/'R'` 那种离现场很远的错）。
        val expectSources: Set<String> = beforeSources.filterNot { it == "doc-weather" }.toSet()
        val sizeNow: Int = reopened.all().size
        val expectSize: Int = sizeBefore - doomedChunks
        r.check(
            "rag_delete_keeps_others",
            afterSources == expectSources && sizeNow == expectSize,
            "删前 sources=$beforeSources 删后 sources=$afterSources size=$sizeNow（期望 $expectSize）" +
                "｜**少了别的文档 =「清库」那个老缺陷复现**",
        )
        // ① 删掉的文档**不再被检索到**（即使把阈值降到 0，也只在文本层不出现在结果里）
        val wide = Retriever(emb, reopened, minScore = 0.0).retrieve("北京今天多云转晴 最高气温", topK = 10)
        r.check(
            "rag_delete_not_retrievable",
            wide.hits.none { it.sourceId == "doc-weather" },
            "阈值=0 宽召回 ${wide.hits.size} 条，其中 doc-weather 出现 ${wide.hits.count { it.sourceId == "doc-weather" }} 次（期望 0）",
        )
        // ② 删除必须**幂等**（Android 断言：再删返回 0）
        r.check(
            "rag_delete_idempotent",
            reIndex.remove("doc-weather") == 0,
            "再删一次返回 ${reIndex.remove("doc-weather")}（期望 0）",
        )
        // ③ 登记表里也该没了（`KnowledgeIndex.remove` 会同时删 registry）
        r.check(
            "rag_registry_delete",
            reopened.get("doc-weather") == null && reopened.list().size == beforeSources.size - 1,
            "registry 剩余=${reopened.list().map { it.id }}",
        )
    }

    // ── 8. 检索评测同表（H6 的另一半：阈值 0.20/0.30/0.40/0.50/0.60）────────
    //    **复用共享层的评测集与 runner**（不另写），但索引换成 **RDB 持久化实现**——
    //    这正是"端口实现真的能跑评测"的证据（Android 是在内存库上标定的）。
    run {
        // 给评测库一个**每轮唯一**的名字：跨轮残留会让"库里有几块"变得不可解释
        //（本轮就踩到了：上一轮的 doc-weather 块留在库里，导致"3 块 1 个 source"这种自相矛盾的数）
        val evalDb = "sekb_rageval_${t0 % 1000000}.db"
        RdbStore.delete(databaseDir, evalDb)
        val evalStore = newStoreFor("", databaseDir, evalDb, emb.space)
        val evalIndex = KnowledgeIndex(emb, evalStore)
        val buildT0 = com.sekb.shared.core.nowMillis()
        // ⚠️ 逐篇 ingest 并且**逐篇检查**：一条失败不能静默地让后面 11 篇全不进去
        //（`forEach` 里抛异常会让整段中止，而"评测表很难看"这个现象与"只进去几篇"长得一模一样）。
        val ingestErrs = mutableListOf<String>()
        for (doc in RetrievalEvalSet.corpus) {
            try {
                val rep = evalIndex.ingest(doc.id, doc.text)
                if (rep.chunks < 1 || rep.zeroFilled != 0) {
                    ingestErrs += "${doc.id}(chunks=${rep.chunks},zeroFilled=${rep.zeroFilled})"
                }
            } catch (e: Throwable) {
                ingestErrs += "${doc.id}(${e::class.simpleName}: ${e.message?.take(40)})"
            }
        }
        val buildMs = com.sekb.shared.core.nowMillis() - buildT0
        val evalChunks = evalStore.size()
        val evalSources = evalStore.all().map { it.sourceId }.toSet().size
        r.check(
            "rag_eval_index_built",
            evalChunks == RetrievalEvalSet.corpus.size && evalSources == RetrievalEvalSet.corpus.size &&
                ingestErrs.isEmpty(),
            "评测索引：chunks=$evalChunks sources=$evalSources（期望各 ${RetrievalEvalSet.corpus.size}）" +
                " 失败=${if (ingestErrs.isEmpty()) "无" else ingestErrs.joinToString("; ")} 用时=${buildMs}ms",
        )
        // ⚠️ **单趟嵌入 + 阈值后处理**（与共享层 `RetrievalEvalRunner.calibrate` 的数值等价，
        //    但设备上便宜 5 倍）——为什么必须这么做（实测，不是优化癖）：
        //    `calibrate` 对**每个阈值**各跑一遍完整 33 问（内部 `retriever.retrieve` 会
        //    重新嵌入查询），5 档 = 165 次嵌入；设备上单次嵌入 ~0.55s → **≈92s**，
        //    叠加 94MB 模型首拷与 `runAll` 串行，模拟器上直接触发
        //    `THREAD_BLOCK_6S`（appfreeze，日志在 `/data/log/faultlog/faultlogger/appfreeze-*`）。
        //    而**同一查询的嵌入结果与阈值无关**——阈值只影响"用不用这条命中"。
        //    所以：每问嵌入一次、取 topK=3、再对 5 个阈值分别套用同一套判据。
        //    **评测集与指标口径仍全部来自共享层**（`RetrievalEvalSet` + 与
        //    `RetrievalEvalRunner.Report` 逐字段相同的定义），鸿蒙侧没有第二套检索逻辑。
        val evalT0 = com.sekb.shared.core.nowMillis()
        val thresholds = listOf(0.2, 0.3, 0.4, 0.5, 0.6)
        val answerable = RetrievalEvalSet.answerable
        val unanswerable = RetrievalEvalSet.unanswerable
        // 每问一行：期望文档、topK=3 的 (sourceId, score)
        class QRow(val expected: String?, val ranked: List<Pair<String, Double>>)
        val qrows = mutableListOf<QRow>()
        for (q in RetrievalEvalSet.questions) {
            val out = Retriever(emb, evalStore, minScore = 0.0).retrieve(q.q, topK = 3, deviceOnly = true)
            qrows += QRow(q.expected, out.hits.map { it.sourceId to it.score })
        }
        val evalMs = com.sekb.shared.core.nowMillis() - evalT0
        r.note("检索评测：语料 ${RetrievalEvalSet.corpus.size} 篇 建索引用时 ${buildMs}ms，" +
            "33 问单趟嵌入用时 ${evalMs}ms（阈值后处理，与共享层 calibrate 数值等价）")
        for (th in thresholds) {
            val ranks = qrows.map { row ->
                if (row.expected == null) 0
                else row.ranked.filter { it.second >= th }.indexOfFirst { it.first == row.expected } + 1
            }
            val hit1 = qrows.indices.count { i -> qrows[i].expected != null && ranks[i] == 1 }
            val hit3 = qrows.indices.count { i -> qrows[i].expected != null && ranks[i] in 1..3 }
            val mrr = qrows.indices.filter { qrows[it].expected != null }
                .map { if (ranks[it] > 0) 1.0 / ranks[it] else 0.0 }
                .average().takeIf { !it.isNaN() } ?: 0.0
            val fp = qrows.indices.count { i ->
                qrows[i].expected == null && qrows[i].ranked.any { it.second >= th }
            }
            r.note(
                "EVAL-TABLE 阈值=${Fmt.fixed(th, 2)} Hit@1=$hit1/$answerable Hit@3=$hit3/$answerable " +
                    "MRR=${Fmt.fixed(mrr, 3)} 误召回=$fp/$unanswerable",
            )
        }
        // 把 0.40 档重算一遍供断言（同一份 qrows，避免上面循环里再取一次）
        val th40 = 0.4
        val ranks40 = qrows.map { row ->
            if (row.expected == null) 0
            else row.ranked.filter { it.second >= th40 }.indexOfFirst { it.first == row.expected } + 1
        }
        val at40Hit1 = qrows.indices.count { i -> qrows[i].expected != null && ranks40[i] == 1 }
        val at40Hit3 = qrows.indices.count { i -> qrows[i].expected != null && ranks40[i] in 1..3 }
        val at40Mrr = qrows.indices.filter { qrows[it].expected != null }
            .map { if (ranks40[it] > 0) 1.0 / ranks40[it] else 0.0 }
            .average().takeIf { !it.isNaN() } ?: 0.0
        val at40Fp = qrows.indices.count { i ->
            qrows[i].expected == null && qrows[i].ranked.any { it.second >= th40 }
        }
        // Android 基线（0.40 → 26/30、30/30、0.928、0/3）：**逐项相同**才算同表
        val at40 = object {
            val hit1 = at40Hit1; val hit3 = at40Hit3; val mrr = at40Mrr
            val falsePositives = at40Fp; val answerable = answerable
            val unanswerable = unanswerable
        }
        // （下面两条 NOTE/断言沿用原口径；`at40` 字段名与共享层 `Report` 对齐，便于对照）
        r.check(
            "rag_eval_threshold_0_40_matches_android",
            at40.hit1 == 26 && at40.hit3 == 30 && at40.answerable == 30 &&
                kotlin.math.abs(at40.mrr - 0.928) < 0.0005 && at40.falsePositives == 0,
            "Hit@1=${at40.hit1}/${at40.answerable} Hit@3=${at40.hit3}/${at40.answerable} " +
                "MRR=${Fmt.fixed(at40.mrr, 3)} 误召回=${at40.falsePositives}/${at40.unanswerable}" +
                "（Android 基线 26/30、30/30、0.928、0/3）",
        )
        if (evalStore is RdbVectorStore) evalStore.close()
    }

    if (reopened is RdbVectorStore) reopened.close()
    emb.close()
    r.note("总用时 ${com.sekb.shared.core.nowMillis() - t0}ms")
    return r.render()
}

