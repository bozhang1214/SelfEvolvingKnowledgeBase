package com.sekb.ondevice.ui

import com.tom_roush.pdfbox.pdmodel.PDDocument
import com.tom_roush.pdfbox.pdmodel.encryption.InvalidPasswordException
import com.tom_roush.pdfbox.text.PDFTextStripper

/**
 * PDF 文本抽取结果。
 *
 * 把"失败"分成几类而不是一个布尔：用户需要的不是"导入失败"，而是**为什么**——
 * 扫描件（没有文本层）该去 OCR，加密件该去解密，损坏件该重新导出。
 * 端侧尤其如此：没有服务端兜底，界面必须把原因说清楚。
 */
sealed interface PdfExtraction {
    /** 抽到的文本（可能来自多页，已按页拼接）。 */
    data class Text(val text: String, val pages: Int) : PdfExtraction

    /** PDF 有效但**没有文本层**（扫描件/纯图片页）——本期不做 OCR。 */
    data class NoTextLayer(val pages: Int) : PdfExtraction

    /** 加密（需要密码）。 */
    data class Encrypted(val detail: String) : PdfExtraction

    /** 解析失败（损坏、非 PDF、内存不足等）。 */
    data class Failed(val detail: String) : PdfExtraction
}

/** 抽取器接口：Android 用 PdfBox；测试注入假实现，从而在 JVM 里验证分类逻辑。 */
fun interface PdfExtractor {
    fun extract(bytes: ByteArray): PdfExtraction
}

/**
 * PdfBox-Android 实现（Apache-2.0）。
 *
 * 只做**文本层抽取**，不做 OCR：扫描件的正确做法是"如实告知需要 OCR"，而不是
 * 硬塞图片或空内容进索引（脏索引/空索引都会让用户以为"AI 乱答"）。
 */
object PdfBoxExtractor : PdfExtractor {

    override fun extract(bytes: ByteArray): PdfExtraction {
        var doc: PDDocument? = null
        return try {
            doc = PDDocument.load(bytes)
            @Suppress("DEPRECATION")
            val stripper = PDFTextStripper()
            val text = stripper.getText(doc).trim()
            if (text.length < MIN_USEFUL_CHARS) {
                PdfExtraction.NoTextLayer(doc.numberOfPages)
            } else {
                PdfExtraction.Text(text, doc.numberOfPages)
            }
        } catch (e: InvalidPasswordException) {
            PdfExtraction.Encrypted(e.message?.take(80) ?: "需要密码")
        } catch (e: OutOfMemoryError) {
            PdfExtraction.Failed("文件过大导致内存不足")
        } catch (e: Throwable) {
            // 捕获 Throwable 而不是 Exception：PdfBox 资源没初始化时抛的是
            // `ExceptionInInitializerError`（Error），只 catch Exception 会让它穿透出去
            // 把整个线程干掉（自检实测踩到），而界面需要的是"能显示的原因"。
            PdfExtraction.Failed("${e.javaClass.simpleName}: ${e.message?.take(80) ?: "无详情"}")
        } finally {
            runCatching { doc?.close() }
        }
    }

    /** 少于这个字符数就当成"没有文本层"：几十个字符多半是页眉/页码，问答用不上。 */
    const val MIN_USEFUL_CHARS = 20
}
