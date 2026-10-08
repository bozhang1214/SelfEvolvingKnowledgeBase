import Foundation
import SwiftUI
import SharedCore

/// 一条气泡（对应 Android 的 `Bubble`）。
struct ChatBubble: Identifiable {
    let id = UUID()
    let fromUser: Bool
    var text: String
    /// 实际完成的平面（`execution.primaryPlane`）——徽章与解释**必须同源**
    var execution: ExecutionInfo?
    /// **客户端侧**是否发生过升级（与服务端回传的 `execution.escalated` 是两件事）
    var escalated: Bool = false
    /// 这次回答引用到的本机检索来源（端侧 RAG 的可追溯性：答对答错都要能看出处）
    var sources: [RetrievalSources.Source] = []
    /// 路由阶段的原因码（`edge_preferred` / `input_over_edge_budget(600>512)` …）
    ///
    /// 为什么挂在气泡上而不是只留一个全局"最近决策"：用户看到"已上云（端侧不达标）"时，
    /// 问的是"**这一条**为什么上云"。全局字段只反映最后一次，往上翻就没了依据。
    var reason: String = ""
    /// 升级阶段原因码（`degenerate` / `timeout`）；未升级为空。与 `reason` 分开存才能分别解释。
    var escalateReason: String = ""

    /// 与 Android `ChatViewModel.badgeOf` 同口径。
    var badge: String {
        let plane = execution?.primaryPlane ?? ""
        if plane == Plane.edge.wire { return "本机完成" }
        if plane == Plane.cloud.wire || escalated {
            let serverEscalated = (execution?.escalated ?? 0) > 0
            return (escalated || serverEscalated) ? "已上云（端侧不达标）" : "云端完成"
        }
        return ""
    }

    var completedOnDevice: Bool { (execution?.primaryPlane ?? "") == Plane.edge.wire }

    var explanation: String {
        PlaneExplain.outcome(completedOnDevice: completedOnDevice,
                             escalated: escalated || (execution?.escalated ?? 0) > 0,
                             decisionReason: reason,
                             escalateReason: escalateReason)
    }
}

/// UI 状态（单一数据源，避免散在各处的 mutable 字段）。
struct SekbUiState {
    var bubbles: [ChatBubble] = []
    var input: String = ""
    var busy: Bool = false
    var thinking: String = ""
    var edgeUrl: String = SekbDefaults.edgeBaseUrl
    var sekbUrl: String = SekbDefaults.sekbBaseUrl
    var email: String = ""
    var password: String = ""
    var deviceId: String = ""
    var enrolled: Bool = false
    var edgeReachable: Bool? = nil
    var notice: String = ""
    var error: String = ""
    var auditTotal: Int32 = 0
    var auditDenied: Int32 = 0
    var deniedRate: Double = 0
    var toolEvalSummary: String = "工具调用：暂无尝试"
    var documents: [DocumentInfo] = []
    var importing: Bool = false
    var conversationTitle: String = ""
    var conversations: [ConversationSummary] = []
    var lastReason: String = ""

    var documentSummary: String {
        documents.isEmpty ? "还没有导入本机文档"
            : "已导入 \(documents.count) 份，共 \(documents.reduce(0) { $0 + Int($1.chunks) }) 段"
    }

    var deniedRateText: String { "\(Int(deniedRate * 100))%" }
}

/// 自检结果的可观察容器：`App.init` 与设置页的"重新运行"共用同一份。
///
/// 为什么要单独一个 store：自检在 `App.init` 的后台队列里跑，界面上要能**看到并重跑**它；
/// 把结果放在视图本地状态里会导致"App 启动时跑的那一次看不见"。
final class SekbSelfTestStore: ObservableObject {
    static let shared = SekbSelfTestStore()

    @Published var results: [CheckResult] = []
    @Published var running = false
    /// 上一次跑自检的时刻（界面显示"什么时候跑的"）
    @Published var lastRunAt: Date?

