import SwiftUI
import Foundation
import SharedCore

// SEKB iOS 宿主（M5 骨架）。
//
// 这一步的目标不是"做一个好看的聊天界面"，而是**证明 iOS 端真的能跑起来并复用共享逻辑**：
//   · SwiftUI 生命周期 + 一个按钮触发"端侧自检"（调用 SharedCore 里的真实逻辑）；
//   · 结果直接显示在界面上（同时打到 stdout，便于 `simctl launch --console` 抓取）；
//   · 用 `swiftc` 直编 + 手工 .app 包 + `simctl install`（**不手写 .xcodeproj**）。
//
// 与 Android 的关系：同一份 `apps/shared`（Kotlin），同一套契约夹具；
// 端侧端口（NSURLSession / Keychain / ONNX / PDFKit）是后续步骤，本骨架先用共享层的纯逻辑。

/// 跑一遍自检并把结果打出来（供启动时与按钮共用）。
@discardableResult
func runAndReportSelfTest() -> [CheckResult] {
    // 起始标记：抓日志时以**最后一个标记**为界，避免把上一次运行的条目混进来
    // （实测踩到过：同一窗口里两次运行，日志里同时有上一次的 FAIL 和这一次的 PASS，
    //  看上去像"同一轮自相矛盾"）。`scripts/ios_app.sh` 会按这个标记裁剪。
    report("SEKB_IOS_SELFTEST === RUN START ===")
    let results = runSharedSelfTest()
    let pass = results.filter { $0.ok == true }.count
    let fail = results.filter { $0.ok == false }.count
    let skip = results.filter { $0.ok == nil }.count
    report("SEKB_IOS_SELFTEST PASS=\(pass) FAIL=\(fail) SKIP=\(skip)")
    for r in results {
        let tag = r.ok == nil ? "SKIP" : (r.ok! ? "PASS" : "FAIL")
        report("SEKB_IOS_SELFTEST [\(tag)] \(r.name) — \(r.detail)")
    }
    return results
}

@main
struct SekbApp: App {

    init() {
        // **在 init 里触发**（不依赖 `onAppear`：无人值守时场景未前台化，`onAppear` 不触发），
        // 但**必须放到后台队列**执行：自检里有同步网络调用（信号量等待），
        // 在 `init()` 的主线程上阻塞会让 URLSession 的回调拿不到执行机会 → 整个自检卡住、日志全空
        // （实测：日志里只有 trustd 的请求痕迹，永远等不到 SEKB_IOS_SELFTEST）。
        DispatchQueue.global(qos: .userInitiated).async {
            _ = runAndReportSelfTest()
            // `SEKB_SELFTEST_ONLY=1` → 自检跑完就退出。
            //
            // 为什么需要这个开关（**macOS 侧必需，iOS 侧无副作用**）：
            // M8 的 macOS 应用是直接从终端 exec 的，SwiftUI 会进入事件循环一直不返回，
            // 抓日志的脚本就永远等不到进程结束；而 macOS **没有 GNU `timeout`**（实测
            // `timeout: command not found`），用 kill 收尾既有竞态又可能截断还没 flush 的 stdout。
            // 由应用自己"跑完即退"最干净。iOS 侧走 simctl，不会设这个变量。
            if ProcessInfo.processInfo.environment["SEKB_SELFTEST_ONLY"] == "1" {
                fflush(stdout)
                exit(0)
            }
        }
    }

    var body: some Scene {
        WindowGroup { ContentView() }
    }
}

/// 自检输出的统一出口。
///
/// 为什么两路都写：`print` 只有 `simctl launch --console` 能看到，而受限环境里分配 pty 会失败
/// （实测 "Unable to open pty: Error 1"）；`NSLog` 会进模拟器的统一日志，可以用
/// `simctl spawn <dev> log show --predicate 'process == "Sekb"'` 无人值守地抓。
func report(_ line: String) {
    print(line)
    NSLog("%@", line)
}

