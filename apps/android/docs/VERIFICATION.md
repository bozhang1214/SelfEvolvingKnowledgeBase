# 验证记录（端侧宿主）

> 规则：**只写跑过的命令与真实输出**。"应该能跑"不算验证；没能验的写在最后一节。

> 端侧单测总量：**231**（功能 187 + 契约夹具 4 + JSON 门面 11 + 格式化 5 + 端侧策略 24）<!-- fact:android_unit_cases=231 -->
>
> 模拟器自检：**PASS=30 FAIL=0 SKIP=2**（两个 SKIP = `cloud_login` 与 `policy_refresh`，都缺前置条件）

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
| **契约夹具在跑，且能失败** | `apps/contract/{routing,signals,privacy}.json` 共 **37** case（routing 10 + signals 14 + privacy 13，按各文件 `cases` 数组实点），由 `ContractFixturesTest` 4 个测试逐 `id` 断言；把 `cloud.input_over_budget` 的期望从 `cloud` 改成 `edge` → **立刻 FAILED**，还原 → 回绿 |
| **夹具不是"假绿"** | 夹具目录已登记为单测输入（`apps/android/app/build.gradle.kts` 的 `inputs.dir(contractDir)`）：**实测**未接线时改夹具不会重跑（Gradle UP-TO-DATE），接线后改夹具会红 |
| **R10：DEVICE_ONLY + 非本机端点 = 一个请求都不发** | `PlaneRouterTest`：`10.0.2.2` / `192.168.1.20` / `100.71.24.105` / `edge-host.local` / `0.0.0.0` 全部判为**非本机** → `blockedReason=device_only_requires_local_runtime`；`127.0.0.1` / `localhost` / `[::1]` 判为本机 → 可执行（`device_only_data`） |
| **编排器层面真的不发** | `ChatOrchestratorTest.device only data is not sent at all when edge endpoint is remote`：端侧 0 次调用、云端 0 次调用、`text=""`、`error` 里有可读原因 |
| **普通数据不受影响** | 同一批测试里 `non device-only traffic is unaffected by endpoint locality`：普通数据打远端端点仍是 `edge_preferred`（R10 不误伤端云协同） |

### 1.0.6 L1 阈值生效与回滚（运行时证据，2026-09-21）

```bash
# 1) 生成一份**已签名**策略（空间戳要与端侧 config 一致）
python3 - <<'PY' > /tmp/policy.json   # 内容见下方"实测输出"
# 2) 推进 app 私有目录（与 push-sample 同一套路；intent 传 JSON 不可靠，实测被 am 重新解析成 URI）
bash scripts/android.sh push-policy /tmp/policy.json
# 3) 跑自检
adb shell am force-stop com.sekb.ondevice
adb shell am start -n com.sekb.ondevice/.MainActivity --ez selftest true
```

实测输出（模拟器）：

```
[PASS] policy_apply_rebuild — 应用=777 阈值 0.5 → 0.7（期望 0.7）重建=true（阈值有变化=true）
[PASS] policy_threshold_wired — 检索器阈值=0.7（策略=0.7）
[PASS] policy_rollback_restores_threshold — 先应用到 0.9，回滚后阈值=0.7（期望 0.7）
[PASS] rag_retrieve — 命中=doc-rag 分=0.690 嵌入=14ms 检索=3ms
自检汇总：PASS=30 FAIL=0 SKIP=2
```

这条证据回答的是"**改阈值不发版即生效**"：策略里的 `retrievalMinScore=0.7` 应用后，
`container.retriever.minScore` 真的从 0.5 变成 0.7（**并且是新实例**——`KbSearchTool` 抓住旧实例，
所以 retriever 与 tools 必须一起重建）。

**过程中暴露并修掉的两个真问题**：
1. **自检里的注入策略污染了后续检查**：策略块原本在 RAG 项之前，阈值被抬到 0.7 后
   `rag_retrieve`/`rag_tool_search`（分数 0.645/0.690）全部失败——**测量工具不能在测量前改变被测对象**。
   修法：策略检查挪到自检**最后**，且结束后 `debugResetPolicy()` 清理（清存储 + 按默认 0.5 重建）。
2. **`edge_reachable=false` 会中断整个自检**（早期 `return items`）：宿主 Ollama 没起时只剩一条结果，
   连"本地 RAG 还好不好"都答不了。改为**非致命**：记录 FAIL 但继续跑本地项。
3. 顺带：intent 传 JSON 被 `am` 重新解析（`dat=issuedAt:`）→ 改为**文件注入**（`push-policy`）。

### 1.0.5 端侧策略接线（M4.5 收尾，2026-09-21）

```bash
bash scripts/android.sh test        # 231 tests, 0 failed（新增 PolicyFetcherTest 10 条）
```

| 接线项 | 实现 | 验证 |
|---|---|---|
| 双槽持久化 | `device/SharedPrefsPolicyStore.kt`（与设备凭证同一个 `sekb_device` prefs；策略非机密，明文即可） | 单测（`InMemoryPolicyStore` 双槽语义 + 回滚） |
| 拉取与应用 | `AppContainer.refreshPolicy()`：拉 → 验签 → 应用 → 落盘；**失败不改状态**只写审计 | `PolicyFetcherTest` 10 条（HTTP 错误 / 网络异常 / 签名不符 / 灰度跳过 / 第二次保存进 previous / 回滚 / 审计不含正文） |
| 拉取时机 | `SekbApp.onCreate` → `startPolicyRefreshLoop()`：启动**异步**拉一次 + 每 6h（守护线程，失败只记日志） | 编译期接线；行为由 fetcher 单测覆盖 |
| 阈值进检索 | `container.retriever` 的 `minScore` 取自落盘策略（`retrievalMinScore`），没有则 0.5 | `activeRetrievalMinScore()` 单测 |
| 自检项 | `policy_refresh`（无设备凭证 → SKIP）、`policy_rollback_guard`（无上一份时必须干净跳过） | ⏳ **本轮未能 E2E**（见下） |

**✅ E2E 已跑通（2026-09-21 复跑）**：

```
[PASS] edge_reachable — http://10.0.2.2:11434/v1 → true
[SKIP] policy_refresh — 无设备凭证（先登录换设备 token）→ 跳过
[PASS] policy_rollback_guard — 无上一份时回滚必须干净跳过（不抛、不改状态）
自检汇总：PASS=28 FAIL=0 SKIP=2
```

**首次失败的原因与最终修法**：第一次跑时自检第 1 项 `edge_reachable=false`——宿主 Ollama 默认只绑
`127.0.0.1:11434`。现在 `scripts/emulator.sh` 起好模拟器后会自动 `adb reverse tcp:11434 tcp:11434`
（配合 `-PsekbEdgeUrl=http://127.0.0.1:11434/v1` 或 `SEKB_EDGE_URL=` 构建），
**不必**把 Ollama 绑到 `0.0.0.0` 暴露到局域网；复跑时 `10.0.2.2` 亦可直连（两种路径都留着）。
`policy_refresh` 之所以 SKIP：策略端点按**设备 token** 鉴权，而自检未登录（与 `cloud_login` 同样 SKIP）——
这不是失败：拿不到策略时端侧照常工作（`PolicyFetcherTest` 已钉住"失败不改状态"）。

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
- [x] ~~**工具调用 JSON 合法率**：约束解码开/关的同一批提示词对比~~ → **§1.4 已做**
      （8 组、40 次调用，全部 100%，结论是"这批条件下约束解码无可测收益"）
