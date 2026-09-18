package com.sekb.ondevice

import com.sekb.ondevice.chat.ChatOrchestrator
import com.sekb.ondevice.chat.CloudChat
import com.sekb.ondevice.chat.CloudReply
import com.sekb.ondevice.edge.ChatMessage
import com.sekb.ondevice.edge.OpenAiCompatibleEdgeLlm
import com.sekb.ondevice.model.ToolCall
import com.sekb.ondevice.route.PlaneRouter

/**
 * 端侧自检（模拟器/真机上的**功能与协议**验证入口，RFC §9.1 的 D10 决策）。
 *
 * 为什么要有它：端侧这些东西（真实 Ollama 流式、Keystore、权限、SSE）在 JVM 单测里
 * 验不了，而"点屏幕看结果"无法留存证据。所以把验证做成可脚本化的自检：
 *
 * ```bash
 * adb shell am start -n com.sekb.ondevice/.MainActivity --ez selftest true
 * adb logcat -d -s SEKB_SELFTEST
 * ```
 *
 * 每项输出 `PASS/FAIL/SKIP` 一行，末尾给出汇总——可以直接贴进验证记录。
 */
object SelfTest {

    private const val TAG = "SEKB_SELFTEST"

    data class Item(val name: String, val status: String, val detail: String)

    /**
     * @param cloud 云端 E2E 参数（email/password/sekbBaseUrl）。为空则跳过云端四项——
     *   端侧的七项不需要任何凭据，随时可跑。
     */
    data class CloudParams(val email: String, val password: String, val sekbBaseUrl: String)

