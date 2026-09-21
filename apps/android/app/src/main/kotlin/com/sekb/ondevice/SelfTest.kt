package com.sekb.ondevice

import com.sekb.shared.chat.ChatOrchestrator
import com.sekb.shared.chat.CloudChat
import com.sekb.shared.chat.CloudReply
import com.sekb.shared.edge.ChatMessage
import com.sekb.shared.edge.OpenAiCompatibleEdgeLlm
import com.sekb.shared.model.ToolCall
import com.sekb.shared.route.PlaneRouter

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
/** 自检需要 Android `Context` 来验证"换模型打开旧库必须失败"；由 Activity 注入。 */
object SelfTestContextHolder {
    var appContext: android.content.Context? = null
}

object SelfTest {

    /** 本机文档 E2E 用的样本文件名（放在 App 内部 filesDir 下）。 */
    const val SAMPLE_FILE = "sekb-sample.md"

    /** PDF 导入 E2E 用的样本（同样放在内部 filesDir）。 */
    const val PDF_SAMPLE_FILE = "sekb-sample.pdf"

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

        // 3.5 R10（M4）：DEVICE_ONLY + 非本机端点 → 一个请求都不许发。
        // 模拟器里 edgeBaseUrl=10.0.2.2（开发机）正是"非本机"，所以这条自检应当看到**拒绝**；
        // 若哪天它变成"照发"，就是隐私硬边界被破坏——这比"功能不可用"严重得多。
        val blockedEdge = object : com.sekb.shared.edge.EdgeLlm {
            var calls = 0
            override fun streamChat(
                model: String, messages: List<ChatMessage>, maxTokens: Int,
                jsonMode: Boolean, onToken: (String) -> Unit,
            ): com.sekb.shared.edge.EdgeCompletion {
                calls++
                return com.sekb.shared.edge.EdgeCompletion("不该出现", 0.0, 0.0, true, "")
            }
            override fun complete(
                model: String, messages: List<ChatMessage>, maxTokens: Int, jsonMode: Boolean,
            ): com.sekb.shared.edge.EdgeCompletion {
                calls++
                return com.sekb.shared.edge.EdgeCompletion("不该出现", 0.0, 0.0, true, "")
            }
        }
        val blockedOrch = ChatOrchestrator(
            config = cfg, router = router, edgeLlm = blockedEdge, cloud = edgeOnlyCloud,
            tools = container.tools,
        )
        val blockedOut = runCatching {
            blockedOrch.send("我的联系人里有谁", deviceData = true)
        }.getOrNull()
        val edgeIsLocal = PlaneRouter.isLocalEndpoint(cfg.edgeBaseUrl)
        val refuseReason = "escalation_blocked:${PlaneRouter.REASON_DEVICE_ONLY_NOT_LOCAL}"
        val gateOk = blockedOut != null && blockedEdge.calls == 0 &&
            if (edgeIsLocal) {
                // 本机端点：允许执行（但仍不许升级）
                blockedOut.escalateReason != refuseReason
            } else {
                // 非本机端点（模拟器就是这种）：必须拒绝，且不能吐出任何内容
                blockedOut.escalateReason == refuseReason && blockedOut.text.isEmpty()
            }
        record(
            "privacy_device_only_local_gate", gateOk,
            "edgeBaseUrl=${cfg.edgeBaseUrl} 本机=$edgeIsLocal 端侧调用=${blockedEdge.calls} " +
                "原因=${blockedOut?.escalateReason} 输出长度=${blockedOut?.text?.length}",
        )

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

        // ---------- 端侧 RAG（M3 第一批） ----------
        val ragDocs = listOf(
            "doc-diet" to "端侧推理省电的原因：计算留在本机，没有网络传输，也没有云端排队等待。",
            "doc-weather" to "北京今天多云转晴，最高气温 26 度，适合骑行。",
            "doc-rag" to "端侧 RAG 把知识索引放在设备上，检索不出网，因此隐私更好、延迟更低。",
        )
        // 持久化检查：自检每次都是**新进程**（跑前会 force-stop），所以启动时索引非空
        // 就证明上一轮的索引真的落盘存活了（SQLite 实现的意义就在这里）。
        val preexisting = container.vectorStore.size()
        val ingestReports = ragDocs.map { (id, text) -> container.knowledgeIndex.ingest(id, text) }
        val providerKind = if (container.onnxModelAvailable) {
            "ONNX " + container.embeddingProvider.space.id
        } else {
            "确定性桩（未找到 ONNX 模型）"
        }
        record("rag_provider", true, "嵌入=$providerKind 空间=${container.embeddingProvider.space.id} " +
            "本机计算=${container.embeddingProvider.isOnDevice}")
        record("rag_model_path", container.onnxModelAvailable,
            container.modelDirDiagnostics.take(150))
        if (container.reembeddedChunks > 0) {
            record("rag_reembed", true,
                "空间从 ${container.previousIndexSpace} 变为 ${container.embeddingProvider.space.id}，" +
                    "原地重算 ${container.reembeddedChunks} 块（不丢文本，RFC §4.5-F 的\"重算\"）")
        }
        record("rag_ingest", ingestReports.all { it.chunks >= 1 },
            ingestReports.joinToString(" ") { "${it.sourceId}:${it.chunks}块" } +
                " 空间=${container.embeddingProvider.space.id}" +
                " 启动时已有=$preexisting（SQLite 持久化）")

