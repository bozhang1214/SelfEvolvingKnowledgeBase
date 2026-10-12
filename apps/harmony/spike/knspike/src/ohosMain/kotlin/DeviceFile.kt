// Kotlin/Native 侧的**文件读写**（POSIX `fopen/fread/fwrite`）。
//
// 为什么不用 okio/kotlinx-io：本 spike 构建里**没有**这两个依赖，而凭证只有几百字节、
// 读写各一次；引一个依赖只为两次系统调用不划算（也少一个 ohos 目标的支持面要确认）。
//
// ⚠️ 路径来自 ArkTS 的 `context.filesDir`（应用私有目录，随进程结束而**保留**）——
// 这正是"重启后凭证仍有效"这条验收要用的落地位置。Kotlin 侧不猜路径，由调用方传进来。
@file:OptIn(kotlinx.cinterop.ExperimentalForeignApi::class)

package com.sekb.ohos.spike

import kotlinx.cinterop.UByteVar
import kotlinx.cinterop.allocArray
import kotlinx.cinterop.get
import kotlinx.cinterop.memScoped
import kotlinx.cinterop.set
import platform.posix.fclose
import platform.posix.fopen
import platform.posix.fread
import platform.posix.fwrite
import platform.posix.remove

private const val MAX_BYTES = 1 shl 20

/** 读整个文件；不存在或读失败返回 null。 */
fun readFileBytes(path: String): ByteArray? = memScoped {
    val fp = fopen(path, "rb") ?: return@memScoped null
    try {
        val buf = allocArray<UByteVar>(MAX_BYTES)
        val n = fread(buf, 1uL, MAX_BYTES.toULong(), fp).toInt()
        if (n <= 0) return@memScoped ByteArray(0)
        ByteArray(n) { i -> buf[i].toInt().toByte() }
    } finally {
        fclose(fp)
    }
}

/** 写整个文件并关闭。返回是否写满。 */
fun writeFileBytes(path: String, bytes: ByteArray): Boolean = memScoped {
    val fp = fopen(path, "wb") ?: return@memScoped false
    try {
        if (bytes.isEmpty()) return@memScoped true
        val buf = allocArray<UByteVar>(bytes.size)
        bytes.forEachIndexed { i, b -> buf[i] = b.toUByte() }
        fwrite(buf, 1uL, bytes.size.toULong(), fp).toInt() == bytes.size
    } finally {
        fclose(fp)
    }
}

/** 删文件（`clear()` 用）。文件本来不存在也算成功。 */
fun deleteFile(path: String): Boolean = platform.posix.remove(path) == 0
