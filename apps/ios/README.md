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
| **iOS App 骨架（SwiftUI）** | ✅ | `bash scripts/ios_app.sh run` → 装到 iPhone 17 Pro 模拟器并启动，日志抓到 **`SEKB_IOS_SELFTEST PASS=8 FAIL=0`**：HMAC 向量、规范化 JSON、路由决策、**R10 本机/远端两条对照**、**流式守卫零外泄**、工具调用解析、检索阈值默认值 |
| **iOS 端口①：传输（NSURLSession）** | ✅ | `apps/ios/App/UrlSessionTransport.swift`：实现共享层 `HttpTransport`（同步语义用信号量、SSE 用 `URLSessionDataDelegate` 逐行回调）。自检实测：`transport_get_host_ollama — code=200 bytes=3474`、`transport_stream_lines — code=200 行数=9`；**iOS 自检 10 PASS / 0 FAIL** |
| **iOS 端口②：凭证（Keychain）** | ⚠️ 实现完成 / 往返未验 | `apps/ios/App/KeychainCredentialStore.swift` 实现共享层 `CredentialStore`（接口与 `CredentialCodec` 本轮**一起搬进共享层**，两端同一格式）。自检：空库→nil ✅、清理→nil ✅、轮换规则（共享层 `needsRotation`）✅；**往返 SKIP 且原因明确**：手搓未签名 `.app` 调 Keychain 返回 **-34018 errSecMissingEntitlement**，而 ad-hoc 签名（带任何 entitlements）会让模拟器 SpringBoard **拒绝启动**——两条路互斥。需真实 Xcode 工程 + 签名身份或真机时补验；entitlements 文件已备好在 `apps/ios/App/Sekb.entitlements` |
| **检索链路（RAG）** | ✅ | 分块 → 嵌入 → 向量库 → 检索 → 阈值 → 隐私闸门，**全部走共享层代码**。自检：`rag_ingest`（1 切片，空间 `stub-hash@256`）、`rag_retrieve`（命中 0.244）、`rag_threshold_refuses`（`below_threshold:最高分=0.180<0.99`）、`rag_device_only_gate`（`device_only_requires_on_device_embedding`）；**iOS 自检 20 PASS / 0 FAIL / 1 SKIP** |
| iOS 端口③：嵌入（ONNX） | 🟡 产物已到手，接线待做（2026-09-22） | ORT iOS xcframework **已下载成功**（来源不在 GitHub，见下方"端口③ 调研结论"）；剩余工作是链接 + 用已有 int8 ONNX 模型跑真嵌入并重标阈值 |
| **iOS 端口④：PDF（PDFKit）** | ✅ | `apps/ios/App/PdfExtractor.swift`：与 Android 同一套**四类结果**（有文本 / 无文本层 / 加密 / 解析失败）+ 先判 `%PDF` 魔数 + 40 万字符上限。自检实测：`pdf_extract_text_layer — 页数=1 字符=78`（**与 Android 端同一份样本的 78 字符/1 页完全一致**）、`pdf_extract_no_text_layer`、`pdf_extract_rejects_non_pdf`；**iOS 自检 13 PASS / 0 FAIL** |
| iOS 真机性能 | ⏳ 等硬件 | 模拟器只验功能 |
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

**iOS 侧又踩到的四个坑（`scripts/ios_app.sh` 已封装）**：
1. `simctl launch` 对**已在运行**的 App 只是切到前台，`onAppear` 不再触发 → 自检挪到 `App.init()`，
   且 launch 前先 `simctl terminate`（否则无人值守抓不到任何输出）；
2. 但 `App.init()` 里**不能同步阻塞主线程**等网络（信号量 + URLSession 回调会拿不到执行机会，
   表现为"日志里只有 trustd 请求痕迹、永远等不到自检输出"）→ 自检放 `DispatchQueue.global().async`；
3. 自检含真实网络调用（宿主 Ollama 冷加载可能十几秒）→ 抓日志前等 45s、窗口 90s；
4. 测流式传输时**必须给生成加上限**（`num_predict`）：否则模型长篇生成，
   60s 超时前流不结束，`statusCode` 拿不到（表现为 `code=-1` 而"行数"上万）——
   测的是传输，不该被模型行为影响。`URLSession` 的 delegate 与完成回调**跨队列**，
   状态码要在 `didReceive response` 里加锁记录，不能只在 `didCompleteWithError` 读。

