package com.sekb.ondevice.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.sekb.ondevice.BuildConfig
import com.sekb.ondevice.SekbApp
import com.sekb.shared.edge.ChatMessage
import com.sekb.shared.model.DeviceCredentials
import com.sekb.shared.model.ExecutionInfo
import com.sekb.shared.model.Plane
import com.sekb.shared.model.RouteEventPayload
import com.sekb.shared.rag.DocumentInfo
import com.sekb.shared.tools.PermissionAudit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** 一条气泡。 */
data class Bubble(
    val fromUser: Boolean,
    val text: String,
    val execution: ExecutionInfo? = null,
    /** **客户端侧**是否发生过升级（与服务端回传的 execution.escalated 是两件事） */
    val escalated: Boolean = false,
    /** 这次回答引用到的本机检索来源（端侧 RAG 的可追溯性：答对答错都要能看出处） */
    val sources: List<RetrievalSources.Source> = emptyList(),
    /**
     * 路由阶段的原因码（如 `edge_preferred` / `input_over_edge_budget(600>512)`）。
     *
     * 为什么要**挂在气泡上**而不是只留一个全局"最近决策"：用户看到"已上云（端侧不达标）"时，
     * 问的是"**这一条**为什么上云"。全局字段只反映最后一次，往上翻就没了依据。
     */
    val reason: String = "",
    /** 升级阶段的原因码（如 `degenerate` / `timeout`）；未升级为空。与 [reason] 分开存，才能分别解释。 */
    val escalateReason: String = "",
)

/** UI 状态（单一数据源，避免散在各处的 mutable 字段）。 */
data class ChatUiState(
    val bubbles: List<Bubble> = emptyList(),
    val input: String = "",
    val busy: Boolean = false,
    val thinking: String = "",
    val edgeUrl: String = "",
    val sekbUrl: String = "",
    val email: String = "",
    val password: String = "",
    val deviceId: String = "",
    val enrolled: Boolean = false,
    val edgeReachable: Boolean? = null,
    val notice: String = "",
    val error: String = "",
    val audit: PermissionAudit.Stats = PermissionAudit.Stats(0, 0, 0, 0.0),
    val toolEvalSummary: String = "工具调用：暂无尝试",
    val lastReason: String = "",
    /** 已导入的本机文档（UI 列表用） */
    val documents: List<com.sekb.shared.rag.DocumentInfo> = emptyList(),
    val importing: Boolean = false,
    /** 当前会话标题（由第一条用户消息推导；空表示还没开始对话）。 */
    val conversationTitle: String = "",
    /** 本机已落盘的会话（最近更新在前）——供历史列表页展示。 */
    val conversations: List<com.sekb.shared.chat.ConversationSummary> = emptyList(),
) {
    val documentSummary: String get() =
        if (documents.isEmpty()) "还没有导入本机文档" else
            "已导入 ${documents.size} 份，共 ${documents.sumOf { it.chunks }} 段"

    val deniedRateText: String get() = "${(audit.deniedRate * 100).toInt()}%"
}

/**
 * UI 与编排器之间的唯一桥梁。
 *
 * 编排器是**同步**的（见 `ChatOrchestrator` 的说明），所以这里用
 * `withContext(Dispatchers.IO)` 把它挪到 IO 线程——UI 层负责线程，核心逻辑不掺异步。
 */
class ChatViewModel(app: Application) : AndroidViewModel(app) {

    private val container = (app as SekbApp).container

    private val _state = MutableStateFlow(
        ChatUiState(
            edgeUrl = container.config.edgeBaseUrl,
            sekbUrl = container.config.sekbBaseUrl,
            deviceId = container.credentialStore.deviceId(),
            enrolled = container.credentialStore.load() != null,
        ),
    )
    val state: StateFlow<ChatUiState> = _state.asStateFlow()

    /** 端侧历史（客户端自己维护；云端的会话由服务端按 conversationId 维护）。 */
    private val history = mutableListOf<ChatMessage>()

    /** 云端会话 ID：第一轮由服务端下发，之后必须带回去才能连续对话。 */
    private var conversationId: String? = null

