package com.sekb.ondevice.model

/** 推理平面。与服务端 `plane_router.PLANE_EDGE/PLANE_CLOUD` 同名，便于日志对齐。 */
enum class Plane(val wire: String) {
    EDGE("edge"),
    CLOUD("cloud");

    companion object {
        fun fromWire(value: String): Plane = if (value == "cloud") CLOUD else EDGE
    }
}

/**
 * 一次请求的平面决策。
 *
 * 字段与服务端 `plane_router.Decision` 一一对应：两条链路（SEKB 内部路由、端侧宿主自建推理）
 * 必须能对同一次请求给出**同样口径**的解释，否则"端侧完成率/升级率"两边不可比。
 */
data class RouteDecision(
    val plane: Plane,
    val reason: String,
    val tier: String = "default",
    val inputTokens: Int = 0,
    val outputBudget: Int = 0,
    /** 数据分级为 DEVICE_ONLY：**永不出端**，即使端侧答得不好也不许升级（RFC §5.2 硬边界） */
    val deviceOnly: Boolean = false,
    /**
     * 不允许执行的原因（当前只有一种：DEVICE_ONLY 数据遇上了**非本机**的端侧端点）。
     *
     * **为什么需要这个字段**（R10，2026-09-21 才发现）：`DEVICE_ONLY` 的隐含前提是
     * "端侧 = 本设备"，但端侧平面是按 `edgeBaseUrl` 寻址的——它可能是**局域网里的另一台机器**
     * （Android 模拟器里就是 `10.0.2.2` = 开发机）。这种情况下的正确行为不是"照发"，而是
     * **拒绝执行并说清原因**：数据分级说它不能出设备，而目标不是本设备。
     *
     * 非 null 时调用方**必须**停止，不得调用任何端点（既不端侧也不云端）。
     */
    val blockedReason: String? = null,
) {
    val isEdge: Boolean get() = plane == Plane.EDGE
    fun escalationAllowed(): Boolean = isEdge && !deviceOnly

    /** 是否可以真的执行（被拦下的决策只能失败，不能发请求）。 */
    fun isBlocked(): Boolean = blockedReason != null
}

/** 本次回答的执行位置（服务端 `execution` 字段的客户端视图）。 */
data class ExecutionInfo(
    val primaryPlane: String = "",
    val primaryRole: String = "",
    val model: String = "",
    val reason: String = "",
    val tier: String = "",
    val escalated: Int = 0,
    val byPlane: Map<String, Int> = emptyMap(),
    val edgeDecided: Int = 0,
    val edgeCompleted: Int = 0,
    val latencyMs: Double = 0.0,
) {
    /** 给 UI 用的一句话：这次到底在哪算的。 */
    fun badge(): String = when (primaryPlane) {
        "edge" -> "本机完成"
        "cloud" -> if (escalated > 0) "已上云（端侧不达标）" else "云端完成"
        else -> "未知"
    }
}

/** SSE 事件（服务端 `chat_stream` 的三种 type）。 */
sealed interface ChatEvent {
    data class Thinking(val content: String) : ChatEvent
    data class Token(val content: String) : ChatEvent
    data class Done(val meta: DoneMeta) : ChatEvent
    data class Error(val detail: String) : ChatEvent
}

data class DoneMeta(
    val conversationId: String = "",
    val intent: String = "",
    val execution: ExecutionInfo? = null,
    val raw: Map<String, String> = emptyMap(),
)

/**
 * 设备凭证（`POST /api/v1/device/enroll` 的产物）。
 *
 * 签发时间要**显式存**：轮换判断按"剩余不足总有效期 1/3"来算，若用
 * `过期时间 - 默认TTL` 反推，服务端一旦改了 TTL（比如压到 7 天），端侧算出的总时长就是错的。
 */
data class DeviceCredentials(
    val deviceId: String,
    val deviceToken: String,
    val issuedAtMillis: Long,
    val expiresAtMillis: Long,
) {
    /** 剩余有效期不足 1/3 时就该轮换（RFC §4.5-H：设备不该等过期才换证）。 */
    fun needsRotation(nowMillis: Long): Boolean {
        val total = expiresAtMillis - issuedAtMillis
        val left = expiresAtMillis - nowMillis
        return total > 0 && left < total / 3
    }

    fun isExpired(nowMillis: Long): Boolean = nowMillis >= expiresAtMillis

    companion object {
        const val DEFAULT_TTL_HOURS: Long = 720

        /** 按服务端返回的 `expires_in_hours` 构造（**不要**猜 TTL）。 */
        fun fromTtl(deviceId: String, token: String, nowMillis: Long, ttlHours: Long): DeviceCredentials {
            val ttl = if (ttlHours > 0) ttlHours else DEFAULT_TTL_HOURS
            return DeviceCredentials(deviceId, token, nowMillis, nowMillis + ttl * 3600 * 1000)
        }
    }
}

/** 一次工具调用（模型输出的结构化意图）。 */
data class ToolCall(val tool: String, val args: Map<String, String> = emptyMap())

/** 工具执行结果。`denied=true` 表示**被权限闸门拦下**（不是执行失败）。 */
data class ToolResult(
    val ok: Boolean,
    val output: String = "",
    val denied: Boolean = false,
    val reason: String = "",
)

/** 一次推理的路由事件（上报给 SEKB，与服务端 `RouteEventIn` 对齐）。 */
data class RouteEventPayload(
    val eventId: String,
    val role: String,
    val plane: String,
    val reason: String,
    val model: String = "",
    val tier: String = "default",
    val inputTokens: Int = 0,
    val outputTokens: Int = 0,
    val latencyMs: Double = 0.0,
    val escalated: Boolean = false,
    val escalateReason: String = "",
    val signals: List<String> = emptyList(),
    val versions: Map<String, String> = emptyMap(),
)
