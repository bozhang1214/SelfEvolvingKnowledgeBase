---
title: 多端跨端收敛方案（Android / iOS / 鸿蒙 / Mac）
layer: 设计层
owner: SEKB Team
status: v1.0 已确认（2026-09-21 owner 通过 §9 的 6 条）
version: v1.0.1
last-updated: 2026-09-21
based-on-commit: 945f07a
related: [docs/多端跨端-KMP方案, docs/多端跨端-CMP方案评估, docs/多端跨端-桌面端方案审计, docs/多端跨端-工程议题（网络层·包体·热修复）, docs/RFC-端云协同与端侧Agent, apps/README]
---

# 多端跨端收敛方案（Android / iOS / 鸿蒙 / Mac）· v1.0（**已确认**）

> **这份文档是唯一的决策入口**：四端做什么、共用什么、按什么顺序、各自的验收数字。
> 细节与证据在五份支撑文档里：主线详设 [`多端跨端-KMP方案.md`](多端跨端-KMP方案.md)、
> 备选评估 [`多端跨端-CMP方案评估.md`](多端跨端-CMP方案评估.md)、
> 桌面存证 [`多端跨端-桌面端方案审计.md`](多端跨端-桌面端方案审计.md)、
> 工程议题（网络层/包体/热修复）[`多端跨端-工程议题（网络层·包体·热修复）.md`](多端跨端-工程议题（网络层·包体·热修复）.md)、
> 原设计 [`RFC-端云协同与端侧Agent.md`](RFC-端云协同与端侧Agent.md)（路由/升级/隐私边界）。
>
> **owner 已确认（2026-09-21）**：
> ① 范围 = **Android / iOS / 鸿蒙 / Mac**，桌面（Windows/Ubuntu）搁置（跟踪项 `docs/BACKLOG.md` D16）；
> ② UI **各端原生**（Compose / SwiftUI / ArkUI，Mac 复用 iOS 的 SwiftUI），CMP 归档为备选；
> ③ 同意 Kotlin 升到 ≥2.2.21（隔离提交 + 回滚 tag）；
> ④ 顺序 = M4 共享层 → iOS → 鸿蒙 spike → Mac；
> ⑤ 鸿蒙先 3 天 spike（四条验收，不过则退 ArkTS + 契约夹具）；
> ⑥ **R10（`DEVICE_ONLY` 语义收口）放进 M4**；⑦ 允许改 CI 加 macOS runner。
> **另有三条工程议题待定**（网络层 libcurl / 包体与插件化 / 热修复）：
> 结论与依据见 [`多端跨端-工程议题（网络层·包体·热修复）.md`](多端跨端-工程议题（网络层·包体·热修复）.md) §0 与 §5（S1–S4）。

**🎯 本期北极星（owner 2026-09-21 定）**：**四端「功能可用」**——
即 Android / iOS / 鸿蒙 / Mac 上**功能对等、能自己跑通**（清单见 §2.3）。
性能数字（真机 tok/s）、包体极致、代码插件化**都不是本期重点**。
**L1 策略/配置热修：本期正常实现**（下发通道 + 本地缓存 + 校验 + 灰度 + 回滚 + 审计 + 可变项白名单
+ `embeddingSpace` 绑定，见 [`多端跨端-工程议题（网络层·包体·热修复）.md`](多端跨端-工程议题（网络层·包体·热修复）.md) §3）；
其中**「设备适配」类策略（机型/SoC/内存档位映射、按 ROM 的差异开关）只预留字段，本期不实现**。

---

## 0. 结论先行（四端收敛表 · 一屏）

