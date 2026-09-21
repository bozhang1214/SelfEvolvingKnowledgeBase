package com.sekb.shared.device

import com.sekb.shared.model.DeviceCredentials

/**
 * 凭证的**明文编解码**（纯函数，便于单测；**两端共用**）。
 *
 * 为什么从 Android 模块搬到共享层（M5 端口②，2026-09-21）：iOS 的 Keychain 存储也要编解码，
 * 而这段逻辑与平台无关——留在 app 模块就等于逼 iOS 重写一份，迟早两边格式不一致。
 *
 * 用最简的分隔格式而不是 JSON：这里只有四个字段，且**不能**因为 JSON 转义问题
 * 把 token 弄坏（token 里可能含 `.` `-` `_`，用 `\u0001` 分隔最省心）。
 */
object CredentialCodec {

    private const val SEP = "\u0001"

    fun encode(c: DeviceCredentials): String = listOf(
        c.deviceId, c.deviceToken, c.issuedAtMillis.toString(), c.expiresAtMillis.toString(),
    ).joinToString(SEP)

    fun decode(plain: String): DeviceCredentials? {
        val parts = plain.split(SEP)
        if (parts.size != 4) return null
        val issued = parts[2].toLongOrNull() ?: return null
        val expires = parts[3].toLongOrNull() ?: return null
        if (parts[0].isBlank() || parts[1].isBlank()) return null
        return DeviceCredentials(parts[0], parts[1], issued, expires)
    }
}
