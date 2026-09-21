---
title: 端侧共享层方案（KMP 逻辑共享 + 各端原生 UI）
layer: 设计层
owner: SEKB Team
status: draft（待 owner 确认）
version: v1.0.0
last-updated: 2026-09-21
based-on-commit: ad6540c
related: [docs/RFC-多端跨端方案, docs/RFC-端云协同与端侧Agent, docs/RFC-端云协同-实施记录, apps/README]
---

# 端侧共享层方案（KMP 逻辑共享 + 各端原生 UI）· v1（待确认）

> **这份文档回应 owner 的两个问题**：
> ①「KMP 会不会损失原生体验？」②「KMP 会不会损耗性能？」
> 以及 owner 的倾向：**每端用原生 UI 开发，不用跨端统一 UI**。
>
> 结论先说：**KMP 不含 UI，所以"原生体验"这一项它根本碰不到**；
> **性能损耗在关键路径上可以忽略**（热点在 ONNX/llama.cpp 的 C/C++ 内核，不在共享逻辑里），
> 真正的代价是**构建复杂度 + 依赖可移植性**——这才是这份方案要花时间的地方。
> CMP（跨端统一 UI）是否值得，见 [`docs/多端跨端-CMP方案评估.md`](多端跨端-CMP方案评估.md)。

---

## 0. 先回答问题（结论 + 依据 + 代价）

### Q1「KMP 会损失原生体验吗？」→ **不会，因为它不含 UI**

| 项 | KMP（本方案） | CMP（跨端统一 UI） |
|---|---|---|
| UI 由谁画 | **各端原生**：Jetpack Compose / SwiftUI / ArkUI | Compose Multiplatform 自己画（Skia 或 ArkUI RenderNode） |
| 控件与手势 | 系统原生（返回手势、惯性滚动、系统文本选择菜单、分享面板） | 由 CMP 实现，与系统行为存在差异 |
| 输入法（中文） | 系统原生 IME | 需 CMP 的 IME 桥（CMP 1.9 已加 iOS `PlatformImeOptions`，但仍不是系统默认体验） |
| 无障碍 | 系统原生（VoiceOver/TalkBack 直接读原生语义树） | 依赖 CMP 的语义映射（web 端到 1.9 仍只支持部分能力） |
| 影响面 | **0**（UI 一行都不在共享层里） | 中～高（取决于 UI 复杂度） |

所以"损失原生体验"这件事，**是 CMP 的问题，不是 KMP 的问题**。owner 选原生 UI 与选 KMP 逻辑共享
**不冲突**：iOS 仍然是 SwiftUI 写的，Android 仍然是 Jetpack Compose 写的。

### Q2「KMP 会损耗性能吗？」→ **关键路径上可忽略；但它确实有成本，得摊开说**

**为什么可忽略**：

1. **编译方式**：Kotlin/Native 是 **AOT 编译成机器码**（无 JIT、无解释器、无虚拟机），
   iOS 上以静态 framework 形式进包——不是"把一门脚本语言塞进 App"。
2. **热点不在共享逻辑里，而且已经实测过**：本项目的耗时大头是**模型推理**
   （嵌入 19ms(int8)、decode 86–116 tok/s，都在 C/C++ 内核），共享逻辑做的是
   "收到请求 → 判一次平面 → 校验一次工具 JSON"。**本机 JVM 实测（2026-09-21）**：

   | 共享逻辑 | 单次耗时（JVM 预热后） |
   |---|---|
   | `PlaneRouter.decide`（端云决策） | **1.135 μs** |
   | `ToolCallJson.parse`（工具调用 JSON 校验） | **0.923 μs** |
   | `Chunking.split`（2KB 文档分块，导入时一次性） | **9.040 μs** |

   一次聊天大约做十几次这类调用 ≈ **10 μs 量级**，相对端到端 **119ms** 是 **约 0.008%**——
   也就是说"把逻辑换成 Kotlin 实现"不可能成为延迟瓶颈（Kotlin/Native 版本待测，见 §5 P1）。
   ⚠️ 口径：这是 **JVM** 上的数字（跑在 `apps/android` 的 183 个单测里），
   不是 Kotlin/Native，也不是真机；它证明的是**量级**，不是最终值。
