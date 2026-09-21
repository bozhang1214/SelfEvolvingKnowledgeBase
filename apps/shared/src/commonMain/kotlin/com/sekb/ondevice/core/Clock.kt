package com.sekb.ondevice.core

/**
 * 当前墙上时钟（毫秒）。
 *
 * **为什么是 expect/actual 而不是直接调 `System.currentTimeMillis()`**：
 * 后者是 JVM/Android 专有，`commonMain` 编不过——这一点是 **iOS 编译器**帮我发现的
 * （建 KMP 模块的价值就在这里：平台泄漏不再靠人眼找，编译不过就是不过）。
 *
 * 各端实现：Android/JVM → `System.currentTimeMillis()`；Apple（iOS/Mac）→ `NSDate`。
 * 需要"可注入的时间"的地方（路由事件、审计、评测）仍然走构造参数 `now: () -> Long`，
 * 这个函数只作为**默认值**，方便单测替换。
 */
expect fun nowMillis(): Long
