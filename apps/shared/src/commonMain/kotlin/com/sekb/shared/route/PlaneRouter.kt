package com.sekb.shared.route

import com.sekb.shared.model.Plane
import com.sekb.shared.model.RouteDecision
import com.sekb.shared.core.JsonObject

/**
 * 端侧平面路由（服务端 `plane_router.PlaneRouter` 的客户端镜像）。
 *
 * **为什么要在客户端再实现一份**：端侧宿主自己就能跑推理（连本机 Ollama 或内嵌 llama.cpp），
 * 这部分请求**根本不经过服务端**——如果决策只在服务端做，那么"端侧完成率"这个指标就只统计了
 * 服务端看到的那一半。所以客户端必须能独立决策，并把决策**如实上报**（见协议 §4）。
 *
 * **口径必须与服务端一致**（否则两边数字不可比）：token 估算、档位→模型映射、预算阈值、
 * 6 类升级信号、退化判定的最小长度，都在这里逐条对齐，并在注释里写明对应关系。
 */
class PlaneRouter(val config: EdgeRuntimeConfig) {

    /** 决策：先看数据分级（硬边界），再看输入/输出预算，最后看偏好。 */
    fun decide(
        role: String,
        messages: List<String>,
        expectedOutputTokens: Int? = null,
        deviceData: Boolean = false,
    ): RouteDecision {
        val tier = tierFor(role, expectedOutputTokens)
        val inputTokens = estimateMessagesTokens(messages)
        val outputBudget = expectedOutputTokens ?: DEFAULT_EXPECTED_OUTPUT[role] ?: 200

        // 数据分级优先于一切：DEVICE_ONLY 数据永不出端（§5.2）
        if (deviceData || role in config.deviceOnlyRoles) {
            // R10（2026-09-21）：还得确认"端侧"真的是**本设备**。端侧平面是按 endpoint 寻址的，
            // 它可能是局域网里的另一台机器（模拟器里就是 10.0.2.2 = 开发机）。
            // 目标不是本机 → **拒绝执行**：不降级、不改道云、也不假装成功。
            if (!isLocalEndpoint(config.edgeBaseUrl)) {
                return RouteDecision(
                    Plane.EDGE, REASON_DEVICE_ONLY_NOT_LOCAL, tier, inputTokens, outputBudget,
                    deviceOnly = true, blockedReason = REASON_DEVICE_ONLY_NOT_LOCAL,
                )
            }
            return RouteDecision(Plane.EDGE, REASON_DEVICE_ONLY, tier, inputTokens,
                outputBudget, deviceOnly = true)
        }
        if (inputTokens > config.maxInputTokens) {
            return RouteDecision(Plane.CLOUD,
                "input_over_edge_budget($inputTokens>${config.maxInputTokens})",
                tier, inputTokens, outputBudget)
        }
        if (outputBudget > config.maxOutputTokens) {
            return RouteDecision(Plane.CLOUD,
                "output_over_edge_budget($outputBudget>${config.maxOutputTokens})",
                tier, inputTokens, outputBudget)
        }
        if (!config.preferEdge) {
            return RouteDecision(Plane.CLOUD, REASON_PREFER_CLOUD, tier, inputTokens, outputBudget)
        }
        return RouteDecision(Plane.EDGE, REASON_EDGE_PREFERRED, tier, inputTokens, outputBudget)
    }

    /** 档位选择：角色名 → 短任务；预期输出规模 → 质量档。与服务端 `derive_tier` 同序。 */
    fun tierFor(role: String, expectedOutputTokens: Int? = null): String {
        if (role in SHORT_ROLES) return "short"
        val budget = expectedOutputTokens ?: DEFAULT_EXPECTED_OUTPUT[role] ?: 0
        return when {
            budget >= 1500 -> "quality"
            budget > config.maxOutputTokens -> "quality"
            else -> "default"
        }
    }

    fun modelFor(tier: String): String =
        config.models[tier] ?: config.models["default"] ?: ""