- [x] ~~**上一条的加强版：重复采样求方差**~~ → **§1.9 已做**（每条重复 5 次 × 2 模型 × 2 模式 = 200 次调用；
      四格全部 100%，Wilson 95% 下界 **92.9%**，10/10 条提示词结果全一致）
- [x] ~~**UI 手工走一遍**~~ → §1.5（并抓到两个真缺陷：密码明文、徽标漏报客户端升级）
- [x] ~~**用户可用 UI**~~ → §1.2（三页改版 + Markdown 渲染；真机截图逐页核对）
- [x] ~~**深色模式**~~ → §1.4（修复 42 处硬编码色 + 机械检查）+ §1.6（真机目视确认）
- [x] ~~**会话历史持久化**~~ → §1.5（重启恢复）+ §1.7（历史列表页）
- [x] ~~**真机性能**（TTFT / 端侧嵌入 / 检索耗时）~~ → §1.1（**真机已测**，真机验收 2026-09-24）
- [x] ~~**L1 策略 apply→rebuild→rollback**~~ → §1.6（真机 E2E 跑通，自检 31 PASS / 0 FAIL / 1 SKIP）
- [x] ~~断网可用性（飞行模式下端侧链路是否仍可用）~~ → **§1.8 已验**（真机飞行模式下自检 31 PASS / 0 FAIL / 1 SKIP）
- [ ] release 开 R8 + 一轮 E2E（见 §1.0.1 的包体结论）

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

> ⚠️ **本节在 2026-09-25 修订**：下面两条曾经成立，但**已不再成立**——留着会误导
> （我本人就被"未做 Compose UI"误导过一次）。修订而非删除，是为了保留"当时确实没有真机"这个事实。

- ~~**无真机**~~ → **已有真机**（华为 Mate 40 Pro / HarmonyOS 4.2）：性能类结论已在本仓库产出，见 §1.1。
  仍缺的是 **iOS 真机**与**鸿蒙 NEXT 设备**（鸿蒙改走模拟器/Preview，见 RFC §7 M6/M7）。
- 设备凭证存储（Keystore AES-GCM）与网络客户端**只做了 JVM 可验的部分**——这一条**仍然成立**：
  Keystore 本身、真实 SSE 连接、真实 Ollama 调用需要设备/模拟器（见 §2）。
- ~~未做：Compose UI~~ → **已做**，见 §1.2/§1.4/§1.5/§1.7（三页 + Markdown 渲染 + 深色模式 + 会话历史）。
- **仍然未做**：约束解码开关的对比实验（见 §2）。

---

## 1.1 **Android 真机验收**（2026-09-24，华为 Mate 40 Pro / HarmonyOS 4.2）

> 背景：RFC 的风险项 **R8「无真机」** 一直是缺口（性能结论只能靠模拟器外推）。
> owner 的手机（`NOH-AN00`，HarmonyOS 4.2 = **Android 12 基座**）可用作 Android 真机——
> 它**不能**跑鸿蒙 NEXT 的 HAP（见 RFC §10 E13），但**能装能跑 Android APK**。

装法与模拟器同一套，只是端侧端点要改成 `adb reverse` 直通（`10.0.2.2` 在真机上不存在）：

```bash
adb reverse tcp:11434 tcp:11434
adb shell settings put global verifier_verify_adb_installs 0   # 视机型需要
bash scripts/android.sh push-model
SEKB_EDGE_URL=http://127.0.0.1:11434/v1 bash scripts/android.sh install
adb shell am start -n com.sekb.ondevice/.MainActivity --ez selftest true
adb logcat -s SEKB_SELFTEST:V      # ⚠️ 必须实时抓：本机日志刷得快，等 2 分钟再 -d 会被挤掉
```

**自检结果（最终）：`PASS=28 FAIL=0 SKIP=4`** ✅

> 过程：首次真机运行是 `PASS=20 FAIL=1 SKIP=7`，那 1 个 `FAIL` 经查是**自检逻辑缺陷**
> （见下第 2 条）而非产品缺陷；修复并重装后复跑得 `28/0/4`，`FAIL` 归零。
> 剩余 4 个 `SKIP` 全部是**缺前置条件**：`cloud_login`（缺账号）、
> `policy_apply_rebuild` / `policy_rollback_restores_threshold` / `policy_threshold_wired`（缺注入的策略包）。
> 其中 `policy_refresh — 未应用：policy_bad_signature` 反而是 **PASS**：L1 策略的验签**正确拒绝了坏签名**。

**真机性能数字（RFC §7「真机验收」要的那几个）**：

| 指标 | 真机实测 | 对照 |
|---|---|---|
| 端侧 LLM 首字延迟（TTFT） | **81–277 ms**（`plane=edge`；随模型冷/热状态波动） | 端到端参考 119 ms（M1） |
| 端侧嵌入 | **3–5 ms / 条**（ONNX int8，512 维） | 评测集均值曾记 38 ms（不同机型/口径） |
| 端侧检索 | **3 ms** | — |
| 检索命中 | `doc-rag` 分 **0.690**；无关文本余弦 0.283 | 模拟器 0.690（一致） |
| SQLite 持久化 | **启动时已有=3 块** → 跨进程存活 | — |
| 工具调用 JSON | `{"tool":"device_time","args":{}}` 合法 | — |
| PDF 抽取 | **78 字符 / 1 页**（开头 `On-device RAG keeps the index…`） | **与 iOS/macOS 同一份样本的 78 字符一致** |
| 文档删除不误清全库 | `rag_delete_file` 文档数 4→3、剩余切片=3；`rag_pdf_delete` 同上 | — |

**两条实测踩到的坑（都已修）**：

1. **`scripts/android.sh` 的 `install` 分支不传 `-P` 属性** → `SEKB_EDGE_URL=... install` 被**静默忽略**，
   `installDebug` 复用旧 BuildConfig（仍是 `10.0.2.2`），真机上表现是"端侧全部连不上"，
   而错误信息里看不到"环境变量没生效"。已改为 assemble/install 共用同一组 `PROP_FLAGS`。
2. **`privacy_device_only_local_gate` 的 `calls == 0` 被当成两分支共同前置条件** →
   本机端点下编排器**正确地**执行了 DEVICE_ONLY（`calls=1`）却判 FAIL。
   后果很严重：这条自检**只能在非本机端点通过**，所以在模拟器（`10.0.2.2`）上一直是
   "**因为错误的原因通过**"，R10「本机允许执行」那一半**从未被真正跑到过**。
   已改为按端点分档断言（本机：`calls ≥ 1` 且升级仍被拒；非本机：`calls == 0` 且拒绝且无输出）。

---

## 1.2 用户可用 UI 改造（2026-09-24，真机截图验证）

### 为什么改

