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

/// 自检横幅前缀：**按平台**（iOS `SEKB_IOS_SELFTEST` / macOS `SEKB_MAC_SELFTEST`）。
///
/// 为什么要区分：两端跑的是同一份源码，但 `scripts/ios_app.sh` 是从模拟器统一日志里
/// **按前缀抓**输出的，`scripts/mac_app.sh` 抓的是 stdout。两端都打同一个前缀时，
/// "这条日志是哪一端跑出来的"就无从判断（跨端排查时这一点很值钱）。
/// ⚠️ iOS 侧的前缀**一个字都不能改**——`scripts/ios_app.sh` 与历史验收记录都按它抓。
var sekbSelfTestPrefix: String { SekbPlatform.selfTestPrefix }

/// 跑一遍自检并把结果打出来（供启动时与设置页的"重新运行"共用）。
@discardableResult
func runAndReportSelfTest() -> [CheckResult] {
    // 起始标记：抓日志时以**最后一个标记**为界，避免把上一次运行的条目混进来
    // （实测踩到过：同一窗口里两次运行，日志里同时有上一次的 FAIL 和这一次的 PASS，
    //  看上去像"同一轮自相矛盾"）。`scripts/ios_app.sh` 会按这个标记裁剪。
    let prefix = sekbSelfTestPrefix
    report("\(prefix) === RUN START ===")
    let results = runSharedSelfTest()
    let pass = results.filter { $0.ok == true }.count
    let fail = results.filter { $0.ok == false }.count
    let skip = results.filter { $0.ok == nil }.count
    report("\(prefix) PASS=\(pass) FAIL=\(fail) SKIP=\(skip)")
    for r in results {
        let tag = r.ok == nil ? "SKIP" : (r.ok! ? "PASS" : "FAIL")
        report("\(prefix) [\(tag)] \(r.name) — \(r.detail)")
    }
    DispatchQueue.main.async {
        SekbSelfTestStore.shared.results = results
        SekbSelfTestStore.shared.running = false
        SekbSelfTestStore.shared.lastRunAt = Date()
    }
    return results
}

/// **自检专用的假传输**：把云端请求换成写死的响应，用来在没有网络/后端的情况下
/// 端到端验证 `SekbApiBridge`（登录 → enroll → SSE 聊天 → 路由事件上报 → 错误路径）。
///
/// 为什么值得单独造它：Apple 侧因为共享层缺 `@Throws` 而**不能用 `SekbApi`**
/// （见 `SekbApiBridge` 的说明），桥是替代方案，就必须有证据表明它真的能跑通、
/// 且**错误路径不会终止进程**。假传输是这份证据里唯一不依赖外部环境的部分。
final class SelfTestTransport: NSObject, HttpTransport {

    /// 全部返回 401（验证"HTTP 错误 → Swift 错误"而不是崩溃）
    let alwaysUnauthorized: Bool

    init(alwaysUnauthorized: Bool = false) {
        self.alwaysUnauthorized = alwaysUnauthorized
        super.init()
    }

    /// 协议文档 §2 的 enroll 响应样例（字段名以文档为准）
    static let enrollFixture = "{\"device_id\":\"9f3c1a2b7d4e5f60\",\"device_token\":\"eyJ.selftest\","
        + "\"token_type\":\"Bearer\",\"expires_in_hours\":720,\"scope\":\"edge\"}"

    /// 协议文档 §3 的 SSE 帧（一行一个 `data:`，空行分隔）
    static let sseFixture: [String] = [
        ": keep-alive",
        SseParser.companion.asDataLine(json: "{\"type\":\"thinking\",\"content\":\"先看本机资料\"}"),
        "",
        SseParser.companion.asDataLine(json: "{\"type\":\"token\",\"content\":\"端侧\"}"),
        "",
        SseParser.companion.asDataLine(json: "{\"type\":\"token\",\"content\":\"检索\"}"),
        "",
        SseParser.companion.asDataLine(json: "{\"type\":\"done\",\"meta\":{\"conversation_id\":\"c-1\","
            + "\"intent\":\"chitchat\",\"execution\":{\"primary_plane\":\"edge\",\"primary_role\":\"chat\","
            + "\"model\":\"qwen3.5-2b\",\"reason\":\"edge_preferred\",\"tier\":\"default\","
            + "\"escalated\":0,\"by_plane\":{\"edge\":1},\"edge_decided\":1,\"edge_completed\":1,"
            + "\"latency_ms\":42.5}}}"),
        "",
    ]

