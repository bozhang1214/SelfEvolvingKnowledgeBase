package com.sekb.ondevice

import android.app.Application
import android.content.Context
import com.sekb.shared.chat.ChatOrchestrator
import com.sekb.shared.chat.CloudChat
import com.sekb.shared.chat.CloudReply
import com.sekb.shared.chat.ToolCallEval
import com.sekb.ondevice.device.KeystoreCredentialStore
import com.sekb.ondevice.device.SharedPrefsPolicyStore
import com.sekb.shared.policy.PolicyAuditEntry
import com.sekb.shared.policy.PolicyFetcher
import com.sekb.shared.policy.PolicyRefreshResult
import com.sekb.shared.edge.OpenAiCompatibleEdgeLlm
import com.sekb.shared.embed.DeterministicEmbedding
import com.sekb.shared.embed.EmbeddingProvider
import com.sekb.shared.embed.EmbeddingSpace
import com.sekb.ondevice.embed.OnnxBgeEmbedding
import com.sekb.shared.rag.KnowledgeIndex
import com.sekb.ondevice.rag.SqliteVectorStore
import com.sekb.shared.rag.Retriever
import com.sekb.shared.rag.VectorStore
import com.sekb.shared.tools.KbSearchTool
import com.sekb.ondevice.net.OkHttpTransport
import com.sekb.shared.net.SekbApi
import com.sekb.shared.route.EdgeRuntimeConfig
import com.sekb.shared.route.PlaneRouter
import com.sekb.ondevice.tools.AndroidDeviceTools
import com.sekb.ondevice.tools.AndroidPermissionChecker
import com.sekb.shared.tools.PermissionAudit
import com.sekb.shared.tools.ToolRegistry

/**
 * 进程级装配（没有引 DI 框架——这个体量用不上，显式构造比注解更好读）。
 *
 * 注意这里的**依赖方向**：UI → 容器 → { 编排器 → 路由 / 端侧 LLM / 云端 / 工具 }。
 * 编排器与路由都不认识 Android，所以它们能在纯 JVM 单测里被验（这是本项目刻意的分层）。
 */
class AppContainer(context: Context) {

    init {
        // PdfBox-Android 必须初始化一次资源加载器：它的字形表（glyphlist）等资源打包在
        // aar 的 assets 里，不 init 就找不到，抽取时抛 `ExceptionInInitializerError`
        // （注意是 **Error 不是 Exception**，普通 try/catch 拦不住）。
        runCatching { com.tom_roush.pdfbox.android.PDFBoxResourceLoader.init(context) }
            .onFailure { android.util.Log.w("SEKB", "PdfBox 资源初始化失败：${it.message}") }
    }

    val credentialStore = KeystoreCredentialStore(context)

    val transport = OkHttpTransport()

    /** 可在设置页改动（模拟器默认 `10.0.2.2` 指向宿主机）。 */
    var config: EdgeRuntimeConfig = EdgeRuntimeConfig(
        edgeBaseUrl = BuildConfig.DEFAULT_EDGE_BASE_URL,
        sekbBaseUrl = BuildConfig.DEFAULT_SEKB_BASE_URL,
        appVersion = BuildConfig.VERSION_NAME,
    )

    val audit = PermissionAudit()

    // ── 端侧策略（L1 热修）────────────────────────────────────────────────
    // 双槽存储 + 拉取器；签名密钥来自构建配置（生产由部署侧注入；未配置时服务端会用派生密钥，
    // 端侧这里保持同一来源即可——它只用于**验签**，不参与任何加密）。
    val policyStore = SharedPrefsPolicyStore(context)

    /** 最近一次策略刷新的结果与审计（自检/界面展示用；不落盘正文）。 */
    var lastPolicyResult: PolicyRefreshResult? = null
        private set
    val policyAudit = mutableListOf<PolicyAuditEntry>()

    val policyFetcher = PolicyFetcher(
        transport = transport,
        sekbBaseUrl = config.sekbBaseUrl,
        signingKey = BuildConfig.DEFAULT_EDGE_POLICY_KEY,
        store = policyStore,
        audit = { entry ->
            synchronized(policyAudit) {
                policyAudit += entry
                while (policyAudit.size > 50) policyAudit.removeAt(0)
            }
        },
    )

