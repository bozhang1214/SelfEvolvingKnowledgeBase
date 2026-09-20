package com.sekb.ondevice.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * PDF 导入的**判定逻辑**（纯函数 + 注入的假抽取器）。
 *
 * 真正的 PdfBox 抽取在设备上验（自检 `rag_import_pdf`）——那是 Android 库，
 * 放 JVM 单测里跑会因为缺 Android 运行时而不稳。这里覆盖的是**分类与分支顺序**。
 */
class PdfImportTest {

    private val textPdf = File("src/test/resources/sample-text.pdf").readBytes()
    private val noTextPdf = File("src/test/resources/sample-no-text.pdf").readBytes()

    private fun extractor(result: PdfExtraction) = PdfExtractor { result }

    private val okExtractor = extractor(PdfExtraction.Text("抽取到的正文内容，足够长以便通过阈值。", 3))

    @Test
    fun `fixtures are real pdfs`() {
        assertTrue(DocumentImporter.isPdf(textPdf))
        assertTrue(DocumentImporter.isPdf(noTextPdf))
        assertTrue(textPdf.size < 2000)          // 手写的最小 PDF，体积很小
    }

    @Test
    fun `pdf magic wins over the binary check`() {
        // 回归：PDF 里必然有二进制字节（嵌入字体/图片流）。若先跑"二进制判定"，所有 PDF 都会被拒。
        // 夹具里特意放了一个二进制流对象，所以这条用的是**真实文件**。
        assertTrue("夹具应含二进制字节，否则测不到分支顺序", DocumentImporter.looksBinary(textPdf))
        assertTrue(DocumentImporter.decide(textPdf, "报告.pdf", okExtractor) is DocumentImporter.Result.Ok)

        // 再用合成数据把顺序钉死（不依赖夹具内部结构）
        val pdfLike = "%PDF-1.4\n".toByteArray() + ByteArray(64) { 0 }
        assertTrue(DocumentImporter.looksBinary(pdfLike))
        assertTrue(DocumentImporter.decide(pdfLike, "x.pdf", okExtractor) is DocumentImporter.Result.Ok)
    }

    @Test
    fun `text layer is imported`() {
        val r = DocumentImporter.decide(textPdf, "报告.pdf", okExtractor) as DocumentImporter.Result.Ok
        assertEquals("报告.pdf", r.name)
        assertTrue(r.text.contains("正文"))
        assertEquals(textPdf.size.toLong(), r.sizeBytes)
    }

    @Test
    fun `scanned pdf is rejected with an actionable reason`() {
        val r = DocumentImporter.decide(
            noTextPdf, "扫描件.pdf", extractor(PdfExtraction.NoTextLayer(pages = 5)),
        )
        assertTrue(r is DocumentImporter.Result.Rejected)
        val reason = (r as DocumentImporter.Result.Rejected).reason
        assertTrue("应说明是扫描件：$reason", reason.contains("扫描件"))
        assertTrue("应说明本期不做 OCR：$reason", reason.contains("OCR"))
        assertTrue("应带上页数：$reason", reason.contains("5"))
    }

    @Test
    fun `encrypted pdf says it needs a password`() {
        val r = DocumentImporter.decide(
            textPdf, "加密.pdf", extractor(PdfExtraction.Encrypted("password required")),
        ) as DocumentImporter.Result.Rejected
        assertTrue(r.reason.contains("加密"))
        assertTrue(r.reason.contains("password required"))
    }

    @Test
    fun `failed parse is reported as failure not as empty text`() {
        val r = DocumentImporter.decide(
            textPdf, "坏.pdf", extractor(PdfExtraction.Failed("xref missing")),
        ) as DocumentImporter.Result.Rejected
        assertTrue(r.reason.contains("解析失败"))
    }

    @Test
    fun `extracted text is capped`() {
        val huge = PdfExtraction.Text("字".repeat(DocumentImporter.MAX_EXTRACTED_CHARS + 5000), 100)
        val r = DocumentImporter.decide(textPdf, "大.pdf", extractor(huge)) as DocumentImporter.Result.Ok
        assertEquals(DocumentImporter.MAX_EXTRACTED_CHARS, r.text.length)
    }

    @Test
    fun `plain text still works and binary is still rejected`() {
        val text = "端侧 RAG 把索引放在设备上。".toByteArray()
        assertTrue(DocumentImporter.decide(text, "笔记.md", okExtractor) is DocumentImporter.Result.Ok)
        val binary = ByteArray(64) { 0x00 }
        assertTrue(DocumentImporter.decide(binary, "x.bin", okExtractor) is DocumentImporter.Result.Rejected)
    }

    @Test
    fun `pdf size limit is larger than the text limit`() {
        assertTrue(DocumentImporter.MAX_PDF_BYTES > DocumentImporter.MAX_BYTES)
    }

    @Test
    fun `pdf detection does not fire on other content`() {
        assertFalse(DocumentImporter.isPdf("PDF".toByteArray()))
        assertFalse(DocumentImporter.isPdf("hello".toByteArray()))
        assertFalse(DocumentImporter.isPdf(ByteArray(0)))
    }
}