    fun run(container: AppContainer, log: (String) -> Unit, cloud: CloudParams? = null): List<Item> {
        val items = mutableListOf<Item>()
        fun record(name: String, ok: Boolean?, detail: String) {
            val status = when (ok) { true -> "PASS"; false -> "FAIL"; null -> "SKIP" }
            items.add(Item(name, status, detail))
            log("[$status] $name — $detail")
        }

        val cfg = container.config
        val edge = OpenAiCompatibleEdgeLlm(container.transport, cfg.edgeBaseUrl, config = cfg)

        // 1. 端侧端点是否可达（模拟器里 10.0.2.2 = 宿主机）
        val reachable = runCatching { edge.isReachable() }.getOrDefault(false)
        record("edge_reachable", reachable, "${cfg.edgeBaseUrl} → $reachable")
        if (!reachable) return items

        // 2. 列出端侧可用模型（确认 qwen3.5 三档已 pull/导入）
        val models = runCatching {
            val resp = container.transport.get("${cfg.edgeBaseUrl}/models",
                mapOf("Authorization" to "Bearer ollama"), timeoutSeconds = 10)
            OpenAiCompatibleEdgeLlm.parseModelIds(resp.body)
        }.getOrDefault(emptyList())
        val hasSmall = models.any { it.contains("qwen3.5-2b") }
        record("edge_models", hasSmall, models.joinToString(", "))

        // 2.5 预热（消除冷启动；否则首字超时信号会把"刚加载"误判成"端侧不行"）
        val warmTarget = PlaneRouter(cfg).modelFor("default")
        val warmed = edge.warmup(warmTarget)
        record("edge_warmup", warmed, "$warmTarget keep_alive=30m")

        // 3. 真实端侧流式 + 前缀守卫（这是 M2 的主链路）
        val router = PlaneRouter(cfg)
        val edgeOnlyCloud = CloudChat { _, _, _, _ ->
            CloudReply(execution = null, conversationId = null)
        }
        val orchestrator = ChatOrchestrator(
            config = cfg, router = router, edgeLlm = edge, cloud = edgeOnlyCloud,
            tools = container.tools,
        )
        val decision = router.decide("chat", listOf("端侧推理为什么省电？"))
        record("router_decision", decision.isEdge,
            "plane=${decision.plane.wire} reason=${decision.reason} tier=${decision.tier} " +
                "model=${router.modelFor(decision.tier)}")

        val out = runCatching {
            orchestrator.send("端侧推理为什么省电？用一句话回答。")
        }.getOrNull()
        if (out == null) {
            record("edge_stream", false, "编排器抛异常")
        } else {
            record("edge_stream", out.plane.wire == "edge" && out.text.length > 5,
                "plane=${out.plane.wire} chars=${out.text.length} ttft=${out.edgeTtftMillis.toInt()}ms " +
                    "text=${out.text.take(40)}")
        }

        // 4. 工具闸门：联系人工具**未授权**必须被拦下（越权拦截率的来源）
        val contactsCall = ToolCall("device_contacts_search", mapOf("query" to "张"))
        val denied = container.tools.execute(contactsCall)
        record("permission_gate", denied.denied && !denied.ok,
            "denied=${denied.denied} reason=${denied.reason}")

        // 5. 无权限工具正常执行（证明闸门不是"一律拒绝"）
        val timeResult = container.tools.execute(ToolCall("device_time"))
        record("permissionless_tool", timeResult.ok, timeResult.output)

        // 6. 审计与指标
        val stats = container.tools.auditStats()
        record("audit_stats", stats.denied >= 1,
            "总调用=${stats.total} 拦截=${stats.denied} 越权拦截率=${(stats.deniedRate * 100).toInt()}%")

        // 7. 端侧 JSON 工具调用（约束模式的可行性）
        val jsonOut = runCatching {
            edge.complete(
                router.modelFor("short"),
                listOf(
                    ChatMessage.system("只输出 JSON，不要解释。"),
                    ChatMessage.user(
                        "调用 device_time 工具，只输出 {\"tool\":\"device_time\",\"args\":{}}",
                    ),
                ),
                maxTokens = 64, jsonMode = true,
            )
        }.getOrNull()
        val legal = jsonOut?.let { ToolCallJsonProbe.isLegal(it.text) } ?: false
        record("edge_tool_json", legal,
            "chars=${jsonOut?.text?.length ?: 0} head=${jsonOut?.text?.take(60)}")

        // ---------- 云端四项（协议 E2E：登录 → 接入 → 聊天 → 上报） ----------
        if (cloud == null) {
            record("cloud_login", null, "未提供账号（--es email/--es password）")
            return items
        }
        container.updateConfig(sekbBaseUrl = cloud.sekbBaseUrl)
        val api = container.api()

        val userToken = runCatching { api.login(cloud.email, cloud.password) }.getOrNull()
        record("cloud_login", userToken != null, cloud.sekbBaseUrl)
        if (userToken == null) return items

        val cred = runCatching {
            api.enroll(
                userToken = userToken, name = "模拟器自检", appVersion = cfg.appVersion,
                embeddingSpace = cfg.embeddingSpace,
            )
        }.getOrNull()
        record("cloud_enroll", cred != null,
            "device_id=${cred?.deviceId} ttl=${cred?.let { (it.expiresAtMillis - it.issuedAtMillis) / 3600000 }}h")
        if (cred == null) return items

        val tokens = StringBuilder()
        val thinking = StringBuilder()
        val chatAttempt = runCatching {
            api.chatStream(cred.deviceToken, "用一句话说明端侧推理的优势。", null,
                onToken = { tokens.append(it) }, onThinking = { thinking.append(it).append(" ") })
        }
        val chatResult = chatAttempt.getOrNull()
        val execution = chatResult?.execution
        // 失败时**必须把原因带出来**：静默失败会让"云端不通"和"模型答得慢"看起来一样
        // （第一版就是这样，白白多跑了一轮 10 分钟）。
        val detail = if (chatResult == null) {
            "失败：${chatAttempt.exceptionOrNull()?.let { "${it.javaClass.simpleName}: ${it.message}" } ?: "未知"}"
        } else {
            "chars=${tokens.length} conversation=${chatResult.conversationId} " +
                "execution=${execution?.primaryPlane ?: "无"} model=${execution?.model ?: "-"} " +
                "reason=${execution?.reason ?: "-"} thinking=${thinking.toString().take(30)} " +
                "text=${tokens.toString().take(40)}"
        }
        record("cloud_chat", tokens.isNotEmpty(), detail)

        val eventId = "selftest-" + System.currentTimeMillis()
        val reported = runCatching {
            api.reportRouteEvent(
                cred.deviceToken,
                com.sekb.ondevice.model.RouteEventPayload(
                    eventId = eventId, role = "chat", plane = "edge",
                    reason = "edge_preferred", model = router.modelFor("default"),
                    tier = "default", inputTokens = 42, outputTokens = 64, latencyMs = 83.0,
                    escalated = false, signals = emptyList(),
                    versions = mapOf("app_version" to cfg.appVersion,
                        "embedding_space" to cfg.embeddingSpace),
                ),
            )
        }.getOrDefault(false)
        record("cloud_route_event", reported, "event_id=$eventId")

        val cloudStats = runCatching { api.routeStats(cred.deviceToken) }.getOrNull()
        record("cloud_stats", cloudStats != null && cloudStats.isNotEmpty(),
            cloudStats?.entries?.take(4)?.joinToString(" ") { "${it.key}=${it.value}" } ?: "无")

        return items
    }

    fun summary(items: List<Item>): String {
        val pass = items.count { it.status == "PASS" }
        val fail = items.count { it.status == "FAIL" }
        val skip = items.count { it.status == "SKIP" }
        return "自检汇总：PASS=$pass FAIL=$fail SKIP=$skip"
    }
}

/** 工具调用 JSON 是否合法（自检里只做最小判定，正式统计走 `ToolCallEval`）。 */
private object ToolCallJsonProbe {
    fun isLegal(text: String): Boolean =
        com.sekb.ondevice.tools.ToolCallJson.parse(text) is com.sekb.ondevice.tools.ToolCallJson.Parsed.Ok
}
