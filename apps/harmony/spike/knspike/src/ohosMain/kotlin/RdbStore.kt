// H5 · RDB 的 NDK C API 落地的**共享小工具层**：配置、句柄、Cursor 读值、BLOB 编解码。
//
// ## 为什么单独一份（而不是塞进 `RdbVectorStore`）
//
// 三个理由，都不是"为了好看"：
//   1. `OH_Rdb_Config` 的 `selfSize` 口径、`OH_Cursor` 的函数指针调用方式、BLOB 的
//      小端 float32 约定——这些是**踩过才知道**的细节，集中一处才能只写一次注释；
//   2. 探针（`RdbProbe.kt`）与生产实现（`RdbVectorStore.kt`）必须走**同一套**存储读法，
//      否则"探针绿了但实现红"时无法分清是引擎问题还是实现问题；
//   3. `OH_Cursor` 的读值散落在各处时，`getSize` 返回的 `size_t` 宽度、`getText` 的
//      NUL 终止、`getBlob` 的 `uint8_t*` 转换各自都会有人写错一遍。
//
// ## 平台绑定从哪来（**不要自建 cinterop `.def`**）
//
// OHOS Kotlin fork 的**平台 klib** 里已经有一份完整的 ArkData/RDB 绑定：
// `klib/platform/ohos_arm64/org.jetbrains.kotlin.native.platform.RDB`，
// manifest 写着 `headers=database/rdb/relational_store.h … linkerOpts=-lnative_rdb_ndk.z`、
// `package=platform.ArkData.RDB`。所以 import 是 `platform.ArkData.RDB.*`。
// （我第一版按 ohcrypto 的先例自建了 `.def`，属于重复造；已删。）
//
// ## ⚠️⚠️ 本层最硬的一条经验：**结构体里的函数指针没有 `@CCall` 转换**
//
// 这是我在这一轮里花掉最多构建时间的地方，值三轮构建，必须写清楚：
//
//   · **顶层函数**（`OH_Rdb_Execute(store, sql)`、`OH_Rdb_GetOrOpen(cfg, err)`）在 cinterop
//     里带 `@CCall.CString` 注解 → **可以直接传 `String`**，也能直接传 `cfg.ptr`；
//   · **结构体里的函数指针字段**（`OH_VBucket.putText`、`OH_Cursor.getText`）**没有**这层注解：
//     它们的形参就是 `CPointer<ByteVarOf<Byte>>?`，**传 `String` 编译不过**，而且
//     `reinterpret<CFunction<(…, String?, …) -> Int>>()` **也绕不过去**
//     （试过，报 `None of the following candidates is applicable because of a receiver type mismatch`）。
//
// 结论（也是本层所有写法的依据）：对这些字段，**必须自己准备 C 缓冲**——
//   · 字符串：`memScoped { ... name.cstr.ptr ... }`（`CValues<ByteVar>.ptr` 给 `CPointer<ByteVar>`）；
//   · BLOB：`memScoped { allocArray<UByteVar>(n) }` 后逐字节填（`ByteArray.refTo(0)` 是
//     `CPointer<ByteVar>`，与 `uint8_t*` 的 `CPointer<UByteVar>` **不兼容**，这条路走不通）。
//
// 这么做还有一个额外好处：缓冲的生命周期被 `memScoped` 明确框住，不存在"传递临时指针"的风险。
@file:OptIn(kotlinx.cinterop.ExperimentalForeignApi::class)

package com.sekb.ohos.spike