    var pass: Int { results.filter { $0.ok == true }.count }
    var fail: Int { results.filter { $0.ok == false }.count }
    var skip: Int { results.filter { $0.ok == nil }.count }
    var summary: String { "PASS=\(pass) FAIL=\(fail) SKIP=\(skip)" }
}

/// UI 与编排器之间的唯一桥梁（对应 Android 的 `ChatViewModel`）。
///
/// 编排器是**同步**的（见 `ChatOrchestrator` 的说明），所以这里统一把它挪到后台队列；
/// UI 层负责线程，核心逻辑不掺异步——两端同一分层。
final class SekbViewModel: ObservableObject {

    @Published var state: SekbUiState
    @Published var selfTest = SekbSelfTestStore.shared

    private let container = SekbContainer()
    private let queue = DispatchQueue(label: "tech.bos-studio.sekb.chat", qos: .userInitiated)

    /// 端侧历史（客户端自己维护；云端的会话由服务端按 conversationId 维护）。
    private var history: [ChatMessage] = []
    /// 云端会话 ID：第一轮由服务端下发，之后必须带回去才能连续对话。
    private var conversationId: String?
    /// 本地会话存储（**与云端 conversationId 是两件事**）。
    private let conversations = FileConversationStore(dir: FileConversationStore.defaultDir())
    private var localConversationId: String = Ids.shared.random()
    private var localTitle: String = ""

    init() {
        state = SekbUiState()
        state.edgeUrl = container.settings.edgeUrl
        state.sekbUrl = container.settings.sekbUrl
        state.deviceId = container.deviceId
        state.enrolled = container.credentialStore.load() != nil
        selfTest = SekbSelfTestStore.shared
        // 仅供视觉验证：`SEKB_DEMO_SEED=1` 时写入演示数据（默认关闭，见 `SekbDemoSeed`）。
        // 必须在 restoreLatestConversation() **之前**：种子会设好"当前会话指针"。
        SekbDemoSeed.applyIfRequested(container: container, store: conversations)
        refreshAudit()
        refreshDocuments()
        restoreLatestConversation()
    }

    // MARK: - 会话持久化

    /// 启动时恢复最近一次会话。
    ///
    /// 优先按"当前会话指针"恢复：用户点过「新对话」时那段会话是空的、没有文件，
    /// 此时必须**保持空**，而不是把上一段拉回来（否则用户会以为新对话没生效）。
    private func restoreLatestConversation() {
        if let pointed = conversations.currentId(), let conv = conversations.load(id: pointed) {
            loadConversation(conv)
            return
        }
        if let latest = conversations.latest() { loadConversation(latest) }
    }

    /// 把一段已落盘的会话装进当前界面。
    ///
    /// 抽出来是因为"启动恢复"与"历史列表里点开某一段"要做**完全相同**的事——
    /// 两份实现必然漂移（例如一处忘了清 `history`，接着提问就会把两段对话混在一起）。
    private func loadConversation(_ conv: Conversation) {
        if conv.messages.isEmpty { return }
        history.removeAll()
        for m in conv.messages {
            history.append(m.fromUser ? ChatMessage.companion.user(content: m.text)
                                      : ChatMessage.companion.assistant(content: m.text))
        }
        localConversationId = conv.id
        localTitle = conv.title
        conversations.saveCurrentId(id: conv.id)
        state.conversationTitle = conv.title
        state.bubbles = conv.messages.map { m in
            ChatBubble(fromUser: m.fromUser,
                       text: m.text,
                       execution: m.plane.isEmpty ? nil : ExecutionInfo(
                           primaryPlane: m.plane, primaryRole: "", model: m.model, reason: m.reason,
                           tier: "", escalated: m.escalated, byPlane: [:],
                           edgeDecided: 0, edgeCompleted: 0, latencyMs: 0),
                       escalated: m.escalated > 0,
                       sources: m.sources.map {
                           RetrievalSources.Source(sourceId: $0.sourceId, score: $0.score, snippet: $0.snippet)
                       },
                       reason: m.reason,
                       escalateReason: m.escalateReason)
        }
    }

    func refreshConversations() {
        state.conversations = conversations.list()
    }