        // 空间戳入库并在打开时校验：换嵌入模型打开旧库必须当场失败（RFC §18.1）
        val appContext = SelfTestContextHolder.appContext
        val spaceMismatchOnOpen = if (appContext == null) null else runCatching {
            com.sekb.ondevice.rag.SqliteVectorStore(
                appContext, com.sekb.shared.embed.EmbeddingSpace("other-model@256", 256),
            )
        }.exceptionOrNull()
        record("rag_sqlite_space_guard", spaceMismatchOnOpen != null,
            (spaceMismatchOnOpen?.message ?: "未触发（不应发生）").take(90))

        // 嵌入合理性：两个语义无关的句子必须给出不同的向量。
        // 若所有向量都一样，检索会"任何问题都命中同一篇且余弦=1.000"——表象很像 bug，
        // 但根因在嵌入这一层（实测踩到过：输入张量/掩码构造错了）。
        val vA = container.embeddingProvider.embedOne("端侧推理为什么省电")
        val vB = container.embeddingProvider.embedOne("北京今天多云转晴，适合骑行")
        val cosAB = com.sekb.shared.rag.VectorMath.cosine(vA, vB)
        val dbg = container.embeddingProvider as? com.sekb.ondevice.embed.OnnxBgeEmbedding
        if (dbg != null) {
            val idsA = dbg.debugTokenIds("端侧推理为什么省电")
            val idsB = dbg.debugTokenIds("北京今天多云转晴，适合骑行")
            record("rag_token_debug", idsA.size != idsB.size || idsA != idsB,
                "A=${idsA.take(8)}(${idsA.size}) B=${idsB.take(8)}(${idsB.size})")
        }
        record("rag_embed_sanity", cosAB < 0.95,
            "两段无关文本余弦=${"%.3f".format(cosAB)}（应明显小于 1；=1.000 说明所有向量相同）" +
                " 维度=${vA.size} 首维=${vA.take(2).joinToString { "%.4f".format(it) }}")

        val hit = container.retriever.retrieve("端侧 RAG 为什么隐私更好", topK = 1)
        record("rag_retrieve", hit.hits.firstOrNull()?.sourceId == "doc-rag",
            "命中=${hit.hits.firstOrNull()?.sourceId} 分=${hit.hits.firstOrNull()?.let { "%.3f".format(it.score) }} " +
                "嵌入=${hit.embedMillis.toInt()}ms 检索=${hit.searchMillis.toInt()}ms")

        // 空间一致性：桩的空间与云端不同 → 必须明确标为"不可与云端融合"
        // 与云端能否融合，判据是**空间 id 是否等于云端空间**，不是"用的是不是 ONNX"：
        //   fp32 bge 与云端同空间 → true；
        //   **int8 量化模型空间不同 → false**（量化会把分数分布抬上去，混用就是混两套向量）；
        //   确定性桩 → false。
        val expectedCloudCompatible =
            container.embeddingProvider.space.id == com.sekb.shared.embed.EmbeddingSpace.SERVER_SPACE_ID
        record("rag_space_guard", hit.cloudCompatible == expectedCloudCompatible,
            "索引空间=${hit.space.id} 与云端一致=${hit.cloudCompatible}（期望 $expectedCloudCompatible：" +
                "只有与云端同空间的模型才允许融合；int8 与桩都不行）")

        // 空间不一致必须**拒绝检索**而不是混算余弦
        val mismatched = com.sekb.shared.rag.Retriever(
            com.sekb.shared.embed.DeterministicEmbedding(
                space = com.sekb.shared.embed.EmbeddingSpace("other-model@256", 256),
            ),
            container.vectorStore,
        )
        val mismatchOutcome = mismatched.retrieve("端侧 RAG")
        record("rag_space_refuses", !mismatchOutcome.searchable && mismatchOutcome.hits.isEmpty(),
            mismatchOutcome.reason.take(80))

        // 设备专属集合 + 非本机嵌入 → 必须拒绝（隐私闸门）。
        // ⚠️ 必须用**同一空间**但 isOnDevice=false 的提供者，否则先触发的是"空间不一致"
        // 那条规则，就测不到隐私闸门本身（第一版就是这么写错的）。
        val sameSpaceRemote = object : com.sekb.shared.embed.EmbeddingProvider {
            override val space = container.embeddingProvider.space
            override val isOnDevice = false
            override fun embed(texts: List<String>) = container.embeddingProvider.embed(texts)
        }
        val remoteEmbed = com.sekb.shared.rag.Retriever(sameSpaceRemote, container.vectorStore)
        val ragDenied = remoteEmbed.retrieve("端侧 RAG", deviceOnly = true)
        record("rag_device_only_guard", !ragDenied.searchable,
            ragDenied.reason.take(80))

