package com.sekb.shared.core

import platform.Foundation.NSDate
import platform.Foundation.timeIntervalSince1970

/**
 * Apple（iOS / Mac）实现：`NSDate` → 毫秒。
 *
 * ⚠️ 放在 `appleMain` 而不是 `nativeMain`：`NSDate` 是 Foundation 的东西，
 * 将来若加 Linux/Android-Native target，不该被这段实现牵连。
 */
actual fun nowMillis(): Long = (NSDate().timeIntervalSince1970 * 1000.0).toLong()