    func openConversation(id: String) {
        guard let conv = conversations.load(id: id) else { return }
        loadConversation(conv)
        refreshConversations()
    }

    /// 删除一段会话；删的若是当前这段，就顺带清空界面（避免"看着已删的对话继续提问"）。
    func deleteConversation(id: String) {
        _ = conversations.delete(id: id)
        if id == localConversationId { newConversation() }
        refreshConversations()
    }

    /// 开一段新对话（旧的那段已经落盘，不会丢）。
    func newConversation() {
        localConversationId = Ids.shared.random()
        localTitle = ""
        // 记下指针：新对话是空的（没有文件），所以重启后应当仍是空对话
        conversations.saveCurrentId(id: localConversationId)
        history.removeAll()
        conversationId = nil
        state.bubbles = []
        state.conversationTitle = ""
        state.input = ""
        state.thinking = ""
        state.error = ""
        state.notice = ""
    }

    /// 把当前会话落盘（放在状态更新**之后**：存下来的就是用户实际看到的那一版）。
    private func persistConversation() {
        let st = state
        if st.bubbles.isEmpty { return }
        let title = localTitle.isEmpty
            ? ConversationCodec.shared.deriveTitle(firstUserMessage: st.bubbles.first { $0.fromUser }?.text ?? "")
            : localTitle
        localTitle = title
        let conv = Conversation(
            id: localConversationId,
            title: title,
            updatedAtMillis: TimeFmt.nowMillis(),
            messages: st.bubbles.map { b in
                StoredMessage(
                    fromUser: b.fromUser,
                    text: b.text,
                    plane: b.execution?.primaryPlane ?? "",
                    model: b.execution?.model ?? "",
                    escalated: b.execution?.escalated ?? (b.escalated ? 1 : 0),
                    reason: b.reason,
                    escalateReason: b.escalateReason,
                    sources: b.sources.map { StoredSource(sourceId: $0.sourceId, score: $0.score, snippet: $0.snippet) })
            })
        conversations.save(c: conv)
        conversations.saveCurrentId(id: conv.id)
    }

    // MARK: - 本机文档

    /// 导入一份本机文档。文档 id 用**文件名**：同名重复导入视为更新
    /// （切片 id 是 `源#序号`，天然覆盖）——与 Android 同一取舍。
    func importDocument(url: URL) {
        state.importing = true
        state.error = ""
        state.notice = ""
        queue.async { [weak self] in
            guard let self else { return }
            let started = Date()
            let outcome = SekbDocumentImporter.read(url: url)
            switch outcome {
            case let .rejected(name, reason):
                self.onMain {
                    self.state.importing = false
                    self.state.error = "「\(name)」未导入：\(reason)"
                }
            case let .ok(name, text, sizeBytes):
                let report = self.container.knowledgeIndex.ingest(
                    sourceId: name, text: text, name: name, sizeBytes: sizeBytes, deviceOnly: true)
                let ms = Int(Date().timeIntervalSince(started) * 1000)
                let docs = self.container.knowledgeIndex.documents()
                self.onMain {
                    self.state.importing = false
                    self.state.documents = docs
                    self.state.notice = "已导入「\(name)」：\(report.chunks) 段，嵌入耗时 \(ms)ms"
                        + (report.chunks == 0 ? "（内容为空，未入库）" : "")
                }
            }
        }
    }

    func deleteDocument(id: String) {
        queue.async { [weak self] in
            guard let self else { return }
            let removed = self.container.knowledgeIndex.remove(sourceId: id)
            let docs = self.container.knowledgeIndex.documents()
            self.onMain {
                self.state.documents = docs
                self.state.notice = "已删除「\(id)」的 \(removed) 段索引"
            }
        }
    }

    func refreshDocuments() {
        queue.async { [weak self] in
            guard let self else { return }
            let docs = self.container.knowledgeIndex.documents()
            self.onMain { self.state.documents = docs }
        }
    }

    // MARK: - 输入与配置

