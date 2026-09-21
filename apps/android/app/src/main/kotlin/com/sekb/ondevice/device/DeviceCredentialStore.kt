package com.sekb.ondevice.device

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import com.sekb.shared.device.CredentialCodec
import com.sekb.shared.device.CredentialStore
import com.sekb.shared.model.DeviceCredentials
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

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
