# 验证记录（端侧宿主）

> 规则：**只写跑过的命令与真实输出**。"应该能跑"不算验证；没能验的写在最后一节。

> 端侧单测总量：**221**（功能 187 + 契约夹具 4 + JSON 门面 11 + 格式化 5 + 端侧策略 14）<!-- fact:android_unit_cases=221 -->

环境：macOS（Apple Silicon）+ Android Studio JBR 21 + Android SDK platform 36.1 +
AVD `Medium_Phone_API_36.1`（arm64-v8a，google_apis_playstore）。

---

## 1. 已验（可在任何机器复现）

### 1.0 跨端契约夹具 + DEVICE_ONLY 的「本机」判据（R10，M4 第 1/3 步，2026-09-21）

```bash
bash scripts/android.sh test        # 191 tests, 0 failed（功能 187 + 契约 4；3–12 秒）
```

| 验的是什么 | 证据 |
|---|---|
| **契约夹具在跑，且能失败** | `apps/contract/{routing,signals,privacy}.json` 共 32 case，由 `ContractFixturesTest` 4 个测试逐 `id` 断言；把 `cloud.input_over_budget` 的期望从 `cloud` 改成 `edge` → **立刻 FAILED**，还原 → 回绿 |
| **夹具不是"假绿"** | 夹具目录已登记为单测输入（`apps/android/app/build.gradle.kts` 的 `inputs.dir(contractDir)`）：**实测**未接线时改夹具不会重跑（Gradle UP-TO-DATE），接线后改夹具会红 |
| **R10：DEVICE_ONLY + 非本机端点 = 一个请求都不发** | `PlaneRouterTest`：`10.0.2.2` / `192.168.1.20` / `100.71.24.105` / `edge-host.local` / `0.0.0.0` 全部判为**非本机** → `blockedReason=device_only_requires_local_runtime`；`127.0.0.1` / `localhost` / `[::1]` 判为本机 → 可执行（`device_only_data`） |
| **编排器层面真的不发** | `ChatOrchestratorTest.device only data is not sent at all when edge endpoint is remote`：端侧 0 次调用、云端 0 次调用、`text=""`、`error` 里有可读原因 |
| **普通数据不受影响** | 同一批测试里 `non device-only traffic is unaffected by endpoint locality`：普通数据打远端端点仍是 `edge_preferred`（R10 不误伤端云协同） |

### 1.0.4 端侧策略（L1 热修）校验与应用（M4.5 端侧，2026-09-21）

```bash
bash scripts/android.sh test        # 221 tests, 0 failed（新增 EdgePolicyTest 14 条）
cd apps/android && KONAN_DATA_DIR=$PWD/../../.tooling/konan ./gradlew \
  -PsekbNativeTargets=true :shared:compileKotlinIosSimulatorArm64 :shared:compileKotlinMacosArm64   # BUILD SUCCESSFUL
```

| 验的是什么 | 证据 |
|---|---|
| **跨语言签名一致**（最关键） | 用**服务端 Python 实现**生成签名（`38a26c8c…679d`）硬编码进测试，Kotlin 必须算出同一个——规范化 JSON（键递归排序、无空格、非 ASCII 不转义）与 HMAC 任一处不一致都会红 |
| 公知向量 | HMAC-SHA256(`key`, "The quick brown fox…") = `f7bc83f4…a3cd8`；SHA-256("abc") = `ba7816bf…15ad` |
| 拒绝路径 | 篡改内容 / 换密钥 → `policy_bad_signature`；空间不符 → `policy_space_mismatch:remote=…,local=…`；过期 → `policy_expired`；白名单外键 → `policy_unknown_key:<键名>`；非 JSON → `policy_not_json`（**不抛异常**） |
| 预留字段 | `deviceProfiles` 非空也放行，但**不读取**、不进 `appliedKeys` |
| 灰度 | 确定性（同设备同 salt 稳定命中）、30% 实际比例落在 0.15–0.45、与服务端同式（`sha256(deviceId:salt)`，**不用 Kotlin 内置 hash**） |
| 如实区分 | `retrievalMinScore` 进 `ignoredKeys` + 专用读取口；`promptPacks` 本期后置 |

> 新增端口：`core/Hmac.kt` 的 `hmacSha256Hex` / `sha256Hex`（expect + androidMain `javax.crypto`/`MessageDigest`
> + appleMain `CCHmac`/`CC_SHA256`）。Apple 侧踩过两个 cinterop 坑并记进注释：
> `allocArray<UByteVar>` 的索引读需要额外 import（改用 `ByteArray.usePinned` + 地址传给 C 绕开）；
> `CC_SHA256` 要求**无符号**指针 → `reinterpret<UByteVar>()` 且需显式类型实参。

### 1.0.3 KMP 共享模块（M4 第 5 步，2026-09-21）

```bash
bash scripts/android.sh test        # 207 tests, 0 failed（单测现在测的是 :shared 里的代码）
bash scripts/android.sh assemble    # app-arm64-v8a-debug.apk（45.4 MB）
bash scripts/emulator.sh --background && bash scripts/android.sh install
adb shell am start -n com.sekb.ondevice/.MainActivity --ez selftest true   # PASS=27 FAIL=0
```

