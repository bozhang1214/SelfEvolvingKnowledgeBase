package com.sekb.shared.core

import kotlin.random.Random

/**
 * ID 生成（M4 第 2 步引入端口，第 5 步去平台化）。
 *
 * **为什么不用 `java.util.UUID`**：那是 JVM 专有，`commonMain` 编不过。
 * 而这里生成的只是**端侧本地标识**（会话、文档、路由事件的 id），
 * 不参与任何安全判定（设备凭证是另一条路：Keystore/Keychain 加密存储），
 * 所以用 `kotlin.random.Random` 在纯 Kotlin 里生成 32 位十六进制即可——
 * 零平台依赖，四个端行为一致，也不需要 expect/actual。
 */
object Ids {

    private const val HEX = "0123456789abcdef"

    /** 32 位十六进制（等于 UUID 去横线后的形状，便于日志/协议沿用原格式）。 */
    fun random(): String = buildString(32) {
        repeat(16) {
            val b = Random.nextInt(256)
            append(HEX[b shr 4])
            append(HEX[b and 0x0F])
        }
    }

    /** 前 [take] 位（默认 12）——日志里够用又不至于太长。 */
    fun short(take: Int = 12): String = random().take(take)
}