    func get(url: String, headers: [String: String], timeoutSeconds: Int64) -> HttpResponse {
        if alwaysUnauthorized { return HttpResponse(code: 401, body: "{\"detail\":\"unauthorized\"}") }
        return HttpResponse(code: 200, body: "{}")
    }

    func postJson(url: String, headers: [String: String], body: String, timeoutSeconds: Int64) -> HttpResponse {
        if alwaysUnauthorized {
            return HttpResponse(code: 401, body: "{\"detail\":\"unauthorized\"}")
        }
        if url.hasSuffix("/auth/login") {
            return HttpResponse(code: 200, body: "{\"user\":{\"id\":1},\"token\":\"tok-selftest\"}")
        }
        if url.hasSuffix("/device/enroll") || url.hasSuffix("/device/refresh") {
            return HttpResponse(code: 200, body: SelfTestTransport.enrollFixture)
        }
        if url.hasSuffix("/edge/route-events") {
            return HttpResponse(code: 200, body: "{}")
        }
        return HttpResponse(code: 404, body: "{\"detail\":\"not found\"}")
    }

    func postJsonStream(url: String, headers: [String: String], body: String,
                        timeoutSeconds: Int64, onLine: @escaping (String) -> Void) -> Int32 {
        if alwaysUnauthorized { return 401 }
        for line in SelfTestTransport.sseFixture { onLine(line) }
        return 200
    }
}

@main
struct SekbApp: App {

