package com.sekb.ondevice.device

import android.content.Context
import com.sekb.shared.policy.PolicyStore

/**
 * 策略双槽存储（Android 实现）：与设备凭证放同一个 `SharedPreferences` 文件。
 *
 * 复用同一个 prefs 是有意的：两者都是"这台设备的本地状态"，放在一起便于排查与清理
 * （清凭证时也应当清策略——否则换账号后还在用上一个账号下发的阈值）。
 * 策略**不是机密**（有签名兜底），所以明文存储即可，不需要 Keystore 加密。
 */
class SharedPrefsPolicyStore(context: Context) : PolicyStore {

    private val prefs = context.applicationContext
        .getSharedPreferences("sekb_device", Context.MODE_PRIVATE)

    override fun loadActive(): String? = prefs.getString(KEY_ACTIVE, null)

    override fun loadPrevious(): String? = prefs.getString(KEY_PREVIOUS, null)

    override fun save(payloadJson: String) {
        // 顺序：先把当前 active 挪到 previous（同一事务），再写新的
        prefs.edit()
            .putString(KEY_PREVIOUS, prefs.getString(KEY_ACTIVE, null))
            .putString(KEY_ACTIVE, payloadJson)
            .apply()
    }

    override fun rollback(): String? {
        val prev = prefs.getString(KEY_PREVIOUS, null) ?: return null
        prefs.edit().putString(KEY_ACTIVE, prev).remove(KEY_PREVIOUS).apply()
        return prev
    }

    override fun clear() {
        prefs.edit().remove(KEY_ACTIVE).remove(KEY_PREVIOUS).apply()
    }

    private companion object {
        const val KEY_ACTIVE = "policy_active"
        const val KEY_PREVIOUS = "policy_previous"
    }
}