| 端 | UI | 共享逻辑 | 端侧推理 | 顺序 | 工作量 | 本机现在能验什么 |
|---|---|---|---|---|---|---|
| **Android** | Jetpack Compose（**已有，几乎不动**） | KMP `shared` | ONNX 嵌入（已有）+ 宿主 Ollama（已有，后续换 llama.cpp） | 已在（M0–M3 完成） | — | 模拟器自检 **30 PASS / 0 FAIL / 2 SKIP**、**207** 单测（功能 187 + 契约 4 + 门面 11 + 格式化 5）、检索评测 ✅ |
| **iOS** | **SwiftUI 原生** | 同上 | ONNX Runtime iOS（C/ObjC）；LLM 先用宿主 Ollama（模拟器 `127.0.0.1:11434`） | **第 1 个做（M5）** | **12–16 天** | Xcode 26.6 + iOS 模拟器（arm64）→ 功能 ✅ / 真机 ✗ |
| **鸿蒙** | **ArkUI 原生** | 同上（经 **CPF-KMP-CMP** 的 Kotlin/Native `OHOS_ARM64`） | 先用宿主/云端；ONNX 自建 OHOS 版（NDK 已验证可编） | **第 2 个做（M6 spike 3 天 → M7）** | **15–19 天** | DevEco SDK API 22 + OHOS NDK 编译链路 ✅ / 模拟器待验 |
| **Mac** | **SwiftUI 原生（复用 iOS 的 UI 代码 + `macosArm64` target）** | 同上 | **Ollama 宿主（M0 已完成）** + 本地 App | **第 3 个做（M8）** | **4–6 天** | 本机原生运行 ✅；性能真值也来自这台机器 |
| Windows / Ubuntu | 搁置 | — | — | **搁置**（跟踪项 D16） | 宿主 4.5–6.5 天（未排期） | 见审计文档 §4（本机无 Win/Ubuntu） |

**一次性前置投入**：**M4 共享层（8–12 天）**——把 3,723 行可移植逻辑抽成 KMP 模块 + 建契约夹具 + 去平台化（后两项已完成，见 §5.2）。
四端都吃这份投入；不做它，后面每端都要重写一遍逻辑（且必然漂移）。

**一句话**：**逻辑一份（KMP）、界面各端原生、推理各端就近**；
先做 iOS（官方路径最稳、能验证共享层设计），再做鸿蒙（先 spike 再承诺），Mac 最后（白捡，复用 iOS 的 SwiftUI）。

---

## 1. 共享边界的硬事实（本仓库实测，不是估计）

| 事实 | 数字 | 出处 |
|---|---|---|
| Android 端 main 代码 | 拆成两块：**`apps/shared/commonMain` 26 文件 / 3,395 行纯逻辑**（零平台 import）+ `apps/android/app` 14 文件（Compose UI/OkHttp/Keystore/SQLite/ONNX/设备工具/装配/自检） | `apps/shared`、`apps/android/app/src/main` |
| 单测可共享比例 | **≈178 / 207（86%）**（功能 158 + 契约夹具 4 + JSON 门面 11 + 格式化 5；当前总量 **207**，`scripts/android.sh test` 全绿） | `apps/android/app/src/test` |
| 端口接口**已经存在** | 5 个：`HttpTransport`/`EmbeddingProvider`/`VectorStore`/`DocumentRegistry`/`CredentialStore` | `apps/android/.../net`、`embed`、`rag`、`device` |
| 共享逻辑单次开销（JVM 实测） | `PlaneRouter.decide` **1.135 μs**、`ToolCallJson.parse` **0.923 μs**、`Chunking.split(2KB)` **9.040 μs** | 2026-09-21 微基准（临时测试，已删） |
| 相对端到端 | 一次聊天十几 μs ≈ 端到端 **119ms** 的 **0.008%** | 同上 + M1 实测 |
| 依赖去平台化 | ✅ **已完成**：`org.json` → `core/Json.kt`（门面）；`okhttp3` → `net/OkHttpTransport.kt`（拆出）；`File`/`UUID` → `core/PlatformFiles.kt`/`core/Ids.kt`（端口）；`ai.onnxruntime` 按设计留在平台实现 | 详见 [`多端跨端-KMP方案.md`](多端跨端-KMP方案.md) §1.2 |

> 结论：**"抽共享层"是搬家 + 换 4 处依赖，不是重写架构**；性能上不可能成为瓶颈（Kotlin/Native 版本另测，见 §7 P1）。

---

## 2. 目标与非目标

### 2.1 目标（按本期优先级排序）

1. **🎯 四端功能可用（本期唯一重点）**：每端都能**独立完成**下面这套动作，且行为对等（§2.3 是验收清单）：
   注册设备 → 聊天（云端 + 端侧路由 + 流式改道）→ 导入文档 → 检索 → 设备工具与权限闸门 → 自检。
