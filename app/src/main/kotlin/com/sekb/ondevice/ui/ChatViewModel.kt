package com.sekb.ondevice.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.sekb.ondevice.BuildConfig
import com.sekb.ondevice.SekbApp
import com.sekb.ondevice.edge.ChatMessage
import com.sekb.ondevice.model.DeviceCredentials
import com.sekb.ondevice.model.ExecutionInfo
import com.sekb.ondevice.model.Plane
import com.sekb.ondevice.model.RouteEventPayload
import com.sekb.ondevice.tools.PermissionAudit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** 一条气泡。 */
data class Bubble(val fromUser: Boolean, val text: String, val execution: ExecutionInfo? = null)

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
) {
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

    init {
        refreshAudit()
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
                    val patched = if (last != null && !last.fromUser) {
                        st.bubbles.dropLast(1) + last.copy(text = result.text, execution = result.execution)
                    } else {
                        st.bubbles + Bubble(false, result.text, result.execution)
                    }
                    st.copy(
                        busy = false, thinking = "", bubbles = patched, audit = stats,
                        toolEvalSummary = container.toolEval.summary(),
                        lastReason = result.decision.let { d -> "${d.plane.wire} · ${d.reason}" } +
                            if (result.escalated) " · 升级：${result.escalateReason}" else "",
                        error = result.error,
                    )
                }
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

    companion object {
        /** 便于 UI 显示"这次在哪算的"；端侧完成与云端完成的文案不同。 */
        fun badgeOf(execution: ExecutionInfo?): String = when (execution?.primaryPlane) {
            Plane.EDGE.wire -> "本机完成"
            Plane.CLOUD.wire -> if ((execution.escalated) > 0) "已上云（端侧不达标）" else "云端完成"
            else -> ""
        }
    }
}