    func onInputChange(_ value: String) { state.input = value }
    func onEdgeUrlChange(_ value: String) { state.edgeUrl = value }
    func onSekbUrlChange(_ value: String) { state.sekbUrl = value }
    func onEmailChange(_ value: String) { state.email = value }
    func onPasswordChange(_ value: String) { state.password = value }

    func applyConfig() {
        container.updateEndpoints(edgeUrl: state.edgeUrl, sekbUrl: state.sekbUrl)
        state.notice = "配置已生效（本轮开始使用新地址）"
        probeEdge()
    }

    /// 端侧端点是否可达（**只影响 UI 提示，不影响路由决策**）。
    func probeEdge() {
        state.edgeReachable = nil
        queue.async { [weak self] in
            guard let self else { return }
            let reachable = self.container.edgeReachable()
            self.onMain { self.state.edgeReachable = reachable }
        }
    }

    func applyThreshold(_ value: Double) {
        container.applyRetrievalThreshold(value)
        state.notice = "检索阈值已改为 \(String(format: "%.2f", value))（检索器与工具已重建）"
    }

    // MARK: - 设备接入

    /// 换设备凭证：账号密码 → 用户 token → 设备 token；凭证进 Keychain，界面只显示 device_id。
    func enroll() {
        if state.email.trimmingCharacters(in: .whitespaces).isEmpty || state.password.isEmpty {
            state.error = "请先填账号与密码"
            return
        }
        state.busy = true
        state.error = ""
        state.notice = ""
        let email = state.email
        let password = state.password
        queue.async { [weak self] in
            guard let self else { return }
            let bridge = self.container.bridge
            switch bridge.login(email: email, password: password) {
            case let .failure(err):
                self.onMain {
                    self.state.busy = false
                    self.state.error = "登录失败：\(err.detail)"
                }
            case let .success(userToken):
                let enrolled = bridge.enroll(userToken: userToken,
                                             name: SekbPlatform.deviceName,
                                             platform: SekbPlatform.apiPlatform,
                                             appVersion: SekbDefaults.appVersion,
                                             embeddingSpace: self.container.config.embeddingSpace)
                switch enrolled {
                case let .failure(err):
                    self.onMain {
                        self.state.busy = false
                        self.state.error = "接入失败：\(err.detail)"
                    }
                case let .success(cred):
                    self.container.credentialStore.save(credentials: cred)
                    self.onMain {
                        self.state.busy = false
                        self.state.enrolled = true
                        self.state.deviceId = cred.deviceId
                        self.state.password = ""
                        self.state.notice = "设备已接入：\(cred.deviceId)"
                    }
                    self.refreshPolicy()
                }
            }
        }
    }

    /// 刷新 L1 策略（失败只保持现状——策略是锦上添花，不能让"没拉到"变成不能聊天）。
    func refreshPolicy() {
        queue.async { [weak self] in
            guard let self else { return }
            let text = self.container.refreshPolicy()
            self.onMain {
                self.state.notice = "策略：\(text)"
                self.refreshAudit()
            }
        }
    }

    func policyVersionText() -> String {
        let v = container.policyVersion()
        return v == 0 ? "尚未应用（使用本地默认 \(String(format: "%.2f", container.settings.retrievalMinScore))）"
                      : "v\(v)（\(container.thresholdSource)）"
    }

    var providerDetail: String { container.providerDetail }
    var thresholdSourceText: String { container.thresholdSource }
    var lastPolicyText: String { container.lastPolicyText }
    var currentThreshold: Double { container.settings.retrievalMinScore }

    // MARK: - 发送