2. **逻辑一份**：路由/6 类升级信号/流式前缀守卫/工具调用校验/编排/分块/检索/SSE 解析/协议客户端/评测集，
   四端行为等价（由同一套契约夹具证明）——**这是"功能可用"能持续成立的前提，不是可选项**。
3. **UI 各端原生**：Compose（Android）/ SwiftUI（iOS、Mac）/ ArkUI（鸿蒙）——体验优先，接受"多写 UI"。
4. **数字可比**：同一评测集在四端跑出同一张表（Hit@1/Hit@3/MRR、阈值标定、端侧完成率、升级率）。
5. **按需编译**：`apps/` 下每端可独立编译；`doc_guard` 的协议校验从三方扩到 **N 方**。

> **非本期重点（登记，不投入）**：真机性能数字（等硬件）、包体极致优化（只做 P0 免费瘦身）、
> 代码级插件化、**L1 里的「设备适配」子集**（机型/SoC/内存档位映射、按 ROM 的差异开关——只预留字段）、
> 桌面端（搁置 D16）、车机、NPU 加速。
> **L1 的通用部分（阈值/升级信号/提示词/工具白名单/模型档位/路由偏好）本期实现**。

### 2.2 非目标（明确不做）

- ❌ **不做跨端统一 UI（CMP）**：已评估并留档（[`多端跨端-CMP方案评估.md`](多端跨端-CMP方案评估.md)），仅当"端数继续涨 + 人力仍是一人"时再翻出来；
- ❌ **不做桌面（Windows/Ubuntu）**：**本轮搁置**，理由与前置条件见 §4 D3 与 `docs/BACKLOG.md` D16；
- ❌ 不做第二个服务端（Linux 服务器版）；
- ❌ 不做车机（OHOS 系，鸿蒙打通后另议）；
- ❌ 不在本期做 NPU 加速（先用 CPU/GPU 路径把功能与基线跑对）。

### 2.3 「功能可用」验收清单（四端逐项对等 · 本期 DoD）

> 口径：**能在该端上自己跑通**（不是"代码写了"）。每项都要有可复现的操作与观察点；
> 端侧推理可按端降级（鸿蒙首版可用宿主/云端），但**行为语义必须一致**。

| # | 功能 | 验收（每端都要满足） | Android 现状 |
|---|---|---|---|
| F1 | 设备身份 | 能 enroll / refresh，凭证加密落盘（Keystore/Keychain/HUKS），重启后仍有效，能吊销 | ✅ 已有 |
| F2 | 云端聊天 | SSE 流式逐字输出；响应里能显示**本次执行位置**（edge/cloud）与理由 | ✅ 已有 |
| F3 | 端侧路由 | 端侧优先；6 类升级信号生效；**流式前缀守卫零外泄**；升级时自动注入交接摘要（用户只看到一个答案） | ✅ 已有 |
| F4 | 文档导入 | PDF（四类结果各有可读原因）+ Markdown；分块入库；**删除单篇不误清全库** | ✅ 已有 |
| F5 | 端侧检索 | 同一标注集（12 篇 / 33 问）：**Hit@1 87% / Hit@3 100% / MRR 0.928**；int8 阈值 0.5 | ✅ 已有 |
| F6 | 工具与权限 | 三道闸门（未注册 / 缺参数 / 未授权）；越权被拒并留痕；权限审计可查 | ✅ 已有 |
| F7 | 隐私硬边界 | `DEVICE_ONLY` 永不出设备；**含 R10 的"本机"判据**（远端端点不得承接 DEVICE_ONLY） | ⚠️ 待 M4 收口 |
| F8 | 离线/降级 | 断网时明确降级（端侧可答则答，不可答则**说清原因**），不静默失败 | ✅ 已有 |
| F9 | 自检入口 | 一条命令/一个入口跑自检，输出 PASS/FAIL/SKIP 与关键数字（可与 Android 的自检项对比） | ✅ 已有（26/26） |
| F10 | 构建与打包 | 该端能独立构建（CI 可跑），不依赖其他端的私有状态 | ✅ 已有（CI 已跑） |

**跨端一致性验收**（每端都要过）：契约夹具全绿 + 同一评测集数字与 Android 一致 + 路由事件结构同构。

---

## 3. 架构：五层 + 一条硬边界（四端视角）

