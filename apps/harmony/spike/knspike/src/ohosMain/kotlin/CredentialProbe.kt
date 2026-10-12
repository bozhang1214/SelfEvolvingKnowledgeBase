// H4 · 凭证端口：HUKS 实现的 `CredentialStore`（共享层接口）+ 设备侧自检探针
//
// ## 分工（与 H3 同一条原则：协议/格式只有一份）
//
//   * **接口**：`com.sekb.shared.device.CredentialStore`（共享层，一行未改）
//   * **编解码**：`com.sekb.shared.device.CredentialCodec`（共享层，一行未改）——
//     本文件**不自己拼字段**，只把 `encode` 出来的明文交给 HUKS 加密
//   * **密钥与加解密**：HUKS（`HuksGcmCipher`，cinterop 直调）
//   * **落地**：应用私有目录里的一个文件（路径由 ArkTS 传入 `context.filesDir`）
//
// 四层分开之后，"Android 写的凭证 iOS/鸿蒙读不出来"这类问题在结构上不可能出现：
// 明文格式由共享层的 `CredentialCodec` 独占，其余各端只负责"怎么把它藏起来"。
//
// ## "重启后仍有效"怎么**证明**（不是"看起来还在"）
//
// 进程每次启动都会新建 `HuksGcmCipher`；HUKS 密钥在**设备密钥库**里、密文在**私有目录**里，
// 两者都不随进程消亡。所以：
//   * 第 1 轮（`bash scripts/harmony.sh net` 的首跑）：`load()` 返回 null → 记 FAIL 并在文案里
//     写明"首轮，已写入 xxx"；同时把凭证写盘；
//   * 第 2 轮（`aa force-stop` 后**不重装**再起）：`load()` 必须读回第 1 轮写的 device_id
//     → PASS，报告里打印两轮的 device_id 供逐字符比对；
//   * HUKS 密钥丢了 → `decrypt` 抛认证失败（不会静默读出空凭证）；
//   * 密文文件丢了 → `load()` 返回 null（又变回"首轮"）。
//
// ⚠️ 一处**实测踩到的自伤**（写在代码里免得下一位重踩）：第一版在探针结尾无条件 `clear()`，
// 于是次轮 `load()` 只能拿到 null —— "重启后仍有效"这条判据**永远不可能 PASS**，
// 而报告里它显示成 FAIL，看起来像 HUKS 有问题，其实是探针自己把证据删了。
// 现在 `clear()` 只在显式 `reset="1"` 时验（脚本用它做"从零首跑"取证）。
@file:OptIn(kotlin.experimental.ExperimentalNativeApi::class, kotlinx.cinterop.ExperimentalForeignApi::class)

package com.sekb.ohos.spike

import com.sekb.shared.core.nowMillis
import com.sekb.shared.device.CredentialCodec
import com.sekb.shared.device.CredentialStore
import com.sekb.shared.model.DeviceCredentials

/**
 * HUKS 落盘的 `CredentialStore`（共享层接口的鸿蒙实现）。
 *
 * 与 Android `KeystoreCredentialStore` 同构：系统密钥库保管密钥 + 私有目录存密文。
 */
class HuksCredentialStore(filesDir: String) : CredentialStore {

    private val cipher = HuksGcmCipher()
    private val blobPath: String = "$filesDir/credentials.huks"

    /** 诊断用（报告里打印）：密钥是否存在、密文文件多大。 */
    val diagnostics: String
        get() {
            val exists = runCatching { cipher.keyExists() }.getOrElse { false }
            val size = readFileBytes(blobPath)?.size ?: -1
            return "密钥存在=${exists}；密文文件=${if (size < 0) "无" else "${size}B"}；路径=${blobPath}"
        }

    override fun load(): DeviceCredentials? {
        val blob = readFileBytes(blobPath) ?: return null
        if (blob.isEmpty()) return null
        val plain = cipher.decrypt(blob).decodeToString()
        return CredentialCodec.decode(plain)
    }

