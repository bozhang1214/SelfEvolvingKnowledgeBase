package com.sekb.ohos

/**
 * SHA-256 / HMAC-SHA256 的**纯 Kotlin 实现**（鸿蒙 `ohosMain` 的 actual 用）。
 *
 * ## 为什么不用平台加密能力（H1 的判定点，结论有证据）
 *
 * 鸿蒙侧确实有系统加密 API，两条路都查过：
 *
 * | 候选 | 实测事实 |
 * |---|---|
 * | `CryptoArchitectureKit`（`libohcrypto.so`，NDK C 接口 `OH_CryptoDigest_*` / `OH_CryptoMac_*`） | 本机 sysroot **有头文件与导入桩**（`.tooling/konan/dependencies/sysroot-ohos-aarch64-6.0.2.640-04/usr/{include/CryptoArchitectureKit,lib/aarch64-linux-ohos/libohcrypto.so}`，114 个符号）；`OH_CryptoDigest_*` 自 **API 12**、`OH_CryptoMac_*` 自 **API 20**，模拟器是 API 22 → 理论上可用 |
 * | `HUKS`（`libhuks_ndk.z.so`） | 面向**密钥管理**，不是"算一次哈希"；本任务不需要 |
 *
 * **选择纯 Kotlin 的原因不是"平台能力不行"，而是"我无法在本环境证明它行"**：
 * 设备上 `test -e /system/lib64/libohcrypto.so` 报 MISSING，但同一条命令对
 * **已确认在跑的** `libmindspore_lite_ndk.so` 也报 MISSING——
 * 说明该探测受 SELinux 限制、结论不可信。
 * 而 HAP 一旦 `DT_NEEDED` 一个设备上不存在的 `.so`，整个应用加载失败（比算法慢一点严重得多）。
 *
 * **另一条更硬的理由**：`hmacSha256Hex` 在共享层里是**策略验签**用的（能改端侧阈值/白名单，
 * 是安全边界）。把它的正确性押在"某个我验证不了的运行时库 + 某个 SDK 版本的接口"上，
 * 而本实现只用**语言运算**、可用 RFC 公知向量逐位验证，风险显然更低。
 *
 * ## 正确性怎么保证（不靠"看起来对"）
 *
 * 1. 只做标准算法，没有自创结构：SHA-256 = FIPS 180-4；HMAC = RFC 2104 / RFC 4231；
 * 2. 冒烟测试用**公知向量**逐位断言（与 Android `javax.crypto` / Apple `CommonCrypto` 同一批）：
 *    - `sha256("abc")` = `ba7816bf…f20015ad`（FIPS 180-4 附录 B）
 *    - `hmacSha256("key", "The quick brown fox jumps over the lazy dog")` =
 *      `f7bc83f4…2d1a3cd8`（RFC 4231 第 4 节 test case 1 的变体，与 iOS 冒烟同一串）
 *    - 与 Android 实现在**同一次设备自检里逐字符比对**（`HmacSmoke`）；
 * 3. **只放在 `ohosMain`**：Android/JVM 继续用 `javax.crypto`、Apple 继续用 `CommonCrypto`，
 *    没有共享实现的**回归风险**，也不会因为"端侧算法换了"而改变任何一端的既有行为。
 */
internal object Sha256Pure {

    private val K = intArrayOf(
        0x428a2f98, 0x71374491, -0x4a3f0431, -0x164a245b, 0x3956c25b, 0x59f111f1, -0x6dc07d5c, -0x54e3a12b,
        -0x27f85568, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, -0x7f214e02, -0x6423f959, -0x3e640e8c,
        -0x1b64963f, -0x1041b87a, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
        -0x67c1aeae, -0x57ce3993, -0x4ffcd838, -0x40a68039, -0x391ff40d, -0x2a586eb9, 0x06ca6351, 0x14292967,
        0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, -0x7e3d36d2, -0x6d8dd37b,
        -0x5d40175f, -0x57e599b5, -0x3db47490, -0x3893ae5d, -0x2e6d17e7, -0x2966f9dc, -0xbf1ca7b, 0x106aa070,
        0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
        0x748f82ee, 0x78a5636f, -0x7b3787ec, -0x7338fdf8, -0x6f410006, -0x5baf9315, -0x41065c09, -0x398e870e,
    )

