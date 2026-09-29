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

**一处网络现实**：~~spike 第 3 条要自建 ONNX Runtime，而 ORT 源码在 GitHub（本机不可达）~~ ——
**2026-09-22 已作废**：改为用系统自带的 **MindSpore Lite**（Kotlin/Native OHOS 工具链已预置绑定），
不必碰 GitHub，详见下文"✅ 2026-09-22 转折"。

**结论**：spike 第 1/2 条（KMP 产物 + kotlinx 库在 ohos 上可解析）**可以试**；
第 3 条**改用 MindSpore Lite 后不再受网络影响**（待实测）；
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

**spike 第 3 条（自建推理运行时）：⚠️ 该方向已于 2026-09-22 作废，改为 MindSpore Lite（见下文"✅ 2026-09-22 转折"）。以下保留为过程记录。**

**~~spike 第 3 条（ONNX Runtime 自建）：🟡 找到可用镜像，源码已开始拉取（2026-09-21）~~**

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

**状态（2026-09-21 结论）：❌ 本机无法完成编译——不是参数问题，是外部依赖拿不到。**

实测过程：`--ohos_arch aarch64` 参数修正后启动编译，**25 分钟无任何产物**（`build/ohos_aarch64` 始终 0B，
`.o` 数为 0），进程卡在 `--update` 阶段。查证：

| 证据 | 说明 |
|---|---|
| `ort-ohos/.gitmodules` | 三个子模块的 URL 全指向 **github.com**（`onnx/onnx`、`google/libprotobuf-mutator`、`emscripten-core/emsdk`） |
| `cmake/external/` | 只有 `.cmake` **脚本**，没有拉取下来的源码（abseil / protobuf / onnx 等都要现拉） |
| 网络 | github.com 在本机 **HTTP 000（不可达）**，与 M5 端口③（ORT iOS）是**同一堵墙** |
| 镜像仓库 | 不含任何预编译 `libonnxruntime.so`（只有移植补丁与构建脚本） |

**✅ 2026-09-22 转折：鸿蒙端侧嵌入根本不需要 ONNX Runtime —— 用系统自带的 MindSpore Lite**

上面整段"自建 `libonnxruntime.so`"的努力**方向错了**。Kotlin/Native 的 OHOS 工具链里**已经预置**了
HarmonyOS 的 MindSpore Lite 平台绑定，检查 `$(KONAN_DATA_DIR)` 得到三条实证：

| 证据（均在 `.tooling/konan/`） | 内容 |
|---|---|
| `kotlin-native-prebuilt-…/konan/platformDef/ohos_arm64/MindSpore.def`（`ohos_x64` 同） | `package = platform.MindSporeLiteKit.MindSpore`、`linkerOpts = -lmindspore_lite_ndk`、`headers = mindspore/{status,types,context,data_type,model,format,tensor}.h` |
| `…/klib/platform/ohos_arm64/org.jetbrains.kotlin.native.platform.MindSpore` | **预编译 cinterop klib**，可直接 `import platform.MindSpore.*`，无需自己写 cinterop |
| `dependencies/sysroot-ohos-aarch64-6.0.2.640-04/usr/{include/mindspore,lib/*-ohos/libmindspore_lite_ndk.so}` | 头文件 + 三个 ABI 的链接桩（`arm-linux-ohos` / `aarch64-linux-ohos` / `x86_64-linux-ohos`，各 8.8K） |

- 桩的**符号已确认**：`llvm-nm -D` 列出全部 `OH_AI_*`（`OH_AI_ContextCreate/SetThreadNum`、
  `OH_AI_CreateNNRTDeviceInfoByName`、`OH_AI_Model*`、`OH_AI_Tensor*` …），且所有符号地址相同（`0x2a1c`）
  —— 典型的**导入桩**，**真实现由鸿蒙系统镜像在运行期提供**（与 `libace_napi.z.so` 同理）。
- **好处不只是"绕过 GitHub"**：MindSpore Lite 是 HarmonyOS 的**系统能力**，不像 ORT 那样要往
  HAP 里塞几十 MB 的 `.so`，包体与 §2 插件化议题都更省。代价是要把 bge-small-zh 从 ONNX 转成
  `.ms`（需 `converter_lite` 宿主机工具，另需确认），以及**`ohosX64` 模拟器上系统是否带这个库待实测**。

**由此修订 spike 第 3 条的验收口径**：原写"ONNX Runtime 自建跑通嵌入"。**改为"在鸿蒙上跑通端侧嵌入"**，
运行时用 **MindSpore Lite（系统能力）**；这条改动的理由是工程性的（省包体、免 GitHub、官方支持），
且**不改变对上层共享层的契约**——`apps/shared` 的 `embed/` 只依赖"给文本返回定长归一化向量"，
换运行时只影响向量空间，按 RFC 既有做法**重标阈值**即可。**这是需要 owner 确认的方案微调**（见汇报）。

**第 1 条"装设备"半段与第 4 条仍然需要外部条件**：鸿蒙应用必须签名才能安装，签名要 DevEco 登录华为开发者账号。