| 项 | 结果 |
|---|---|
| 模块划分 | `apps/shared/src/commonMain` = **26 个文件 / 3,395 行纯逻辑**；`apps/android/app` 剩 **14 个文件**（Compose UI、OkHttp、Keystore、SQLite、ONNX、设备工具、装配、自检） |
| 依赖方向 | `:app` → `:shared`（app 的 202 个单测直接测共享层代码，无需复制） |
| 平台泄漏 | `shared` 里没有任何 `android.*` / `java.*` / okhttp / onnxruntime import（这是能编译到 iOS 的前提） |

**踩到并解决的三个环境坑**（记下来，否则下一个人会重踩）：
1. **AGP 9 禁止 `com.android.library` + KMP**（报 "not compatible ... since AGP 9.0"），必须用 `com.android.kotlin.multiplatform.library`；
2. 该插件**没有 `compileSdkMinor`**，而本机只有 `platforms/android-36.1` → 在仓库内 `.tooling/android-sdk` 造了
   `platforms/android-36`（软链 + 改写 `source.properties`/`package.xml`），`sdk.dir` 指向它（`local.properties` 不入库）；系统 SDK 未改动；
3. Kotlin/Native 要写 `~/.konan`（工作区外）→ 加 `KONAN_DATA_DIR=$ROOT/.tooling/konan`。

**native target（iOS/Mac）默认关闭**：`-PsekbNativeTargets=true` 打开——一打开，配置阶段就会准备
Kotlin/Native 工具链（数百 MB），把日常 Android 构建拖成分钟级。

**iOS / macOS 编译已实测通过**（这是 M5 的前置）：

```bash
cd apps/android && KONAN_DATA_DIR=$PWD/../../.tooling/konan ./gradlew \
  -PsekbNativeTargets=true :shared:compileKotlinIosSimulatorArm64 :shared:compileKotlinMacosArm64
# BUILD SUCCESSFUL
```

第一次跑时 iOS 编译器**查出 13 处平台泄漏**（`System.currentTimeMillis` ×6、`Math` ×3、
`Character.getType` ×7、`HashMap.putIfAbsent`、`String.format` ×10、`@Synchronized` ×5）——
这正是"先建 KMP 模块"的价值：平台泄漏不再靠人眼找，编译不过就是不过。修法：
新增 `core/Clock.kt`（`expect fun nowMillis()` + `androidMain`/`appleMain` 两个 actual）、
`core/Fmt.kt`（固定小数位/百分比/定宽对齐，替代 `String.format`）、`Math` → `kotlin.math` +
自写 `floorMod`、`Character.getType` → `CharCategory`、`putIfAbsent` → `containsKey` 判断、
`@Synchronized` → 去掉并写明"单线程契约"（编排器本身就是同步设计）。
`FmtTest` 5 条钉住"与 `%.3f`/`%2d` 等价的输出"，其中一条直接对齐文档里记录的历史报告串。

### 1.0.2 依赖去平台化（M4 第 2 步，2026-09-21）

```bash
bash scripts/android.sh test        # 202 tests, 0 failed（新增 JsonFacadeTest 11 条）
grep -rn "org.json" app/src/main    # 只剩 core/Json.kt（门面本体）
```

| 原先散落的 JVM 依赖 | 处理 | 现在在哪 |
|---|---|---|
| `org.json`（8 个文件直接 import） | 收敛成一个门面（`JsonObject`/`JsonArray`，API 与 org.json 子集同名，调用点只改 import） | `core/Json.kt`（唯一 import org.json 的地方） |
| `okhttp3` | 接口与实现分文件 | `net/HttpTransport.kt`（接口）/ `net/OkHttpTransport.kt`（实现） |
| `java.io.File`（词表读取） | 分词器改为吃**词表文本**；读文件收进端口 | `core/PlatformFiles.kt` + `embed/BertWordPieceTokenizer.kt`（已无 JVM import） |
| `java.util.UUID` | 收进端口 | `core/Ids.kt` |
| `ai.onnxruntime`（JVM API） | **按设计保留**（这就是平台实现） | `embed/OnnxBgeEmbedding.kt` |

> 服务端口径同步（2026-09-21）：`backend/app/core/plane_router.py` 增加了同一套 `is_local_endpoint()`
> 与 `Decision.blocked_reason`，`RoutedLLM.ainvoke` / `astream_with_stats` 在 `is_blocked` 时
> **直接抛错、不发任何请求**；测试见 `backend/tests/unit/test_plane_router.py`（+5 条）。

**结果**：除 4 个端口文件（`OkHttpTransport`/`PlatformFiles`/`Ids`/`OnnxBgeEmbedding`）外，
`main` 代码里**不再出现任何 JVM/platform import**；纯 Kotlin 行数 **3,723 / 5,673 = 66%**（去平台化前 70% 口径含了端口文件，现已更正）。
JSON 门面新增 11 条专测（失败返回 null 而非抛、转义/中文/嵌套往返一致、宽松 vs 严格读法、`intMap` 动态键）。

