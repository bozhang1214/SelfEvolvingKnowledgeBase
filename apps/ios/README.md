# apps/ios · iOS 端侧宿主

**状态：功能完整可用**（2026-10-08）：聊天 / 历史 / 文档 / 设置四页 SwiftUI，与 Android `SekbScreen` 同名同顺序；逻辑全部走共享层（KMP），平台端口仍是四个（NSURLSession / Keychain / ONNX Runtime C API / PDFKit）。
自检 **42 PASS / 0 FAIL / 1 SKIP**（SKIP = Keychain 往返，需真实签名身份）。


> ✅ **2026-09-21 定案（UI 用原生 SwiftUI）**：owner 已定 **各端原生 UI**，不用跨端统一 UI。
> 下面 M4 计划里的「SwiftUI 界面」**保持原样**（这正是最终方案）；逻辑层抽成 KMP 共享模块
> （`apps/shared`），iOS 侧直接用这份 Kotlin（KMP 的 Swift interop）。
> CMP 的评估结论归档在 [`docs/多端跨端-CMP方案评估.md`](../../docs/多端跨端-CMP方案评估.md)（仅作备选）。
> 四端范围与顺序见 [`docs/RFC-多端跨端方案.md`](../../docs/RFC-多端跨端方案.md) v1.0。


---

## 四页 UI（2026-10-08 本期）

### 交付了什么

| 页面 | 内容 | 与 Android 的对齐方式 |
|---|---|---|
| **聊天** | 用户右/主色气泡、助手左/白卡、**流式逐字**（`ChatOrchestrator` + `SseParser`，真流式不是伪流式）、执行位置徽章（本机=绿 / 云端=金）+ 可展开的**原因解释**、引用来源列表、思考态、空状态示例问题、底部输入栏、Markdown 渲染 | 同名同序，同一套共享层调用；`PlaneExplain` / `RetrievalSources` / `Markdown` 是 `ui/*.kt` 的**逐行移植**（正则、分支顺序、"未闭合标记降级为文本"都一样） |
| **历史** | 会话列表（相对时间 + 条数 + 当前对话标记）、点开切换、删除、**当前会话指针**（"新对话"后重启不会跳回旧对话） | 落盘格式复用共享层 `ConversationCodec`（两端同一份 JSON）；`FileConversationStore` 与 Android 的 `chat/FileConversationStore.kt` 三条行为一致：坏文件跳过、上限 50 段、原子写 |
| **文档** | 导入（文本 + **PDF 走 PDFKit**，四类结果各有可读原因）、分块入库（共享层 `Chunking` + `KnowledgeIndex`）、列表（片段数 / 人类可读体积）、**单篇删除不误清全库** | PDF 语义与 Android 的 `PdfExtraction` 四类一一对应；`FileKnowledgeStore` 同时实现 `VectorStore` + `DocumentRegistry`（对应 Android 的 `SqliteVectorStore`） |
| **设置** | 设备 enroll（`SekbApi` 语义 + Keychain）、连接状态与高级地址、**检索阈值调节（默认 0.40）**、端侧嵌入状态（模型/空间戳/维度/是否本机）、隐私与权限审计、**自检入口（可重跑 + 明细）**、策略/版本 | 分组顺序与文案照 `ui/SettingsScreen.kt`；阈值默认值差异见下文（**不是各端一个口味**） |

**一份源码两端跑**：`apps/ios/App/*.swift` 同时编 iOS 与 macOS（`apps/mac/README.md` 记录了与 iOS 的**有意差异**）。
当前规模 <!-- fact:ios_app_files=25 -->**25 个文件** / <!-- fact:ios_app_lines=5356 -->**5,356 行**
（`scripts/check_edge_facts.py` 会按实测校验这两个数；改代码后若不一致，`doc_guard.sh` 第 7 项会红）。
`#if os(...)` 只出现在两处：自检横幅前缀/`RootView` 的最小窗口尺寸，以及 Keychain 的 macOS 语义差异。

### 复现命令与原始输出

```bash
bash scripts/ios_app.sh run    # 装到 iPhone 17 Pro（iOS 26.3）模拟器并抓自检
bash scripts/mac_app.sh run    # macOS 同源跑，同一张自检表
bash scripts/ios_app.sh ui     # 逐页截图 → apps/ios/docs/screenshots/
bash scripts/mac_app.sh ui     # macOS 逐页渲染 → apps/mac/docs/screenshots/
```

