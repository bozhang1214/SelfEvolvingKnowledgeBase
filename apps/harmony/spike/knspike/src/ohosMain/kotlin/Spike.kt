// `@CName` 在这个 Kotlin 版本上需要显式 opt-in（实测报错，不是"可选"）
@file:OptIn(kotlin.experimental.ExperimentalNativeApi::class)

package com.sekb.ohos.spike

/**
 * M6 spike 的最小导出符号。
 *
 * 目的不是"功能"，而是证明**三件事同时成立**：
 * 1. KMP 能在本机编出 ohos arm64 的动态库（`libkn.so`）；
 * 2. `@CName` 导出的是稳定的 C 符号（HAP 侧用 NAPI 按名字拿它）；
 * 3. 编译进来的共享逻辑（这里用 kotlinx 的序列化/协程各一个符号当代表）能一起进产物。
 */
@CName("sekb_spike_ping")
fun sekbSpikePing(): Int = 42

/** 用一下 kotlinx-serialization，确保它的 ohos 变体真的参与编译与链接。 */
@CName("sekb_spike_echo_len")
fun sekbSpikeEchoLen(text: String): Int = text.length