**端口②的一个硬结论（2026-09-21 实测）**：模拟器上"手搓 `.app`"与"Keychain 可用"**互斥**——
未签名 → `SecItemAdd` 报 `-34018`；`codesign -s - --entitlements ...`（哪怕只带
`application-identifier`）→ SpringBoard 拒绝启动（`FBSOpenApplicationServiceErrorDomain code=1`）。
当前选择保住"能启动"（其余自检都依赖它），因此 iOS 自检采用**三态**（PASS/FAIL/**SKIP**，
与 Android 侧自检同口径）："没验"既不算过、也不算失败，原因写进详情。

### 端口③（端侧嵌入）调研结论（2026-09-21 调研，**2026-09-22 解决**）

| 方案 | 现状 | 结论 |
|---|---|---|
| 宿主 Ollama 嵌入（`HostOllamaEmbedding`） | 宿主**没有装任何嵌入模型**（`/api/tags` 只有 qwen3.5/deepseek/llama2 等对话模型） | 需先拉一个嵌入模型；而本机到 ollama registry 的国际链路此前实测不可用（`scripts/edge_m0_setup.sh` 记录了 ModelScope 绕行方案） |
| ONNX Runtime iOS（`onnxruntime-c`） | ✅ **2026-09-22 解决** | 见下方"来源订正" |
| Core ML（把 bge-small 转 coreml） | 需要 `coremltools` 转换环境 + 模型转换验证 | 不再需要：ORT 已到手，Core ML 只作为将来"用 ANE 加速"的可选优化 |

#### 来源订正（2026-09-22）——**ORT 的 iOS 产物不在 GitHub 上**

2026-09-21 的结论「ORT iOS 被网络卡死」是**误判**：当时只试了 GitHub Release（`github.com` 实测 HTTP 000），
就推断唯一来源是 GitHub。实际查 CocoaPods 规格库后真相是：

```bash
# CocoaPods CDN 会 301 到 jsdelivr，用 -L 跟随（不带 -L 会看到 301 而误以为失败）
h=$(echo -n onnxruntime-c | md5 -q)   # 3aa95c6f…，规格库按名字 md5 的前 3 个字符分片
curl -sL "https://cdn.cocoapods.org/Specs/${h:0:1}/${h:1:1}/${h:2:1}/onnxruntime-c/1.20.0/onnxruntime-c.podspec.json"
# → source.http = https://download.onnxruntime.ai/pod-archive-onnxruntime-c-1.20.0.zip
curl -L -o ort-c-1.20.0.zip https://download.onnxruntime.ai/pod-archive-onnxruntime-c-1.20.0.zip
```

- 该 URL 实测 **HTTP 200 / 44,218,716 B**，**完全绕开 GitHub**，也不需要装 CocoaPods；
- 落盘 `.tooling/ort-ios/`（构建状态，不入库），sha256 `50891a8aadd17d4811acb05ed151ba6c394129bb3ab14e843b0fc83a48d450ff`；
- `onnxruntime.xcframework` **三个切片齐全**：`ios-arm64`(真机 33M)、`ios-arm64_x86_64-simulator`(模拟器 71M)、
  `macos-arm64_x86_64`（**顺带解锁 M8 Mac 端**）；
- `nm -gU` 确认模拟器切片导出 `_OrtGetApiBase`，头文件 `ORT_API_VERSION 20` 与 1.20.0 匹配 → 可直接链接。

**经验（写在这里避免重犯）**：判断"某产物被墙"之前，先确认**唯一来源**是不是 GitHub——
CocoaPods 系产物真实托管在 `download.onnxruntime.ai`，Maven 系在 `repo1.maven.org`/镜像，
两者都与 GitHub 无关。另外 `curl` 查这类 CDN 必须带 `-L`。

#### 仍未完成的部分

本轮之前已用共享层的**确定性桩嵌入**打通整条检索链路（与 Android 在"ONNX 模型缺失"时**同一条代码路径**），
证明 iOS 上 RAG 的结构、阈值语义与隐私闸门都成立。**剩余**：把 xcframework 真正链进 App、
用已有的 int8 ONNX 模型（`apps/android` 那份，23.9MB）跑真嵌入，并按新空间**重标阈值**。


## 与 SEKB 的契约

只依赖 [协议文档](../../docs/ops/16-端云协同协议.md)：设备凭证（enroll/refresh/吊销）、
聊天 SSE（含 `execution` 执行位置）、路由事件上报、6 类升级信号。
**协议改了就要同步改这里和三端实现**——这是把它们放进同一个仓库的主要原因。