3. **边界是粗粒度的**：跨语言调用（Swift↔Kotlin、ArkTS↔Kotlin）有固定开销，但本方案的端口
   **每次调用做一件事**（"给我一份文档的文本"、"把这个向量存进去"），不是"每帧回调"。
   唯一需要小心的是**流式回调**（逐个 token）——方案里已把 SSE 解析放在共享层，
   token 边界不进跨语言边界（见 §3.4）。

**诚实的成本（不藏着）**：

| 成本 | 量级 | 处置 |
|---|---|---|
| 包体积 | 共享 framework 增加**数 MB**（Kotlin/Native 运行时 + stdlib + coroutines + serialization） | 开 dead-code elimination、release 混淆；iOS 用静态 framework；**实测后记进文档** |
| 构建复杂度 | iOS 需要 Xcode ↔ Gradle 协作（Run Script 产 XCFramework）；Android 侧多一个模块 | 一次性脚本化（`scripts/ios.sh`，**计划新增**），并写进 `apps/ios/README.md` <!-- check-docs:ignore 计划中的脚本，尚未创建 --> |
| 调试体验 | Swift ↔ Kotlin 断点跨语言；Kotlin/Native 的崩溃栈需要符号化 | 用 XCFramework + dSYM；关键路径保留 Swift 侧日志 |
| **依赖可移植性**（真正的工作量） | `org.json` / `okhttp3` / `java.io.File`·`UUID` / `ai.onnxruntime`（JVM API）在 iOS/鸿蒙不可用 | ✅ **已处理（M4 第 2 步，2026-09-21）**：`org.json` 收敛到 `core/Json.kt` 一个门面；`okhttp3` 拆到 `net/OkHttpTransport.kt`；`File`/`UUID` 变成 `core/PlatformFiles.kt` / `core/Ids.kt` 两个端口；`ai.onnxruntime` 留在 `embed/OnnxBgeEmbedding.kt`（**按设计**就是平台实现）。结果：除这 4 个端口文件外，其余 main 代码**不再出现任何 JVM/platform import** |
| 学习/维护面 | 一套 Kotlin + 各端原生 UI（Swift/ArkTS 也要会） | 这是 owner 已选的路线；共享的是"最容易写错的那部分" |

### Q3 三条路线对比（给 owner 的决策用）

| 路线 | 逻辑 | UI | 各端体验 | 一人维护成本 | 本方案 |
|---|---|---|---|---|---|
| **A. KMP 逻辑 + 原生 UI**（owner 倾向） | 共享 1 份 | 各写 1 份（3–4 份） | **最好** | 中（逻辑共享抵消了大部分重复） | ✅ 本文 |
| B. KMP 逻辑 + CMP UI | 共享 1 份 | 共享 1 份 | 中（有观感差异） | **最低** | 见 CMP 评估文档 |
| C. 各端全部手写 | 各写 1 份 | 各写 1 份 | 最好 | **最高**（且四份逻辑必然漂移） | ❌ 不建议 |

> **一句话**：owner 的选择（A）在"体验优先 + 一人维护"这个约束下是**合理的**；
> 本方案的价值就是把 A 里"逻辑不许漂移"这件事做成机器可判的（§7）。

---

## 1. 共享边界：什么进共享层、什么留在各端

