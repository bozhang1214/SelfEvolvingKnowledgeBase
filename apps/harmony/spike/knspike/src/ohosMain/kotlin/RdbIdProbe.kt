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
    r.note("库=$dbName dir=$databaseDir")

    RdbStore.delete(databaseDir, dbName)
    val (store, err) = RdbStore.open(databaseDir, dbName)
    if (store == null) {
        r.check("open", false, "打开失败 errCode=$err")
        return r.render()
    }
    r.check("open", err == RdbCode.OK, "errCode=$err")

    val vs = RdbVectorStore(databaseDir, com.sekb.shared.embed.EmbeddingSpace("probe@3", 3), dbName)
    r.note("space=${vs.space.id} dim=${vs.space.dim}")

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
            if (cursorNext(cur) == 0) {
                idv = cursorText(cur, "id")
                src = cursorText(cur, "source_id")
            }
            cursorDestroy(cur)
            r.check(
                "read_multi_column",
                idv == "doc-probe#0" && src == "doc-probe",
                "id='${idv ?: "<null>"}' source_id='${src ?: "<null>"}'（期望 doc-probe#0 / doc-probe）",
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
