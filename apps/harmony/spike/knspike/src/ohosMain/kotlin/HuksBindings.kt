// H4 · HUKS（HarmonyOS 通用密钥库）绑定 —— **cinterop 直调平台 C API，纯 Kotlin/Native，不经 ArkTS 桥**
//
// ## 为什么是 cinterop（而不是手写 `CStructVar` 布局）
//
// 第一版我手写了结构体字节偏移，**编译期就撞墙**（Kotlin/Native 2.2.x 上
// `CStructVar` + `reinterpret<T>()` + `MemScope` 那一套已经变了：裸 `ptr.reinterpret()`
// 会报 "No value passed for parameter 'rawPtr'"，`COpaquePointer`/`MemScope` 也不可见）。
// 更要紧的是：`OH_Huks_Result` 是**按值返回的结构体**、`OH_Huks_Param` 里有 union ——
// 手工布局即使编过，也只是"我猜对了"，不是"编译器保证对了"。
//
// 而本仓库**已有成功先例**：`knspike/src/nativeInterop/cinterop/ohcrypto.def` 用同一套 sysroot
// 头文件 + `linkerOpts` 编过 `libohcrypto.so`。所以走 cinterop 是照既有事实办。
//
// ## 设备侧可得性（H3 探针的原始证据）
//
// `bash scripts/harmony.sh probe` → `LOAD OK libhuks_ndk.z.so`（dlopen 实测）。
// sysroot 里同样有头文件（`usr/include/huks/native_huks_{api,param,type}.h`）与导入桩
// （`usr/lib/aarch64-linux-ohos/libhuks_ndk.z.so`，28 个 `OH_Huks_*` 符号）。
//
// ## 一个必须记住的 HUKS 语义
//
// GCM 的认证标签由 `OH_Huks_FinishSession` 产出、**附在密文尾部**（输出 = 明文 + 16B）。
// 解密时要把 tag **切下来**作为 `OH_HUKS_TAG_AE_TAG` 传回去；忘了切不会"解出乱码"，
// 而会直接失败（`12000003` 之类）。见 `HuksGcmCipher.decrypt`。
@file:OptIn(kotlinx.cinterop.ExperimentalForeignApi::class)

package com.sekb.ohos.spike

import kotlinx.cinterop.ByteVar
import kotlinx.cinterop.CPointer
import kotlinx.cinterop.CValue
import kotlinx.cinterop.CPointerVar
import kotlinx.cinterop.MemScope
import kotlinx.cinterop.UByteVar
import kotlinx.cinterop.alloc
import kotlinx.cinterop.allocArray
import kotlinx.cinterop.get
import kotlinx.cinterop.memScoped
import kotlinx.cinterop.pointed
import kotlinx.cinterop.ptr
import kotlinx.cinterop.reinterpret
import kotlinx.cinterop.set
import kotlinx.cinterop.toKString
import kotlinx.cinterop.useContents
import kotlinx.cinterop.value
import huks.OH_HUKS_AES_KEY_SIZE_256
import huks.OH_HUKS_ALG_AES
import huks.OH_HUKS_ERR_CODE_ITEM_NOT_EXIST
import huks.OH_HUKS_ERR_CODE_KEY_ALREADY_EXIST
import huks.OH_HUKS_KEY_PURPOSE_DECRYPT
import huks.OH_HUKS_KEY_PURPOSE_ENCRYPT
import huks.OH_HUKS_MODE_GCM
import huks.OH_HUKS_PADDING_NONE
import huks.OH_HUKS_SUCCESS
import huks.OH_HUKS_TAG_AE_TAG
import huks.OH_HUKS_TAG_ALGORITHM
import huks.OH_HUKS_TAG_BLOCK_MODE
import huks.OH_HUKS_TAG_KEY_SIZE
import huks.OH_HUKS_TAG_NONCE
import huks.OH_HUKS_TAG_PADDING
import huks.OH_HUKS_TAG_PURPOSE
import huks.OH_Huks_AbortSession
import huks.OH_Huks_AddParams
import huks.OH_Huks_Blob
import huks.OH_Huks_BuildParamSet
import huks.OH_Huks_DeleteKeyItem
import huks.OH_Huks_FinishSession
import huks.OH_Huks_FreeParamSet
import huks.OH_Huks_GenerateKeyItem
import huks.OH_Huks_InitParamSet
import huks.OH_Huks_InitSession
import huks.OH_Huks_IsKeyItemExist
import huks.OH_Huks_Param
import huks.OH_Huks_ParamSet
import huks.OH_Huks_Result