    override fun save(credentials: DeviceCredentials) {
        val plain = CredentialCodec.encode(credentials).encodeToByteArray()
        writeFileBytes(blobPath, cipher.encrypt(plain))
    }

    override fun clear() {
        deleteFile(blobPath)
        cipher.deleteKey()
    }
}

/** 一行式报告（脚本 grep `CRED`）。 */
private class CredReport {
    val lines = mutableListOf<String>()
    var failed = 0
    var total = 0
    fun check(name: String, ok: Boolean, detail: String) {
        total++
        if (!ok) failed++
        lines += "${if (ok) "PASS" else "FAIL"} ${name} — ${detail}"
    }
    fun render(): String =
        (listOf("CRED ${if (failed == 0) "PASS" else "FAIL"} ${total - failed}/${total}") + lines)
            .joinToString("\n")
}

/** 凭证的指纹（跨轮比对用：`device_id` + token 前 8 字符）。 */
internal fun credFingerprint(c: DeviceCredentials?): String =
    if (c == null) "(无)" else "${c.deviceId}/tok=${c.deviceToken.take(8)}…(${c.deviceToken.length})"

/**
 * 设备侧凭证自检。
 *
 * @param filesDir 应用私有目录（ArkTS 的 `context.filesDir`）
 * @param reset    `"1"` = 先清掉已有凭证与 HUKS 密钥（用于"从零首跑"取证；正常两轮验收传 `"0"`）
 * @param runTag   本轮运行标签，写进新建凭证的 device_id，便于跨轮比对
 */
