# apps · 各端应用

一个仓库装下**服务端 + 所有端侧应用**，按需编译：

```
apps/
├── android/     Kotlin + Jetpack Compose（已可用）
├── ios/         SwiftUI（规划中，见 apps/ios/README.md）
└── harmony/     ArkTS / ArkUI（规划中，见 apps/harmony/README.md）
```

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
| **纯逻辑**（无平台依赖） | 路由决策、6 类升级信号、前缀守卫、SSE 解析、工具调用 JSON、编排器 | 目标：`shared/`（Kotlin Multiplatform）→ Android + iOS 共用一份 |
| **平台适配** | HTTP 传输、凭证存储（Keystore/Keychain）、权限、设备工具、UI | 各端自己的目录 |

现状：这些纯逻辑已经**物理隔离**在 `apps/android/app/src/main/kotlin/com/sekb/ondevice/`
的 `route/` `net/` `tools/` `chat/` `eval/` 里（它们不 import 任何 `android.*`），
因此抽成 KMP 模块是**搬家**而不是重写。抽出的时机是开始做 iOS 时（M4）——
在那之前保持现状，避免为还没存在的第二个端付构建复杂度。

⚠️ **鸿蒙是个例外**：ArkTS 不能直接复用 Kotlin。可选路线：① 用 ArkTS 重写一份纯逻辑并
配同一套契约测试（本项目 Android 侧的 96 条单测可直接当模板）；② 把纯逻辑下沉到
C/C++/Rust 核心，三端各自做绑定。**不要**在没有契约测试的情况下手抄逻辑。

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