```
┌──────────────────────────────────────────────────────────────────────┐
│ L5 UI（原生各写一份）  Compose(Android) │ SwiftUI(iOS/Mac) │ ArkUI(鸿蒙) │
├──────────────────────────────────────────────────────────────────────┤
│ L4 平台端口（各端实现，薄）  网络 · 凭证 · 文件 · PDF · 存储 · 嵌入运行时  │
│                            · LLM 运行时 · 设备工具 · 权限 · 日志         │
├──────────────────────────────────────────────────────────────────────┤
│ L3 可移植纯逻辑（KMP 一份）  路由/守卫/工具/编排/RAG/评测/常量           │
├──────────────────────────────────────────────────────────────────────┤
│ L2 契约层（语言无关）        协议文档 + JSON 夹具 + 各端 runner          │
├──────────────────────────────────────────────────────────────────────┤
│ L1 云端 SEKB REST/SSE（已存在，不改）                                   │
└──────────────────────────────────────────────────────────────────────┘
```

**硬边界（M1 已实现，跨端后必须收口）**：`DEVICE_ONLY` = **永不离开本设备**。
⚠️ 它现在有一个**语义缺口**：LLM 平面的 EDGE 是"按 `edgeBaseUrl` 寻址的端点"，
而这个地址**可能是另一台机器**（模拟器里就是 `10.0.2.2` = 开发机）。嵌入侧已有 `isOnDevice` 闸门，
LLM 侧没有 → 见 §8 R10（**建议在 M4 一起收口**）。

---

## 4. 四个关键决策

### D1 · UI：各端原生（**owner 已定**）

| 端 | UI 技术 | 现状 | 备注 |
|---|---|---|---|
| Android | Jetpack Compose | **已有**（`ui/ChatScreen.kt` 265 行、`ChatViewModel.kt` 330 行） | 只做"接共享层"的适配，不重写界面 |
| iOS | **SwiftUI** | 待写 | 不为 CMP 付"手感差异"成本；平台特性（Keychain/PDFKit/分享）本来就要写 |
| 鸿蒙 | **ArkUI** | 待写 | 同 iOS 理由 |
| Mac | **SwiftUI（复用 iOS 代码）** | 待写 | 用同一份 SwiftUI + `#if os(macOS)` 分支，**不写第三套 UI** |

CMP 评估文档里那 16 项能力对照与 12 条待实测门槛（V-1…V-12）**归档保留**；
若将来端数继续增加或人手不变，可直接拿它做决策输入。

### D2 · 鸿蒙：KMP 逻辑 + ArkUI 原生 UI（**先 3 天 spike**）

> **"spike" 是什么**：不是"试试看"，而是**有明确期限、有可证伪验收、有失败出口的技术验证**——
> 目的是用**最小代价**把一个"不做就不知道行不行"的关键假设问出答案，**答案可以是"不行"**。
> 做法：只写能验证假设的最小代码（不接产品、不做 UI），跑完就写结论并**按结论选路**，
> 不允许"反正写了点，就继续往下做"。本端 spike 的产出只有两样：**一份结论 + 四条验收的原始日志**。
>
> 事实前提（外部 + 本机实测）：
- 官方 Kotlin/Native **没有 OHOS target**；华为在 HDC 2026 发布社区版 **CPF-KMP-CMP**
  （基于 KMP 2.2.21 + CMP 1.9.2，新增 `OHOS_ARM64`/`OHOS_X64`，毕昇 LLVM 19 出 ELF）——**Beta + 社区分叉**；
- 本机 **已实测**：OHOS NDK（`aarch64-unknown-linux-ohos-clang` 15.0.4）能编 + 链出
  `ELF 64-bit LSB pie executable, ARM aarch64`（musl）→ **"给鸿蒙编 C/C++ 库"这条链路是通的**；
  同一条实测也暴露风险：clang 15 基线偏老，ONNX Runtime 可能要打补丁或降版本。

**spike（3 天）四条验收，任一不过就退路线 B**：
1. KMP 模块能编出 `ohos_arm64` 产物，并被一个 HAP 通过 NAPI 调用（打日志即算成功）；
2. `kotlinx-coroutines` + `kotlinx-serialization` 在 `ohos_arm64` 上**能解析到产物并跑起来**；
3. 用本机 OHOS NDK 交叉编译 **ONNX Runtime**，跑通 `bge-small-zh` 出向量（与 Android 同文本、余弦一致）；
4. HAP 能装能起（模拟器或真机至少一个）。