    /**
     * 端侧嵌入实现（**可插拔**）。
     *
     * 当前用确定性桩：它**不是**真实语义模型，只是让"切片→嵌入→检索→工具调用→审计"
     * 这条链路在没有模型的情况下也能跑通并被验证（空间戳为 `stub-hash@256`，
     * 因此 **cloudCompatible=false**：绝不允许与云端向量融合，见 RFC §18.1）。
     *
     * 下一步（真机/ONNX）：把这里换成 ONNX 版 `bge-small-zh-v1.5`（512 维），
     * 空间戳变成 `BAAI/bge-small-zh-v1.5@512` → 与云端一致，才允许跨端复用。
     * 换实现必须**重建索引**（[VectorStore.space] 是构造期确定的）。
     */
    /** int8 就位则优先用它（体积 1/4；排序与 fp32 一致，只是分数分布上移 → 阈值 0.5）。 */
    private val int8Dir = OnnxBgeEmbedding.int8Dir(context)
    private val int8Available = OnnxBgeEmbedding.isAvailable(int8Dir)
    private val modelDir = if (int8Available) int8Dir else OnnxBgeEmbedding.resolveDir(context)

    /** 模型路径诊断（自检里打印；"模型没被认到"最常见的原因就是路径/属主问题）。 */
    val modelDirDiagnostics: String = OnnxBgeEmbedding.diagnostics(context)

    /** ONNX 模型是否就位（就位则用真实嵌入，否则退回桩）。 */
    val onnxModelAvailable: Boolean = OnnxBgeEmbedding.isAvailable(modelDir)

    val embeddingProvider: EmbeddingProvider = if (onnxModelAvailable) {
        OnnxBgeEmbedding(
            modelDir,
            spaceId = if (int8Available) OnnxBgeEmbedding.INT8_SPACE_ID else EmbeddingSpace.SERVER_SPACE_ID,
        )
    } else {
        DeterministicEmbedding()
    }

    /**
     * 端侧知识索引（SQLite 持久化：索引要能跨 App 重启存活）。
     *
     * 打开时若发现"库里记的空间 ≠ 当前嵌入模型"（例如刚从桩切到 ONNX），
     * 走**原地重嵌入**而不是删库——索引里的文本是设备侧唯一副本（RFC §4.5-F 的"重算"）。
     */
    private val openResult = SqliteVectorStore.openOrReembed(context, embeddingProvider)
    val vectorStore: VectorStore = openResult.store
    val reembeddedChunks: Int = openResult.reembedded
    val previousIndexSpace: String? = openResult.previousSpace
    // SqliteVectorStore 同时实现 VectorStore 与 DocumentRegistry（同一张库）
    val knowledgeIndex = KnowledgeIndex(
        embeddingProvider, vectorStore, registry = openResult.store,
    )
    /** 检索阈值：优先用已落盘策略里的值（`retrievalMinScore`），否则用 Retriever 的默认 0.5。 */
    private fun policyMinScore(): Double = policyFetcher.activeRetrievalMinScore() ?: 0.5

    val retriever = Retriever(embeddingProvider, vectorStore, minScore = policyMinScore())

    /** 端侧工具（含权限闸门所需的真实权限检查）。 */
    val tools = ToolRegistry(
        // kb_search：本机知识检索（deviceOnly=true —— 本机索引里的内容按设备专属处理）
        tools = AndroidDeviceTools.all(context) + KbSearchTool.create(retriever, deviceOnly = true),
        checker = AndroidPermissionChecker(context),
        audit = audit,
    )

    val toolEval = ToolCallEval()

    fun updateConfig(edgeBaseUrl: String? = null, sekbBaseUrl: String? = null) {
        config = config.copy(
            edgeBaseUrl = edgeBaseUrl?.takeIf { it.isNotBlank() } ?: config.edgeBaseUrl,
            sekbBaseUrl = sekbBaseUrl?.takeIf { it.isNotBlank() } ?: config.sekbBaseUrl,
        )
    }