原界面是**调试台**：裸 URL（`http://127.0.0.1:11434/v1`）、裸设备 ID、原始文档名
（`doc-rag (1 段, 36B)`）、端点/账号密码输入框、审计与工具合法率统计，**全部铺在聊天首页上**。
用户第一眼看到的是配置项和内部指标，而不是能用的聊天。

### 改成了什么

| 页面 | 内容 | 关键取舍 |
|---|---|---|
| **聊天**（首页） | 顶栏（`SEKB` + 状态圆点 + 知识库/设置入口）、气泡列表、底部输入栏 | **不再出现任何 URL**；状态用一句话表达（"端侧就绪 · 数据不出本机" / "端侧不可用 · 将使用云端"） |
| **本机知识库** | 完整文档列表（名称 / N 个片段 / 可读体积）+ 导入 FAB + 删除 | 上一版只在聊天页底部显示"最近 3 份"；体积从"整数 KB"（小文件显示 `0KB`）改成 B/KB/MB |
| **设置** | 账号与设备 / 连接（默认折叠，含"高级设置"）/ 隐私与权限审计 / 关于 | 审计**保留但对用户可读**（"被权限闸门拦截 N 次"），不再是原始指标行 |

- **主题**：主色对齐网页端 Ant Design 的蓝 `#1677ff`（原来是 M3 默认紫），
  并定义**语义色**：绿 = 本机完成、金 = 已上云（只在执行位置徽章上用，避免"绿色"失去含义）。
- **执行位置徽章保留但弱化**：它是产品承诺（设备专属数据不出端），不是调试信息，所以每条回答都标，
  但做成小字 + 图标（本机/云）。
- **引用来源**默认收起为一行"引用 N 段本机资料"，展开才看得到出处与分数。
- **报错与提示改走 Snackbar**，不再在输入框上方堆小字。

### 新增：Markdown 渲染（`ui/Markdown.kt` + `ui/MarkdownView.kt`）

模型的回答**本来就是 Markdown**（实测出现 `# 标题`、`## 核心结论`、`> ⚠️ **信息说明**`、
`| 路径 | 环节 |`）。改造前直接 `Text(bubble.text)`，用户看到的是**源码**；
网页端是渲染过的（`frontend/src/features/chat/markdown.tsx`），端侧必须对齐。

自己实现（不引第三方库，理由同 `BertWordPieceTokenizer`：依赖会漂移、镜像环境不稳），
覆盖模型实际会输出的子集：标题/列表/有序列表/引用/代码块/表格/分隔线 + 行内粗体/码/斜体/链接。
**两条硬约束**（都有单测钉住）：
1. **流式前缀不丢字**——逐字符喂入真实回答，每个前缀解析后可见字符必须与原文一致；
2. **解析器不抛异常**——它每帧跑在 UI 线程上。

### 真机截图（同目录 `screenshots/`）

| 文件 | 内容 |
|---|---|
| `ui-before-debug.png` | **改造前**：裸 URL / 裸设备 ID / 原始文档名 / 端点与账号输入框 / 审计指标，全在聊天首页 |
| `ui-after-chat-empty.png` | 改造后 · 聊天首页：状态一句话 + 圆点、示例问题、底部输入栏 |
| `ui-after-chat-markdown.png` | 改造后 · 真实问答：Markdown 标题/项目符号/代码块/分隔线渲染正确 + 流式提示 |
| `ui-after-documents.png` | 改造后 · 本机知识库：文档卡片（名称 / N 个片段 / 可读体积）+ 导入 FAB |
| `ui-after-settings.png` | 改造后 · 设置：账号与设备 / 连接 / 隐私与权限 / 关于 四个分组 |

### 验证

- `bash scripts/android.sh test` → **244 用例 / 0 失败**（新增 13 条 `MarkdownTest`）。
- **真机截图逐页核对**（华为 Mate 40 Pro）：聊天空状态（示例问题可点）、真实问答
  （Markdown 标题/项目符号/代码块/分隔线渲染正确、执行位置徽章、流式"正在生成"提示）、
  本机知识库（3 份文档 + 可读体积 + 导入 FAB）、设置（4 个分组卡片）。
- 过程中修掉一个**只有装到真机才看得见**的 bug：空状态用 `fillMaxSize()` 会在 Column 里
  吃掉全部高度，把**底部输入栏挤出屏幕**（整屏只有空状态，用户没法输入）→ 改 `weight(1f)`。

---

## 1.3 执行位置徽章"能自己解释原因"（2026-09-24）

### 问题

徽章只给结论："已上云（端侧不达标）"。用户（正确地）追问"为什么不达标？我的数据出去了吗？"——
而 UI 里**没有任何地方**回答这个问题（我上一轮把调试用的"最近决策"一行删掉了，却没把原因搬走，
属于**我造成的缺陷**）。

### 做法

- `Bubble` **分两个字段**存原因：`reason`（路由阶段，如 `edge_preferred`）与
  `escalateReason`（升级阶段，如 `degenerate`）。挂在**气泡上**而不是全局字段——
  用户问的是"**这一条**为什么上云"，全局字段只反映最后一次，往上翻就没了依据。
- `ui/PlaneExplain.kt`：原因码 → 用户能懂的一句话；徽章可点开显示。
- **设计原则：认不出的原因码原样透出**（`原因：<code>`），绝不返回"未知原因"。
  这条原则当场就见效了：真机上暴露出了我没见过的 `edge_unavailable`（见下）。

### ⚠️ 过程中修掉一个**我自己引入的严重 bug**：解释与徽章互相打脸

第一版从 `decision.plane`（路由**计划**走的平面）推结论，而徽章依据 `execution.primaryPlane`
（**实际**完成的平面）。而"升级"的定义恰恰就是"计划本机、实际云端"——两者必然不一致：
徽章说"已上云（端侧不达标）"，解释却说"这次在本机完成"。
**自相矛盾的解释比没有解释更糟**（会让用户不再相信任何徽章）。已改为由调用方传入与徽章**同源**的事实
（`completedOnDevice` / `escalated` / 两个原因码）。

同时修掉一个"吞掉后半段"的解析缺陷：`describe()` 原本只按逗号切分，而 `edge_preferred · degenerate`
这种 ` · ` 连接的组合码会被前缀正则命中第一段、**整段吞掉升级原因**。

### 验证

- `bash scripts/android.sh test` → **256 用例 / 0 失败**（含 `PlaneExplainTest` 10 条：
  含"升级场景不许声称在本机完成"与"` · ` 组合码不被截断"两条**回归测试**）。
- **真机实测**：针对 `edge_unavailable` 真实触发了一次"已上云"，展开后显示
  「端侧没能达标（原因：edge_unavailable），这次改由云端重答。」——与徽章一致。
  该码当时不在表里，被**原样透出**，因此被发现并补进翻译表。
- Markdown **表格**渲染也在本轮真机上首次确认（此前只做过代码对齐）。

---

## 1.4 深色模式修复 + 配色纪律的机械检查（2026-09-24）

### 查到的问题：**"我定义了深色配色"这句话当时是不成立的**