### 1.0.1 P0 包体瘦身 + 自检 27/27（M4 第 4 步，2026-09-21）

```bash
bash scripts/android.sh assemble     # → app-arm64-v8a-debug.apk
bash scripts/android.sh install && adb shell am start -n com.sekb.ondevice/.MainActivity --ez selftest true
```

| 项 | 改前 | 改后 | 证据 |
|---|---|---|---|
| debug APK 体积 | **101,945,162 B（≈102 MB）** | **41,595,392 B（≈41.6 MB）** | `ls -la app/build/outputs/apk/debug/` |
| APK 内 ABI | 4 个（x86_64/x86/arm64-v8a/armeabi-v7a） | **仅 arm64-v8a** | `unzip -l` → `lib/arm64-v8a/` |
| ONNX Runtime 原生库 | 70.4 MB（4 ABI） | **17.6 MB（1 ABI）** | `unzip -l` 明细 |
| BouncyCastle PQC 参数文件 | ≈**6.7 MB** | **0**（`unzip -l \| grep -c pqc` = 0） | PDFBox 只用文本层，这些文件永不被读 |
| 模拟器自检 | PASS=26 | **PASS=27 FAIL=0 SKIP=1** | 新增 `privacy_device_only_local_gate` 也 PASS |

> 做法：`splits.abi { include("arm64-v8a") }` + `packaging.resources.excludes += "org/bouncycastle/pqc/**"`。
> **未做**：release 开 R8（dex 仍 ~66 MB，是下一个大头）——它需要签名的 release 包 + 一轮 E2E 验证，
> 留到能跑完整验收的轮次再做，避免"体积好看了但运行时崩"。

> 新增自检项实测输出：`[PASS] privacy_device_only_local_gate — edgeBaseUrl=http://10.0.2.2:11434/v1
> 本机=false 端侧调用=0 原因=escalation_blocked:device_only_requires_local_runtime 输出长度=0`。

> ⚠️ **这会改变模拟器上的产品行为**：模拟器的 `edgeBaseUrl` 是 `10.0.2.2`（开发机）→
> **DEVICE_ONLY 请求会被明确拒绝**（不再发给开发机上的 Ollama）。这是隐私边界的正确语义
> （"本机"≠"局域网里的另一台机器"）；真机接入本地运行时后才会有可用的 DEVICE_ONLY 端侧推理。


### 1.1 纯逻辑与端云全流程单测（88 用例）

```bash
export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
./gradlew :app:testDebugUnitTest
```

| 测试类 | 用例 | 覆盖的风险 |
|---|---|---|
| `PlaneRouterTest` | 19 | 预算决策、`device_only` 硬边界、6 类升级信号、**前缀阶段不判 `json_invalid`**、token 估算口径 |
| `StreamGuardTest` | 7 | 攒够阈值才放行、退化前缀改道且**零字符外泄**、短输出退化为整段评估、JSON 角色不被半截 JSON 误杀 |
| `SseParserTest` | 7 | `thinking/token/done/error` 四类事件、`done.meta.execution` 解析、非 JSON 数据不静默丢弃 |
| `ToolCallJsonTest` | 9 | 围栏/单引号/尾随逗号宽容解析、缺工具名与"没有 JSON"分别归类、字符串内花括号 |
| `ToolRegistryTest` | 8 | 三道闸门（未注册/缺参数/未授权）、**越权拦截率**计算、工具异常不崩 |
| `PermissionAuditTest` | 4 | 审计容量裁剪、最近优先、空日志不产生 NaN |
| `ChatOrchestratorTest` | 10 | **端云协同全流程**（无网、无模拟器）：端侧完成 / 退化前缀改道且零字符外泄 / 端点不可用改道 / DEVICE_ONLY 不升级且保留端侧结果 / 工具轮执行 / 越权拦截 / 交接块内容与截断 |
| `EdgeLlmClientTest` | 7 | 请求体约定（`reasoning_effort=none` 且**不带** `think`、JSON 模式）、OpenAI 兼容流式解析、HTTP 错误与网络异常不抛 |
| `SekbApiTest` | 8 | enroll/refresh/heartbeat/上报/聊天的**请求形状**与错误分类（401/403/429）、SSE 执行位置解析 |
| `CredentialCodecTest` | 5 | 凭证编解码往返、坏数据解成 null 而不是崩、轮换阈值随服务端 TTL 变 |
| `ToolCallEvalTest` | 4 | 合法率分母是"尝试次数"而非全部回答 |

**构建期抓到的真缺陷**（单元测试的第一价值就是这些）：

1. **端侧输出预算照抄服务端的 300**，而 `chat` 角色的预期输出是 400 →
   **每一次聊天都被判去云端**，"端侧优先"名存实亡。改为 512（依据：M0 实测 2B
   decode 86–116 tok/s → 512 token ≈ 4.5–6s 最坏，首字延迟由 `maxTtftMs` 与前缀守卫兜住），
   并补回归测试 `default chat role must be able to run on device` 钉住默认值组合。