/** 一次 HUKS 调用的可读结果（返回码 + 错误描述）。 */
data class HuksRet(val code: UInt, val msg: String) {
    val ok: Boolean get() = code == OH_HUKS_SUCCESS
    /** 平台枚举在 cinterop 里是 `UInt`；报告/比较时用这个 `Int` 视图。 */
    val codeInt: Int get() = code.toInt()
    override fun toString(): String = "${code}${if (msg.isBlank()) "" else "($msg)"}"
}

/** 参数值：`Num` 走 union 的整数视图，`Bytes` 走 union 的 `Blob` 视图。 */
sealed interface HuksValue {
    class Num(val v: Int) : HuksValue
    class Bytes(val v: ByteArray) : HuksValue
}

/** ⚠️ 参数值是 **`UInt`**：cinterop 把 C 枚举常量生成成 `UInt`（实测 `const val OH_HUKS_ALG_AES: UInt = 20u`）。 */
fun hNum(v: UInt): HuksValue = HuksValue.Num(v.toInt())
fun hBytes(v: ByteArray): HuksValue = HuksValue.Bytes(v)

/**
 * HUKS C API 的薄封装（cinterop 直调：没有 dlopen、没有手写布局）。
 *
 * ⚠️ 不加锁：全部调用点都在 ArkTS → NAPI 的同一条线程上（Kotlin/Native 没有 `@Synchronized`，
 * 见 README 记过的坑）。将来出现并发调用点时要自己上 `pthread_mutex`。
 */
object Huks {

    const val ALIAS_DEFAULT = "sekb_ohos_credential_aes_gcm_v1"

    /** `OH_Huks_Result` 是按值返回的结构体 → `useContents` 读字段。 */
    private fun CValue<OH_Huks_Result>.toRet(): HuksRet =
        useContents { HuksRet(errorCode.toUInt(), errorMsg?.toKString() ?: "") }

    /** 把 Kotlin 字符串落成 `OH_Huks_Blob`（数据分配在同一个 [MemScope] 里）。 */
    private fun MemScope.blobOf(text: String): CPointer<OH_Huks_Blob> {
        val buf = allocArray<UByteVar>(maxOf(text.length, 1))
        text.forEachIndexed { i, c -> buf[i] = c.code.toUByte() }
        val blob = alloc<OH_Huks_Blob>()
        blob.size = text.length.toUInt()
        blob.data = buf
        return blob.ptr
    }

    private fun MemScope.byteBlobOf(data: ByteArray): CPointer<OH_Huks_Blob> {
        val buf = allocArray<UByteVar>(maxOf(data.size, 1))
        data.forEachIndexed { i, b -> buf[i] = b.toUByte() }
        val blob = alloc<OH_Huks_Blob>()
        blob.size = data.size.toUInt()
        blob.data = buf
        return blob.ptr
    }

    /**
     * 用 [params] 构造参数集，执行 [body]，**无论成败都释放**。
     *
     * `Blob` 型参数（NONCE / AE_TAG）必须与参数集**同寿命**：`OH_Huks_AddParams` 按指针读，
     * 参数集用完前那块内存不能失效 —— 所以整段都在同一个 `memScoped` 里分配
     * （在 Kotlin/Native 上写错不会崩，只会给出错结果，必须小心）。
     */
    private fun <T> withParamSet(
        scope: MemScope,
        params: List<Pair<UInt, HuksValue>>,
        body: (CPointer<OH_Huks_ParamSet>) -> T,
    ): T? {
        val setVar = scope.alloc<CPointerVar<OH_Huks_ParamSet>>()
        if (!OH_Huks_InitParamSet(setVar.ptr).toRet().ok) return null
        val raw = scope.allocArray<OH_Huks_Param>(maxOf(params.size, 1))
        params.forEachIndexed { i, entry ->
            raw[i].tag = entry.first.toUInt()
            when (val value = entry.second) {
                is HuksValue.Num -> raw[i].int32Param = value.v
                is HuksValue.Bytes -> {
                    val buf = scope.allocArray<UByteVar>(maxOf(value.v.size, 1))
                    value.v.forEachIndexed { k, b -> buf[k] = b.toUByte() }
                    raw[i].blob.size = value.v.size.toUInt()
                    raw[i].blob.data = buf
                }
            }
        }
        val set0: CPointer<OH_Huks_ParamSet> = setVar.value ?: return null
        if (!OH_Huks_AddParams(set0, raw.reinterpret(), params.size.toUInt()).toRet().ok) return null
        if (!OH_Huks_BuildParamSet(setVar.ptr).toRet().ok) {
            OH_Huks_FreeParamSet(setVar.ptr)
            return null
        }
        val built: CPointer<OH_Huks_ParamSet> = setVar.value ?: return null
        return try {
            body(built)
        } finally {
            OH_Huks_FreeParamSet(setVar.ptr)
        }
    }

