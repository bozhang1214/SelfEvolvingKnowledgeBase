package com.sekb.ondevice.ui

import android.content.Context
import android.net.Uri
import android.provider.OpenableColumns
import java.io.ByteArrayOutputStream

/**
 * 本机文档导入（M3 第二批）。
 *
 * **范围**：纯文本类（.txt/.md/.json/.csv/.log 等）。PDF/Word **明确不在本期**——
 * 那需要额外解析库，而且解析质量直接决定 RAG 质量，值得单独做一批（见 BACKLOG）。
 * 遇到二进制文件如实拒绝，而不是把乱码灌进索引（脏索引比空索引更糟：
 * 检索会命中一堆乱码，用户还以为"AI 乱答"）。
 */
object DocumentImporter {

    /** 单文件上限：端侧索引是"小而准"，塞进超大文件只会拖慢嵌入且几乎必然切得很碎。 */
    const val MAX_BYTES = 2 * 1024 * 1024

    sealed interface Result {
        data class Ok(val name: String, val text: String, val sizeBytes: Long) : Result
        data class Rejected(val name: String, val reason: String) : Result
    }

    fun read(context: Context, uri: Uri): Result {
        val name = displayName(context, uri)
        val bytes = try {
            context.contentResolver.openInputStream(uri)?.use { input ->
                val buf = ByteArrayOutputStream()
                val chunk = ByteArray(64 * 1024)
                var total = 0L
                while (true) {
                    val n = input.read(chunk)
                    if (n <= 0) break
                    total += n
                    if (total > MAX_BYTES) {
                        return Result.Rejected(name, "文件超过 ${MAX_BYTES / 1024 / 1024}MB 上限")
                    }
                    buf.write(chunk, 0, n)
                }
                buf.toByteArray()
            } ?: return Result.Rejected(name, "无法读取该文件")
        } catch (e: Exception) {
            return Result.Rejected(name, "读取失败：${e.message?.take(60) ?: "未知原因"}")
        }
        if (looksBinary(bytes)) {
            return Result.Rejected(name, "看起来是二进制/压缩格式（如 PDF、Word），本期只支持纯文本")
        }
        return Result.Ok(name, String(bytes, Charsets.UTF_8), bytes.size.toLong())
    }

    /**
     * 二进制判定（纯函数，便于单测）。
     *
     * 判据：前 8KB 里出现 NUL 字节，或不可打印字符占比超过 10%。
     * 为什么不用扩展名：用户从聊天软件存的文件经常没有扩展名，而内容判断更可靠。
     */
    fun looksBinary(bytes: ByteArray): Boolean {
        if (bytes.isEmpty()) return false
        val head = bytes.take(8192)
        if (head.any { it == 0.toByte() }) return true
        var weird = 0
        for (b in head) {
            val v = b.toInt() and 0xFF
            val printable = v == 9 || v == 10 || v == 13 || (v in 32..126) || v >= 0x80  // >=0x80 交给 UTF-8 解码
            if (!printable) weird++
        }
        return weird.toDouble() / head.size > 0.10
    }

    /** 取展示名（SAF 的 DISPLAY_NAME 列；取不到就用路径末段）。 */
    fun displayName(context: Context, uri: Uri): String = try {
        context.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)
            ?.use { c -> if (c.moveToFirst()) c.getString(0) else null }
            ?: uri.lastPathSegment ?: "未命名"
    } catch (e: Exception) {
        uri.lastPathSegment ?: "未命名"
    }
}
