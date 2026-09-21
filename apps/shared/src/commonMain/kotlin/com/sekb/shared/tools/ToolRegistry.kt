package com.sekb.shared.tools

import com.sekb.shared.core.nowMillis

import com.sekb.shared.model.ToolCall
import com.sekb.shared.model.ToolResult

/**
 * 设备工具：**注册即可见，执行要过闸**。
 *
 * 每个工具声明自己需要的 Android 权限（没有就是 null）。执行前由 [ToolRegistry] 检查，
 * 未授权一律拦下并写审计——这是"越权拦截率"这个验收数字的唯一来源。
 */
data class DeviceTool(
    val name: String,
    val description: String,
    /** 需要的 Android 权限（如 `android.permission.READ_CONTACTS`）；null = 不需要 */
    val requiredPermission: String? = null,
    /** 参数名 → 说明（用于生成给模型的 schema，**不代表必填**） */
    val args: Map<String, String> = emptyMap(),
    /**
     * 必填参数。
     *
     * ⚠️ 必须与 [args] 分开：第一版把"声明过的参数"一律当必填，于是 `kb_search` 的可选参数
     * `top_k` 让每次调用都变成"缺少参数: top_k"，工具直接不可用。
     * 默认取 [args] 的全部键（保持既有工具行为不变），有可选参数的工具显式声明。
     */
    val requiredArgs: Set<String> = args.keys,
    /** 真正的实现。**只有过了权限闸门才会被调用** */
    val run: (Map<String, String>) -> ToolResult,
)

/**
 * 权限审计：每一次工具调用都留一条记录（允许 / 拒绝 + 原因）。
 *
 * 为什么必须有：端侧 Agent 能读设备数据，"它到底读了什么、被拦了什么"要能被用户看到。
 * 越权拦截率 = 拒绝次数 / 总调用次数，是 M2 的验收数字之一（RFC §9）。
 */
class PermissionAudit(private val capacity: Int = 500) {

    data class Entry(
        val timestampMillis: Long,
        val tool: String,
        val allowed: Boolean,
        val reason: String,
    )

    private val entries = ArrayDeque<Entry>()

    data class Stats(
        val total: Int,
        val allowed: Int,
        val denied: Int,
        /** 越权拦截率：被拦下的比例（分母为 0 时返回 0.0，避免 NaN） */
        val deniedRate: Double,
    )

    fun record(tool: String, allowed: Boolean, reason: String, nowMillis: Long = com.sekb.shared.core.nowMillis()) {
        entries.addLast(Entry(nowMillis, tool, allowed, reason))
        while (entries.size > capacity) entries.removeFirst()
    }

    fun all(): List<Entry> = entries.toList()

    fun recent(limit: Int): List<Entry> = entries.toList().takeLast(limit).reversed()

    fun clear() = entries.clear()

    fun stats(): Stats {
        val total = entries.size
        val denied = entries.count { !it.allowed }
        return Stats(
            total = total,
            allowed = total - denied,
            denied = denied,
            deniedRate = if (total == 0) 0.0 else denied.toDouble() / total,
        )
    }
}

/** 权限检查抽象：Android 实现走 `ContextCompat.checkSelfPermission`，测试用假实现。 */
fun interface PermissionChecker {
    fun has(permission: String): Boolean
}

/**
 * 工具注册表：**唯一**的工具执行入口。
 *
 * 三道闸门，顺序不能换：
 * 1. 工具名必须已注册 → 否则是**工具幻觉**（返回 `reason=tool_hallucination`，
 *    上层据此触发升级信号）；
 * 2. 必填参数必须齐 → 缺参数不执行（避免把 null 传进设备 API）；
 * 3. 需要权限的必须已授权 → 否则拦下并审计（`denied=true`）。
 */
class ToolRegistry(
    private val tools: List<DeviceTool>,
    private val checker: PermissionChecker,
    private val audit: PermissionAudit = PermissionAudit(),
    private val now: () -> Long = { nowMillis() },
) {

    private val byName = tools.associateBy { it.name }

    fun names(): Set<String> = byName.keys

    fun all(): List<DeviceTool> = tools

    fun find(name: String): DeviceTool? = byName[name]

    /** 执行一次工具调用。**永远不会抛**——失败也是一种结果，要让模型看到。 */
    fun execute(call: ToolCall): ToolResult {
        val tool = byName[call.tool]
        if (tool == null) {
            audit.record(call.tool, allowed = false, reason = "unregistered_tool", nowMillis = now())
            return ToolResult(ok = false, denied = true, reason = "tool_hallucination:未知工具 ${call.tool}")
        }
        val missing = ToolCallJson.missingRequired(call, tool)
        if (missing.isNotEmpty()) {
            audit.record(call.tool, allowed = false, reason = "missing_args:${missing.joinToString(",")}",
                nowMillis = now())
            return ToolResult(ok = false, reason = "缺少参数: ${missing.joinToString(", ")}")
        }
        val permission = tool.requiredPermission
        if (permission != null && !checker.has(permission)) {
            audit.record(call.tool, allowed = false, reason = "permission_denied:$permission",
                nowMillis = now())
            return ToolResult(ok = false, denied = true, reason = "未授权 $permission，已拦截")
        }
        audit.record(call.tool, allowed = true, reason = "ok", nowMillis = now())
        return try {
            tool.run(call.args)
        } catch (e: Exception) {
            ToolResult(ok = false, reason = "执行异常: ${e.message?.take(80) ?: "unknown"}")
        }
    }

    fun auditStats(): PermissionAudit.Stats = audit.stats()

    fun auditEntries(limit: Int = 50): List<PermissionAudit.Entry> = audit.recent(limit)
}