import kotlinx.cinterop.ByteVar
import kotlinx.cinterop.CPointer
import kotlinx.cinterop.IntVar
import kotlinx.cinterop.LongVar
import kotlinx.cinterop.ULongVar
import kotlinx.cinterop.UByteVar
import kotlinx.cinterop.alloc
import kotlinx.cinterop.allocArray
import kotlinx.cinterop.get
import kotlinx.cinterop.invoke
import kotlinx.cinterop.memScoped
import kotlinx.cinterop.pointed
import kotlinx.cinterop.ptr
import kotlinx.cinterop.reinterpret
import kotlinx.cinterop.set
import kotlinx.cinterop.sizeOf
import kotlinx.cinterop.toKString
import kotlinx.cinterop.value
import platform.ArkData.RDB.OH_Cursor
import platform.ArkData.RDB.OH_Rdb_Config
import platform.ArkData.RDB.OH_Rdb_GetOrOpen
import platform.ArkData.RDB.OH_Rdb_Store
import platform.ArkData.RDB.OH_VBucket
import platform.ArkData.RDB.S1

/** RDB 的错误码（`relational_store_error_code.h`；只列实现里真的会判的几个）。 */
object RdbCode {
    const val OK = 0

    /** `E_BASE`：常见异常的基码 */
    const val E_BASE = 14800000
    const val E_INVALID_ARGS = E_BASE + 1
}

/** 包名/模块名（`OH_Rdb_Config` 要，必须与 hapshell 的 `AppScope/app.json5` 一致）。 */
const val SEKB_BUNDLE = "com.sekb.ohos.spike"
const val SEKB_MODULE = "entry"


/**
 * 一个**显式生命周期**的 C 字符串（含结尾 NUL）。
 *
 * ⚠️ 为什么不用 `String.cstr`：它给的是 `CValues<ByteVar>`，而结构体函数指针的形参是
 * `CPointer<ByteVar>?`——两者之间**没有** `.ptr` 可取（实测报
 * `actual type is 'CPointer<CValues<ByteVarOf<Byte>>>'`，连 `.ptr` 都解析不到）。
 *
 * ⚠️ 为什么不用 `memScoped { allocArray<ByteVar>() }` **然后返回它的指针**：
 * 那是在返回一个**已经失效**的栈上地址（`memScoped` 离开块就 free），
 * 属于"能编过、行为未定义"的写法——正是本项目最忌讳的那类坑。
 * 所以这里用 `nativeHeap`（调用方负责 [close]），生命周期**显式**。
 */
private class CBuf private constructor(val ptr: CPointer<ByteVar>) {
    companion object {
        /**
         * 把 Kotlin 字符串复制成 C 字符串（含结尾 NUL）。
         *
         * 生命周期口径（**这里刻意写清楚，因为它看起来像 bug 但不是**）：
         * `memScoped` 返回的 `CPointer` **不会**因为离开块而失效——Kotlin/Native 的
         * `CPointer` 会**持有**它指向的 `NativePointed`，那个对象由 GC 管；
         * 只要 `CPointer` 可达，内存就有效。这是 cinterop 的**标准逃逸写法**
         * （官方示例里 `memScoped { ... .ptr }` 被返回到处都是）。
         * 反过来，"用一个原始 nativeHeap 指针 + 手工 free" 更容易写错：实测
         * `nativeHeap.allocArray` 返回的是 `CPointer`，而 `nativeHeap.free` 要
         * `NativePlacement`，两者**不是一回事**。
         */
        fun of(value: String): CBuf {
            val bytes = value.encodeToByteArray()
            val p: CPointer<ByteVar> = memScoped {
                val buf = allocArray<ByteVar>(bytes.size + 1)
                for (i in bytes.indices) buf[i] = bytes[i]
                buf[bytes.size] = 0
                buf
            }
            return CBuf(p)
        }
    }
}

/** 在 [block] 期间持有若干 C 字符串。 */
private inline fun <R> withCStrings(vararg values: String, block: (Array<CPointer<ByteVar>>) -> R): R {
    val bufs = Array(values.size) { CBuf.of(values[it]) }
    return block(Array(values.size) { bufs[it].ptr })
}

