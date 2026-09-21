# apps · 各端应用

一个仓库装下**服务端 + 所有端侧应用**，按需编译：

```
apps/
├── android/     Kotlin + Jetpack Compose（已可用）
├── ios/         iOS 宿主（规划中，见 apps/ios/README.md）
├── harmony/     HarmonyOS 宿主（规划中，见 apps/harmony/README.md）
└── desktop/     Windows/Linux/macOS（规划中：边缘宿主 + 桌面 GUI）
```

> **多端跨端方案（draft，待 owner 拍板）**：[`docs/RFC-多端跨端方案.md`](../docs/RFC-多端跨端方案.md)。
> 关键建议：① UI 统一到 **CMP**（Android/iOS/鸿蒙/桌面一套 UI）；② 鸿蒙走 **KMP/CMP 鸿蒙版**（先 3 天 spike）；
> ③ 桌面**先做 headless 边缘宿主**（给手机端供算力）、GUI 后置。开工前先看那份文档 §9 的决策清单。

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
| **纯逻辑**（无平台依赖） | 路由决策、6 类升级信号、前缀守卫、SSE 解析、工具调用 JSON、编排器 | 目标：`shared/core`（Kotlin Multiplatform）→ **四端共用一份**（Android/iOS/鸿蒙/桌面） |
| **平台适配** | HTTP 传输、凭证存储（Keystore/Keychain/HUKS）、权限、设备工具、文件与 PDF、SQLite、嵌入运行时 | 各端自己的目录（薄） |
| **UI** | 聊天 / 来源 / 文档 / 设置 | 目标：`shared/ui`（Compose Multiplatform）→ 一套 UI 四端跑 |

现状：这些纯逻辑已经**物理隔离**在 `apps/android/app/src/main/kotlin/com/sekb/ondevice/`
的 `route/` `net/` `tools/` `chat/` `eval/` 里（它们不 import 任何 `android.*`），
因此抽成 KMP 模块是**搬家**而不是重写。实测：main 5,406 行里 **3,786 行（70%）可移植**，
剩下 1,620 行是平台代码。抽出的时机是开始做 iOS 时（M4）。

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
# Android（唯一已可用的端）
bash scripts/android.sh test            # 单元测试，无需模拟器
bash scripts/android.sh assemble        # 打 debug APK
bash scripts/emulator.sh --background   # 起模拟器（状态全在 .tooling/ 内）
bash scripts/android.sh install         # 装到已连接的模拟器/真机
```

**实测（2026-09-18）**：从零到"模拟器里跑出端侧推理"全程**没有任何仓库外的写操作**，
因此也不需要额外授权——构建缓存、debug keystore、AVD、模拟器的临时文件都在 `.tooling/`。

构建状态全部落在仓库内的 `.tooling/`（Gradle 缓存、Android debug.keystore），
所以**不需要任何仓库外的写权限**——这也是把它放进仓库的附带好处。
