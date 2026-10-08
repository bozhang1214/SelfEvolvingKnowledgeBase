import Foundation
import SharedCore

/// 落盘的设置项（端侧地址、云端地址、检索阈值）。
///
/// 与凭证分开存：凭证进 Keychain（系统加密存储），这些是**普通配置**，
/// 混在一起会让"改个地址"也要过钥匙串 ACL，失败面白白变大。
struct SekbSettings: Codable {
    var edgeUrl: String = SekbDefaults.edgeBaseUrl
    var sekbUrl: String = SekbDefaults.sekbBaseUrl
    var retrievalMinScore: Double = SekbDefaults.retrievalMinScore

    static func load() -> SekbSettings {
        let url = SekbPaths.dataDir().appendingPathComponent("settings.json")
        guard let data = try? Data(contentsOf: url),
              let s = try? JSONDecoder().decode(SekbSettings.self, from: data) else {
            return SekbSettings()
        }
        return s
    }

    func save() {
        let url = SekbPaths.dataDir().appendingPathComponent("settings.json")
        guard let data = try? JSONEncoder().encode(self) else { return }
        try? data.write(to: url, options: .atomic)
    }
}

/// L1 策略的双槽落盘（`PolicyStore` 的 Apple 侧实现）。
///
/// 双槽的意义：**新策略应用失败（或应用后用户报告异常）可以立刻回滚到上一份**。
/// 写盘顺序必须是"先把当前 active 挪成 previous，再写新的"——反过来中途失败就两份都丢。
final class FilePolicyStore: NSObject, PolicyStore {

    private let dir: URL
    private let activeFile: URL
    private let previousFile: URL

    init(dir: URL = SekbPaths.policyDir()) {
        self.dir = dir
        self.activeFile = dir.appendingPathComponent("policy-active.json")
        self.previousFile = dir.appendingPathComponent("policy-previous.json")
        super.init()
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    }

    func loadActive() -> String? { try? String(contentsOf: activeFile, encoding: .utf8) }

    func loadPrevious() -> String? { try? String(contentsOf: previousFile, encoding: .utf8) }

    func save(payloadJson: String) {
        if let active = loadActive() {
            try? active.write(to: previousFile, atomically: true, encoding: .utf8)
        }
        try? payloadJson.write(to: activeFile, atomically: true, encoding: .utf8)
    }

    func rollback() -> String? {
        guard let prev = loadPrevious() else { return nil }
        try? prev.write(to: activeFile, atomically: true, encoding: .utf8)
        try? FileManager.default.removeItem(at: previousFile)
        return prev
    }

    func clear() {
        try? FileManager.default.removeItem(at: activeFile)
        try? FileManager.default.removeItem(at: previousFile)
    }
}

/// 权限检查（Apple 侧）。
///
/// ⚠️ **如实返回 false**：Apple 侧当前只注册不需要危险权限的工具
/// （`device_time` / `kb_search`），所以这里永远不会被问到。
/// 之所以还要实现得这么"死"，是因为一旦将来注册了需要权限的工具，
/// "静默返回 true"就等于**把权限闸门拆了**——那是隐私边界，不能靠"暂时用不到"。
final class SekbPermissionChecker: NSObject, PermissionChecker {
    func has(permission: String) -> Bool { false }
}

/// 云端聊天端口（共享层 `CloudChat`）→ 走 [SekbApiBridge]。
///
/// 失败时**不抛异常**（Swift 侧接口本来也不能抛）：把错误交给编排器的 `error` 字段，
/// 由界面如实显示。抛出去只会让 App abort（原因见 `SekbApiBridge` 的说明）。
final class SekbCloudChat: NSObject, CloudChat {

    private let bridge: SekbApiBridge
    private let deviceToken: String

    init(bridge: SekbApiBridge, deviceToken: String) {
        self.bridge = bridge
        self.deviceToken = deviceToken
        super.init()
    }

    func stream(message: String, conversationId: String?,
                onToken: @escaping (String) -> Void,
                onThinking: @escaping (String) -> Void) -> CloudReply {
        let result = bridge.chatStream(deviceToken: deviceToken, message: message,
                                       conversationId: conversationId,
                                       onToken: onToken, onThinking: onThinking)
        switch result {
        case let .success(r):
            return CloudReply(execution: r.execution, conversationId: r.conversationId)
        case let .failure(err):
            // 通过一个"空执行位置 + 无会话 id"表达失败；错误文本由上报/界面体现
            // （共享层 `CloudReply` 没有 error 字段，编排器会把 text 为空判成 `empty` 信号）
            NSLog("%@ cloud_chat_failed — %@", SekbPlatform.selfTestPrefix, err.description)
            return CloudReply(execution: nil, conversationId: conversationId)
        }
    }
}