**路线 B（spike 失败时）**：ArkUI 原生 UI + **ArkTS 重写纯逻辑**，但必须**先把契约夹具抄过去**
（用 Android 侧的单测当模板）——没有测试的"重写一份"等于制造第二套事实。

### D3 · 桌面（Windows/Ubuntu）：搁置（记入 `docs/BACKLOG.md` D16）

不是"不做"，是**现在不做**。审计已给出结论与代价，存证在
[`多端跨端-桌面端方案审计.md`](多端跨端-桌面端方案审计.md)：

| 项 | 结论 |
|---|---|
| 价值 | 算力宿主（给手机供算力）+ 文件主场；**不是**"第五个客户端" |
| 代价 | 宿主 **4.5–6.5 人日**（原估 2–3 偏低：鉴权默认值、资源上限、Windows 服务化、卸载） |
| 关键修正 | **拓扑 A≠B**：M0 profile 的 `planes.edge.base_url=127.0.0.1`，对手机毫无用处 |
| 前置条件 | ① 先修 §8 R10 的 `DEVICE_ONLY` 语义收口；② 有 Windows/Ubuntu 的验证手段（VM 或 CI runner） |
| GUI | 原生 UI 路线下**建议不做**（浏览器入口为终点），可省 5–8 人日 |
| 重启用条件 | 有人真的要在 PC 上管文档 / 要给家里手机供算力 / 需要演示给非技术观众 |

### D4 · 端侧推理运行时矩阵（四端各选什么）

| 能力 | Android | iOS | 鸿蒙 | Mac |
|---|---|---|---|---|
| 本地 LLM | 宿主 Ollama（现）→ llama.cpp NDK（后） | 宿主 Ollama（模拟器 `127.0.0.1:11434`）→ llama.cpp（Metal） | **先用宿主/云端**；本地 LLM 最后做 | **Ollama（M0 已完成，8B/4bit 61 tok/s、32B/4bit 16 tok/s）** |
| 嵌入 | ONNX Runtime Android 1.20.0（已用） | ONNX Runtime iOS（C/ObjC，可选 CoreML EP） | ONNX Runtime 自建 OHOS 版（NDK 已验证） | ONNX Runtime（JVM，API 与 Android 同一套） |
| 向量存储 | SQLite（已用） | SQLite（FMDB/自写 C） | relationalStore / SQLDelight（待验） | SQLite（JDBC） |
| 凭证 | Keystore（已用） | Keychain | HUKS | Keychain |
| 传输 | OkHttp（已用）→ **libcurl** | NSURLSession（**保留系统栈**） | **libcurl（与 Android 共用一份 C 实现）** | NSURLSession（复用 iOS） |
| ⤷ 说明 | 网络层统一方案（libcurl 范围、TLS 自建、iOS 为何保留）见 [`多端跨端-工程议题（网络层·包体·热修复）.md`](多端跨端-工程议题（网络层·包体·热修复）.md) §1，待确认项 **S1** ||||

**一条纪律**：嵌入模型在所有端必须是**同一空间戳**（`BAAI/bge-small-zh-v1.5-int8@512`），
否则端侧向量与云端 L3 不可比（M3 定的第一条不变量）。

---

## 5. 布局与迁移路径

### 5.1 目标结构

```
apps/
├── settings.gradle.kts / gradlew     # wrapper 从 apps/android 上移到 apps/
├── shared/                           # ★ KMP：core(纯逻辑) + ports(端口) + 各端 actual + commonTest
├── contract/                         # ★ 语言无关契约夹具（JSON）+ 各端 runner 约定
├── android/app/                      # 现有 Android（Compose 原生 UI）
├── ios/                              # Xcode 工程（SwiftUI 原生 UI）+ 平台端口实现
├── harmony/                          # DevEco 工程（ArkUI 原生 UI）+ NAPI 桥
└── mac/                              # SwiftUI（复用 ios/ 的 UI 源码）+ macosArm64 target
```

### 5.2 迁移 8 步（每步都能证伪）