2. **改道成功后回答被重复输出一遍**：工具轮没走时 `toolRound.text` 就是刚吐过的那段，
   又补发了一次。修法：只有真的做了工具轮才补发（`toolRound.attempted`）。
3. **"端点连不上"被误报成"模型答了空"**：守卫的整段评估先跑，空输出命中 `empty`，
   把真正的失败原因盖掉了。修法：失败判定提到守卫之前。
4. **DEVICE_ONLY 场景下用户看到空回答**：判定该改道却不许改道时，守卫缓冲里的内容被丢弃了。
   修法：不允许改道时把缓冲内容保留并展示——宁可给一段不完美的本机回答，也不能给空白。

### 1.2 APK 构建

```bash
./gradlew :app:assembleDebug
# → app/build/outputs/apk/debug/app-debug.apk
```

### 1.4 验收数字：工具调用 JSON 合法率（约束解码 ON/OFF 对照）

```bash
adb shell am force-stop com.sekb.ondevice
adb shell am start -n com.sekb.ondevice/.MainActivity --ez eval true            # 易档
adb shell am start -n com.sekb.ondevice/.MainActivity --ez eval true --ez hard true  # 难档
adb logcat -d -s SEKB_EVAL:I
```

做法：同一批提示词 × 同一模型 × 同一温度，只切换
`response_format={"type":"json_object"}`（约束解码在 OpenAI 兼容协议里的对应物）；
分母用**尝试次数**（"模型有没有想调工具"）而不是全部回答——否则模型越不敢用工具，这个数字越好看。

| 档位 | 提示词 | 约束解码 | 尝试 | 合法 | 合法率 | 平均延迟 |
|---|---|---|---|---|---|---|
| qwen3.5-2b | 易档（含"只调用工具"） | ON | 10/10 | 10 | **100%** | 187ms |
| qwen3.5-2b | 易档 | OFF | 10/10 | 10 | **100%** | 163ms |
| qwen3.5-4b | 易档 | ON | 10/10 | 10 | **100%** | 319ms |
| qwen3.5-4b | 易档 | OFF | 10/10 | 10 | **100%** | 346ms |
| qwen3.5-2b | 难档（不给"只输出 JSON"指令） | ON | 10/10 | 10 | **100%** | 190ms |
| qwen3.5-2b | 难档 | OFF | 10/10 | 10 | **100%** | 183ms |
| qwen3.5-4b | 难档 | ON | 10/10 | 10 | **100%** | 358ms |
| qwen3.5-4b | 难档 | OFF | 10/10 | 10 | **100%** | 361ms |

**结论（如实说，包括它推翻的东西）**：40 次调用里**没有一次**非法，也没有一次工具名幻觉；
两种模式的延迟差在噪声范围（±20ms，且方向在两个档位间不一致）。
所以**在这批条件下，约束解码没有带来可测量的收益**——原本假设"小模型必须靠 grammar 才能产出合法
工具调用"，实测**不成立**（qwen3.5-2b 在难档下同样是 100%）。据此的建议是：
**先不要为 grammar 付出工程复杂度**，把 `response_format` 留成开关，等换了更弱的模型再验证。

**这个实验的局限（同样要说清楚）**：
- 系统提示里仍然给了完整的 JSON 形状与工具清单——没有测"完全不给 schema"的极端条件；
- 10 条提示词各只跑 **1 次**，没有重复采样，因此**没有方差估计**，100% 也可能只是运气好；
- 工具集只有 3 个且互不冲突，未测"多个相似工具里选错"的情况；
- 全部在**模拟器 + 宿主 Ollama** 上完成（见 §9.1：模拟器不产出性能结论，这里只做功能与协议判定）。

### 1.6 端侧 RAG（M3 第一批，2026-09-18）

单测：`rag.*` / `embed.*` / `tools.KbSearchToolTest` 共 **41 条**（首次构建时抓到一个真缺陷，见下）。

模拟器自检（`--ez selftest true`）新增六项，**PASS=15 FAIL=0 SKIP=1**：

```
rag_ingest          PASS  doc-diet:1块 doc-weather:1块 doc-rag:1块 空间=stub-hash@256
rag_retrieve        PASS  命中=doc-rag 分=0.360 嵌入=1ms 检索=0ms
rag_space_guard     PASS  索引空间=stub-hash@256 与云端一致=false（桩实现应为 false）
rag_space_refuses   PASS  embedding_space_mismatch:索引=stub-hash@256 当前模型=other-model@256
rag_device_only_guard PASS device_only_requires_on_device_embedding:当前嵌入=stub-hash@256 非本机计算
rag_tool_search     PASS  ok=true 输出=[0.348] doc-rag: 端侧 RAG 把知识索引放在设备上…
```

存储：SQLite 持久化（`SqliteVectorStore`）。**跨进程重启验证**（跑前 force-stop，每次都是新进程）：