> **日志抓取用 `log stream`，不再用 `log show --last`**（这轮踩到的坑）：后者要把时间窗内的历史日志
> 整段读出来，本机负载高时实测要**几十分钟**，还会因为时间窗过期而报"没抓到总结行"
> （真实发生过：一次 `run` 卡了 54 分钟）。`log stream` 是增量输出、秒级返回；
> 脚本先接流再启动 App（否则漏掉开头几行），并保留 `log show` 作为兜底。
> ⚠️ 接流的子进程必须**直接后台起 `xcrun`**（不能套 `( ... ) &` 子 shell，否则 `$!` 是子 shell，
> kill 掉它之后真正的 `log stream` 会变成孤儿一直跑）。

自检汇总（两端**同一张表**，原始行）：

```
SEKB_IOS_SELFTEST PASS=42 FAIL=0 SKIP=1
SEKB_MAC_SELFTEST PASS=42 FAIL=0 SKIP=1
```

> **构建口径（完整路径，未跳过任何步骤）**：`bash scripts/ios_app.sh run` 会先重编
> `iosSimulatorArm64` 的 `SharedCore.framework`（本轮实测 framework `Oct 9 01:16` 重新链接），
> 再编 App、装到模拟器、启动并抓日志。macOS 侧同理（framework `Oct 9 02:0x`）。
> 收尾期间共享层一度被其他工作流编辑到中间态（`SekbApi.kt` 的 KDoc 提前闭合、
> `EmbeddingProvider.kt` 的 override 多出 `@Throws` 过滤条件），那两个中间态下编不过；
> 主线修好后（`SekbApi` 八个方法补 `@Throws`、`HostOllamaEmbedding.embed` 改成
> "捕获→同数量零向量 + `lastError`"）**已用完整路径复跑并得到上面的行**。
> 复跑对照：修 `ui_doc_delete_isolated`（它自己用错了层级——应走 UI 用的
> `KnowledgeIndex.remove`，不是存储层的 `deleteBySource`）之前是 41 PASS / 1 FAIL，现在是 42 / 0。

关键项（**与 Android 逐项相同**，不允许回归）：

```
embed_onnx_sanity   — 无关文本余弦=0.244（应明显 <1） L2范数=1.0000/1.0000（应为 1）
rag_retrieve        — 命中=1 首条分=0.630
rag_eval_calibrate  — 阈值=0.20 Hit@1=26/30( 87%) Hit@3=30/30 MRR=0.928 误召回=3/3(100%)
                      阈值=0.30 Hit@1=26/30( 87%) Hit@3=30/30 MRR=0.928 误召回=2/3( 67%)
                      阈值=0.40 Hit@1=26/30( 87%) Hit@3=30/30 MRR=0.928 误召回=0/3(  0%)
                      阈值=0.50 Hit@1=26/30( 87%) Hit@3=29/30 MRR=0.911 误召回=0/3(  0%)
                      阈值=0.60 Hit@1=24/30( 80%) Hit@3=24/30 MRR=0.800 误召回=0/3(  0%)
ui_data_dir_writable — 来源=SEKB_DATA_DIR 路径=…/.tooling/tmp/ios-app-data
ui_doc_delete_isolated — 删除前=2 删除=1 剩余=1 重开=1 剩余文档=doc-b
bridge_http_error_is_swift_error — 401 → Swift 错误码=401（进程存活）
```

### 视觉验证（截图 + 我看到的）

无人值守下没法点界面，所以 `ui` 动作靠两个**只读环境变量**把应用"摆"到要拍的状态
（`SEKB_INITIAL_SCREEN` / `SEKB_DEMO_SEED`，都默认关闭）：

