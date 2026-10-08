// SEKB 鸿蒙侧 · 共享层 interop 冒烟（H1）
//
// 目的与 iOS 的 `apps/ios/smoke/Smoke.swift` 完全对称：**在 ohos 目标上真的跑一遍共享逻辑**，
// 而不是"能编译就算过"。四项都取自最容易坏的地方：
//   1. `hmacSha256Hex` —— 鸿蒙侧是**纯 Kotlin** 实现（理由见 `com.sekb.ohos.Sha256Pure`），
//      必须对 RFC 4231 / FIPS 180-4 的**公知向量**逐位相等；
//   2. `sha256Hex`     —— 灰度分桶要与服务端同源（空串/ASCII/UTF-8 各一个向量）；
//   3. canonical JSON  —— 与服务端 Python 逐字节一致（策略验签的前提）；
//   4. `ToolCallJson`  —— shared 的 sealed 类在 Kotlin/Native 上仍可构造与判别；
//   5. `Fmt`           —— 与 Android `FmtTest` 的期望值**逐字符**一致。
//
// 入口是 `sekbSmokeLine()`（`@CName("sekb_spike_smoke")`）：返回一行
// `SMOKE <PASS|FAIL> <n>/<total> [<失败项>]` + 每条明细。NAPI 侧把它打到 hilog，
// `bash scripts/harmony.sh smoke` 会 grep 出来。
@file:OptIn(kotlin.experimental.ExperimentalNativeApi::class)

package com.sekb.ohos.spike

import com.sekb.shared.core.Digests
import com.sekb.shared.core.Fmt
import com.sekb.shared.core.JsonArray
import com.sekb.shared.core.JsonObject
import com.sekb.shared.policy.EdgePolicy
import com.sekb.shared.tools.ToolCallJson

/** 冒烟的一行结果（供脚本 grep），同时把明细一并返回便于在设备上直接看。 */
private class SmokeReport {
    val lines = mutableListOf<String>()
    var failed = 0
    var total = 0

    fun check(name: String, ok: Boolean, detail: String) {
        total++
        if (!ok) failed++
        lines += "${if (ok) "PASS" else "FAIL"} ${name} — ${detail}"
    }

    fun render(): String {
        val head = "SMOKE ${if (failed == 0) "PASS" else "FAIL"} ${total - failed}/${total}"
        return (listOf(head) + lines).joinToString("\n")
    }
}

private fun runSmoke(): SmokeReport {
    val r = SmokeReport()

    // 1) HMAC-SHA256 公知向量（与 iOS 冒烟、Android javax.crypto 同一串）
    val hmac = Digests.hmacSha256Hex(
        key = "key",
        message = "The quick brown fox jumps over the lazy dog",
    )
    r.check(
        "digests_hmac_vector",
        hmac == "f7bc83f430538424b13298e6aa6fb143ef4d59a14946175997479dbc2d1a3cd8",
        hmac,
    )

    // 2) SHA-256 公知向量三连（长度 0 / 3 / 多字节 UTF-8，覆盖 padding 与字节序）
    r.check(
        "digests_sha256_empty",
        Digests.sha256Hex("") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        Digests.sha256Hex(""),
    )
    r.check(
        "digests_sha256_abc",
        Digests.sha256Hex("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        Digests.sha256Hex("abc"),
    )
    val utf8Sha = Digests.sha256Hex("端侧知识库")
    r.check(
        "digests_sha256_utf8",
        utf8Sha == "76ad06a8b09c73f397b178bbab45704e316abe39e4b0f8d95040f229d803fa1d",
        utf8Sha,
    )

    // 3) canonical JSON：键排序 + 无空格，必须与服务端 Python 逐字节一致
    //    （策略验签的载体，差一个空格就验不过）
    val canonical = EdgePolicy.canonicalJson(
        JsonObject().put(key = "b", value = 1)
            .put(key = "a", value = JsonArray().put(value = 1).put(value = 2)),
    )
    r.check("canonical_json_matches_python", canonical == """{"a":[1,2],"b":1}""", canonical)

    // 4) ToolCallJson：合法 / 非法两条路径，证明 sealed 类在 Native 上可用
    val okParse = ToolCallJson.parse("""{"tool":"device_time","args":{}}""")
    r.check(
        "toolcall_parse_ok",
        okParse is ToolCallJson.Parsed.Ok && (okParse as ToolCallJson.Parsed.Ok).call.tool == "device_time",
        okParse.toString().take(60),
    )
    val badParse = ToolCallJson.parse("这不是 JSON")
    r.check(
        "toolcall_parse_invalid",
        badParse is ToolCallJson.Parsed.Invalid,
        (badParse as? ToolCallJson.Parsed.Invalid)?.reason ?: badParse.toString(),
    )

    // 5) Fmt 与 Android `FmtTest` 逐字符一致（期望值直接抄自那个测试，不是我自己编的）
    r.check("fmt_fixed_0_6902", Fmt.fixed(0.6902, 3) == "0.690", Fmt.fixed(0.6902, 3))
    r.check("fmt_fixed_neg_zero", Fmt.fixed(-0.0001, 3) == "0.000", Fmt.fixed(-0.0001, 3))
    r.check("fmt_pct_87", Fmt.pct(0.8734) == "87", Fmt.pct(0.8734))
    r.check("fmt_pad", Fmt.padStart(7, 2) == " 7" && Fmt.padEnd("free", 4) == "free", "padStart/padEnd")

    return r
}

/**
 * NAPI 入口：返回整段冒烟报告（多行）。
 *
 * 为什么返回字符串而不是自己打 hilog：日志 API 在 Native 侧要多引一个
 * `libhilog_ndk.z.so`，而这里**只需要把结果交给已经在打日志的 NAPI 层**——
 * 少一个依赖 + 少一个能出错的地方。
 */
@CName("sekb_spike_smoke")
fun sekbSpikeSmoke(): String = runSmoke().render()