        // 工具化：模型可调用的 kb_search 走同一套闸门
        val toolRes = container.tools.execute(
            com.sekb.shared.model.ToolCall("kb_search", mapOf("query" to "端侧 RAG 隐私")),
        )
        record("rag_tool_search", toolRes.ok && toolRes.output.contains("doc-rag"),
            "ok=${toolRes.ok} 输出=${toolRes.output.take(50)}")

        // ---------- 本机文档：导入 → 检索 → 删除（M3 第二批） ----------
        // 样本文件由 scripts/android.sh push-sample 写进 App 内部目录（避免存储权限问题）；
        // 没有文件时如实 SKIP，而不是硬失败。
        val appCtx = SelfTestContextHolder.appContext
        val sample = appCtx?.let { java.io.File(it.filesDir, SAMPLE_FILE) }
        if (sample == null || !sample.isFile) {
            record("rag_import_file", null,
                "未找到样本文件（先跑：bash scripts/android.sh push-sample）")
        } else {
            val name = sample.name
            val before = container.knowledgeIndex.documents().size
            val text = sample.readText()
            val report = container.knowledgeIndex.ingest(
                sourceId = name, text = text, name = name,
                sizeBytes = sample.length(), deviceOnly = true,
            )
            val docs = container.knowledgeIndex.documents()
            record("rag_import_file", report.chunks >= 1 && docs.any { it.id == name },
                "「$name」${report.chunks} 段/${sample.length()}B 文档数 $before→${docs.size}" +
                    " 空间=${report.space}")

            // 导入的内容必须真能被检索到（否则"导入成功"只是自我安慰）
            val hit = container.retriever.retrieve("删除文档会不会影响其他文档？", topK = 1)
            record("rag_import_retrieve", hit.hits.firstOrNull()?.sourceId == name,
                "命中=${hit.hits.firstOrNull()?.sourceId} 分=${hit.hits.firstOrNull()?.let { "%.3f".format(it.score) }}")

            // 删除该文档：只删它的切片，其他文档不受影响（回归那个"清空整库"的 bug）
            val others = docs.count { it.id != name }
            val removed = container.knowledgeIndex.remove(name)
            val after = container.knowledgeIndex.documents()
            record("rag_delete_file", removed >= 1 && after.none { it.id == name } &&
                after.size == others && container.vectorStore.size() >= 1,
                "删除 $removed 段，文档数 ${docs.size}→${after.size}（应保留 $others），" +
                    "剩余切片=${container.vectorStore.size()}")
        }

        // ---------- PDF 导入（M3 第三批：真实 PdfBox 抽取） ----------
        val pdfSample = appCtx?.let { java.io.File(it.filesDir, PDF_SAMPLE_FILE) }
        if (pdfSample == null || !pdfSample.isFile) {
            record("rag_import_pdf", null, "未找到 PDF 样本（先跑：bash scripts/android.sh push-sample）")
        } else {
            val bytes = pdfSample.readBytes()
            val extraction = com.sekb.ondevice.ui.PdfBoxExtractor.extract(bytes)
            record("rag_pdf_extract", extraction is com.sekb.ondevice.ui.PdfExtraction.Text,
                when (extraction) {
                    is com.sekb.ondevice.ui.PdfExtraction.Text ->
                        "抽取 ${extraction.text.length} 字符 / ${extraction.pages} 页；" +
                            "开头=${extraction.text.take(60).replace("\n", " ")}"
                    is com.sekb.ondevice.ui.PdfExtraction.NoTextLayer -> "无文本层（${extraction.pages} 页）"
                    is com.sekb.ondevice.ui.PdfExtraction.Encrypted -> "已加密：${extraction.detail}"
                    is com.sekb.ondevice.ui.PdfExtraction.Failed -> "解析失败：${extraction.detail}"
                })

            if (extraction is com.sekb.ondevice.ui.PdfExtraction.Text) {
                val name = pdfSample.name
                val report = container.knowledgeIndex.ingest(
                    sourceId = name, text = extraction.text, name = name,
                    sizeBytes = pdfSample.length(), deviceOnly = true,
                )
                val hit = container.retriever.retrieve("does deleting a document affect other documents?", 1)
                record("rag_import_pdf", report.chunks >= 1 && hit.hits.firstOrNull()?.sourceId == name,
                    "「$name」${report.chunks} 段；检索命中=${hit.hits.firstOrNull()?.sourceId} " +
                        "分=${hit.hits.firstOrNull()?.let { "%.3f".format(it.score) }}")
                val removed = container.knowledgeIndex.remove(name)
                record("rag_pdf_delete", removed >= 1, "删除 $removed 段，剩余切片=${container.vectorStore.size()}")
            } else {
                record("rag_import_pdf", false, "抽取未得到文本，后续步骤跳过")
            }
        }

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
                com.sekb.shared.model.RouteEventPayload(
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
        com.sekb.shared.tools.ToolCallJson.parse(text) is com.sekb.shared.tools.ToolCallJson.Parsed.Ok
}
