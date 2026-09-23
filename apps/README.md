# apps · 各端应用

一个仓库装下**服务端 + 所有端侧应用**，按需编译：

```
apps/
├── contract/    语言无关契约夹具（四端跑同一份 JSON，见 apps/contract/README.md）
├── android/     Kotlin + Jetpack Compose（✅ 可用）
├── ios/         iOS 宿主（✅ 已完成：SwiftUI 原生 UI，见 apps/ios/README.md）
├── harmony/     HarmonyOS 宿主（🟡 spike 未收口：ArkUI 原生 UI，见 apps/harmony/README.md）
├── mac/         Mac 宿主（✅ 已完成：复用 ios/ 的 SwiftUI 源码，见 apps/mac/README.md）
└── desktop/     Windows/Ubuntu —— **本轮搁置**（跟踪项 docs/BACKLOG.md D16）
```

> **四端状态（2026-09-22 实测）**
>
> | 端 | 状态 | 自检 | 备注 |
> |---|---|---|---|
> | Android | ✅ 已完成 | 30 PASS / 0 FAIL / 2 SKIP | 231 单测（含契约夹具逐 37 个 case 断言） |
> | iOS | ✅ 已完成 | 26 PASS / 0 FAIL / 1 SKIP | 唯一 SKIP = Keychain 往返（需真实签名身份） |
> | Mac | ✅ 已完成 | 26 PASS / 0 FAIL / 1 SKIP | 与 iOS **同一份 Swift 源码** |
> | 鸿蒙 | 🟡 spike 未收口 | — | NAPI↔KMP 链路 + `.ms` 模型转换已通；**运行期/装机等真机** |
>
> 三端检索评测**同一张表**：阈值 0.20–0.60 的 Hit@1/Hit@3/MRR/误召回逐项相同
> （0.40 → 26/30、30/30、0.928、0/3）。

> **四端收敛方案**：[`docs/RFC-多端跨端方案.md`](../docs/RFC-多端跨端方案.md)（**v1.1.0，owner 已确认**）。
> 已定口径：**范围 = Android / iOS / 鸿蒙 / Mac**（桌面搁置）；**逻辑一份（KMP 共享）+ UI 各端原生**
> （Compose / SwiftUI / ArkUI，**不用跨端统一 UI**）；顺序 = M4 共享层 → iOS → 鸿蒙 spike → Mac。
> 细节：主线 [`多端跨端-KMP方案.md`](../docs/多端跨端-KMP方案.md)、备选评估（**已归档、不采用**）
> [`多端跨端-CMP方案评估.md`](../docs/多端跨端-CMP方案评估.md)。

## 为什么放在同一个仓库（而不是各端独立成仓）

1. **用户视角**：一次 clone 拿到全量代码，想用哪端就编哪端。多仓 + 子模块会让"拉全量"
   变成一件需要说明书的事（本项目已有 `jobcopilot` 子模块的前车之鉴）。
2. **契约一致性**：端云两侧共享同一份协议（[`docs/ops/16-端云协同协议.md`](../docs/ops/16-端云协同协议.md)）。
   文档与实现放在同一个提交里改，才能避免"文档说 A、代码做 B"。
3. **跨端逻辑只写一遍**（见下）。

## 端侧逻辑的分层（重要）

端云协同的判定逻辑（平面路由、升级信号、流式前缀守卫、工具调用校验）**必须三端一致**，
否则各端的"端侧完成率"互相不可比。所以：

