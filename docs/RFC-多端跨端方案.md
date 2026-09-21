---
title: 多端跨端方案（iOS / 鸿蒙 / Windows / Linux）
layer: 设计层
owner: SEKB Team
status: draft（待 owner 确认）
version: v0.1.0
last-updated: 2026-09-21
based-on-commit: 1e32fea
related: [docs/RFC-端云协同与端侧Agent, docs/RFC-端云协同-实施记录, apps/README, apps/ios/README, apps/harmony/README]
---

# 多端跨端方案（iOS / 鸿蒙 / Windows / Linux）· v0.1（待确认）

> **本文回答什么问题**：端侧从「Android 一个端」扩到「iOS + 鸿蒙 + 桌面」时，
> **哪些代码写一遍、哪些必须各写一遍、先做哪个、Windows/Linux 到底要不要做**。
> **状态**：**待 owner 确认后开工**（确认项见 §9）。本文不含任何已写代码。
> **owner 反馈（2026-09-21）**：① UI **倾向各端原生开发**，要求先看 CMP 的设计与接口再做最终决定
> → 见 [`多端跨端-CMP方案评估.md`](多端跨端-CMP方案评估.md)；② 要一版 KMP 方案供确认
> → 见 [`多端跨端-KMP方案.md`](多端跨端-KMP方案.md)（**推荐主线：KMP 共享逻辑 + 各端原生 UI**）；
> ③ 桌面要一份审计文档 → 见 [`多端跨端-桌面端方案审计.md`](多端跨端-桌面端方案审计.md)（该审计**修正了本文 §4 D3 的两处估算**：
> 拓扑 A≠B、宿主人日 2–3 → 4.5–6.5，并发现 §8 R9 的 `DEVICE_ONLY` 契约漏洞）；
> ④ **同意** Kotlin 升到 ≥2.2.21（按 §5.2 第 5 步隔离提交 + 回滚 tag）。
> **前置阅读**：[`RFC-端云协同与端侧Agent.md`](RFC-端云协同与端侧Agent.md)（路由/升级/隐私边界）、
> [`RFC-端云协同-实施记录.md`](RFC-端云协同-实施记录.md)（M0–M3 实测数字与三条不变量）。

---

## 0. 结论先行（TL;DR，一屏）

| 端 | 建议 | 复用率（预估） | 顺序 | 本机能否验证 |
|---|---|---|---|---|
| **iOS** | **KMP 共享逻辑 + CMP 共享 UI**（不再按旧计划写 SwiftUI 第二套） | 逻辑 ~70%、UI ~60% | **第 1 个做** | ✅ Xcode 26.6 + iOS 模拟器（arm64 原生，性能仍不代表真机） |
| **鸿蒙** | 走 **KMP/CMP 鸿蒙版**（CPF-KMP-CMP，Beta）；**先 3 天 spike，通过才排期** | 逻辑 ~70%、UI ~55% | 第 2 个做（spike 可并行） | ⚠️ 本机有 DevEco SDK API 22 + OHOS NDK，但模拟器可行性**待验** |
| **桌面（Win/Linux/macOS）** | **分两步**：① headless「边缘宿主」**必做**（2–3 天）；② CMP 桌面 GUI **建议做**（增量 ~5–7 天） | 逻辑 ~85%、UI ~70% | 第 3 个做 | ✅ 本机 macOS 直接跑 JVM 目标 |
| **Linux 服务器版** | **不做**（已有云端；重复实现无收益） | — | — | — |
| **车机** | 不单独排期；鸿蒙（OHOS 系）打通后顺势评估 | 复用鸿蒙成果 | 后续 | — |

**三句话结论**：

1. **逻辑只写一遍**（KMP 共享），**UI 也只写一遍**（CMP 共享）——本仓库 Android 侧已经实测：main 5,406 行里
   **3,786 行（70%）不 import 任何 `android.*`**，抽共享层是**搬家**不是重写；
2. **鸿蒙从「不能复用 Kotlin」变成了「能」**：2026-06 HDC 华为发布 KMP/CMP 鸿蒙社区版 Beta
   （基于 KMP 2.2.21 + CMP 1.9.2），`apps/harmony/README.md` 里那张「三条路线」表**需要按此更新**——
   但它是 **Beta + 社区分叉**，所以先 spike 再决定；
3. **Windows/Linux 值得做，但不要做成"第五个原生客户端"**：桌面最大的价值是**算力宿主**（跑大模型给手机用）
   + **文件主场**（文档大多在 PC 上），这两件事用「headless 宿主 + 复用 Web/共享 UI」就能拿到，
   成本比再写一套原生客户端低一个数量级。