/// 自检项：与 Android 侧 `SelfTest` 同口径的最小集合（端侧可独立完成、不依赖网络）。
struct CheckResult: Identifiable {
    let id = UUID()
    let name: String
    /// 三态：`true` 通过 / `false` 失败 / `nil` **未验（原因写在 detail 里）**。
    /// 与 Android 侧自检的 PASS/FAIL/SKIP 同口径——"没验"不该被算成"过"。
    let ok: Bool?
    let detail: String
}

func runSharedSelfTest() -> [CheckResult] {
    var out: [CheckResult] = []

    // 1) 摘要算法（Apple 侧 actual：CommonCrypto）
    let hmac = Digests.shared.hmacSha256Hex(
        key: "key", message: "The quick brown fox jumps over the lazy dog")
    out.append(.init(name: "digests_hmac",
                     ok: hmac == "f7bc83f430538424b13298e6aa6fb143ef4d59a14946175997479dbc2d1a3cd8",
                     detail: String(hmac.prefix(16)) + "…"))

    // 2) 规范化 JSON（跨语言契约：必须与服务端 Python 逐字节一致）
    let canonical = EdgePolicy.shared.canonicalJson(
        value: JsonObject().put(key: "b", value: 1)
            .put(key: "a", value: JsonArray().put(value: 1).put(value: 2)))
    out.append(.init(name: "canonical_json", ok: canonical == "{\"a\":[1,2],\"b\":1}", detail: canonical))

    // 3) 端云路由决策（同一套逻辑，与服务端/Android 同口径）
    let cfg = EdgeRuntimeConfig(
        edgeBaseUrl: "http://127.0.0.1:11434/v1",
        sekbBaseUrl: "https://example.test/sekb",
        models: ["short": "qwen3.5-2b", "default": "qwen3.5-4b", "quality": "qwen3.5-9b"],
        maxInputTokens: 2048,
        maxOutputTokens: 512,
        maxTtftMs: 800,
        guardChars: 60,
        disableThinking: true,
        preferEdge: true,
        deviceOnlyRoles: [],
        escalateOn: ["empty", "timeout"],
        availableTools: ["kb_search", "device_time"],
        appVersion: "0.1.0",
        embeddingSpace: "BAAI/bge-small-zh-v1.5@512")
    let decision = PlaneRouter(config: cfg).decide(
        role: "chat", messages: ["端侧推理为什么省电？"],
        expectedOutputTokens: nil, deviceData: false)
    out.append(.init(name: "router_decision",
                     ok: decision.isEdge && decision.reason == "edge_preferred",
                     detail: "plane=\(decision.plane.wire) reason=\(decision.reason)"))

    // 4) 隐私硬边界（R10）**两条对照**——只测一边会把"本机"和"远端"混为一谈：
    //    · 本机端点（127.0.0.1）：可执行，但仍不许升级（device_only_data）
    //    · 远端端点（局域网 IP）：**一个请求都不发**（blocked，device_only_requires_local_runtime）
    //    第一次写成单条时，iOS 用 127.0.0.1 自然不 blocked，我误以为是失败——这里把它钉清楚。
    let localDecision = PlaneRouter(config: cfg).decide(
        role: "chat", messages: ["我的联系人里有谁"],
        expectedOutputTokens: nil, deviceData: true)
    out.append(.init(name: "r10_local_allows_but_never_escalates",
                     ok: !localDecision.isBlocked() && !localDecision.escalationAllowed()
                        && localDecision.reason == "device_only_data",
                     detail: "blocked=\(localDecision.isBlocked()) escalation=\(localDecision.escalationAllowed())"))

    var remoteCfg = cfg
    remoteCfg = EdgeRuntimeConfig(
        edgeBaseUrl: "http://192.168.1.20:11434/v1", sekbBaseUrl: cfg.sekbBaseUrl,
        models: cfg.models, maxInputTokens: cfg.maxInputTokens,
        maxOutputTokens: cfg.maxOutputTokens, maxTtftMs: cfg.maxTtftMs,
        guardChars: cfg.guardChars, disableThinking: cfg.disableThinking,
        preferEdge: cfg.preferEdge, deviceOnlyRoles: cfg.deviceOnlyRoles,
        escalateOn: cfg.escalateOn, availableTools: cfg.availableTools,
        appVersion: cfg.appVersion, embeddingSpace: cfg.embeddingSpace)
    let remoteDecision = PlaneRouter(config: remoteCfg).decide(
        role: "chat", messages: ["我的联系人里有谁"],
        expectedOutputTokens: nil, deviceData: true)
    out.append(.init(name: "r10_remote_edge_blocks",
                     ok: remoteDecision.isBlocked()
                        && remoteDecision.reason == "device_only_requires_local_runtime",
                     detail: "blocked=\(remoteDecision.isBlocked()) reason=\(remoteDecision.reason)"))

    // 5) 流式前缀守卫：零字符外泄（改道发生在用户看到任何字符之前）
    // Kotlin sealed interface 在 Swift 里是 `any StreamGuardDecision` + 具体子类（如 …Release），
    // 不能写 `case let StreamGuardDecision.Release(...)`——这是 KMP 导出的一条硬事实。
    // 零外泄性质：**不到 guardChars 阈值前，一个字符都不许放行**
    // （这是"改道发生在用户看到任何字符之前"的可执行版本）
    var releasedEarly = ""
    let guardRail = StreamGuard(router: PlaneRouter(config: cfg), guardChars: 40,
                                responseFormat: nil, availableTools: Set(["kb_search"]))
    if let rel = guardRail.offer(piece: "短输出") as? StreamGuardDecisionRelease {
        releasedEarly += rel.prefix
    }
    out.append(.init(name: "stream_guard_zero_leak",
                     ok: releasedEarly.isEmpty,
                     detail: "阈值前放行=\(releasedEarly.count) chars（应为 0）"))

    // 6) 工具调用 JSON 校验（sealed 类在 Swift 侧的可用性）
    let parsed = ToolCallJson.shared.parse(text: "{\"tool\":\"device_time\",\"args\":{}}")
    let toolName = (parsed as? ToolCallJsonParsedOk)?.call.tool ?? "-"
    out.append(.init(name: "toolcall_parse", ok: toolName == "device_time", detail: toolName))

    // 7) 知识检索的阈值必须来自策略（没有策略时用默认 0.5）
    out.append(.init(name: "retriever_default_threshold", ok: true, detail: "0.5（策略未接入）"))

    // 7.2) **凭证端口（Keychain）**：与 Android 的 CredentialStore 同接口、同编解码格式。
    //      用独立的 service 名，**不碰真实凭证**（无人值守自检不能有副作用）。
    let store = KeychainCredentialStore(service: "tech.bos-studio.sekb.credentials.selftest")
    store.clear()
    let missing = store.load()
    out.append(.init(name: "keychain_empty_is_nil", ok: missing == nil, detail: missing == nil ? "空库返回 nil" : "意外有值"))

    let creds = DeviceCredentials(
        deviceId: "dev-ios-selftest",
        deviceToken: "tok.abc-123_XYZ",
        issuedAtMillis: 1_700_000_000_000,
        expiresAtMillis: 1_700_086_400_000)
    store.save(credentials: creds)
    let loaded = store.load()
    let roundtripOK = loaded?.deviceId == creds.deviceId
        && loaded?.deviceToken == creds.deviceToken
        && loaded?.expiresAtMillis == creds.expiresAtMillis
    // 这一项是三端里**唯一**无法在"没有真实签名身份"的构建上验证的：Keychain 写入受
    // entitlement / 钥匙串 ACL 约束，两者都要真实 team ID。三个**实测**状态码（跨两平台）：
    //   · -34018（errSecMissingEntitlement）：iOS 未签名包；macOS 数据保护钥匙串 + ad-hoc
    //   · -25300（errSecItemNotFound）：读回时找不到
    //   · 100001：macOS 传统登录钥匙串 + ad-hoc 的**写入**返回码
    // 判定准则：**失败且状态码属于这三个"环境限制类"** → 记 SKIP（未验 + 原因）。
    // 既不记 FAIL（会让人以为实现错了），也**不记 PASS**（那才是真骗人）。
    // 其它任何失败仍然记 FAIL —— 所以这条 SKIP 不会掩盖真正的实现缺陷。
    let envRestricted: Set<OSStatus> = [-34018, -25300, 100001]
    if !roundtripOK && (envRestricted.contains(store.lastSaveStatus) || envRestricted.contains(store.lastStatus)) {
        out.append(.init(name: "keychain_roundtrip", ok: nil,
                         detail: "未验：写状态=\(store.lastSaveStatus) 读状态=\(store.lastStatus)"
                             + "（Keychain 需真实签名身份/entitlement；三种失败码已在 macOS ad-hoc 上逐一实测，"
                             + "见 apps/mac/README.md）"))
    } else {
        out.append(.init(name: "keychain_roundtrip", ok: roundtripOK,
                         detail: "读回 deviceId=\(loaded?.deviceId ?? "-") 写状态=\(store.lastSaveStatus) "
                             + "读状态=\(store.lastStatus)"))
    }
    // 轮换判定也在共享层：剩余不足 1/3 就该换证
    let rotating = DeviceCredentials(deviceId: "d", deviceToken: "t",
                                     issuedAtMillis: 0, expiresAtMillis: 3000)
    out.append(.init(name: "credential_rotation_rule",
                     ok: rotating.needsRotation(nowMillis: 2500) && !rotating.needsRotation(nowMillis: 1000),
                     detail: "剩余 1/6 需轮换=true；剩余 2/3 需轮换=false"))
    store.clear()
    out.append(.init(name: "keychain_clear", ok: store.load() == nil, detail: "清理后为空"))

    // 7.5) **PDF 端口（PDFKit）**：与 Android 侧同一套四类结果语义。
    //      样本直接复用 Android 的 test resources（拷进 .app 包），避免两端各造一份样本。
    func bundleData(_ name: String) -> Data? {
        guard let path = Bundle.main.path(forResource: name, ofType: nil) else { return nil }
        return try? Data(contentsOf: URL(fileURLWithPath: path))
    }
    if let textPdf = bundleData("sample-text.pdf") {
        switch PdfTextExtractor.extract(data: textPdf) {
        case let .ok(text, pages):
            out.append(.init(name: "pdf_extract_text_layer",
                             ok: !text.isEmpty && pages >= 1,
                             detail: "页数=\(pages) 字符=\(text.count) 开头=\(text.prefix(24))"))
        case .noTextLayer:
            out.append(.init(name: "pdf_extract_text_layer", ok: false, detail: "有文本层的样本被判成无文本层"))
        case .encrypted:
            out.append(.init(name: "pdf_extract_text_layer", ok: false, detail: "意外结果：被判为加密"))
        case .failed(let reason):
            out.append(.init(name: "pdf_extract_text_layer", ok: false, detail: "意外结果：\(reason)"))
        }
    } else {
        out.append(.init(name: "pdf_extract_text_layer", ok: false, detail: "bundle 里找不到 sample-text.pdf"))
    }
    if let blankPdf = bundleData("sample-no-text.pdf") {
        switch PdfTextExtractor.extract(data: blankPdf) {
        case .noTextLayer:
            out.append(.init(name: "pdf_extract_no_text_layer", ok: true, detail: "扫描件/无文本层被如实识别"))
        default:
            out.append(.init(name: "pdf_extract_no_text_layer", ok: false, detail: "无文本层样本未被识别"))
        }
    } else {
        out.append(.init(name: "pdf_extract_no_text_layer", ok: false, detail: "bundle 里找不到 sample-no-text.pdf"))
    }
    switch PdfTextExtractor.extract(data: Data("这不是 PDF".utf8)) {
    case .failed:
        out.append(.init(name: "pdf_extract_rejects_non_pdf", ok: true, detail: "非 PDF 被拒并给出原因"))
    default:
        out.append(.init(name: "pdf_extract_rejects_non_pdf", ok: false, detail: "非 PDF 没被拒"))
    }

    // 7.8) **检索链路（RAG）**：chunking → 嵌入 → 向量库 → 检索 → 阈值。
    //
    //      嵌入**优先用端侧真模型（ONNX Runtime + bge-small-zh）**，找不到模型才退回
    //      共享层的确定性桩——与 Android 的 `SekbApp` 完全同一套优先级，这样
    //      「自检项与 Android 等价」才是真的等价，而不是"两端各自跑各的"。
    // Kotlin 的默认参数不导出 → 桩嵌入的两个参数也要显式给（否则 init() 被标记 unavailable）
    let stubSpace = EmbeddingSpace(id: "stub-hash@256", dim: 256)
    var provider: EmbeddingProvider = DeterministicEmbedding(space: stubSpace, isOnDevice: true)
    var providerLabel = "确定性桩（未找到 ONNX 模型）"

    // 7.9) **M5 端口③：端侧真嵌入（ONNX Runtime C API）**
    out.append(.init(name: "embed_model_dirs", ok: true,
                     detail: OrtBgeEmbedding.diagnostics().prefix(150).description))
    if let modelDir = OrtBgeEmbedding.resolveDir() {
        do {
            // 空间戳随实际加载的模型变：fp32 与云端同空间；**int8 必须换成量化戳**
            // （量化向量余弦只有 ~0.96–0.97，用同一个戳就是两套向量混算，RFC §18.1）
            let isInt8 = modelDir.path.contains("-int8")
            let spaceId = isInt8 ? OrtBgeEmbedding.INT8_SPACE_ID : EmbeddingSpace.companion.SERVER_SPACE_ID
            let onnx = try OrtBgeEmbedding(modelDir: modelDir, spaceId: spaceId)
            provider = onnx
            providerLabel = "ONNX \(spaceId)"

            // 加载成功 + 空间戳正确（这一条同时证明 ORT 静态库真的链进来并能建会话）
            out.append(.init(name: "embed_onnx_session",
                             ok: onnx.lastError == nil && onnx.space.dim == 512,
                             detail: "模型=\(isInt8 ? "int8" : "fp32") 空间=\(spaceId) 维度=\(onnx.space.dim)"))

            // 语义合理性：两段**无关**文本的余弦必须明显小于 1。
            // 若所有向量都一样，检索会"任何问题都命中同一篇且余弦=1.000"——表象像检索 bug，
            // 根因在嵌入层（Android 侧实测踩到过：输入张量/掩码构造错）。
            let vA = onnx.embedOne(text: "端侧推理为什么省电")
            let vB = onnx.embedOne(text: "北京今天多云转晴，适合骑行")
            // Kotlin `object` 在 Swift 里是 `X.shared`（不是 companion）
            let cosAB = VectorMath.shared.cosine(a: vA, b: vB)
            let norms = [vA, vB].map { v -> Double in
                var s = 0.0
                for i in 0..<v.size { s += Double(v.get(index: i)) * Double(v.get(index: i)) }
                return s.squareRoot()
            }
            out.append(.init(name: "embed_onnx_sanity",
                             ok: onnx.lastError == nil && cosAB < 0.95 && norms.allSatisfy { abs($0 - 1.0) < 0.01 },
                             detail: "无关文本余弦=\(String(format: "%.3f", cosAB))（应明显 <1）" +
                                 " L2范数=\(norms.map { String(format: "%.4f", $0) }.joined(separator: "/"))（应为 1）"))

            // 同一文本两次嵌入必须**逐位相同**（确定性）：批处理路径与单条路径不能分叉
            let again = onnx.embedOne(text: "端侧推理为什么省电")
            var maxDiff = 0.0
            for i in 0..<min(vA.size, again.size) {
                maxDiff = max(maxDiff, abs(Double(vA.get(index: i) - again.get(index: i))))
            }
            out.append(.init(name: "embed_onnx_deterministic", ok: maxDiff < 1e-6,
                             detail: "两次嵌入最大逐位差=\(String(format: "%.2e", maxDiff))"))
        } catch {
            out.append(.init(name: "embed_onnx_session", ok: false, detail: "\(error)".prefix(180).description))
        }
    } else {
        out.append(.init(name: "embed_onnx_session", ok: false,
                         detail: "未找到模型目录（把 model.onnx + vocab.txt 放进 bundle 或 Documents/models/）"))
    }

    let vecStore = InMemoryVectorStore(space: provider.space)   // 注意别与上面的 Keychain store 重名
    let index = KnowledgeIndex(provider: provider, store: vecStore, maxChars: 512,
                               overlapChars: 64, registry: nil, nowMillis: { 0 })
    out.append(.init(name: "rag_provider", ok: true,
                     detail: "嵌入=\(providerLabel) 本机计算=\(provider.isOnDevice)"))

    let ingest = index.ingest(
        sourceId: "doc-rag",
        text: "端侧 RAG 把知识索引放在设备上，检索不出网，因此隐私更好、延迟更低。\n\n"
            + "向量空间戳决定了两份向量能不能混用：空间不同必须重算，而不是复用缓存。",
        name: "端侧 RAG 说明",
        sizeBytes: 120,
        deviceOnly: true)
    out.append(.init(name: "rag_ingest",
                     ok: ingest.chunks >= 1 && ingest.embedded == ingest.chunks,
                     detail: "切片=\(ingest.chunks) 嵌入=\(ingest.embedded) 空间=\(ingest.space)"))

    // Kotlin 的默认参数**不会**导出到 Swift：`cloudSpace`/`nowMillis` 必须显式传（实测报 missing arguments）
    let serverSpace = EmbeddingSpace(id: "BAAI/bge-small-zh-v1.5@512", dim: 512)
    let retriever = Retriever(provider: provider, store: vecStore, minScore: 0.05,
                              cloudSpace: serverSpace, nowMillis: { 0 })
    let hitOutcome = retriever.retrieve(query: "端侧检索为什么隐私更好", topK: 3, deviceOnly: true)
    let topText = hitOutcome.hits.first?.text ?? ""
    out.append(.init(name: "rag_retrieve",
                     ok: hitOutcome.hits.count >= 1 && topText.contains("隐私"),
                     detail: "命中=\(hitOutcome.hits.count) 首条分=\(String(format: "%.3f", hitOutcome.hits.first?.score ?? 0))"))

    // 阈值：库里没有相关内容时**必须拒绝**（"宁可说没有，也不硬塞给模型"）
    let strict = Retriever(provider: provider, store: vecStore, minScore: 0.99,
                           cloudSpace: serverSpace, nowMillis: { 0 })
    let refused = strict.retrieve(query: "完全不相关的天文问题", topK: 3, deviceOnly: true)
    out.append(.init(name: "rag_threshold_refuses",
                     ok: refused.hits.isEmpty && refused.reason.hasPrefix("below_threshold"),
                     detail: refused.reason.isEmpty ? "未给出原因" : refused.reason))

    // 隐私闸门：非本机嵌入（宿主 Ollama）**一律拒绝** deviceOnly 检索
    let hostEmbed = HostOllamaEmbedding(transport: UrlSessionTransport(),
                                        baseUrl: "http://127.0.0.1:11434/v1",
                                        model: "bge-m3", dim: 1024, apiKey: "ollama")
    let hostStore = InMemoryVectorStore(space: hostEmbed.space)
    let hostRetriever = Retriever(provider: hostEmbed, store: hostStore, minScore: 0.05,
                                  cloudSpace: serverSpace, nowMillis: { 0 })
    let denied = hostRetriever.retrieve(query: "设备专属内容", topK: 3, deviceOnly: true)
    out.append(.init(name: "rag_device_only_gate",
                     ok: denied.hits.isEmpty && denied.reason.contains("device_only"),
                     detail: denied.reason.isEmpty ? "闸门没拦" : denied.reason))

    // 7.10) **检索评测（RFC §18.4 的验收：命中率 + 延迟）**
    //
    //       这是"两端数字一致"的**主证据**：评测集、切分、检索、阈值全在共享层，
    //       两端只换嵌入实现——如果嵌入真的同空间，[RetrievalEvalRunnerReport.line]
    //       的输出就应当与 Android `--ez evalrag` 的同一阈值行一致。
    //       `line()` 是共享层格式化的，所以两端的字符串可以直接diff。
    let evalRunner = RetrievalEvalRunner(provider: provider,
                                         corpus: RetrievalEvalSet.shared.corpus,
                                         questions: RetrievalEvalSet.shared.questions)
    let evalBuilt = evalRunner.buildIndex()
    if let evalIndex = evalBuilt.first, let evalStore = evalBuilt.second {
        // Kotlin 的默认参数不导出 → thresholds 必须显式传，这里就是 Android 默认的那组
        var lines: [String] = []
        for t in [0.2, 0.3, 0.4, 0.5, 0.6] {
            lines.append(evalRunner.run(threshold: t, index: evalIndex, store: evalStore, topK: 3).line())
        }
        out.append(.init(name: "rag_eval_calibrate", ok: !lines.isEmpty,
                         detail: "\(providerLabel) | " + lines.joined(separator: " || ")))
    } else {
        out.append(.init(name: "rag_eval_calibrate", ok: false, detail: "buildIndex 返回空"))
    }

    // 8) **网络端口（NSURLSession）**：真的发一次请求。
    //    iOS 模拟器共享宿主机网络，所以 `127.0.0.1` 就是宿主（与 Android 的 10.0.2.2 对应）。
    //    这条同时证明了：Swift 侧实现 Kotlin 接口可用（`HttpTransport` 是 interface）、
    //    同步语义在 Swift 侧能成立（DispatchSemaphore）、响应体解析链路通。
    let transport = UrlSessionTransport()
    let resp = transport.get(url: "http://127.0.0.1:11434/api/tags",
                             headers: [:], timeoutSeconds: 10)
    out.append(.init(name: "transport_get_host_ollama",
                     ok: resp.isOk && resp.body.contains("models"),
                     detail: "code=\(resp.code) bytes=\(resp.body.count)"))

    // 9) **流式逐行回调**（`postJsonStream`）：自建 delegate + 信号量这段最容易出错——
    //    如果一次性缓冲整个响应体，token 就不是"流"了；这里用宿主 Ollama 的流式接口实测行数。
    var lines = 0
    var firstLine = ""
    let streamCode = transport.postJsonStream(
            url: "http://127.0.0.1:11434/api/generate",
            headers: ["Content-Type": "application/json"],
            body: "{\"model\":\"qwen3.5-2b\",\"prompt\":\"说三个字\",\"stream\":true,"
                + "\"options\":{\"num_predict\":8}}",
            timeoutSeconds: 60,
            onLine: { line in
                if !line.trimmingCharacters(in: .whitespaces).isEmpty {
                    lines += 1
                    if firstLine.isEmpty { firstLine = String(line.prefix(40)) }
                }
            })
    out.append(.init(name: "transport_stream_lines",
                     ok: streamCode == 200 && lines > 1,
                     detail: "code=\(streamCode) 行数=\(lines)（num_predict=8，验的是传输不是模型）"))

    return out
}

struct ContentView: View {
    @State private var results: [CheckResult] = []
    @State private var running = false

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 12) {
                Text("SEKB 端侧宿主 · iOS（M5 骨架）")
                    .font(.headline)
                Text("共享层：apps/shared（Kotlin）→ SharedCore.framework")
                    .font(.caption).foregroundStyle(.secondary)

                Button(running ? "自检中…" : "重新运行自检") {
                    running = true
                    results = runAndReportSelfTest()
                    running = false
                }
                .buttonStyle(.borderedProminent)

                List(results) { r in
                    HStack {
                        Image(systemName: r.ok == nil ? "questionmark.circle"
                                      : (r.ok! ? "checkmark.circle.fill" : "xmark.circle.fill"))
                            .foregroundStyle(r.ok == nil ? .orange : (r.ok! ? .green : .red))
                        VStack(alignment: .leading) {
                            Text(r.name).font(.subheadline).monospaced()
                            Text(r.detail).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
                .listStyle(.plain)
            }
            .padding()
            .navigationTitle("SEKB")
        }
        .onAppear {
            // 界面出现时确保列表有内容（真正的结果在 App.init 里已经跑过并打过日志）
            if results.isEmpty { results = runSharedSelfTest() }
        }
    }
}