```
第 1 次：rag_ingest … 启动时已有=0（SQLite 持久化）
第 2 次：rag_ingest … 启动时已有=3（SQLite 持久化）   ← 上一轮的索引真的落盘存活了
rag_sqlite_space_guard  PASS  索引的空间是 stub-hash@256，当前嵌入模型是 other-model@256：
                              换模型必须重建索引（RFC §18.1）
```

**四个"拒绝/约束"路径是重点**（比"能检索"更能说明设计成立）：

| 场景 | 期望行为 | 实测 |
|---|---|---|
| 索引空间 ≠ 当前嵌入模型（换模型没重建索引） | **拒绝检索**，而不是混算余弦 | ✅ 返回 `embedding_space_mismatch` + 空结果 |
| 设备专属集合 + 嵌入非本机 | **拒绝**（宁可答不出来） | ✅ 返回 `device_only_requires_on_device_embedding` |
| 桩实现的空间 ≠ 云端空间 | 本机可检索，但标记**不可与云端融合** | ✅ `cloudCompatible=false` |
| 换嵌入模型后打开旧索引（SQLite） | **打开就失败**并要求重建 | ✅ 抛出并带明确原因 |

### 端侧 ONNX 嵌入（已接入，2026-09-18）

| 项 | 实测 |
|---|---|
| 提供者 | **ONNX `bge-small-zh-v1.5`**（不是桩） |
| 空间 | `BAAI/bge-small-zh-v1.5@512` → `cloudCompatible=**true**`（与云端同空间） |
| 区分度自检 | 两段无关文本余弦 **0.244**（云端参照 0.243864；桩会是 1.000 那种"塌缩"） |
| 检索 | 查询"端侧 RAG 为什么隐私更好" → 命中 **doc-rag 得分 0.696**；嵌入 36ms、检索 <1ms |
| 工具 | `kb_search` 返回 doc-rag 0.642 |
| 隐私闸门 | 同空间但 `isOnDevice=false` → 拒绝，原因 `device_only_requires_on_device_embedding` |
| 自检汇总 | **PASS=20 FAIL=0 SKIP=1** |
| 检索评测（33 条标注集） | Hit@1 **87%**、Hit@3 **100%**、MRR **0.928**、误召回 **0/3**；嵌入 38ms（p95 57ms）、检索 0.5ms —— 见 [`RETRIEVAL-EVAL.md`](RETRIEVAL-EVAL.md) |
| 阈值标定 | 由评测定出 **0.4**（0.2 时 3 条"库里没有"的问题全被强行回答） |

**两个环境约束**（都写进了脚本）：

1. **ONNX Runtime 必须用 1.20.0**：1.30.0 在模拟器上 **SIGILL**（`ILL_ILLOPC`）——
   模拟器 CPU 暴露了 `asimddp/bf16` 但**没有 `i8mm`**，ORT 新版 arm64 内核用到了它。
2. **模型不能 `adb push` 到外部私有目录**：推过去的属主是 `shell`、目录权限
   `drwxrws--- shell:ext_data_rw`，App 不在该组里 → 读不到（表现为"模型在但找不到"）。
   改用 `adb shell run-as <pkg> sh -c 'cat > <绝对路径>'`（整条远程命令必须是一个字符串）。
   日常用 `bash scripts/android.sh push-model` 即可（已封装）。

**踩过的坑（值得单独记）**：主机上"ONNX 与 sentence-transformers 余弦 1.000000"曾被当成
导出成功的证据，其实是**空洞验证**——两边都用了我这台 Mac 缓存里**退化的 `model.safetensors`**。
用 `pytorch_model.bin` 加载才正常（0.243864，与云端 safetensors 完全一致）。
详见 SEKB RFC §18.6；`scripts/fetch_embedding_model.sh --verify` 现在会额外做**区分度检查**。

> 桩（`stub-hash@256`）仍保留：模型不在设备上时自动退回，并在自检里如实标注提供者。

**首次构建抓到的真缺陷**：`DeviceTool.args` 被同时当作"给模型看的 schema"和"必填参数"，
于是 `kb_search` 的可选参数 `top_k` 让**每次调用都变成"缺少参数: top_k"**（工具直接不可用）。
修法：`DeviceTool` 拆出 `requiredArgs`（默认取 `args.keys`，可选参数显式声明）——已补 1 条回归测试。

**两个环境坑**（都写进了脚本/记录）：

1. `scripts/emulator.sh` 原先只等 adb 认到设备，会在"能 adb 但 PackageManager 还没起完"时
   返回假就绪 → `install` 报 `Error: device is still booting`。**已改为同时等 `sys.boot_completed=1`**。
2. 把 `ANDROID_USER_HOME` 搬进仓库会**换掉 debug 签名密钥**，于是模拟器上旧装的 APK 无法覆盖安装
   （`INSTALL_FAILED_UPDATE_INCOMPATIBLE: signatures do not match`）→ 先 `adb uninstall` 一次即可。
   这是一次性代价，之后签名稳定。

### 1.9 int8 量化嵌入（M3 第三批，2026-09-20）

`onnxruntime.quantization.quantize_dynamic`（QInt8）→ **23.9MB（fp32 94.9MB 的 1/4）**。