| 层 | 内容（现状代码） | 共享？ | 理由 |
|---|---|---|---|
| 协议客户端 | `net/SekbApi` `net/SseParser` `net/OpenAiSseParser` | ✅ | 协议只有一份；三端各写一遍必然分叉 |
| 端云路由 | `route/PlaneRouter` `route/StreamGuard` | ✅ | **行为必须等价**，否则"端侧完成率"不可比 |
| 工具层 | `tools/ToolRegistry` `tools/ToolCallJson` `tools/KbSearchTool` | ✅ | 校验规则与 JSON 模板必须一致 |
| 编排 | `chat/ChatOrchestrator` `chat/CloudChat` `chat/ToolCallEval` | ✅ | 升级/交接流程的语义在 M1 已定死 |
| 边侧 LLM | `edge/EdgeLlmClient` | ✅ | 它是"OpenAI 兼容端点的客户端"，与平台无关 |
| RAG | `rag/{Chunking,VectorMath,Retriever,KnowledgeIndex,VectorStore,DocumentRegistry}` | ✅ | 分块/阈值/空间戳是**不变量**，必须一份 |
| 嵌入（接口） | `embed/{EmbeddingProvider,BertWordPieceTokenizer}` | ✅ | 分词器是纯算法；**运行时**留各端（`OnnxBgeEmbedding` 的调用壳） |
| 评测 | `eval/{RetrievalEvalSet,RetrievalEvalRunner,ToolCallEvalRunner}` | ✅ | 同一套标注集与同一套指标定义 |
| 常量 | 空间戳、默认阈值 0.5、6 类升级信号、前缀守卫 60 字符 | ✅ | **不变量**，绝不允许各端各写一份 |
| UI | `ui/{ChatScreen,ChatViewModel,RetrievalSources}` | ❌（原生各写） | owner 决策：体验优先 |
| 传输实现 | `net/HttpTransport` 的 OkHttp 实现 | ❌（端口 + 各端实现） | 平台网络栈 |
| 凭证 | `device/{CredentialStore, KeystoreCredentialStore, CredentialCodec}` | ⚠️ 接口共享 / 实现各端 | Keystore / Keychain / HUKS / 文件(0600) |
| 存储 | `rag/SqliteVectorStore`（`android.database`） | ⚠️ 接口共享 / 实现各端 | JDBC / FMDB / relationalStore |
| 嵌入运行时 | `embed/OnnxBgeEmbedding`（JVM API） | ⚠️ 壳共享 / 运行时各端 | ONNX Runtime 各端产物不同 |
| PDF | `ui/PdfExtractor`（PdfBox-Android） | ❌ | iOS=PDFKit、桌面=PDFBox(JVM)、鸿蒙=待查 |
| 设备工具 | `tools/AndroidDeviceTools` | ❌ | 平台能力 + 权限模型 |
| 入口/装配 | `MainActivity` `SekbApp`（容器） | ❌（每端一个薄容器） | 依赖注入点不同 |

**边界一句话**：**"看不见的逻辑"全共享；"看得见的界面"和"平台没有的能力"各写。**

---

## 2. 模块结构（KMP 工程布局）

```
apps/
├── settings.gradle.kts            # include(":shared", ":android:app", ":desktop")
├── gradlew / gradle/              # wrapper 从 apps/android/ 上移（一次性）
├── shared/
│   ├── build.gradle.kts           # kotlin { androidLibrary{}; iosArm64(); iosSimulatorArm64(); macosArm64() }
│   │                              #   ↑ native target 用 -PsekbNativeTargets=true 开关（默认关，见 §5 第 3 步）
│   └── src/
│       ├── commonMain/kotlin/com/sekb/shared/
│       │   ├── core/              # ← 现 android 的 route/ net/ tools/ chat/ rag/ embed/ eval/ model/ edge/ core/JsonX
│       │   ├── ports/             # 端口接口（HttpTransport / CredentialStore / VectorStore / …）
│       │   └── constants/         # 空间戳、阈值、升级信号枚举（不变量集中地）
│       ├── commonTest/kotlin/     # ← 现 207 个用例里可共享的部分（功能 ~158 + 契约 4 + 门面 11 + 格式化 5；见 §2.1）
│       ├── androidMain/           # OkHttp、Keystore、SQLite、ONNX(android)、PdfBox、设备工具
│       ├── iosMain/               # NSURLSession、Keychain、SQLite、ONNX(ObjC/C)、PDFKit、设备工具
│       ├── jvmMain/               # 桌面宿主/单测：OkHttp(JVM)、JDBC SQLite、ONNX(java)、PdfBox、文件存储
│       └── ohosMain/              # 仅当鸿蒙 spike 通过：NAPI 桥接 ArkTS 侧实现
├── android/app/                   # 原生 Compose UI（几乎不动）+ 平台实现装配
├── ios/                           # 原生 SwiftUI UI + Xcode 工程（引入 shared 的 XCFramework）
├── harmony/                       # 原生 ArkUI UI +（若 spike 通过）NAPI 桥
└── desktop/                       # 边缘宿主（headless）与桌面 GUI（见 docs/多端跨端-桌面端方案审计.md）
```

**产出物**：Android→AAR；iOS→**静态 XCFramework**（`iosArm64` + `iosSimulatorArm64`）；
JVM→jar（桌面对共享层是"零边界成本"，这也让**单测跑在 JVM 上最快**）；鸿蒙→`.so` + NAPI 声明（待 spike 确认）。