    /**
     * 本地会话存储（**与云端 conversationId 是两件事**）。
     *
     * 云端那个是"服务端侧的会话"，只在接入账号后才有；端侧这一段（含**设备专属**的
     * 检索来源与执行位置）服务端并不知道，也不该上传。所以本地另有一套落盘，
     * 重启 App 后能恢复上次聊到哪——**这是聊天应用的基本预期**。
     */
    private val conversations: com.sekb.shared.chat.ConversationStore =
        com.sekb.ondevice.chat.FileConversationStore(
            com.sekb.ondevice.chat.FileConversationStore.defaultDir(app.filesDir),
        )

    /** 当前本地会话 id 与标题。 */
    private var localConversationId: String = com.sekb.shared.core.Ids.random()
    private var localTitle: String = ""

    init {
        refreshAudit()
        refreshDocuments()
        restoreLatestConversation()
    }

    /**
     * 启动时恢复最近一次会话。
     *
     * 只恢复**本机**这一段（气泡与执行位置）；云端会话上下文由服务端按 `conversationId` 维护，
     * 而那个 id 没有持久化——所以恢复后继续提问时，服务端会开一个新会话。
     * 这是有意的取舍：把云端会话 id 落盘会让"换设备/清数据"后的行为变得难以解释，
     * 而端侧要保住的是**用户在本机看到过什么**。
     */
    private fun restoreLatestConversation() {
        // 优先按"当前会话指针"恢复：用户点过「新对话」时，那段会话是空的、没有文件，
        // 此时必须**保持空**，而不是把上一段拉回来（否则用户会以为新对话没生效）。
        val pointed = runCatching { conversations.currentId() }.getOrNull()
        if (pointed != null) {
            val conv = runCatching { conversations.load(pointed) }.getOrNull()
            if (conv != null) loadConversation(conv)
            return
        }
        // 没有指针（旧版本数据）：退回"最近一段"
        val latest = runCatching { conversations.latest() }.getOrNull() ?: return
        loadConversation(latest)
    }

    /**
     * 把一段已落盘的会话装进当前界面。
     *
     * 抽出来是因为"启动恢复最近一段"与"历史列表里点开某一段"要做**完全相同**的事——
     * 分成两份实现必然漂移（例如一处忘了清 `history`，接着提问就会把两段对话混在一起）。
     */
    private fun loadConversation(latest: com.sekb.shared.chat.Conversation) {
        if (latest.messages.isEmpty()) return
        history.clear()
        latest.messages.forEach { m ->
            history.add(
                if (m.fromUser) ChatMessage.user(m.text) else ChatMessage.assistant(m.text),
            )
        }
        localConversationId = latest.id
        localTitle = latest.title
        runCatching { conversations.saveCurrentId(latest.id) }
        _state.update { st ->
            st.copy(
                conversationTitle = latest.title,
                bubbles = latest.messages.map { m ->
                    Bubble(
                        fromUser = m.fromUser,
                        text = m.text,
                        execution = if (m.plane.isEmpty()) null else com.sekb.shared.model.ExecutionInfo(
                            primaryPlane = m.plane, model = m.model, escalated = m.escalated,
                        ),
                        escalated = m.escalated > 0,
                        sources = m.sources.map {
                            RetrievalSources.Source(it.sourceId, it.score, it.snippet)
                        },
                        reason = m.reason,
                        escalateReason = m.escalateReason,
                    )
                },
            )
        }
    }

    /** 刷新本机会话列表（进历史页时调用）。 */
    fun refreshConversations() {
        val list = runCatching { conversations.list() }.getOrDefault(emptyList())
        _state.update { it.copy(conversations = list) }
    }

    /** 打开历史里的某一段会话。 */
    fun openConversation(id: String) {
        val conv = runCatching { conversations.load(id) }.getOrNull() ?: return
        loadConversation(conv)
        refreshConversations()
    }

    /** 删除一段会话；删的若是当前这段，就顺带清空界面（避免"看着已删的对话继续提问"）。 */
    fun deleteConversation(id: String) {
        runCatching { conversations.delete(id) }
        if (id == localConversationId) {
            newConversation()
        }
        refreshConversations()
    }

