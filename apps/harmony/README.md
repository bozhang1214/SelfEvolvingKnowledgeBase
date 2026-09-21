# apps/harmony · 鸿蒙端侧宿主（规划中）

**状态：占位。** 记录开工前的事实与取舍。

> ⚠️ **2026-09-20 更新**：下面「ArkTS 不能复用 Kotlin」的前提**已经过期**——2026-06 华为 HDC 发布了
> **KMP/CMP 鸿蒙社区版 Beta**（`CPF-KMP-CMP`，基于 KMP 2.2.21 + CMP 1.9.2；Kotlin/Native 新增
> `OHOS_ARM64`/`OHOS_X64` target，毕昇 LLVM 19 出 ELF，再由 NAPI 与 ArkTS 协作）。
> 新的路线判断、**3 天 spike 的四条验收标准**、回退条件与风险见
> [`docs/RFC-多端跨端方案.md`](../../docs/RFC-多端跨端方案.md) §4 D2 与 §10 E2/E3。
> **结论先说**：优先走 KMP/CMP 路线（spike 通过则复用 ~70% 逻辑），spike 失败才退回下面的路线 A。
> 本机已具备条件：DevEco SDK **API 22** + OHOS NDK（`aarch64-unknown-linux-ohos-clang`，sysroot `aarch64-linux-ohos`）→ C/C++ 可交叉编译。


## 本机工具链（2026-09-18 实测）

| 项 | 路径 / 实测值 |
|---|---|
| DevEco Studio | `/Applications/DevEco-Studio.app` |
| SDK | `/Applications/DevEco-Studio.app/Contents/sdk/default`（另有 `~/Library/Huawei/Sdk/productConfig.json`） |
| 构建系统 | `Contents/tools/hvigor`（`hvigor` + `hvigor-ohos-plugin`） |
| 包管理 | `Contents/tools/ohpm` |
| 内置 Node / JDK | `Contents/tools/node/bin`、`Contents/jbr` |

即：**命令行可构建**（hvigor + ohpm），不必只依赖 IDE 界面。

## 关键取舍：ArkTS 不能复用 Kotlin（与 iOS 不同）

iOS 能通过 Kotlin Multiplatform 共用纯逻辑，**鸿蒙不能**。所以三条路线：

| 路线 | 做法 | 代价 |
|---|---|---|
| A. ArkTS 重写纯逻辑 | 按协议文档重写路由/守卫/工具校验 | 逻辑会漂移；**必须**配同一套契约测试（Android 侧 96 条单测可当模板） |
| B. C/C++ 核心 + 三端绑定 | 把纯逻辑下沉成 C 核心，Kotlin/Swift/ArkTS 各自 FFI | 一次投入大，但三端一致性最好；顺带能复用到车机 |
| C. 端侧只做 UI + 云侧逻辑 | 鸿蒙端不做本地推理，只当"瘦客户端" | 最省事，但违背"端侧优先"的初衷 |

**建议**：先按 A 起步（鸿蒙端的本地推理本来就排在 Android/iOS 之后），
等三端都要做时再评估 B。**无论哪条路线，落地的第一步都是先把契约测试抄过去**——
没有测试的"重写一份"等于制造第二套事实。