**hvigor / ohpm CLI 已确认可用**（HAP 壳工程可以命令行构建）：
`/Applications/DevEco-Studio.app/Contents/tools/hvigor/bin/hvigorw`、`.../tools/ohpm/bin/ohpm`。
→ spike 第 1 条的"HAP 半段"可以本地做出**未签名 HAP 包**；只有**装到设备/模拟器**需要 owner 的
华为开发者账号（官方明确"鸿蒙应用必须完成签名才能安装"）。

**spike 第 4 条（HAP 能装能起）：⛔ 需要 owner 的华为开发者账号**（DevEco 模拟器登录 + 应用签名）。

> 按方案 §4 D2：**四条全通才进 M7**；任一不过就退路线 B（ArkTS 重写 + 契约夹具）。
> 当前判断：第 2 条已过；第 1 条下一轮试（独立构建编 `libkn.so` + 一个最小 HAP 经 NAPI 调用）；
> 第 3/4 条需要外部条件（镜像 / 华为账号）。

---

## ✅ spike 第 1 条（构建 + 链接层面）通过（2026-09-22）

HAP 壳工程落在 `apps/harmony/spike/hapshell/`（从 DevEco 自带模板派生，不是手写 pbxproj 那类冒险做法），
一条命令构建并自检：

```bash
bash scripts/harmony_spike.sh kn    # 编 KMP 产物 libkn.so（ohosArm64 真机 + ohosX64 模拟器）
bash scripts/harmony_spike.sh hap   # 编 HAP + 验证 NAPI↔KMP 引用
bash scripts/harmony_spike.sh all   # 两者
```

**产物**：`entry-default-unsigned.hap` 4.8M（未签名，符合预期）。

**验证口径要说清楚**——本条只证明到"**产物齐备且 NAPI↔KMP 引用成立**"，
"能装能起 + 真的调通"属于第 4 条。脚本对每个 ABI 做三个缺一不可的断言：

| 断言 | 为什么必须 | 实测 |
|---|---|---|
| `libs/<abi>/libknspike.so` 存在 | NAPI 模块真的编出来了 | ✅ arm64-v8a / x86_64 都有 |
| 同包内有 `libs/<abi>/libkn.so` | KMP 产物**真的进了 HAP**，而不是只拷到工程目录 | ✅ 两 ABI 都有 |
| `libknspike.so` 里 `sekb_spike_ping`/`sekb_spike_echo_len` 是 **`U`（undefined）** | 说明它不自己实现，而是**加载期由 libkn.so 解析**——这才叫接上了 | ✅ 两 ABI 各 2/2 |

调用链：ArkTS `import knspike from 'libknspike.so'` → NAPI 模块 `knspike`
→ `entry/src/main/cpp/napi_init.cpp` → `libkn.so` 里的 C 符号。
符号名由 Kotlin 侧 `@CName` 钉死（见 `knspike/.../Spike.kt`），C++ 侧只需声明一致、**不需要 Kotlin 头文件**。
`pages/Index.ets` 把两个数字显示在屏幕上——装上去一眼能分辨"应用起没起"和"调没调到 KMP"。

### 三个「不这么做就跑不起来」的坑（都在 `scripts/harmony_spike.sh` 注释里）

| 坑 | 症状 | 解法 |
|---|---|---|
| hvigor 默认写 `~/.hvigor` | 受限沙箱里直接 `EPERM: operation not permitted, mkdir '~/.hvigor/project_caches/...'` | `HVIGOR_USER_HOME` 指到仓库内 `.tooling/`（与 `scripts/android.sh` 同一思路：**构建状态不落仓库外**） |
| hvigor 会 `npm install -g pnpm` | `npm ERR! code EPERM ... ~/.npm/_cacache` | `HOME` + `npm_config_cache/prefix/userconfig` 一并指到仓库内。**只在脚本内覆盖 HOME**——签名那步要用真实 HOME 找 `~/.ohos` |
| `PackageHap` 需要 JDK | `Unable to locate a Java Runtime`，而且失败发生在 native 编译**成功之后**，极易误判成编译问题 | `JAVA_HOME=$DEVECO/jbr/Contents/Home` |

另外两个实测细节：`EXTERN_C_START/END` **不在** NAPI 头里（`grep` 无匹配），要自己定义；
Kotlin `String` 参数在 C 侧就是 `const char*`（以生成的 `libkn_api.h` 为准，不需要 KString 包装）。

### 第 4 条仍然卡在签名上（附已核实的账号状态）

owner 的华为开发者账号**已登录且实名认证已完成**（DevEco 日志 2026-09-22 16:57:07
`LoginSuccessListener: Whether a developer has completed real-name authentication: true`）。
但**登录 ≠ 有签名材料**：实测本机 `~/.ohos/config/` 不存在、全盘找不到任何 `.p12/.cer/.p7b`。
证书与 Profile 是"按工程"申请的，需要在 DevEco 里对一个工程点一次
`Project Structure → Signing Configs → Automatically generate signature`（凭据全程留在 IDE，不经手 Agent）。

