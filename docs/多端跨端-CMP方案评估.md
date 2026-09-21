---
title: 多端跨端 · CMP（Compose Multiplatform）方案评估
layer: 设计层
owner: SEKB Team
status: draft（待 owner 决策）
version: v0.1.0
last-updated: 2026-09-21
based-on-commit: ad6540c
related: [docs/RFC-多端跨端方案, docs/RFC-端云协同与端侧Agent, apps/README, apps/ios/README, apps/harmony/README]
---

# 多端跨端 · CMP 方案评估（v1）

> **本文回答三件事**
> ① owner 的问题「**CMP 会影响原生体验吗**」——按平台逐项给证据，不给态度；
> ② 如果做 CMP，**长什么样**：模块划分、现有 Android UI 怎么搬、与原生互操作的 Kotlin 接口签名、Android 构建怎么改；
> ③ **诚实的结论**：什么情况值得、什么情况不值得；若最终选原生，这份文档里哪些设计**仍然有用**。
>
> **背景**：我在 [`RFC-多端跨端方案.md`](RFC-多端跨端方案.md) 里建议 UI 统一到 CMP；
> owner 的回复是「先给一版 CMP 的设计方案和接口设计，最终我倾向于用原生开发，**CMP 会影响原生体验？**」。
> 这份文档就是对这个问题的正面回答 + 可执行的设计。
>
> **口径**：外部事实截至 **2026-09-21**；CMP 最新稳定版 **1.12.0**（2026-08-25 发布），
> Kotlin/Native 官方 target 列表**不含鸿蒙**（§6 E-3）。每条外部事实标
> **✅ 已核验**（官方来源）、**📄 第三方**（仅旁证）或 **⚠️ 待本地验证**（§1.3 给方法与门槛）。
>
> **边界**：本文只评估 **CMP（共享 UI）** 这一支；KMP 逻辑共享、桌面端方案由同批其他评估文档分别覆盖，
> 三者结论互不依赖。本文不含任何已写代码。

---

## 0. 结论先行（一屏）

| 问题 | 结论 |
|---|---|
| CMP 会**破坏**原生体验吗？ | 不会破坏，但会**替换**：iOS/鸿蒙上界面由 Skia **自绘**，选择菜单、手势返回、键盘、无障碍这些原本由系统提供的东西，改成 CMP 自己实现或经 interop 桥接。**差距真实存在，集中在「文本输入 / 手势 / 系统控件」三类。** |
| 差多少？（按平台递减） | **Android ≈ 0**：官方明确 Android 侧映射回**原版 androidx** 产物，等于没换 UI 框架（§6 E-4）；**iOS 中等**：1.9 起持续补齐，**1.11 才提供原生文本输入**且仍是实验特性（E-7）；**鸿蒙最大**：社区 fork，CMP 落后上游 **3 个小版本**（1.9.2 vs 1.12.0，E-9）；**桌面**唯一无历史包袱（E-2/E-14）。 |
| 落在本项目上要紧吗？ | 我们现在 UI 只有 **1 个界面 + 3 个面板、`ui/ChatScreen.kt` 265 行**，无自绘、无复杂动画、无系统控件深度集成。**"能不能用"的风险低，"手感差异"的风险高。** 但聊天输入框是核心交互——**中文 IME 必须实测**（V-1）。 |
| 建议 | 若真要做 **iOS + 鸿蒙 + 桌面 GUI 且只有一人维护** → CMP 值得（UI 写 1 遍 vs 3–4 遍）；若只做 **iOS + Android**，或认为"原生观感就是产品差异点" → **KMP 共享逻辑 + 各端原生 UI** 更划算（§4，也是 owner 倾向）。 |

**一句话**：CMP 的代价不是"跑不起来"，而是**把平台细节的实现责任从系统搬到了你身上**；换来的是 UI 只写一遍。

---

## 1. 正面回答：「CMP 会影响原生体验吗？」

### 1.1 先定标准：把"原生体验"拆成 5 类

| 类别 | 内容 | CMP 带来的额外影响 |
|---|---|---|
| **A. 渲染与滚动手感** | 惯性、回弹、帧率、下拉刷新 | **中**：自绘（Skia/Skiko），官方称 iOS/Android 滚动"与平台对齐"，但需实测 |
| **B. 系统输入** | 软键盘/中文 IME、文本选择、上下文菜单 | **高**：最容易被用户感知的一类 |
| **C. 系统集成** | 分享、通知、权限、后台、文件选择 | **高，但与 UI 方案无关**：不用 CMP 也得各端写 |
| **D. 无障碍与系统偏好** | VoiceOver、动态字体、高对比度 | **中**：语义可映射到原生无障碍；高对比度要自己写 |
| **E. 观感细节** | 字体、系统控件样式、动效曲线 | **中**：官方明确不做像素级一致 |

**关键区分**：C 类不是 CMP 的锅——它是"任何跨端方案都要写的端口"。真正由 CMP 额外引入的风险只有 **A/B/D/E**。

### 1.2 能力项逐条对照（iOS / Android / 鸿蒙 / 桌面）

