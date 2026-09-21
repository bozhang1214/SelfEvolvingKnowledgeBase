package com.sekb.shared.core

import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/** Android/JVM 实现：`javax.crypto.Mac`（HmacSHA256）。 */
actual fun hmacSha256Hex(key: String, message: String): String {
    val mac = Mac.getInstance("HmacSHA256")
    mac.init(SecretKeySpec(key.toByteArray(Charsets.UTF_8), "HmacSHA256"))
    val digest = mac.doFinal(message.toByteArray(Charsets.UTF_8))
    val hex = StringBuilder(digest.size * 2)
    for (b in digest) {
        val v = b.toInt() and 0xFF
        hex.append(HEX_CHARS.elementAt(v ushr 4))
        hex.append(HEX_CHARS.elementAt(v and 0x0F))
    }
    return hex.toString()
}

private const val HEX_CHARS = "0123456789abcdef"

/** Android/JVM 实现：`MessageDigest("SHA-256")`。 */
actual fun sha256Hex(message: String): String {
    val digest = java.security.MessageDigest.getInstance("SHA-256")
        .digest(message.toByteArray(Charsets.UTF_8))
    val hex = StringBuilder(digest.size * 2)
    for (b in digest) {
        val v = b.toInt() and 0xFF
        hex.append(HEX_CHARS.elementAt(v ushr 4))
        hex.append(HEX_CHARS.elementAt(v and 0x0F))
    }
    return hex.toString()
}