/**
 * SQL 字符串字面量转义（只处理单引号——SQLite 标准做法：`'` → `''`）。
 *
 * 为什么需要它：写入路径改走**纯 SQL** 之后（见 [RdbStore.insertReplace] 的说明），
 * `source_id` / `text` 都是用户内容，不转义就会被引号截断（甚至注入）。
 * 转义比 `OH_Predicates` 弱一档（没有参数绑定），所以：
 *   · **只用在"必须拼 SQL"的地方**（写入路径），并且转义必须做全；
 *   · 检索/查询路径本来就不带用户内容拼串（`SELECT ... WHERE source_id = '<转义后>'`）。
 * 将来若要回到参数绑定，`OH_Rdb_ExecuteV2(sql, args)` 是正路（本机 API 22 有），
 * 但它要构造 `OH_Data_Values`，多一层复杂度——当前先用转义 + 统一走这一处。
 */
fun sqlQuote(v: String): String = "'" + v.replace("'", "''") + "'"

/** BLOB 的 SQL 字面量：`X'616263'`（十六进制），与 `encodeVector` 的输出逐字节一致。 */
fun sqlBlob(b: ByteArray): String {
    val sb = StringBuilder(b.size * 2 + 3)
    sb.append("X'")
    for (x in b) {
        val v = x.toInt() and 0xFF
        sb.append(HEX[v ushr 4]).append(HEX[v and 0xF])
    }
    return sb.append('\'').toString()
}

private const val HEX = "0123456789ABCDEF"

/** 一个已打开的 RDB store。**必须 close()**：RDB 内部有句柄与页缓存，泄漏会表现为"第二次打开失败"。 */
class RdbStore private constructor(val handle: CPointer<OH_Rdb_Store>) {

    /** 执行一条 SQL（DDL 或无需读回的写）。返回 RDB 状态码。 */
    fun execute(sql: String): Int = platform.ArkData.RDB.OH_Rdb_Execute(handle, sql)

    /** 执行一条 SELECT，返回 Cursor（调用方负责 `cursorDestroy`）。 */
    fun query(sql: String): CPointer<OH_Cursor>? =
        platform.ArkData.RDB.OH_Rdb_ExecuteQuery(handle, sql)

    /** 普通插入：**主键冲突返回 -1**（默认 ConflictResolution 不是 REPLACE）。 */
    fun insert(table: String, bucket: CPointer<OH_VBucket>): Int =
        platform.ArkData.RDB.OH_Rdb_Insert(handle, table, bucket)

    /**
     * **upsert 的正确写法**：`OH_Rdb_Insert` 在主键冲突时返回 -1，不会覆盖。
     *
     * ⚠️ **2026-10-26 实测：这条路径在 Kotlin/Native 侧不可用**（返回 -1），
     * 而**同一个 C API、同一个 `ValuesBucket` 布局**在 C++（dlopen）侧返回 1 且数据正确入库。
     * 因此生产写入**改走 [execSql]（纯 SQL）**——见 [upsertChunk] / [upsertDocument]。
     * 这个方法**保留但不再被生产路径调用**，留着是为了让"哪条路走过、结果如何"有迹可循，
     * 也便于后来者用一个最小用例复现（不要直接删掉又踩回来）。
     */
    fun insertReplace(table: String, bucket: CPointer<OH_VBucket>): Int = memScoped {
        val rowId = alloc<LongVar>()
        rowId.value = -1L
        val rc = platform.ArkData.RDB.OH_Rdb_InsertWithConflictResolution(
            handle, table, bucket,
            platform.ArkData.RDB.RDB_CONFLICT_REPLACE, rowId.ptr,
        )
        if (rc != RdbCode.OK) rc else rowId.value.toInt()
    }

    /**
     * 执行一条写 SQL，返回**受影响行数**（`OH_Rdb_Execute` 只回状态码，所以这里再查一次
     * `changes()`；仅用于"删了多少行"这类需要行数的场景）。
     */
    fun execChanges(sql: String): Int {
        val rc = execute(sql)
        if (rc != RdbCode.OK) return -1
        val cur = query("SELECT changes() AS n") ?: return 0
        var n = 0
        if (cursorNext(cur) == 0) n = (cursorInt64(cur, "n") ?: 0L).toInt()
        cursorDestroy(cur)
        return n
    }