| 层 | 内容 | 放哪 |
|---|---|---|
| **纯逻辑**（无平台依赖） | 路由决策、6 类升级信号、前缀守卫、SSE 解析、工具调用 JSON、编排器 | ✅ **已落地**：`apps/shared/src/commonMain`（Kotlin Multiplatform）→ **四端共用一份**（Android/iOS/鸿蒙/Mac），**35 文件 / 4,066 行** |
| **平台适配** | HTTP 传输、凭证存储（Keystore/Keychain/HUKS）、权限、设备工具、文件与 PDF、SQLite、嵌入运行时 | 各端自己的目录（薄）。⚠️ **嵌入运行时是唯一没能"写一次"的**：Android 走 ORT Java API、iOS/Mac 走 ORT C API、鸿蒙走系统 MindSpore Lite —— 语义对齐靠共享层的 `EmbeddingProvider` 接口 + 同一套分词器 + 同一空间戳 |
| **UI** | 聊天 / 来源 / 文档 / 设置 | ✅ **各端原生，不用跨端统一 UI**（owner 已定 D1）：Compose（Android）/ SwiftUI（iOS + Mac **共用一份源码**）/ ArkUI（鸿蒙）。~~目标 `shared/ui`（Compose Multiplatform）~~ **已否决**，CMP 评估归档为备选 |

现状：纯逻辑已经**真的搬进 KMP 模块**（不是"目标"）——`apps/shared/src/commonMain`
**35 文件 / 4,066 行零平台依赖**，各端只留薄的平台实现：
Android `app/src/main` 2,695 行 / 15 文件、iOS `App/` 1,118 行 / 5 文件（Mac 复用同一份）。

⚠️ **鸿蒙的旧结论已过期**：2026-06 HDC 华为发布了 KMP/CMP 鸿蒙社区版 Beta（Kotlin/Native 新增
`OHOS_ARM64` target），"ArkTS 不能复用 Kotlin" 不再是唯一选项。新的路线判断、spike 验收标准与
回退条件见 [`docs/RFC-多端跨端方案.md`](../docs/RFC-多端跨端方案.md) §4 D2；
**无论走哪条路线，都必须先有契约测试**——没有测试的"重写一份"等于制造第二套事实。

## 进度与恢复

端侧线的**当前进度、待办与恢复步骤**在
[`docs/RFC-端云协同-实施记录.md`](../docs/RFC-端云协同-实施记录.md) 开头的
「阶段总结与恢复指引」；那里也有三条必须守住的不变量（向量空间戳、阈值标定、设备专属数据）。

## 按需编译

```bash
# Android
bash scripts/android.sh test            # 231 单测，无需模拟器
bash scripts/android.sh assemble        # 打 debug APK
bash scripts/emulator.sh --background   # 起模拟器（状态全在 .tooling/ 内）
bash scripts/android.sh install         # 装到已连接的模拟器/真机

# iOS（模拟器）
bash scripts/ios.sh smoke               # framework + Swift 冒烟（macOS 宿主上真跑共享逻辑）
bash scripts/ios_app.sh run             # 装到 iPhone 模拟器并抓自检输出

# Mac（与 iOS 共用 app/ios/App/*.swift，不复制源码）
bash scripts/mac_app.sh run             # 产出 .app、直接跑、stdout 抓自检

# 鸿蒙（spike）
bash scripts/harmony_spike.sh kn        # 编 KMP 产物 libkn.so（ohosArm64 + ohosX64）
bash scripts/harmony_spike.sh hap       # 编 HAP + 断言 NAPI↔KMP 引用
bash scripts/harmony_spike.sh mindspore # 断言 MindSpore Lite 平台绑定可用
bash scripts/model_to_ms.sh all         # 把 bge-small-zh 转成鸿蒙要的 .ms（详见该脚本顶部说明）
```

**编译状态是否真"按需"**：每端都能独立构建，且**构建状态全部落在仓库内 `.tooling/`**
（Gradle 缓存、Android debug keystore、AVD 与模拟器临时文件、Kotlin/Native konan、hvigor home、npm 缓存），
因此从零到"跑出端侧推理"全程**没有任何仓库外的写操作**，也不需要额外授权。

（鸿蒙侧尤其明显：hvigor 默认往 `~/.hvigor` 与 `~/.npm` 写，在受限沙箱里直接 **EPERM**，
必须靠 `HVIGOR_USER_HOME` / `HOME` / `npm_config_*` 收进仓库，见 `scripts/harmony_spike.sh` 注释。）