`SekbTheme` 里写了 `DarkScheme`，但三个页面里**有 42 处硬编码** `SekbColors.*` 常量
（`Fill` `#F0F2F5` 近白、`PrimaryContainer` `#E6F0FF` 近白蓝、`Border` `#E5E7EB`、`Primary` `#1677FF`），
**页面根本没走主题**。深色模式下会是：输入框与头像变**白块**、空状态变**白圆**、边框在深底上几乎看不见。

### 修法

- 页面里的主题色一律改走 `MaterialTheme.colorScheme.*`
  （`Fill`→`surfaceVariant`、`Border`→`outlineVariant`、`TextSecondary`→`onSurfaceVariant`、
  `Primary`→`primary`、`PrimaryContainer`→`primaryContainer`）。
- **语义色**（本机完成 = 绿 / 已上云 = 金 / 失败 = 红）改为 `SekbSemantic` + `LocalSekbSemantic`，
  **明暗两套**（深色下提亮，否则糊在背景里）。
- `MarkdownView.inlineText()` **不是** `@Composable`（要在任意上下文构造文本），
  所以行内码底色与链接色改为**显式参数**——直接读 `MaterialTheme` 会编译失败；
  这也顺带把"这段文本用什么底色"从隐式主题读取变成显式契约。

> 替换时的坑：`SekbColors.Primary` 是 `SekbColors.PrimaryContainer` 的**前缀**，
> 必须先替换长的，否则短的会把长的截断成半成品。

### 新增机械检查 `ThemeColorDisciplineTest`

深色模式坏掉**在浅色模式下完全看不出来**，靠记性守不住。所以加两条 JVM 单测：

1. 页面里**不许出现** `SekbColors.`（`theme/` 豁免；注释里提到不算）；扫到的文件数 < 3 直接判失败，
   避免"目录找错 → 静默通过"；
2. `Markdown.kt` / `PlaneExplain.kt` 这类纯逻辑文件**不许 import Compose**（要能被 JVM 单测直接跑）。

**并验证了这条检查真的会失败**（一个不能失败的检查等于不存在）：
往 `ChatScreen.kt` 注入一处 `SekbColors.Primary` → `FAILED` 且精确指到 `ChatScreen.kt:128`；
还原 → 立刻回绿。

### 验证

- `bash scripts/android.sh test` → **258 用例 / 0 失败**。
- `grep -rn 'SekbColors\.' ui/*.kt | grep -v SekbTheme` → **空**（页面已全部走主题）。
- ⚠️ **未做**：深色模式的**真机目视确认**——验证时手机已从 adb 断开（`adb devices` 为空）。
  代码层面已对齐，但"看起来对不对"仍需一次真机截图，见下节缺口。

---

## 1.5 会话历史持久化（2026-09-25）

### 为什么做

上一版的气泡**只在内存里**：杀掉 App 再打开，聊过的内容全没了。
对聊天应用这是不能接受的——用户的第一预期就是"上次聊到哪还在"。
网页端有会话列表（左侧栏），端侧连"恢复最近一次"都做不到。

### 结构（照 `DocumentRegistry` / `VectorStore` 的既有套路）

| 层 | 文件 | 职责 |
|---|---|---|
| 共享（可移植） | `shared/chat/Conversation.kt` | `Conversation` / `StoredMessage` / `StoredSource` 模型 + `ConversationCodec`（JSON 编解码）+ `ConversationStore` 接口 + `InMemoryConversationStore` |
| Android（平台） | `ondevice/chat/FileConversationStore.kt` | 落盘实现：**每个会话一个 JSON 文件** |

### 四个刻意的设计选择

1. **一文件一会话，而不是"一个大 JSON 全都会话"**：单文件更简单，但一次写坏 = 用户所有历史全没；
   一文件一会话的失败面小得多。
2. **写盘走"临时文件 + 原子改名"**：直接覆写时若进程被杀，会留下半截 JSON，
   下次打开就是一段坏数据。
3. **坏文件跳过而不是抛异常**（`lastSkippedCorrupt` 计数）：
   用户宁可少看到一段对话，也不该看到"历史列表打不开"。
   `ConversationCodec.decode` 对**旧数据缺字段**一律取默认值——**宁可丢一个字段，也不能丢整段对话**。
4. **上限裁剪（50 段）**：端侧存储有限，"无限增长的聊天记录"是典型的隐性磁盘泄漏。

### 与云端 `conversationId` 的关系（**有意不持久化它**）

云端 `conversationId` 是"服务端侧的会话"，只在接入账号后才有；端侧这一段（含**设备专属**的
检索来源与执行位置）服务端并不知道、也不该上传。所以本地另有一套落盘，只恢复
**用户在本机看到过什么**。代价是：恢复后继续提问时服务端会开一个新会话——
这是有意的取舍（把云端会话 id 落盘会让"换设备/清数据"后的行为难以解释）。

### 验证

- `bash scripts/android.sh test` → **273 用例 / 0 失败**（新增 15 条：`ConversationTest` 7 + `FileConversationStoreTest` 8）。
- `FileConversationStoreTest` 用**真实文件系统**（不是 mock），核心用例是
  `restarting the app still finds the latest conversation`——用**新的 store 实例**指向同一目录，
  等价于杀进程重启；另覆盖坏文件跳过、`.tmp` 不残留、上限裁剪、同 id 覆盖不重复。
- ⚠️ **未做**：真机上的"杀掉 App → 重开 → 历史还在"目视确认（验证时手机已断开 adb）；
  文件层已用真实文件系统验过，但端到端仍待一次真机操作。

---

## 1.6 L1 策略真机 E2E 跑通 —— 过程中发现**两个各自致命**的跨端缺陷（2026-09-25）

> 触发：owner 让继续推进遗留项。做「B3：把真机自检里 3 个策略 SKIP 转正」时，
> 推上签名策略包后**被拒**，顺藤摸出两个缺陷。

### 缺陷 1：开发默认签名密钥两侧不一致 → 端侧拒绝**所有**服务端签发的策略

| 侧 | 值 | 来源 |
|---|---|---|
| 服务端（未配 `SEKB_EDGE_POLICY_SECRET` 时） | `sha256("edge-policy:dev-only").hexdigest()` = `8d1c82bf…99b6` | `backend/app/core/edge_policy.py` |
| 端侧 `BuildConfig.DEFAULT_EDGE_POLICY_KEY` | 原文字面量 `"edge-policy:dev-only"` | `apps/android/app/build.gradle.kts` |

`build.gradle.kts` 的注释写着"服务端未配时会**派生同一个**开发密钥"——**这句话是错的**。
且**没有任何地方**传 `-PsekbPolicyKey`、也没配 `SEKB_EDGE_POLICY_SECRET`，
所以默认配置下端侧必然把服务端策略判为 `policy_bad_signature`。
真机日志里 `policy_refresh — 未应用：policy_bad_signature` 就是这个缺陷在**线上服务器**上的表现。

### 缺陷 2：时间单位不一致（服务端**秒** vs 端侧**毫秒**）→ 每份策略都被判"已过期"

端侧 `EdgePolicy.apply`：

```kotlin
val expiresAt = payload.optInt("expiresAt", 0)
if (expiresAt in 1 until nowMillis) return Outcome.Rejected("policy_expired")
```