**自检 E2E（模拟器，int8 生效）PASS=26 FAIL=0**，其中关键两行：

```
rag_provider  PASS  嵌入=ONNX BAAI/bge-small-zh-v1.5-int8@512 空间=…-int8@512 本机计算=true
rag_reembed   PASS  空间从 BAAI/bge-small-zh-v1.5@512 变为 …-int8@512，
                    原地重算 3 块（不丢文本，RFC §4.5-F 的"重算"）
```

**索引空间切换 → 原地重算**这条链路是这轮顺带拿到的端到端验证：换模型不删库、不丢文本。

| 指标（设备实测） | fp32 | int8 |
|---|---|---|
| 体积 | 94.9 MB | **23.9 MB** |
| Hit@1 / Hit@3 / MRR | 26/30、30/30、0.928 | **26/30、30/30、0.928**（排序不变） |
| 误召回 @阈值 0.4 | 0/3 | 1–2/3 ⚠️ |
| 误召回 @阈值 **0.5** | 0/3 | **0/3** |
| 嵌入延迟 | 33–38 ms | **19 ms（≈1.7× 快）** |

**两个必须记住的约束**：

1. **阈值随模型变**：int8 的分数分布整体上移，同一阈值下误召回从 0/3 变 1–2/3；
   默认阈值因此定为 **0.5**（对两个模型都成立）。换模型/换语料都要重跑标定。
2. **int8 的空间戳不同 → 不能与云端向量融合**（`cloudCompatible=false`，自检断言已按
   "是否等于云端空间"判定，而不是"是否 ONNX"）。

**建议**：默认用 int8（体积 1/4、排序不变、更快）；代价是放弃与云端检索结果融合——
而融合尚未实现，所以现阶段没有实际损失（真机阶段若要做混合检索，再评估是否回到 fp32）。

### 1.8 PDF 导入（M3 第三批，2026-09-20）

**自检 E2E（模拟器）PASS=26 FAIL=0**，PDF 三步：

```
rag_pdf_extract  PASS  抽取 78 字符 / 1 页；开头=On-device RAG keeps the index on the device…
rag_import_pdf   PASS  「sekb-sample.pdf」1 段；检索命中=sekb-sample.pdf 分=0.538
rag_pdf_delete   PASS  删除 1 段，剩余切片=3
```

用的是 **PdfBox-Android 2.0.27.0**（Apache-2.0），只抽**文本层**。四种结果各自有可读原因：

| 情况 | 行为 |
|---|---|
| 有文本层 | 抽文本 → 走既有切片/索引链路（抽取文本上限 40 万字符） |
| **无文本层**（扫描件/纯图片页） | 拒绝，提示"多半是扫描件，本期不做 OCR，请先转成带文本的 PDF" |
| **加密** | 拒绝，提示需要密码（并带上库给的细节） |
| 解析失败 | 拒绝，带上失败原因（不是静默变空文本） |

**踩到的坑（值得记）**：抽取时抛的是
`IOException: GlyphList 'com/tom_roush/pdfbox/resources/glyphlist/glyphlist.txt' not found`
——PdfBox 的资源打在 aar 的 assets 里，必须先 `PDFBoxResourceLoader.init(context)`。
而且**没初始化时抛的是 `ExceptionInInitializerError`（Error 而不是 Exception）**，
普通 `try/catch (Exception)` 拦不住，会把整个线程干掉（自检当时没有汇总行就是这个原因）。
两处都改了：App 启动时 `init`，且抽取器与自检包装都改成捕获 **Throwable**。

**范围与边界**：
- 只支持**文本层**；扫描件需要 OCR（不在本期，见 BACKLOG）；
- 单文件上限 **20MB**、抽取文本上限 **40 万字符**（防止一个 PDF 把索引灌爆）；
- PDF 判定看**魔数 `%PDF`** 而不是扩展名（用户从聊天软件存的文件常没有扩展名），
  而且**必须先判 PDF 再判二进制**——PDF 里必然有二进制字节（有专门的回归测试钉住这个顺序）；
- 单测夹具是**手写的最小 PDF**（`sample-text.pdf` / `sample-no-text.pdf`，各 ~700B，
  用 `pypdf` 交叉验证过能读出/读不出文本）；E2E 的 PDF 内容是英文（手写 PDF 不做中文字体嵌入），
  所以检索断言用的是英文提问。

### 1.7 本机文档导入 / 列表 / 删除（M3 第二批，2026-09-20）

**自检 E2E（模拟器 + ONNX 嵌入）PASS=23 FAIL=0**，其中文档三步：

```
rag_import_file      PASS  「sekb-sample.md」1 段/574B 文档数 3→4 空间=BAAI/bge-small-zh-v1.5@512
rag_import_retrieve  PASS  命中=sekb-sample.md 分=0.503   ← 导入的内容真能被检索到
rag_delete_file      PASS  删除 1 段，文档数 4→3（应保留 3），剩余切片=3
```

`rag_delete_file` 那条是**回归验证**：早期 `KnowledgeIndex.remove()` 用"清空整库"实现删除，
删一份文档会把整个索引清掉（其余文档的文本是设备侧唯一副本）。现在存储层有
`deleteBySource`，且单测钉住"删一篇不影响其他"。

