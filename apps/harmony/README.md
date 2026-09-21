# apps/harmony · 鸿蒙端侧宿主（规划中）

**状态：占位。** 记录开工前的事实与取舍。

> ✅ **2026-09-21 定案（KMP 逻辑 + ArkUI 原生 UI，先 spike）**：owner 已定各端原生 UI，
> 所以本端目标形态是 **ArkUI 原生界面 + KMP 共享逻辑**（经 CPF-KMP-CMP 的 Kotlin/Native `OHOS_ARM64`），
> 而不是"用 CMP 写鸿蒙界面"。**先做 3 天 spike**（四条验收见
> [`docs/RFC-多端跨端方案.md`](../../docs/RFC-多端跨端方案.md) §4 D2），任一不过就退回下面的**路线 A**
> （ArkTS 重写纯逻辑，但必须**先把契约夹具抄过去**）。
>
> ⚠️ **前提已变（2026-09-20）**：本节原结论「ArkTS 不能复用 Kotlin」**不再成立**——2026-06 华为 HDC 发布了
> **KMP/CMP 鸿蒙社区版 Beta**（`CPF-KMP-CMP`，基于 KMP 2.2.21 + CMP 1.9.2；Kotlin/Native 新增
> `OHOS_ARM64`/`OHOS_X64` target，毕昇 LLVM 19 出 ELF，再由 NAPI 与 ArkTS 协作）。外部来源与核验状态见
> [`docs/RFC-多端跨端方案.md`](../../docs/RFC-多端跨端方案.md) §10 E2/E3。
>
> 🔧 **本机工具链（已实测）**：DevEco SDK **API 22** + OHOS NDK（`aarch64-unknown-linux-ohos-clang`，sysroot `aarch64-linux-ohos`）。
> 该 clang（15.0.4 "OHOS (dev)"）能编 + 链出 `ELF 64-bit LSB pie executable, ARM aarch64`，
> 解释器 `/lib/ld-musl-aarch64.so.1`（musl）→ "给鸿蒙编 C/C++ 库"这条链路是通的；
> 但 clang 15 基线偏老，ONNX Runtime 可能要吃补丁或降版本（spike 第 3 条要撞的墙）。



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

## M6 spike 前置调研（2026-09-21 实测，文档来自 AtomGit `CPF-KMP-CMP/docs`）

已把官方文档 clone 到 `.tooling/ohos-spike/docs`（`git clone --depth 1 https://atomgit.com/CPF-KMP-CMP/docs.git`，**AtomGit 可达**）。
从文档里读出的四条硬事实（每条都能在 `zh-cn/入门/*.md` 里查到）：

| # | 事实 | 对 spike 的影响 |
|---|---|---|
| 1 | **工具链是 IDE 插件**（"KMP OHOS Support-1.0.0.zip"，离线安装方式；下载源是第三方 nexus `maven.eazytec-cloud.com`，实测 **HTTP 200 可达**） | 官方路径是 Android Studio / IDEA 装插件；**纯命令行路径文档未承诺**——spike 第 1 条要自己趟 |
| 2 | **鸿蒙应用必须签名才能装到真机/模拟器**，签名需在 **DevEco Studio** 里完成，要求**注册并登录华为开发者账号** | **spike 第 4 条（HAP 能装能起）需要 owner 的华为开发者账号** —— 这是只有你能提供的前置条件 |
| 3 | `ohosArm64` = **真机**（arm64-v8a）；**模拟器 / x86_64 开发机需额外启用 `ohosX64`** | 本机模拟器是 x86_64 → 必须同时启用 `ohosX64`，否则产物装不进模拟器 |
| 4 | 版本门槛：JDK 17+、Gradle 8+、DevEco Studio **6.0.0+**、HarmonyOS SDK **API 17+** | 本机：SDK **API 22**（6.0.2.130）✅、OHOS NDK ✅、DevEco tools（hvigor/llvm/node）✅、JDK 有 21 与 JBR（17+ ✅） |