| 图 | 证明了什么 |
|---|---|
| `apps/ios/docs/screenshots/01-chat-empty.png` | **空状态不挤压输入栏**（Android 真机上正是这个 bug：整屏只有空状态、用户没法输入）。图里输入栏与发送按钮完整可见 |
| `apps/ios/docs/screenshots/02-chat.png` | 气泡左右分栏、**Markdown 渲染**（标题/列表/代码块/引用）、`引用 2 段本机资料`、绿色 `本机完成 · qwen3.5-2b` 与金色 `已上云（端侧不达标）· qwen3.5-4b` 两种徽章、顶栏标题=恢复出来的会话名、文档角标=2 |
| `apps/ios/docs/screenshots/03-history.png` | 会话列表：2 段、相对时间（`16 分钟前` / `19:29`）、条数、`当前对话` 标记、删除入口 |
| `apps/ios/docs/screenshots/04-documents.png` | 文档列表 `sample-text.pdf`（**真走 PDFKit 抽取**，744 B / 1 段）与 `端侧 RAG 说明.md`（320 B / 1 段）、导入按钮 |
| `apps/ios/docs/screenshots/05-settings.png` | 账号与设备、连接状态（**绿点：端侧可用**）、检索阈值 0.40 + 来源、端侧嵌入（`ONNX fp32 空间=BAAI/bge-small-zh-v1.5@512 维度=512 本机计算=true`）、隐私与权限 |

macOS 侧另有一套 `apps/mac/docs/screenshots/*.png`（同一份源码、同一套开关；取图方式与局限见 `apps/mac/README.md`）。

> 截图的取得时间：iOS 为 2026-10-09 00:16–01:04（同一轮验证里的三次 `ui` 启动），macOS 为 00:48–00:49。
> 之后对源码做过的唯一改动是 `ConversationListScreen` 的一处**交互**修正
> （整行由 `Button` 改成 `onTapGesture` —— SwiftUI 里嵌套 Button 会吞掉删除按钮的点击），
> **外观未变**，所以这些图仍然是当前界面的准确呈现。

### 新增的自检项（12 条，全部可证伪）

本期**没有加"永远 PASS 的空检查"**——顺手把一条旧的空检查改成了真断言：

| 自检项 | 断言什么 |
|---|---|
| `retriever_default_threshold` | **原来写死 `ok: true`**（常量改了也不会红，等于不存在）→ 现在断言默认阈值 == 0.40 |
| `ui_screens_align_android` | 四页名字与顺序 == `chat → history → documents → settings` |
| `ui_markdown_blocks` | 解析出 `h1,p,bullet2,num2,quote1,code:swift:…,table2x1,divider`（8 类块各一条） |
| `ui_markdown_inline` | 粗体/行内码/链接各归各位，且 `**没闭合` 降级为普通文本（流式半句话不能丢字） |
| `ui_plane_explain_known` | `output_over_edge_budget(600>512)` 被翻成人话（含具体数字） |
| `ui_plane_explain_unknown_passthrough` | 不认识的原因码**原样透出**（`原因：some_future_code_v9`），不返回"未知原因" |
| `ui_plane_explain_escalated_not_contradictory` | 升级场景解释以"端侧没能达标"开头，且 ` · ` 分隔的多个原因码**没有被前缀规则吞掉** |
| `ui_sources_parse` | 从 `Retriever.formatForModel` 的文本里解析出出处与分数；失败的工具结果被跳过 |
| `ui_time_relative` | `刚刚 / 5 分钟前 / 3 天前` 三段降级 |
| `ui_data_dir_writable` | 数据目录**真的写进去一个文件**（不是"存在就算"；受限沙箱里 Application Support 是被拒的） |
| `ui_conversation_roundtrip` | 会话 save→load 往返（含 plane/reason/sources），**坏文件被跳过**且列表仍可打开 |
| `ui_doc_delete_isolated` | 删 A 不影响 B：切片与元信息一起删、**重开索引后 B 仍在**（"删一篇清空全库"是曾经的真缺陷） |
| `shared_login_token_fields` / `shared_enroll_credentials` / `shared_sse_events` / `shared_route_event_reported` / `shared_http_error_is_catchable` | **共享层 `SekbApi` 在 Apple 侧真的能跑通**：字段名解析、SSE 装配（正文/思考/执行位置/会话 id）、请求体由共享层生成、**401 变成可捕获的错误而不是 abort** |

### ✅ 曾经的共享层缺口已闭合：`SekbApi` 的 `@Throws`（2026-10-26）

**这一段留着，因为它解释了"为什么曾经有一份 248 行的 Swift 重复实现"，也留下了判据。**

历史事实（本机实测，macOS 框架，`SekbApi.login` 收到 HTTP 500）：