@CName("sekb_spike_cred_selftest")
fun sekbSpikeCredSelfTest(filesDir: String, reset: String, runTag: String): String {
    val r = CredReport()
    val doReset = reset == "1" || reset == "true"

    r.check("huks_binding", true, huksBindingStatus)

    // ── ① AES-GCM 往返（HUKS 密钥真的在设备密钥库里、密文真的能解回来）────────
    val cipher = HuksGcmCipher()
    val plain = "端侧凭证 roundtrip ${runTag.ifBlank { "no-tag" }} 0123456789".encodeToByteArray()
    runCatching {
        if (doReset) cipher.deleteKey()
        val generated = cipher.ensureKey()
        val blob = cipher.encrypt(plain)
        val back = cipher.decrypt(blob)
        r.check(
            "huks_key_ready",
            true,
            if (generated) "本次在设备密钥库新建 AES-256-GCM 密钥（alias=${Huks.ALIAS_DEFAULT}）"
            else "设备密钥库里已有密钥（跨进程存活=持久）",
        )
        r.check(
            "huks_encrypt_decrypt_roundtrip",
            back.contentEquals(plain),
            "明文=${plain.size}B → 密文容器=${blob.size}B（nonce 12 + tag 16 + 密文 ${blob.size - 34}）" +
                " → 解回=${back.size}B，逐字节相同=${back.contentEquals(plain)}",
        )
        r.check(
            "huks_ciphertext_hides_plaintext",
            !blob.decodeToString().contains("roundtrip") && blob.size > plain.size,
            "密文容器前 8 字节=${blob.copyOfRange(0, minOf(8, blob.size)).toHex()}；明文串不可见=" +
                "${!blob.decodeToString().contains("roundtrip")}",
        )
    }.onFailure { e ->
        r.check("huks_key_ready", false, "抛异常：${e.message}")
        r.check("huks_encrypt_decrypt_roundtrip", false, "抛异常：${e.message}")
    }

    // ── ② 共享层 `CredentialCodec` 往返（编解码走的就是共享层那一份）────────
    val codec = DeviceCredentials(
        deviceId = "dev-$runTag",
        deviceToken = "tok-$runTag-abcdef",
        issuedAtMillis = nowMillis(),
        expiresAtMillis = nowMillis() + 24L * 3600 * 1000,
    )
    val encoded = CredentialCodec.encode(codec)
    val decoded = CredentialCodec.decode(encoded)
    r.check(
        "credential_codec_roundtrip",
        decoded == codec,
        "共享层 CredentialCodec：encode=${encoded.length} 字符（${encoded.count { it == '\u0001' }} 个分隔符）" +
            "→ decode 逐字段相同=${decoded == codec}",
    )

    // ── ③ 完整 CredentialStore：save → load（跨进程复用同一个密文文件）────────
    val store = HuksCredentialStore(filesDir)
    val before: DeviceCredentials?
    val after: DeviceCredentials?
    try {
        if (doReset) store.clear()
        before = store.load()
        val fresh = before ?: DeviceCredentials(
            deviceId = "ohos-dev-${runTag.ifBlank { "anon" }}",
            deviceToken = "ohos-token-${runTag.ifBlank { "anon" }}",
            issuedAtMillis = nowMillis(),
            expiresAtMillis = nowMillis() + 24L * 3600 * 1000,
        )
        if (before == null) store.save(fresh)
        after = store.load()
    } catch (e: Throwable) {
        r.check("credential_store_save_load", false, "抛异常：$e")
        r.check("credential_restart", false, "抛异常：$e")
        r.lines += "CRED-DIAG ${store.diagnostics}"
        return r.render()
    }

    r.check(
        "credential_store_save_load",
        after != null && after.deviceId.isNotBlank() && after.deviceToken.isNotBlank(),
        "save → load：${credFingerprint(after)}（device_id/token 都非空=${after != null}）",
    )
    r.check(
        "credential_restart",
        before != null,
        if (before != null)
            "**本轮 load() 读到了上一次进程写盘的凭证**：${credFingerprint(before)}" +
                "（HUKS 解密成功 ⇒ 密钥在设备密钥库、密文在私有目录，两者都跨进程存活）"
        else
            "首轮（磁盘上还没有凭证，已在本次写入 ${credFingerprint(after)}）" +
                "——force-stop 后再跑一轮这条才会 PASS",
    )

    // ⚠️ **不要无条件 clear()**（第一版就是这里出的缺陷）：清了之后次轮 `load()` 只能拿到
    // null，"重启后仍有效"这条判据**永远不可能 PASS**；报告里它显示成 FAIL，看起来像 HUKS
    // 有问题，其实是探针自己把证据删了。所以 clear() 只在显式 `reset="1"` 时验。
    if (doReset) {
        r.check(
            "credential_store_clear",
            run {
                store.clear()
                store.load() == null && !cipher.keyExists()
            },
            "reset=1 → clear() 后 load()=null 且 HUKS 密钥已删除（保留场景不验这一条）",
        )
    } else {
        r.check(
            "credential_store_persists",
            true,
            "本轮**未** clear（清掉就验不了「重启后仍有效」）：密文文件保留，供下一轮 load() 读回",
        )
    }
    r.lines += "CRED-DIAG ${store.diagnostics}"
    return r.render()
}

/** 最小探针：只做加解密往返（HR2 要求的那一条；不落盘、不依赖网络）。 */
@CName("sekb_spike_huks_roundtrip")
fun sekbSpikeHuksRoundtrip(payload: String): String {
    val r = CredReport()
    r.check("huks_binding", true, huksBindingStatus)
    val text = if (payload.isBlank()) "SEKB-HUKS-ROUNDTRIP" else payload
    val bytes = text.encodeToByteArray()
    val cipher = HuksGcmCipher()
    runCatching {
        cipher.ensureKey()
        val blob = cipher.encrypt(bytes)
        val back = cipher.decrypt(blob)
        r.check(
            "huks_roundtrip_identical",
            back.contentEquals(bytes),
            "明文 $text → 密文 ${blob.size}B → 解回 ${back.decodeToString()}（逐字节相同=${back.contentEquals(bytes)}）",
        )
    }.onFailure { e -> r.check("huks_roundtrip_identical", false, "抛异常：$e") }
    return r.render()
}
