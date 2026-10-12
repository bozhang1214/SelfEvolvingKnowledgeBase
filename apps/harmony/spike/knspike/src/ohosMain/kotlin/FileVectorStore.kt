// 文件型向量库（**替代 RDB 的存储实现**，2026-10-26 主线）。
//
// ── 为什么要有它 ────────────────────────────────────────────────────────────────
// 鸿蒙的 RDB **NDK C 路径**在本机模拟器上被证明**读一致性不可靠**：同一进程、同一句柄、
// 相邻两次调用会给出互相矛盾的答案（`size()`=0 而 `all()`=2；同一条 SQL 聚合一篇算 1 一篇算 0）。
// 排查已完成并留档：**不是**事务、游标长度语义、列名/索引、库名/目录、多句柄、SQL 聚合、
// 嵌入与打分——这些都被逐一实验排除（见 `RdbIdProbe.kt` 与 changelog）。
//
// 而**文件 I/O 在鸿蒙上是被验证可靠的**：聊天历史就是"JSON 文件 + 先写 .tmp 再 rename"，
// 实测跨 force-stop/重装都能恢复（见 H8 的 `ui-chat-restored` 截图）。
// 向量索引是**可重建的派生数据**，不需要事务/并发/查询这些数据库能力——
// 于是最务实的选择是：**绕开 RDB，用文件存**。
//
// ── 为什么这是对的工程选择（而不是"逃避"）────────────────────────────────────
// 1. `VectorStore`/`DocumentRegistry` 两个接口**一字不改** ⇒ 共享层的
//    `KnowledgeIndex`/`Chunking`/`Retriever`/评测集**零改动**（端口模式的价值就在这里）；
// 2. NDK C 路径与它的探针**保留在仓库**（`RdbVectorStore.kt`/`RdbIdProbe.kt`），
//    它们已经证明了很多"不是什么原因"，将来平台修好 RDB 时可直接切回；
// 3. 容量匹配：端侧文档库是几十~几百个切片（512 维 float），全量驻内存 + 落盘毫无压力；
//    真要上十万级切片时再谈索引结构（那时也不是 RDB 该干的事）。
//
// ── 文件格式（**行式 + 十六进制**，故意选最笨但最不容易解析错的）──────────────
//   每行一条记录，字段用 `\t` 分隔，文本里的换行/制表符**写入前替换**（保证"一行一记录"）：
//     `D\t<id>\t<name>\t<sizeBytes>\t<chunks>\t<addedAt>\t<deviceOnly>`     文档登记
//     `C\t<id>\t<sourceId>\t<text>\t<hex(encodeVector)>`                    切片
//   为什么用十六进制而不是二进制：解析器只有 `split('\t')`，没有长度前缀/字节序/对齐问题；
//   代价是体积翻倍，而本场景体积无关紧要。
//
// ── 写入的原子性 ──────────────────────────────────────────────────────────────
// 先写 `<file>.tmp` 再 `rename` 覆盖（与 `HistoryStore` 同一条经验）：中途崩溃不会留下半个库。
// 保存是**整库重写**（数据量小，换来实现简单与"读回来的一定是刚才那份内存状态"）。
@file:OptIn(kotlinx.cinterop.ExperimentalForeignApi::class, kotlin.experimental.ExperimentalNativeApi::class)

package com.sekb.ohos.spike

import com.sekb.shared.embed.EmbeddingSpace
import com.sekb.shared.rag.DocumentInfo
import com.sekb.shared.rag.DocumentRegistry
import com.sekb.shared.rag.VectorHit
import com.sekb.shared.rag.VectorMath
import com.sekb.shared.rag.VectorRecord
import com.sekb.shared.rag.VectorStore
import kotlinx.cinterop.ByteVar
import kotlinx.cinterop.UByteVar
import kotlinx.cinterop.addressOf
import kotlinx.cinterop.allocArray
import kotlinx.cinterop.reinterpret
import kotlinx.cinterop.memScoped
import kotlinx.cinterop.toKString
import kotlinx.cinterop.usePinned

/** 十六进制字符表（与 `sqlBlob` 用同一套，避免两处不一致）。 */
private val HEX_CHARS = "0123456789abcdef".toCharArray()

private fun bytesToHex(b: ByteArray): String {
    val sb = StringBuilder(b.size * 2)
    for (x in b) {
        val v = x.toInt() and 0xFF
        sb.append(HEX_CHARS[v ushr 4]).append(HEX_CHARS[v and 0xF])
    }
    return sb.toString()
}

private fun hexVal(c: Char): Int = when (c) {
    in '0'..'9' -> c - '0'
    in 'a'..'f' -> c - 'a' + 10
    in 'A'..'F' -> c - 'A' + 10
    else -> -1
}

