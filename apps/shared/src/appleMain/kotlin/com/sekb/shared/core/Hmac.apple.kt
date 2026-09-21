package com.sekb.shared.core

import kotlinx.cinterop.ExperimentalForeignApi
import kotlinx.cinterop.addressOf
import kotlinx.cinterop.convert
import kotlinx.cinterop.reinterpret
import kotlinx.cinterop.usePinned
import platform.CoreCrypto.CCHmac
import platform.CoreCrypto.CC_SHA256
import platform.CoreCrypto.kCCHmacAlgSHA256

/**
 * Apple（iOS / Mac）实现：CommonCrypto 的 `CCHmac`。
 *
 * 实现细节（踩过）：**不要**用 `allocArray<UByteVar>` 再按索引读——`CPointer[i]` 需要额外导入
 * `kotlinx.cinterop.get`，容易写出"看起来对、编不过"的代码。直接给三个 `ByteArray` 打 pin、
 * 把地址交给 C，既没有指针算术，也不用碰 cinterop 的取值操作符。
 */
@OptIn(ExperimentalForeignApi::class)
actual fun hmacSha256Hex(key: String, message: String): String {
    val keyBytes = key.encodeToByteArray()
    val msgBytes = message.encodeToByteArray()
    val out = ByteArray(32)
    keyBytes.usePinned { k ->
        msgBytes.usePinned { m ->
            out.usePinned { o ->
                CCHmac(
                    kCCHmacAlgSHA256,
                    k.addressOf(0), keyBytes.size.convert(),
                    m.addressOf(0), msgBytes.size.convert(),
                    o.addressOf(0),
                )
            }
        }
    }
    val hex = StringBuilder(64)
    for (b in out) {
        val v = b.toInt() and 0xFF
        hex.append(HEX_CHARS.elementAt(v ushr 4))
        hex.append(HEX_CHARS.elementAt(v and 0x0F))
    }
    return hex.toString()
}

private const val HEX_CHARS = "0123456789abcdef"

/** Apple 实现：CommonCrypto 的 `CC_SHA256`。 */
@OptIn(ExperimentalForeignApi::class)
actual fun sha256Hex(message: String): String {
    val bytes = message.encodeToByteArray()
    val out = ByteArray(32)
    bytes.usePinned { m ->
        out.usePinned { o ->
            // CC_SHA256 的参数是**无符号**指针（`CValuesRef<UByteVar>`），
            // 而 ByteArray 的地址是 `CPointer<ByteVar>` → 必须 reinterpret（实测报 type mismatch）
            CC_SHA256(
                m.addressOf(0).reinterpret<kotlinx.cinterop.UByteVar>(),
                bytes.size.convert(),
                o.addressOf(0).reinterpret<kotlinx.cinterop.UByteVar>(),
            )
        }
    }
    val hex = StringBuilder(64)
    for (b in out) {
        val v = b.toInt() and 0xFF
        hex.append(HEX_CHARS.elementAt(v ushr 4))
        hex.append(HEX_CHARS.elementAt(v and 0x0F))
    }
    return hex.toString()
}