    private val H0 = intArrayOf(
        0x6a09e667, -0x4498517b, 0x3c6ef372, -0x5ab00ac6, 0x510e527f, -0x64fa9774, 0x1f83d9ab, 0x5be0cd19,
    )

    fun hash(message: ByteArray): ByteArray {
        val bitLen = message.size.toLong() * 8

        // 填充：0x80 后补 0，直到长度 ≡ 56 (mod 64)，再放 8 字节大端位长
        val padLen = ((56 - (message.size + 1) % 64) + 64) % 64
        val total = message.size + 1 + padLen + 8
        val buf = ByteArray(total)
        message.copyInto(buf, 0, 0, message.size)
        buf[message.size] = 0x80.toByte()
        for (i in 0 until 8) {
            buf[total - 1 - i] = ((bitLen ushr (8 * i)) and 0xFF).toByte()
        }

        val h = H0.copyOf()
        val w = IntArray(64)
        var block = 0
        while (block < total) {
            for (t in 0 until 16) {
                val o = block + t * 4
                w[t] = ((buf[o].toInt() and 0xFF) shl 24) or
                    ((buf[o + 1].toInt() and 0xFF) shl 16) or
                    ((buf[o + 2].toInt() and 0xFF) shl 8) or
                    (buf[o + 3].toInt() and 0xFF)
            }
            for (t in 16 until 64) {
                val s0 = w[t - 15].rotateRight(7) xor w[t - 15].rotateRight(18) xor (w[t - 15] ushr 3)
                val s1 = w[t - 2].rotateRight(17) xor w[t - 2].rotateRight(19) xor (w[t - 2] ushr 10)
                // Kotlin Int 加法溢出即为 mod 2^32，正是 SHA-256 要的语义
                w[t] = w[t - 16] + s0 + w[t - 7] + s1
            }

            var a = h[0]; var b = h[1]; var c = h[2]; var d = h[3]
            var e = h[4]; var f = h[5]; var g = h[6]; var hh = h[7]

            for (t in 0 until 64) {
                val s1 = e.rotateRight(6) xor e.rotateRight(11) xor e.rotateRight(25)
                val ch = (e and f) xor (e.inv() and g)
                val temp1 = hh + s1 + ch + K[t] + w[t]
                val s0 = a.rotateRight(2) xor a.rotateRight(13) xor a.rotateRight(22)
                val maj = (a and b) xor (a and c) xor (b and c)
                val temp2 = s0 + maj
                hh = g; g = f; f = e; e = d + temp1
                d = c; c = b; b = a; a = temp1 + temp2
            }

            h[0] += a; h[1] += b; h[2] += c; h[3] += d
            h[4] += e; h[5] += f; h[6] += g; h[7] += hh
            block += 64
        }

        val out = ByteArray(32)
        for (i in 0 until 8) {
            out[i * 4] = ((h[i] ushr 24) and 0xFF).toByte()
            out[i * 4 + 1] = ((h[i] ushr 16) and 0xFF).toByte()
            out[i * 4 + 2] = ((h[i] ushr 8) and 0xFF).toByte()
            out[i * 4 + 3] = (h[i] and 0xFF).toByte()
        }
        return out
    }

    /** HMAC-SHA256（RFC 2104）：`H((K⊕opad) ‖ H((K⊕ipad) ‖ m))`。 */
    fun hmac(key: ByteArray, message: ByteArray): ByteArray {
        val blockSize = 64
        val k = if (key.size > blockSize) hash(key) else key
        val ipad = ByteArray(blockSize) { 0x36 }
        val opad = ByteArray(blockSize) { 0x5c }
        for (i in k.indices) {
            ipad[i] = (ipad[i].toInt() xor k[i].toInt()).toByte()
            opad[i] = (opad[i].toInt() xor k[i].toInt()).toByte()
        }
        val inner = hash(ipad + message)
        return hash(opad + inner)
    }

    // 不用 `Integer.rotateRight`（JVM 专有，commonMain 编不过——同样的坑在 Fmt.kt 里记过）
    private fun Int.rotateRight(n: Int): Int = (this ushr n) or (this shl (32 - n))

    fun toHex(bytes: ByteArray): String {
        val hex = StringBuilder(bytes.size * 2)
        for (b in bytes) {
            val v = b.toInt() and 0xFF
            hex.append(HEX[v ushr 4])
            hex.append(HEX[v and 0x0F])
        }
        return hex.toString()
    }

    private const val HEX = "0123456789abcdef"
}
