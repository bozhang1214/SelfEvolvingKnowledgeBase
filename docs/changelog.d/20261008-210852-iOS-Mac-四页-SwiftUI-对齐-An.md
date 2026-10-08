## 2026-10-08（iOS-Mac 四页 SwiftUI 对齐 Android：聊天-历史-文档-设置 + 共享层 @Throws 缺口）

- **背景**：iOS/Mac 此前只有"四端口 + 自检页"（M5/M8 骨架）。四端「功能可用」的 DoD
  （`docs/RFC-多端跨端方案.md` §2.3 F1–F10）要求**用户可用的界面**，而 Android 侧四页
  （聊天/历史/文档/设置）已经落地——两端不对等，"功能可用"就只对了一半。
  这一轮把 iOS 与 Mac 用**同一份 SwiftUI 源码**补齐到与 Android 同级，并把"逻辑一份"这条
  纪律在 UI 层也守到底（解析/解释/来源解析都是 Android `ui/*.kt` 的**逐行移植**，不是另写一套）。

- **改动**（主改动集中在 `apps/ios/App/`，新增 19 个文件）：
  - **四页 UI**：`AppShell`（`screen` 状态 + 顶栏返回导航，与 Android `SekbScreen` 同名同序，
    不用 TabView）、`ChatScreen`、`ConversationListScreen`、`DocumentsScreen`、`SettingsScreen`。
    聊天走**真流式**（`ChatOrchestrator` + `SseParser`，不是伪流式），含执行位置徽章
    （本机=绿 / 云端=金）+ 可展开的原因解释、引用来源列表、思考态、空状态示例问题、底部输入栏、
    Markdown 渲染（标题/段落/列表/引用/代码块/表格/分隔线 + 行内粗体/码/链接）。
  - **与 Android 逐行对齐的三个 UI 语义模块**：`SekbMarkdown`（正则与分支顺序照 `ui/Markdown.kt`，
    含"未闭合标记降级为普通文本"）、`PlaneExplain`（认不出的原因码**原样透出**，绝不返回"未知原因"）、
    `RetrievalSources`。
  - **平台端口补齐**：`FileConversationStore`（一会话一 JSON、坏文件跳过、上限 50、原子写，
    落盘格式复用共享层 `ConversationCodec`）、`FileKnowledgeStore`（同时实现 `VectorStore` +
    `DocumentRegistry`：切片与元信息同库、`deleteBySource` **单篇删除不误清全库**、空间不符时
    **原地重嵌入**而不是删库）、`SekbDocumentImporter`（文本 + PDF，四类结果各有可读原因）。
  - **`SekbContainer` + `SekbViewModel`**：装配路由/编排器/工具/嵌入/策略（L1）；检索阈值可调并
    **重建 retriever + tools**（阈值是构造参数，没有 setter）；数据目录按
    `SEKB_DATA_DIR` → Application Support → 临时目录解析，并由自检断言"真的可写"。
  - **自检**：新增 12 条**可证伪**项（`ui_*` / `bridge_*`），并把一条旧空检查
    `retriever_default_threshold`（原来写死 `ok: true`，常量改了也不会红）改成真断言；
    另修掉一条我自己写错层级的自检（`ui_doc_delete_isolated` 误用存储层 `deleteBySource`）。
  - **自检横幅按平台前缀**：iOS `SEKB_IOS_SELFTEST`（**一个字未改**，`scripts/ios_app.sh` 按它抓）
    / macOS `SEKB_MAC_SELFTEST`（`scripts/mac_app.sh` 兼容两种前缀抓）。
  - **脚本**：`ios_app.sh` 加 `ui`（逐页截图 → `apps/ios/docs/screenshots/`）与 `screenshot`；
    `mac_app.sh` 加 `ui`；两者加 `SEKB_SKIP_FRAMEWORK_BUILD=1`（他人正在改共享层时先验自己这一端，
    跳过时会打印 framework 时间戳，默认关闭）。
  - **自检日志抓取改为 `log stream`（增量）而不是 `log show --last`（扫历史）**：后者在本机负载高、
    日志库大时实测要**几十分钟**，而且会因时间窗过期而报"没抓到总结行"（真实踩到：一次 `run`
    卡了 54 分钟）。`log stream` 秒级返回，并保留 `log show` 作为兜底。
  - **共享层缺口（本轮用替代方案）**：`SekbApi` 的 throwing 方法未标 `@Throws` 时，
    Kotlin/Native 会把异常**直接终止进程**——实测 `SekbApi.login` 收到 HTTP 500 →
    `Abort trap: 6`（"Function doesn't have or inherit @Throws annotation ... Program will be terminated"）。
    Apple 侧因此**一行 `SekbApi` 都不能调**（token 过期 401 就直接闪退）。本轮用
    `apps/ios/App/SekbApiBridge.swift` 顶上：只重写 HTTP + 状态码 + 字段名，
    SSE 解析走共享层 `SseParser`、凭证走 `DeviceCredentials.fromTtl`、JSON 走 `JsonX`/`JsonObject`，
    并用 5 条自检（含"401 → Swift 错误而不是 abort"）钉住字段名与错误路径。
    主线随后补齐 `@Throws`（`SekbApi` 八个公开方法 + `Json` 四个缺失即抛的方法）、把
    `HostOllamaEmbedding.embed` 改成"捕获→同数量零向量 + `lastError`"（与 Apple 侧 `OrtBgeEmbedding`
    的失败语义统一：宿主故障时不再"iOS 降级、Android 崩"），并加了机械守卫
    （`check_edge_facts.py` 第 ⑥ 项「公开方法会 throw 但没标 `@Throws`」，反向验证过）+
    把 native 编译纳入 `scripts/verify_all_ends.sh quick`（补上"Android 编得过 ≠ 共享层编得过"这个洞）。
    **下一轮应收尾删除该 bridge、改回共享层 `SekbApi`**（单列一轮 + 单独提交）。
  - **两份 README**（`apps/ios/README.md` / `apps/mac/README.md`）重写为"四页功能可用"的验收页：
    复现命令、原始自检输出、截图清单与**每张图证明了什么**、与 Android 的**有意差异**表、未验清单。

- **验证**：
  - `bash scripts/ios_app.sh run`（iPhone 17 Pro / iOS 26.3 模拟器）与 `bash scripts/mac_app.sh run`
    的**原始汇总行**写在两份 README 里，两端同一张表；
  - 数字**不回归**：`rag_retrieve` 首条分 0.630、`embed_onnx_sanity` 余弦 0.244 / L2 1.0000、
    `rag_eval_calibrate` 五阈值（0.2/0.3/0.4/0.5/0.6）的 Hit@1/Hit@3/MRR/误召回与 Android **逐项相同**；
  - **视觉验证**：`apps/ios/docs/screenshots/`（模拟器真实截图 5 张，逐张看图确认；
    其中空状态那张证明**输入栏没有被挤出屏幕**——Android 真机上正是这个 bug）；
    `apps/mac/docs/screenshots/`（5 张，用 `NSWindow` + `NSHostingView` + `cacheDisplay` 渲染：
    本机 `screencapture` 被系统权限拒绝，实测 `could not create image from display`）；
  - 文档守卫：`bash docs/tech/.validation/doc_guard.sh --no-warn` 与
    `python3 scripts/changelog_merge.py --check`（第 7 项一度被主线新增的 `@Throws` 守卫判红，
    指向共享层 5 处，已由主线补齐）。
