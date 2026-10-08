package com.sekb.shared.core

import com.sekb.ohos.Sha256Pure

/**
 * 鸿蒙（ohosArm64）实现：**纯 Kotlin SHA-256 / HMAC-SHA256**。
 *
 * 为什么不用平台加密 API、以及正确性怎么被验证，见 `com.sekb.ohos.Sha256Pure` 的类注释
 * （那里记着两条实测事实与选型理由，别在这里重复，也别随手改成 `OH_CryptoMac_*`）。
 */
actual fun hmacSha256Hex(key: String, message: String): String =
    Sha256Pure.toHex(
        Sha256Pure.hmac(key.encodeToByteArray(), message.encodeToByteArray()),
    )

actual fun sha256Hex(message: String): String =
    Sha256Pure.toHex(Sha256Pure.hash(message.encodeToByteArray()))