    /** 开一段新对话（旧的那段已经落盘，不会丢）。 */
    fun newConversation() {
        localConversationId = com.sekb.shared.core.Ids.random()
        localTitle = ""
        // 记下指针：新对话是空的（没有文件），所以重启后应当仍是空对话
        runCatching { conversations.saveCurrentId(localConversationId) }
        history.clear()
        conversationId = null
        _state.update {
            it.copy(bubbles = emptyList(), conversationTitle = "", input = "", thinking = "", error = "", notice = "")
        }
    }

    /**
     * 把当前会话落盘。
     *
     * 时间戳用注入的时钟（`Clock.nowMillis()`）而不是 `System.currentTimeMillis()`：
     * 后者是 JVM 专有，共享层编不过——而且会话排序要能被测试钉住。
     */
    private fun persistConversation() {
        val st = _state.value
        if (st.bubbles.isEmpty()) return
        val title = localTitle.ifEmpty {
            com.sekb.shared.chat.ConversationCodec.deriveTitle(
                st.bubbles.firstOrNull { it.fromUser }?.text.orEmpty(),
            ).also { localTitle = it }
        }
        val conv = com.sekb.shared.chat.Conversation(
            id = localConversationId,
            title = title,
            updatedAtMillis = com.sekb.shared.core.nowMillis(),
            messages = st.bubbles.map { b ->
                com.sekb.shared.chat.StoredMessage(
                    fromUser = b.fromUser,
                    text = b.text,
                    plane = b.execution?.primaryPlane.orEmpty(),
                    model = b.execution?.model.orEmpty(),
                    escalated = b.execution?.escalated ?: (if (b.escalated) 1 else 0),
                    reason = b.reason,
                    escalateReason = b.escalateReason,
                    sources = b.sources.map {
                        com.sekb.shared.chat.StoredSource(it.sourceId, it.score, it.snippet)
                    },
                )
            },
        )
        runCatching {
            conversations.save(conv)
            conversations.saveCurrentId(conv.id)
        }
    }

    // ---------- 本机文档（端侧 RAG 的输入） ----------

    /**
     * 导入一份本机文档。
     *
     * 文档 id 用**文件名**：同名文件重复导入视为更新（切片 id 是 `源#序号`，天然覆盖）。
     * 这是个有意的取舍——用内容哈希能区分同名不同内容的文件，但用户会看到两份同名条目，
     * 反而困惑；等真机上出现真实用例再改（见 BACKLOG）。
     */
    fun importUri(uri: android.net.Uri) {
        viewModelScope.launch {
            _state.update { it.copy(importing = true, error = "", notice = "") }
            val app = getApplication<android.app.Application>()
            val result = withContext(Dispatchers.IO) { DocumentImporter.read(app, uri) }
            when (result) {
                is DocumentImporter.Result.Rejected ->
                    _state.update { it.copy(importing = false, error = "「${result.name}」未导入：${result.reason}") }

                is DocumentImporter.Result.Ok -> {
                    val started = System.currentTimeMillis()
                    val report = withContext(Dispatchers.IO) {
                        runCatching {
                            container.knowledgeIndex.ingest(
                                sourceId = result.name, text = result.text,
                                name = result.name, sizeBytes = result.sizeBytes, deviceOnly = true,
                            )
                        }
                    }
                    val docs = withContext(Dispatchers.IO) { container.knowledgeIndex.documents() }
                    report.onSuccess { r ->
                        val ms = System.currentTimeMillis() - started
                        _state.update {
                            it.copy(
                                importing = false, documents = docs,
                                notice = "已导入「${result.name}」：${r.chunks} 段，嵌入耗时 ${ms}ms" +
                                    if (r.chunks == 0) "（内容为空，未入库）" else "",
                            )
                        }
                    }.onFailure { e ->
                        _state.update { it.copy(importing = false, error = "导入失败：${e.message}") }
                    }
                }
            }
        }
    }