---

## 1. 现状盘点：要搬多少东西（本仓库实测，2026-09-20）

### 1.1 代码分层现状（`apps/android/app/src/main/kotlin/com/sekb/ondevice/`）

| 分类 | 文件数 | 行数 | 说明 |
|---|---|---|---|
| **不 import `android.*`（可移植）** | 28 | **3,786** | `route/` `chat/` `net/SseParser` `net/SekbApi` `tools/{ToolRegistry,ToolCallJson,KbSearchTool}` `rag/{Chunking,VectorMath,VectorStore,Retriever,KnowledgeIndex,DocumentRegistry}` `embed/{BertWordPieceTokenizer,EmbeddingProvider,OnnxBgeEmbedding}` `eval/` `edge/EdgeLlmClient` `model/` `core/JsonX` |
| **平台相关** | 9 | 1,620 | `MainActivity` `ui/{ChatScreen,ChatViewModel,DocumentImporter,PdfExtractor}` `device/DeviceCredentialStore` `rag/SqliteVectorStore` `tools/AndroidDeviceTools` `SekbApp`（容器装配） |
| 测试 | — | 2,274（**182 个用例**） | 大部分跟着可移植逻辑走，抽层后可**三端共用** |

### 1.2 但"不 import android" ≠ "直接能编译到 iOS/鸿蒙"

可移植文件里还埋着 **4 类 JVM 依赖**，抽 KMP 时必须一起处理（这是工作量的大头，不是搬家）：

| 依赖 | 用在哪些文件 | 影响 | 处理方式 |
|---|---|---|---|
| `org.json`（Android 自带） | 12 个文件直用；`core/JsonX` 只是薄门面 | iOS/鸿蒙没有 `org.json` | **收敛到 `JsonX` 一个门面**，底层换 `kotlinx-serialization-json`（`JsonElement`）；调用点多数不用改 |
| `okhttp3` | `net/HttpTransport.kt`（实现类与接口同文件） | OkHttp 无 iOS/鸿蒙产物 | **拆成 `HttpTransport`（接口，common）+ 各端实现**：Android=OkHttp、iOS=NSURLSession、鸿蒙=ArkTS `@ohos.net.http` 或 libcurl |
| `java.io.File` / `java.util.UUID` / `java.util.concurrent` | `SelfTest` `BertWordPieceTokenizer` `PlaneRouter` 等 | Kotlin/Native 无这些 | `okio`（多平台文件）/ `kotlin.uuid.Uuid` / `kotlinx.atomicfu` |
| `ai.onnxruntime.*`（JVM API） | `embed/OnnxBgeEmbedding.kt` | iOS/鸿蒙无 JVM API | 保留 `EmbeddingProvider` 接口，**按端给实现**：Android/桌面同 API 换依赖；iOS 走 ONNX Runtime C/ObjC；鸿蒙走 NAPI→`libonnxruntime.so` |

> **一句话**：真正的跨端成本不在"搬 3,786 行"，而在**把上面 4 类依赖变成"端口（port）"**。
> 这一步做完，后面每多一个端都是"填适配 + 打包"，不是重写。

### 1.3 构建与工具链现状（本机实测）

| 端 | 现状 | 备注 |
|---|---|---|
| Android | Gradle 9.2.1 + AGP 9.0.0 + Kotlin 2.2.10 + Compose BOM 2024.09.00；182 单测 / 模拟器自检 26/26 | 构建状态全在仓库内 `.tooling/` |
| iOS | Xcode **26.6**、iOS 模拟器运行时 26.3/26.4；`DEVELOPER_DIR` 需显式指向 Xcode | 尚无代码；模拟器 arm64 原生 |
| 鸿蒙 | DevEco Studio SDK **API 22**（6.0.2.130）+ **OHOS NDK**（`aarch64-unknown-linux-ohos-clang`、sysroot `aarch64-linux-ohos`） | **能本地交叉编译 C/C++** → ONNX Runtime 自建有戏；模拟器可行性待验 |

---

## 2. 目标与非目标

### 2.1 目标

1. **一套逻辑**：路由决策 / 6 类升级信号 / 前缀守卫 / 工具调用校验 / 分块与检索 / SSE 解析 / 编排器，
   在所有端**行为等价**（等价性由同一套契约夹具证明，不靠"我抄得仔细"）。
2. **一套 UI**：聊天、来源面板、文档管理、设置，一套 Compose 代码覆盖 Android / iOS / 鸿蒙 / 桌面；
   平台特性（Keychain、权限、文件选择器、分享）保留平台实现。
