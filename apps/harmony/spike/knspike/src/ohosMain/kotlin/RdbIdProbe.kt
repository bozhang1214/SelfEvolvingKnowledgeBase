// H5 取证探针（主线）：**RDB 里 `source_id` 到底有没有落对**。
//
// 为什么单独写它（2026-10-26）：
//   H8 的自检里出现两条互相矛盾的证据 —— ① 向量检索能命中并给出分数（0.696），
//   ② 同一个命中项的 `sourceId` 读回来是**空串**，于是 `deleteBySource("doc-rag")` 删 0 行。
//   两者不可能同时为真：说明"写"或"读"有一侧错了。两个工作流都已收工，所以主线用最小用例把它钉死。
//
// 判据设计（每一步都可独立判真假，避免"结论依赖结论"）：
//   1. 建隔离库（每轮唯一名）→ upsert **一条**已知记录（sourceId="doc-probe"）；
//   2. `SELECT source_id FROM chunks`（单列）→ 直接看这一列在库里是什么；
//   3. `SELECT id, source_id, text FROM chunks`（多列）→ 看**列名取索引**这条路对不对；
//   4. `all()`（生产路径：id/source_id/text/vector 四列 + cursorText）→ 看生产读法对不对；
//   5. `deleteBySource("doc-probe")` → 能不能删到 1 行；再 `size()` 复核。
// 只要 2 与 3/4 不一致，就说明是 `cursorColumnIndex`/`cursorText`（读）的问题；
// 三者一致但为空，就是"写"的问题（SQL 字面量/列序）。
@file:OptIn(kotlinx.cinterop.ExperimentalForeignApi::class, kotlin.experimental.ExperimentalNativeApi::class)

package com.sekb.ohos.spike

import com.sekb.shared.core.Fmt
import com.sekb.shared.rag.VectorRecord

private class RdbIdReport {
    val lines = mutableListOf<String>()
    var failed = 0
    var total = 0

    fun check(name: String, ok: Boolean, detail: String) {
        total++
        if (!ok) failed++
        lines += "${if (ok) "PASS" else "FAIL"} $name — $detail"
    }

    fun note(s: String) {
        lines += "NOTE $s"
    }

    fun render(): String =
        (listOf("RDBID ${if (failed == 0) "PASS" else "FAIL"} ${total - failed}/$total") + lines)
            .joinToString("\n")
}

/**
 * 入口。`databaseDir` 由 ArkTS 侧给（`UIAbilityContext.databaseDir`）。
 *
 * 返回多行报告，首行 `RDBID PASS n/N` 或 `RDBID FAIL n/N`。
 */