**另外两处网络现实**（与 M5 端口③ 同类）：spike 第 3 条要自建 ONNX Runtime，而 ORT 源码在 **GitHub（本机不可达，实测 HTTP 000）**——
可能的绕行是社区在 AtomGit/GitCode 上的 OHOS 移植镜像（如 `code.ruyicommunity.cn` 的 onnxruntime 移植分支），**待验证**。

**结论**：spike 第 1/2 条（KMP 产物 + kotlinx 库在 ohos 上可解析）**可以试**；
第 3 条受 ORT 源码获取影响（需先找镜像）；
**第 4 条需要 owner 的华为开发者账号**（DevEco 模拟器登录 + 应用签名）。
按方案 §4 D2 的规矩：**四条全通才进 M7，任一不过就退路线 B（ArkTS 重写 + 契约夹具）**。

## M6 spike 进展（2026-09-21）

**spike 第 2 条（kotlinx 库在 ohos 上可解析）：✅ 已在产物层面验证**——第三方 nexus
（`https://maven.eazytec-cloud.com/nexus/repository/maven-public/`）上确实发布了 ohos 变体：

| 坐标 | 实测 |
|---|---|
| `org.jetbrains.kotlinx:kotlinx-coroutines-core-ohosarm64:1.10.2-1.0.0` | **HTTP 200** |
| `org.jetbrains.kotlinx:kotlinx-serialization-json-ohosarm64:1.9.1-1.0.0` | **HTTP 200** |

（"依赖供给"是这条路线最大的风险——现在它从"担心"变成了"已验证"。
注意版本线有两套：文档 三方库表用 `-1.0.0`，官方 sample 用 `-0.3.0-04`。）

**spike 第 1 条（KMP 产物 + HAP 通过 NAPI 调用）：🟢 编译半段已完成（2026-09-21 实测）**

在**独立 Gradle 构建**（`apps/harmony/spike/`，故意不并进 `apps/android` 的 AGP 9 构建）里实编通过：

```bash
cd apps/harmony/spike && ./gradlew :knspike:linkDebugSharedOhosArm64 :knspike:linkDebugSharedOhosX64
# BUILD SUCCESSFUL
```

| 证据 | 实测 |
|---|---|
| 产物 | `knspike/build/bin/ohosArm64/debugShared/libkn.so`（**ELF 64-bit LSB shared object, ARM aarch64**）与 `ohosX64/.../libkn.so`（x86-64） |
| 导出符号（NAPI 按名字取） | `nm -D` → `T sekb_spike_ping`、`T sekb_spike_echo_len` |
| 头文件 | 同时产出 `libkn_api.h`（HAP 侧 C/NAPI 用） |
| 工具链自动就位 | 构建时自动从该 nexus 拉取 **OHOS sysroot（`sysroot-hms-aarch64-6.0.2.640-02`）** 与 **毕昇 LLVM 19（`llvm-1914-aarch64-macos-dev-10`）** 到仓库内 `.tooling/konan/` |
| 踩到的坑 | ① 空的 `kotlin.native.home=` 会让插件报 `Cannot convert '' to File`（删掉即可）；② `@CName` 需要 `@file:OptIn(kotlin.experimental.ExperimentalNativeApi::class)`；③ sample 的 wrapper 指向 **Gradle 8.14.3（腾讯镜像）**，与它的 Kotlin fork 匹配 |

**仍未做的半段**：把 `libkn.so` 放进 HAP、经 NAPI 调用并**装到设备/模拟器**——按官方文档
"鸿蒙应用必须完成签名才能安装"，这需要 **owner 的华为开发者账号**（DevEco 登录 + 签名配置）。

**spike 第 1 条（原始描述）：🟡 配方已拿全，尚未实际编译。**
从官方 KMP sample（`gitcode.com/CPF-KMP-CMP/kmp-cmp-example` 的 `kmp-example` 分支，
已 clone 到 `.tooling/ohos-spike/sample`）抄到的**关键配方**：

