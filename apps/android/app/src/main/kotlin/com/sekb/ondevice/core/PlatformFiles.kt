package com.sekb.ondevice.core

import java.io.File
import com.sekb.shared.core.*

/**
 * 文件读取端口（M4 第 2 步：把 `java.io.File` 从纯逻辑里赶出来）。
 *
 * **为什么要有这一层**：抽 KMP `shared/` 时 `commonMain` 里不能出现 `java.io.File`。
 * 需要读文件的地方其实只有两处（词表、模型目录探测），把"读"收在这里，
 * 其余逻辑只接字符串/字节。
 *
 * 将来 `shared/` 落地时改成 `expect object PlatformFiles`：
 *   Android/JVM → 本文件（`File`）；iOS → `NSFileManager` + `NSString(contentsOfFile:)`；
 *   鸿蒙 → `@ohos.file.fs`（经 NAPI）。
 */
object PlatformFiles {

    /** 读文本（UTF-8）。失败抛异常——调用方负责决定"读不到"怎么降级。 */
    fun readText(path: String): String = File(path).readText(Charsets.UTF_8)

    /** 读字节（模型文件）。 */
    fun readBytes(path: String): ByteArray = File(path).readBytes()

    fun exists(path: String): Boolean = File(path).exists()

    fun isDirectory(path: String): Boolean = File(path).isDirectory

    /** 目录下的文件名（不递归）。 */
    fun listNames(dir: String): List<String> = File(dir).list()?.toList() ?: emptyList()
}
