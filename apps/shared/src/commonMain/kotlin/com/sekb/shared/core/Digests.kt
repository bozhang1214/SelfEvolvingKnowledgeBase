package com.sekb.shared.core

/**
 * 摘要/签名入口（给 iOS/Mac 的 Swift 侧一个**确定的名字**）。
 *
 * 为什么包一层：Kotlin/Native 导出顶层函数时，Swift 里看到的是 `<文件名>Kt.hmacSha256Hex(...)`——
 * 文件名一变、类名就变。包成 `object` 后 Swift 侧稳定地写 `Digests.shared.hmacSha256Hex(...)`，
 * 不会因为"把 expect 挪到哪个文件"而破坏调用方。
 */
object Digests {

    fun hmacSha256Hex(key: String, message: String): String =
        com.sekb.shared.core.hmacSha256Hex(key, message)

    fun sha256Hex(message: String): String =
        com.sekb.shared.core.sha256Hex(message)
}
