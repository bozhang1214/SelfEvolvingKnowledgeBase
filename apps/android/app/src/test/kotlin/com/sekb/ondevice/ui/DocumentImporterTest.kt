package com.sekb.ondevice.ui

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** 导入的**判定逻辑**（纯函数）；真正的读取走 Android ContentResolver，只能在设备上验。 */
class DocumentImporterTest {

    @Test
    fun `plain utf8 text is not binary`() {
        assertFalse(DocumentImporter.looksBinary("端侧推理为什么省电。\nHello world!".toByteArray()))
    }

    @Test
    fun `empty content is not binary`() {
        assertFalse(DocumentImporter.looksBinary(ByteArray(0)))
    }

    @Test
    fun `nul byte means binary`() {
        // PDF 的头部就是 "%PDF-" 后面跟二进制流，里面必然有 NUL
        assertTrue(DocumentImporter.looksBinary(byteArrayOf(0x25, 0x50, 0x44, 0x46, 0x00, 0x01)))
    }

    @Test
    fun `mostly control characters means binary`() {
        val bytes = ByteArray(100) { if (it < 30) 0x01 else 'a'.code.toByte() }
        assertTrue(DocumentImporter.looksBinary(bytes))
    }

    @Test
    fun `a few stray control characters are still text`() {
        val bytes = ByteArray(1000) { if (it < 50) 0x01 else 'a'.code.toByte() }   // 5%
        assertFalse(DocumentImporter.looksBinary(bytes))
    }

    @Test
    fun `utf8 multibyte is not treated as binary`() {
        // 中文的 UTF-8 字节都 >= 0x80，不能被判成二进制（否则中文文档全被拒）
        assertFalse(DocumentImporter.looksBinary("端侧 RAG 把知识索引放在设备上".toByteArray()))
    }

    @Test
    fun `size cap is 2MB`() {
        assertTrue(DocumentImporter.MAX_BYTES == 2 * 1024 * 1024)
    }
}
