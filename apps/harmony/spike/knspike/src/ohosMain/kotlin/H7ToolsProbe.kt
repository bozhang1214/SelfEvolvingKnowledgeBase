// H7 自检：**工具权限三闸门 + 越权拦截率**（复用共享层 `ToolRegistry`，不另写一套判定）。
//
// ── 为什么放在 Kotlin 侧而不是 ArkTS ──────────────────────────────────────────
// 方案（`docs/多端跨端-鸿蒙端方案.md` H7 行）写得很明确：
// **"隐私判定留在共享层，不要搬到 ArkTS"**。三闸门的顺序、原因码、审计口径都是
// `apps/shared/.../tools/ToolRegistry.kt` 里的既有契约（Android 已按它验过），
// 端侧各平台只应"提供端口 + 触发一次"，**判定逻辑一份**。所以这里只做：
// 用**假的 PermissionChecker**（不碰真实系统权限）构造一个真的 `ToolRegistry`，
// 把四类调用打过去，再把审计统计读回来 —— 判据与 Android 同名同义。
//
// ── 四类调用（对应三道闸门 + 一次放行）──────────────────────────────────────
//   ① 未注册工具            → `tool_hallucination`（闸门 1：工具幻觉）
//   ② 缺必填参数            → 不执行、报缺哪个（闸门 2）
//   ③ 需要权限但未授权      → `denied=true`、记审计（闸门 3）
//   ④ 需要权限且已授权      → 真的执行（证明闸门**不是一律拒绝**）
// 只测"拦得住"而不测"放得行"，会出现"永远拒绝也全绿"的假通过——所以 ④ 不可省。
@file:OptIn(kotlin.experimental.ExperimentalNativeApi::class)

package com.sekb.ohos.spike

import com.sekb.shared.tools.DeviceTool
import com.sekb.shared.tools.PermissionChecker
import com.sekb.shared.tools.ToolRegistry
import com.sekb.shared.model.ToolCall
import com.sekb.shared.model.ToolResult
import com.sekb.shared.core.Fmt

/** 报告（与 `RdbIdReport`/`RagReport` 同格式：首行 `H7 PASS n/N`，明细一行一项）。 */
private class H7Report {
    val lines = mutableListOf<String>()
    var failed = 0
    var total = 0

    fun check(name: String, ok: Boolean, detail: String) {
        total++
        if (!ok) failed++
        lines += "${if (ok) "PASS" else "FAIL"} $name — $detail"
    }

    fun note(s: String) {
        lines += "NOTE $s"
    }

    fun render(): String =
        (listOf("H7 ${if (failed == 0) "PASS" else "FAIL"} ${total - failed}/$total") + lines)
            .joinToString("\n")
}

/**
 * H7 自检入口（**无参数**：不碰系统权限、不碰模型、不碰网络，毫秒级）。
 *
 * 返回多行报告，首行 `H7 PASS n/N` 或 `H7 FAIL n/N`。
 */