安装目标也不具备：本机**没有任何鸿蒙模拟器镜像**（只有 `tools/emulator` 可执行文件）；
真机可以走 USB + `hdc`。两条路都需要 owner 操作一次。

---

## ✅ spike 第 3 条：可行性成立（编译 + 链接层面，2026-09-22）

```bash
bash scripts/harmony_spike.sh mindspore    # 编 libkn.so + 断言 MindSpore Lite 可用
```

**结论：鸿蒙端侧嵌入根本不需要自建 ONNX Runtime。** Kotlin/Native 的 OHOS 工具链**已预置**
HarmonyOS 的 MindSpore Lite 平台绑定，它是**系统能力**（`libmindspore_lite_ndk.so` 由系统镜像提供），
比往 HAP 里塞几十 MB 的 ONNX Runtime 省得多。

三条断言（每个 OHOS 目标各一遍），都在 `libkn.so` 产物上实测：

| 断言 | 含义 | 实测 |
|---|---|---|
| 导出 `sekb_spike_mindspore_*` | 平台 klib 真的编译链接过了 | ✅ arm64 / x64 |
| 5 个 `OH_AI_*` 为 **undefined（`w`）** | 不自己实现，运行时由系统解析 | ✅ 两目标各 5 个 |
| `DT_NEEDED` 含 `libmindspore_lite_ndk.so` | `.def` 里的 `linkerOpts` 真的生效 | ✅ 两目标 |

### 两个实测细节

- `package = platform.MindSporeLiteKit.MindSpore`（来自工具链 `konan/platformDef/ohos_*/MindSpore.def`）。
  这是**平台库**：**不需要**在 `build.gradle.kts` 里写 `cinterops`，直接 `import` 即可；
  但所有 `OH_AI_*` 都要 `@OptIn(kotlinx.cinterop.ExperimentalForeignApi::class)`（第一次编译就报这个）。
- `OH_AI_ContextDestroy` 吃的是**句柄的地址**（`OH_AI_ContextHandle*`），不是句柄本身——
  要 `memScoped { alloc<COpaquePointerVar>() }` 先放进可寻址存储。`OH_AI_TensorHandleArray`
  是 `{ size_t handle_num; OH_AI_TensorHandle *handle_list; }`。

### 仍未完成（两条，都不是"再写点代码"能解决的）

1. **真跑嵌入需要有设备/模拟器**——只有运行期才验证得了 `OH_AI_*` 到底解析得到、模型跑得动。
   与第 4 条同一个卡点。
2. **模型格式**：头文件里 `OH_AI_MODELTYPE` **只有 `MINDIR`（`.ms`）**，没有 ONNX。
   所以要把 bge-small-zh 从 ONNX 用 `converter_lite` 转一次；而**本机与 DevEco 里都没有这个工具**
   （`find DevEco-Studio.app -iname '*converter*'` 无结果）。
   `mirrors.huaweicloud.com/mindspore/` 可达（HTTP 200），但页面是 JS 门户、没有直链目录，
   下一轮需要另外找 MindSpore Lite 工具包的分发地址。

---

## ⚠️ 模型转换：工具链已打通，但 `.ms` **运行时 build 失败**（2026-09-22，未解决）

上一节说"找不到 converter"，本轮找到了并**全部验证过**；但接着撞到一个更深的问题。

工具链（`bash scripts/model_to_ms.sh fetch|rewrite|convert|check`）：

| 步骤 | 结果 |
|---|---|
| 下载 MindSpore Lite 2.7.0 **Linux-aarch64** 包（含 converter） | ✅ 60.8MB，**sha256 与官方公布值完全一致** |
| 为什么不用 macOS 版 | 官方**没有 macOS 版 converter**（只有 Linux-x86_64 / aarch64 / Windows） |
| 为什么不用 `--platform linux/amd64` | 会去拉不存在的镜像并 403；**aarch64 包与宿主同为 arm64，在本地已有容器里原生执行**（无 Rosetta） |
| 图重写：去掉 converter 不认的 `IsNaN` | ✅ 4 处 `Where(IsNaN(softmax), 0, softmax)`（=`nan_to_num`，本模型里是死代码）被短路；**onnxruntime 证明改写前后逐位相同（最大差 0.000e+00，4 种输入形状）** |
| `converter_lite` | ✅ `CONVERT RESULT SUCCESS:0`，产出 `.ms` 94.8MB，魔数 **`MSL2`** |
| **`benchmark` 实际加载运行** | ❌ **失败** |

**两条失败路径（都试过，失败点不同）**：