    /**
     * 事后评估：命中任何一条即应升级。
     *
     * @param partial 流式**前缀**阶段。此时必须只看"在半截输出上也有意义"的信号——
     *    `json_invalid` 一定要排除：半截 JSON 必然不合法，拿它判会误杀所有 JSON 角色。
     */
    fun evaluate(
        text: String,
        responseFormat: String? = null,
        ttftMillis: Double = 0.0,
        availableTools: Set<String> = config.availableTools,
        partial: Boolean = false,
    ): List<String> {
        val signals = mutableListOf<String>()
        val trimmed = text.trim()

        if (trimmed.isEmpty()) signals.add(SIGNAL_EMPTY)
        if (looksDegenerate(trimmed)) signals.add(SIGNAL_DEGENERATE)
        if (looksLowConfidence(trimmed)) signals.add(SIGNAL_LOW_CONFIDENCE)
        if (referencesUnknownTool(text, availableTools)) signals.add(SIGNAL_TOOL_HALLUCINATION)

        if (responseFormat == "json" && !partial) {
            if (parseJsonObjectOrNull(trimmed) == null) signals.add(SIGNAL_JSON_INVALID)
        }
        if (config.maxTtftMs > 0 && ttftMillis > config.maxTtftMs) signals.add(SIGNAL_TIMEOUT)

        // 前缀阶段只保留"半截输出上也成立"的信号
        return if (partial) signals.filter { it in PARTIAL_SAFE_SIGNALS } else signals
    }

    fun shouldEscalate(signals: List<String>): Boolean =
        signals.any { it in config.escalateOn }

    /** 端侧可用的小模型档位（S4）：角色名 → 期望输出规模仅用于日志与调试。 */
    fun describe(decision: RouteDecision): String =
        "${decision.plane.wire}:${decision.reason}:${decision.tier}"

