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

    /** 纯文本单文件上限：端侧索引是"小而准"，塞进超大文件只会拖慢嵌入且几乎必然切得很碎。 */
    const val MAX_BYTES = 2 * 1024 * 1024

    /** PDF 单文件上限（PDF 是容器，体积天然更大；限制的是**原始文件**而不是抽取后的文本）。 */
    const val MAX_PDF_BYTES = 20 * 1024 * 1024

    /** 抽取后的文本上限：防止一个 PDF 抽出几 MB 文本把索引灌爆。 */
    const val MAX_EXTRACTED_CHARS = 400_000

    sealed interface Result {
        data class Ok(val name: String, val text: String, val sizeBytes: Long) : Result
        data class Rejected(val name: String, val reason: String) : Result
    }

    fun read(context: Context, uri: Uri, pdf: PdfExtractor = PdfBoxExtractor): Result {
        val name = displayName(context, uri)
        val limit = if (looksLikePdfName(name)) MAX_PDF_BYTES else MAX_BYTES
        val bytes = try {
            context.contentResolver.openInputStream(uri)?.use { input ->
                val buf = ByteArrayOutputStream()
                val chunk = ByteArray(64 * 1024)
                var total = 0L
                while (true) {
                    val n = input.read(chunk)
                    if (n <= 0) break
                    total += n
                    if (total > limit) {
                        return Result.Rejected(name, "文件超过 ${limit / 1024 / 1024}MB 上限")
                    }
                    buf.write(chunk, 0, n)
                }
                buf.toByteArray()
            } ?: return Result.Rejected(name, "无法读取该文件")
        } catch (e: Exception) {
            return Result.Rejected(name, "读取失败：${e.message?.take(60) ?: "未知原因"}")
        }
        return decide(bytes, name, pdf)
    }

    /**
     * 纯判定：字节 → 导入结果（不碰 Android API，便于单测）。
     *
     * 分支顺序有意义：**先认 PDF**（PDF 里必然有二进制字节，先跑二进制判定会把所有 PDF 拒掉）。
     */
    fun decide(bytes: ByteArray, name: String, pdf: PdfExtractor): Result {
        if (isPdf(bytes)) {
            return when (val r = pdf.extract(bytes)) {
                is PdfExtraction.Text -> {
                    val text = if (r.text.length > MAX_EXTRACTED_CHARS) {
                        r.text.take(MAX_EXTRACTED_CHARS)
                    } else {
                        r.text
                    }
                    Result.Ok(name, text, bytes.size.toLong())
                }
                is PdfExtraction.NoTextLayer ->
                    Result.Rejected(name, "这份 PDF 没有文本层（${r.pages} 页，多半是扫描件）；" +
                        "本期不做 OCR，请先用工具转成带文本的 PDF")
                is PdfExtraction.Encrypted -> Result.Rejected(name, "PDF 已加密，需要密码：${r.detail}")
                is PdfExtraction.Failed -> Result.Rejected(name, "PDF 解析失败：${r.detail}")
            }
        }
        if (looksBinary(bytes)) {
            return Result.Rejected(name, "看起来是二进制/压缩格式（本期支持纯文本与 PDF；" +
                "Word/Excel 见 BACKLOG）")
        }
        return Result.Ok(name, String(bytes, Charsets.UTF_8), bytes.size.toLong())
    }

    /** PDF 判定看**魔数**（`%PDF`），不看扩展名：用户从聊天软件存的文件常常没有扩展名。 */
    fun isPdf(bytes: ByteArray): Boolean =
        bytes.size >= 4 && bytes[0] == 0x25.toByte() && bytes[1] == 0x50.toByte() &&
            bytes[2] == 0x44.toByte() && bytes[3] == 0x46.toByte()

    private fun looksLikePdfName(name: String): Boolean = name.lowercase().endsWith(".pdf")

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