而服务端 `issued = int(time.time())`（秒）。实测：`expiresAt = 1790753917` <
`nowMillis ≈ 1790667517429` → **恒真**，即**每一份策略都立刻过期**。

### 为什么两个缺陷此前都没被发现（结构性原因）

**两侧测试都是"同侧自洽"**：

- 后端：`verify_payload(p, policy_secret(cfg))` —— 自签自验，两侧用同一个**错误**密钥也通过；
- 端侧：`EdgePolicyTest` 用的是**自选**密钥 `"test-key"`，只验算法不验默认值；
  且时间字面量是 `nowMillis = 1700000001`（**秒级**），恰好让"秒 vs 毫秒"暴露不出来。

M4.5 验收③（改一次阈值 → 不发版、不重启也生效）因此**从未真正端到端跑通过**，
此前记录的"⏳ 拉取时机待接""policy_refresh SKIP 无设备凭证"掩盖了它。

### 修法与新增守卫（都是**跨端**的，不是自洽的）

1. `edge_policy.py`：开发默认密钥改为字面量 `DEV_DEFAULT_SECRET = "edge-policy:dev-only"`（与端侧逐字一致）；
2. `edge_policy.py`：`issuedAt`/`expiresAt` 改为 **epoch 毫秒**（`int(now*1000)`、`ttl_seconds*1000`），
   并在 docstring 里写明单位契约；
3. 后端 `test_dev_default_matches_android_build_default`：**直接读 `apps/android/app/build.gradle.kts`**
   抽出端侧默认值比对——任何一侧单方面改动立刻红；
4. 后端 `issuedAt/expiresAt > 10**12` 单位守卫；
5. 端侧新增 `PolicyCrossEndFixtureTest`：加载**由服务端代码签出**的真实策略包
   （`src/test/resources/policy/server-signed-dev.json`），断言端侧用默认密钥能验过；
   并有一条**反向守卫**（用当年那个 sha256 派生密钥验签必须被判 `policy_bad_signature`）。
   夹具测试的时间**必须用真实毫秒**（`System.currentTimeMillis()`）——
   第一版我写了秒级字面量，结果"恰好"绕过了缺陷 2，这本身就是"用了不真实的时间等于没测时间语义"。

**已验证守卫会红**：把 `build.gradle.kts` 的默认值改成别的 → 后端守卫立刻 FAILED 并打印两侧的值；
还原 → 回绿。

### 真机 E2E 结果（首次跑通）

推入服务端签发的策略包（`retrievalMinScore=0.7`）后：

```
[PASS] policy_apply_rebuild — 应用=641466276 阈值 0.5 → 0.7（期望 0.7）重建=true（阈值有变化=true）
[PASS] policy_threshold_wired — 检索器阈值=0.7（策略=0.7）
[PASS] policy_rollback_restores_threshold — 先应用到 0.9，回滚后阈值=0.7（期望 0.7）
自检汇总：PASS=31 FAIL=0 SKIP=1
```

**真机自检从 `PASS=28 FAIL=0 SKIP=4` → `PASS=31 FAIL=0 SKIP=1`**（3 个策略 SKIP 全部转正，
剩余 1 个 SKIP 是 `cloud_login`——需要真实设备凭证）。
这同时证明 **M4.5 验收③ 成立**：改阈值 → 不重新发版、不重启，检索器阈值当场由 0.5 变 0.7。

### 顺带修掉的脚本缺陷（都在 `scripts/android.sh`）

- `push-policy` 读的是 `$1`，而 `$1` 是**任务名** → 报"找不到策略文件：push-policy"。应为 `$2`（后改 `$1`+shift）。
- 循环是 `for task in "$@"` → 带参数的调用会把**文件路径当成第二个任务**去跑 `./gradlew <路径>` → BUILD FAILED。
  已改为 `while [ $# -gt 0 ]` + `shift`，并在 `push-policy` 里消费掉文件参数。
- 相对路径按仓库根解析（脚本前面已 `cd "$APP_DIR"`，否则 `apps/...` 会被解析成 `apps/android/apps/...`）。

### ⚠️ 仍需一次**后端重新部署**

线上 `bos-studio.tech` 跑的还是旧代码（旧密钥 + 秒级时间戳），所以真机 `policy_refresh`
目前仍显示"未应用：policy_bad_signature"。修好的代码要部署上去，**实时拉取**这条路径才算闭环
（本次验证走的是"注入本地策略包"的离线路径）。

---

## 1.7 历史对话列表页 + "新对话后重启跳回旧对话"的修复（2026-09-25）

### 为什么要这一页

§1.5 的会话持久化只做到"重启恢复**最近一次**会话"——更早的对话**其实已经落盘**，
但用户看不到、也点不进去。**存了却拿不出来等于没存**（用户会以为"历史没了"，而文件就在那儿）。
历史列表页就是把已存的东西变成可访问的。

### 实现

- `ui/ConversationListScreen.kt`：列表卡片（标题 / 相对时间 / 消息条数 / 删除）、空状态、
  当前对话标记；点开即加载并回到聊天页。
- `ui/TimeFmt.kt`：相对时间文案（**纯逻辑**，可被 JVM 单测直接跑）——"刚刚 / N 分钟前 / HH:mm /
  昨天 HH:mm / N 天前 / 日期"分段降级。超过一周改用具体日期：这时"37 天前"反而不如日期直观。
  **时区与"当前时间"都作为参数注入**，否则测试会在 UTC 的 CI 与 UTC+8 的开发机上给出不同答案。
- `ChatViewModel`：`refreshConversations` / `openConversation` / `deleteConversation`；
  "启动恢复"与"列表点开"共用同一个 `loadConversation()`——
  分成两份实现必然漂移（例如一处忘了清 `history`，接着提问就会把两段对话混在一起）。

### 过程中发现并修掉的体验缺陷：**点「新对话」后重启会跳回旧对话**

发现方式很偶然：我用 `adb input tap` 点历史图标时**点偏到了「新对话」**（"+" 只在有对话时出现，
位置会随图标数量变化），于是对话被清空；随后重启 App，**旧对话又回来了**。

根因：启动恢复用的是"最近更新的一段"（`latest()`），而**新对话是空的、因而没有落盘**，
所以 `latest()` 只能把上一段拉回来。用户刚点过「新对话」，重启却回到旧对话——与他的操作相反。

修法：新增**当前会话指针**（`ConversationStore.saveCurrentId/currentId`，
Android 实现存 `files/conversations/current.id`，文件名**不以 `.json` 结尾**所以不会被列表当成会话）。
启动时优先按指针恢复；指针指向的新会话没有文件 → 启动就是干净的空对话。
没有指针时（旧版本数据）退回 `latest()`，保证兼容。

### 验证

- `bash scripts/android.sh test` → **285 用例 / 0 失败**（新增 `TimeFmtTest` 9 条）。
- **真机**（Mate 40 Pro，深色模式）：历史页显示 `1 段 · 都存在这台设备上`，
  卡片标题「现在几点了？」+ **`25 分钟前 · 2 条消息`**（相对时间正确）；
  杀进程重开后聊天页标题与内容正确恢复；顶栏四个入口（历史 / 新对话 / 知识库 / 设置）布局正确。