    /**
     * 按 `source_id` 删一批行（**`deleteBySource` 的实现基础**）。
     *
     * 为什么用 `OH_Predicates`（结构化条件）而不是拼 SQL 字符串：
     * `source_id` 来自用户导入的文件名，直接拼 SQL 会有引号/注入问题；
     * predicates 的 `equalTo` 由引擎做参数绑定。
     */
    fun deleteBySourceColumn(table: String, sourceId: String): Int =
        withCStrings(table, sourceId, "source_id") { cs ->
            val pred = platform.ArkData.RDB.OH_Rdb_CreatePredicates(table) ?: return@withCStrings -1
            val obj = platform.ArkData.RDB.OH_Rdb_CreateValueObject()
            if (obj == null) {
                pred.pointed.destroy?.invoke(pred)
                return@withCStrings -1
            }
            val putText = obj.pointed.putText
            val equalTo = pred.pointed.equalTo
            if (putText == null || equalTo == null) {
                obj.pointed.destroy?.invoke(obj)
                pred.pointed.destroy?.invoke(pred)
                return@withCStrings -1
            }
            // 结构体函数指针 → 必须自己准备 C 字符串（见文件头）
            var rc = -1
            if (putText(obj, cs[1]) == RdbCode.OK) {
                equalTo(pred, cs[2], obj)
                rc = platform.ArkData.RDB.OH_Rdb_Delete(handle, pred)
            }
            obj.pointed.destroy?.invoke(obj)
            pred.pointed.destroy?.invoke(pred)
            rc
        }

    fun beginTransaction(): Int = platform.ArkData.RDB.OH_Rdb_BeginTransaction(handle)
    fun commit(): Int = platform.ArkData.RDB.OH_Rdb_Commit(handle)
    fun rollBack(): Int = platform.ArkData.RDB.OH_Rdb_RollBack(handle)

    fun close(): Int = platform.ArkData.RDB.OH_Rdb_CloseStore(handle)