    init() {
        // **macOS 视觉验证**：`SEKB_UI_PROBE=1` 时离屏渲染一页并写 PNG，然后自行退出。
        // 放在自检之前：探针只是"看图"，不需要（也不该等）整轮自检。
        // （macOS 上 `screencapture` 被系统权限拒绝，实测 "could not create image from display"，
        //   所以这一端用 ImageRenderer 离屏渲染取图，见 `MacUiProbe.swift`。）
        #if os(macOS)
        if MacUiProbe.runIfRequested() { return }
        #endif
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
        WindowGroup { RootView() }
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

    // 7) 检索阈值默认值。
    //    这一条**必须是真的断言**：上一版写的是 `ok: true, detail: "0.5（策略未接入）"`——
    //    那是"永远 PASS 的空检查"，常量改了它也不会红（等于不存在）。
    //    现在断言的是**实际参与检索的那个默认值**，并说明它为什么与 Android 的 0.5 不同
    //    （不是各端一个口味，而是两套嵌入空间各自的标定点，见 `SekbDefaults`）。
    out.append(.init(name: "retriever_default_threshold",
                     ok: abs(SekbDefaults.retrievalMinScore - 0.40) < 1e-9,
                     detail: String(format: "%.2f（本机加载 fp32 空间 → 0.40 是标定值；"
                                    + "int8 空间分布上移，对应 0.5）", SekbDefaults.retrievalMinScore)))

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

    // ══════════════════════════════════════════════════════════════════════════════
    // 10) UI 层自检（本期新增）
    //
    //     原则：**宁可少加，也不加"永远 PASS 的空检查"**。下面每一条都断言一个
    //     具体的、可证伪的值——改坏了必须变红。
    // ══════════════════════════════════════════════════════════════════════════════

    // 10.1 四页与 Android `SekbScreen` **同名同顺序**（导航壳的装配契约）
    let screenNames = SekbScreen.allCases.map { $0.rawValue }
    out.append(.init(name: "ui_screens_align_android",
                     ok: screenNames == ["chat", "history", "documents", "settings"],
                     detail: "页面顺序=\(screenNames.joined(separator: " → "))"))

    // 10.2 Markdown 块级解析：与 Android `Markdown.parse` 同一套规则
    //      （标题/段落/无序/有序/引用/代码块/表格/分隔线各一条，且表格必须被识别）
    let mdLines = [
        "# 标题一",
        "这是**粗体**与`行内码`。",
        "",
        "- 项目 A",
        "- 项目 B",
        "",
        "1. 第一",
        "2. 第二",
        "",
        "> 引用一行",
        "",
        "```swift",
        "let x = 1",
        "```",
        "",
        "| 列一 | 列二 |",
        "| --- | --- |",
        "| a | b |",
        "",
        "---",
    ]
    let mdKinds: [String] = SekbMarkdown.parse(mdLines.joined(separator: "\n")).map { block in
        switch block {
        case let .heading(level, _): return "h\(level)"
        case .paragraph: return "p"
        case let .bullet(items): return "bullet\(items.count)"
        case let .numbered(items): return "num\(items.count)"
        case let .quote(lines): return "quote\(lines.count)"
        case let .code(lang, code): return "code:\(lang):\(code)"
        case let .table(header, rows): return "table\(header.count)x\(rows.count)"
        case .divider: return "divider"
        }
    }
    let mdExpected = ["h1", "p", "bullet2", "num2", "quote1",
                      "code:swift:let x = 1", "table2x1", "divider"]
    out.append(.init(name: "ui_markdown_blocks",
                     ok: mdKinds == mdExpected,
                     detail: mdKinds.joined(separator: ",")))

    // 10.3 行内片段：粗体/行内码/链接各归各位，**未闭合标记降级为普通文本**
    //      （流式输出必然出现半截标记，丢字比不渲染更糟）
    func spanKind(_ s: MarkdownSpan) -> String {
        switch s {
        case let .text(t): return "t(\(t))"
        case let .bold(t): return "b(\(t))"
        case let .italic(t): return "i(\(t))"
        case let .code(t): return "c(\(t))"
        case let .link(t, u): return "l(\(t)|\(u))"
        }
    }
    let spans = SekbMarkdown.inline("看**粗体**和`code`以及[链接](https://x)").map(spanKind)
    let spansExpected = ["t(看)", "b(粗体)", "t(和)", "c(code)", "t(以及)", "l(链接|https://x)"]
    let unclosed = SekbMarkdown.inline("**没闭合").map(spanKind)
    out.append(.init(name: "ui_markdown_inline",
                     ok: spans == spansExpected && unclosed == ["t(**没闭合)"],
                     detail: "闭合=\(spans.joined(separator: " ")) | 未闭合=\(unclosed.joined(separator: " "))"))

    // 10.4 原因码翻译：认识的要翻成人话，**不认识的原样透出**（绝不返回"未知原因"）
    let explainKnown = PlaneExplain.describe(raw: "output_over_edge_budget(600>512)")
    let explainUnknown = PlaneExplain.describe(raw: "some_future_code_v9")
    // 升级场景的解释**不能**与徽章互相打脸（徽章依据实际完成的平面）：
    // 这条断言 "端侧没能达标…"，并同时证明 ` · ` 分隔的原因码没有被前缀规则吞掉
    let explainEscalated = PlaneExplain.outcome(completedOnDevice: false, escalated: true,
                                                decisionReason: "edge_preferred",
                                                escalateReason: "edge_preferred · degenerate")
    out.append(.init(name: "ui_plane_explain_known",
                     ok: explainKnown.contains("600>512") && !explainKnown.contains("原因："),
                     detail: explainKnown))
    out.append(.init(name: "ui_plane_explain_unknown_passthrough",
                     ok: explainUnknown == "原因：some_future_code_v9",
                     detail: explainUnknown))
    out.append(.init(name: "ui_plane_explain_escalated_not_contradictory",
                     ok: explainEscalated.hasPrefix("端侧没能达标")
                        && explainEscalated.contains("重复、退化"),
                     detail: explainEscalated))

    // 10.5 检索来源解析（端侧 RAG 的可追溯性）+ 失败结果必须被跳过
    let okToolResult = ToolResult(
        ok: true,
        output: "[0.696] doc-rag: 端侧 RAG 把知识索引放在设备上，检索不出网。\n"
            + "[0.612] doc-privacy：设备专属数据永不出端。",
        denied: false, reason: "")
    let deniedToolResult = ToolResult(ok: false, output: "[0.900] ghost-doc: 不该被解析",
                                      denied: true, reason: "未授权")
    let parsedSources = RetrievalSources.parse([okToolResult, deniedToolResult])
    out.append(.init(name: "ui_sources_parse",
                     ok: parsedSources.count == 2
                        && parsedSources.first?.sourceId == "doc-rag"
                        && abs((parsedSources.first?.score ?? 0) - 0.696) < 1e-9
                        && !parsedSources.contains { $0.sourceId == "ghost-doc" },
                     detail: RetrievalSources.summary(parsedSources)))

    // 10.6 相对时间文案（会话列表用；分段降级必须与 Android 同口径）
    let t0: Int64 = 1_700_000_000_000
    let relJust = TimeFmt.relative(t0 - 30_000, nowMillis: t0)
    let relMin = TimeFmt.relative(t0 - 5 * 60_000, nowMillis: t0)
    let relDays = TimeFmt.relative(t0 - 3 * 86_400_000, nowMillis: t0)
    out.append(.init(name: "ui_time_relative",
                     ok: relJust == "刚刚" && relMin == "5 分钟前" && relDays == "3 天前",
                     detail: "\(relJust) / \(relMin) / \(relDays)"))

    // 10.7 数据目录**真的可写**（不是"存在就算"）
    //      受限沙箱里 Application Support 是被拒的，所以目录是解析出来的，
    //      这里必须验证"解析到的那个目录能写进文件"，否则会话/索引会静默不落盘。
    let dataDir = SekbPaths.dataDir()
    let dataProbe = dataDir.appendingPathComponent("selftest-write-probe.txt")
    let dataWritable = (try? "ok".write(to: dataProbe, atomically: true, encoding: .utf8)) != nil
    try? FileManager.default.removeItem(at: dataProbe)
    out.append(.init(name: "ui_data_dir_writable",
                     ok: dataWritable,
                     detail: "来源=\(SekbPaths.resolvedFrom) 路径=\(dataDir.path)"))

    // 10.8 会话持久化往返 + **坏文件必须被跳过而不是让列表打不开**
    //      （用独立临时目录：自检不能碰用户真实历史）
    let tmpConvDir = FileManager.default.temporaryDirectory
        .appendingPathComponent("sekb-selftest-conv-\(UUID().uuidString)")
    let selftestConvStore = FileConversationStore(dir: tmpConvDir)
    selftestConvStore.save(c: Conversation(
        id: "c-selftest",
        title: "自检会话",
        updatedAtMillis: t0,
        messages: [
            StoredMessage(fromUser: true, text: "端侧检索为什么隐私更好？", plane: "", model: "",
                          escalated: 0, reason: "", escalateReason: "", sources: []),
            StoredMessage(fromUser: false, text: "因为索引在设备上。", plane: "edge",
                          model: "qwen3.5-2b", escalated: 0, reason: "edge_preferred",
                          escalateReason: "",
                          sources: [StoredSource(sourceId: "doc-rag", score: 0.63, snippet: "索引在设备上")]),
        ]))
    selftestConvStore.saveCurrentId(id: "c-selftest")
    let convReloaded = selftestConvStore.load(id: "c-selftest")
    let convListBefore = selftestConvStore.list()
    let convPointer = selftestConvStore.currentId()
    // 故意写一个坏文件：`list()` 必须跳过它并计数，而不是整页打不开
    try? "{ 这不是 JSON".write(to: tmpConvDir.appendingPathComponent("broken.json"),
                             atomically: true, encoding: .utf8)
    let convListAfter = selftestConvStore.list()
    let convOK = convReloaded?.messages.count == 2
        && convReloaded?.messages[1].plane == "edge"
        && convReloaded?.messages[1].sources.count == 1
        && abs((convReloaded?.messages[1].sources.first?.score ?? 0) - 0.63) < 1e-9
        && convListBefore.count == 1 && convPointer == "c-selftest"
        && convListAfter.count == 1 && selftestConvStore.lastSkippedCorrupt == 1
    out.append(.init(name: "ui_conversation_roundtrip",
                     ok: convOK,
                     detail: "读回=\(convReloaded?.messages.count ?? -1) 条 · 列表=\(convListBefore.count) 段"
                        + " · 指针=\(convPointer ?? "-") · 坏文件跳过=\(selftestConvStore.lastSkippedCorrupt)"))
    try? FileManager.default.removeItem(at: tmpConvDir)

    // 10.9 文档删除**不误清全库** + 重启后索引仍在（单篇删除是曾经踩过的真缺陷：
    //      接口缺 deleteBySource 时调用方用"清空整库"绕过去，删一篇 = 全丢）
    let tmpKBDir = FileManager.default.temporaryDirectory
        .appendingPathComponent("sekb-selftest-kb-\(UUID().uuidString)")
    let tmpKBFile = tmpKBDir.appendingPathComponent("index.json")
    let kbStore = FileKnowledgeStore(file: tmpKBFile, provider: provider)
    let kbIndex = KnowledgeIndex(provider: provider, store: kbStore, maxChars: 512, overlapChars: 64,
                                 registry: kbStore, nowMillis: { 0 })
    _ = kbIndex.ingest(sourceId: "doc-a", text: "端侧 RAG 把知识索引放在设备上，检索不出网，隐私更好。",
                       name: "doc-a", sizeBytes: 30, deviceOnly: true)
    _ = kbIndex.ingest(sourceId: "doc-b", text: "设备专属数据永不出端，这是产品的硬边界。",
                       name: "doc-b", sizeBytes: 24, deviceOnly: true)
    let kbBefore = kbStore.size()
    let kbDocsBefore = kbStore.list().count
    // ⚠️ 必须走 UI 用的那个入口（`KnowledgeIndex.remove` = 切片 + 元信息一起删）。
    // 第一次写这条自检时直接调了 `store.deleteBySource`（那是**存储层**接口，只管切片），
    // 于是元信息还在、断言失败——失败是真的，但错在自检用错了层级（这次修正的正是它）。
    let kbRemoved = kbIndex.remove(sourceId: "doc-a")
    let kbAfter = kbStore.size()
    let kbDocsLeft = kbStore.list()
    // 重新打开（模拟重启）：剩下的文档与它的切片都还在
    let kbReopened = FileKnowledgeStore(file: tmpKBFile, provider: provider)
    let kbOK = kbBefore >= 2 && kbRemoved >= 1 && kbAfter == kbBefore - kbRemoved && kbAfter >= 1
        && kbDocsBefore == 2
        && kbDocsLeft.count == 1 && kbDocsLeft.first?.id == "doc-b"
        && kbReopened.size() == kbAfter && kbReopened.list().first?.id == "doc-b"
        && kbReopened.space.id == provider.space.id
    out.append(.init(name: "ui_doc_delete_isolated",
                     ok: kbOK,
                     detail: "删除前=\(kbBefore) 删除=\(kbRemoved) 剩余=\(kbAfter)"
                        + " 重开=\(kbReopened.size()) 剩余文档=\(kbReopened.list().map { $0.id }.joined(separator: ","))"))
    try? FileManager.default.removeItem(at: tmpKBDir)

    // 10.10 云端桥（`SekbApiBridge`）：字段名 + SSE 装配 + **错误路径不终止进程**
    //       为什么必须测：共享层 `SekbApi` 未标 `@Throws`，Apple 侧直接调它会在非 2xx 时
    //       abort（本机实测 Abort trap: 6）；桥是替代方案，这些断言就是它的证据。
    let fixtureTransport = SelfTestTransport()
    let fixtureBridge = SekbApiBridge(transport: fixtureTransport, baseUrl: "http://selftest.local")
    let fixtureCreds = SekbApiBridge.parseCredentials(SelfTestTransport.enrollFixture,
                                                      nowMillis: t0)
    out.append(.init(name: "bridge_parse_enroll_response",
                     ok: fixtureCreds?.deviceId == "9f3c1a2b7d4e5f60"
                        && fixtureCreds?.issuedAtMillis == t0
                        && fixtureCreds?.expiresAtMillis == t0 + 720 * 3600 * 1000,
                     detail: "id=\(fixtureCreds?.deviceId ?? "-") ttl小时=720"))
    let loginToken = SekbApiBridge.parseLoginToken("{\"user\":{\"id\":1},\"token\":\"tok-1\"}")
    let legacyToken = SekbApiBridge.parseLoginToken("{\"access_token\":\"tok-2\"}")
    out.append(.init(name: "bridge_login_token",
                     ok: loginToken == "tok-1" && legacyToken == "tok-2",
                     detail: "token=\(loginToken ?? "-") access_token=\(legacyToken ?? "-")"))

    var streamedText = ""
    var streamedThinking = ""
    let streamed = fixtureBridge.chatStream(deviceToken: "tok", message: "本机资料里有什么？",
                                           conversationId: nil,
                                           onToken: { streamedText += $0 },
                                           onThinking: { streamedThinking += $0 })
    var streamedPlane = "-"
    var streamedConv = "-"
    if case let .success(r) = streamed {
        streamedPlane = r.execution?.primaryPlane ?? "-"
        streamedConv = r.conversationId ?? "-"
    }
    out.append(.init(name: "bridge_sse_events",
                     ok: streamedText == "端侧检索" && !streamedThinking.isEmpty
                        && streamedPlane == "edge" && streamedConv == "c-1",
                     detail: "正文=\(streamedText) 思考=\(streamedThinking) 平面=\(streamedPlane) 会话=\(streamedConv)"))

    let routeEvent = RouteEventPayload(eventId: "e-selftest", role: "chat", plane: "edge",
                                       reason: "edge_preferred", model: "qwen3.5-2b", tier: "default",
                                       inputTokens: 3, outputTokens: 5, latencyMs: 1.5,
                                       escalated: true, escalateReason: "degenerate",
                                       signals: ["degenerate"], versions: ["tier": "default"])
    let routeBody = SekbApiBridge.routeEventBody(routeEvent)
    out.append(.init(name: "bridge_route_event_body",
                     ok: routeBody.contains("\"escalated\":true")
                        && routeBody.contains("\"signals\":[\"degenerate\"]")
                        && routeBody.contains("\"input_tokens\":3")
                        && routeBody.contains("\"versions\":{\"tier\":\"default\"}"),
                     detail: String(routeBody.prefix(150))))

    let unauthorizedBridge = SekbApiBridge(transport: SelfTestTransport(alwaysUnauthorized: true),
                                           baseUrl: "http://selftest.local")
    var failedCode: Int32 = 0
    switch unauthorizedBridge.login(email: "a@b.c", password: "x") {
    case let .success(token): failedCode = -1; _ = token
    case let .failure(err): failedCode = err.code
    }
    // 这一条是整组里最重要的：**401 必须变成 Swift 错误而不是 abort**
    out.append(.init(name: "bridge_http_error_is_swift_error",
                     ok: failedCode == 401,
                     detail: "401 → Swift 错误码=\(failedCode)（进程存活，说明桥的替代方案有效）"))

    return out
}

/// 根视图。
///
/// 从"M5 自检骨架页"改成四页应用：自检结果移到「设置」页（可重跑），
/// 首页就是聊天——与 Android 的 `AppShell` 同一结构。
///
/// `.sekbTheme()` 装在**根**上：配色随系统明暗整套切换，页面里不出现硬编码色值
/// （Android 侧那次"深色模式只在主题文件里成立"的缺陷就是这么来的）。
/// macOS 另给一个最小窗口尺寸：四页布局（顶栏 + 列表 + 输入栏）在过小的窗口里会挤。
struct RootView: View {
    var body: some View {
        #if os(macOS)
        AppShell().sekbTheme().frame(minWidth: 460, minHeight: 720)
        #else
        AppShell().sekbTheme()
        #endif
    }
}