    fun deleteDocument(id: String) {
        viewModelScope.launch {
            val removed = withContext(Dispatchers.IO) { container.knowledgeIndex.remove(id) }
            val docs = withContext(Dispatchers.IO) { container.knowledgeIndex.documents() }
            _state.update {
                it.copy(documents = docs, notice = "已删除「$id」的 $removed 段索引")
            }
        }
    }

    fun refreshDocuments() {
        viewModelScope.launch {
            val docs = withContext(Dispatchers.IO) {
                runCatching { container.knowledgeIndex.documents() }.getOrDefault(emptyList())
            }
            _state.update { it.copy(documents = docs) }
        }
    }

    fun onInputChange(value: String) = _state.update { it.copy(input = value) }
    fun onEdgeUrlChange(value: String) = _state.update { it.copy(edgeUrl = value) }
    fun onSekbUrlChange(value: String) = _state.update { it.copy(sekbUrl = value) }
    fun onEmailChange(value: String) = _state.update { it.copy(email = value) }
    fun onPasswordChange(value: String) = _state.update { it.copy(password = value) }

    fun applyConfig() {
        container.updateConfig(_state.value.edgeUrl, _state.value.sekbUrl)
        _state.update { it.copy(notice = "配置已生效（本轮开始使用新地址）") }
        probeEdge()
    }

    /** 端侧端点是否可达（只影响 UI 提示，不影响路由决策）。 */
    fun probeEdge() {
        viewModelScope.launch {
            val reachable = withContext(Dispatchers.IO) { runCatching { container.edgeReachable() }.getOrDefault(false) }
            _state.update { it.copy(edgeReachable = reachable) }
        }
    }

    /**
     * 换设备凭证：用账号先登录拿用户 token，再 enroll 换长效设备 token。
     * 设备 token 加密落盘（Keystore），页面只显示 device_id。
     */
    fun enroll() {
        val s = _state.value
        if (s.email.isBlank() || s.password.isBlank()) {
            _state.update { it.copy(error = "请先填账号与密码") }
            return
        }
        viewModelScope.launch {
            _state.update { it.copy(busy = true, error = "", notice = "") }
            val outcome = withContext(Dispatchers.IO) {
                runCatching {
                    val api = container.api()
                    val userToken = api.login(s.email, s.password)
                    api.enroll(
                        userToken = userToken, name = "Android 端侧宿主",
                        appVersion = BuildConfig.VERSION_NAME,
                        embeddingSpace = container.config.embeddingSpace,
                    )
                }
            }
            outcome.onSuccess { cred: DeviceCredentials ->
                container.credentialStore.save(cred)
                _state.update {
                    it.copy(
                        busy = false, enrolled = true, deviceId = cred.deviceId,
                        notice = "设备已接入：${cred.deviceId}",
                        password = "",
                    )
                }
            }.onFailure { e ->
                _state.update { it.copy(busy = false, error = "接入失败：${e.message}") }
            }
        }
    }