### 2.1 测试能共享多少（实测，2026-09-21）

`apps/android/app/src/test` 现有 **207 个 `@Test`**（功能 187 + 契约夹具 4 + JSON 门面 11 + 格式化 5）；其中所在文件 import 了
`android.*` / `androidx.*` / `org.json` 的只有 **24 个**（`SekbApiTest` 10、`EdgeLlmClientTest` 7、
`EmbeddingProviderTest` 7），而且这 24 个里的平台依赖**只是用 `org.json` 造测试数据**——
也就是 §6 步骤 2 的"去平台化"做完后，它们可以一起共享。

**结论：≈178 个用例可以直接搬进 `commonTest`**（功能 158 + 契约 4 + 门面 11 + 格式化 5，占 207 的 86%），并在 JVM 上秒级跑完、
在 iOS/鸿蒙上跑同一份断言。这是"逻辑共享"最硬的证据：**行为一致性由测试保证，不靠人抄得仔细**。

> 顺带核对了基线：本轮为拿 §0 的微基准数字跑了一次全量 JVM 单测，
> **当时 182 个用例全部通过**（11 秒；临时基准是第 183 个，跑完已删）。
> 之后依次新增：4 个契约夹具测试（`apps/contract/README.md`）、11 个 JSON 门面专测、5 个格式化专测 → 总量 **207**。

---

## 3. 接口设计（v1）

### 3.1 已有端口（保留现有签名，只做"去 JVM 化"）

现状**已经很干净**：这 5 个接口早就存在，抽层时不用重新设计，只需把实现搬到各端 sourceSet：

```kotlin
// net/HttpTransport.kt（接口已在；把 OkHttp 实现移出 commonMain）
interface HttpTransport {
    fun postJson(url: String, headers: Map<String, String>, body: String, timeoutSeconds: Long = 60): HttpResponse
    fun get(url: String, headers: Map<String, String>, timeoutSeconds: Long = 30): HttpResponse
    fun postJsonStream(url: String, headers: Map<String, String>, body: String,
                       timeoutSeconds: Long = 300, onLine: (String) -> Unit): Int
}
data class HttpResponse(val code: Int, val body: String) { val isOk: Boolean get() = code in 200..299 }

// embed/EmbeddingProvider.kt（接口已在；ONNX 壳各端）
interface EmbeddingProvider {
    val space: EmbeddingSpace          // <模型>@<维度>，跨端一致性的唯一判据
    val isOnDevice: Boolean            // 隐私闸门：false 时不得用于 DEVICE_ONLY 数据
    fun embed(texts: List<String>): List<FloatArray>
}

// rag/VectorStore.kt / rag/DocumentRegistry.kt（接口已在；SQLite 实现各端）
interface VectorStore {
    val space: EmbeddingSpace
    fun upsert(records: List<VectorRecord>)
    fun deleteBySource(sourceId: String): Int
    fun search(query: FloatArray, topK: Int): List<VectorHit>
    fun size(): Int
    fun clear()
    fun all(): List<VectorRecord>
}
interface DocumentRegistry {
    fun upsert(info: DocumentInfo); fun list(): List<DocumentInfo>
    fun get(id: String): DocumentInfo?; fun delete(id: String): Boolean; fun clear()
}

// device/DeviceCredentialStore.kt（接口已在；Keystore/Keychain/HUKS 各端）
interface CredentialStore {
    fun load(): DeviceCredentials?; fun save(credentials: DeviceCredentials); fun clear()
}
```

> **这 5 个接口本身就是"可移植性设计"的证据**：它们的存在让"换成 KMP"变成
> "把实现搬到对应 sourceSet + 换掉 4 类 JVM 依赖"，而不是"重新设计架构"。

### 3.2 新增端口（UI 与平台能力，v1 签名）