| 转换方式 | 结果 |
|---|---|
| 动态 shape（不带 `--inputShape`） | 转换成功，但 `benchmark` 加载时 `FindBackendKernel return nullptr, name: /m/Flatten, type: Flatten` → 典型的"上游动态形状没解析出来、下游算子形状未知、拿不到 kernel" |
| 固定 shape（四步重写后的静态 ONNX） | ✅ **转换成功**：`CONVERT RESULT SUCCESS:0` → `bge-static.ms` **94,808,432 B**、魔数 `MSL2`；但 `benchmark` **0 CPU 挂起**（详见下） |
| ~~固定 shape（未做四步重写时）~~ | ~~转换阶段失败 `Convert to meta graph failed`~~ ← **此行已过时**（那是四步重写完成**之前**的状态） |

**所以"鸿蒙端侧嵌入"的模型依赖目前是未打通的**，不是"差一个下载链接"。三条可选路径（按性价比）：

1. 把 ONNX 里那段**动态掩码展开子图**（`Shape`/`ConstantOfShape`/`Range`/`Gather` 链）改写成静态等价形式，
   再走固定 shape 转换——与本轮已验证的 `IsNaN` 重写是同一思路，但工作量大得多；
2. 放弃 MindSpore Lite，回到**社区版 OHOS ONNX Runtime**（原方案，需找到预编译产物）；
3. 按 RFC §4 D2 退**路线 B（ArkTS 重写 + 契约夹具）**。

**要强调的是**：第 3 条(运行时)与第 4 条本来也要等设备/签名，所以这个模型问题**不是当前唯一的卡点**，
但它决定了 M7 走 MindSpore Lite 还是退路线 B，需要 owner 拍板（见汇报）。




---

## 🎯 真机日操作清单（2026-09-23 已就绪：装包即出结果）

设备调试顺延到次日，所以把"真机日要做的事"提前做完了——**现在只需要装包、看屏幕**，
不需要现场写代码或查 API。

```bash
bash scripts/harmony_spike.sh hap    # 一条命令：编 KMP → 编 HAP（含 .ms rawfile）→ 自检断言
# 随后用 hdc 装到手机（签名材料就位后）
```

**已接好的链路**（每一环都有产物级证据）：

```
ArkTS (pages/Index.ets)
  └─ NAPI 方法 msSelfTest / msFirstFloat / ping / echoLen     ← libknspike.so
       └─ 4 个 @CName C 符号（undefined，加载期由同包 libkn.so 解析）  ← libkn.so
            └─ MindSpore Lite C API（16 个 OH_AI_* undefined）        ← 系统 libmindspore_lite_ndk.so
```

**模型是随包分发的**：`scripts/harmony_spike.sh hap` 会把 `.ms`（94.8MB）放进
`entry/src/main/resources/rawfile/`，App 首次启动复制到私有目录再交给 `OH_AI_ModelBuildFromFile`
（那个 API 要的是**文件系统路径**，rawfile 不是路径）。**因此真机日不需要手工 push**，
也不会踩 `/data/local/tmp` 之类的权限坑。代价是 HAP 涨到 **96MB**（spike 阶段可接受）。

### 屏幕上三行结果怎么读

| 界面项 | 期望 | 不是这个值说明什么 |
|---|---|---|
| `sekb_spike_ping() = 42` | **42** | 链路没通（NAPI/KMP/加载任一处）。**这一项与模型无关**，先把它弄绿 |
| `输出元素数=262144` | **262144** = 1×512×512 | 负数是**失败步骤码**，按值定位：`-1` context 空、`-2` model 空、`-3` build 失败（`.ms` 不被接受/路径不对）、`-4` 没有输入、`-5` 输入不是 int64（拿错模型）、`-6` predict 失败（算子跑不动）、`-7` 没有输出、`-8` 输出太小 |
| `\|v[0]\|×1e6 = <非零>` | 非零 | 为 0 说明"形状对但**数据没流过来**"——全零也能通过上一项，所以这一项是必要的第二判据 |

> 这两项一起才能说"**`.ms` 在设备上真的能加载并算出东西**"，也就是 spike 第 3 条的运行期验收。
> 它同时决定 M7 的路线：**能跑 → MindSpore Lite；不能跑 → 立刻转路线③（ArkTS + 契约夹具）**。

### 还没做的（等上面绿了再做，避免白做）

`src/ohosMain/kotlin-pending/MindSporeBgeEmbedding.kt`（**不参与编译**）是完整的
`EmbeddingProvider` 实现（CLS pooling + L2 归一化 + 空间戳，与 Android/iOS 逐点对齐）。
它没接进来的原因很具体：spike 的 Gradle 构建是**独立**的，**不含 `:shared`**，
要接就得先给 `ohosMain` 补共享层的 `actual` 端口（`Clock`/`Hmac`/`Digests`）——
那是 M7 的活。**顺序上应该先确认模型能跑**（上面的清单），再花这个成本；
否则一旦转路线③，这份 Kotlin 就白写了。

---

## 模拟器 / Preview 路径的实际可达性（2026-09-25 盘点）

> 背景：owner 于 2026-09-25 把鸿蒙验收前提从"必须 NEXT 真机"改为
> **"用 DevEco 模拟器或 Preview 模式调试功能和 UI"**。本节是动手前的**实际盘点**，
> 只写查到的路径与版本，不写"应该可以"。