@CName("sekb_spike_h7_tools")
fun sekbSpikeH7Tools(): String {
    val r = H7Report()

    // 一个"只有 READ_CONTACTS 已授权"的假检查器：**故意不碰真实系统权限**，
    // 否则这条自检的结果会随设备授权状态变（那是环境，不是端侧逻辑）。
    val granted = setOf("android.permission.READ_CONTACTS")
    val checker = PermissionChecker { p -> p in granted }

    // 三个工具：kbtool 免权限且有真实现；contact 需要权限；cal 需要另一项权限（用于"未授权"一档）
    var kbRan = 0
    var contactRan = 0
    val tools = listOf(
        DeviceTool(
            name = "kb_search",
            description = "检索本机知识库",
            requiredPermission = null,
            args = mapOf("q" to "查询词", "top_k" to "返回条数（可选）"),
            requiredArgs = setOf("q"),
        ) { args ->
            kbRan++
            ToolResult(ok = true, reason = "kb:${args["q"]}")
        },
        DeviceTool(
            name = "read_contacts",
            description = "读联系人",
            requiredPermission = "android.permission.READ_CONTACTS",
            args = mapOf("name" to "姓名"),
        ) { ToolResult(ok = true, reason = "contacts-ok") },
        DeviceTool(
            name = "read_calendar",
            description = "读日历",
            requiredPermission = "android.permission.READ_CALENDAR",
            args = mapOf("date" to "日期"),
        ) { ToolResult(ok = true, reason = "calendar-ok") },
    )
    val reg = ToolRegistry(tools, checker)

    // ⓪ 能力面：三个工具都注册上了（否则后面的"未知工具"一档会失去意义）
    r.check(
        "tool_registry_names",
        reg.names() == setOf("kb_search", "read_contacts", "read_calendar"),
        "注册的工具=${reg.names().sorted()}",
    )

    // ① 闸门 1：未注册工具 = 工具幻觉（**必须 denied，且原因码可被判上层识别**）
    val t1 = reg.execute(ToolCall("delete_everything", emptyMap()))
    r.check(
        "permission_gate_unregistered",
        !t1.ok && t1.denied && t1.reason.startsWith("tool_hallucination"),
        "未注册工具 → ok=${t1.ok} denied=${t1.denied} reason='${t1.reason}'",
    )

    // ② 闸门 2：缺必填参数（`kb_search` 声明了可选 `top_k`，缺它**不算**缺参数——
    //    这正是 value 里那条"把声明当必填"的旧缺陷要防的）
    val t2 = reg.execute(ToolCall("kb_search", emptyMap()))
    r.check(
        "permission_gate_missing_args",
        !t2.ok && !t2.denied && t2.reason.contains("q") && kbRan == 0,
        "缺必填 q → ok=${t2.ok} reason='${t2.reason}' 工具是否被执行=${kbRan > 0}（期望 false）",
    )
    // ②′ 反向：只给必填（不给可选的 top_k）**必须放行** —— 防"可选参数被当必填"
    val t2b = reg.execute(ToolCall("kb_search", mapOf("q" to "端侧 RAG")))
    r.check(
        "permission_gate_optional_arg_not_required",
        t2b.ok && kbRan == 1,
        "只给必填 q（不给可选 top_k）→ ok=${t2b.ok} reason='${t2b.reason}' 调用次数=$kbRan（期望 1）",
    )

    // ③ 闸门 3：需要权限但未授权 → 拦下 + 审计
    val t3 = reg.execute(ToolCall("read_calendar", mapOf("date" to "2026-10-26")))
    r.check(
        "permission_gate_denied",
        !t3.ok && t3.denied && t3.reason.contains("READ_CALENDAR"),
        "未授权 READ_CALENDAR → ok=${t3.ok} denied=${t3.denied} reason='${t3.reason}'",
    )

    // ④ 反向：**已授权必须放行**（证明闸门不是"一律拒绝"）
    val t4 = reg.execute(ToolCall("read_contacts", mapOf("name" to "张")))
    r.check(
        "permission_gate_allows_granted",
        t4.ok && !t4.denied && t4.reason == "contacts-ok",
        "已授权 READ_CONTACTS → ok=${t4.ok} denied=${t4.denied} reason='${t4.reason}'",
    )

    // ⑤ 审计统计：越权拦截率 = 拒绝/总数（M2 的验收数字，RFC §9）
    val st = reg.auditStats()
    // 到这里的四次调用：未注册(拒) / 缺参数(拒) / 未授权(拒) / 已授权(允) + 上面 ②′ 的放行 = 5 次
    val deniedExpected = 3
    val totalExpected = 5
    r.check(
        "audit_stats",
        st.denied == deniedExpected && st.total == totalExpected,
        "审计：total=${st.total}（期望 $totalExpected）denied=${st.denied}（期望 $deniedExpected）" +
            " 拦截率=${Fmt.fixed(st.deniedRate * 100, 1)}%",
    )

    // ⑥ 审计明细可见（用户要能看到"读了什么、被拦了什么"）：最近一条应是"已授权"
    val recent = reg.auditEntries(limit = 1)
    r.check(
        "audit_entries_visible",
        recent.isNotEmpty() && recent[0].tool == "read_contacts" && recent[0].allowed,
        "最近一条审计=${recent.firstOrNull()?.let { "${it.tool}/${if (it.allowed) "allowed" else "denied"}/${it.reason}" } ?: "无"}",
    )

    r.note("闸门顺序=未注册 → 缺参数 → 未授权 → 执行（共享层 ToolRegistry 一处定义，端侧不复制判定）")
    return r.render()
}