private fun hexToBytes(s: String): ByteArray {
    if (s.length % 2 != 0) return ByteArray(0)
    val out = ByteArray(s.length / 2)
    for (i in out.indices) {
        val hi = hexVal(s[i * 2])
        val lo = hexVal(s[i * 2 + 1])
        if (hi < 0 || lo < 0) return ByteArray(0)
        out[i] = ((hi shl 4) or lo).toByte()
    }
    return out
}

/**
 * 文件型向量库。实现与 `RdbVectorStore` 完全相同的两个接口，可直接互换。
 *
 * @param filePath 存储文件路径（由 ArkTS 侧把 `filesDir` 传进来；见 `SekbApp.open`）。
 */
class FileVectorStore(
    private val filePath: String,
    override val space: EmbeddingSpace,
) : VectorStore, DocumentRegistry {

    private val chunks = LinkedHashMap<String, VectorRecord>()
    private val docs = LinkedHashMap<String, DocumentInfo>()

    /** 打开时发生了什么（与 `RdbVectorStore.openDiagnostics` 同格式，自检要报出来）。 */
    val openDiagnostics: String

    init {
        var loaded = 0
        var bad = 0
        val text = readTextOrNull(filePath)
        if (text == null) {
            openDiagnostics = "新建（文件不存在）$filePath"
        } else {
            for (line in text.split('\n')) {
                if (line.isEmpty()) continue
                val f = line.split('\t')
                try {
                    when {
                        f.size >= 7 && f[0] == "D" -> {
                            docs[f[1]] = DocumentInfo(
                                id = f[1], name = f[2],
                                sizeBytes = f[3].toLongOrNull() ?: 0L,
                                chunks = f[4].toIntOrNull() ?: 0,
                                addedAtMillis = f[5].toLongOrNull() ?: 0L,
                                deviceOnly = f[6] == "1",
                            )
                            loaded++
                        }
                        f.size >= 5 && f[0] == "C" -> {
                            chunks[f[1]] = VectorRecord(
                                id = f[1], sourceId = f[2],
                                text = unescape(f[3]),
                                vector = decodeVector(hexToBytes(f[4])),
                            )
                            loaded++
                        }
                        else -> bad++
                    }
                } catch (_: Throwable) {
                    bad++
                }
            }
            // ⚠️ 空间戳校验：与 `RdbVectorStore` 同口径——**换模型必须重算**，绝不混算余弦。
            //    文件里第一行约定是 `S\t<spaceId>`（没有就当成"旧格式/空库"，按当前空间认领）。
            openDiagnostics = "复用已有索引（文件）$filePath 载入 $loaded 条" +
                (if (bad > 0) "（⚠️ 跳过无法解析的行 $bad 条）" else "")
        }
        // 空间戳存在单独一行 `S\t<spaceId>`（与切片/文档同行式格式，简单）
        val storedSpace = text?.split('\n')?.firstOrNull { it.startsWith("S\t") }?.removePrefix("S\t")
        if (storedSpace != null && storedSpace != space.id) {
            error("索引的空间是 $storedSpace，当前嵌入模型是 ${space.id}：换模型必须**重算**（RFC §18.1）")
        }
    }

    // ── 内部：文本转义 / 读写 ────────────────────────────────────────────────

    /** 保证"一行一记录"：换行与制表符替换成可见占位（**显示时不影响检索**，只影响原文里的空白）。 */
    private fun escape(s: String): String =
        s.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")

    private fun unescape(s: String): String {
        val sb = StringBuilder(s.length)
        var i = 0
        while (i < s.length) {
            val c = s[i]
            if (c == '\\' && i + 1 < s.length) {
                when (s[i + 1]) {
                    'n' -> { sb.append('\n'); i += 2 }
                    'r' -> { sb.append('\r'); i += 2 }
                    't' -> { sb.append('\t'); i += 2 }
                    '\\' -> { sb.append('\\'); i += 2 }
                    else -> { sb.append(c); i++ }
                }
            } else {
                sb.append(c); i++
            }
        }
        return sb.toString()
    }

    /**
     * 读整个文件（不存在返回 null）。
     *
     * ⚠️ 实现细节（踩过）：这里**不用** `cp[i]` 这种下标取字节——在本文件里它会被解析到
     * `MatchGroupCollection.get(name)` 上（Kotlin/Native 的扩展解析候选里竟然包含正则那个），
     * 报出 `actual type is 'MatchGroup?'`。改用 `platform.posix.open/read` + `reinterpret<ByteVar>()`
     * 的显式循环，语义一样但完全没有扩展解析的歧义。
     */
    private fun readTextOrNull(path: String): String? {
        // ⚠️ 用**独立的 `ByteArray` 接收**，再 `usePinned` 取原生地址交给 `read`：
        //    这样**完全不碰 `cp[i]` 下标**——在本文件里那个写法会被解析到
        //    `MatchGroupCollection.get(name)`（Kotlin/Native 扩展候选里居然有正则那个），
        //    报出 `actual type is 'MatchGroup?'`，非常难查。经验：**在 Kotlin/Native 里
        //    碰原生缓冲，优先"Kotlin 数组 + usePinned + addressOf"，少用指针下标。**
        val fd = platform.posix.open(path, platform.posix.O_RDONLY, 0u)
        if (fd < 0) return null
        try {
            val sb = StringBuilder()
            val buf = ByteArray(8192)
            buf.usePinned { pinned ->
                while (true) {
                    val r = platform.posix.read(fd, pinned.addressOf(0), 8192u)
                    if (r <= 0L) break
                    sb.append(buf.decodeToString(0, r.toInt()))
                }
            }
            return sb.toString()
        } finally {
            platform.posix.close(fd)
        }
    }

    private var dirty = false

    /** 整库重写（先 `.tmp` 再 rename：中途崩溃不会留下半个库）。 */
    private fun save() {
        if (!dirty) return
        val sb = StringBuilder()
        sb.append("S\t").append(space.id).append('\n')
        for (d in docs.values) {
            sb.append("D\t").append(d.id).append('\t').append(d.name).append('\t')
                .append(d.sizeBytes).append('\t').append(d.chunks).append('\t')
                .append(d.addedAtMillis).append('\t').append(if (d.deviceOnly) "1" else "0").append('\n')
        }
        for (c in chunks.values) {
            sb.append("C\t").append(c.id).append('\t').append(c.sourceId).append('\t')
                .append(escape(c.text)).append('\t').append(bytesToHex(encodeVector(c.vector))).append('\n')
        }
        val tmp = "$filePath.tmp"
        writeText(tmp, sb.toString())
        // rename 覆盖（同目录，POSIX rename 是原子的）
        platform.posix.rename(tmp, filePath)
        dirty = false
    }

    private fun writeText(path: String, text: String) {
        val bytes = text.encodeToByteArray()
        val fd = platform.posix.open(path, platform.posix.O_WRONLY or platform.posix.O_CREAT or platform.posix.O_TRUNC, 0x1A4u)
        if (fd < 0) error("无法写入 $path（open 返回 $fd）")
        try {
            bytes.usePinned { pinned ->
                var off = 0
                while (off < bytes.size) {
                    val wr = platform.posix.write(
                        fd,
                        pinned.addressOf(off),
                        (bytes.size - off).toULong(),
                    )
                    if (wr <= 0L) error("写 $path 失败（write 返回 $wr，已写 $off/${bytes.size}）")
                    off += wr.toInt()
                }
            }
        } finally {
            platform.posix.close(fd)
        }
    }

    // ── VectorStore ─────────────────────────────────────────────────────────

    override fun upsert(records: List<VectorRecord>) {
        if (records.isEmpty()) return
        for (r in records) {
            require(r.vector.size == space.dim) {
                "向量维度不符：期望 ${space.dim}，实际 ${r.vector.size}（换了嵌入模型就必须重建索引）"
            }
            chunks[r.id] = r
        }
        dirty = true
        save()
    }

    override fun deleteBySource(sourceId: String): Int {
        val doomed = chunks.values.filter { it.sourceId == sourceId }.map { it.id }
        for (id in doomed) chunks.remove(id)
        if (doomed.isNotEmpty()) {
            dirty = true
            save()
        }
        return doomed.size
    }

    override fun search(query: FloatArray, topK: Int): List<VectorHit> {
        if (query.size != space.dim) return emptyList()
        val records = chunks.values.toList()
        val byId = records.associateBy { it.id }
        return VectorMath.topK(query, records.map { it.id to it.vector }, topK).mapNotNull { (id, score) ->
            byId[id]?.let { VectorHit(it.id, it.sourceId, it.text, score) }
        }
    }

    override fun size(): Int = chunks.size

    override fun clear() {
        chunks.clear()
        docs.clear()
        dirty = true
        save()
    }

    override fun all(): List<VectorRecord> = chunks.values.sortedBy { it.id }

    // ── DocumentRegistry ────────────────────────────────────────────────────

    override fun upsert(info: DocumentInfo) {
        docs[info.id] = info
        dirty = true
        save()
    }

    override fun list(): List<DocumentInfo> = docs.values.sortedByDescending { it.addedAtMillis }

    override fun get(id: String): DocumentInfo? = docs[id]

    override fun delete(id: String): Boolean {
        val had = docs.remove(id) != null
        if (had) {
            dirty = true
            save()
        }
        return had
    }
}
