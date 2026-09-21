# apps/ios · iOS 端侧宿主（规划中）

**状态：占位。** 还没有代码；这一页记录"开工前必须知道的事"，避免下一次重新摸索。

> ✅ **2026-09-21 定案（UI 用原生 SwiftUI）**：owner 已定 **各端原生 UI**，不用跨端统一 UI。
> 下面 M4 计划里的「SwiftUI 界面」**保持原样**（这正是最终方案）；逻辑层抽成 KMP 共享模块
> （`apps/shared`），iOS 侧直接用这份 Kotlin（KMP 的 Swift interop）。
> CMP 的评估结论归档在 [`docs/多端跨端-CMP方案评估.md`](../../docs/多端跨端-CMP方案评估.md)（仅作备选）。
> 四端范围与顺序见 [`docs/RFC-多端跨端方案.md`](../../docs/RFC-多端跨端方案.md) v1.0。


## M5 进展（2026-09-21）

| 步骤 | 状态 | 证据 |
|---|---|---|
| KMP 共享层编到 iOS/macOS | ✅ | `-PsekbNativeTargets=true` 下 `:shared:compileKotlinIosSimulatorArm64` / `compileKotlinMacosArm64` 通过 |
| framework 产出 | ✅ | `:shared:linkDebugFrameworkIosSimulatorArm64` / `linkDebugFrameworkMacosArm64` → `SharedCore.framework`（`isStatic = true`） |
| **Swift 互操作冒烟** | ✅ | `bash scripts/ios.sh smoke` → **SMOKE OK（5/5）**：Apple 侧 HMAC/SHA256 对齐公知向量、canonical JSON 与 Python 一致、`ToolCallJson.parse` 的 sealed 类导出可用、`Fmt` 与 Android 逐字符一致 |
| iOS App（SwiftUI + 模拟器安装） | ⏳ 下一步 | 计划用 `swiftc` 直编 iOS 模拟器目标 + 手工 `.app` 包（`simctl install`），避免手写 `.xcodeproj` |
| 四个端口（NSURLSession/Keychain/ONNX/PDFKit） | ⏳ | 待 App 骨架跑通后补 |

```bash
bash scripts/ios.sh smoke        # 产出 framework + 编译并运行 Swift 冒烟
bash scripts/ios.sh framework    # 只产出 iOS 模拟器 + macOS 的 framework
```

**三个必须知道的环境事实（都踩过，脚本里已封装）**：
1. `DEVELOPER_DIR` **必须**指向 Xcode（`/Applications/Xcode.app/Contents/Developer`）：
   本机 `xcode-select` 指向 CommandLineTools，不设它会报
   `An error occurred during an xcrun execution / xcrun xcodebuild -version`——
   **表面像 klib 缓存错误，实则是 Xcode 不可用**（我第一次就被它误导）；
2. `KONAN_DATA_DIR` 指向仓库内 `.tooling/konan`（Kotlin/Native 工具链默认写 `~/.konan`，工作区外会被沙箱拒）；
3. `CLANG_MODULE_CACHE_PATH` + `-module-cache-path` 也要指向仓库内（Swift/clang 默认写
   `/var/folders/.../C/clang/ModuleCache`，同样报 `Operation not permitted`）。

## 本机工具链（2026-09-18 实测）

| 项 | 实测值 | 备注 |
|---|---|---|
| Xcode | **26.6**（Build 17F113），`/Applications/Xcode.app` | 已安装 |
| iOS 模拟器运行时 | **26.3 / 26.4** | `xcrun simctl list runtimes` |
| `swift` / `swiftc` | `/usr/bin/swift`、`/usr/bin/swiftc` | 可用 |
| ⚠️ 活动开发者目录 | 指向 `CommandLineTools`（**不是** Xcode） | 直接跑 `xcodebuild` 会报 "requires Xcode" |

两种解法（选一）：

```bash
# A. 临时（无需 sudo，脚本里用这个）
export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
xcodebuild -version

# B. 永久（需要 sudo，改全局状态）
sudo xcode-select -s /Applications/Xcode.app/Contents/Developer
```

`scripts/android.sh` 里已经顺手导出了 `DEVELOPER_DIR`，将来 iOS 构建脚本可直接复用。

## 计划（M4）

1. **先有 `shared/`**：把 `apps/android` 里那批**不 import android.\*** 的纯逻辑
   （`route/` `net/SseParser` `tools/ToolCallJson` `tools/ToolRegistry` `chat/ChatOrchestrator`
   `eval/`）抽成 Kotlin Multiplatform 模块，产出 Android + iOS 两个 target。
   iOS 侧直接用这份 Kotlin（KMP 的 Objective-C/Swift interop），**不要重写一遍**。
2. SwiftUI 界面 + 平台适配：`URLSession` 传输（实现 `HttpTransport`）、
   Keychain 存设备凭证（对应 Android 的 Keystore 实现）、
   iOS 设备工具（时间/网络/通讯录，权限用 `CNContactStore` 授权状态判断）。
3. 验证策略与 Android 一致：**纯逻辑走单测（KMP 里跑）+ 模拟器验功能与协议**，
   性能数字等真机（模拟器不产出性能结论，见 SEKB RFC §9.1）。

## 与 SEKB 的契约

只依赖 [协议文档](../../docs/ops/16-端云协同协议.md)：设备凭证（enroll/refresh/吊销）、
聊天 SSE（含 `execution` 执行位置）、路由事件上报、6 类升级信号。
**协议改了就要同步改这里和三端实现**——这是把它们放进同一个仓库的主要原因。
