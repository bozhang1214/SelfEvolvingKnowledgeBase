# apps/ios · iOS 端侧宿主（规划中）

**状态：占位。** 还没有代码；这一页记录"开工前必须知道的事"，避免下一次重新摸索。

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
