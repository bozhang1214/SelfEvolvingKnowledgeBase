package com.sekb.ondevice.device

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import com.sekb.shared.model.DeviceCredentials
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * 设备凭证的本地存储。
 *
 * **为什么不能明文放 SharedPreferences**：这个 token 是长效的（默认 30 天），
 * 谁拿到它就能以这台设备的身份聊天、上报。虽然 App 私有目录本身受沙箱保护，
 * 但 root/备份/取证提取都会拿到明文。所以用 **Android Keystore 里的 AES-GCM 密钥**加密：
 * 密钥不出安全硬件（有 TEE 的机型上），密文即使被提走也解不开。
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

/**
 * Keystore 加密实现。
 *
 * 存储布局（SharedPreferences 的 `sekb_device`）：
 * - `credentials_blob` = base64(iv) + ":" + base64(ciphertext)
 * - `device_id`        = 设备 ID（明文，便于在 UI 上显示"当前是哪台设备"）
 *
 * 设备 token 只以密文形式落盘；`device_id` 不是凭据，明文无妨。
 */
class KeystoreCredentialStore(
    context: Context,
    private val alias: String = KEY_ALIAS,
) : CredentialStore {

    private val prefs = context.applicationContext
        .getSharedPreferences("sekb_device", Context.MODE_PRIVATE)

    override fun load(): DeviceCredentials? {
        val blob = prefs.getString(KEY_BLOB, null) ?: return null
        val parts = blob.split(":")
        if (parts.size != 2) return null
        return try {
            val iv = Base64.decode(parts[0], Base64.NO_WRAP)
            val cipherText = Base64.decode(parts[1], Base64.NO_WRAP)
            val cipher = Cipher.getInstance(TRANSFORMATION)
            cipher.init(Cipher.DECRYPT_MODE, secretKey(), GCMParameterSpec(TAG_BITS, iv))
            val plain = String(cipher.doFinal(cipherText), Charsets.UTF_8)
            CredentialCodec.decode(plain)
        } catch (e: Exception) {
            // 解不开（换机恢复/密钥被清）→ 当作没有凭证，重新 enroll。
            // **绝不能**在这里抛：否则 App 会在启动时直接崩，用户只能重装。
            null
        }
    }

    override fun save(credentials: DeviceCredentials) {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, secretKey())
        val cipherText = cipher.doFinal(CredentialCodec.encode(credentials).toByteArray(Charsets.UTF_8))
        val blob = Base64.encodeToString(cipher.iv, Base64.NO_WRAP) + ":" +
            Base64.encodeToString(cipherText, Base64.NO_WRAP)
        prefs.edit().putString(KEY_BLOB, blob).putString(KEY_DEVICE_ID, credentials.deviceId).apply()
    }

    override fun clear() {
        prefs.edit().remove(KEY_BLOB).remove(KEY_DEVICE_ID).apply()
    }

    /** 当前设备 ID（明文，仅用于展示；没有凭证时为空串）。 */
    fun deviceId(): String = prefs.getString(KEY_DEVICE_ID, "").orEmpty()

    private fun secretKey(): SecretKey {
        val keyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        (keyStore.getEntry(alias, null) as? KeyStore.SecretKeyEntry)?.let { return it.secretKey }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE)
        generator.init(
            KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                // 不要求用户认证：设备需要在后台刷新凭证；靠 App 沙箱 + Keystore 隔离兜住
                .setUserAuthenticationRequired(false)
                .build(),
        )
        return generator.generateKey()
    }

    companion object {
        private const val ANDROID_KEYSTORE = "AndroidKeyStore"
        private const val KEY_ALIAS = "sekb_device_credentials"
        private const val TRANSFORMATION = "AES/GCM/NoPadding"
        private const val TAG_BITS = 128
        private const val KEY_BLOB = "credentials_blob"
        private const val KEY_DEVICE_ID = "device_id"
    }
}

/**
 * 凭证的**明文编解码**（纯函数，便于单测）。
 *
 * 用最简的分隔格式而不是 JSON：这里只有四个字段，且**不能**因为 JSON 转义问题
 * 把 token 弄坏（token 里可能含 `.` `-` `_`，用 `\u0001` 分隔最省心）。
 */
object CredentialCodec {

    private const val SEP = "\u0001"

    fun encode(c: DeviceCredentials): String = listOf(
        c.deviceId, c.deviceToken, c.issuedAtMillis.toString(), c.expiresAtMillis.toString(),
    ).joinToString(SEP)

    fun decode(plain: String): DeviceCredentials? {
        val parts = plain.split(SEP)
        if (parts.size != 4) return null
        val issued = parts[2].toLongOrNull() ?: return null
        val expires = parts[3].toLongOrNull() ?: return null
        if (parts[0].isBlank() || parts[1].isBlank()) return null
        return DeviceCredentials(parts[0], parts[1], issued, expires)
    }
}
