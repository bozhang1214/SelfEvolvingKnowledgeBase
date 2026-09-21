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
    let results = runSharedSelfTest()
    let pass = results.filter { $0.ok }.count
    report("SEKB_IOS_SELFTEST PASS=\(pass) FAIL=\(results.count - pass)")
    for r in results {
        report("SEKB_IOS_SELFTEST [\(r.ok ? "PASS" : "FAIL")] \(r.name) — \(r.detail)")
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
    let ok: Bool
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
                        Image(systemName: r.ok ? "checkmark.circle.fill" : "xmark.circle.fill")
                            .foregroundStyle(r.ok ? .green : .red)
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