### 一、有什么 / 缺什么（实测）

| 组件 | 状态 | 证据（路径 / 版本） |
|---|---|---|
| DevEco Studio | ✅ 已装 | `/Applications/DevEco-Studio.app` |
| 鸿蒙 SDK | ✅ **内置在 App 里** | `/Applications/DevEco-Studio.app/Contents/sdk/default`（所以 `~/Library/Huawei/Sdk` 近乎空也能构建 HAP） |
| hvigor / ohpm / node CLI | ✅ | `Contents/tools/hvigor/bin/hvigorw`、`tools/ohpm/bin/ohpm`、`tools/node` |
| JBR (JDK) | ✅ | `Contents/jbr` |
| **模拟器引擎** | ✅ 已部署 | `tools/emulator/`，`~/.Huawei/Emulator/deployed/versionInfo.txt` = `6.0.2.642`；`Emulator -version` → `HarmonyOS Emulator :6.0.2.210` |
| **Previewer** | ✅ 存在且**可执行** | `sdk/default/openharmony/previewer/common/bin/Previewer`（Mach-O 64-bit executable **arm64**） |
| **模拟器系统镜像** | ❌ **没有** | 全盘无 >100MB 的镜像文件；`~/.Huawei/Emulator/deployed/` 只有 `versionInfo.txt` |
| 模拟器实例清单 | ❌ 没有 | `Emulator -list` → `can not open file : "$USER_HOME$/.Huawei/Emulator/deployed//lists.json"`（`$USER_HOME$` **占位符未被替换**，说明该文件由 IDE 生成） |
| 签名材料 | ❌ 没有 | 无 `~/.ohos/config/`、无 `.p12/.cer/.p7b` |

### 二、⚠️ 一个必须先说清的技术区分：**Preview 验 UI，验不了"功能"**

这不是取舍，是硬限制：

- `Previewer` 是 **macOS arm64 原生程序**，它在 macOS 上直接跑 ArkUI/ArkTS；
- 而我们的 NAPI 桥 `libkn.so`（KMP 产物 + `libmindspore_lite_ndk` 依赖）是 **ohosArm64** 目标；
- 因此 **Preview 模式下 `libkn.so` 加载不了** → NAPI↔KMP 调用、`.ms` 模型推理、hdc/设备能力
  这些"功能"在 Preview 里**无法验证**。

所以 owner 的要求需要拆成两半：

| 目标 | 可行路径 | 前置条件 |
|---|---|---|
| **UI**（ArkUI 布局/交互/深浅色） | **Previewer**（macOS 本地，不需要镜像） | 需要先产出 preview 构建产物（见下，**尚未打通**） |
| **功能**（NAPI↔KMP、`.ms` 运行期、M6 第 4 条"能装能起"） | **模拟器**（真 OHOS，能跑 `.so`） | **需要系统镜像**——而镜像要在 DevEco 的 Device Manager 里下载，**需要 owner 的华为账号登录** |

### 三、Previewer 的 CLI 事实（实测）

- 它**能启动并自报参数需求**（`RichPreviewer`）：

  ```
  [ERROR][CommandParser.cpp][IsAppPathValid][335]: Launch -j parameters abnormal!
  [ERROR][CommandParser.cpp][IsCommandValid][145]: No app path specified.
  ```
  即 `-j <app 路径>` 是必须的（其余参数需逐个试出）；它会尝试连本地 socket 收 trace，
  连不上只报 `TraceTool::pipe connect failed`，**不致命**（进程继续）。
- 模拟器 CLI 是完整的：`Emulator [-hvd <name> -path <path> -imageRoot <path>] [-list] [-stop <name>] [-hdcport <port>]`。
  也就是说**一旦有了镜像，模拟器可以脱离 IDE 用命令行起**。

### 四、Preview 构建任务存在，但**在 CLI 下构建失败**（已定位到具体报错）

任务确实注册了（`taskTree` 可见完整链）：

```
:entry:PreviewBuild → :default@PreviewArkTS → … → :default@PreviewHookCompileResource
```

调用形式与 IDE 一致：

```bash
hvigorw --mode module -p module=entry@default -p product=default \
        -p requiredDeviceType=phone PreviewBuild --no-daemon
```

**结果：715ms 内失败**，且失败点在 ArkTS 预览编译：

```
ERROR: Failed :entry:default@PreviewArkTS...
ERROR: The "data" argument must be of type string or an instance of Buffer, TypedArray, or DataView. Received undefined
```

即 hvigor 的预览任务在读一个**未定义**的输入——也就是 IDE 平时会额外传入、而命令行没传的东西
（`hvigor-ohos-plugin/src/tasks/hook/previewer/preview-build.js` 里没有显式的 `previewerParam` 之类的键，
说明它来自 IDE 侧注入的上下文）。