```kotlin
// ports/PlatformPorts.kt（commonMain）

/** 文件选择与读取：Android=SAF、iOS=UIDocumentPicker、鸿蒙=picker、桌面=JFileChooser/原生对话框 */
interface FileSourcePort {
    /** 让用户挑文件；返回可读句柄（取消则空） */
    fun pickDocuments(mimeTypes: List<String>): List<PickedFile>
    fun readBytes(file: PickedFile): ByteArray
    fun displayName(file: PickedFile): String
    fun sizeBytes(file: PickedFile): Long
}
class PickedFile(val uri: String, val name: String)   // uri 是各端自己的不透明标识

/** 文本抽取（PDF 等）：Android=PdfBox-Android、iOS=PDFKit、桌面=PdfBox(JVM) */
interface TextExtractPort {
    /** 四类结果必须可区分（M3 定下的产品要求：端侧没有服务端兜底，失败必须说清原因） */
    fun extractPdf(bytes: ByteArray): PdfExtractResult
}
sealed interface PdfExtractResult {
    data class Ok(val text: String, val pages: Int) : PdfExtractResult
    data object NoTextLayer : PdfExtractResult                 // 扫描件：本期不做 OCR
    data object Encrypted : PdfExtractResult
    data class Failed(val reason: String) : PdfExtractResult
}

/** 设备能力（工具）：Android=AndroidDeviceTools、iOS=CoreLocation/CNContactStore、鸿蒙=对应 Kit */
interface DeviceToolPort {
    /** 每个工具声明所需权限；未授权时 registry 必须拦下（三道闸门之一） */
    fun tools(): List<DeviceTool>
    /** 运行时权限申请（UI 需要，但判定仍在共享层） */
    fun requestPermission(permission: String): Boolean
    fun hasPermission(permission: String): Boolean
}

/** 平台环境：时钟、临时目录、随机 ID、日志 */
interface PlatformEnv {
    fun nowMillis(): Long
    fun randomId(): String                 // 替代 java.util.UUID
    fun cacheDir(): String                 // 替代 java.io.File 的落点
    fun log(tag: String, message: String)  // hilog / os_log / Logcat / stdout
}

/** 端侧配置（各端构建期或运行期注入，不进共享层的硬编码） */
data class EdgeConfig(
    val sekbBaseUrl: String,               // 云端
    val edgeBaseUrl: String,               // 宿主/本机 OpenAI 兼容端点
    val edgeModel: String,
    val streamGuardChars: Int = 60,        // 不变量默认值（可覆盖，但默认值只有一份）
    val contextBudgetTokens: Int = 2048,
    val outputBudgetTokens: Int = 512,
)
```

### 3.3 各端实现矩阵（谁实现哪个端口）

| 端口 | Android | iOS | 鸿蒙（若 spike 通过） | 桌面（JVM） |
|---|---|---|---|---|
| `HttpTransport` | OkHttp | NSURLSession | ArkTS `@ohos.net.http`（NAPI 回调） | OkHttp / `java.net.http` |
| `EmbeddingProvider` | ONNX Runtime Android 1.20.0（已有） | ONNX Runtime iOS（C/ObjC，可选 CoreML EP） | ONNX Runtime 自建 OHOS 版 | ONNX Runtime Java（**API 与 Android 同一套，代码近乎照搬**） |
| `VectorStore` / `DocumentRegistry` | `SqliteVectorStore`（已有） | SQLite（FMDB/自写 C 调用） | relationalStore 或 SQLDelight 原生驱动（待验） | JDBC SQLite |
| `CredentialStore` | Keystore AES-GCM（已有） | Keychain | HUKS | `~/.sekb/credentials`（0600）+ 可选钥匙串 |
| `FileSourcePort` | SAF（已有 `DocumentImporter`） | `UIDocumentPickerViewController` | picker | 原生文件对话框 |
| `TextExtractPort` | PdfBox-Android（已有） | PDFKit | 待查（可能暂不支持） | PdfBox（JVM，代码与 Android 近乎相同） |
| `DeviceToolPort` | `AndroidDeviceTools`（已有） | CoreLocation / Contacts | 对应 Kit | 基本为空（桌面无"设备专属"数据） |
| `PlatformEnv` | Logcat / 文件目录 | os_log / caches | hilog | stdout / 用户目录 |

### 3.4 端口设计约束（这几条决定"会不会变慢/变难调"）

1. **粗粒度**：一次调用干完一件事（`extractPdf(bytes)` 而不是"逐页/逐块回调"）。
   跨语言边界开销固定，粗粒度把它摊薄到可忽略。
2. **流式只在一处**：SSE 的 `onLine` 回调**不跨语言边界**——解析在共享层做完，
   只在 UI 需要时把"已完成的段落"交给原生 UI（避免每 token 一次 Swift/Kotlin 调用）。
