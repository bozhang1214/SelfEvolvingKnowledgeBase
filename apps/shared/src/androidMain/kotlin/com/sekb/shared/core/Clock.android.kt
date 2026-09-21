package com.sekb.shared.core

/** Android/JVM 实现：系统墙上时钟。 */
actual fun nowMillis(): Long = System.currentTimeMillis()
