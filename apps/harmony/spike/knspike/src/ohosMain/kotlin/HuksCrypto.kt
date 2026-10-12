// H4 · HUKS 的 AES-GCM-256 加解密往返（密钥由**设备密钥库**生成并保管，取不出密钥本体）
//
// ## 口径（为什么是 AES-GCM + 系统生成的密钥）
//
// 与 Android `KeystoreCredentialStore` 同一个形状：密钥不落盘、只由系统密钥库保管，
// 应用侧拿到的只有密文。所以这里：
//   * 密钥：`OH_Huks_GenerateKeyItem`（AES-256、`PURPOSE_ENCRYPT|DECRYPT`、GCM、无填充），
//     alias 固定；生成之后**任何接口都取不出密钥本体**——这才是选 HUKS 而不是
//     "自己生成一个密钥写进文件"的全部意义；
//   * 每次加密：**随机 12 字节 NONCE**（GCM 的硬要求：同一 key+nonce 复用会直接摧毁保密性）；
//   * 密文容器（自描述，便于将来换算法/tag 长度）：
//     `"SKB1" | nonceLen(1B) | tagLen(1B) | nonce | tag | ciphertext`
@file:OptIn(kotlinx.cinterop.ExperimentalForeignApi::class)

package com.sekb.ohos.spike

import kotlinx.cinterop.memScoped

/** HUKS 侧的加解密失败（带原始返回码，报告里能直接定位）。 */
class HuksCryptoException(val ret: HuksRet) : Exception("HUKS ${ret}")

/** 固定 alias 的 HUKS AES-GCM 会话封装。 */
class HuksGcmCipher(private val alias: String = Huks.ALIAS_DEFAULT) {

    /** 密钥是否已经在设备密钥库里（true 说明以前生成过，进程重启也还在）。 */
    fun keyExists(): Boolean = memScoped { Huks.isKeyItemExist(this, alias).ok }

    /**
     * 确保密钥存在。返回是否**本次真的生成了**（true=新生成；false=以前就有）。
     *
     * 幂等：`OH_HUKS_ERR_CODE_KEY_ALREADY_EXIST`（API 20 起）也当成功。
     */
    fun ensureKey(): Boolean {
        if (keyExists()) return false
        val ret = memScoped { Huks.generateKeyItem(this, alias, Huks.generateParams()) }
            ?: throw HuksCryptoException(HuksRet(UInt.MAX_VALUE, "GenerateKeyItem 的参数集构造失败"))
        if (!ret.ok && ret.code != Huks.alreadyExist) throw HuksCryptoException(ret)
        return ret.ok
    }

    fun deleteKey(): Boolean = memScoped {
        val r = Huks.deleteKeyItem(this, alias)
        r.ok || r.code == Huks.notExist
    }

    /** 加密：返回自描述密文容器（见文件头）。 */
    fun encrypt(plain: ByteArray): ByteArray {
        ensureKey()
        val nonce = randomBytes(NONCE_LEN)
        val (ret, out) = memScoped {
            Huks.runSession(this, alias, Huks.encryptParams(nonce), plain, plain.size + TAG_LEN)
        }
        if (!ret.ok) throw HuksCryptoException(ret)
        require(out.size >= TAG_LEN) { "GCM 输出只有 ${out.size} 字节，装不下 tag" }
        val body = out.copyOfRange(0, out.size - TAG_LEN)
        val tag = out.copyOfRange(out.size - TAG_LEN, out.size)
        return MAGIC + byteArrayOf(NONCE_LEN.toByte(), TAG_LEN.toByte()) + nonce + tag + body
    }

    /** 解密：接受 [encrypt] 的产物；容器格式不对或认证失败都抛 [HuksCryptoException]。 */
    fun decrypt(blob: ByteArray): ByteArray {
        if (blob.size < 6 || !blob.copyOfRange(0, 4).contentEquals(MAGIC)) {
            throw HuksCryptoException(HuksRet(UInt.MAX_VALUE - 1u, "密文容器魔数不对（长度=${blob.size}）"))
        }
        val nonceLen = blob[4].toInt() and 0xff
        val tagLen = blob[5].toInt() and 0xff
        val head = 6 + nonceLen + tagLen
        if (blob.size < head) throw HuksCryptoException(HuksRet(UInt.MAX_VALUE - 1u, "密文容器被截断"))
        val nonce = blob.copyOfRange(6, 6 + nonceLen)
        val tag = blob.copyOfRange(6 + nonceLen, head)
        val body = blob.copyOfRange(head, blob.size)
        val (ret, out) = memScoped {
            Huks.runSession(this, alias, Huks.decryptParams(nonce, tag), body, body.size + 16)
        }
        if (!ret.ok) throw HuksCryptoException(ret)
        return out
    }

    companion object {
        private const val NONCE_LEN = 12
        private const val TAG_LEN = 16
        private val MAGIC = byteArrayOf('S'.code.toByte(), 'K'.code.toByte(), 'B'.code.toByte(), '1'.code.toByte())
    }
}