/// Apple 侧注册的端侧工具。
///
/// 与 Android 的差异**如实保留**：Android 有四个（`device_time` / `device_network` /
/// `device_contacts_search` / `kb_search`），Apple 侧只有两个：
/// - `device_contacts_search` 需要通讯录权限（`NSContactsUsageDescription` + 授权流程），
///   本期 DoD 不含设备工具，硬做等于给用户一个"点了没反应"的入口；
/// - `device_network` 需要 `Network.framework` 的异步路径监测，放进同步工具里会有竞态。
/// 两者都登记为缺口，**不假装存在**（`config.availableTools` 直接由注册表算出来，
/// 所以"模型以为有、实际没有"这种半生效状态不会发生）。
enum SekbDeviceTools {

    static func all() -> [DeviceTool] {
        [
            DeviceTool(
                name: "device_time",
                description: "查询设备当前日期与时间（含时区）",
                requiredPermission: nil,
                args: [:],
                requiredArgs: [],
                run: { _ in
                    let f = DateFormatter()
                    f.locale = Locale(identifier: "zh_CN")
                    f.timeZone = .current
                    f.dateFormat = "yyyy-MM-dd HH:mm:ss"
                    return ToolResult(ok: true, output: f.string(from: Date())
                        + "（" + TimeZone.current.identifier + "）",
                        denied: false, reason: "")
                }),
        ]
    }
}

/// 进程级装配（对应 Android 的 `AppContainer`）。
///
/// 依赖方向与 Android 一致：**UI → 容器 → { 编排器 → 路由 / 端侧 LLM / 云端 / 工具 }**。
/// 编排器与路由都不认识 SwiftUI，所以它们的行为与 Android 逐条可比。
final class SekbContainer {

    // MARK: - 端口

    let transport = UrlSessionTransport()
    let credentialStore = KeychainCredentialStore()
    let audit = PermissionAudit(capacity: 500)
    let toolEval = ToolCallEval()

    // MARK: - 嵌入与索引

    let embeddingProvider: EmbeddingProvider
    /// 界面/自检展示用（"模型/空间戳/维度"）。
    let providerDetail: String
    let knowledgeStore: FileKnowledgeStore
    let knowledgeIndex: KnowledgeIndex

    // MARK: - 策略（L1 热修）

    let policyStore: FilePolicyStore
    private let policyFetcher: PolicyFetcher
    private(set) var lastPolicyText: String = "尚未拉取（需要设备凭证）"

    // MARK: - 运行时

    private(set) var settings: SekbSettings
    private(set) var config: EdgeRuntimeConfig
    private(set) var retriever: Retriever
    private(set) var tools: ToolRegistry
    /// 当前阈值是"用户设的"还是"策略下发的"（设置页要如实显示，不能让用户以为改生效了）。
    private(set) var thresholdSource: String

    var bridge: SekbApiBridge { SekbApiBridge(transport: transport, baseUrl: config.sekbBaseUrl) }

    /// 已接入设备的 id（没有凭证时为空串）。
    ///
    /// ⚠️ 共享层的 `CredentialStore` 端口**只有 load/save/clear 三个方法**，
    /// 没有 `deviceId()`（Android 的 `KeystoreCredentialStore` 是在 app 层额外加的）。
    /// 所以这里自己取一次，不去动共享层接口。
    var deviceId: String { credentialStore.load()?.deviceId ?? "" }

    // MARK: - 构造