    companion object {
        /**
         * 这个端点是否指向**本机**（R10 的判据）。
         *
         * 为什么按"地址"而不是按"配置项名"判断：端侧端点是可配置的（模拟器 `10.0.2.2`、
         * 真机局域网 IP、桌面宿主 Tailscale IP 都合法），**只有回环地址才等价于"这台设备自己"**。
         * 判据故意保守：拿不准（域名、0.0.0.0、解析失败）一律当**非本机**——
         * 宁可拒绝执行，也不把 DEVICE_ONLY 数据发出去。
         */
        fun isLocalEndpoint(url: String): Boolean {
            val host = hostOf(url) ?: return false
            val h = host.removePrefix("[").removeSuffix("]").lowercase()
            return h == "127.0.0.1" || h == "localhost" || h == "::1" ||
                h == "0:0:0:0:0:0:0:1" || h == "localhost.localdomain"
        }

        /** 从 URL 里取 host（不引 java.net.URI：要为 KMP 铺路，纯字符串解析更省事）。 */
        fun hostOf(url: String): String? {
            val afterScheme = url.substringAfter("://", missingDelimiterValue = "")
            if (afterScheme.isEmpty()) return null
            val authority = afterScheme.substringBefore('/').substringBefore('?')
            if (authority.isEmpty() || authority.startsWith("@")) return null
            val hostPart = authority.substringAfter('@')          // 去掉 user:pass@
            return when {
                hostPart.startsWith("[") -> hostPart.substringBefore(']').removePrefix("[")  // IPv6
                else -> hostPart.substringBefore(':').ifEmpty { null }
            }
        }

        const val REASON_DEVICE_ONLY = "device_only_data"
        /**
         * DEVICE_ONLY 数据 + **非本机**端侧端点 → 拒绝执行（R10）。
         * 留痕用这个名字，别与 `escalation_blocked:device_only` 混：
         * 前者是"根本没发出去"，后者是"端侧答完想改道、被隐私边界拦下"。
         */
        const val REASON_DEVICE_ONLY_NOT_LOCAL = "device_only_requires_local_runtime"
        const val REASON_EDGE_PREFERRED = "edge_preferred"
        const val REASON_PREFER_CLOUD = "prefer_cloud"
        const val REASON_JSON_INVALID = "json_invalid"
        const val REASON_EMPTY = "empty"
        const val REASON_DEGENERATE = "degenerate"
        const val REASON_TIMEOUT = "timeout"
        const val REASON_LOW_CONFIDENCE = "low_confidence"
        const val REASON_TOOL_HALLUCINATION = "tool_hallucination"
        const val REASON_CONTEXT_OVERFLOW = "context_overflow"

        const val SIGNAL_JSON_INVALID = "json_invalid"
        const val SIGNAL_EMPTY = "empty"
        const val SIGNAL_DEGENERATE = "degenerate"
        const val SIGNAL_TIMEOUT = "timeout"
        const val SIGNAL_LOW_CONFIDENCE = "low_confidence"
        const val SIGNAL_TOOL_HALLUCINATION = "tool_hallucination"

        /** 流式前缀阶段可用的信号（不含 `json_invalid`，原因见 [evaluate]）。 */
        val PARTIAL_SAFE_SIGNALS = setOf(
            SIGNAL_EMPTY, SIGNAL_DEGENERATE, SIGNAL_TIMEOUT,
            SIGNAL_LOW_CONFIDENCE, SIGNAL_TOOL_HALLUCINATION,
        )

        /** 与服务端 `ABSTAIN_MARKERS` 同一份清单：两侧"低置信"的判定必须一致。 */
        val ABSTAIN_MARKERS = listOf(
            "无法确定", "无法回答", "无法判断", "不确定", "不清楚", "没有足够",
            "抱歉，我无法", "我不知道", "i'm not sure", "cannot determine",
            "not enough information", "insufficient information",
        )

        /** 与服务端 `DEFAULT_EXPECTED_OUTPUT` 对齐（端侧宿主自己的角色名在前）。 */
        val DEFAULT_EXPECTED_OUTPUT = mapOf(
            "chat" to 400,
            "chat_simple" to 64,
            "tool_call" to 96,
            "supervisor" to 32,
            "critic" to 64,
            "critic_complex" to 128,
            "rerank" to 16,
            "ragas" to 64,
            "scribe" to 400,
            "planner" to 600,
            "executor" to 800,
            "job_analysis" to 1500,
            "news_report" to 6000,
        )

        /** 这些角色只做短输出（分类/排序），一律走最小档。 */
        val SHORT_ROLES = setOf("supervisor", "rerank", "tool_call", "critic")

        /** 退化检测的最小长度：与服务端 `_DEGEN_MIN_LEN=40` 一致。 */
        const val DEGEN_MIN_LEN = 40

        private val CJK = Regex("[\\u4e00-\\u9fff\\u3400-\\u4dbf\\uf900-\\ufaff]")

        /**
         * token 粗估：CJK 1 token/字，其余 4 字符 1 token。与服务端 `estimate_tokens` 同口径。
         * 实测该口径对 923 token 的真实 prompt 误差 ±10%，用于"是否超预算"足够。
         */
        fun estimateTokens(text: String): Int {
            if (text.isEmpty()) return 0
            val cjk = CJK.findAll(text).count()
            val other = text.length - cjk
            return cjk + maxOf(other / 4, 0)
        }

        fun estimateMessagesTokens(messages: List<String>): Int =
            messages.sumOf { estimateTokens(it) }

        /**
         * 退化输出检测：循环重复行 / 单字符刷屏。与服务端 `looks_degenerate` 同步。
         */
        fun looksDegenerate(text: String): Boolean {
            val t = text.trim()
            if (t.length < DEGEN_MIN_LEN) return false
            val lines = t.split("\n").map { it.trim() }.filter { it.isNotEmpty() }
            if (lines.size >= 3) {
                var run = 1
                for (i in 1 until lines.size) {
                    run = if (lines[i] == lines[i - 1]) run + 1 else 1
                    if (run >= 3) return true
                }
            }
            val head = t.take(200)
            if (head.isNotEmpty() && head.count { it == head[0] }.toDouble() / head.length > 0.5) {
                return true
            }
            return false
        }

        fun looksLowConfidence(text: String): Boolean {
            val lower = text.lowercase()
            return ABSTAIN_MARKERS.any { lower.contains(it.lowercase()) }
        }

        /** 引用了未注册的工具名 → 工具幻觉（服务端 `referenced_tools` 的客户端版）。 */
        fun referencesUnknownTool(text: String, availableTools: Set<String>): Boolean {
            if (availableTools.isEmpty()) return false
            val known = availableTools.map { it.lowercase() }.toSet()
            val mentioned = TOOL_NAME.find(text)?.let { match ->
                match.groupValues.drop(1)
                    .filter { it.isNotEmpty() }
                    .map { it.lowercase() }
                    .toSet()
            } ?: return false
            return mentioned.any { it !in known }
        }

        private val TOOL_NAME = Regex(
            "\"(?:tool|name|function)\"\\s*:\\s*\"([A-Za-z_][A-Za-z0-9_]*)\"",
        )

        fun parseJsonObjectOrNull(text: String): JsonObject? = try {
            // 容忍 ```json 围栏与前后废话：只取第一个 { 到最后一个 }
            val start = text.indexOf('{')
            val end = text.lastIndexOf('}')
            if (start < 0 || end <= start) null else JsonObject.parse(text.substring(start, end + 1))
        } catch (e: Exception) {
            null
        }
    }
}