3. **不泄漏平台异常**：端口实现把 `NSError`/`ArkTS 异常`/`SQLException` 转成领域结果
   （`Result<T>` 或 sealed class），共享层不 `catch` 平台类型。
4. **数据不可变**：端口只传 `data class` / `List` / `ByteArray`，不共享可变对象引用
   （Kotlin/Native 与 Swift/ArkTS 的内存模型不同，共享可变状态是 bug 温床）。
5. **隐私闸门在共享层，且"端侧"必须显式排除远端宿主**：`EmbeddingProvider.isOnDevice` 与
   DEVICE_ONLY 判定都在共享层，**不交给 UI 决定**；并且 `DEVICE_ONLY` 只允许"本设备上的本地运行时"
   （远端边缘宿主收到即拒绝 + 留痕）——这是桌面审计发现的契约漏洞（见
   [`docs/多端跨端-桌面端方案审计.md`](多端跨端-桌面端方案审计.md) §2.6 F-1 与
   [`docs/RFC-多端跨端方案.md`](RFC-多端跨端方案.md) §8 R9）。
6. **不变量集中**：空间戳、阈值 0.5、6 类升级信号、60 字符前缀守卫只在 `shared/constants` 定义一次，
   各端原生 UI 只能**读**，不能各写一份。

---

## 4. 各端怎么用共享层（原生 UI 视角）

### 4.1 Android（原生 Compose，改得最少）

```kotlin
// android/app/build.gradle.kts
implementation(project(":shared"))                 // KMP → AAR
```
`ChatViewModel` 保留在 Android 侧（它是 UI 状态机），但它依赖的编排器来自共享层；
平台实现（OkHttp/Keystore/SQLite/ONNX/PdfBox/DeviceTools）在 `shared/src/androidMain`。

### 4.2 iOS（原生 SwiftUI + XCFramework）

- Gradle 产出 `SharedCore.xcframework`（`iosArm64` + `iosSimulatorArm64`），
  Xcode 里用 **Run Script** 调 Gradle 生成（`scripts/ios.sh` 封装，**计划新增**），再 `import SharedCore`；<!-- check-docs:ignore 计划中的脚本，尚未创建 -->
- SwiftUI 侧写：聊天界面、来源面板、文档列表、设置；平台实现放在 Swift 里**实现 Kotlin 协议**；
- 关键调用形态（示意）：

```swift
let orchestrator = ChatOrchestrator(api: SekbApi(transport: UrlSessionTransport()),
                                    knowledge: KnowledgeIndex(...))
for await chunk in orchestrator.send(text) { /* 更新 SwiftUI 状态 */ }
```
> 说明：`Flow` 到 Swift 需要包装（Kotlin 侧提供 `suspend` 或回调版本更省事）——
> 设计上**共享层对外只暴露 suspend/回调**，不把 `Flow` 当跨语言 API（§3.4 约束 1）。

### 4.3 鸿蒙（原生 ArkUI）

两条路（取决于 spike 结果，见 [`docs/RFC-多端跨端方案.md`](RFC-多端跨端方案.md) §4 D2）：
① **KMP 逻辑 + ArkUI 原生 UI**：Kotlin 编成 `.so`，NAPI 暴露 C ABI，ArkTS 侧调用；
② spike 失败 → ArkTS 重写纯逻辑 + 用**同一套契约夹具**做一致性（§7）。

### 4.4 桌面（JVM）

共享层直接给 JVM target → 桌面端**没有任何跨语言边界**；
边缘宿主（headless）与桌面 GUI 的取舍见 [`docs/多端跨端-桌面端方案审计.md`](多端跨端-桌面端方案审计.md)。

---

## 5. 把"会不会变慢"变成数字（验收项）

不靠感觉，三项都实测并记进 `apps/ios/docs/` 与本文档：

