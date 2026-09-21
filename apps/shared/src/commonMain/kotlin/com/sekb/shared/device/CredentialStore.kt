package com.sekb.shared.device

import com.sekb.shared.model.DeviceCredentials

/**
 * 设备凭证的本地存储**端口**（M5 端口②，2026-09-21 从 Android 模块搬到共享层）。
 *
 * 为什么接口必须在共享层：iOS 侧要**实现同一个接口**（`KeychainCredentialStore`），
 * 接口留在 Android 模块里，iOS 就只能另立一套——那"四端同一套端口"就只剩口号了。
 *
 * 各端实现：
 * - Android：`KeystoreCredentialStore`（Keystore 里的 AES-GCM 密钥加密后写 SharedPreferences）
 * - iOS/Mac：`KeychainCredentialStore`（`kSecClassGenericPassword`，系统加密存储）
 * - 鸿蒙：HUKS（待 M7）
 *
 * 编解码统一走 [CredentialCodec]（也在共享层）：两端格式一致，
 * 才不会出现"Android 写的凭证 iOS 读不出来"。
 */
interface CredentialStore {
    fun load(): DeviceCredentials?
    fun save(credentials: DeviceCredentials)
    fun clear()
}

/** 内存实现：测试与"不落盘"场景用。 */
class InMemoryCredentialStore(private var value: DeviceCredentials? = null) : CredentialStore {
    override fun load(): DeviceCredentials? = value
    override fun save(credentials: DeviceCredentials) { value = credentials }
    override fun clear() { value = null }
}