    /**
     * 拉取并应用端侧策略（L1 热修）。
     *
     * **不阻塞启动**：调用方在后台线程调它；失败只是保持现状（策略是锦上添花）。
     * 成功时把新配置写回 [config]，后续新建的编排器即按新参数跑。
     */
    fun refreshPolicy(): PolicyRefreshResult {
        val creds = credentialStore.load() ?: return PolicyRefreshResult.Skipped("policy_no_device_token")
        val result = policyFetcher.refresh(
            deviceToken = creds.deviceToken,
            current = config,
            deviceId = creds.deviceId,
        )
        if (result is PolicyRefreshResult.Applied) {
            config = config.copy(
                guardChars = result.config.guardChars,
                escalateOn = result.config.escalateOn,
                maxInputTokens = result.config.maxInputTokens,
                maxOutputTokens = result.config.maxOutputTokens,
                maxTtftMs = result.config.maxTtftMs,
                preferEdge = result.config.preferEdge,
                models = result.config.models,
                availableTools = result.config.availableTools,
            )
        }
        lastPolicyResult = result
        return result
    }

    /** 回滚到上一份策略（新策略把行为改坏时的止损口）。 */
    fun rollbackPolicy(): PolicyRefreshResult {
        val result = policyFetcher.rollback(config)
        if (result is PolicyRefreshResult.Applied) {
            config = result.config
        }
        lastPolicyResult = result
        return result
    }

    /** 当前生效策略的版本号（0 = 还没拉到过）。 */
    fun policyVersion(): Int = policyFetcher.activeVersion()

    /**
     * 启动策略刷新循环：**立即拉一次，之后每 [intervalHours] 小时一次**（默认 6h）。
     *
     * 为什么放后台线程且不 await：策略拉取要走网络，绝不能拖慢启动或让"没网就不能聊天"。
     * 拉失败只是保持现状（[PolicyFetcher] 里已保证失败不改状态），下一轮再试。
     */
    fun startPolicyRefreshLoop(intervalHours: Long = 6) {
        val worker = Thread {
            while (true) {
                runCatching { refreshPolicy() }
                    .onFailure { android.util.Log.w("SEKB", "策略刷新异常：${it.message}") }
                try {
                    Thread.sleep(intervalHours * 3600_000L)
                } catch (e: InterruptedException) {
                    return@Thread
                }
            }
        }
        worker.isDaemon = true
        worker.name = "sekb-policy-refresh"
        worker.start()
    }

    fun api(): SekbApi = SekbApi(transport, config.sekbBaseUrl)

    /** 端侧是否就绪（端点可达且模型已加载）。**不参与路由决策**，只用于 UI 提示。 */
    fun edgeReachable(): Boolean =
        OpenAiCompatibleEdgeLlm(transport, config.edgeBaseUrl, config = config).isReachable()

    /**
     * 造一个编排器：端侧 = 本机/宿主 Ollama；云端 = SEKB SSE。
     *
     * 每轮新建（无状态、线程安全），省得为"配置改了要重建"再写一套失效逻辑。
     */
    fun orchestrator(
        deviceToken: String,
        onRouteEvent: (com.sekb.shared.model.RouteEventPayload) -> Unit,
    ): ChatOrchestrator {
        val cfg = config
        val api = api()
        val edge = OpenAiCompatibleEdgeLlm(transport, cfg.edgeBaseUrl, config = cfg)
        val cloud = CloudChat { message, conversationId, onToken, onThinking ->
            // 服务端回传的执行位置是**权威口径**（它知道自己内部各角色落在哪）
            val result = api.chatStream(
                deviceToken = deviceToken, message = message, conversationId = conversationId,
                onToken = onToken, onThinking = onThinking,
            )
            CloudReply(result.execution, result.conversationId)
        }
        return ChatOrchestrator(
            config = cfg,
            router = PlaneRouter(cfg),
            edgeLlm = edge,
            cloud = cloud,
            tools = tools,
            reporter = onRouteEvent,
        )
    }
}

class SekbApp : Application() {

    lateinit var container: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        container = AppContainer(this)
        // L1 热修：启动后**异步**拉一次策略并每 6h 刷新（不阻塞启动；失败保持现状）
        container.startPolicyRefreshLoop()
    }
}