    // ── 直通封装 ────────────────────────────────────────────────────────────

    fun isKeyItemExist(scope: MemScope, alias: String): HuksRet =
        OH_Huks_IsKeyItemExist(scope.blobOf(alias), null).toRet()

    fun deleteKeyItem(scope: MemScope, alias: String): HuksRet =
        OH_Huks_DeleteKeyItem(scope.blobOf(alias), null).toRet()

    fun generateKeyItem(scope: MemScope, alias: String, params: List<Pair<UInt, HuksValue>>): HuksRet? {
        val aliasBlob = scope.blobOf(alias)
        return withParamSet(scope, params) { set -> OH_Huks_GenerateKeyItem(aliasBlob, set, null).toRet() }
    }

    /**
     * 一次完整会话：`InitSession` → `FinishSession`（数据单段）。
     *
     * 为什么不用 `UpdateSession`：AES-GCM 的 AE tag 在 `FinishSession` 才产出；
     * 单段输入走 init+finish 是 HUKS 官方样例的写法，也少一个出错环节
     * （多段大文件时才需要 Update，届时再加）。
     */
    fun runSession(
        scope: MemScope,
        alias: String,
        params: List<Pair<UInt, HuksValue>>,
        input: ByteArray,
        outCapacity: Int,
    ): Pair<HuksRet, ByteArray> {
        val aliasBlob = scope.blobOf(alias)
        val inBlob = scope.byteBlobOf(input)

        val outBuf = scope.allocArray<UByteVar>(maxOf(outCapacity, 1))
        val outBlob = scope.alloc<OH_Huks_Blob>()
        outBlob.size = outCapacity.toUInt()
        outBlob.data = outBuf

        val handleBlob = scope.alloc<OH_Huks_Blob>()
        handleBlob.size = 8u
        handleBlob.data = scope.allocArray<UByteVar>(8)

        val tokenBlob = scope.alloc<OH_Huks_Blob>()
        tokenBlob.size = 0u
        tokenBlob.data = scope.allocArray<UByteVar>(8)

        var code = UInt.MAX_VALUE
        var msg = ""
        withParamSet(scope, params) { set ->
            val r0 = OH_Huks_InitSession(aliasBlob, set, handleBlob.ptr, tokenBlob.ptr).toRet()
            if (!r0.ok) {
                code = r0.code
                msg = r0.msg
                return@withParamSet
            }
            val r1 = OH_Huks_FinishSession(handleBlob.ptr, set, inBlob, outBlob.ptr).toRet()
            if (!r1.ok) {
                OH_Huks_AbortSession(handleBlob.ptr, null)
                code = r1.code
                msg = r1.msg
                return@withParamSet
            }
            code = OH_HUKS_SUCCESS
        }
        // ⚠️ `outBlob` 是 `alloc` 出来的 **CVariable**（不是 CValue），所以用
        // `ptr.pointed.size` 读回实际写入长度（`useContents` 只适用于 CValue）。
        val n = outBlob.ptr.pointed.size.toInt()
        val out = ByteArray(n.coerceIn(0, outCapacity)) { i -> outBuf[i].toInt().toByte() }
        return HuksRet(code, msg) to out
    }