- `ThemeColorDisciplineTest` 的"纯逻辑文件不许依赖 Compose"清单已加入 `TimeFmt.kt`。

---

## 1.8 断网可用性（真机飞行模式，2026-09-25）

### 做法

真机开飞行模式（`adb shell cmd connectivity airplane-mode enable`，确认 `airplane_mode_on=1`），
经 **USB `adb reverse`** 保持端侧运行时可达，然后跑自检（**走 logcat，不需要解锁屏幕**——
锁屏会挡住截图，这是个实用技巧：`--ez selftest true` 的输出全在 logcat 里）。

### 结果：与联网时**完全相同**

```
[PASS] edge_reachable — http://127.0.0.1:11434/v1 → true
[PASS] privacy_device_only_local_gate — 本机=true 端侧调用=1 原因=escalation_blocked:device_only
[PASS] rag_retrieve — 命中=doc-rag 分=0.690 嵌入=16ms 检索=12ms
[PASS] rag_device_only_guard — device_only_requires_on_device_embedding
自检汇总：PASS=31 FAIL=0 SKIP=1
```

**零 FAIL**。即：端侧嵌入（16ms）、本地检索（12ms）、隐私闸门、L1 策略应用/回滚
**全都不依赖互联网**；唯一的 SKIP 仍是 `cloud_login`（需要设备凭证）。

### ⚠️ 这个结论的边界（必须说清）

"端侧"在这套联调形态下是 **经 USB 直连宿主机的 OpenAI 兼容端点**（`127.0.0.1:11434` + `adb reverse`），
不是"模型跑在手机 SoC 上"。所以本实验证明的是：

- ✅ **链路不依赖互联网**（飞行模式下全程可用）；
- ✅ R10 的判据（回环地址 = 本机）在离线时依然成立；
- ❌ **不等于**验证了"模型在手机本地推理"——那是 MindSpore Lite / 端侧推理运行时的范畴（M6/M7）。

### 顺带发现的一个疑点（**尚未查清，留给下一轮**）

自检里 `rag_device_only_guard` 报告设备**实际使用的嵌入空间**是
`BAAI/bge-small-zh-v1.5-int8@512`（int8 模型生效），
而 §1.6 推入并成功应用的策略包，其空间戳是 `BAAI/bge-small-zh-v1.5@512`（**不带 int8**）——**却 PASS 了**。

空间戳绑定的**目的**是"防止热修把检索改坏"（`docs/多端跨端-工程议题` §3.2 的硬约束）。
如果校验比的是 `EdgeRuntimeConfig.embeddingSpace` 而**不是检索器实际使用的空间**，
那么当两者不一致时（本机就是这种情况：int8 在跑、配置写着 fp32 空间），
**这道绑定并没有真正保护检索**。需要下一轮核实：配置里的空间是谁写的、为什么与检索器不一致、
以及绑定应当比哪一个。

---

## 1.9 工具调用合法率：重复采样求方差（2026-09-25）

### 为什么做

§1.4 得到 40/40「合法」，据此写了"这批条件下约束解码没有可测收益"。但那批数据
**每条提示词只跑了 1 次**，没有方差——**10/10 全对时真实合法率的 Wilson 95% 下界只有 72.2%**，
点估计撑不住结论的语气。§1.4 的"局限"节自己也写明了这一点。本轮把它补上。

### 做法

- 新增 `EvalStats`（共享层，纯逻辑）：**Wilson** 区间 + 按提示词的稳定性统计，附 6 条单测
  （用公开已知值校验：`wilson(10,10)≈[0.722,1.0]`、`wilson(0,10)≈[0,0.278]`、`wilson(5,10)≈[0.237,0.763]`）。
  选 Wilson 而非正态近似（Wald）的理由：`p=1, n=10` 时 Wald 方差为 0，区间退化成 `[1,1]`，
  **恰好把"样本不够"这个最重要的事实藏起来**。
- `ToolCallEvalRunner` 新增 `repeats` 参数并聚合逐条结果；报告里打印区间 + 不稳定提示词清单。
- 真机跑：`am start ... --ez eval true --ez hard true --ei repeats 5`（难档，两个档位模型都跑）。

### 结果（真机 Mate 40 Pro，200 次调用）

| 档位 | 模式 | 尝试 | 合法 | 合法率 | 95% CI (Wilson) | 逐条一致 |
|---|---|---|---|---|---|---|
| qwen3.5-2b | 约束解码 ON | 50 | 50 | **100%** | [92.9%, 100%] | **10/10** |
| qwen3.5-2b | OFF | 50 | 50 | **100%** | [92.9%, 100%] | **10/10** |
| qwen3.5-4b | ON | 50 | 50 | **100%** | [92.9%, 100%] | **10/10** |
| qwen3.5-4b | OFF | 50 | 50 | **100%** | [92.9%, 100%] | **10/10** |

### 结论

- §1.4 的结论**成立且被显著加强**：不只是点估计 100%，而是**每一条提示词在 5 次重复下结果全部一致**
  （不存在"某条问法偶尔失败"），区间下界从 n=10 时的 72.2% 提升到 n=50 时的 **92.9%**。
  即：**这批条件下约束解码确实没有可测量的收益**，可以放心不为 grammar 付工程复杂度。
- **仍然存在的局限（必须说）**：Wilson 区间把 `提示词 × 重复` 当作独立样本，是**乐观的**——
  同一提示词的重复彼此相关（聚类），真实区间应当更宽。报告里的"逐条稳定性"正是为对冲这一点：
  它显示 10/10 条都稳定，所以即使考虑聚类，也没有证据表明存在不稳定的问法。
- **仍未覆盖**：完全不给 schema 的极端条件；工具集只有 3 个、不存在相似工具干扰。

### 本次新增的工程能力

`--ei repeats N`（默认 1，行为与旧版一致）+ `EvalStats`（Wilson 区间/稳定性，均有单测），
以后任何"比例类"结论都不必再靠单次采样。

---

## 1.10 空间戳绑定失效（硬约束方向反了）——真缺陷 + 修复 + 双向验证（2026-09-25）

### 怎么发现的

§1.8 跑断网自检时注意到一处不一致：`rag_device_only_guard` 报告设备**实际使用**的嵌入空间是
`BAAI/bge-small-zh-v1.5-int8@512`，而 §1.6 推入并**成功应用**的策略包空间戳是
`BAAI/bge-small-zh-v1.5@512`（**不带 int8**）——**却通过了校验**。

### 根因

`EdgePolicy.apply` 比对的是 `current.embeddingSpace`，即 `EdgeRuntimeConfig.embeddingSpace`：

```kotlin
val space = payload.optString("embeddingSpace", "")
if (space.isNotEmpty() && space != current.embeddingSpace) return Rejected("policy_space_mismatch:…")
```