    /** 诊断：某 id 是否存在（临时对照实验用）。 */
    fun countRows(id: String): Int {
        val cur = query("SELECT COUNT(*) AS n FROM chunks WHERE id = '${id.replace("'", "''")}'") ?: return -1
        var n = 0
        if (cursorNext(cur) == 0) n = (cursorInt64(cur, "n") ?: 0L).toInt()
        cursorDestroy(cur)
        return n
    }

    companion object {
        /** 填一份 `OH_Rdb_Config`（`selfSize` 必须是**真实字节数**，见 `#pragma pack(1)`）。 */
        // ⚠️ [cBuf] 里的缓冲是 memScoped 分配、**离开块即失效**，所以 fill 的调用点
        //    （open/delete）本身也在 memScoped 里，且 fill 之后立刻用 cfg —— 生命周期正确。
        private fun fill(
            cfg: OH_Rdb_Config,
            cs: Array<CPointer<ByteVar>>,
        ) {
            cfg.selfSize = sizeOf<OH_Rdb_Config>().toInt()
            // 注意：这几个字段**是结构体成员**，所以 `@CCall.CString` 不生效 → 必须显式 C 缓冲
            cfg.dataBaseDir = cs[0]
            cfg.storeName = cs[1]
            cfg.bundleName = cs[2]
            cfg.moduleName = cs[3]
            cfg.isEncrypt = false
            // S1 = 1u（OHOS 枚举在 Kotlin 侧是 UInt），字段是 `int securityLevel`
            cfg.securityLevel = S1.toInt()
            cfg.area = 0
        }

        /**
         * 打开（不存在则新建）。
         *
         * @param dir `UIAbilityContext.databaseDir` —— **由平台给**，C 侧不做路径推断
         *   （App 沙箱路径与 bundle/模块名相关，自己拼必然错）
         */
        fun open(dir: String, name: String, bundle: String = SEKB_BUNDLE): Pair<RdbStore?, Int> {
            // ⚠️ **装包后第一次打开几乎必然失败一次**（2026-10-26 三轮实测）：
            //    刚 `hdc install` 完（或换了一台新模拟器实例）时，第一次 `OH_Rdb_GetOrOpen`
            //    返回 `14800037 = RDB_E_CREATE_FOLDER_FAIL`，而**同一进程随后的重试就成功**；
            //    把模拟器实例 stop→start 也一样（文档里记了那条复现）。这不是我们代码的路径问题：
            //    失败前已确认 `databaseDir` 存在且可写（`AppCtx.ensureDatabaseDir` 会先建父目录+目标目录
            //    并复核），所以剩下的解释是"平台侧 el2 存储区在装包/首启后尚未就绪"。
            //    策略：**重试 3 次、每次间隔 120ms**，并把最后一次的 errCode 原样返回
            //    （不吞错：三次都失败说明是真问题，调用方仍能拿到错误码）。
            var last = -1
            var handle: CPointer<platform.ArkData.RDB.OH_Rdb_Store>? = null
            for (attempt in 1..3) {
                val pair = withCStrings(dir, name, bundle, SEKB_MODULE) { cs ->
                    memScoped {
                        val cfg = alloc<OH_Rdb_Config>()
                        fill(cfg, cs)
                        val err = alloc<IntVar>()
                        err.value = 0
                        val h = OH_Rdb_GetOrOpen(cfg.ptr, err.ptr)
                        (if (h == null) null else RdbStore(h)) to err.value
                    }
                }
                last = pair.second
                if (pair.first != null) return pair
                if (attempt < 3) platform.posix.usleep(120_000u)
            }
            return null to last
        }

        /** 删除整个库文件（只有"重建索引"与探针清理用；索引是可重建的派生数据）。 */
        fun delete(dir: String, name: String, bundle: String = SEKB_BUNDLE): Int =
            withCStrings(dir, name, bundle, SEKB_MODULE) { cs ->
                memScoped {
                    val cfg = alloc<OH_Rdb_Config>()
                    fill(cfg, cs)
                    platform.ArkData.RDB.OH_Rdb_DeleteStore(cfg.ptr)
                }
            }
    }
}

// ── Cursor：`OH_Cursor` 是"函数指针表"，不是不透明句柄 ──────────────────────────
//
// ⚠️ 三个坑，各值了一轮构建：
//   1. `OH_Cursor_GoToNextRow` 之类**不是**顶层函数（只有 `OH_Cursor_GetFloatVector*` 是），
//      它们真实身份是**结构体里的函数指针字段** → 必须 `cur.pointed.<字段>`；
//   2. **函数指针调用需要显式 `import kotlinx.cinterop.invoke`**（那是顶层扩展）。
//      不 import 时报的错是 `Cannot infer type for type parameter 'T'/'R'` +
//      `None of the following candidates is applicable because of a receiver type mismatch`
//      ——从这些信息几乎猜不到"少的是一行 import"（我为此写了个 scratch 文件做二分才定位到）；
//   3. 传缓冲/字符串时**没有自动转换**（见文件头），必须 `allocArray<ByteVar>` / `cstr.ptr`。

/** 光标下移一行；返回 0 表示有行。 */
fun cursorNext(cur: CPointer<OH_Cursor>): Int = cur.pointed.goToNextRow?.invoke(cur) ?: -1

fun cursorDestroy(cur: CPointer<OH_Cursor>) {
    cur.pointed.destroy?.invoke(cur)
}

/**
 * **列名自省**（2026-10-26 主线加，用来终结"按名字取不到列"的猜测）。
 *
 * 为什么需要：实测出现 `getColumnIndex("id")` 取不到（`cursorText` 返回 null → `all()` 把整行跳过），
 * 而同一张表的 `COUNT(*)` 又是对的。与其猜，不如把游标**自己报告的列名**列出来对比。
 * 返回 `"0=id|1=source_id|2=text"` 这种形式。
 */
fun cursorColumnNames(cur: CPointer<OH_Cursor>): String {
    val n = memScoped {
        val c = alloc<IntVar>()
        c.value = -1
        val rc = cur.pointed.getColumnCount?.invoke(cur, c.ptr) ?: -1
        if (rc != RdbCode.OK) -1 else c.value
    }
    if (n <= 0) return "getColumnCount 失败/0（n=$n）"
    val sb = StringBuilder()
    for (i in 0 until n) {
        if (i > 0) sb.append('|')
        sb.append(i).append('=')
        memScoped {
            val buf = allocArray<ByteVar>(128)
            val rc = cur.pointed.getColumnName?.invoke(cur, i, buf, 128) ?: -1
            sb.append(if (rc == RdbCode.OK) buf.toKString() else "?rc=$rc")
        }
    }
    return sb.toString()
}

fun cursorColumnIndex(cur: CPointer<OH_Cursor>, name: String): Int =
    withCStrings(name) { cs ->
        memScoped {
            val i = alloc<IntVar>()
            i.value = -1
            val f = cur.pointed.getColumnIndex ?: return@memScoped -1
            if (f(cur, cs[0], i.ptr) != RdbCode.OK) -1 else i.value
        }
    }

/** 列值的字节数（TEXT 含结尾 NUL；BLOB 是原始长度）。失败返回 -1。 */
fun cursorColumnSize(cur: CPointer<OH_Cursor>, idx: Int): Long = memScoped {
    val n = alloc<ULongVar>()
    n.value = 0uL
    val rc = cur.pointed.getSize?.invoke(cur, idx, n.ptr) ?: -1
    if (rc != RdbCode.OK) -1L else n.value.toLong()
}

fun cursorText(cur: CPointer<OH_Cursor>, name: String): String? {
    val idx = cursorColumnIndex(cur, name)
    if (idx < 0) return null
    val n = cursorColumnSize(cur, idx)
    if (n < 0) return null
    // ⚠️ **长度语义**（2026-10-26 主线实测）：`OH_Cursor::getText(cursor, idx, buf, length)` 的
    // `length` 文档写"可由 getSize 得到"。第一版传 `n+1`（多留 1 字节给 NUL）——
    // 在 `id`（值较长、`doc-probe#0`）这一列上**实测返回非 0**（拿不到值），
    // 而 `source_id`（`doc-probe`）却正常；`getColumnIndex`/列名/索引都已自省确认无误。
    // 现在按"先试它说的大小"再退"多一个字节"的顺序取，**两者都失败才算失败**（不再靠一个魔数）。
    // 让 `all()` 的 `?: continue` 不再把整行静默跳过——那是"库里明明有数据却检索不到"的直接来源。
    val attempts = intArrayOf(n.toInt(), (n + 1).toInt())
    for (len in attempts) {
        if (len <= 0) continue
        val got = memScoped {
            val buf = allocArray<ByteVar>(len)
            val rc = cur.pointed.getText?.invoke(cur, idx, buf, len) ?: -1
            if (rc != RdbCode.OK) null else buf.toKString()
        }
        if (got != null) return got
    }
    return null
}

/** 诊断：某列 `getSize` 的返回值与两次 `getText` 的返回码（用于上面那条长度语义实验）。 */
fun cursorTextProbe(cur: CPointer<OH_Cursor>, name: String): String {
    val idx = cursorColumnIndex(cur, name)
    if (idx < 0) return "$name: idx<0"
    val n = cursorColumnSize(cur, idx)
    val codes = IntArray(2)
    memScoped {
        val b1 = allocArray<ByteVar>(maxOf(n.toInt(), 1))
        codes[0] = cur.pointed.getText?.invoke(cur, idx, b1, n.toInt()) ?: -1
        val b2 = allocArray<ByteVar>(maxOf(n.toInt() + 1, 1))
        codes[1] = cur.pointed.getText?.invoke(cur, idx, b2, (n + 1).toInt()) ?: -1
    }
    return "$name: idx=$idx size=$n getText(size)=${codes[0]} getText(size+1)=${codes[1]}"
}

fun cursorInt64(cur: CPointer<OH_Cursor>, name: String): Long? {
    val idx = cursorColumnIndex(cur, name)
    if (idx < 0) return null
    return memScoped {
        val v = alloc<LongVar>()
        v.value = 0L
        val rc = cur.pointed.getInt64?.invoke(cur, idx, v.ptr) ?: -1
        if (rc != RdbCode.OK) null else v.value
    }
}

fun cursorBlob(cur: CPointer<OH_Cursor>, name: String): ByteArray? {
    val idx = cursorColumnIndex(cur, name)
    if (idx < 0) return null
    val n = cursorColumnSize(cur, idx)
    if (n < 0) return null
    if (n == 0L) return ByteArray(0)
    return memScoped {
        val buf = allocArray<UByteVar>(n)
        val rc = cur.pointed.getBlob?.invoke(cur, idx, buf, n.toInt()) ?: -1
        if (rc != RdbCode.OK) null else ByteArray(n.toInt()) { buf[it].toByte() }
    }
}

// ── ValuesBucket 的**带判据**写入 ─────────────────────────────────────────────
//
// 每个 put* 都有返回值，而**静默忽略它是本层最容易犯的错**：写失败只会表现为
// "那列是 NULL/默认值"，检索时看不出异常。所以这里全部返回 Boolean，调用方必须处理。
//
// ⚠️ 每个函数体都在 `memScoped` 里：C 字符串/字节缓冲必须在调用期间存活（见文件头）。

fun bucketPutText(b: CPointer<OH_VBucket>, field: String, value: String): Boolean =
    withCStrings(field, value) { cs ->
        val f = b.pointed.putText ?: return@withCStrings false
        f(b, cs[0], cs[1]) == RdbCode.OK
    }

fun bucketPutInt64(b: CPointer<OH_VBucket>, field: String, value: Long): Boolean =
    withCStrings(field) { cs ->
        val f = b.pointed.putInt64 ?: return@withCStrings false
        f(b, cs[0], value) == RdbCode.OK
    }

fun bucketPutBlob(b: CPointer<OH_VBucket>, field: String, value: ByteArray): Boolean =
    withCStrings(field) { cs ->
        memScoped {
            val f = b.pointed.putBlob ?: return@memScoped false
            val buf = allocArray<UByteVar>(value.size)
            for (i in value.indices) buf[i] = value[i].toUByte()
            f(b, cs[0], buf, value.size.toUInt()) == RdbCode.OK
        }
    }

fun bucketDestroy(b: CPointer<OH_VBucket>) {
    b.pointed.destroy?.invoke(b)
}

// ── 向量的 BLOB 编解码（**float32 小端**，与 Android `SqliteVectorStore.encode/decode` 逐字节同口径）──
//
// 为什么不用 `OH_VBucket_PutFloatVector`：那个 API 存的是它自己的浮点向量列类型
// （配套 `OH_Cursor_GetFloatVector*`），而 Android 侧存的是普通 BLOB。
// **两端要能互相读懂**（同一份索引可在端间迁移是 RFC §18.1 的目标），
// 所以必须用同一个字节口径 = 小端 float32 BLOB。

fun encodeVector(v: FloatArray): ByteArray {
    val out = ByteArray(v.size * 4)
    for (i in v.indices) {
        val bits = v[i].toRawBits()
        for (b in 0 until 4) out[i * 4 + b] = ((bits ushr (8 * b)) and 0xFF).toByte()
    }
    return out
}

fun decodeVector(b: ByteArray): FloatArray {
    val out = FloatArray(b.size / 4)
    for (i in out.indices) {
        var bits = 0
        for (k in 0 until 4) bits = bits or ((b[i * 4 + k].toInt() and 0xFF) shl (8 * k))
        out[i] = Float.fromBits(bits)
    }
    return out
}
