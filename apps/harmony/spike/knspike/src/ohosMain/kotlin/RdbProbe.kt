// H5 · **探针先行**：RDB NDK C API 在模拟器上的最小往返验证（HR2 的要求）
//
// HR2 原文：「`relationalStore`/HUKS 在模拟器上的可得性未知 → 先写一个最小验证
// （建表 + 加解密往返），不通过就换方案」。所以这一份**故意只做四件事**，
// 拿到设备上的原始证据之后再去写完整的 `VectorStore`/`DocumentRegistry`：
//
//   ① 打开（或新建）一个 store，把表建起来；
//   ② 插入一行（**含 BLOB**——向量在实现里就是小端 float32 字节，BLOB 往返是硬需求）；
//   ③ 同一个句柄里查回来，逐字段比对；
//   ④ **关闭 → 重新打开**，再查一次（持久化 + 重开可读，这才是"能当索引使"）。
//
// 每一步都单独报码，理由与 `MsStep` 相同：设备上只看得到一行 hilog，
// 「哪一步失败」必须自解释，否则等于把诊断成本推给下一个人。
//
// ## 与生产实现共用同一套读法
//
// 配置/句柄/Cursor/BLOB 那些容易写错的细节都在 `RdbStore.kt` 里，**探针与
// `RdbVectorStore` 走同一份**。这样"探针绿、实现红"时能确定问题在实现而不在绑定。
//
// ## 绑定从哪来（**这里踩过一次，写下来免得重踩**）
//
// 第一版我按 ohcrypto 的先例自建了 `cinterop/rdb.def`，结果**多余**：
// OHOS Kotlin fork 的**平台 klib** 里已经有一份完整的 ArkData/RDB 绑定——
// `.../klib/platform/ohos_arm64/org.jetbrains.kotlin.native.platform.RDB`，
// manifest 写着 `headers=database/rdb/relational_store.h … linkerOpts=-lnative_rdb_ndk.z`、
// `package=platform.ArkData.RDB`。所以**正确的 import 是 `platform.ArkData.RDB.*`**，
// 自建 `.def` 只会重复一份（已删）。
//
// ## 另外两个必须在这里定死的事
//
//   · `OH_Rdb_Config` 是 `#pragma pack(1)` 的，`selfSize` 必须等于结构体**真实字节数**
//     （pack(1) 下 = 45）。不一致时 OHOS 走"部分字段可读"的兼容路径，症状是神秘失败，
//     所以报告里直接打 `sizeof` 的实测值。
//   · `dataBaseDir` 由 ArkTS 侧给（`UIAbilityContext.databaseDir`），C 侧不做路径推断。
@file:OptIn(kotlinx.cinterop.ExperimentalForeignApi::class, kotlin.experimental.ExperimentalNativeApi::class)

package com.sekb.ohos.spike

import kotlinx.cinterop.BooleanVar
import kotlinx.cinterop.CPointer
import kotlinx.cinterop.alloc
import kotlinx.cinterop.invoke
import kotlinx.cinterop.memScoped
import kotlinx.cinterop.pointed
import kotlinx.cinterop.value
import kotlinx.cinterop.ptr
import kotlinx.cinterop.sizeOf
import platform.ArkData.RDB.OH_Rdb_Config
import platform.ArkData.RDB.OH_Rdb_CreateValuesBucket
import platform.ArkData.RDB.OH_VBucket

/** 探针报告（与 `EmbedReport` 同一形状：首行 `<名字> PASS|FAIL n/N`，便于脚本 grep）。 */
private class RdbReport(private val title: String) {
    val lines = mutableListOf<String>()
    var failed = 0
    var total = 0

    fun check(name: String, ok: Boolean, detail: String) {
        total++
        if (!ok) failed++
        lines += "${if (ok) "PASS" else "FAIL"} ${name} — ${detail}"
    }

    fun note(detail: String) {
        lines += "NOTE $detail"
    }

    fun render(): String =
        (listOf("$title ${if (failed == 0) "PASS" else "FAIL"} ${total - failed}/${total}") + lines)
            .joinToString("\n")
}

/** 一个 3 维 float32 小端 BLOB（真实现里就是 `encodeVector(vector)` 的产物）。 */
private fun sampleBlob(): ByteArray = encodeVector(floatArrayOf(1.5f, -2.25f, 1.0e-3f))

/** 诊断：字节数组的十六进制串（BLOB 往返比对用）。 */
private fun hex(b: ByteArray): String = b.joinToString("") { ((it.toInt() and 0xFF) or 0x100).toString(16).substring(1) }

/** 该列是不是 NULL（`isNull` 也是结构体函数指针，同 `cursorNext` 的调用形状）。 */
private fun cursorIsNull(cur: CPointer<platform.ArkData.RDB.OH_Cursor>, idx: Int): Boolean {
    if (idx < 0) return false
    return memScoped {
        val v = alloc<BooleanVar>()
        v.value = false
        val rc = cur.pointed.isNull?.invoke(cur, idx, v.ptr) ?: -1
        rc == 0 && v.value
    }
}