    // ── 参数表（tag 与取值都来自平台头里的枚举常量）──────────────────────────

    fun generateParams(): List<Pair<UInt, HuksValue>> = listOf(
        OH_HUKS_TAG_ALGORITHM to hNum(OH_HUKS_ALG_AES),
        OH_HUKS_TAG_KEY_SIZE to hNum(OH_HUKS_AES_KEY_SIZE_256),
        OH_HUKS_TAG_PURPOSE to hNum(OH_HUKS_KEY_PURPOSE_ENCRYPT or OH_HUKS_KEY_PURPOSE_DECRYPT),
        OH_HUKS_TAG_BLOCK_MODE to hNum(OH_HUKS_MODE_GCM),
        OH_HUKS_TAG_PADDING to hNum(OH_HUKS_PADDING_NONE),
    )

    fun encryptParams(nonce: ByteArray): List<Pair<UInt, HuksValue>> = listOf(
        OH_HUKS_TAG_ALGORITHM to hNum(OH_HUKS_ALG_AES),
        OH_HUKS_TAG_BLOCK_MODE to hNum(OH_HUKS_MODE_GCM),
        OH_HUKS_TAG_PADDING to hNum(OH_HUKS_PADDING_NONE),
        OH_HUKS_TAG_PURPOSE to hNum(OH_HUKS_KEY_PURPOSE_ENCRYPT),
        OH_HUKS_TAG_NONCE to hBytes(nonce),
    )

    fun decryptParams(nonce: ByteArray, tag: ByteArray): List<Pair<UInt, HuksValue>> = listOf(
        OH_HUKS_TAG_ALGORITHM to hNum(OH_HUKS_ALG_AES),
        OH_HUKS_TAG_BLOCK_MODE to hNum(OH_HUKS_MODE_GCM),
        OH_HUKS_TAG_PADDING to hNum(OH_HUKS_PADDING_NONE),
        OH_HUKS_TAG_PURPOSE to hNum(OH_HUKS_KEY_PURPOSE_DECRYPT),
        OH_HUKS_TAG_NONCE to hBytes(nonce),
        OH_HUKS_TAG_AE_TAG to hBytes(tag),
    )

    /** 平台枚举在 cinterop 里是 `UInt`；这两个是报告/比较用的视图。 */
    val alreadyExist: UInt get() = OH_HUKS_ERR_CODE_KEY_ALREADY_EXIST
    val notExist: UInt get() = OH_HUKS_ERR_CODE_ITEM_NOT_EXIST
}

/** 十六进制（自检报告里比对密文用；不引第三方）。 */
fun ByteArray.toHex(): String {
    val digits = "0123456789abcdef"
    val sb = StringBuilder(size * 2)
    for (b in this) {
        val v = b.toInt() and 0xff
        sb.append(digits[v ushr 4]).append(digits[v and 0x0f])
    }
    return sb.toString()
}

/**
 * 从 `/dev/urandom` 取 [n] 个随机字节（GCM 的 nonce 必须不可预测）。
 *
 * 为什么不用 `rand`：那是可预测的伪随机，用在 GCM nonce 上等于把保密性归零。
 * 读失败就抛错，**不允许**退化成可预测来源。
 */
fun randomBytes(n: Int): ByteArray {
    val fd = platform.posix.open("/dev/urandom", platform.posix.O_RDONLY)
    if (fd < 0) throw IllegalStateException("打不开 /dev/urandom")
    try {
        val out = ByteArray(n)
        var off = 0
        while (off < n) {
            val got = memScoped {
                val buf = allocArray<UByteVar>(n - off)
                val r = platform.posix.read(fd, buf, (n - off).toULong()).toInt()
                if (r > 0) for (i in 0 until r) out[off + i] = buf[i].toInt().toByte()
                r
            }
            if (got <= 0) throw IllegalStateException("/dev/urandom 读取失败（$off/$n）")
            off += got
        }
        return out
    } finally {
        platform.posix.close(fd)
    }
}

/** cinterop 是链接期直链（`DT_NEEDED`），所以"能不能用"由加载期决定，没有 dlopen 失败态。 */
val huksBindingStatus: String get() = "cinterop 直链 libhuks_ndk.z.so（DT_NEEDED）"
