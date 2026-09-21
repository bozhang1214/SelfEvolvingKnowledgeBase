import Foundation
import SharedCore

// 端侧共享层的 Swift 互操作冒烟（M5 第一步）。
//
// 目的：**在 Apple 上真的跑一遍共享逻辑**，而不是"能编译就算过"。重点验证三件最容易坏的事：
//   1. Apple 侧的 CCHmac/CC_SHA256 实现（我自己写的 actual）算出来的签名对不对；
//   2. JSON 门面在 native 上的解析与 sealed 类导出（ToolCallJson.parse 的 Ok/Invalid）；
//   3. 纯逻辑（Fmt 格式化）在 native 上与 Android 逐字符一致。
//
// 运行：bash scripts/ios.sh smoke（编译 macosArm64 的 framework 并执行本文件）。

var failures: [String] = []
func check(_ name: String, _ ok: Bool, _ detail: String) {
    print("\(ok ? "PASS" : "FAIL") \(name) — \(detail)")
    if !ok { failures.append(name) }
}

// 1) HMAC-SHA256（Apple 的 CommonCrypto 实现）对齐公知向量
let hmac = Digests.shared.hmacSha256Hex(
    key: "key",
    message: "The quick brown fox jumps over the lazy dog")
check("digests_hmac_vector",
      hmac == "f7bc83f430538424b13298e6aa6fb143ef4d59a14946175997479dbc2d1a3cd8",
      hmac)

// 2) SHA-256
let sha = Digests.shared.sha256Hex(message: "abc")
check("digests_sha256_vector",
      sha == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
      sha)

// 3) 服务端签名载体一致性：canonical JSON 必须与 Python 逐字节一致
let canonical = EdgePolicy.shared.canonicalJson(value: JsonObject().put(key: "b", value: 1)
    .put(key: "a", value: JsonArray().put(value: 1).put(value: 2)))
check("canonical_json_matches_python", canonical == "{\"a\":[1,2],\"b\":1}", canonical)

// 4) JSON 门面 + sealed 类导出：工具调用解析
let parsed = ToolCallJson.shared.parse(text: "{\"tool\":\"device_time\",\"args\":{}}")
if let ok = parsed as? ToolCallJsonParsedOk {
    check("toolcall_parse_ok", ok.call.tool == "device_time", ok.call.tool)
} else {
    check("toolcall_parse_ok", false, "得到 \(type(of: parsed))")
}
let bad = ToolCallJson.shared.parse(text: "这不是 JSON")
check("toolcall_parse_invalid", bad is ToolCallJsonParsedInvalid, "\(type(of: bad))")

// 5) 格式化与 Android 一致（%.3f 等价）
check("fmt_matches_android", Fmt.shared.fixed(value: 0.6902, digits: 3) == "0.690",
      Fmt.shared.fixed(value: 0.6902, digits: 3))

print(failures.isEmpty ? "SMOKE OK（5/5）" : "SMOKE FAILED: \(failures)")
exit(failures.isEmpty ? 0 : 1)