```
Function doesn't have or inherit @Throws annotation and thus exception isn't propagated
from Kotlin to Objective-C/Swift as NSError ... Program will be terminated.
Uncaught Kotlin exception: com.sekb.shared.net.SekbApiException: SEKB 500: probe-500
Abort trap: 6
```

即：**只要服务端返回非 2xx（token 过期 401 很常见）或 SSE 出现 `error` 事件，Apple 侧当场 abort**。
当时为了不崩，Apple 侧抄了一份客户端（`SekbApiBridge.swift`，248 行）——
协议语义仍是共享的（SSE 解析/凭证/JSON 都走共享层），但**HTTP、状态码与字段名成了第二处维护点**。

主线已给 `SekbApi` 的 8 个公开方法补 `@Throws(SekbApiException::class)`，生成的 ObjC 头可以逐条验证：

```objc
- (NSString * _Nullable)loginEmail:... error:(NSError **)error
    __attribute__((swift_name("login(email:password:)")));
// @note This method converts instances of SekbApiException to errors.
```

于是：
- **`SekbApiBridge.swift` 已删除**；`SekbContainer` 改用 `SekbApiShim`——
  它只做 `throws → Result` 的转换，**不含任何协议知识**（判据：里面一旦出现 `/api/v1/` 或字段名字符串，
  就说明又长回第二套事实了）。
- 自检项改为直接打共享层（上表 5 条），其中 `shared_http_error_is_catchable` 就是"`@Throws` 被去掉就会红"的守卫
  （去掉后会退化成 `Abort trap: 6`，进程直接死，自检汇总行根本不会打印）。
- 两处**顺手发现的真问题**（都已修）：Kotlin 的**默认参数不跨语言**（`SekbApi(transport:baseUrl:now:)`
  三个都必须显式给，`now` 要 `KotlinLong(longLong:)`）；以及**截断取样当断言**会产生假失败
  （路由事件请求体截到 80 字符时，`escalated`/`input_tokens` 落在后面 → 误判 FAIL）。

### 与 Android 的**有意**差异（不是遗漏）

| 项 | Android | iOS/Mac | 为什么 |
|---|---|---|---|
| 检索阈值默认 | `0.5` | **`0.40`** | 不是"各端一个口味"，而是**两套嵌入空间各自的标定点**：iOS/Mac 加载 fp32（0.40 → 命中率不降、误召回 0/3；0.20 时误召回 3/3），Android 优先 int8（分数分布上移，0.5 才是 87%/100%/0-3）。阈值必须按**实际加载的空间**标定 |
| 端侧工具 | 4 个（time / network / contacts / kb_search） | **2 个**（`device_time` / `kb_search`） | `device_contacts_search` 要通讯录权限与授权流程、`device_network` 要 `Network.framework` 的异步路径监测；本期 DoD 不含设备工具，硬做等于给用户一个"点了没反应"的入口。`config.availableTools` 由**注册表算出来**，所以不会出现"模型以为有、实际没有" |
| 向量存储 | SQLite（`SqliteVectorStore`） | 单个 JSON（`FileKnowledgeStore`） | 端侧语料"小而准"（几十份/几百切片），JSON 读写成本可忽略，而**零额外依赖**在受限构建环境里更划算；接口与语义完全一致，换实现只影响这一个文件 |
| 数据目录 | 应用私有目录 | `SEKB_DATA_DIR` → Application Support → 临时目录（自检断言可写） | 受限沙箱里 `~/Library/Application Support` 与模拟器容器目录实测都是 `Operation not permitted`，按惯例写会**静默不落盘**；脚本显式指到 `.tooling/tmp/`，可复核可清理 |
| 执行位置徽章配色来源 | Compose `LocalSekbSemantic` | `@Environment(\.sekb)` 调色板 | 明暗两套都在 `SekbPalette` 里，页面**不出现硬编码色值**（Android 那次"深色模式只在主题文件里成立"的缺陷就是硬编码色值造成的） |

### 未验清单（如实列）