> **两条自我更正（都值得记）**：
> 1. 第一次跑时我以"10 分钟无输出"判定"卡住"并终止——**是错的**，真正原因是失败得太快而我用
>    `cmd | tail -40` 把输出**缓冲**住了（管道到命令结束才吐字节）。**长任务不要管道给 `tail`**，
>    要写文件再 `tail`。
> 2. 修正后复跑，失败是**确定且可复现**的（715ms），这才是有用的信息。

### 五、结论与下一步：**优先走模拟器，而不是继续钻 Preview CLI**

| 路径 | 能验什么 | 卡在谁身上 |
|---|---|---|
| **模拟器** | **UI + 功能**（真 OHOS，能跑 `libkn.so` 与 `.ms`） | **只需要 owner 下载镜像**（DevEco Device Manager + 华为账号），CLI 已完备（`Emulator -hvd/-path/-imageRoot`） |
| Previewer（CLI） | **仅 UI**（`libkn.so` 是 ohosArm64，Preview 里加载不了） | 需要 DevEco 插件内部那个未公开的预览上下文，**投入产出比差** |

**所以建议：owner 花几分钟下载模拟器镜像 + 点一次自动签名，比继续逆推 Preview 的 CLI 参数划算得多**
——前者一次动作同时解锁"UI 与功能"，后者即使打通也验不了功能。

**不需要 owner 的部分**（下次可做）：
1. 若仍想试 Preview，用 `--debug` 抓 `PreviewArkTS` 前的完整参数，看 IDE 注入项的键名；
2. 其余时间投入 Android 侧仍未验的项（重复采样求方差、release R8）。

**需要 owner**（做完这两步，M6 第 3 条运行期与第 4 条才有路）：
3. DevEco 登录华为账号 → Device Manager 下载 **HarmonyOS 模拟器镜像**（约数 GB，会生成 `lists.json`/实例）；
4. Device Manager 里点一次**自动生成签名**（`~/.ohos/config/` 目前为空）。
### 五、下一步（按"是否需要 owner"分）

**不需要 owner**：
1. 后台重跑 `PreviewBuild` 并给足时间，确认它产出 preview 产物（若真卡住再用 `--debug` 定位）；
2. 打通后按 Previewer 参数起一次预览，截图核对 ArkUI 页面（这是 **M7 UI 的可验证部分**）。

**需要 owner**（做完这两步，功能侧才有路）：
3. 在 DevEco 登录华为账号 → Device Manager 下载 **HarmonyOS 模拟器镜像**（约数 GB），
   生成 `lists.json`/实例；
4. Device Manager 里点一次**自动生成签名**（`~/.ohos/config/` 目前为空）。

> 完成 3/4 后，M6 第 3 条运行期（`.ms` 能否加载）与第 4 条（HAP 能装能起）就能在模拟器上验，
> 从而决定 M7 走 MindSpore Lite 还是转路线③（ArkTS + 37 条契约夹具）。
### 五、下一步（按"是否需要 owner"分）

**不需要 owner**：
1. 诊断 `PreviewBuild`：确认任务名与必要参数（`hvigorw --debug`、或查 `entry/hvigorfile.ts` 注册的任务）；
2. 打通后按 Previewer 参数起一次预览，截图核对 ArkUI 页面（这是 **M7 UI 的可验证部分**）。

**需要 owner**（做完这两步，功能侧才有路）：
3. 在 DevEco 登录华为账号 → Device Manager 下载 **HarmonyOS 模拟器镜像**（约数 GB），
   生成 `lists.json`/实例；
4. Device Manager 里点一次**自动生成签名**（`~/.ohos/config/` 目前为空）。

> 完成 3/4 后，M6 第 3 条运行期（`.ms` 能否加载）与第 4 条（HAP 能装能起）就能在模拟器上验，
> 从而决定 M7 走 MindSpore Lite 还是转路线③（ArkTS + 37 条契约夹具）。

---

## 原生层补齐 float32 输入能力（2026-09-25，M6 第 4 条的前置）

### 补的是一个**能力缺口**，不是修 bug

模型侧打通后（见 `scripts/model_to_ms.sh` 头部：转换/等价/运行三项齐备，
additive mask 由宿主喂入）我去看原生层能不能真的驱动它——**不能**：

`MsLite.kt` 原本只有 `predict(feeds: Map<String, LongArray>)`，**只能喂 int64**。
而 `sekb_additive_mask_zero` 是 **float32**。也就是说：**即使 `.ms` 已经能跑，
原生层也喂不进那个输入**，M6 第 4 条到不了。

### 改动（**纯新增，不动既有已验证代码**）

- 新增 `Feed`（`I64` / `F32`）与 `predictTyped(feeds: Map<String, Feed>)`；
- 既有的 `predict(Map<String, LongArray>)` **一行未改**——它已经过验证，没必要冒回归风险；
- `predictTyped` 逐个输入**显式校验元素数与声明类型**，不匹配就早报。

**为什么强调显式校验**：`OH_AI_TensorSetData` 返回 void、**没有状态可查**，
类型/长度对不上**不会报错**，只会表现为"向量不对"——这是最难查的一类问题
（该类的注释里原本就写着这条教训，新代码把它执行得更彻底）。