    /** 发一条消息：决策 → 端侧流式（守卫）→ 必要时改道云端 → 上报。 */
    fun send() {
        val s = _state.value
        val message = s.input.trim()
        if (message.isEmpty() || s.busy) return
        val cred = container.credentialStore.load()
        if (cred == null) {
            _state.update { it.copy(error = "还没有设备凭证：请先在上方接入设备") }
            return
        }

        _state.update {
            it.copy(
                bubbles = it.bubbles + Bubble(fromUser = true, text = message),
                input = "", busy = true, error = "", thinking = "", notice = "",
            )
        }

        viewModelScope.launch {
            val outcome = withContext(Dispatchers.IO) {
                runCatching {
                    val orchestrator = container.orchestrator(
                        deviceToken = cred.deviceToken,
                        onRouteEvent = { event: RouteEventPayload ->
                            // 上报失败**不能**影响回答：事件重传由服务端幂等键兜住
                            runCatching { container.api().reportRouteEvent(cred.deviceToken, event) }
                        },
                    )
                    orchestrator.send(
                        userMessage = message,
                        history = history.toList(),
                        conversationId = conversationId,
                        onToken = { piece ->
                            // 流式增量：直接更新最后一条气泡
                            _state.update { st ->
                                val last = st.bubbles.lastOrNull()
                                if (last != null && !last.fromUser) {
                                    st.copy(bubbles = st.bubbles.dropLast(1) + last.copy(text = last.text + piece))
                                } else {
                                    st.copy(bubbles = st.bubbles + Bubble(fromUser = false, text = piece))
                                }
                            }
                        },
                        onThinking = { text -> _state.update { it.copy(thinking = text) } },
                    )
                }
            }

            outcome.onSuccess { result ->
                // 历史只保留端侧这一段（云端历史由服务端按 conversationId 维护）
                history.add(ChatMessage.user(message))
                history.add(ChatMessage.assistant(result.text))
                conversationId = result.conversationId
                // 评测：工具调用合法率的分母是"尝试次数"
                container.toolEval.record(
                    attempted = result.toolCallAttempted,
                    legal = result.toolCallLegal,
                )
                val stats = container.audit.stats()
                _state.update { st ->
                    val last = st.bubbles.lastOrNull()
                    val sources = RetrievalSources.parse(result.toolResults)
                    val reasonLine = decisionLine(result)
                    val patched = if (last != null && !last.fromUser) {
                        st.bubbles.dropLast(1) +
                            last.copy(text = result.text, execution = result.execution,
                                escalated = result.escalated, sources = sources,
                                reason = result.decision.reason,
                                escalateReason = result.escalateReason)
                    } else {
                        st.bubbles + Bubble(false, result.text, result.execution, result.escalated,
                            sources, result.decision.reason, result.escalateReason)
                    }
                    st.copy(
                        busy = false, thinking = "", bubbles = patched, audit = stats,
                        conversationTitle = localTitle.ifEmpty { st.conversationTitle },
                        toolEvalSummary = container.toolEval.summary(),
                        lastReason = reasonLine,
                        error = result.error,
                    )
                }
                // 落盘放在状态更新**之后**：这样存下来的就是用户实际看到的那一版
                // （含执行位置、引用来源、失败文本）。写盘失败不影响对话本身。
                persistConversation()
            }.onFailure { e ->
                _state.update {
                    it.copy(busy = false, thinking = "", error = "请求失败：${e.message}")
                }
            }
        }
    }

    fun refreshAudit() {
        _state.update {
            it.copy(audit = container.audit.stats(), toolEvalSummary = container.toolEval.summary())
        }
    }

    fun clearAudit() {
        container.audit.clear()
        container.toolEval.reset()
        refreshAudit()
    }

    /**
     * 把一次结果压成"平面 · 原因"一行。
     *
     * `clientEscalated` 必须单独看：服务端回传的 `execution.escalated` 只统计**服务端内部**的升级，
     * 而端侧宿主自己失败后改道云端这件事服务端并不知道——只看服务端字段会把
     * "客户端刚因为端侧不可用改道"显示成平平无奇的"云端完成"。
     */
    private fun decisionLine(result: com.sekb.shared.chat.ChatOutcome): String {
        val head = "${result.decision.plane.wire} · ${result.decision.reason}"
        val tail = when {
            result.escalated && result.escalateReason.isNotEmpty() -> " · ${result.escalateReason}"
            else -> ""
        }
        return head + tail
    }

    companion object {
        /**
         * 便于 UI 显示"这次在哪算的"。
         *
         * `clientEscalated` 必须一起看：服务端回传的 `execution.escalated` 只统计**服务端内部**
         * 的升级，而端侧宿主自己失败后改道云端这件事服务端并不知道——
         * 只看服务端字段会把"客户端刚因为端侧不可用改道"显示成平平无奇的"云端完成"。
         */
        fun badgeOf(execution: ExecutionInfo?, clientEscalated: Boolean = false): String =
            when {
                execution?.primaryPlane == Plane.EDGE.wire -> "本机完成"
                execution?.primaryPlane == Plane.CLOUD.wire || clientEscalated ->
                    if (clientEscalated || (execution?.escalated ?: 0) > 0) "已上云（端侧不达标）" else "云端完成"
                else -> ""
            }
    }
}