- **iOS 真机**：未验（无设备）。模拟器不产出性能结论。
- **Keychain 往返**：SKIP（模拟器未签名包 → `-34018`；ad-hoc 签名 → SpringBoard 拒启动，两条路互斥，见下文 M5 记录）。
- **enroll / refresh 的真实云端往返**：**未验**——需要真实账号与可达的 `SEKB` 后端。桥的解析与错误路径用协议文档样例报文验过（自检 5 条），但"真的换到设备凭证"没验。
- **聊天走真实云端 SSE**：**未验**。端侧链路（宿主 Ollama → 路由 → 守卫 → 流式）已在 `transport_*` 与自检里验到传输层；云端 SSE 的端到端要在有后端时补。
- **`DEVICE_ONLY` + 真机设备工具**：未验（Apple 侧未注册需要权限的工具，闸门路径只在共享层单测覆盖）。
- **鸿蒙侧**：不属于本工作流。
- **两个 ORT 会话**（已知低效，未优化）：UI 容器与启动自检**各建一个 `OrtBgeEmbedding`**，
  于是端侧嵌入模型被加载两次（内存约 2×，bge-small fp32 约 90MB）。这是"自检自我封闭"与
  "UI 复用同一容器"两条诉求的取舍结果，不影响正确性；要收敛得让自检复用容器实例（下一轮可做）。

---

## M5 进展（2026-09-21）

| 步骤 | 状态 | 证据 |
|---|---|---|
| KMP 共享层编到 iOS/macOS | ✅ | `-PsekbNativeTargets=true` 下 `:shared:compileKotlinIosSimulatorArm64` / `compileKotlinMacosArm64` 通过 |
| framework 产出 | ✅ | `:shared:linkDebugFrameworkIosSimulatorArm64` / `linkDebugFrameworkMacosArm64` → `SharedCore.framework`（`isStatic = true`） |
| **Swift 互操作冒烟** | ✅ | `bash scripts/ios.sh smoke` → **SMOKE OK（5/5）**：Apple 侧 HMAC/SHA256 对齐公知向量、canonical JSON 与 Python 一致、`ToolCallJson.parse` 的 sealed 类导出可用、`Fmt` 与 Android 逐字符一致 |
| **iOS App 骨架（SwiftUI）** | ✅ | `bash scripts/ios_app.sh run` → 装到 iPhone 17 Pro 模拟器并启动，日志抓到 **`SEKB_IOS_SELFTEST PASS=8 FAIL=0`**：HMAC 向量、规范化 JSON、路由决策、**R10 本机/远端两条对照**、**流式守卫零外泄**、工具调用解析、检索阈值默认值 |
| **iOS 端口①：传输（NSURLSession）** | ✅ | `apps/ios/App/UrlSessionTransport.swift`：实现共享层 `HttpTransport`（同步语义用信号量、SSE 用 `URLSessionDataDelegate` 逐行回调）。自检实测：`transport_get_host_ollama — code=200 bytes=3474`、`transport_stream_lines — code=200 行数=9`；**iOS 自检 10 PASS / 0 FAIL** |
| **iOS 端口②：凭证（Keychain）** | ⚠️ 实现完成 / 往返未验 | `apps/ios/App/KeychainCredentialStore.swift` 实现共享层 `CredentialStore`（接口与 `CredentialCodec` 本轮**一起搬进共享层**，两端同一格式）。自检：空库→nil ✅、清理→nil ✅、轮换规则（共享层 `needsRotation`）✅；**往返 SKIP 且原因明确**：手搓未签名 `.app` 调 Keychain 返回 **-34018 errSecMissingEntitlement**，而 ad-hoc 签名（带任何 entitlements）会让模拟器 SpringBoard **拒绝启动**——两条路互斥。需真实 Xcode 工程 + 签名身份或真机时补验；entitlements 文件已备好在 `apps/ios/App/Sekb.entitlements` |
| **检索链路（RAG）** | ✅ | 分块 → 嵌入 → 向量库 → 检索 → 阈值 → 隐私闸门，**全部走共享层代码**。自检：`rag_ingest`（1 切片，空间 `stub-hash@256`）、`rag_retrieve`（命中 0.244）、`rag_threshold_refuses`（`below_threshold:最高分=0.180<0.99`）、`rag_device_only_gate`（`device_only_requires_on_device_embedding`）；**iOS 自检 20 PASS / 0 FAIL / 1 SKIP** |
| **iOS 端口③：嵌入（ONNX Runtime）** | ✅ | `apps/ios/App/OrtBgeEmbedding.swift`：ORT **C** API（Android 走 Java API，这是两端唯一的实现差异）。自检：`embed_onnx_session`（模型=fp32 空间=`BAAI/bge-small-zh-v1.5@512` 维度=512）、`embed_onnx_sanity`（无关文本余弦 **0.244**、L2 范数 **1.0000/1.0000**）、`embed_onnx_deterministic`（两次嵌入逐位差 **0.00e+00**）；`rag_retrieve` 首条分 **0.630**。**iOS 自检 26 PASS / 0 FAIL / 1 SKIP** |
| **iOS 端口④：PDF（PDFKit）** | ✅ | `apps/ios/App/PdfExtractor.swift`：与 Android 同一套**四类结果**（有文本 / 无文本层 / 加密 / 解析失败）+ 先判 `%PDF` 魔数 + 40 万字符上限。自检实测：`pdf_extract_text_layer — 页数=1 字符=78`（**与 Android 端同一份样本的 78 字符/1 页完全一致**）、`pdf_extract_no_text_layer`、`pdf_extract_rejects_non_pdf`；**iOS 自检 13 PASS / 0 FAIL** |
| iOS 真机性能 | ⏳ 等硬件 | 模拟器只验功能 |
| 四个端口（NSURLSession/Keychain/ONNX/PDFKit） | ⏳ | 待 App 骨架跑通后补 |