而 `EdgeRuntimeConfig` 是在 `SekbApp` **第 56 行**构造的，那时还不知道本机跑 fp32 还是 int8
（`int8Available` 要到第 100 行才算），于是 `embeddingSpace` 一直是**默认值** `…@512`；
检索器用的却是 `if (int8Available) …-int8@512 else …@512`。**本机 int8 生效 → 两者不一致。**

于是这道硬约束**方向反了**（它的目的正是"防止为某个嵌入空间调的阈值被用到另一个空间上把检索改坏"）：

| 策略的空间戳 | 修复前 | 应当是 |
|---|---|---|
| `…-int8@512`（本机真正需要的） | **被拒** ❌ | 接受 |
| `…@512`（fp32） | **被接受并应用到 int8 检索器** ❌ | 拒绝 |

### 修复

在嵌入器就绪后**把 config 的空间对齐到嵌入器实际的 `space.id`**（`config = config.copy(embeddingSpace = embeddingProvider.space.id)`）——
**由构造保证同源**，而不是靠"记得同步"。已加详细注释说明这个坑。

### 双向验证（真机，服务端签名的两份策略包）

| 用例 | 修复前 | 修复后 |
|---|---|---|
| 推**int8 空间**策略（`minScore=0.62`） | `[FAIL] policy_apply_rebuild — 应用=null`（被拒） | ✅ `应用=0 阈值 0.5 → 0.62` + `检索器阈值=0.62` |
| 推**fp32 空间**策略（`minScore=0.71`） | 被接受（**错误**） | ✅ 被拒（`应用=null`，阈值不变） |

修复后恢复正确策略，自检回到 **PASS=31 FAIL=0 SKIP=1**。

### ⚠️ 顺带发现：自检把"策略被正确拒绝"报成了 FAIL

推入 fp32 策略（**应当**被拒）时自检报 `PASS=28 FAIL=3`：

```
[FAIL] policy_apply_rebuild — 应用=null 阈值 0.5 → 0.5（期望 0.71）
```

但这是**正确行为**——自检的这几项假定"注入的策略一定合法"，把"被拒"与"应用后阈值不对"混为一谈。
**我自己就先误读了一次**（以为是修复没生效）。这三项应当：① 打印拒绝原因；
② 在拒绝原因属于"预期可拒"（空间不符/签名不符）时记 **SKIP 并说明**，而不是 FAIL。
**留待下一轮修改**（不影响本次结论）。

### 顺带说明：单元测试里的跨端夹具不受影响

`src/test/resources/policy/server-signed-dev.json` 的空间戳是 `@512`，而单测里
`config()` 也是显式构造成 `@512` 的——它验的是**签名/规范化 JSON 的跨端一致性**，
与"设备实际用哪个空间"是两件事，因此仍然有效。

---

## 1.11 自检：把"策略被正确拒绝"与"产品缺陷"区分开（2026-09-25）

### 问题

§1.10 里推入 fp32 空间策略（**应当**被拒）时，自检报 `PASS=28 FAIL=3`。
但那是**正确行为**——那几项假定"注入的策略一定合法"，把"被拒"与"应用后阈值不对"混为一谈。
**我自己就先误读了一次**（以为是修复没生效，实际是没装包）。

### 修法（比"把 FAIL 改成 SKIP"多一点）

直接改 SKIP 会走向另一个错误：**真缺陷也会被藏起来**。
而 §1.10 修的那个缺陷，症状恰恰就是"策略被**错误**拒绝"。所以：

1. **把不变量本身变成一条自检项** `policy_space_stamp_same_source`：
   断言 `config.embeddingSpace == vectorStore.space.id`（用**向量库**的空间——库里存的就是这套向量，
   策略阈值必须与它匹配）。这一项若存在，§1.10 的缺陷**当初就会红**，不必靠人肉比日志发现。
   该项在策略注入之前执行，因此**任何情况下都会跑**。
2. **注入的策略被拒绝 → SKIP + 打印原因**（空间/签名/过期/灰度都属于"预期可拒"），
   并提示"请用与本机空间一致的策略包重推"；而"该不该拒"由第 1 条不变量把关。

### 验证状态（如实）

- **单元测试**：`bash scripts/android.sh test` → **291 用例 / 0 失败**（本次改动在 self-test 代码里，
  不新增单测；既有用例全绿）。
- ⚠️ **真机验证未完成**：装包时手机弹出**华为统一身份验证**
  （`com.huawei.coauthservice/…UnifiedAuthenticationDialogActivity`）需要指纹/密码，
  我无法通过；`lastUpdateTime` 仍是上一版，说明新包**没装上**。
  故本条的两条验证（正确空间→全 PASS、错误空间→SKIP 而非 FAIL）**待 owner 解锁手机后补跑**。

---

## 1.12 release 开 R8：dex 64.7 MB → 2.8 MB（2026-09-25）

### 为什么做

§1.0.1 做完 P0 瘦身后留下的结论是："未做：release 开 R8（dex 仍约 66 MB，是下一个大头）"。
本轮把它做掉——**量体积这半不需要装包**，因此不受真机安装受限的影响。

### 改动

- `app/build.gradle.kts`：`release { isMinifyEnabled = true; isShrinkResources = true }`
  （`shrinkResources` 只在 minify 打开时才有意义，两者一起开才能同时瘦 dex 与资源）。
- `app/proguard-rules.pro`：加 `-dontwarn com.gemalto.jp2.JP2Decoder`。

### 遇到的唯一阻塞（R8 自己给出了答案）

```
ERROR: Missing class com.gemalto.jp2.JP2Decoder (referenced from: …pdfbox.filter.JPXFilter…)
```

`com.gemalto.jp2` 是 **PDFBox 的可选依赖**，只有解析 PDF 内嵌的 **JPEG2000 图像**时才需要。
本项目的 `PdfExtractor` **只用 `PDFTextStripper` 抽文本、不走图像路径**，
所以该缺失不可能影响现有能力；加 `-dontwarn` 让 R8 剥离它，而不是为一个用不到的解码器把包体做大。
R8 会把建议规则写到 `build/outputs/mapping/release/missing_rules.txt`——照它加即可。

> **残留限制（如实记录）**：若将来要渲染/提取 PDF 内嵌的 JPEG2000 图像，必须加回 `jp2-android`
> 依赖，否则那条路径会在运行时抛错。当前无此需求。

### 结果（真机同源构建，arm64）

| | debug | **release（R8）** | 降幅 |
|---|---|---|---|
| APK | 43.3 MB | **20.8 MB** | **−52%** |
| dex 合计 | 64.7 MB | **2.8 MB** | **−96%** |

（debug 的 dex 分布：`classes.dex` 42.5 MB、`classes13` 12.4 MB、`classes14` 8.9 MB，其余为碎片。）

### 已验证 / 未验证（务必分清）

- ✅ **体积**：上面是实测数字（`scripts/android.sh release`，新增该动作，可复现）。
- ✅ **R8 没删掉自检入口**：`mapping.txt` 里有 `com.sekb.ondevice.SelfTest.*`，且 release dex 里
  仍存在 `SEKB_SELFTEST` 字符串 → 签名后的 release 包仍能跑自检做 E2E。