    init() {
        // 1) 设置
        settings = SekbSettings.load()

        // 2) 端侧嵌入：真模型优先，找不到就退回确定性桩（与 Android 同一套优先级判断）
        let stubSpace = EmbeddingSpace(id: "stub-hash@256", dim: 256)
        var provider: EmbeddingProvider = DeterministicEmbedding(space: stubSpace, isOnDevice: true)
        var detail = "确定性桩 stub-hash@256（未找到 ONNX 模型，检索数字不可与云端比）"
        if let modelDir = OrtBgeEmbedding.resolveDir() {
            let isInt8 = modelDir.path.contains("-int8")
            let spaceId = isInt8 ? OrtBgeEmbedding.INT8_SPACE_ID : EmbeddingSpace.companion.SERVER_SPACE_ID
            do {
                let onnx = try OrtBgeEmbedding(modelDir: modelDir, spaceId: spaceId)
                provider = onnx
                detail = "ONNX \(isInt8 ? "int8" : "fp32") 空间=\(spaceId) 维度=\(onnx.space.dim)"
                    + " 本机计算=\(onnx.isOnDevice) 目录=\(modelDir.lastPathComponent)"
            } catch {
                detail = "ONNX 加载失败（\(error)）→ 已退回确定性桩"
            }
        }
        embeddingProvider = provider
        providerDetail = detail

        // 3) 索引（文件持久化；空间不符时原地重嵌入）
        let store = FileKnowledgeStore(file: FileKnowledgeStore.defaultFile(), provider: provider)
        knowledgeStore = store
        knowledgeIndex = KnowledgeIndex(provider: provider, store: store,
                                        maxChars: 512, overlapChars: 64,
                                        registry: store, nowMillis: { KotlinLong(longLong: TimeFmt.nowMillis()) })

        // 4) 策略端口
        let policyStore = FilePolicyStore()
        self.policyStore = policyStore
        policyFetcher = PolicyFetcher(
            transport: transport,
            sekbBaseUrl: settings.sekbUrl,
            signingKey: SekbDefaults.policySigningKey,
            store: policyStore,
            now: { KotlinLong(longLong: TimeFmt.nowMillis()) },
            audit: { _ in })

        // 5) 阈值：策略里有就用策略的（那是云端标定过的），否则用本地默认
        let policyScore = policyFetcher.activeRetrievalMinScore()?.doubleValue
        let score = policyScore ?? settings.retrievalMinScore
        thresholdSource = policyScore != nil ? "策略下发" : "本地设置"

        // 6) 运行时对象（config 的空间戳**由嵌入器实际空间决定**，不靠"记得同步"）
        let initialConfig = SekbContainer.makeConfig(
            edgeUrl: settings.edgeUrl, sekbUrl: settings.sekbUrl,
            embeddingSpace: provider.space.id, availableTools: [])
        config = initialConfig

        let retriever = Retriever(provider: provider, store: store, minScore: score,
                                  cloudSpace: EmbeddingSpace.companion.SERVER,
                                  nowMillis: { KotlinLong(longLong: TimeFmt.nowMillis()) })
        self.retriever = retriever
        let tools = SekbContainer.buildTools(retriever: retriever, audit: audit)
        self.tools = tools
        // `availableTools` 与注册表**同源**：模型的工具幻觉判据必须等于"实际注册了什么"
        config = initialConfig.doCopy(
            edgeBaseUrl: initialConfig.edgeBaseUrl, sekbBaseUrl: initialConfig.sekbBaseUrl,
            models: initialConfig.models, maxInputTokens: initialConfig.maxInputTokens,
            maxOutputTokens: initialConfig.maxOutputTokens, maxTtftMs: initialConfig.maxTtftMs,
            guardChars: initialConfig.guardChars, disableThinking: initialConfig.disableThinking,
            preferEdge: initialConfig.preferEdge, deviceOnlyRoles: initialConfig.deviceOnlyRoles,
            escalateOn: initialConfig.escalateOn, availableTools: tools.names(),
            appVersion: initialConfig.appVersion, embeddingSpace: initialConfig.embeddingSpace)
    }

    // MARK: - 构造辅助

    static func makeConfig(edgeUrl: String, sekbUrl: String,
                           embeddingSpace: String, availableTools: Set<String>) -> EdgeRuntimeConfig {
        EdgeRuntimeConfig(
            edgeBaseUrl: edgeUrl,
            sekbBaseUrl: sekbUrl,
            models: ["short": "qwen3.5-2b", "default": "qwen3.5-4b", "quality": "qwen3.5-9b"],
            maxInputTokens: 2048,
            maxOutputTokens: 512,
            maxTtftMs: 800,
            guardChars: 60,
            disableThinking: true,
            preferEdge: true,
            deviceOnlyRoles: [],
            escalateOn: ["json_invalid", "empty", "degenerate", "timeout",
                         "low_confidence", "tool_hallucination"],
            availableTools: availableTools,
            appVersion: SekbDefaults.appVersion,
            embeddingSpace: embeddingSpace)
    }

    /// 检索器与工具必须**一起**重建：`KbSearchTool` 在创建时就抓住了检索器实例，
    /// 只换 retriever 不换 tools，工具里仍是旧阈值（这种"半生效"最难查）。
    static func buildTools(retriever: Retriever, audit: PermissionAudit) -> ToolRegistry {
        let kb = KbSearchTool.shared.create(retriever: retriever, deviceOnly: true, defaultTopK: 5)
        return ToolRegistry(tools: SekbDeviceTools.all() + [kb],
                            checker: SekbPermissionChecker(),
                            audit: audit,
                            now: { KotlinLong(longLong: TimeFmt.nowMillis()) })
    }

    // MARK: - 设置变更

    func updateEndpoints(edgeUrl: String, sekbUrl: String) {
        settings.edgeUrl = edgeUrl
        settings.sekbUrl = sekbUrl
        settings.save()
        config = config.doCopy(
            edgeBaseUrl: edgeUrl, sekbBaseUrl: sekbUrl,
            models: config.models, maxInputTokens: config.maxInputTokens,
            maxOutputTokens: config.maxOutputTokens, maxTtftMs: config.maxTtftMs,
            guardChars: config.guardChars, disableThinking: config.disableThinking,
            preferEdge: config.preferEdge, deviceOnlyRoles: config.deviceOnlyRoles,
            escalateOn: config.escalateOn, availableTools: config.availableTools,
            appVersion: config.appVersion, embeddingSpace: config.embeddingSpace)
    }