| 步 | 动作 | 验收 |
|---|---|---|
| 1 | **契约夹具先行**（协议/字段/升级信号/隐私用例/评测集 JSON 化）+ Android runner | ✅ **已完成（2026-09-21）**：186 测试全绿（含契约 4），且可证伪（改夹具即红） |
| 2 | **依赖去平台化**：`org.json` → `JsonX` 门面；`HttpTransport` 拆接口/实现；`File/UUID` 变端口 | ✅ **已完成（2026-09-21）**：**202 测试全绿**、自检 30 PASS / 2 SKIP、检索数字不变 |
| 4 | ✅ **P0 包体瘦身（已完成 2026-09-21）**：ABI 只发 arm64-v8a + 排除 BouncyCastle PQC 参数 | **debug APK 102 MB → 41.6 MB**；自检 **30 PASS / 2 SKIP** 不回归（R8 留待签名的 release + E2E 轮次） |
| 3 | 建 `apps/shared`（KMP），搬 26 个纯逻辑文件（3,395 行）进 `commonMain` | ✅ **已完成（2026-09-21）**：**202 测试全绿** + 自检 **30 PASS / 2 SKIP**；平台实现留在 app 模块，shared 保持零平台依赖 |
| 4 | 加 `iosArm64`/`iosSimulatorArm64`/`macosArm64` + 补 `iosMain` 端口 | `linkDebugFrameworkIosSimulatorArm64` 通过；微基准与体积出数 |
| 5 | Kotlin 2.2.10 → **≥2.2.21**（**单独提交 + 回滚 tag**） | Android 全量复跑：207 测试 + 自检 30 PASS / 2 SKIP + 检索评测 |
| 6 | iOS（SwiftUI）成形 | 见 §7 M5 验收 |
| 7 | 鸿蒙 spike →（通过则）ArkUI 端 | 见 §4 D2 四条 |
| 8 | Mac App（复用 iOS SwiftUI + `macosArm64`） | 见 §7 M8 验收 |

---

## 6. 一致性机制（原生 UI 路线下**更**重要）

四端各写 UI 后，一旦逻辑或常量也各写一份，"行为一致"就只能靠人。所以下面四件事是**前置条件**：

| 机制 | 做什么 | 落地物 |
|---|---|---|
| **共享逻辑（唯一实现）** | 路由/守卫/校验/检索只有一份 | `apps/shared/core` |
| **契约夹具（单一事实源）** | 协议/字段/错误码/升级信号/隐私用例 JSON 化，各端 runner 必须全绿 | `apps/contract/` |
| **同一套评测集** | 检索 33 问 / 意图分类 / 工具调用，各端产出同一张表 | 现有 `RetrievalEvalSet` 上移共享层 |
| **不变量集中 + N 方守卫** | 空间戳、阈值 0.5、6 类升级信号只定义一次；`doc_guard` 第 5 项从三方扩到 **N 方** | `shared/constants`、`scripts/check_protocol_paths.py` |

---

## 7. 里程碑与验收（四端）

| 阶段 | 内容 | 工作量 | 验收（可证伪） |
|---|---|---|---|
| **M4** 共享层 | 契约夹具 ✅ + 去平台化 + `shared/` + iOS/Mac target + Kotlin 升级 | **8–12 天** | Android **207 测试 + 自检 30 PASS / 2 SKIP + 检索数字 不回归**；`shared` 在 JVM 与 `iosSimulatorArm64` 均可编译并通过共享单测；**P1** 微基准 ≤100 µs（Kotlin/Native 版）、**P3** iOS 包体积增量 ≤5 MB |
| **M4.5** L1 策略热修（**本期实现**） | 服务端 `GET /edge/policy`（版本化 + 签名 + 灰度桶）**✅ 已完成**（`edge_policy.py` + 端点 + 19 条测试）；**端侧校验与应用也已实现**（`EdgePolicy.kt`：验签/有效期/空间戳/白名单/灰度 → 合并进 `EdgeRuntimeConfig`，含**跨语言签名向量测试**）；⏳ 拉取时机与双槽持久化/审计落盘待接；**「设备适配」子集只预留字段** | ✅ 主体完成 | ① 单测：非法/过期/空间不符/越白名单的策略**必须被拒**且留痕；② 灰度：同版本策略按设备分桶生效，可一键回滚到上一版；③ 端到端：改一次阈值 → 不发版、不重启也能生效（日志可证）；④ 后端全量测试不回归（基线 **971**：955 单元 + 16 集成） |
| **M5** iOS 端 | SwiftUI UI + NSURLSession/Keychain/PDFKit + 自检入口 | **12–16 天** | 自检项与 Android **等价**；检索 **Hit@1 87% / Hit@3 100% / MRR 0.928**（同语料同模型）；路由事件与 Android 同构；**P2** 跨语言调用 ≤5 µs |
| **M6** 鸿蒙 spike | D2 四条 | **3 天** | 四条全通 → M7；任一不通 → 路线 B（ArkTS + 契约夹具） |
| **M7** 鸿蒙端 | ArkUI UI + NAPI 桥 | **12–16 天** | 契约 runner 全绿；嵌入向量与 Android **同空间同余弦** |
| **M8** Mac 端 | SwiftUI（复用 iOS 源码）+ `macosArm64` | **4–6 天** | 本机原生跑通：聊天 + 文档导入 + 检索；宿主角色沿用 M0 实测数字 |
| 并行 | **真机验收**（Android/iOS/鸿蒙） | 等硬件 | decode tok/s、TTFT、内存峰值、发热——**模拟器测不了**（RFC §9.1 口径） |