/**
 * 端侧运行时配置：阈值与模型清单。
 *
 * 默认值取自 M0 在 M5 Pro 上的实测（RFC §2.4）：2B decode 86–116 tok/s、
 * 923 token 输入 TTFT 427ms；4B 作为默认档，9B 作为质量档。
 */
data class EdgeRuntimeConfig(
    val edgeBaseUrl: String,
    val sekbBaseUrl: String,
    val models: Map<String, String> = mapOf(
        "short" to "qwen3.5-2b",
        "default" to "qwen3.5-4b",
        "quality" to "qwen3.5-9b",
    ),
    val maxInputTokens: Int = 2048,
    /**
     * 端侧输出预算。
     *
     * ⚠️ 这个值不能直接抄服务端的 300：服务端那边 300 是在"长答案本来就该上云"的前提下定的，
     * 而端侧宿主的**主功能就是聊天**——第一版抄了 300，结果 `chat` 角色预期 400 字符，
     * 于是每一次聊天都被判去云端，"端侧优先"形同虚设（这个坑由单元测试当场抓住）。
     *
     * 512 的依据：M0 实测 2B decode 86–116 tok/s → 512 token ≈ 4.5–6s 最坏；
     * 首字延迟由 `maxTtftMs` 与流式前缀守卫兜住，超时信号会把它改道云端。
     */
    val maxOutputTokens: Int = 512,
    val maxTtftMs: Int = 800,
    /** 流式前缀守卫的缓冲字符数（0 = 关闭）。60 ≈ 2B 上 0.3–0.6s，是退化检测下限（40）之上的最小代价 */
    val guardChars: Int = 60,
    /** 端侧默认关思考：实测同一任务 2283ms → 89ms（25 倍） */
    val disableThinking: Boolean = true,
    val preferEdge: Boolean = true,
    val deviceOnlyRoles: Set<String> = emptySet(),
    val escalateOn: Set<String> = setOf(
        PlaneRouter.SIGNAL_JSON_INVALID, PlaneRouter.SIGNAL_EMPTY,
        PlaneRouter.SIGNAL_DEGENERATE, PlaneRouter.SIGNAL_TIMEOUT,
        PlaneRouter.SIGNAL_LOW_CONFIDENCE, PlaneRouter.SIGNAL_TOOL_HALLUCINATION,
    ),
    /**
     * 端侧可派发的工具集。
     *
     * ⚠️ 这里必须与 SEKB 服务端的 `plane_router.DEFAULT_AVAILABLE_TOOLS` **保持同一口径**：
     * 该清单是"工具幻觉"信号的判据，两边不一致会把合法工具误判成幻觉（或反之）。
     */
    val availableTools: Set<String> = setOf(
        "device_time", "device_network", "device_contacts_search", "kb_search",
    ),
    val appVersion: String = "0.1.0",
    /** 端侧向量空间标识：跨端交换必须一致，不一致要重算而不是复用缓存（§4.5-F） */
    val embeddingSpace: String = "BAAI/bge-small-zh-v1.5@512",
)