| # | 测什么 | 怎么测 | 通过标准（暂定） |
|---|---|---|---|
| P1 | 共享逻辑的微基准 | **JVM 已测（2026-09-21）**：`PlaneRouter.decide` **1.135 μs**、`ToolCallJson.parse` **0.923 μs**、`Chunking.split(2KB)` **9.040 μs**（183 个单测里跑的临时基准，跑完已删）。**待补**：Kotlin/Native（iOS 模拟器）同项数字 | 单次 **≤ 100 μs**（相对 119ms 端到端 < 0.1%）→ ✅ **JVM 已通过** |
| P2 | 跨语言边界开销 | iOS：Swift 调共享层方法 10 万次的耗时；对比纯 Swift 空实现 | 单次 **≤ 5 μs**（粗粒度端口下一次请求只调十几次） |
| P3 | 包体积增量 | iOS release 产物 加/不加 XCFramework 对比；Android APK 同理 | iOS **≤ 5 MB**、Android **≤ 3 MB**（超出就查是否漏了 dead-code elimination） |
| P4 | 端到端不回归 | 同一批问题在 iOS 模拟器与 Android 模拟器各跑一遍自检 + 检索评测 | 自检项等价、Hit@1/Hit@3/MRR **一致**（同一语料同一模型） |

---

## 6. 迁移路径（8 步，每步都能证伪）

| 步 | 动作 | 验收 |
|---|---|---|
| 1 | **契约夹具先行**：协议/字段、6 类升级信号、隐私边界用例固化成 `apps/contract/*.json`；Android 接上 runner | ✅ **已完成**：186 测试全绿；改夹具立刻红（Gradle 输入已接线） |
| 2 | **依赖去平台化（不建 KMP 模块）**：`org.json` 全量收敛到 `JsonX`；`HttpTransport` 拆接口/实现；`File/UUID/concurrent` 换多平台 API | Android 行为零变化（自检 28 PASS / 2 SKIP、检索数字不变） |
| 3 | 建 `apps/shared`（KMP），把 26 个纯逻辑文件搬进 `commonMain` | ✅ **已完成（2026-09-21）**：**207 单测全绿**（app 模块单测直接测共享层代码）+ 模拟器自检 **28 PASS / 0 FAIL / 2 SKIP**；平台实现仍留在 app 模块，shared 零平台依赖。实测坑：AGP 9 禁止 `com.android.library`+KMP（须用 `com.android.kotlin.multiplatform.library`）；该插件无 `compileSdkMinor`（本机只有 android-36.1 → 仓库内 `.tooling/android-sdk` 自造 `platforms/android-36`）；native target 做成 `-PsekbNativeTargets=true` 开关 |
| 4 | 加 `iosArm64`/`iosSimulatorArm64`/`macosArm64`；iOS 侧端口实现（NSURLSession/Keychain/ONNX/PDFKit）属 M5 | ✅ **编译已通过（2026-09-21）**：`:shared:compileKotlinIosSimulatorArm64` 与 `:shared:compileKotlinMacosArm64` **BUILD SUCCESSFUL**（iOS 编译器查出并修掉 13 处平台泄漏）；⏳ framework 链接 / P1 微基准 / P3 体积待 M5 |
| 5 | Kotlin 升到 ≥2.2.21（owner 已同意）+ AGP 9 对齐；**单独提交 + 回滚 tag** | Android 全量验证复跑：**207 测试** + 自检 28 PASS / 2 SKIP + 检索评测 |
| 6 | iOS SwiftUI 端成形（原生 UI） | 自检项与 Android 等价；P2/P4 出数 |
| 7 | 鸿蒙 spike（3 天，四条验收见 RFC §4 D2） | 通过 → 走 ArkUI 原生 UI；不通过 → ArkTS 重写逻辑 + 契约夹具 |
| 8 | 桌面（headless 宿主优先，GUI 见桌面审计文档） | 手机端能连上桌面宿主并跑通端侧推理，路由日志出现 `edge` 记录 |

---

## 7. 一致性机制（原生 UI 路线下**更**重要）

原生 UI 意味着"各端各写界面"，一旦逻辑也各写一份，"三端行为一致"就只能靠人。
所以下面三件事是**前置条件**，不是收尾工作：