---

## 8. 风险与对策

| # | 风险 | 影响 | 概率 | 对策 / 回退 |
|---|---|---|---|---|
| R1 | 依赖可移植性（`org.json`/OkHttp/`java.*`/ONNX JVM API） | 共享层抽不出 | **高（确定发生）** | §5.2 步 2 先做，有 207 测试兜底 |
| R2 | Kotlin/Native 体积/启动超预期 | 包体积、冷启动 | 中 | P3 实测；DCE、静态 framework、裁剪依赖面 |
| R3 | Kotlin/Compose 升级打破现有 Android 基线 | 已验证基线失效 | 中 | §5.2 步 5 隔离提交 + 回滚 tag + 全量复跑 |
| R4 | 鸿蒙 `ohos_arm64` 依赖产物供给不足 | 鸿蒙 KMP 走不通 | 中 | spike 四条第 2 项专门验；失败退路线 B |
| R5 | 鸿蒙模拟器/真机验证手段不足 | 只能"编过"，不能"跑过" | 中 | spike 第 4 条要求 HAP 能装能起；不可行则先只交付"能编 + 契约测试" |
| R6 | 四端一致性靠人盯 → 必然漂移 | 端侧指标不可比 | **高** | §6 四机制**先做**，不放到最后 |
| R7 | 原生 UI × 4 端验证成本 | 每轮改动验证慢 | **高** | 分层验证：共享逻辑跑 JVM 单测（秒级）；各端只跑端口 + 端到端自检 |
| R8 | 无真机（Android/iOS/鸿蒙都缺） | 性能结论缺失 | 高（现状） | 模拟器验功能、Mac 拿性能真值、按带宽保守外推 |
| R9 | 桌面搁置后"给手机供算力"能力缺失 | 手机上只能用宿主（开发机）或云端 | 低（可接受） | 已记 D16；Mac 宿主可用（M0 完成） |
| **R10** | **`DEVICE_ONLY` 在 LLM 平面缺"本机"判据** | 若 EDGE 指向局域网另一台机器，标记"永不出设备"的数据就会出设备 | 中 | ✅ **已实现（2026-09-21，M4 第 3 步）**：`PlaneRouter.isLocalEndpoint()` 只在回环地址（`127.0.0.1`/`localhost`/`[::1]`）判定为"本机"，域名/`0.0.0.0`/局域网/尾网一律按非本机处理 → `blockedReason=device_only_requires_local_runtime`；编排器**一个请求都不发**（端侧 0 次、云端 0 次）并给用户可读原因。证据：客户端 `PlaneRouterTest` 5 条 + `ChatOrchestratorTest` 2 条 + 契约夹具 `privacy.json` 13 case（含本机/局域网/模拟器宿主/尾网四种端点）+ 模拟器自检 `privacy_device_only_local_gate`；**服务端同口径同步**（`backend/app/core/plane_router.py` 的 `is_local_endpoint()` / `Decision.blocked_reason`，`RoutedLLM.ainvoke` 与 `astream_with_stats` 在 `is_blocked` 时直接抛错不发请求，`backend/tests/unit/test_plane_router.py` +5 条）。⚠️ **行为变化**：模拟器上 DEVICE_ONLY 请求会被明确拒绝（这正是正确语义） |