### 验证（按仓库口径：只到 build+link）

```
bash scripts/harmony_spike.sh kn    → libkn.so arm64-v8a 2.5M / x86_64 2.0M
bash scripts/harmony_spike.sh hap   → entry-default-unsigned.hap 96M
                                    → ✅ arm64-v8a: libknspike.so 引用 4 个 KMP 入口
                                    → ✅ x86_64:    同上
                                    → ✅ 模型 rawfile 在包内
```

⚠️ **口径**：以上只证明"产物齐备且 NAPI↔KMP 引用成立"。
**"能装能起 + 真的调通"属于 spike 第 4 条**，需要签名 HAP + 设备/模拟器——**仍未做**。

### M6 第 4 条还差什么（明确清单）

1. 把 HAP 里的模型 rawfile 换成**可运行**的 `bge-maskinput.ms`（当前包内是那个会挂起的静态版）；
2. 调用方（`MindSporeProbe` / 待接的 `MindSporeBgeEmbedding`）计算 additive mask
   —— 可见位 `0`、被掩位 `-inf`、形状 `[1,1,512,512]`（实测四层相同，单个输入即可）
   —— 并用 `predictTyped` 传入；
3. 在**模拟器/设备**上验证（需 DevEco 模拟器镜像）。

---

## 宿主侧接线完成 + HAP 改打可运行模型（2026-09-25）

### 改动

1. **`MindSporeProbe.kt`**：新增 `additiveMask(mask)`（可见位 `0`、被掩位 `-inf`、形状 `[1,1,seq,seq]`），
   两处 `predict` 调用改为 `predictTyped`，输入集合由
   `{input_ids, attention_mask, token_type_ids}` 改为
   **`{input_ids, token_type_ids, sekb_additive_mask_zero}`**
   —— 因为新模型里 `attention_mask` 已被裁掉（它在图内的唯一作用就是展开成 additive mask）。
   ⚠️ 只按 **key** 掩：若把 query 行也掩成全 -inf，softmax 会出现 nan，而原模型就是只掩 key。
2. **`scripts/harmony_spike.sh`**：`build_hap` 由打包 `bge-static.ms`（图内仍有动态掩码链 → 0 CPU 挂起）
   改为打包 **`bge-maskinput.ms`（可运行版）**。

### 验证（仅 build+link）

```
bash scripts/harmony_spike.sh all
  ✅ libkn.so arm64-v8a 2.5M / x86_64 2.0M
  ✅ HAP 96M；arm64-v8a 与 x86_64 的 libknspike.so 各引用 4 个 KMP 入口
```

**模型确实在包内**（直接查 HAP 内容）：
```
94802472  resources/rawfile/bge-small-fixed512.ms     ← 大小 = bge-maskinput.ms（可运行版）
```

### ⚠️ 同时发现一处**验证脚本误报**（如实记录，未修）

同一次运行里，`verify_link` 的模型检查却打印了
"⚠️ 包里没有模型 rawfile"。**这是误报**——上面 `unzip -l` 的直接证据表明它在包内、
且大小正是可运行版。该检查用 `unzip -l "$hap" | grep -q 'rawfile/bge-small-fixed512.ms'`，
按内容本应命中，**根因尚未定位**（可能是该代码路径的时序/`unzip` 可用性问题）。
**未修**，以免在没搞清原因前改检查逻辑而掩盖真实问题；已在此标注，避免下次被这句警告误导。

---

## 修掉 `verify_link` 的模型检查误报（2026-09-25）

### 现象回顾

上一轮 `bash scripts/harmony_spike.sh all` 同一次运行里：
链接检查两条 ✅，模型检查却打印 "⚠️ 包里没有模型 rawfile"。
而**磁盘上唯一的 HAP 明明含该文件**（`unzip -l` 显示 94802472 B = 可运行版）。
一个会骗人的检查比没有检查更糟——它会让人去查一个不存在的缺陷。

### 修法（把"不可解释"变成"可诊断"）

1. **确定性地选产物**：原来 `hap="$(find … -name '*.hap' | head -1)"`——
   `find` 的输出顺序**不保证**，陈旧/中间产物都可能被选中。
   改为：优先 `outputs/` 下的产物，同目录内按**修改时间取最新**（无 outputs 时再全局取最新）。
2. **失败时自诊断**：列出生效的产物路径，并列出工程内**所有** `.hap` 及其是否带模型。
   ——上次那条警告信息量不足以定位，报错信息必须够定位，否则等于没报。

### 结果

重跑后三项全过：

```
HAP: …/outputs/default/entry-default-unsigned.hap（96M）
   ✅ arm64-v8a / x86_64：各引用 4 个 KMP 入口
   ✅ 模型 rawfile 在包内
```

### ⚠️ 根因的判断与其证据强度（不夸大）

