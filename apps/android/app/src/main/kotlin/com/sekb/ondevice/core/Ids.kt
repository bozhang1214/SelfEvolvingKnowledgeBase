package com.sekb.ondevice.core

import java.util.UUID

/**
 * ID 生成端口（M4 第 2 步）。
 *
 * 纯逻辑里出现 `java.util.UUID` 会让 commonMain 编不过；而"生成一个随机 ID"这件事
 * 每端都有现成能力（JVM `UUID`、iOS `NSUUID`、鸿蒙 `@ohos.util` 的 util.generateRandomUUID）。
 * 所以收在这里，调用点只认 `Ids.random()`。
 */
object Ids {

    /** 32 位十六进制（去横线），用于会话/文档/事件 ID。 */
    fun random(): String = UUID.randomUUID().toString().replace("-", "")

    /** 前 [take] 位（默认 12）——日志里够用又不至于太长。 */
    fun short(take: Int = 12): String = random().take(take)
}
