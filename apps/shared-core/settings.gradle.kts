// ⚠️ 这个文件是**给鸿蒙 spike 构建用的占位 settings**，不是一份独立的构建。
//
// 背景（H1 实测）：`apps/harmony/spike` 用 OHOS Kotlin fork（2.2.21-0.3.0-07），
// 而 `apps/android` 用标准 Kotlin 2.2.10 + AGP 9。**两者不能合并**：
// 试过 `includeBuild("../../shared")`（复合构建），在**插件应用阶段**就失败：
//
//   Plugin [id: 'org.jetbrains.kotlin.multiplatform'] was not found ...
//   Build file '.../apps/shared/build.gradle.kts' line: 14   （2026-10-25 实测，4s 失败）
//
// 所以 shared-core 的形态是：**一份源码、两侧各自编译**
//   · Android/iOS 侧：`apps/shared`（AGP 9 + 标准 Kotlin）
//   · 鸿蒙侧：`apps/harmony/spike` 把本目录 `include(":shared-core")`
// 本文件只在有人**误以为本目录是独立构建**（例如 `cd apps/shared-core && gradle`）时
// 给出明确提示，避免出现"我在哪份构建里"的困惑。
//
// 正常用法（都在 spike 构建里）：
//   bash scripts/harmony.sh smoke      # 编 ohos 产物 + 跑 interop 冒烟
//   bash scripts/harmony.sh build      # 编 HAP
rootProject.name = "sekb-shared-core-standalone-marker"
