package com.sekb.shared.core

import platform.posix.time
import kotlinx.cinterop.ExperimentalForeignApi

/**
 * 鸿蒙（ohosArm64）实现：墙壁时钟（毫秒）。
 *
 * 用 libc 的 `time()`（秒）而不是 `gettimeofday`：只需要毫秒级且**不要求高精度**——
 * 这个函数只是 `now: () -> Long` 的默认值，真正要求精度的地方（TTFT 计时）走
 * `kotlin.system.getTimeNanos()` 之类的单调时钟，与墙上时钟无关。
 *
 * ⚠️ `time_t` 在 64 位 OHOS 上是 64 位；`* 1000` 不会溢出（Long）。
 */
@OptIn(ExperimentalForeignApi::class)
actual fun nowMillis(): Long = time(null) * 1000L