| # | 能力项 | CMP 现状 | 来源 | 状态 | 对本项目的影响与缓解 |
|---|---|---|---|---|---|
| 1 | **文本输入与中文 IME** | 默认走 Compose 自研输入（跨端稳定）；1.9 加 `PlatformImeOptions`（键盘类型/自动纠错/return key）；1.10 加 `inputView`/`inputAccessoryView`；**1.11 起可切原生 UIKit 输入**（`UITextInput`/`UIKeyInput`，`usingNativeTextInput(true)`，**实验特性**） | [1.9.3 IME](https://kotlinlang.org/docs/multiplatform/whats-new-compose-190.html#ime-options)、[1.10.3](https://kotlinlang.org/docs/multiplatform/whats-new-compose-110.html)、[1.11.1 原生文本输入](https://kotlinlang.org/docs/multiplatform/whats-new-compose-111.html#native-text-input) | ✅ 已核验（API 存在）／⚠️ 中文候选词与联想手感 **V-1** | 聊天输入框是主路径。缓解：`WindowInsetsRulers` + `useSoftwareKeyboardInset`（1.10 转正）防遮挡；iOS 打开 `usingNativeTextInput` 并把它写进验收项 |
| 2 | **文本选择与上下文菜单** | 1.9 引入新上下文菜单 API（`SelectionContainer`/`BasicTextField`，需 `ComposeFoundationFlags.isNewContextMenuEnabled = true`），**iOS 与 Web 实现完整**，桌面初步；1.11 原生输入自带系统菜单（Autofill/Translate/Search） | [1.9.3 上下文菜单](https://kotlinlang.org/docs/multiplatform/whats-new-compose-190.html#new-context-menu-api) | ✅ 已核验；历史 issue [#5259](https://github.com/JetBrains/compose-multiplatform/issues/5259)（选择/粘贴菜单缺失）**已于 2025-03 关闭** | 现有界面只显示回答文本，选择需求弱；若要做"复制回答"，用新 API + `SelectionContainer` |
| 3 | **滚动惯性与手势返回** | 官方："Android 与 iOS 的滚动手感**与平台对齐**"；返回手势：Android 默认有、**iOS 由 CMP 主动提供**（模仿 Android）；1.10 起端侧（右）边缘 pan 由 `EndEdgePanGestureBehavior` 控制、**默认 Disabled**，起始边缘始终绑返回；`PredictiveBackHandler` 在 1.10 **已弃用** → 改用 `NavigationBackHandler` | [平台差异](https://kotlinlang.org/docs/multiplatform/compose-platform-specifics.html)、[1.10.3 Navigation 3/返回](https://kotlinlang.org/docs/multiplatform/whats-new-compose-110.html) | ✅ 官方口径已核验／⚠️ 惯性细节（回弹、快速甩动、状态栏点按回顶）**V-2** | 我们是单页 + 列表，返回手势只在二级页出现；引出"iOS 上首次引入返回手势语义"这一差异，需产品确认 |
| 4 | **无障碍（VoiceOver 等）** | Compose 语义映射到原生无障碍对象，VoiceOver 可读、Material 组件基本自动；`testTag` → `accessibilityIdentifier`；XCTest 可跑 `performAccessibilityAudit()`；**高对比度不自动**（需自备调色板 + `UIAccessibilityDarkerSystemColorsEnabled`）；无障碍树默认按需同步（可 `Always` 但掉性能） | [iOS 无障碍支持](https://kotlinlang.org/docs/multiplatform/compose-ios-accessibility.html) | ✅ 已核验／⚠️ 真机 VoiceOver 走查 **V-3** | 现有 UI 只有 Text/Button/TextField，`contentDescription` 已写；高对比度需补一版 Material3 配色（小工作量） |
| 5 | **系统控件（操作表/日期/弹窗）** | CMP 不封装系统原生控件，用 Material3 组件替代；官方明确"某些原生弹层（如文本选择的系统建议动作）按平台不同，且无法定制" | [平台差异](https://kotlinlang.org/docs/multiplatform/compose-platform-specifics.html) | ✅ 已核验／⚠️ 观感对比 **V-4** | 我们没有日期选择/操作表；只有 `Dialog/Popup`（1.10 起 `usePlatformInsets`/`useSoftwareKeyboardInset` 转正） |
| 6 | **分享面板** | CMP 无内置分享 API → 需自写端口：Android `Intent.ACTION_SEND`、iOS `UIActivityViewController`、鸿蒙 want/`startAbility`、桌面无统一标准 | [Android 分享](https://developer.android.com/training/sharing/send)、[UIActivityViewController](https://developer.apple.com/documentation/uikit/uiactivityviewcontroller) | ✅ 平台 API 存在／⚠️ 端口实现 **V-5** | 价值高（"把这条回答分享出去"），但**与 UI 方案无关**：原生 UI 也要写一遍 |
| 7 | **暗色模式与动态字体** | Material3 + `isSystemInDarkTheme` 跨端可用；文本用 `sp`。iOS Dynamic Type（系统字号）跟随程度文档未明确承诺 | [平台差异（字体/文本）](https://kotlinlang.org/docs/multiplatform/compose-platform-specifics.html) | ⚠️ **V-6** | 现有界面大量 `fontSize = 10–14.sp`、硬编码色值（如 `Color(0xFF1B5E20)`）；迁移时正好收敛成主题色 |
| 8 | **启动时间** | 无官方公开数字；Kotlin/Native 运行时 + Compose 首帧会带来冷启动增量；Android 侧因已用 Compose，增量≈共享层初始化 | —（无公开数据，不猜） | ⚠️ **V-7** | 冷启动对本产品不算卖点，但"点开即用"仍要量；门槛见 §1.3 |
| 9 | **包体积** | iOS：Kotlin/Native 框架 + Skia 体积明显；官方给减重开关 `smallBinary`、`latin1Strings`（均实验）；Android：**映射回 androidx 原版**，增量≈共享代码 + R8 | [二进制选项](https://kotlinlang.org/docs/native-binary-options.html)、[AndroidX 打包方式](https://kotlinlang.org/docs/multiplatform/compose-multiplatform-jetpack-libraries.html) | ✅ 机制已核验／⚠️ 实测增量 **V-8** | 端侧 APK 现在 ≈ 带 ONNX 模型（int8 23.9MB）；若 iOS 包体再涨 8MB+ 要权衡 |
| 10 | **内存** | 官方给内存相关开关：`mmapTag`（Apple 平台按 tag 追踪内存）、`appStateTracking`（后台 GC 策略，省电）、GC 默认 `cms`（2.4.0 起） | [二进制选项](https://kotlinlang.org/docs/native-binary-options.html) | ✅ 机制已核验／⚠️ 实测 **V-9** | 端侧 2B 模型本来就吃内存，UI 层多占要和推理抢内存；门槛见 §1.3 |
| 11 | **GPU 上下文 / 原生视图叠放** | iOS 上 Compose 自绘到自己的渲染层；原生视图 interop 有统一触摸策略（**150ms 协作延迟**启发自 `UIScrollView`），1.12 起可用 `interactionMode = Cooperative/NonCooperative` 与 `isInteractive=false` 调优；`placedAsOverlay`（1.10）可把原生视图放到 Compose 之上，**但会遮住同区域其它内容** | [interop 触摸](https://kotlinlang.org/docs/multiplatform/compose-ios-touch.html)、[1.10.3 overlay](https://kotlinlang.org/docs/multiplatform/whats-new-compose-110.html) | ✅ 已核验／⚠️ 帧率实测 **V-10** | 我们**不需要** embed 原生视图（聊天+列表全是 Compose），所以这条风险几乎为零——这是本项目的一个有利事实 |
| 12 | **调试体验** | 桌面：**内置 Compose Hot Reload**（1.10 起随插件打包），1.12 还带 MCP server（AI agent 可触发重载/截屏/读语义树）；Android：Android Studio 如常；**iOS/鸿蒙：无热重载**（官方明确 hot reload 仅 JVM/桌面），走 Xcode/DevEco + LLDB，`sourceInfoType` 可提升堆栈可读性 | [平台差异（热重载）](https://kotlinlang.org/docs/multiplatform/compose-platform-specifics.html)、[1.12.0 MCP](https://kotlinlang.org/docs/multiplatform/whats-new-compose-112.html)、[二进制选项](https://kotlinlang.org/docs/native-binary-options.html) | ✅ 已核验 | 桌面开发体验**比原生更好**；iOS 上会**比 SwiftUI 差**（改一行要重编 Kotlin/Native + 重启 App） |
| 13 | **后台与生命周期** | `appStateTracking` 控制后台 GC；后台任务/唤醒仍要平台 API | [二进制选项](https://kotlinlang.org/docs/native-binary-options.html) | ✅ 机制已核验 | 我们目前无后台任务（除导入文档），风险低 |
| 14 | **通知** | CMP 无内置通知 API → 需端口（Android 渠道+权限、iOS `UNUserNotificationCenter`、鸿蒙 notificationManager） | — | ⚠️ **V-11** | 现阶段没有通知需求；不构成选型理由 |
| 15 | **桌面特有** | 桌面**不支持多点触控**（捏合缩放做不了）；滚动仅鼠标滚轮；1.12 提供 Window/Dialog API v2（实验）；打包走 Compose Gradle 任务（MSI/dmg/deb/AppImage） | [平台差异](https://kotlinlang.org/docs/multiplatform/compose-platform-specifics.html)、[1.12.0](https://kotlinlang.org/docs/multiplatform/whats-new-compose-112.html) | ✅ 已核验／⚠️ Linux 打包实测 **V-12** | 桌面定位是"算力宿主 + 文件主场"（见 RFC §4 D3），GUI 优先级低 |
| 16 | **鸿蒙整体** | 官方 Kotlin/Native **无 OHOS target**（E-3）；鸿蒙靠社区 fork **CPF-KMP-CMP**：KMP `2.2.21-1.0.0` + CMP `1.9.2-1.0.0`，`OHRenderer`/ArkUI 渲染，`@ArkEntry` 双向互操作，要求 DevEco 6.0.0 / API17 | [Kotlin/Native targets](https://kotlinlang.org/docs/native-target-support.html)、[CPF-KMP-CMP 组织](https://atomgit.com/CPF-KMP-CMP)、[第三方介绍](https://jishuzhan.net/article/2096032868351528961) | ✅ 无官方 target 已核验；📄 fork 细节为第三方来源 | **最大不确定性**：fork 落后上游 3 个小版本（1.11 的 iOS 原生输入等改进拿不到），且要锁 Kotlin fork 版本 → **先 3 天 spike 再谈**（RFC §4 D2 已写四条验收） |

### 1.3 待本地验证清单（先定门槛，避免"看着还行"）

> 规则：**每条都要能量化、能失败**；验证不通过就触发 §4.4 的回退条件。

| # | 待验证项 | 方法（可执行） | 判定门槛（先写死） |
|---|---|---|---|
| **V-1** | 中文 IME：拼音候选、联想、删除整词、键盘遮挡、return key | iOS 模拟器 + CMP 1.11+/1.12 空工程放一个 `BasicTextField`，分别测**默认输入**与 `usingNativeTextInput(true)`；Android 同步对照 | 中文输入无丢字/错位；键盘不遮挡输入框；候选词栏正常；与 Android 现有手感"主观等价" |
| **V-2** | iOS 滚动惯性 / 回弹 / 返回手势 | 同一段长列表（≥200 条气泡）在 CMP 与 SwiftUI 各跑一遍，对比甩动减速度、回弹、边缘返回触发区 | 60fps、无掉帧（p95 帧 ≤16.7ms）；返回手势从起始边缘触发成功率 ≥95% |
| **V-3** | VoiceOver 走查 | Xcode Accessibility Inspector + `performAccessibilityAudit()`（官方支持） | 关键元素均可聚焦、可朗读、有 `contentDescription`；审计无严重项 |
| **V-4** | Dialog/Popup 观感 | 与原生 `UIAlertController` 并排截图对比 | 无遮挡/错位；圆角与遮罩可接受（不要求像素一致） |
| **V-5** | 分享端口 | iOS `UIActivityViewController`、Android `Intent.ACTION_SEND` 各接一次，从聊天页分享一条回答 | 两端都能调起系统分享面板并带出文本 |
| **V-6** | 暗色模式 + iOS 动态字体 | 系统切暗色 / 调大字号（设置→显示→文字大小）后截图 | 暗色下无硬编码色错乱；字号变化有可预期缩放（若 Dynamic Type 不跟随，明确记为已知差异） |
| **V-7** | 冷启动增量 | iOS：`xcrun simctl launch` 计时 + Instruments App Launch；Android：`adb shell am start -W` | 相对原生基线增量 ≤400ms 或 ≤1.5×（先取严的一条） |
| **V-8** | 包体积增量 | Android `apkanalyzer apk compare`；iOS 对比 `.ipa`/dSYM（开 `smallBinary`） | Android ≤ +2MB；iOS ≤ +8MB（达标即接受，超标再开减重开关） |
| **V-9** | 内存峰值 | Instruments Allocations/VM Tracker + `mmapTag`；导入 3 份文档 + 10 轮问答 | 常驻内存增量 ≤40MB；无 OOM |
| **V-10** | 帧率 / GPU | Instruments Core Animation（iOS）；`gfxinfo`（Android） | 聊天滚动 60fps；无持续掉帧 |
| **V-11** | 通知权限（若将来需要） | 各端写一个最小 Notifier 端口 | 能弹通知且权限被正确请求 |
| **V-12** | 鸿蒙 spike（RFC §4 D2 四条）+ 桌面 Linux 打包 | 本机 DevEco SDK API 22 + OHOS NDK；桌面 `packageDeb`/AppImage | 四条全通才排期；任一不通退回 ArkTS 重写（RFC 已写死） |

**⚠️ 说明**：上表全部**尚未执行**——本机没有 iOS CMP 工程，也没有鸿蒙 CMP 工程。这是本文档最重要的诚实边界：
**"CMP 会不会影响原生体验"目前只能给出"官方已支持哪些 + 哪些必须实测"，不能给出"我测过，没问题"。**

---

## 2. CMP 设计方案 v1

### 2.1 目标结构（与 AGP 9 的硬约束对齐）

AGP 9 有两条硬约束（[JetBrains AGP 9 迁移文档](https://github.com/JetBrains/skills/blob/main/kotlin-tooling-agp9-migration/references/MIGRATION-APP-SPLIT.md) ✅ 已核验）：
`com.android.application` **不能**与 `org.jetbrains.kotlin.multiplatform` 同模块；且 AGP 9 的应用插件**自带 Kotlin**，不能再 apply `org.jetbrains.kotlin.android`。
→ 所以结构必须是「**纯 Android 应用模块 + KMP 共享库模块**」，这正好与 RFC §5.1 的五层布局一致：

```text
apps/
├── settings.gradle.kts          # include(":app", ":shared")；wrapper 上移到 apps/
├── shared/                      # KMP 共享层（唯一新模块）
│   ├── core/                    # L3 纯逻辑：route/ chat/ rag/ embed/ eval/ net 协议层
│   ├── platform/                # L4 端口接口（commonMain）+ 各端 actual 实现
│   └── ui/                      # L5 CMP 界面（commonMain）
├── contract/                    # 语言无关契约夹具（JSON）+ 各端 runner 约定
├── android/                     # 现有工程：app/ 保持"纯 Android 应用模块"
├── ios/                         # Xcode 工程 + iOS 端口实现
├── harmony/                     # DevEco 工程（若 spike 通过）
└── desktop/                     # headless 宿主（先）+ CMP 桌面入口（后）
```

**为什么 `shared/` 拆成三个子目录而不是一个**：`core` 与 `ui` 的**回退边界**不同——
如果 iOS 的 CMP UI 不行，我们只回退 `ui`（换 SwiftUI），`core/platform` 一行不动（RFC §4 D1 已写死这条回退）。

### 2.2 现状 Android UI 如何映射（逐文件）

| 现状文件（`apps/android/app/src/main/kotlin/com/sekb/ondevice/`） | 行数 | CMP 目标 | 要动的点 |
|---|---|---|---|
| `ui/ChatScreen.kt` | 265 | `shared/ui/` 的聊天页 | **只有 2 处平台耦合**：`rememberLauncherForActivityResult` + `ActivityResultContracts.OpenMultipleDocuments`（Android-only）→ 改为 `FilePicker` 端口（挂起函数）；`collectAsStateWithLifecycle` → 多平台 lifecycle 版本。其余（`Scaffold`/`LazyColumn`/`Card`/`OutlinedTextField`/Material3/`PasswordVisualTransformation`）**原样可编** |
| `ui/ChatViewModel.kt` | 330 | 拆两半：`shared/ui/` 的 `ChatStateHolder`（纯状态机）+ 各端薄壳 | 三处耦合：`AndroidViewModel`（→ 多平台 `ViewModel` + 端口注入）、`BuildConfig`（→ `AppInfo` 端口）、`android.net.Uri`（→ `PickedFile`）；`Dispatchers.IO` → `DispatcherProvider`（**Native 上 IO 调度器可用性待验证**，见 §7 Q4） |
| `ui/DocumentImporter.kt` | 121 | `decide`/`looksBinary`/`isPdf` 三个**纯函数**进 `shared/core`；`read(context, uri)` 退化为端口调用 | `Context`/`Uri`/`ContentResolver` 全部移出；两个上限常量（2MB/20MB）与四类拒绝原因保留语义 |
| `ui/PdfExtractor.kt` | 69 | 接口 `PdfExtractor` + `PdfExtraction` 四态**原样**进 `shared/platform`；`PdfBoxExtractor` 留 `androidMain`（桌面 JVM 同实现），iOS 换 PDFKit | 已经是 `fun interface`，**零逻辑改动**；`PDFBoxResourceLoader.init` 留在 Android 容器 |
| `ui/RetrievalSources.kt` | 45 | `shared/ui/`（纯逻辑） | 零改动 |
| `MainActivity.kt` | — | Android 入口只留 `setContent { App() }` 与自检/评测 flag；iOS 入口 `MainViewController`；桌面 `main()` | 自检（26/26）与检索评测入口改为调用共享 `SelfTest`/`RetrievalEvalRunner` |
| `SekbApp.kt`（`AppContainer`） | 168 | `shared/` 的 `AppContainer`（构造参数是端口）+ 各端工厂 | `KeystoreCredentialStore`/`OkHttpTransport`/`OnnxBgeEmbedding`/`AndroidDeviceTools`/`AndroidPermissionChecker` 全部变成注入项 |

**结论**：**UI 层真正"必須重写"的只有文件选择这一处**（SAF → 端口）；其余是"搬家 + 拆壳"。这与 RFC §1.1 的实测一致（main 5,406 行里 3,786 行不 import `android.*`）。

### 2.3 导航与状态管理选型

| 决策 | 选择 | 理由 |
|---|---|---|
| 导航 | **v1 不引入导航库**（单页 + 面板）；需要二级页时用 `org.jetbrains.androidx.navigation:navigation-*`（2.9.x 稳定线），**不用** Navigation 3（1.12 里仍是 alpha） | 现在只有 1 个界面，引入导航是过度设计；返回手势相关 API 到时用 `NavigationBackHandler`（`PredictiveBackHandler` 已在 1.10 弃用） |
| 状态 | `MutableStateFlow<UiState>` + `collectAsStateWithLifecycle`，**单一状态对象**（沿用现状 `ChatUiState`） | 与现有代码一致，迁移成本≈0；不引 DI 框架（168 行的显式容器够用） |
| 线程 | 逻辑保持**同步**（现有 `ChatOrchestrator` 就是同步的），异步只在 UI 层；`DispatcherProvider` 端口提供 `io`/`main` | 保持"逻辑可在 JVM 单测里跑"这一现有优势（182 个用例里绝大多数是 JVM 测试） |
| 主题 | Material3 主题收敛硬编码色值（`Color(0xFF1B5E20)` 等）+ 补一版高对比度配色 | §1.2 第 4/7 项的缓解措施，正好借迁移做掉 |

### 2.4 与原生互操作的接口设计（Kotlin 签名）

**原则：用「接口 + 各端实现 + 工厂」而不是 `expect/actual`。** 理由：接口可以在 JVM 单测里注入假实现（项目已有 `InMemoryCredentialStore`/假 `HttpTransport`/`DeterministicEmbedding` 这套做法），
`expect/actual` 无法在 common 里提供默认实现、也不便于"同一端多个实现"（如 Android 上有 Keystore 版和内存版）。

```kotlin
// shared/platform/.../Ports.kt（commonMain）
interface PlatformPorts {
    val appInfo: AppInfo                  // 版本号 + 默认端点（替代 BuildConfig）
    val credentials: CredentialStore      // 已存在：Keystore / Keychain / HUKS / 0600 文件
    val files: FilePicker                 // 新增：替代 SAF
    val pdf: PdfExtractor                 // 已存在：PdfBox(Android/桌面) / PDFKit(iOS)
    val share: ShareService               // 新增
    val clipboard: Clipboard              // 新增
    val permissions: PermissionChecker     // 已存在（fun interface）
    val transport: HttpTransport          // 已存在（同步接口，SSE 用 postJsonStream）
    val embedder: EmbeddingProvider       // 已存在：space + isOnDevice + embed
    val vectorStore: VectorStoreFactory    // 已存在 VectorStore 接口，工厂按端给 SQLite 实现
    val notifier: Notifier?                // 可空：现阶段不需要
    val dispatchers: DispatcherProvider
}

data class AppInfo(
    val versionName: String,
    val defaultEdgeBaseUrl: String,       // 现 BuildConfig.DEFAULT_EDGE_BASE_URL
    val defaultSekbBaseUrl: String,       // 现 BuildConfig.DEFAULT_SEKB_BASE_URL
    val osName: String,
)

/** 文件选择：唯一真正新增的端口（Android SAF 在 CMP common 里不可用）。 */
interface FilePicker {
    suspend fun pickDocuments(
        accept: List<String> = listOf("text/*", "application/pdf"),
    ): List<PickedFile>
}

data class PickedFile(
    val name: String,
    val sizeBytes: Long,
    /** 惰性读取：避免把 20MB PDF 一次性驻留内存（现有实现也是流式读完再判上限）。 */
    val bytes: () -> ByteArray,
)

interface ShareService { suspend fun share(text: String, subject: String? = null) }

interface Clipboard { fun copy(text: String); fun paste(): String? }

interface Notifier {
    suspend fun notify(title: String, body: String)
    suspend fun ensurePermission(): Boolean
}

interface DispatcherProvider { val io: CoroutineDispatcher; val main: CoroutineDispatcher }
```

```kotlin
// shared/ui/.../SekbTextField.kt（commonMain）：IME 只在这一处做端差异
@Composable
fun SekbTextField(
    value: String,
    onValueChange: (String) -> Unit,
    modifier: Modifier = Modifier,
    singleLine: Boolean = false,
    password: Boolean = false,
    imeAction: ImeAction = ImeAction.Default,
)

// iosMain：打开 1.11+ 的原生文本输入（实验特性，出问题可一键回退）
//   PlatformImeOptions { usingNativeTextInput(true) }
// androidMain：沿用现有 KeyboardOptions + PasswordVisualTransformation
```

| 端口 | Android | iOS | 鸿蒙（若做） | 桌面 |
|---|---|---|---|---|
| `FilePicker` | `ActivityResultContracts.OpenMultipleDocuments`（SAF） | `UIDocumentPickerViewController` | `@ohos.file.picker` | `java.awt.FileDialog` / 或先用 headless 宿主 |
| `PdfExtractor` | PdfBox-Android（现用） | PDFKit | 暂无（如实拒绝，保持四类原因语义） | PdfBox（JVM） |
| `ShareService` | `Intent.ACTION_SEND` | `UIActivityViewController` | want/`startAbility` | 剪贴板兜底 |
| `CredentialStore` | Keystore AES-GCM（现用） | Keychain | HUKS | `~/.sekb/` 0600 |
| `VectorStore` | SQLite（现用） | SQLDelight / 原生 sqlite3 | `@ohos.data.relationalStore` | JDBC SQLite |
| `EmbeddingProvider` | ONNX Runtime Android 1.20.0（现用） | ONNX Runtime iOS（C/ObjC） | ONNX Runtime 自建（NDK） | ONNX Runtime Java |
| `HttpTransport` | OkHttp（现用） | NSURLSession | `@ohos.net.http` | OkHttp / `java.net.http` |

> **第三方可选**：文件选择可用 [FileKit](https://github.com/vinceglb/FileKit)（MIT、KMP/CMP 文件选择，1540★，2026-09 仍在维护 ✅ 已核验）；
> 但它**不覆盖鸿蒙**，且引入第三方会削弱"L4 端口自己可控"的边界——建议**先自己写 30 行端口**，不够用再评估。

### 2.5 Android 侧切 CMP 的构建改动

| 项 | 现状（本仓库实测） | 目标 | 依据 |
|---|---|---|---|
| 模块结构 | `:app`（`com.android.application` + Compose 编译器） | `:app`（纯 Android 应用）+ `:shared`（KMP 库） | AGP 9 不允许 application + KMP 同模块（E-5） |
| KMP 库插件 | 无 | `com.android.kotlin.multiplatform.library`（**不是** `com.android.library`），`kotlin { android { … } }` 取代 `androidTarget {}` | 同上 |
| `org.jetbrains.kotlin.android` | 根 `build.gradle.kts` 里 `apply false`，`app` 未 apply ✅ 已符合 | 保持不 apply（AGP 9 应用插件内置 Kotlin，重复 apply 会冲突） | E-5 |
| Kotlin 版本 | **2.2.10** | **≥2.3.10**（CMP 1.11+ 对 native/web target 的要求）；若只升到 CMP 1.10 则 ≥2.2.20 | E-6/E-7 |
| Compose 依赖 | `androidx.compose:compose-bom:2024.09.00` | `org.jetbrains.compose` **1.12.0** + **直接坐标**（1.10 起 `compose.ui` 等别名已弃用）；Android 产物仍是 androidx 原版 | E-4/E-8 |
| AGP / Gradle | AGP 9.0.0 / Gradle 9.2.1 | **不变**（CMP 1.9.3+ 明确支持 AGP 9.0.0） | E-6 |
| compileSdk | 36 + minor 1（`-PsekbCompileSdkMinor=0` 可覆盖） | `:app` 不变；`:shared` 自己声明 `compileSdk`/`minSdk`（namespace 必须不同，否则 R 类冲突） | 本仓库 `apps/android/app/build.gradle.kts` 注释 + E-5 |
| 资源 | Android `res/` | 共享资源走 Compose Resources；`androidResources { enable = true }` 按需开 | E-4 |
| 回归门槛 | 182 JVM 用例 + 模拟器自检 26/26 + 检索评测（Hit@1 87%/Hit@3 100%/MRR 0.928） | **全部不得回归**；`bash scripts/android.sh test`、`assemble`、`install` 与 `doc_guard` 6/6 都必须绿 | `apps/android/docs/VERIFICATION.md`、`apps/android/docs/RETRIEVAL-EVAL.md` |

**风险与隔离**（照 RFC §5.2 第 5 步执行）：Kotlin 2.2.10 → 2.3.10 + Compose 体系切换会动到**已经验证过的构建**。
处置：单独提交 + 打 tag 做回滚点；切完**复跑 Android 全量验证**（单测 + 模拟器自检 + 检索评测）。
构建入口继续走 `bash scripts/android.sh`（构建状态全在仓库内），不要在受限沙箱里手工 export `GRADLE_USER_HOME`。

---

## 3. 收益与成本（一人维护）

| 维度 | CMP 一套 UI | 各端原生 UI |
|---|---|---|
| UI 代码量 | 1 份（现状 265 行聊天页 → 四端复用，估 400–600 行含主题/自适应） | Android 已有；iOS SwiftUI 估 600–800 行；鸿蒙 ArkTS 估 600–800 行；桌面可继续用 Compose（≈共享） |
| 每端新增工作量 | 端口实现 150–400 行 + 入口/打包 100–200 行 | 每端整套 UI + 状态绑定 |
| 新增功能成本 | 改 1 处 | 改 3–4 处（最容易出现"四端行为分叉"） |
| 版本耦合 | **高**：Kotlin/CMP 升级是全局事件；鸿蒙 fork 落后 3 个小版本 → 实际是两套版本线 | 低：各端工具链独立演进 |
| 原生观感 | 中（平台细节靠自己补） | 高（系统默认就是对的） |
| 调试体验 | 桌面优于原生；iOS 明显弱于 SwiftUI（无热重载，改一行重编框架） | 各端最好 |
| 回退成本 | 低：只换 `shared/ui`（L2/L3/L4 不动） | 不适用 |

---

## 4. 诚实结论：什么时候值得，什么时候不值得

### 4.1 值得做 CMP 的条件（同时满足 3 条以上）

1. **目标端 ≥3**（iOS + Android + 鸿蒙，或再加桌面 GUI）；
2. **人力 ≤2 人**且没有专职 iOS/鸿蒙工程师——"UI 写 4 遍"是致命成本；
3. **UI 不是产品差异点**：以表单/列表/聊天为主，没有自绘图表、复杂动效、系统控件深度集成；
4. 团队**已接受"平台细节自己补"**：中文 IME、分享、通知、无障碍都愿意逐端验证并写端口；
5. 能接受**版本耦合**：Kotlin/CMP 升级需要专门排期，鸿蒙端要接受落后上游版本。

### 4.2 不值得（或应选原生 UI）的条件（命中 1 条就要重新想）

1. **owner 认为"原生观感/系统集成就是产品差异点"**——这是价值判断，不是技术问题，技术数据无法推翻它；
2. **目标端只有 2 个**（如 iOS + Android）：省下的 UI 工作量 < 端口/适配/版本耦合成本；
3. **依赖系统深度能力**：Widget/灵动岛、Share Extension、Live Activity、手表/车机、系统搜索集成等——这些在 CMP 里都要回到原生，等于**双份维护**；
4. **iOS 是主战场**：那 SwiftUI 的调试与观感优势更值钱，共享逻辑（KMP）已经能拿到 70% 的复用；
5. **中文输入体验是核心**（我们的聊天框就是）：**必须先跑 V-1**，不通过就不做 CMP UI。

### 4.3 我的建议：折中方案（KMP 共享逻辑 + 各端原生 UI）

这与 owner 的倾向一致，且能拿到跨端收益的大部分：

| 层 | 方案 | 复用率 |
|---|---|---|
| L2 契约 | JSON 夹具 + 各端 runner（含"协议 N 方一致"守卫） | 100%（语言无关） |
| L3 逻辑 | KMP `shared/core`：**与 UI 方案无关，无论选什么都该做** | ~95% |
| L4 端口 | 接口在 common、实现在各端（本文档 §2.4 的签名**原样可用**） | 0%，但每端仅几十~几百行 |
| L5 UI | **各端原生**：Android Compose / iOS SwiftUI / 鸿蒙 ArkUI | 0%（这是代价，也是"原生体验"的来源） |

代价对比：iOS 侧**多写一整套 SwiftUI（约 6–8 天）**，但两端都要写的端口/契约/共享逻辑**不重复计入**；
换来的是**没有 interop、没有版本耦合、iOS 手感与调试最好**。鸿蒙同理（ArkTS 重写 + 契约测试，RFC §4 D2 方案 B）。
（本条即"原生 UI 路线"，与本文是同一批评估里的两个分支，结论互不依赖。）

### 4.4 若走 CMP：回退条件（写死，避免骑虎难下）

- **V-1（中文 IME）不通过且 2 周内无解** → iOS 只回退 **UI 层**为 SwiftUI，`core`/`platform` 一行不动；
- **V-8（包体积）超标且 `smallBinary` 也压不下来** → 重新评估是否只在 Android+桌面用 CMP；
- **鸿蒙 spike 四条任一不通** → 鸿蒙退回 ArkTS 重写（RFC 已定），不影响 iOS/桌面。

---

## 5. 若 owner 最终选原生：这份文档里哪些设计**仍然有用**

| 设计 | 为什么仍然有用 | 落地物 |
|---|---|---|
| **§2.4 的端口接口签名** | 原生 UI 一样要文件选择/分享/凭证/向量库/嵌入——接口在 common、实现在各端，**选原生只是换掉 L5** | `shared/platform/` 的接口 + 各端实现（iOS 侧用 Kotlin 暴露给 Swift，或直接 Swift 协议 + 桥接） |
| **`ChatStateHolder`（从 330 行 ViewModel 拆出的纯状态机）** | 端云路由/升级/handoff 的**决策逻辑只有一份**；SwiftUI/ArkUI 只做"状态 → 视图"绑定 | `shared/core/` + 各端轻绑定（可观测包装） |
| **契约夹具 + N 方一致守卫** | 四端最容易烂的不是编译，而是**行为分叉**（谁走端侧、阈值、空间戳） | 扩展现有 `scripts/check_protocol_paths.py`（现覆盖文档↔服务端↔端侧客户端三方） |
| **纯函数化的文档导入判定** | `decide`/`looksBinary`/`isPdf`/四类 PDF 失败原因已经是纯函数 + 已测，任何 UI 都能复用 | `shared/core/`（或各自端口里直接引同一份 Kotlin） |
| **不变量（同空间才可融合、阈值随模型标定、DEVICE_ONLY 永不出端）** | 与 UI 无关，且是端侧方案唯一的硬约束 | 现有自检 + 契约测试，扩到各端 |
| **同一套评测集产出同一张表** | 换了 UI 方案，检索质量与端云路由指标仍要可比 | `RetrievalEvalSet` 上移共享层；三端跑同一份标注集 |
| **§1.3 的验证清单** | 即使全原生，iOS 也要量启动/包体积/内存/手势——这张表直接变成原生端的验收单（把 CMP 特有的 V-1/V-10 换成对应原生项） | 本文 §1.3 |
| **§2.5 的构建隔离原则** | AGP 9 的"纯应用模块 + KMP 库"结构、版本切换单独提交 + tag、切完复跑全量验证——**KMP 共享逻辑也要这么做** | `apps/android/app/build.gradle.kts` 的改动 + 回滚 tag |

**会被丢掉的**（选原生时的真实损失，如实列出）：`shared/ui` 的全部 CMP 代码、CMP Gradle 插件与版本线、CMP 导航/IME 适配层、以及"一次改到处生效"的便利。

---

## 6. 外部事实来源与核验状态

| # | 事实 | 来源 | 状态 |
|---|---|---|---|
| E-1 | CMP 最新稳定版 **1.12.0**（2026-08-25）；依赖矩阵（Runtime/UI 1.12.0、Material3 1.12.0-alpha03 等） | [What's new in CMP 1.12.0](https://kotlinlang.org/docs/multiplatform/whats-new-compose-112.html) | ✅ 已核验 |
| E-2 | 1.12.0：iOS lazy 布局滚动性能优化；桌面 **MCP server**（AI agent 可重载/截屏/读语义树）；桌面 Window/Dialog API v2（实验） | 同上 | ✅ 已核验 |
| E-3 | Kotlin/Native 官方支持 target **不含 OHOS/鸿蒙**；`iosX64` 为 Tier 3、`macosX64` 已弃用；iOS 默认最低 15.0 | [Kotlin/Native targets](https://kotlinlang.org/docs/native-target-support.html) | ✅ 已核验 |
| E-4 | **CMP 在 Android 侧映射回原版 androidx 产物**（"Android distribution uses the Android artifact"）；`org.jetbrains.compose.runtime` 现为别名 | [How multiplatform Jetpack libraries are packaged](https://kotlinlang.org/docs/multiplatform/compose-multiplatform-jetpack-libraries.html) | ✅ 已核验 |
| E-5 | AGP 9 不允许 `com.android.application` + `kotlin.multiplatform` 同模块；KMP 库用 `com.android.kotlin.multiplatform.library`；AGP 9 应用插件自带 Kotlin，勿再 apply `kotlin.android` | [JetBrains AGP 9 迁移](https://github.com/JetBrains/skills/blob/main/kotlin-tooling-agp9-migration/references/MIGRATION-APP-SPLIT.md) | ✅ 已核验 |
| E-6 | CMP **1.9.3 起支持 AGP 9.0.0**（需 1.9.3 或 1.10.0+）；建议使用独立的 Android 应用模块 | [What's new in CMP 1.9.3](https://kotlinlang.org/docs/multiplatform/whats-new-compose-190.html)（**注**：该页标注日期 2026-07-31 晚于 1.10.3 页的 2026-03-19，疑为文档站笔误，以版本号与依赖矩阵为准） | ✅ 已核验 |
| E-7 | 1.11.0：**iOS 原生文本输入**（`usingNativeTextInput`，实验，基于 `UITextInput`/`UIKeyInput`，含原生选择与系统菜单）；并发渲染默认开启；移除 `iosX64`/`macosX64`；最低 iOS 14.0；原生/web target 要求 **Kotlin ≥2.3.10** | [What's new in CMP 1.11.1](https://kotlinlang.org/docs/multiplatform/whats-new-compose-111.html) | ✅ 已核验 |
| E-8 | 1.10.3：`PredictiveBackHandler` 弃用 → `NavigationBackHandler`；iOS `EndEdgePanGestureBehavior`（端边缘 pan 默认禁用，起始边缘绑返回）；`PlatformImeOptions` 支持 `inputView`/`inputAccessoryView`；`UIKitInteropProperties(placedAsOverlay)`；**依赖别名（`compose.ui` 等）自 1.10.0-beta01 起弃用** | [What's new in CMP 1.10.3](https://kotlinlang.org/docs/multiplatform/whats-new-compose-110.html) | ✅ 已核验 |
| E-9 | 鸿蒙社区方案 **CPF-KMP-CMP**：KMP `2.2.21-1.0.0` + CMP `1.9.2-1.0.0` + skiko `0.9.22.2-1.0.0`；`OHRenderer`/ArkUI 渲染；`@ArkEntry` 与 Compose 双向互操作；要求 DevEco Studio 6.0.0 / HarmonyOS SDK API17 | [CPF-KMP-CMP 组织（AtomGit）](https://atomgit.com/CPF-KMP-CMP)、[第三方介绍（2026-09-05）](https://jishuzhan.net/article/2096032868351528961)、[华为开发者话题](https://developer.huawei.com/consumer/cn/forum/topic/0208225020573231164) | 📄 第三方来源（**须以 spike 实测为准**） |
| E-10 | 平台差异官方清单：键盘/insets 在 iOS 上"位置可能略有不同"；桌面**不支持多点触控**、滚动仅滚轮；iOS/Android **滚动手感与平台对齐**；iOS 无原生返回手势、由 CMP 提供；字体/文本不做像素级一致；**热重载仅 JVM/桌面** | [Default UI behavior on different platforms](https://kotlinlang.org/docs/multiplatform/compose-platform-specifics.html) | ✅ 已核验 |
| E-11 | iOS 无障碍：语义映射到原生无障碍对象（VoiceOver 可用）；高对比度**需手动**（`UIAccessibilityDarkerSystemColorsEnabled`）；无障碍树按需懒同步（可 `Always`，掉性能）；XCTest `performAccessibilityAudit()`；`testTag` → `accessibilityIdentifier` | [iOS 无障碍支持](https://kotlinlang.org/docs/multiplatform/compose-ios-accessibility.html) | ✅ 已核验 |
| E-12 | iOS interop 触摸：当前只有一种策略——触摸先归原生；**ScrollView 启发式：150ms 内若 Compose 消费了事件则不交给原生**；1.12 起可用 `interactionMode = Cooperative/NonCooperative`、`isInteractive=false`、`isNativeAccessibilityEnabled` | [Handling touch events with interop on iOS](https://kotlinlang.org/docs/multiplatform/compose-ios-touch.html) | ✅ 已核验 |
| E-13 | Kotlin/Native 减重与内存开关：`smallBinary`（减包体，2.2.20 起实验）、`latin1Strings`（减包体/内存）、`mmapTag`（Apple 平台内存追踪）、`appStateTracking`（后台 GC）、`enableSafepointSignposts`（Xcode Instruments 看 GC 暂停）、`sourceInfoType`（堆栈带行号）；GC 默认 `cms`（2.4.0 起） | [Kotlin/Native binary options](https://kotlinlang.org/docs/native-binary-options.html) | ✅ 已核验 |
| E-14 | 桌面：Compose Hot Reload 自 1.10 随插件打包（最低 Kotlin 2.1.20）；1.12 起含 MCP server（实验） | [1.10.3](https://kotlinlang.org/docs/multiplatform/whats-new-compose-110.html)、[1.12.0](https://kotlinlang.org/docs/multiplatform/whats-new-compose-112.html) | ✅ 已核验 |
| E-15 | 文件选择第三方库 FileKit：MIT、1540★、KMP/CMP（Android/iOS/JVM/JS/WASM 等），**支持列表不含鸿蒙** | [vinceglb/FileKit](https://github.com/vinceglb/FileKit) | ✅ 已核验（GitHub API 元数据） |
| E-16 | CMP issue #5259（iOS 文本框选择/粘贴菜单缺失）**已关闭**（2025-03-12，completed） | [issue #5259](https://github.com/JetBrains/compose-multiplatform/issues/5259) | ✅ 已核验 |

> **核验纪律**：本表的 ✅ 只代表"官方文档这么写"，**不代表本机实测通过**；`apps/harmony/README.md` 里
> 「ArkTS 不能复用 Kotlin」那张表**需要按 E-9 更新**（鸿蒙已从"不能"变成"社区 Beta 能，但落后上游"）。
> 另：本次调研尝试用 GitHub Search API 统计"iOS 相关 open issue 数量"**失败**（返回 0，不可作依据），
> 因此本文不引用"未解决 issue 数"这类数字——要统计就上 [YouTrack CMP 项目](https://youtrack.jetbrains.com/issues/CMP)。

---

## 7. 需要 owner 决策 / 我需要确认的问题

| # | 问题 | 我的建议 |
|---|---|---|
| Q1 | **是否先花 3–5 天做"体验对照实验"**（只做 V-1 中文 IME + V-2 滚动 + V-8 包体积三项，空工程即可），用数据而不是文档来回答"影响不影响原生体验"？ | ✅ 建议做：这三项决定 80% 的结论，成本很低（不需要动现有代码） |
| Q2 | 若最终原生：**是否接受"UI 各端各写，但共享逻辑用 KMP"**（§4.3）？ | ✅ 建议接受：iOS 多花 6–8 天，换掉 interop 与版本耦合 |
| Q3 | 鸿蒙是否仍按 RFC 先做 **3 天 spike**（E-9 的 fork 是唯一路径）？ | ✅ 保持不变；spike 失败即退 ArkTS |
| Q4 | 共享层里 `Dispatchers.IO` 在 Kotlin/Native 上是否可用（影响 §2.2 的 `DispatcherProvider` 设计） | ⚠️ 待验证：在 `iosSimulatorArm64` target 写一行编译 + 运行；不可用则统一用 `Dispatchers.Default` 包 IO 端口 |
| Q5 | 桌面 GUI 是否仍要做（RFC Q3）？ | 建议：先只做 headless 边缘宿主（2–3 天），GUI 等 iOS 端成形后再评估 |

**下一步（等你点头）**：做 Q1 的对照实验 → 拿到三项数字 → 再回来定"CMP 还是原生"。在此之前**不写任何端侧代码**。