    /// 改检索阈值：**重建** retriever + tools（阈值是构造参数，没有 setter）。
    func applyRetrievalThreshold(_ value: Double) {
        settings.retrievalMinScore = value
        settings.save()
        thresholdSource = "本地设置"
        retriever = Retriever(provider: embeddingProvider, store: knowledgeStore,
                              minScore: value,
                              cloudSpace: EmbeddingSpace.companion.SERVER,
                              nowMillis: { KotlinLong(longLong: TimeFmt.nowMillis()) })
        tools = SekbContainer.buildTools(retriever: retriever, audit: audit)
    }

    // MARK: - 策略

    /// 拉取并应用策略。**不阻塞启动**：失败只保持现状（策略是锦上添花）。
    func refreshPolicy() -> String {
        guard let creds = credentialStore.load() else {
            lastPolicyText = "跳过：还没有设备凭证"
            return lastPolicyText
        }
        let result = policyFetcher.refresh(deviceToken: creds.deviceToken,
                                           current: config, deviceId: creds.deviceId)
        if let applied = result as? PolicyRefreshResultApplied {
            applyAppliedConfig(applied.config)
            lastPolicyText = "已应用 v\(applied.version)："
                + "应用 \(applied.appliedKeys.count) 项，忽略 \(applied.ignoredKeys.count) 项"
        } else if let skipped = result as? PolicyRefreshResultSkipped {
            lastPolicyText = "未应用：\(skipped.reason)"
        } else {
            lastPolicyText = "未应用：未知结果"
        }
        return lastPolicyText
    }

    /// 当前生效策略版本（0 = 还没拉到过）。
    func policyVersion() -> Int32 { policyFetcher.activeVersion() }

    private func applyAppliedConfig(_ applied: EdgeRuntimeConfig) {
        config = config.doCopy(
            edgeBaseUrl: config.edgeBaseUrl, sekbBaseUrl: config.sekbBaseUrl,
            models: applied.models, maxInputTokens: applied.maxInputTokens,
            maxOutputTokens: applied.maxOutputTokens, maxTtftMs: applied.maxTtftMs,
            guardChars: applied.guardChars, disableThinking: applied.disableThinking,
            preferEdge: applied.preferEdge, deviceOnlyRoles: config.deviceOnlyRoles,
            escalateOn: applied.escalateOn, availableTools: applied.availableTools,
            appVersion: config.appVersion, embeddingSpace: applied.embeddingSpace)
        if let score = policyFetcher.activeRetrievalMinScore()?.doubleValue {
            retriever = Retriever(provider: embeddingProvider, store: knowledgeStore,
                                  minScore: score,
                                  cloudSpace: EmbeddingSpace.companion.SERVER,
                                  nowMillis: { KotlinLong(longLong: TimeFmt.nowMillis()) })
            tools = SekbContainer.buildTools(retriever: retriever, audit: audit)
            thresholdSource = "策略下发"
        }
    }

    // MARK: - 云端客户端与编排

    /// 端侧端点是否可达（**只影响 UI 提示，不参与路由决策**）。
    func edgeReachable() -> Bool {
        OpenAiCompatibleEdgeLlm(transport: transport, baseUrl: config.edgeBaseUrl,
                                apiKey: "ollama", config: config,
                                now: { KotlinLong(longLong: TimeFmt.nowMillis()) }).isReachable()
    }

    /// 造一个编排器：端侧 = 本机/宿主 Ollama；云端 = SEKB SSE。
    /// 每轮新建（无状态、线程安全），省得为"配置改了要重建"再写一套失效逻辑。
    func orchestrator(deviceToken: String,
                      onRouteEvent: @escaping (RouteEventPayload) -> Void) -> ChatOrchestrator {
        let cfg = config
        let edge = OpenAiCompatibleEdgeLlm(transport: transport, baseUrl: cfg.edgeBaseUrl,
                                           apiKey: "ollama", config: cfg,
                                           now: { KotlinLong(longLong: TimeFmt.nowMillis()) })
        let cloud = SekbCloudChat(bridge: SekbApiBridge(transport: transport, baseUrl: cfg.sekbBaseUrl),
                                  deviceToken: deviceToken)
        return ChatOrchestrator(config: cfg, router: PlaneRouter(config: cfg), edgeLlm: edge,
                                cloud: cloud, tools: tools,
                                reporter: onRouteEvent,
                                now: { KotlinLong(longLong: TimeFmt.nowMillis()) },
                                newId: { Ids.shared.short(take: 12) })
    }
}