    /// 发一条消息：决策 → 端侧流式（守卫）→ 必要时改道云端 → 上报。
    func send() {
        let message = state.input.trimmingCharacters(in: .whitespacesAndNewlines)
        if message.isEmpty || state.busy { return }
        guard let cred = container.credentialStore.load() else {
            state.error = "还没有设备凭证：请先到「设置」接入设备"
            return
        }
        state.bubbles.append(ChatBubble(fromUser: true, text: message))
        state.input = ""
        state.busy = true
        state.error = ""
        state.thinking = ""
        state.notice = ""

        queue.async { [weak self] in
            guard let self else { return }
            let orchestrator = self.container.orchestrator(deviceToken: cred.deviceToken) { event in
                // 上报失败**不能**影响回答：事件重传由服务端幂等键兜住
                _ = self.container.bridge.reportRouteEvent(deviceToken: cred.deviceToken, event: event)
            }
            let historySnapshot = self.history
            let outcome = orchestrator.send(
                userMessage: message,
                history: historySnapshot,
                conversationId: self.conversationId,
                deviceData: false,
                role: "chat",
                onToken: { piece in
                    self.onMain { self.appendAssistantToken(piece) }
                },
                onThinking: { text in
                    self.onMain { self.state.thinking = text }
                })

            self.history.append(ChatMessage.companion.user(content: message))
            self.history.append(ChatMessage.companion.assistant(content: outcome.text))
            self.conversationId = outcome.conversationId
            // 评测：工具调用合法率的分母是"尝试次数"
            self.container.toolEval.record(attempted: outcome.toolCallAttempted,
                                           legal: outcome.toolCallLegal,
                                           hallucinated: false)
            let stats = self.container.audit.stats()
            let sources = RetrievalSources.parse(outcome.toolResults)
            let toolSummary = self.container.toolEval.summary()
            let decisionLine = "\(outcome.decision.plane.wire) · \(outcome.decision.reason)"
                + (outcome.escalated && !outcome.escalateReason.isEmpty
                   ? " · \(outcome.escalateReason)" : "")

            self.onMain {
                let patched = ChatBubble(fromUser: false, text: outcome.text,
                                         execution: outcome.execution,
                                         escalated: outcome.escalated,
                                         sources: sources,
                                         reason: outcome.decision.reason,
                                         escalateReason: outcome.escalateReason)
                if let last = self.state.bubbles.last, !last.fromUser {
                    self.state.bubbles[self.state.bubbles.count - 1] = patched
                } else {
                    self.state.bubbles.append(patched)
                }
                self.state.busy = false
                self.state.thinking = ""
                self.state.auditTotal = stats.total
                self.state.auditDenied = stats.denied
                self.state.deniedRate = stats.deniedRate
                self.state.toolEvalSummary = toolSummary
                self.state.lastReason = decisionLine
                self.state.error = outcome.error
                if !self.localTitle.isEmpty { self.state.conversationTitle = self.localTitle }
                // 落盘放在状态更新**之后**：这样存下来的就是用户实际看到的那一版
                // （含执行位置、引用来源、失败文本）。写盘失败不影响对话本身。
                self.persistConversation()
            }
        }
    }

    /// 流式增量：直接更新最后一条助手气泡（没有就新建）。
    private func appendAssistantToken(_ piece: String) {
        if let last = state.bubbles.last, !last.fromUser {
            state.bubbles[state.bubbles.count - 1].text += piece
        } else {
            state.bubbles.append(ChatBubble(fromUser: false, text: piece))
        }
    }

    // MARK: - 审计与自检

    func refreshAudit() {
        let stats = container.audit.stats()
        state.auditTotal = stats.total
        state.auditDenied = stats.denied
        state.deniedRate = stats.deniedRate
        state.toolEvalSummary = container.toolEval.summary()
        state.deviceId = container.deviceId
        state.enrolled = container.credentialStore.load() != nil
    }

    func clearAudit() {
        container.audit.clear()
        container.toolEval.reset()
        refreshAudit()
    }

    /// 重新跑一遍自检（设置页入口；与 App 启动时的那次**同一份代码**）。
    func rerunSelfTest() {
        if selfTest.running { return }
        selfTest.running = true
        DispatchQueue.global(qos: .userInitiated).async {
            _ = runAndReportSelfTest()
            // `runAndReportSelfTest` 内部已经把结果写回 store（主线程）
        }
    }

    // MARK: - 工具

    private func onMain(_ block: @escaping () -> Void) {
        if Thread.isMainThread { block() } else { DispatchQueue.main.async(execute: block) }
    }
}