**最可能的解释**：检查执行时工程内存在**两个** HAP（一个陈旧中间产物 + 最终产物），
`find | head -1` 选中了陈旧那个；随后 hvigor 清理，现在只剩一个。
——这与"现在确定性选择即可通过、而当时在唯一产物上无从复现"是一致的。

但我**没有取到当时的产物清单**（那一刻的证据已被覆盖），所以这仍是**推断而非证实**。
能确定的是：**原来的选产物方式不可靠，且现在已确定性与可诊断**。
按"结论要有证据"的标准，这里我把"已修好"与"根因已证实"分开说，不混为一谈。

---

## M7 UI 骨架（ArkUI 聊天页，2026-09-25）

### 做了什么

新增 `entry/src/main/ets/pages/Chat.ets`（≈190 行）并注册进 `main_pages.json`：
顶栏（标题 + 端侧状态圆点）、消息区（用户气泡右/主色、助手气泡左/白卡）、
**执行位置徽章**（本机完成=绿 / 云端完成=金）、空状态三个示例问题、底部输入栏。
视觉语言与网页端（Ant Design）和 Android 端一致：主色 `#1677FF`、浅灰底 `#F5F7FA`、白卡片、圆角。

### 边界（必须说清，否则容易被误读为"M7 做完了"）

- **只做界面**：消息是页面本地状态，**尚未接 NAPI**。
  真正的推理链路等 M6 第 4 条在模拟器/设备上跑通后再接——**不把未验证的运行时假设写进 UI**。
- 代码里那条回答**明确写成占位文本**（"此处将显示端侧模型的回答…"），
  **不是推理结果**，就是为了避免有人把它当成"已经跑通"。
- **为什么不违反 RFC 的 M6→M7 顺序**：界面与"用哪个推理运行时"无关
  （`apps/shared` 的 `embed/` 只暴露"给文本返回定长向量"的契约），
  所以先写 UI 不会因运行时选型变化而白做。

### 验证级别（不许含糊）

- ✅ **ArkTS 编译通过 + HAP 打包成功**（`bash scripts/harmony_spike.sh hap`，三项链接检查同时全过）。
- ✅ **确认新页面真的进了包**（而不是被增量构建跳过——日志里有多条 `UP-TO-DATE`，所以专门查了）：
  包内 `main_pages.json` = `["pages/Index","pages/Chat"]`；`modules.abc` 中 `pages/Chat` 出现 **64 处**（`pages/Index` 34 处）。
- ❌ **没有任何视觉验证**：没在 Previewer / 模拟器 / 设备上看过一眼，
  **布局与交互都还没被眼睛验过**。这正是 owner 把验收环境改为"模拟器 / Preview"之后要补的那一步。
  在此之前，本页只能说"能编译、能打包"，**不能说"界面能用"**。

---

## M7：模型层与 NAPI 边界契约（2026-09-25）

### 做了什么

把业务语义**从视图里抽出来**，并显式定义"界面 ↔ 端侧推理"的边界：

| 文件 | 内容 |
|---|---|
| `ets/model/Types.ets` | `Plane` / `ChatMessage`；`badgeOf()`（**客户端改道**也要算"端侧不达标"）、`badgeColor()`、`explainReason()`（**认不出的码原样透出**，绝不返回"未知原因"） |
| `ets/model/EdgeAgent.ets` | `EdgeAgent` 接口（`embed` / `ask`）+ **`UnwiredEdgeAgent` 桩**（明确抛错）+ `currentAgent()` |
| `ets/pages/Chat.ets` | 改为消费上述模块；发送走 `currentAgent().ask()` |

**为什么先定契约**：真实链路要等 M6 第 4 条在模拟器/设备上验证通过。在运行时未验证前就把调用写进界面，
等于**把未验证的假设固化到 UI**；定契约后，接线只需替换实现（`UnwiredEdgeAgent` → NAPI 实现），**界面一行不改**。

**桩必须明确失败**：回一个"看起来像回答"的假结果是最坏的选择——它会让人以为链路通了。
界面把桩抛出的错误**如实显示成一条回答**（带 `edge_unavailable` 原因码）。

### 验证级别

- ✅ ArkTS 编译 + HAP 打包成功；三项链接检查同时全过。
- ✅ **新模块确实编进包**：`modules.abc` 中 `pages/Chat` 出现 **67** 处；
  且 `edge_preferred`、`edge_unavailable` 各 1 处——**这两个串只存在于 `Types.ets`**，
  它们出现即证明新模块被编译并打包。
- ⚠️ **一处测量方法的局限（如实记录）**：中文串（如 `端侧推理尚未接线`、`已上云（端侧不达标）`）
  用 `strings` 或 `grep -a` 按 UTF-8 查均为 **0**——说明 **ABC 串表并不以明文 UTF-8 存中文**。
  这是**检查手段的局限，不是"没编译进去"**；ASCII 串可查、中文串不可查，不能把两者混为一谈。
- ❌ **仍无任何视觉验证**（未在 Previewer/模拟器/设备上看过）：布局与交互尚未被眼睛验过。