```bash
bash scripts/ios.sh smoke        # 产出 framework + 编译并运行 Swift 冒烟
bash scripts/ios.sh framework    # 只产出 iOS 模拟器 + macOS 的 framework
```

**三个必须知道的环境事实（都踩过，脚本里已封装）**：
1. `DEVELOPER_DIR` **必须**指向 Xcode（`/Applications/Xcode.app/Contents/Developer`）：
   本机 `xcode-select` 指向 CommandLineTools，不设它会报
   `An error occurred during an xcrun execution / xcrun xcodebuild -version`——
   **表面像 klib 缓存错误，实则是 Xcode 不可用**（我第一次就被它误导）；
2. `KONAN_DATA_DIR` 指向仓库内 `.tooling/konan`（Kotlin/Native 工具链默认写 `~/.konan`，工作区外会被沙箱拒）；
3. `CLANG_MODULE_CACHE_PATH` + `-module-cache-path` 也要指向仓库内（Swift/clang 默认写
   `/var/folders/.../C/clang/ModuleCache`，同样报 `Operation not permitted`）。

## 本机工具链（2026-09-18 实测）

| 项 | 实测值 | 备注 |
|---|---|---|
| Xcode | **26.6**（Build 17F113），`/Applications/Xcode.app` | 已安装 |
| iOS 模拟器运行时 | **26.3 / 26.4** | `xcrun simctl list runtimes` |
| `swift` / `swiftc` | `/usr/bin/swift`、`/usr/bin/swiftc` | 可用 |
| ⚠️ 活动开发者目录 | 指向 `CommandLineTools`（**不是** Xcode） | 直接跑 `xcodebuild` 会报 "requires Xcode" |

两种解法（选一）：

```bash
# A. 临时（无需 sudo，脚本里用这个）
export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
xcodebuild -version

# B. 永久（需要 sudo，改全局状态）
sudo xcode-select -s /Applications/Xcode.app/Contents/Developer
```

`scripts/android.sh` 里已经顺手导出了 `DEVELOPER_DIR`，将来 iOS 构建脚本可直接复用。

## 计划（M4）

1. **先有 `shared/`**：把 `apps/android` 里那批**不 import android.\*** 的纯逻辑
   （`route/` `net/SseParser` `tools/ToolCallJson` `tools/ToolRegistry` `chat/ChatOrchestrator`
   `eval/`）抽成 Kotlin Multiplatform 模块，产出 Android + iOS 两个 target。
   iOS 侧直接用这份 Kotlin（KMP 的 Objective-C/Swift interop），**不要重写一遍**。
2. SwiftUI 界面 + 平台适配：`URLSession` 传输（实现 `HttpTransport`）、
   Keychain 存设备凭证（对应 Android 的 Keystore 实现）、
   iOS 设备工具（时间/网络/通讯录，权限用 `CNContactStore` 授权状态判断）。
3. 验证策略与 Android 一致：**纯逻辑走单测（KMP 里跑）+ 模拟器验功能与协议**，
   性能数字等真机（模拟器不产出性能结论，见 SEKB RFC §9.1）。