---

## 9. 决策记录（✅ 6 条已由 owner 确认，2026-09-21）

| # | 决策 | 结论 |
|---|---|---|
| Q1 | 四端范围：Android（已在）/ iOS / 鸿蒙 / Mac，桌面搁置 | ✅ **已确认**（桌面 → `docs/BACKLOG.md` D16） |
| Q2 | UI 各端原生（Compose / SwiftUI / ArkUI），CMP 归档为备选 | ✅ **已确认** |
| Q3 | 顺序 M4 共享层 → iOS → 鸿蒙 spike → Mac（Mac 复用 iOS 的 SwiftUI） | ✅ **已确认** |
| Q4 | 鸿蒙先 3 天 spike，四条验收任一不过 → 退 ArkTS 重写 + 契约夹具 | ✅ **已确认** |
| Q5 | R10（`DEVICE_ONLY` 语义收口）放进 M4 | ✅ **已确认**（0.5–1 天） |
| Q6 | 允许改 CI（`.github/workflows` 加 macOS runner） | ✅ **已确认** |
| Q7 | **本期北极星 = 四端「功能可用」**（§2.3 清单）；性能数字/包体极致/代码插件化**非本期重点**；**L1 策略热修本期正常实现**（下发/缓存/校验/灰度/回滚/审计/白名单 + 空间戳绑定，M4.5）；**仅「设备适配」类策略只预留字段** | ✅ **已确认（owner 2026-09-21：L1 正常实现；Android 设备适配相关的策略/配置预留、本期不实现）** |

**另有三条工程议题待确认**（owner 2026-09-21 提出）：
网络层是否统一到 **libcurl**、跨端包体增长是否要**插件化**、是否一起做**热修复**。
分析与建议：见 [`多端跨端-工程议题（网络层·包体·热修复）.md`](多端跨端-工程议题（网络层·包体·热修复）.md)，需确认项 **S1–S4**。

**开工状态**：M4 共享层（契约夹具 → 去平台化 → `shared/` → iOS target → Kotlin 升级 → R10 收口 + P0 包体瘦身）。

---

## 10. 外部事实与本地待验证

| # | 事实 | 状态 |
|---|---|---|
| E1 | CMP（备选路线）能力与原生体验差异：16 项对照 + 12 条待实测门槛 | ✅ 已核验/已归档（[`多端跨端-CMP方案评估.md`](多端跨端-CMP方案评估.md)） |
| E2 | 华为 HDC 2026 发布 KMP&CMP 鸿蒙社区版 Beta（KMP 2.2.21 + CMP 1.9.2，`OHOS_ARM64` target） | 📄 外部来源（EEPW/第三方分析）→ **spike 实测为准** |
| E3 | 官方 Kotlin/Native **无 OHOS target** | ✅ 已核验（官方 target 列表） |
| E4 | 支付宝 MYKMP：一套 Kotlin/Compose 跑 Android/iOS/HarmonyOS | 📄 旁证（不作技术依据） |
| E5 | OHOS 可构建 ONNX Runtime（社区移植文档给出 riscv64 流程） | ⏳ 待验（spike 第 3 条；本机 NDK 已具备） |
| E6 | llama.cpp 在做 OpenHarmony/musl 兼容，但社区反馈鸿蒙上仍有问题 | ⚠️ 已知有坑 → 鸿蒙本地 LLM 排最后 |
| E7 | 本机 iOS 工具链：Xcode 26.6、iOS 模拟器 26.3/26.4（需 `DEVELOPER_DIR` 指向 Xcode） | ✅ 已验证 |
| E8 | 本机鸿蒙工具链：DevEco SDK API 22 + OHOS NDK；**交叉编出 aarch64 musl ELF 已实测**（clang 15.0.4） | ✅ 编译链路已验证；⏳ ONNX 待验 |
| E9 | Kotlin/Native 支持 `macosArm64`（Mac 端复用 iOS 逻辑与 UI 的前提） | ⏳ 待验（M4 步 4） |
| E10 | 桌面（Windows/Ubuntu）：宿主/GUI 的全部外部事实与验证计划 | 🗄️ 已存档（[`多端跨端-桌面端方案审计.md`](多端跨端-桌面端方案审计.md)），D16 |
