## 2026-09-21（M5 第一步：SharedCore.framework + Swift 互操作冒烟 5/5）

- **背景**：M5 的第一件事不是写界面，而是**证明 Kotlin 共享层在 Apple 上真能跑**——
  能编译只是最低要求，签名算法（我自己写的 `CCHmac` actual）、JSON 门面、sealed 类导出
  这些"跨语言边界上的东西"必须在运行时验一遍。

- **改动**：
  - `apps/shared/build.gradle.kts`：native target 产出 **`SharedCore.framework`**（`isStatic = true`）。
  - `core/Digests.kt`（新）：把 `hmacSha256Hex` / `sha256Hex` 包成 `object`——
    Kotlin/Native 导出顶层函数时 Swift 里看到的是 `<文件名>Kt.xxx`，文件名一变类名就变；
    包一层后 Swift 稳定写 `Digests.shared.hmacSha256Hex(...)`。
  - `apps/ios/smoke/Smoke.swift` + `scripts/ios.sh`（新）：一条命令产出 framework 并
    编译运行 Swift 冒烟（macOS host 上真跑共享逻辑）。

- **验证（实测输出）**：
  ```
  PASS digests_hmac_vector — f7bc83f430538424b13298e6aa6fb143ef4d59a14946175997479dbc2d1a3cd8
  PASS digests_sha256_vector — ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad
  PASS canonical_json_matches_python — {"a":[1,2],"b":1}
  PASS toolcall_parse_ok — device_time
  PASS toolcall_parse_invalid — SharedCoreToolCallJsonParsedInvalid
  PASS fmt_matches_android — 0.690
  SMOKE OK（5/5）
  ```
  即：Apple 侧的 HMAC/SHA256 对齐公知向量；规范化 JSON 与 Python 逐字节一致；
  封接口/密封类在 Swift 里可用；格式化与 Android 一致。

- **踩到并记下的三个环境事实**（`scripts/ios.sh` 已封装，`apps/ios/README.md` 有完整版）：
  1. **`DEVELOPER_DIR` 必须指向 Xcode** —— 不设会报 `xcrun xcodebuild -version` 失败，
     而 Gradle 的表面错误是 `Failed to build cache for ...kotlinx-serialization-core-...klib`
     （**很容易误判成 klib 版本不兼容**，我第一次就查错了方向）；
  2. `KONAN_DATA_DIR` → 仓库内（工具链默认写 `~/.konan`，工作区外被沙箱拒）；
  3. `CLANG_MODULE_CACHE_PATH` / `-module-cache-path` → 仓库内（默认写 `/var/folders/...`，
     报 `Operation not permitted`）。

- **下一步（M5 第二步）**：iOS App 骨架——用 `swiftc` 直编 iOS 模拟器目标 + 手工 `.app` 包
  （`Info.plist` + 二进制）再 `simctl install`，**避免手写 `.xcodeproj`**；跑通后再补
  NSURLSession / Keychain / ONNX / PDFKit 四个端口。
