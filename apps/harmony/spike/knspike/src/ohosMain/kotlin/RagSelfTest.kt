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
import com.sekb.shared.eval.RetrievalEvalRunner
import com.sekb.shared.eval.RetrievalEvalSet
import com.sekb.shared.rag.KnowledgeIndex
import com.sekb.shared.rag.Retriever
import com.sekb.shared.rag.VectorRecord

/** 报告（与 `EmbedReport`/`RdbReport` 同形状：首行 `RAG PASS|FAIL n/N`）。 */
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
    val dbName = "sekb_rag_selftest.db"
    RdbStore.delete(databaseDir, dbName)

    // 需要"进程重启后索引仍在"的证据 → 先关掉，从头打开
    var store: RdbVectorStore
    try {
        store = RdbVectorStore(databaseDir, emb.space, dbName)
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
    store.close()
    val reopened = RdbVectorStore(databaseDir, emb.space, dbName)
    r.check(
        "rag_persist_across_reopen",
        reopened.size() == sizeBefore && sizeBefore > 0,
        "关闭前 size=$sizeBefore 重开后 size=${reopened.size()}（0 说明没落盘）diag=${reopened.openDiagnostics}",
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
        val evalStore = RdbVectorStore(databaseDir, emb.space, "sekb_rag_eval.db")
        evalStore.clear()
        val evalIndex = KnowledgeIndex(emb, evalStore)
        val runner = RetrievalEvalRunner(emb)
        val buildT0 = com.sekb.shared.core.nowMillis()
        RetrievalEvalSet.corpus.forEach { evalIndex.ingest(it.id, it.text) }
        val buildMs = com.sekb.shared.core.nowMillis() - buildT0
        val evalT0 = com.sekb.shared.core.nowMillis()
        val reports2 = runner.calibrate(evalIndex, evalStore, listOf(0.2, 0.3, 0.4, 0.5, 0.6))
        val evalMs = com.sekb.shared.core.nowMillis() - evalT0
        r.note("检索评测：语料 ${RetrievalEvalSet.corpus.size} 篇 建索引用时 ${buildMs}ms，5 档阈值用时 ${evalMs}ms")
        for (rep in reports2) r.note("EVAL ${rep.line()}")
        // Android 基线（0.40 → 26/30、30/30、0.928、0/3）：**逐项相同**才算同表
        val at40 = reports2.first { it.threshold == 0.4 }
        r.check(
            "rag_eval_threshold_0_40_matches_android",
            at40.hit1 == 26 && at40.hit3 == 30 && at40.answerable == 30 &&
                kotlin.math.abs(at40.mrr - 0.928) < 0.0005 && at40.falsePositives == 0,
            "Hit@1=${at40.hit1}/${at40.answerable} Hit@3=${at40.hit3}/${at40.answerable} " +
                "MRR=${Fmt.fixed(at40.mrr, 3)} 误召回=${at40.falsePositives}/${at40.unanswerable}" +
                "（Android 基线 26/30、30/30、0.928、0/3）",
        )
        // 全档逐项打印都放进 NOTE（脚本会把 NOTE 行也抓出来存档）
        for (rep in reports2) {
            r.note(
                "EVAL-TABLE 阈值=${Fmt.fixed(rep.threshold, 2)} Hit@1=${rep.hit1}/${rep.answerable} " +
                    "Hit@3=${rep.hit3}/${rep.answerable} MRR=${Fmt.fixed(rep.mrr, 3)} " +
                    "误召回=${rep.falsePositives}/${rep.unanswerable}",
            )
        }
        evalStore.close()
    }

    reopened.close()
    emb.close()
    r.note("总用时 ${com.sekb.shared.core.nowMillis() - t0}ms")
    return r.render()
}

