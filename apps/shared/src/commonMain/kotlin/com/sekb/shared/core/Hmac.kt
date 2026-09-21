package com.sekb.shared.core

/**
 * HMAC-SHA256（十六进制小写）——端侧**验签**用。
 *
 * 为什么是端口（expect/actual）：Kotlin common 没有加密库，而"验签"必须在端侧本地做
 * （把策略发回服务端验签等于没验）。各端都用系统实现：
 * Android/JVM → `javax.crypto.Mac`；Apple（iOS/Mac）→ `CommonCrypto` 的 `CCHmac`。
 *
 * **不要**用自己写的哈希：策略下发是安全边界（能改端侧阈值/白名单），
 * 一旦实现有偏差，"验签通过"就失去意义。系统实现 + 官方测试向量（RFC 4231）才对得起这条边界。
 */
expect fun hmacSha256Hex(key: String, message: String): String

/**
 * SHA-256（十六进制小写）。
 *
 * 用途只有一个：**灰度分桶**（`sha256(deviceId + ":" + salt) % 100 < percent`）。
 * 必须与服务端 `edge_policy.applies_to_device` 用同一种哈希——否则"同一台设备在服务端算命中、
 * 在端侧算不命中"，灰度就变成了掷骰子。（不能用 Kotlin 内置 `hashCode`：跨进程/跨语言不稳定。）
 */
expect fun sha256Hex(message: String): String