UI（截图 `docs/screenshots/m3-rag-docs-panel.png`）：

```
本机文档  导入文件  刷新  已导入 3 份，共 3 段
· doc-rag（1 段，574B）      🗑
· doc-weather（1 段，…)      🗑
· doc-diet（1 段，…)         🗑
```

| 验证到什么程度 | 说明 |
|---|---|
| ✅ 导入按钮**确实唤起系统 SAF 选择器** | 实测点"导入文件"后出现 DocumentsUI 的 "Recent files" 界面 |
| ✅ 导入→入库→检索→删除的**逻辑** | 自检用真实文件跑通（含删除回归） |
| ✅ 面板渲染（列表、段数、文件大小、删除按钮） | dump + 截图 |
| ⚠️ **未验证**：全程脚本化"在选择器里选中文件→看到列表刷新" | adb 推送的文件不在选择器 Recent 列表（需媒体扫描），该 AVD 的 DocumentsUI 根目录抽屉对点击/滑动无响应。人工在模拟器上点两下即可确认（导入成功后计数应 3→4） |

**只支持纯文本**（txt/md/json/csv；≤2MB）：二进制/PDF/Word 会被拒绝并给出可读原因，
不把乱码灌进索引（脏索引比空索引更糟——检索会命中乱码，用户以为"AI 乱答"）。
PDF/Word 解析列为后续（见 BACKLOG）。

### 1.5 UI 人工路径验收（2026-09-18）

自检（§1.3）走的是"编排器直连"，**绕过了界面**；这一节补的是界面本身：真实点击、"设备接入"、
发送、渲染。做法：`adb shell input tap` + `uiautomator dump` 定位控件（不靠猜坐标），
每步 dump 校验状态，最后截图目视确认。

**过程中踩到的两个环境坑**（都会让"点击无效"看起来像 App 的 bug）：

1. **Gboard 窗口盖住下半屏**：点击全打在输入法上 → 按钮没反应。解法：
   `adb shell ime disable com.google.android.inputmethod.latin/com.android.inputmethod.latin.LatinIME`
   （`input text` 不依赖输入法，禁掉后点击才落到 App）。
2. **`input text` 的空格要写 `%s`**：`input text 'What is the answer'` 只会输入 `What`，
   其余被当成 `input` 的子命令。中文也无法用 `input text` 输入。
3. 焦点切换用 `KEYCODE_TAB`（点击密码框在部分状态下不生效）。

**结果**（`docs/screenshots/m2-ui-e2e.png`）：

```
设备：f26c077a59704b52                     ← 界面上完成接入（enroll）
用户气泡：Hello
助手气泡：你好！有什么我可以帮你的吗？
        本机完成 · qwen3.5-4b              ← 执行位置徽标（绿色）
最近决策：edge · edge_preferred
权限审计：调用 0 次，拦截 0 次（越权拦截率 0%）
```

**这一轮 UI 验收抓到两个真缺陷**（都已修）：

1. **密码框明文显示**——截图里密码白纸黑字可见（截图/投屏/旁人一瞥即泄漏）。
   修法：`visualTransformation = PasswordVisualTransformation()`。
2. **徽标漏报"客户端侧升级"**——客户端因 `edge_unavailable` 改道云端时，服务端回传的
   `execution.escalated=0`（它只统计**服务端内部**的升级），于是界面显示成平平无奇的
   "云端完成"，用户不知道自己的问题在端侧失败过。修法：徽标同时看客户端侧结果，
   并补 5 条 `BadgeTest` 钉住文案。

### 1.3 模拟器真机 E2E（14 项全绿，2026-09-18）

环境：macOS Apple Silicon + AVD `Medium_Phone_API_36.1`（arm64-v8a，`-memory 4096`）
+ 宿主机 Ollama（`10.0.2.2:11434`）+ **本地** SEKB 后端（`10.0.2.2:8010`，双平面档）。

```bash
# 1) 起模拟器与本地后端
emulator -avd Medium_Phone_API_36.1 -memory 4096 &
cd <SEKB>/backend && HF_HUB_OFFLINE=1 SEKB_CONFIG_PATH=/tmp/sekb-e2e.yaml \
  .venv/bin/python -m uvicorn app.api.main:app --host 0.0.0.0 --port 8010

# 2) 装机 + 跑自检（**必须先 force-stop**：extras 只在 onCreate 生效）
./gradlew :app:installDebug
adb shell am force-stop com.sekb.ondevice
adb shell am start -n com.sekb.ondevice/.MainActivity --ez selftest true \
  --es sekb "http://10.0.2.2:8010" --es email <账号> --es password <密码>
adb logcat -d -s SEKB_SELFTEST:I
```

实测结果（原样摘录 logcat）：