**iOS 侧又踩到的四个坑（`scripts/ios_app.sh` 已封装）**：
1. `simctl launch` 对**已在运行**的 App 只是切到前台，`onAppear` 不再触发 → 自检挪到 `App.init()`，
   且 launch 前先 `simctl terminate`（否则无人值守抓不到任何输出）；
2. 但 `App.init()` 里**不能同步阻塞主线程**等网络（信号量 + URLSession 回调会拿不到执行机会，
   表现为"日志里只有 trustd 请求痕迹、永远等不到自检输出"）→ 自检放 `DispatchQueue.global().async`；
3. 自检含真实网络调用（宿主 Ollama 冷加载可能十几秒）→ 抓日志前等 45s、窗口 90s；
4. 测流式传输时**必须给生成加上限**（`num_predict`）：否则模型长篇生成，
   60s 超时前流不结束，`statusCode` 拿不到（表现为 `code=-1` 而"行数"上万）——
   测的是传输，不该被模型行为影响。`URLSession` 的 delegate 与完成回调**跨队列**，
   状态码要在 `didReceive response` 里加锁记录，不能只在 `didCompleteWithError` 读。

**端口②的一个硬结论（2026-09-21 实测）**：模拟器上"手搓 `.app`"与"Keychain 可用"**互斥**——
未签名 → `SecItemAdd` 报 `-34018`；`codesign -s - --entitlements ...`（哪怕只带
`application-identifier`）→ SpringBoard 拒绝启动（`FBSOpenApplicationServiceErrorDomain code=1`）。
当前选择保住"能启动"（其余自检都依赖它），因此 iOS 自检采用**三态**（PASS/FAIL/**SKIP**，
与 Android 侧自检同口径）："没验"既不算过、也不算失败，原因写进详情。

### 端口③（端侧嵌入）调研结论（2026-09-21 调研，**2026-09-22 解决**）

| 方案 | 现状 | 结论 |
|---|---|---|
| 宿主 Ollama 嵌入（`HostOllamaEmbedding`） | 宿主**没有装任何嵌入模型**（`/api/tags` 只有 qwen3.5/deepseek/llama2 等对话模型） | 需先拉一个嵌入模型；而本机到 ollama registry 的国际链路此前实测不可用（`scripts/edge_m0_setup.sh` 记录了 ModelScope 绕行方案） |
| ONNX Runtime iOS（`onnxruntime-c`） | ✅ **2026-09-22 解决** | 见下方"来源订正" |
| Core ML（把 bge-small 转 coreml） | 需要 `coremltools` 转换环境 + 模型转换验证 | 不再需要：ORT 已到手，Core ML 只作为将来"用 ANE 加速"的可选优化 |

#### 来源订正（2026-09-22）——**ORT 的 iOS 产物不在 GitHub 上**

2026-09-21 的结论「ORT iOS 被网络卡死」是**误判**：当时只试了 GitHub Release（`github.com` 实测 HTTP 000），
就推断唯一来源是 GitHub。实际查 CocoaPods 规格库后真相是：

```bash
# CocoaPods CDN 会 301 到 jsdelivr，用 -L 跟随（不带 -L 会看到 301 而误以为失败）
h=$(echo -n onnxruntime-c | md5 -q)   # 3aa95c6f…，规格库按名字 md5 的前 3 个字符分片
curl -sL "https://cdn.cocoapods.org/Specs/${h:0:1}/${h:1:1}/${h:2:1}/onnxruntime-c/1.20.0/onnxruntime-c.podspec.json"
# → source.http = https://download.onnxruntime.ai/pod-archive-onnxruntime-c-1.20.0.zip
curl -L -o ort-c-1.20.0.zip https://download.onnxruntime.ai/pod-archive-onnxruntime-c-1.20.0.zip
```

- 该 URL 实测 **HTTP 200 / 44,218,716 B**，**完全绕开 GitHub**，也不需要装 CocoaPods；
- 落盘 `.tooling/ort-ios/`（构建状态，不入库），sha256 `50891a8aadd17d4811acb05ed151ba6c394129bb3ab14e843b0fc83a48d450ff`；
- `onnxruntime.xcframework` **三个切片齐全**：`ios-arm64`(真机 33M)、`ios-arm64_x86_64-simulator`(模拟器 71M)、
  `macos-arm64_x86_64`（**顺带解锁 M8 Mac 端**）；
- `nm -gU` 确认模拟器切片导出 `_OrtGetApiBase`，头文件 `ORT_API_VERSION 20` 与 1.20.0 匹配 → 可直接链接。

**经验（写在这里避免重犯）**：判断"某产物被墙"之前，先确认**唯一来源**是不是 GitHub——
CocoaPods 系产物真实托管在 `download.onnxruntime.ai`，Maven 系在 `repo1.maven.org`/镜像，
两者都与 GitHub 无关。另外 `curl` 查这类 CDN 必须带 `-L`。

#### 接线结果（2026-09-22，已完成）

`apps/ios/App/OrtBgeEmbedding.swift` + `apps/ios/App/SekbOrtBridge.h`（桥接头只做
`#import <onnxruntime/onnxruntime_c_api.h>`），`scripts/ios_app.sh` 负责 `-F <模拟器切片>
-framework onnxruntime -lc++ -import-objc-header …` 并把模型拷进 bundle。

踩到的三个坑（都写进了代码注释，避免重犯）：

| 坑 | 症状 | 根因 |
|---|---|---|
| **`AllocatorFree` 顺序反了** | 报"ONNX 模型出现了未预期的输入："（名字是空的），向量全零 | 先 `AllocatorFree(cName)` 再 `String(cString: cName)` → **读已释放内存**。必须**先转字符串、后释放** |
| **`embed` 失败返回空数组** | App **当场崩溃**：`EXC_BREAKPOINT`，栈顶 `__SwiftNativeNSArrayWithContiguousStorage._objectAt` ← `Kotlin_NSArrayAsKList_get` ← `KnowledgeIndex.ingest` | 共享层 `ingest` 会**按切片数索引**嵌入结果；Swift 返回短列表 → Kotlin 侧下标越界。且 `EmbeddingProvider.embed` 在 Kotlin 里**没有 throws**，Swift 无法抛错 → 只能返回**同数量的零向量**（余弦 0 → 必然不命中，安全失败）并置 `lastError` |
| **`OrtStatus` 是不完整类型** | 编译报 `cannot find type 'OrtStatus' in scope` | 该头文件只用 `OrtStatus*`、从不定义结构体 → Swift 侧映射为 `OpaquePointer`；`OrtAllocator*` 反而是具体类型 |

#### 验收：评测数字与 Android **逐项一致**（RFC §4.5-F 的"同一张表"）

```
Android（apps/android/docs/RETRIEVAL-EVAL.md，真机 fp32）
阈值=0.40  Hit@1=26/30( 87%)  Hit@3=30/30  MRR=0.928  误召回=0/3(  0%)  嵌入均 38ms(p95 57ms)  检索均 0.5ms
iOS（模拟器 fp32，on-device ONNX）
阈值=0.40  Hit@1=26/30( 87%)  Hit@3=30/30  MRR=0.928  误召回=0/3(  0%)  嵌入均  2ms(p95  3ms)  检索均 0.1ms
```

| 阈值 | Hit@1 | Hit@3 | MRR | 误召回 | 两端一致 |
|---|---|---|---|---|---|
| 0.20 | 26/30 (87%) | 30/30 | 0.928 | 3/3 (100%) | ✅ |
| 0.30 | 26/30 (87%) | 30/30 | 0.928 | 2/3 (67%) | ✅ |
| 0.40 | 26/30 (87%) | 30/30 | 0.928 | **0/3** | ✅ |
| 0.50 | 26/30 (87%) | 29/30 | 0.911 | 0/3 | ✅ |
| 0.60 | 24/30 (80%) | 24/30 | 0.800 | 0/3 | ✅ |

**五个阈值的排序指标全部逐项相同**，说明两端嵌入确实在**同一个语义空间**
（换实现若悄悄换了空间，Hit@1 会变而不会"恰好一致"）。**唯一差异是延迟**——
模拟器跑在 M5 Pro 宿主上（2ms），Android 那组是真机（38ms）；
iOS 真机性能仍在缺口表里（等硬件），不能拿模拟器数字充当真机结论。



## 与 SEKB 的契约

只依赖 [协议文档](../../docs/ops/16-端云协同协议.md)：设备凭证（enroll/refresh/吊销）、
聊天 SSE（含 `execution` 执行位置）、路由事件上报、6 类升级信号。
**协议改了就要同步改这里和三端实现**——这是把它们放进同一个仓库的主要原因。