3. **一致的数字**：同一套评测集在三端跑出**同一张表**（Hit@1/Hit@3/MRR、阈值标定、端侧完成率、升级率）——
   差异可见、可解释。
4. **按需编译**：`apps/` 下每个端独立可编译；不装某端工具链的人也能 clone 全量代码。

### 2.2 非目标（明确不做，避免摊子铺太大）

- ❌ 不做第二个服务端（Linux 服务器版）：云端已有 REST/SSE，端侧只做客户端 + 本地推理；
- ❌ 不重写 Web 前端：桌面"浏览器入口"继续用现有 React 前端；
- ❌ 不做车机独立排期：OHOS 系打通后评估；
- ❌ 不在本期做 NPU 加速（HTP/Hexagon/CoreML-NPU/HiAI NPU）：先用 CPU/GPU 路径把功能与基线跑对；
- ❌ 不做跨端状态同步的新协议：聊天是追加式，云端权威 + 端侧窗口（M1 已定），多端不引入 CRDT。

---

## 3. 架构：五层 + 一条硬边界

```
┌─────────────────────────────────────────────────────────────┐
│ L5 UI 层        CMP 共享 UI（聊天/来源/文档/设置）             │  ← 写一遍（四端）
├─────────────────────────────────────────────────────────────┤
│ L4 平台端口层   凭证 · 文件选择 · PDF 抽取 · SQLite · HTTP     │  ← 每端写一遍（薄）
│                · 嵌入运行时 · LLM 运行时 · 设备工具 · 权限     │
├─────────────────────────────────────────────────────────────┤
│ L3 可移植纯逻辑 路由/升级信号/前缀守卫/工具校验/编排/分块/检索  │  ← 写一遍（KMP main）
├─────────────────────────────────────────────────────────────┤
│ L2 契约层      协议文档 + JSON 夹具 + 契约测试 runner          │  ← 写一遍，各端都跑
├─────────────────────────────────────────────────────────────┤
│ L1 云端         SEKB REST/SSE（已存在，不改）                 │
└─────────────────────────────────────────────────────────────┘
```

**那条硬边界**：`DEVICE_ONLY` 数据永不离开设备（M1 已实现 `escalation_allowed` 闸门）。
跨端后这条边界**必须是共享逻辑**（不是各端自己实现），否则"四端行为一致"就是空话——
所以它是 L3 的第一公民，也是契约测试里的必测项。

| 层 | 内容 | 归属 | 复用率 |
|---|---|---|---|
| L2 契约 | 协议路径/字段、错误码、夹具、评测集 | `apps/contract/` + `docs/ops/16-端云协同协议.md` | 100%（语言无关） |
| L3 逻辑 | 见 §1.1 的 28 个文件 | `apps/shared/core/`（KMP commonMain） | ~95% |
| L4 端口 | 见 §1.2 的 4 类依赖 + 设备能力 | `apps/shared/platform/` + 各端 `sourceSets` | 0%（但每端只几十~几百行） |
| L5 UI | 页面与组件 | `apps/shared/ui/`（CMP commonMain） | ~60–70%（其余是平台特性入口） |

---

## 4. 四个关键决策

### D1 · UI 方案：CMP 一套（推荐） vs 各端原生

| 方案 | 优点 | 代价 | 结论 |
|---|---|---|---|
| **CMP 一套（推荐）** | 一套 UI 覆盖 Android/iOS/鸿蒙/桌面；本机四端都能编；"一次改到处生效" | iOS 上部分系统级交互要靠 interop；CMP 鸿蒙版是 Beta；需把 Kotlin/Compose 升到与 CPF 对齐 | ✅ **推荐** |
| 各端原生 UI（SwiftUI / ArkUI / Compose） | 各端体验最正 | UI 写 4 遍；每加一个功能改 4 处（本项目 UI 有 700+ 行、还会长） | ❌ 不推荐（一人维护不动） |
| Web UI 一把梭（壳里装 WebView） | 最省事 | 端侧价值（低延迟、离线、隐私）体现不出来；也失去了"端侧 Agent"的叙事 | ❌ 不推荐 |