```kotlin
// settings.gradle.kts：所有依赖与插件都来自这一个仓库
maven("https://maven.eazytec-cloud.com/nexus/repository/maven-public/")

// gradle.properties
kotlin.mpp.applyDefaultHierarchyTemplate=false      // ohos 源集要手工接
kotlin.native.cacheKind.ohosArm64=none              // 与我在 iOS 上撞到的 klib cache 同源问题
kotlin.native.cacheKind.ohosX64=none

// 模块
ohosArm64 { binaries { sharedLib { baseName = "kn" }; staticLib { baseName = "kn" } } }
ohosX64   { /* 模拟器/x86_64 用 */ }
// 源集：手工创建 ohosMain，再让 ohosArm64Main / ohosX64Main dependsOn 它（两目标共享导出代码）
```

版本线（sample 实测）：**Kotlin fork `2.2.21-0.3.0-07`**、kotlinx-coroutines `1.10.2-0.3.0-04`、
AGP 8.11.2（sample 用 AGP 8；**我们现有 Android 构建是 AGP 9** → spike 必须用**独立 Gradle 构建**，
否则会把已验证的 Android 基线搅乱）。

**spike 第 3 条（ONNX Runtime 自建）：🟡 找到可用镜像，源码已开始拉取（2026-09-21）**

| 候选源 | 实测 |
|---|---|
| `code.ruyicommunity.cn/xw1216/onnxruntime`（社区 OHOS 移植，带 `docs/ohos/build_deploy_usage.md`） | **HTTP 200** ✅ 已 `git clone --depth 1` |
| `gitcode.com/OpenHarmony-AI-Components/ohos_model_benchmark` | **HTTP 200**（旁证：OHOS AI 组件生态存在） |
| `gitcode.com/openharmony/third_party_onnxruntime` | 404（路径不对） |
| GitHub 官方源码 | HTTP 000（不可达，已排除） |

该移植的构建入口是 `tools/ci_build/build.py --ohos --ohos_arch <arch> --ohos_ndk_root <NDK> ...`。
**本轮（2026-09-21）把编译真正启动起来，并纠正了三处"文档没写、只有撞了才知道"的细节**：

| 坑 | 实际情况 |
|---|---|
| `--ohos_arch` 取值 | **是 `aarch64`，不是 `arm64`**（非法值会打印可选列表：`riscv64/aarch64/armv7/x86_64`） |
| 工具链文件 | 移植版**只带 `cmake/ohos_riscv64.toolchain.cmake`**；arm64 需自己写 →
  已新增 `cmake/ohos_aarch64.toolchain.cmake`（改名以匹配 `cmake/ohos_<arch>.toolchain.cmake` 的查找规则） |
| 主机 Python | 系统 `/usr/bin/python3` 是 **3.9**，而 ORT 构建脚本用了 `match` 语句（需 3.10+）→
  改用 **后端 venv 的 python3.11** |
| 主机 cmake/ninja | 本机**都没装** → 用 Android SDK 自带那份（`~/Library/Android/sdk/cmake/4.1.2/bin`，含 ninja） |

**状态**：编译已在后台启动（`build_dir=build/ohos_aarch64`，`--skip_tests`，
`onnxruntime_BUILD_SHARED_LIB=ON`）。ONNX Runtime 体积大、耗时长，**结果下一轮取**。

**hvigor / ohpm CLI 已确认可用**（HAP 壳工程可以命令行构建）：
`/Applications/DevEco-Studio.app/Contents/tools/hvigor/bin/hvigorw`、`.../tools/ohpm/bin/ohpm`。
→ spike 第 1 条的"HAP 半段"可以本地做出**未签名 HAP 包**；只有**装到设备/模拟器**需要 owner 的
华为开发者账号（官方明确"鸿蒙应用必须完成签名才能安装"）。

**spike 第 4 条（HAP 能装能起）：⛔ 需要 owner 的华为开发者账号**（DevEco 模拟器登录 + 应用签名）。

> 按方案 §4 D2：**四条全通才进 M7**；任一不过就退路线 B（ArkTS 重写 + 契约夹具）。
> 当前判断：第 2 条已过；第 1 条下一轮试（独立构建编 `libkn.so` + 一个最小 HAP 经 NAPI 调用）；
> 第 3/4 条需要外部条件（镜像 / 华为账号）。
