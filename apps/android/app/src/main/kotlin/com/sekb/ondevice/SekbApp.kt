package com.sekb.ondevice

import android.app.Application
import android.content.Context
import com.sekb.ondevice.chat.ChatOrchestrator
import com.sekb.ondevice.chat.CloudChat
import com.sekb.ondevice.chat.CloudReply
import com.sekb.ondevice.chat.ToolCallEval
import com.sekb.ondevice.device.KeystoreCredentialStore
import com.sekb.ondevice.edge.OpenAiCompatibleEdgeLlm
import com.sekb.ondevice.net.OkHttpTransport
import com.sekb.ondevice.net.SekbApi
import com.sekb.ondevice.route.EdgeRuntimeConfig
import com.sekb.ondevice.route.PlaneRouter
import com.sekb.ondevice.tools.AndroidDeviceTools
import com.sekb.ondevice.tools.AndroidPermissionChecker
import com.sekb.ondevice.tools.PermissionAudit
import com.sekb.ondevice.tools.ToolRegistry

/**
 * 进程级装配（没有引 DI 框架——这个体量用不上，显式构造比注解更好读）。
 *
 * 注意这里的**依赖方向**：UI → 容器 → { 编排器 → 路由 / 端侧 LLM / 云端 / 工具 }。
 * 编排器与路由都不认识 Android，所以它们能在纯 JVM 单测里被验（这是本项目刻意的分层）。
 */
class AppContainer(context: Context) {

    val credentialStore = KeystoreCredentialStore(context)

    val transport = OkHttpTransport()

    /** 可在设置页改动（模拟器默认 `10.0.2.2` 指向宿主机）。 */
    var config: EdgeRuntimeConfig = EdgeRuntimeConfig(
        edgeBaseUrl = BuildConfig.DEFAULT_EDGE_BASE_URL,
        sekbBaseUrl = BuildConfig.DEFAULT_SEKB_BASE_URL,
        appVersion = BuildConfig.VERSION_NAME,
    )
        private set

    val audit = PermissionAudit()

    /** 端侧工具（含权限闸门所需的真实权限检查）。 */
    val tools = ToolRegistry(
        tools = AndroidDeviceTools.all(context),
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
        onRouteEvent: (com.sekb.ondevice.model.RouteEventPayload) -> Unit,
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
    }
}