**为什么现在敢选 CMP**（外部事实，见 §10 待本地复核）：CMP 1.9.3（2026-07）已是稳定版，
iOS 有专门的 `preferredFrameRate`、`PlatformImeOptions` 等原生能力；
且 CMP 1.9.3 明确支持 **AGP 9.0.0**——正好是我们 Android 侧现在的 AGP。
参考 [What's new in Compose Multiplatform 1.9.3](https://kotlinlang.org/docs/multiplatform/whats-new-compose-190.html)。

**回退条件（写死，避免骑虎难下）**：iOS 上若出现"流式文本渲染/中文输入法/长列表"三类致命问题，
且 2 周内无解 → **iOS 只回退 UI 层**（改 SwiftUI 壳），L2/L3/L4 一行不动。
这就是把边界画在 UI 之下的意义。

### D2 · 鸿蒙路线：KMP/CMP 鸿蒙版（推荐，先 spike）

`apps/harmony/README.md` 现在写的是「ArkTS 不能复用 Kotlin → 三条路线」。**这个前提需要更新**：
2026-06 华为在 HDC 2026 发布 **KMP&CMP 鸿蒙社区首版本 Beta**（`CPF-KMP-CMP`，基于 KMP 2.2.21 + CMP 1.9.2），
做法是**给 Kotlin/Native 增加 `OHOS_ARM64` / `OHOS_X64` target**（`Family.OHOS`），
用毕昇 LLVM 19 出 ELF（`libkn.so` / `libkn.a` / `.kexe`），再通过 NAPI 与 ArkTS/ArkUI 协作；
CMP 侧有两条渲染路径（Skia 自渲染 / ArkUI RenderNode 统一渲染）。
参考 [KMP&CMP 社区首版本开启 Beta](https://www.eepw.com.cn/zhuanlan/202606/395999.html)、
[实现细节分析](https://ai6s.net/6a33418e10ee7a33f27f2ce2.html)，以及
[支付宝 MYKMP 开源（同一套 Kotlin/Compose 跑 Android/iOS/HarmonyOS）](https://mp.weixin.qq.com/s/5IznaN4xbBhMaPalN3v6mg)。

| 方案 | 逻辑复用 | UI 复用 | 风险 | 结论 |
|---|---|---|---|---|
| **A. KMP/CMP 鸿蒙版** | ~70% | ~55% | 社区 Beta、多处 fork（JetBrains / 腾讯 Kuikly+ovCompose / CPF-KMP-CMP）、**依赖产物供给**（`ohos_arm64` 的 kotlinx 库谁发布） | ✅ **先 spike 后决定** |
| B. ArkTS 重写纯逻辑 + 契约测试 | 0%（重写） | 0% | 逻辑必然漂移，靠契约测试兜 | 备选（spike 失败时退这条） |
| C. C/C++ 核心 + 三端 FFI | 高（C 一份） | 0% | 一次投入最大；等于把 L3 再实现一遍成 C | ❌ 本期不做（除非将来上车机/多端数量继续涨） |
| D. 瘦客户端（鸿蒙不做本地推理） | ~70%（若走 KMP） | ~55% | 违背"端侧优先"初衷 | ❌ 不做 |

**spike 的验收标准（3 天，必须能证伪）**：
1. 一个 KMP 模块能编出 `ohos_arm64` 产物，并被一个 HAP 通过 NAPI 调用（打日志即算成功）；
2. `kotlinx-coroutines` + `kotlinx-serialization` 在 `ohos_arm64` 上**能解析到产物并跑起来**；
3. 用本机 OHOS NDK 交叉编译 **ONNX Runtime**，在设备/模拟器上跑通 `bge-small-zh` 出向量（与 Android 端同一段文本、余弦一致）；
4. 三条中任一条 3 天内不通 → **退回方案 B**，并把结论写进 `apps/harmony/README.md`。

### D3 · 桌面（Windows/Linux/macOS）：做，但分两步

**先回答 owner 的问题：有没有必要做？——有，但理由不是"多一个端"，而是这两件事：**

| 桌面独有价值 | 为什么手机上做不了/做不好 |
|---|---|
| **算力宿主**：7B–32B 模型跑在 PC 上，给同局域网/尾网的手机端当"边缘云" | 手机内存/带宽/发热撑不住大模型；M0 已在 Mac 上验证这条路（8B/4bit 61 tok/s、32B/4bit 16 tok/s） |
| **文件主场**：PDF/Word/Excel 大批量导入与索引 | Android 侧已经踩过 SAF 选择器的坑；PC 上文档本来就是主场 |
| （附带）**演示与交付**：一台笔记本当场演示端云协同 | 面试/售前场景比手机更方便 |

**分两步，不要一次到位**：

| 步骤 | 内容 | 工作量 | 建议 |
|---|---|---|---|
| ① **headless 边缘宿主（必做）** | Windows/Linux/macOS 一键启动脚本：起 Ollama（或 llama.cpp server）+ 复用 M0 的端云 profile + 路由日志；局域网/尾网暴露 OpenAI 兼容端点 | **2–3 天** | ✅ 先做——它直接放大手机端的端侧能力，且不写 UI |
| ② **CMP 桌面 GUI（建议做）** | 复用共享 UI + JVM 平台适配（JDBC SQLite、ONNX Runtime Java、本地文件、系统托盘）；打包 MSI/dmg/deb/AppImage | **5–7 天**（共享层已存在时） | ✅ 建议做——边际成本低，且"一套 Compose 跑四端"本身就是作品亮点 |

**不做**：Linux 服务器端第二套服务端实现（云端已有）；也不做 Windows 原生（WinUI/.NET）——没有复用价值。

### D4 · 端侧推理运行时矩阵（每端各选什么，写清楚免得每端重新试）

| 能力 | Android | iOS | 鸿蒙 | 桌面（Win/Linux/macOS） |
|---|---|---|---|---|
| 本地 LLM | llama.cpp（NDK，CPU）；本期先用宿主 Ollama | llama.cpp（Metal）或先用宿主 Ollama | **先用边缘宿主/云端**；本地 LLM 延后（llama.cpp 有 OHOS/musl 兼容在推进，但风险最高） | **Ollama / llama.cpp server（就是边缘宿主本体）** |
| 嵌入模型 | ONNX Runtime Android 1.20.0（已用） | ONNX Runtime iOS（C/ObjC）+ 可选 CoreML EP | ONNX Runtime 自建 OHOS 版（NDK 已具备）→ NAPI | ONNX Runtime Java（与 Android 同一套 API，代码几乎照搬） |
| 向量存储 | SQLite（已用，暴力余弦） | SQLite / SQLDelight | 关系型存储（`@ohos.data.relationalStore`）或 SQLDelight 原生驱动（待验） | SQLite（JDBC）/ 或直接用 ChromaDB（桌面是本机，也可以挂云端 L3） |
| 凭证存储 | Keystore（已用） | Keychain | 鸿蒙加密存储（HUKS） | `~/.sekb/credentials`（0600）+ 可选系统钥匙串 |
| 传输 | OkHttp（已用） | NSURLSession | `@ohos.net.http` / libcurl | OkHttp / java.net.http |
| PDF 抽取 | PdfBox-Android（已用，仅文本层） | PDFKit（原生，能力更强） | 鸿蒙 PDF 能力（待查）或暂不支持 | PdfBox（JVM，代码与 Android 几乎相同） |

> **一条纪律**：嵌入模型在**所有端必须是同一个空间戳**（`BAAI/bge-small-zh-v1.5-int8@512`），
> 否则端侧向量与云端 L3 不可比、也不可融合。这是 M3 定的第一条不变量，跨端后更容易被破坏，必须进契约测试。

---

## 5. 目录与构建布局

### 5.1 目标结构

```
apps/
├── settings.gradle.kts          # 单一 Gradle 构建：include(":shared", ":android:app", ":desktop")
├── gradlew / gradle/            # wrapper 上移到 apps/（现在在 apps/android/）
├── shared/                      # ★ 新增：KMP 共享层
│   ├── core/                    #   L3 纯逻辑（commonMain）+ 各端 actual
│   ├── platform/                #   L4 端口接口（expect）+ androidMain/iosMain/ohosMain/jvmMain
│   └── ui/                      #   L5 CMP 界面（commonMain）
├── contract/                    # ★ 新增：语言无关契约夹具（JSON）+ 各端 runner 约定
├── android/app/                 # 现有 Android（几乎不动，改依赖与容器装配）
├── ios/                         # ★ 新增：Xcode 工程 + iOS 入口 + 平台端口实现
├── harmony/                     # ★ 新增：DevEco 工程（entry + NAPI 桥 + ArkTS 平台实现）
└── desktop/                     # ★ 新增：headless 宿主（先）+ CMP 桌面入口（后）
```

### 5.2 迁移路径（每步都保持"随时可跑"）

| 步 | 动作 | 验收（必须能证伪） |
|---|---|---|
| 1 | **契约夹具先行**：把协议路径/字段/升级信号/评测集固化成 `apps/contract/*.json`；写 Android 端 runner | Android 182 测试 + 契约 runner 全绿 |
| 2 | **依赖去平台化**（不建 KMP 模块）：`org.json` 全量收敛到 `JsonX`；`HttpTransport` 拆接口/实现；`File/UUID/concurrent` 换多平台 API | Android 行为零变化（自检 26/26、评测数字不变） |
| 3 | **建 `shared/` KMP 模块，先只加 JVM+Android target**，把 28 个文件搬进去 | Android 编译通过 + 182 测试迁移后全绿（JVM 上也能跑） |
| 4 | **加 iOS target**（`iosArm64`/`iosSimulatorArm64`），修 `actual` | `./gradlew :shared:compileKotlinIosSimulatorArm64` 通过 |
| 5 | Android 切 **CMP**（androidx BOM → `org.jetbrains.compose`，Kotlin 升到 ≥2.2.21），UI 搬 `shared/ui` | Android 自检 26/26 + 评测数字不变（这一步最容易打破基线，必须复跑全部验证） |
| 6 | iOS 端成形 + 模拟器验收 | 见 §7 M5 |
| 7 | 鸿蒙 spike | 见 §4 D2 的四条 |
| 8 | 桌面 headless → 桌面 GUI | 见 §7 M6 |

> **风险提示（第 5 步）**：Kotlin 2.2.10 → 2.2.21+ 与 Compose 体系切换会动到**已经验证过的构建**。
> 处置：单独一个提交 + 一个 git tag 做回滚点；切完必须复跑 Android 全量验证（单测 + 模拟器自检 + 检索评测）。

---

## 6. 一致性机制（跨端最容易烂的地方）

跨端项目最常见的死法不是"编不过"，而是**四端行为悄悄分叉**：同样的输入，端 A 走端侧、端 B 走云端，
或阈值/空间戳不一致，导致"端侧完成率"这类指标失去意义。所以一致性要**机器可判**：

| 机制 | 做什么 | 落地物 | 现状 |
|---|---|---|---|
| **契约夹具（单一事实源）** | 协议路径/字段、升级信号枚举、错误码、隐私边界用例，全部 JSON 化 | `apps/contract/` + 各端 runner | 新增 |
| **共享逻辑（而不是共享文档）** | 路由/守卫/校验/检索只有一份实现 | `apps/shared/core` | 新增（从 Android 搬） |
| **同一套评测集** | 检索 33 问、意图分类、工具调用，各端跑同一份标注集，产出同一张表 | 现有 `RetrievalEvalSet` 上移共享层 | 已有，上移 |
| **协议 N 端守卫** | `doc_guard` 第 5 项从"文档↔服务端↔端侧客户端"扩成 **文档↔服务端↔Kotlin 共享层↔iOS 壳↔鸿蒙壳** | `scripts/check_protocol_paths.py` 扩展 | 已有 3 方，扩为 N 方 |
| **不变量断言** | ① 仅同空间 fp32 可融合 ② 阈值随模型标定 ③ DEVICE_ONLY 永不出端 | 契约测试 + 各端自检 | 已有（Android），上移共享层 |
| **活数字跨文档一致** | 端数/复用率/各端用例数进"活数字"表，禁止两处取值矛盾 | `doc_guard` 第 4 项 | 已有，扩字段 |

---

## 7. 里程碑与工作量（一人 + AI 结对，人日）

| 阶段 | 内容 | 工作量 | 交付物 | 验收数字（可证伪） |
|---|---|---|---|---|
| **M4** 共享层 | 契约夹具 + 去平台化 + `shared/` 抽取 + iOS target | **8–12 天** | `apps/shared`、`apps/contract` | Android 182 测试与自检 26/26 **不回归**；`shared` 在 JVM + `iosSimulatorArm64` 均可编译并通过共享单测 |
| **M5** iOS 端 | CMP UI + NSURLSession/Keychain/PDFKit + 启动参数自检 | **8–12 天** | `apps/ios`（模拟器可跑） | 自检项与 Android **等价**（同样 PASS 数）；检索评测 Hit@1/Hit@3/MRR **与 Android 一致**（同一语料）；端云路由事件与 Android 同构 |
| **M6** 鸿蒙 spike | D2 的四条验收 | **3 天** | `apps/harmony` 最小可跑 + 结论记录 | 四条全通 → 进入 M7；任一不通 → 退方案 B（ArkTS 重写 + 契约测试） |
| **M7** 鸿蒙端 | KMP/CMP 鸿蒙端成形（若 spike 通过） | **8–12 天** | `apps/harmony` 正式端 | 契约 runner 全绿 + 嵌入向量与 Android 同空间同余弦 |
| **M8** 桌面 | ① headless 边缘宿主 ② CMP 桌面 GUI | **2–3 天 + 5–7 天** | `apps/desktop` | ①手机端能连上桌面宿主并跑端侧推理（同一套路由日志）②桌面 GUI 复用共享 UI，功能与 iOS 等价 |
| **并行** 真机验收 | Android/iOS 真机性能 | 待硬件 | 性能报告 | decode tok/s、TTFT、内存峰值、发热（模拟器测不了，见 RFC §9.1） |

**总计**：iOS 主线 **16–24 天**；鸿蒙（含 spike）**11–15 天**；桌面 **7–10 天**。**先做 M4+M5，再决策鸿蒙**。

**排序理由**：iOS 路径是**官方支持**（JetBrains 上游），先做它能验证"共享层 + 端口边界"设计对不对；
鸿蒙是社区 Beta，放到共享层稳定之后，风险才可隔离。

---

## 8. 风险与对策

| # | 风险 | 影响 | 概率 | 对策 / 触发回退 |
|---|---|---|---|---|
| R1 | **KMP 新 target 的依赖供给**：`ohos_arm64` 上 kotlinx/okio 等库谁发布产物 | 鸿蒙编不过 | 高 | M6 spike 第 2 条专门验；不通就用方案 B（ArkTS 重写 + 契约测试） |
| R2 | **CPF-KMP-CMP 生态分叉**（JetBrains / 腾讯 Kuikly+ovCompose / CPF 三处分叉） | 锁死在某个 fork；升级困难 | 中 | 把 KMP **共享逻辑**与 CMP **UI** 的依赖面收窄（只用 coroutines/serialization/okio）；UI 保持可替换 |
| R3 | Kotlin/Compose 升级打破现有 Android 基线 | 已验证的 182 测试/26 自检失效 | 中 | 单独提交 + tag；切完复跑全量验证（§5.2 第 5 步） |
| R4 | OHOS 上 ONNX Runtime 自建失败（clang 15 基线、C++17、musl） | 鸿蒙端侧 RAG 做不了 | 中 | spike 第 3 条验；退路：鸿蒙端暂不做本地嵌入（走宿主/云端），先保路由与 UI |
| R5 | iOS 上 CMP 的输入法/长列表/流式渲染体验 | UI 不可用 | 中 | D1 的回退条件：iOS 只换 UI 层为 SwiftUI，逻辑不动 |
| R6 | 四端一致性靠人盯 → 必然漂移 | 端侧指标不可比 | **高** | §6 的机器可判机制（契约夹具 + N 方守卫 + 同评测集）**先做**，不放到最后 |
| R7 | 端数变多导致验证成本爆炸 | 每轮改动要验 4 端 | 中 | 分层验证：共享逻辑跑 JVM 单测（快）；各端只验"端口 + 端到端自检"（慢但少） |
| R8 | 无真机（Android/iOS 都缺） | 性能结论缺失 | 高（现状） | 沿用 RFC §9.1：模拟器验功能、Mac 拿性能真值、按带宽保守外推；真机到货后补 |
| R9 | **`DEVICE_ONLY` 语义被"边缘宿主"绕开**（桌面审计发现的契约漏洞） | 手机上标记"永不出本机"的数据，会走局域网到另一台机器（同样是"端侧"，但不是本设备） | **高（一旦做桌面宿主就必然发生）** | **改契约**：`DEVICE_ONLY` 显式定义为"**本设备上的本地运行时**"，排除远端宿主；宿主收到 `DEVICE_ONLY` 请求一律**拒绝 + 留痕**（不是"能跑就跑"）；宿主代手机推理时**不得落盘 prompt 正文**。详细论证与验收见 [`docs/多端跨端-桌面端方案审计.md`](多端跨端-桌面端方案审计.md) §2.6（F-1）——**这条排在桌面宿主功能之前做** |

---

## 9. 需要 owner 确认的决策清单

| # | 决策 | 我的建议 | 影响 |
|---|---|---|---|
| Q1 | **UI 是否统一到 CMP**（放弃原 iOS SwiftUI 计划） | ✅ 统一 CMP | 决定 UI 写 1 遍还是 4 遍；也决定桌面 GUI 的成本 |
| Q2 | **鸿蒙是否先做 3 天 spike**（而不是直接按 ArkTS 重写） | ✅ 先 spike | spike 结果决定鸿蒙走 A 还是 B 路线 |
| Q3 | **Windows/Linux：headless 宿主 + CMP GUI 都做，还是只做 headless** | ✅ 都做（GUI 后置到 M8） | 桌面 GUI 约 5–7 天；只做 headless 则 2–3 天 |
| Q4 | **是否接受 Kotlin 2.2.10 → ≥2.2.21 升级**（CMP/AGP9/鸿蒙对齐需要） | ✅ 接受，按 §5.2 步骤 5 隔离提交 | 影响现有 Android 构建基线（有回滚 tag） |
| Q5 | **顺序是否同意：共享层 → iOS → 鸿蒙 → 桌面** | ✅ 同意 | 决定里程碑排期 |
| Q6 | **桌面端是否也要做"端侧本地 RAG"**（还是只用云端 L3 + 本地 LLM） | 建议：复用共享 RAG（成本低） | 若做，需要 SQLite 端口 + 文件导入端口 |

---

## 10. 外部事实与本地待验证（不信"网上说"，逐条验）

| # | 外部事实（来源） | 本地验证方法 | 状态 |
|---|---|---|---|
| E1 | CMP 1.9.3 稳定、支持 AGP 9.0.0、iOS 有原生 IME/帧率能力（[官方文档](https://kotlinlang.org/docs/multiplatform/whats-new-compose-190.html)） | 建空 CMP 工程，`assembleDebug` + iOS 模拟器跑起来 | ⬜ 待验 |
| E2 | 华为 HDC 2026 发布 KMP&CMP 鸿蒙社区版 Beta（KMP 2.2.21 + CMP 1.9.2）（[EEPW 报道](https://www.eepw.com.cn/zhuanlan/202606/395999.html)、[实现分析](https://ai6s.net/6a33418e10ee7a33f27f2ce2.html)） | 拉 `CPF-KMP-CMP` 示例，用本机 DevEco SDK 编 HAP | ⬜ 待验 |
| E3 | Kotlin/Native 新增 `OHOS_ARM64`/`OHOS_X64` target，毕昇 LLVM 19 出 ELF，产物 `libkn.so/.a/.kexe` | 同上：确认 Gradle DSL 能写 `ohosArm64()` | ⬜ 待验 |
| E4 | 支付宝 MYKMP 开源：一套 Kotlin/Compose 跑 Android/iOS/HarmonyOS（[微信文章](https://mp.weixin.qq.com/s/5IznaN4xbBhMaPalN3v6mg)） | 作为"路线可行"的旁证，不作为技术依据 | 📄 旁证 |
| E5 | OHOS 上可构建 ONNX Runtime（`--ohos --ohos_arch`，社区移植文档给出 riscv64 流程） | 用本机 NDK 试 `--ohos_arch arm64` 编 `libonnxruntime.so` | ⬜ 待验（spike 第 3 条） |
| E6 | llama.cpp 在做 OpenHarmony/musl 兼容（[PR #29156](https://app.semanticdiff.com/gh/ggerganov/llama.cpp/pull/29156/overview)）；但社区反馈鸿蒙上用 llama.cpp 仍有问题 | 鸿蒙本地 LLM 排到最后；先用宿主/云端 | ⚠️ 已知有坑 |
| E7 | 本机已具备 iOS 与鸿蒙工具链 | 已实测：Xcode 26.6；DevEco SDK API 22 + OHOS NDK（`aarch64-unknown-linux-ohos-clang`，sysroot `aarch64-linux-ohos`） | ✅ 已验证 |
| E8 | **OHOS NDK 真能交叉编出 arm64 产物**（这是"鸿蒙端侧 RAG 有戏"的前提） | 已实测（2026-09-21）：用 `aarch64-unknown-linux-ohos-clang`（**clang 15.0.4 "OHOS (dev)"**）编译 + 链接一个最小 C 程序，得到 `ELF 64-bit LSB pie executable, ARM aarch64`，解释器 `/lib/ld-musl-aarch64.so.1`（musl）。⚠️ 同一条实测暴露风险：**clang 15 基线偏老**（C++20 支持不全），ONNX Runtime 新版本可能要打补丁或降版本——这正是 spike 第 3 条要撞的墙 | ✅ 编译链路已验证；⏳ ONNX 待验 |

---

## 11. 不做什么 / 已知代价（诚实清单）

- **不追求四端同时开工**：一次只推进一个端，其余保持"能编译、不回归"；
- **不承诺鸿蒙本地大模型**：鸿蒙首版可能只有"路由 + 共享 UI + 本地嵌入"，本地 LLM 依赖宿主/云端；
- **不接受"共享逻辑但各端抄一份参数"**：阈值、空间戳、升级信号枚举必须来自共享常量/契约夹具；
- **已知代价**：CMP 一套 UI 意味着放弃部分平台原生观感（iOS 的导航手势、鸿蒙的平行视界），
  这是"一人维护四端"的必然取舍；需要时用平台 interop 局部补，而不是整体重写。