@CName("sekb_spike_rdb_id_probe")
fun sekbSpikeRdbIdProbe(databaseDir: String, runTag: String): String {
    val r = RdbIdReport()
    val dbName = "sekb_idp_${(runTag.hashCode() and 0xFFFFFF).toString(16)}.db"

    // ⚠️ **`delete` 之后再 `open`：实测删不干净**（2026-10-26 主线取证）：
    //    探针库名每轮唯一（`sekb_idp_<tag>`），但打开后 `all()` 却读到 **3 条上一轮的 `__selftest__` 行**
    //    （`dim=512`，来自自检库 `sekb_docs.db`）→ 说明 `OH_Rdb_DeleteStore` 没真的清掉，
    //    或者屏幕内的句柄仍指向旧文件。这是"自检库跨轮带脏数据"的**直接证据**，
    //    也是 `rag_retrieve` 看起来能命中（0.696）的原因之一：它命中的是**残留行**。
    //    这里把 open 后的**初始条数**报出来（期望 0）——它就是"库是否干净"的判据。
    // ⚠️ **探针用独立目录**（2026-10-26 主线，修"探针自伤"）：
    //    原来探针在**会话同一个目录**里开库（只换库名），实测这会与会话句柄互相干扰
    //    （同一次自检里 `rag_ingest` 会从"2 篇/2 段"掉成"1 篇/0 段"）。
    //    顺带解释了那条 `14800037`：**同一目录上再加一个新库名**在该实例上就是建不出来，
    //    而同一个已有库能开——换成独立目录后建库正常（本轮实测）。
    val probeDir = "$databaseDir/../probe"
    // ⚠️ Kotlin/Native 里**没有** `java.io.File`（这是"JVM 专有 API"扫描器专门挡的东西）：
    //    用 POSIX `mkdir`，父目录（`databaseDir` 的父级）已存在，所以一级即可；已存在时会返回 -1，忽略。
    platform.posix.mkdir(probeDir, 0x1EDu)
    r.note("库=$dbName dir=$probeDir（探针独立目录，不与会话抢句柄）")
    RdbStore.delete(probeDir, dbName)
    var (store, err) = RdbStore.open(probeDir, dbName)
    if (store == null) {
        // ⚠️ **对照实验**（2026-10-26 主线）：平台上先后出现过两个 databaseDir ——
        // 早期探针用的是 `/data/app/el2/100/base/<bundle>/haps/entry/database`（当时 PASS 20/20），
        // 现在 `UIAbilityContext.databaseDir` 给的是 `/data/storage/el2/database/entry`（报 14800037）。
        // 两条路径都是"目录存在、可写"却只有一条能开库，所以这里**都试一遍**并分别报出来：
        // 若旧路径能开、新路径不能 → 是路径/沙箱区域问题（可在 ArkTS 侧换用旧路径或改 area/securityLevel）；
        // 两条都不能开 → 是这台模拟器实例的 storage 状态问题（换实例/重装系统镜像）。
        val alt = "/data/app/el2/100/base/com.sekb.ohos.spike/haps/entry/database"
        val (store2, err2) = RdbStore.open(alt, dbName)
        r.check(
            "open_alt_path",
            store2 != null,
            "新路径 errCode=$err ｜ 旧路径($alt) errCode=$err2 " +
                (if (store2 != null) "→ **旧路径能开**（说明是路径/区域问题）" else "→ 两条都不能开（像是实例 storage 状态问题）"),
        )
        if (store2 != null) { store = store2; err = err2 }
        // 两条都失败：如实返回，后续断言会全部标 FAIL（不掩盖）
        if (store == null) {
            r.check("open", false, "两条路径都打不开：新=$err 旧=$err2")
            return r.render()
        }
    } else {
        r.check("open", err == RdbCode.OK, "errCode=$err（新路径 $databaseDir）")
    }

    val vs = RdbVectorStore(probeDir, com.sekb.shared.embed.EmbeddingSpace("probe@3", 3), dbName)
    r.note("space=${vs.space.id} dim=${vs.space.dim}")
    // **库干净性判据**（期望 0）：delete+open 之后不该有任何行。非 0 说明删除没生效/句柄仍指旧数据。
    val initRows = runCatching { vs.size() }.getOrElse { -1 }
    val initAll = runCatching { vs.all().map { it.id } }.getOrElse { emptyList() }
    r.check(
        "fresh_db_is_empty",
        initRows == 0 && initAll.isEmpty(),
        "delete+open 后条数=$initRows（期望 0）内容=${initAll.take(4)}" +
            (if (initRows != 0) " ⇒ **库不干净**：上表所有断言都会被残留数据污染" else ""),
    )

    // ⓪ **最小实验（裸 SQL，不经任何封装）**：把每一步的返回码都报出来，
    //    用来判定"写入不可见"到底是哪一层造成的（2026-10-26 主线加）。
    run {
        val t = "probe_raw"
        val d = store.execute("CREATE TABLE IF NOT EXISTS $t (id TEXT PRIMARY KEY, v TEXT NOT NULL)")
        val i = store.execute("INSERT OR REPLACE INTO $t (id, v) VALUES ('a', 'hello')")
        val n1 = runCatching { vs.size() }.getOrElse { -1 }   // 这是 chunks 表，与 t 无关，仅作对照
        var rawCount = -1
        val cur = store.query("SELECT COUNT(*) AS n FROM $t")
        if (cur != null) {
            if (cursorNext(cur) == 0) rawCount = (cursorInt64(cur, "n") ?: -1L).toInt()
            cursorDestroy(cur)
        }
        var rawVal: String? = null
        var rawTrace: String = "(未查询)"
        val cur2 = store.query("SELECT v FROM $t WHERE id='a'")
        if (cur2 != null) {
            // 先取轨迹（同一个游标、同一行），再取正式值
            if (cursorNext(cur2) == 0) {
                rawTrace = cursorTextProbe(cur2, "v")
                rawVal = cursorText(cur2, "v")
            } else {
                rawTrace = "cursorNext 非 0"
            }
            cursorDestroy(cur2)
        } else {
            rawTrace = "query 返回 null"
        }
        r.check(
            "raw_sql_visible",
            d == RdbCode.OK && i == RdbCode.OK && rawCount == 1 && rawVal == "hello",
            "裸 SQL：create=$d insert=$i count=$rawCount val='${rawVal ?: "<null>"}'（期望 0/0/1/hello）；chunks.size=$n1 ｜ 轨迹=[${rawTrace}]",
        )
    }

    // ① 写：一条已知记录（走生产路径 upsert → 纯 SQL INSERT OR REPLACE）
    vs.upsert(
        listOf(
            VectorRecord(
                id = "doc-probe#0",
                sourceId = "doc-probe",
                text = "探针文本",
                vector = floatArrayOf(1f, 0f, 0f),
            ),
        ),
    )
    r.check("write_size", vs.size() == 1, "size=${vs.size()}（期望 1）")

    // ② 单列 SELECT：直接看库里 source_id 的值
    run {
        val cur = store.query("SELECT source_id FROM chunks LIMIT 1")
        if (cur == null) {
            r.check("read_single_column", false, "query 返回 null")
        } else {
            var v: String? = null
            if (cursorNext(cur) == 0) v = cursorText(cur, "source_id")
            cursorDestroy(cur)
            r.check("read_single_column", v == "doc-probe", "source_id='${v ?: "<null>"}'（期望 doc-probe）")
        }
    }

    // ③ 多列 SELECT：验"按列名取索引"这条路（H8 现象指向这里）
    run {
        val cur = store.query("SELECT id, source_id, text FROM chunks LIMIT 1")
        if (cur == null) {
            r.check("read_multi_column", false, "query 返回 null")
        } else {
            var idv: String? = null
            var src: String? = null
            val nextRc = cursorNext(cur)
            val names = cursorColumnNames(cur)
            val textProbeId = cursorTextProbe(cur, "id")
            val textProbeSrc = cursorTextProbe(cur, "source_id")
            val idIdx = cursorColumnIndex(cur, "id")
            val srcIdx = cursorColumnIndex(cur, "source_id")
            if (nextRc == 0) {
                idv = cursorText(cur, "id")
                src = cursorText(cur, "source_id")
            }
            cursorDestroy(cur)
            r.check(
                "read_multi_column",
                idv == "doc-probe#0" && src == "doc-probe",
                "nextRc=$nextRc 列名=[$names] idx(id)=$idIdx idx(source_id)=$srcIdx" +
                    " 探针=[${textProbeId} | ${textProbeSrc}]" +
                    " id='${idv ?: "<null>"}' source_id='${src ?: "<null>"}'（期望 doc-probe#0 / doc-probe）",
            )
        }
    }

    // ④ 生产读法 `all()`（四列 + BLOB）
    run {
        val all = vs.all()
        val first = all.firstOrNull()
        r.check(
            "read_all_path",
            all.size == 1 && first?.sourceId == "doc-probe",
            "条数=${all.size} 首条 id='${first?.id}' source='${first?.sourceId}'" +
                " dim=${first?.vector?.size}",
        )
    }

    // ⑤ 删：契约要求返回"删除条数"
    val removed = vs.deleteBySource("doc-probe")
    r.check("delete_by_source", removed == 1, "removed=$removed（期望 1）")
    r.check("size_after_delete", vs.size() == 0, "size=${vs.size()}（期望 0）")

    // ⑥ 维度不符必须报错（与 Android 同口径）；这里只验"短向量会被拒"
    run {
        val bad = runCatching {
            vs.upsert(
                listOf(VectorRecord("x#0", "x", "t", floatArrayOf(1f, 0f))),
            )
        }
        r.check("dim_mismatch_refused", bad.isFailure, "结果=${bad.exceptionOrNull()?.message ?: "没有报错（不应发生）"}")
    }

    store.close()
    return r.render()
}