| 机制 | 做什么 | 为什么在原生 UI 路线下更关键 |
|---|---|---|
| **契约夹具（单一事实源）** | 协议/字段/错误码/升级信号/隐私用例 JSON 化，各端 runner 必须全绿 | 各端 UI 不同 → 行为差异更难靠肉眼发现 |
| **共享逻辑（唯一实现）** | 路由/守卫/校验/检索只有一份（§1） | 手抄的第二份逻辑 = 第二套事实 |
| **同一套评测集** | 检索 33 问、意图分类、工具调用，各端跑同一标注集，产出同一张表 | 数字可比，差异可定位到"端口实现"而不是"逻辑实现" |
| **协议 N 方守卫** | `doc_guard` 第 5 项从三方扩成：文档 ↔ 服务端 ↔ 共享层 ↔ 各端壳 | 原生多端后，漏改一端是常态 |
| **不变量集中** | 空间戳/阈值/信号枚举只在 `shared/constants` | 各端 UI 会各自"顺手"加默认值 |

---

## 8. 风险与对策

| # | 风险 | 影响 | 概率 | 对策 / 回退 |
|---|---|---|---|---|
| R1 | **依赖可移植性**：`org.json`/OkHttp/`java.*`/ONNX JVM API 在 iOS 不可用 | 共享层抽不出来 | **高（确定会发生）** | 步骤 2 先做（先"去平台化"再搬），有 182 个测试兜底 |
| R2 | Kotlin/Native framework 体积/启动超预期 | 包体积、冷启动变差 | 中 | P3 实测；开 DCE、静态 framework、裁剪依赖面（只用 coroutines/serialization/okio） |
| R3 | Xcode ↔ Gradle 集成与调试成本 | iOS 开发变慢 | 中 | 一次性脚本化 + 文档化；关键路径保留 Swift 日志 |
| R4 | 鸿蒙 `ohos_arm64` 依赖产物供给不足 | 鸿蒙走不通 KMP | 中 | spike 四条验收；失败退 ArkTS + 契约夹具 |
| R5 | 原生 UI × 4 端的**验证成本**（UI 测试不能共享） | 每轮改动验证变慢 | **高** | 分层验证：共享逻辑跑 JVM 单测（秒级）；各端只跑端口 + 端到端自检 |
| R6 | 只共享"逻辑"却共享得不够（各端仍各写常量/阈值） | 数字不可比 | 中 | §7 的不变量集中 + N 方守卫 |

---

## 9. 工作量（相对 `RFC-多端跨端方案.md` 的更新：UI 改原生后增加）

| 阶段 | CMP 路线（旧估算） | **KMP + 原生 UI（本方案）** | 增量原因 |
|---|---|---|---|
| M4 共享层（含去平台化、契约夹具） | 8–12 天 | **8–12 天** | 不变 |
| M5 iOS | 8–12 天 | **12–16 天** | SwiftUI 原生 UI 要写一遍（聊天/来源/文档/设置） |
| M6 鸿蒙 spike | 3 天 | **3 天** | 不变 |
| M7 鸿蒙端 | 8–12 天 | **12–16 天** | ArkUI 原生 UI + 可能的重写逻辑（spike 失败时） |
| M8 桌面 | **4.5–6.5 天**（审计修正，原估 2–3）+ GUI 后置 | [`多端跨端-桌面端方案审计.md`](多端跨端-桌面端方案审计.md) | 宿主：手机端连上并跑通端侧推理；GUI：待审计结论（且需先有目标 OS 验证手段） |

**主线（iOS）合计 20–28 天**；鸿蒙（含 spike）**15–19 天**。

---

## 10. 待 owner 确认

| # | 决策 | 我的建议 |
|---|---|---|
| K1 | 是否按「KMP 逻辑共享 + 各端原生 UI」推进（本文方案） | ✅ 建议采纳（与 owner 倾向一致，且逻辑漂移风险最低） |
| K2 | 是否接受为此增加的原生 UI 工作量（iOS +4 天、鸿蒙 +4 天） | 取决于体验优先级——若要体验最好，值 |
| K3 | §5 的四项性能/体积验收标准（P1–P4）是否认可为"必须实测" | ✅ 建议认可（把"会不会变慢"变成数字） |
| K4 | 鸿蒙 UI 是否也坚持原生 ArkUI（而非 CMP） | 建议：是（与 iOS 一致；但若 spike 发现 ArkUI 与 KMP 桥接成本过高，再评估） |
| K5 | 桌面 GUI 用原生/CMP/Web 壳 | 见 [`多端跨端-桌面端方案审计.md`](多端跨端-桌面端方案审计.md) 的结论（建议：先做「宿主 + 浏览器入口」，GUI 后置） |