/** 建表 SQL：与 Android `SqliteVectorStore.onCreate` **同一份 schema**（列名也一致）。 */
private const val DDL =
    "CREATE TABLE IF NOT EXISTS chunks (" +
        "id TEXT PRIMARY KEY, source_id TEXT NOT NULL, text TEXT NOT NULL, " +
        "dim INTEGER NOT NULL, vector BLOB NOT NULL)"

/**
 * 在已打开的 store 上做"建表 + 插一行 + 查回来"，返回 BLOB 是否逐字节往返成功。
 *
 * 抽成函数是为了让"首次打开"与"重开后"跑**同一段代码**——否则两次的判据会漂移。
 */
private fun roundTrip(store: RdbStore, r: RdbReport, tag: String): Boolean {
    val blob = sampleBlob()

    // ① 建表（幂等：IF NOT EXISTS）
    val rcExec = store.execute(DDL)
    r.check("${tag}_create_table", rcExec == RdbCode.OK, "OH_Rdb_Execute(DDL) rc=$rcExec（0=RDB_OK）")

    // ② 插入（ValuesBucket；**BLOB 必须走 PutBlob**，向量就是这条路径）
    val bucket: CPointer<OH_VBucket>? = OH_Rdb_CreateValuesBucket()
    if (bucket == null) {
        r.check("${tag}_insert", false, "OH_Rdb_CreateValuesBucket 返回空")
        return false
    }
    val b = bucket
    val rcId = bucketPutText(b, "id", "probe#0")
    val rcTx = bucketPutText(b, "text", "端侧 RAG 探针文本")
    val rcSrc = bucketPutText(b, "source_id", "probe")
    val rcDim = bucketPutInt64(b, "dim", 3L)
    val rcBlob = bucketPutBlob(b, "vector", blob)
    r.check(
        "${tag}_put_text",
        rcId && rcTx && rcSrc,
        "putText(id)=$rcId putText(text)=$rcTx putText(source_id)=$rcSrc（true=RDB_OK）",
    )
    r.check("${tag}_put_int64", rcDim, "putInt64(dim)=$rcDim（true=RDB_OK）")
    r.check(
        "${tag}_put_blob",
        rcBlob,
        "putBlob(vector, ${blob.size}B)=$rcBlob（true=RDB_OK）——向量入库走的就是这一条",
    )
    // 反向验证：往**不存在的列**写必须失败。它证明"put* 的返回值真的能反映失败"，
    // 否则上面三个判据就只是摆设（"判据本身不成立"的坑本项目踩过多次）。
    val rcUnknown = bucketPutText(b, "no_such_column", "x")
    r.check(
        "${tag}_put_unknown_column_rejected",
        !rcUnknown,
        "往不存在的列写 → $rcUnknown（**必须 false**，用来证明 put* 返回值可信）",
    )
    val rowId = store.insert("chunks", b)
    bucketDestroy(b)
    // ── 对照实验（临时诊断）：同一行改用**纯 SQL** 插一次 ─────────────────────
    // 目的：把"ValuesBucket 这条路有 bug"与"RDB 本身插不进去"分开。
    // 若 SQL 版成功而 bucket 版 rowId=-1，问题就在 bucket 的构造/写入这条链上。
    val hex = blob.joinToString("") { ((it.toInt() and 0xFF) or 0x100).toString(16).substring(1) }
    val rcSqlInsert = store.execute(
        "INSERT OR REPLACE INTO chunks (id, source_id, text, dim, vector) VALUES " +
            "('probe#sql', 'probe', '端侧 RAG 探针文本', 3, X'$hex')",
    )
    r.note("${tag}_ctrl_sql_insert rc=$rcSqlInsert（对照：纯 SQL 插入，绕过 ValuesBucket）")
    // ⚠️ 主键已存在时 `OH_Rdb_Insert` 返回 -1（默认 ConflictResolution 不是 REPLACE）。
    //    所以"重开后"这一轮**预期** rowId=-1：这不是缺陷，它反过来证明**行确实落盘了**；
    //    真实现的 upsert 必须走 `insertReplace`（RDB_CONFLICT_REPLACE）。
    r.check(
        "${tag}_insert",
        if (tag == "rdb") rowId >= 1 else rowId == -1,
        "OH_Rdb_Insert rowId=$rowId（首轮期望 ≥1；重开轮期望 -1=主键冲突→证明上一轮的行真的落盘了）",
    )

    // ③ 查回来（ExecuteQuery 走原生 SQL；真实现里 SELECT 也用它）
    val cur = store.query("SELECT id, source_id, text, dim, vector FROM chunks WHERE source_id = 'probe'")
    if (cur == null) {
        r.check("${tag}_query", false, "OH_Rdb_ExecuteQuery 返回空")
        return false
    }
    var rows = 0
    var seenId: String? = null
    var seenDim: Long? = null
    var seenSource: String? = null
    var text: String? = null
    var roundTripped = false
    while (cursorNext(cur) == 0) {
        rows++
        seenId = cursorText(cur, "id")
        seenSource = cursorText(cur, "source_id")
        text = cursorText(cur, "text")
        seenDim = cursorInt64(cur, "dim")
        val back = cursorBlob(cur, "vector")
        val f = back?.let { decodeVector(it) }
        roundTripped = back != null && back.contentEquals(blob) && f != null && f.size == 3 &&
            f[0] == 1.5f && f[1] == -2.25f && f[2] == 1.0e-3f
        // 诊断（本轮 BLOB 往返失败时加的）：把"写进去的"与"读回来的"逐字节打出来。
        // 没有这条，报告只说 "false"，无法区分"写坏了 / 读坏了 / 列索引取错"。
        r.note(
            "${tag}_blob_diag 写入=${hex(blob)} 读回=${back?.let { hex(it) } ?: "null"}" +
                " 字节数=${back?.size} 列尺寸=${cursorColumnSize(cur, cursorColumnIndex(cur, "vector"))}" +
                " 为NULL=${cursorIsNull(cur, cursorColumnIndex(cur, "vector"))}",
        )
    }
    cursorDestroy(cur)
    r.note("${tag}_ctrl_sql_row rows=$rows（含 SQL 对照行；桶行=${rows - store.countRows("probe#sql")}）")
    r.check("${tag}_query_once", rows >= 1, "GoToNextRow 命中行数=$rows（期望 ≥1）")
    r.check(
        "${tag}_text_roundtrip",
        seenId == "probe#0" && seenSource == "probe" && seenDim == 3L && text == "端侧 RAG 探针文本",
        "id='$seenId' source_id='$seenSource' dim=$seenDim text='$text'（中文必须逐字符相同）",
    )
    r.check(
        "${tag}_blob_roundtrip",
        roundTripped,
        "BLOB ${blob.size}B 读出后逐字节相同=$roundTripped（float32 小端：1.5/-2.25/1.0e-3）",
    )
    return roundTripped
}

/**
 * 探针入口。`databaseDir` 由 ArkTS 侧给（`UIAbilityContext.databaseDir`）。
 *
 * 报告首行：`RDB-PROBE PASS|FAIL n/N`。
 */
@CName("sekb_spike_rdb_probe")
fun sekbSpikeRdbProbe(databaseDir: String): String {
    val r = RdbReport("RDB-PROBE")
    val dbName = "sekb_probe.db"

    // 先删掉旧的探针库：**探针必须可重复运行**，否则第二次跑会因为主键冲突给出假红。
    val rcDel = RdbStore.delete(databaseDir, dbName)
    r.note("pre_clean OH_Rdb_DeleteStore rc=$rcDel（首次运行时库不存在，返回非 0 是正常的）")

    // selfSize 的实测值：把 structure layout 的事实钉在报告里（45 = 4+8×4+1+4+4，pack(1)）
    val sz = sizeOf<OH_Rdb_Config>()
    r.check(
        "config_self_size",
        sz == 45L,
        "sizeof(OH_Rdb_Config)=$sz（pack(1) 下期望 45；不等于 45 说明 cinterop 的结构体布局" +
            "与头文件不一致，必须先查清再继续）",
    )

    // ══ 第一轮：打开 → 建表 → 插入 → 查询 ══════════════════════════════════
    val (store1, err1) = RdbStore.open(databaseDir, dbName)
    if (store1 == null) {
        r.check("open_store", false, "OH_Rdb_GetOrOpen 返回空 errCode=$err1（dir=$databaseDir）")
        return r.render()
    }
    r.check("open_store", err1 == RdbCode.OK, "errCode=$err1（0=RDB_OK）dir=$databaseDir")
    roundTrip(store1, r, "rdb")

    // ══ 第二轮：**关闭后重新打开**，同一份数据必须还在（持久化的硬判据）══════
    val rcClose = store1.close()
    r.check("close_store", rcClose == RdbCode.OK, "OH_Rdb_CloseStore rc=$rcClose（0=RDB_OK）")

    val (store2, err2) = RdbStore.open(databaseDir, dbName)
    if (store2 == null) {
        r.check("reopen_store", false, "重开失败 errCode=$err2 —— 数据没落盘或句柄泄漏")
        return r.render()
    }
    r.check("reopen_store", err2 == RdbCode.OK, "errCode=$err2（同一个库文件，重开成功）")
    roundTrip(store2, r, "rdb2")
    store2.close()

    return r.render()
}