- ✅ **关键类未被删**：`MainActivity`、`tom_roush.pdfbox.text.PDFTextStripper` 均在 mapping 中。
- ❌ **"R8 后还能不能正常跑"仍未验证**（本次唯一的缺口）：
  - 已加联调签名（`release.signingConfig = debug`，复用 `.tooling/android-home/debug.keystore`，
    与 debug 包**同一把 key** → `install -r` 可覆盖安装），产出 `app-arm64-v8a-release.apk` **20.8 MB**（已签名）。
  - 但**装不上**：`adb install -r` 停在设备侧确认/验证上超过 10 分钟无进展，
    且屏幕已灭/锁（`mResumedActivity` 查询为空），我无法交互。判定依据是设备上 APK 仍是 **43.3 MB（debug）**。
  - **闭环只差一步**：手机解锁后跑
    `adb install -r apps/android/app/build/outputs/apk/release/app-arm64-v8a-release.apk`，
    再 `am start … --ez selftest true` 跑一轮自检（R8 后能否 32 PASS 即证明 R8 没删坏代码）。

> ⚠️ **生产必须换正式签名**：把 `release.signingConfig` 指向 debug keystore **仅用于联调**，
> 不可用于发布。

### 顺带：本轮的"新不变量项"已真机验证（在 debug 包上）

自检新增的 `policy_space_stamp_same_source` 在真机跑出：

```
[PASS] policy_space_stamp_same_source — 策略校验空间=BAAI/bge-small-zh-v1.5-int8@512 向量库实际空间=BAAI/bge-small-zh-v1.5-int8@512
[PASS] policy_apply_rebuild — 应用=0 阈值 0.5 → 0.62
[PASS] policy_threshold_wired — 检索器阈值=0.62
[PASS] policy_rollback_restores_threshold — 先应用到 0.9，回滚后阈值=0.62
自检汇总：PASS=32 FAIL=0 SKIP=1        （新增 1 项后由 31 → 32）
```

即 §1.11 的"把不变量变成自检项"**已生效**：§1.10 那个反向缺陷若再出现，这一项会直接报红。
（**注意**：这次跑的是设备上的 **debug** 包——因此它验证的是**自检改动**，不是 R8。）

---

## 1.13 R8 运行期验证：**抓到一个真回归**（2026-09-25）

> 背景：§1.12 只验了 R8 的**体积**，运行期一直是缺口，理由正是"R8 可能删坏代码，必须跑一轮 E2E"。
> 本轮手机短暂解锁，把这个缺口补上了——**然后立刻抓到一个真问题**。

### 已完成的验证

| 步骤 | 结果 |
|---|---|
| 构建带签名的 release（复用 debug keystore，可与 debug 包覆盖安装） | ✅ `app-arm64-v8a-release.apk` **20.8 MB** |
| 安装到真机 | ✅ `Success`；设备 APK **20.8 MB**、**无 `DEBUGGABLE` 标志** → 确认是 release 包 |
| 跑自检 | ⚠️ **前 10 项 0 FAIL，然后卡在第 11 项** |

### 现象：卡在 `rag_provider`（第 11 项）

```
[PASS] edge_reachable … [PASS] edge_tool_json        ← 前 10 项全过、0 FAIL
（此后无任何自检输出；无异常、无崩溃、进程存活）
```

第 11 项是 **`rag_provider`**——ONNX 嵌入提供者的创建。

### 诊断：ONNX Runtime 的 JNI/反射被 R8 剥掉了

`ai.onnxruntime.**` 的 Java API 通过 **JNI + 反射**加载 native 库与类，R8 **看不到这些反射引用**，
于是把相关类/成员剥掉或改名 → 运行时创建不出 provider。
这是 R8 的经典坑，也正是"R8 必须跑一轮 E2E"的价值：**体积数字好看不代表还能跑**。

### 已应用的修法（**待重验**）

`app/proguard-rules.pro` 增加：

```
-keep class ai.onnxruntime.** { *; }
-keepclassmembers class ai.onnxruntime.** { *; }
-dontwarn ai.onnxruntime.**
```

重建：`BUILD SUCCESSFUL`。

### ⚠️ 重验未完成（如实记录）

补齐 keep 规则后重装失败、且**手机随后从 adb 断开**（设备查询全空），故
**"加了 keep 规则后第 11 项是否恢复"尚未验证**。下一步只需：

```bash
SEKB_EDGE_URL=http://127.0.0.1:11434/v1 bash scripts/android.sh release   # 必须带 edge 地址！
adb install -r apps/android/app/build/outputs/apk/release/app-arm64-v8a-release.apk
adb shell am start -n com.sekb.ondevice/.MainActivity --ez selftest true   # 看 logcat
```

### 本轮顺带查清的两条 release 包操作约束

1. **release 包构建必须带 `SEKB_EDGE_URL`**：我第一次构建时忘了带，包内是默认的 `10.0.2.2`
   （模拟器地址）→ 真机上 `edge_reachable=false`、所有 `edge_*` 全 FAIL。
   这不是 R8 的问题，是构建参数漏了——但表现很像"R8 把网络搞坏了"，**很容易误判**。
2. **release 包（非 debuggable）不能用 `run-as`** → `scripts/android.sh push-policy` 会报
   `run-as: package not debuggable`。因此 3 个策略项在 release 自检里**只能 SKIP**，
   release 包的满分不是 32 而是约 28（+SKIP）——这个口径要记住，否则会把"推不进策略包"误当成缺陷。

---

## 1.14 ✅ R8 回归已修复：release 包自检与 debug **完全一致**（2026-09-25）

§1.13 记下"加 keep 规则后第 11 项是否恢复尚未验证"。手机重新接上后补验完成：

```bash
SEKB_EDGE_URL=http://127.0.0.1:11434/v1 bash scripts/android.sh release   # 38s
adb install -r apps/android/app/build/outputs/apk/release/app-arm64-v8a-release.apk   # Success
adb shell am start -n com.sekb.ondevice/.MainActivity --ez selftest true
```

```
[PASS] rag_provider  — 嵌入=ONNX BAAI/bge-small-zh-v1.5-int8@512 空间=…-int8@512 本机计算=true
[PASS] rag_model_path — …/bge-small-zh-v1.5=true | …-int8=true
自检汇总：PASS=32 FAIL=0 SKIP=1
```

**结论**：`-keep class ai.onnxruntime.** { *; }` 生效。
**R8 release 包的自检结果与 debug 包完全一致（32 PASS / 0 FAIL / 1 SKIP）** ——
即 **R8 只瘦身、没有改变行为**，这一条终于有了运行期证据，而不只是体积数字。

> 备注：release 包（非 debuggable）本不能用 `run-as` 推策略包，但此处策略项仍 PASS ——
> 因为应用数据在同一 package + 同一签名 key 的**覆盖安装**中被保留，之前注入的策略包还在
> `files/sekb-e2e-policy.json`。这也顺带验证了"release 与 debug 可覆盖安装"这条联调路径。

### Android 侧遗留项状态：**全部收尾**

深色模式、重启恢复会话、L1 策略 apply→rebuild→rollback、断网可用性、约束解码重复采样、
会话列表页、**release R8（体积 + 运行期）** —— 全部完成或已验证。