| 项 | 结果 |
|---|---|
| `edge_reachable` | PASS `http://10.0.2.2:11434/v1` |
| `edge_models` | PASS qwen3.5-2b/4b/9b 等 8 个模型在位 |
| `edge_warmup` | PASS qwen3.5-4b keep_alive=30m |
| `router_decision` | PASS plane=edge reason=edge_preferred tier=default model=qwen3.5-4b |
| `edge_stream` | PASS 39–59 字符真实流式，**预热后 TTFT 73–178ms**（预热前 1780ms） |
| `permission_gate` | PASS 未授权 `READ_CONTACTS` → 拦截 |
| `permissionless_tool` | PASS `device_time` 正常返回 |
| `audit_stats` | PASS 总调用=2 拦截=1 **越权拦截率 50%** |
| `edge_tool_json` | PASS 约束模式产出 `{"tool":"device_time","args":{}}` |
| `cloud_login` | PASS 真实 SEKB 登录 |
| `cloud_enroll` | PASS device_id=… ttl=720h |
| `cloud_chat` | PASS 106 字符流式 + `execution=cloud` + `reason=output_over_edge_budget(800>300)\|stream_no_escalate` + thinking 事件 |
| `cloud_route_event` | PASS 上报幂等键 `selftest-…`，服务端 200 |
| `cloud_stats` | PASS `by_plane={"edge":7,"cloud":28}` `by_role={...,"chat":2,...}` ← **设备上报的事件真的落库了** |

**这套 E2E 抓到两个只有真客户端能暴露的缺陷**：

1. **登录字段名猜错**：客户端按 OAuth 习惯读 `access_token`，而 SEKB 的
   `LoginResponse` 是 `{"user":…,"token":…}` → 服务端 200 OK、客户端却报"登录失败"。
   单测当时喂的是我**以为**的响应体，所以照样全绿（这就是"用假传输测出来的绿"的边界）。
2. **SEKB 流式路由事件缺 `model`**：`execution.model` 为空。修在 SEKB 侧
   （新增 `_resolved_model`，不依赖实例缓存），并补了回归测试。

**环境注意事项**（踩过，写下来省下一次）：

- 本地后端必须 `HF_HUB_OFFLINE=1`：否则 sentence-transformers 会对 huggingface.co
  发 HEAD 探测（本网络不可达），每次调用重试 5×2s，聊天能拖到 10 分钟并触发客户端读超时。
- 本地后端要关掉资讯调度（`news.enabled=false`）：单 worker 下长报告生成会独占 LLM 容量。
- 用 `/tmp` 下的临时配置时要**显式指定 `storage.data_dir`**，否则数据目录跟着配置走，
  已注册的账号会"消失"。
- 自检用 `am force-stop` 后再 `am start`（extras 只在 `onCreate` 生效）。

---

## 2. 待验（需要模拟器 / 联网 / 云端）

这一节是**尚未完成**的部分，不要当成已验：

- [x] ~~模拟器安装并启动 App~~ → §1.3
- [x] ~~端侧链路：App → 宿主机 Ollama 真实流式回答~~ → §1.3（预热后 TTFT 73–178ms）
- [x] ~~端云协同：enroll / SSE / 执行位置 / 上报落库~~ → §1.3
- [ ] **工具调用 JSON 合法率**：约束解码开/关的**同一批提示词对比**（当前只验了"能产出合法 JSON"，
      还没跑成组的合法率对比——需要固定一组提示词 + 各跑 N 次）
- [x] ~~**UI 手工走一遍**~~ → §1.5（并抓到两个真缺陷：密码明文、徽标漏报客户端升级）
- [ ] 断网可用性（飞行模式下端侧链路是否仍可用）
- [ ] 真机性能（decode tok/s、TTFT、内存峰值）——**模拟器测不了**，属 M3

## 3. 怎么验（模拟器联调步骤）

前置：

1. 宿主机起 Ollama 并确认模型就绪：
   ```bash
   ollama list | grep qwen3.5        # 期望看到 qwen3.5-2b/4b/9b
   curl -s http://127.0.0.1:11434/v1/models | head -c 200
   ```
2. 起模拟器（**内存给到 4G**：默认 2048 太小，加载模型会抖）：
   ```bash
   ~/Library/Android/sdk/emulator/emulator -avd Medium_Phone_API_36.1 -memory 4096 &
   adb wait-for-device
   ```
3. 安装并启动：
   ```bash
   ./gradlew :app:installDebug
   adb shell am start -n com.sekb.ondevice/.MainActivity
   ```

> `10.0.2.2` 是模拟器里指向**宿主机**的固定地址（不是 localhost）。
> 真机联调时把 App 里的 edge base url 改成开发机的局域网 IP，
> 并把该 IP 加进 `app/src/main/res/xml/network_security_config.xml` 的明文白名单。

## 4. 已知限制

- **无真机**：所有性能类结论都不在本仓库产出（RFC §9.1 的 D10 决策）。
- 设备凭证存储（Keystore AES-GCM）与网络客户端**只做了 JVM 可验的部分**：
  Keystore 本身、真实 SSE 连接、真实 Ollama 调用都必须在模拟器上验（见 §2）。
- 未做：Compose UI、Android 运行时装配（把上面这些接起来）、约束解码开关的对比实验。
