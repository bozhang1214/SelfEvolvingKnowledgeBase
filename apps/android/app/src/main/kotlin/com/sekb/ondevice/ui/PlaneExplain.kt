package com.sekb.ondevice.ui

/**
 * 把内部的**路由/升级原因码**翻译成用户读得懂的一句话。
 *
 * ## 为什么需要它（这不是"文案美化"）
 *
 * 执行位置徽章会显示"已上云（端侧不达标）"——这只给了**结论**。
 * 用户（正确地）会追问"为什么不达标？我的数据出去了吗？"。
 * 而原因码本身是给日志看的：`degenerate`、`escalation_blocked:device_only`、
 * `output_over_edge_budget(600>512)`。把码直接摆给用户等于没解释。
 *
 * 端侧产品的核心承诺是"**设备专属数据不出端**"，所以"什么时候出了端、为什么"
 * 必须能被用户自己看懂——否则这个承诺就只是一句宣传语。
 *
 * ## 设计原则：**不认识的原因码也要原样透出**
 *
 * 绝不返回"未知原因"这类兜底话术——那会让将来新加的原因码静默消失，
 * 用户看到"未知"、开发者也发现不了。认不出就把原始码显示出来（难看但诚实）。
 */
object PlaneExplain {

    /** 原因码 → 用户能懂的话。`{1}` 会被正则的第一个捕获组替换。 */
    private val TABLE: List<Pair<Regex, String>> = listOf(
        // —— 路由阶段就决定走云端 ——
        Regex("""^edge_preferred""") to
            "这次请求在设备能力范围内，按设计优先由本机处理。",
        Regex("""^input_over_edge_budget\(([^)]*)\)""") to
            "输入太长（{1}），超出端侧能处理的规模，按设计直接交给云端。",
        Regex("""^output_over_edge_budget\(([^)]*)\)""") to
            "预期回答长度（{1}）超出端侧预算，按设计直接交给云端。",
        Regex("""^prefer_cloud""") to
            "按当前策略，这类请求走云端。",

        // —— 隐私硬边界 ——
        Regex("""^escalation_blocked:device_only_requires_local_runtime""") to
            "这是设备专属内容，但当前端侧地址不是本机（回环地址）；为不让数据出端，**这次请求没有发出**。",
        Regex("""^escalation_blocked:device_only""") to
            "这是设备专属内容，不允许改道云端，因此**不升级**。",
        Regex("""^escalation_blocked:(.*)$""") to
            "这次不允许改道云端（原因：{1}）。",
        Regex("""^device_only_data""") to
            "这是标记为「设备专属」的内容，只允许在本机处理。",

        // —— 端侧生成中被守卫拦下（这些会被 outcome() 拼进"端侧没能达标（…）"） ——
        // 真机实测发现的原因码：这次是它导致"已上云（端侧不达标）"。
        // （先以"原样透出"的形式暴露出来，才被发现 → 这正是那条设计原则的价值）
        Regex("""^edge_unavailable""") to "端侧运行时不可用（连不上或调用失败）",
        Regex("""^degenerate""") to "端侧回答中途开始重复、退化",
        Regex("""^timeout""") to "端侧生成超时",
        Regex("""^low_confidence""") to "端侧对这次回答没有把握",
        Regex("""^json_invalid""") to "端侧要调用的工具参数不是合法格式",
        Regex("""^empty""") to "端侧没有产出有效内容",
        Regex("""^tool_hallucination""") to "端侧试图调用不存在的工具",
        Regex("""^context_overflow""") to "上下文超出端侧能容纳的长度",
    )

    /**
     * 逗号或 ` · ` 连接的多个原因码逐个翻译。
     *
     * 为什么要一并处理 ` · `：编排器在多信号同时命中时用**逗号**拼接，
     * 而路由行历史上用的是 ` · `。只按逗号切的话，`edge_preferred · degenerate`
     * 会因为前缀正则命中 `edge_preferred` 而**把升级原因整段吞掉**（实测踩到：
     * 徽章显示"已上云"，解释却说"优先由本机处理"）。
     */
    fun describe(raw: String): String {
        val parts = raw.split(',', '·').map { it.trim() }.filter { it.isNotEmpty() }
        if (parts.isEmpty()) return ""
        return parts.joinToString("；") { describeOne(it) }
    }

    private fun describeOne(code: String): String {
        TABLE.forEach { (re, tpl) ->
            val m = re.find(code) ?: return@forEach
            return if (tpl.contains("{1}")) {
                tpl.replace("{1}", m.groupValues.getOrNull(1).orEmpty())
            } else {
                tpl
            }
        }
        return "原因：$code"   // 认不出就原样透出，不让它静默消失
    }

    /**
     * 组一句"这次到底发生了什么、为什么"。
     *
     * ## ⚠️ 三个事实必须由调用方传进来，**不能**从 `decision.plane` 推
     *
     * 第一版图省事，把 `edge · edge_preferred · degenerate` 这种决策行拆开、
     * 取前面的平面词当结论 —— 结果是**升级场景下解释与徽章互相打脸**：
     * 徽章说"已上云（端侧不达标）"（依据 `execution.primaryPlane` = **实际**完成的平面），
     * 解释却说"这次在本机完成"（依据 `decision.plane` = **计划**走的平面）。
     * 而"升级"的定义恰恰就是"计划本机、实际云端"——两者必然不一致。
     * **一个自相矛盾的解释比没有解释更糟**：它会让用户不再相信任何徽章。
     *
     * @param completedOnDevice 实际是否在本机完成——**必须与徽章同源**（`execution.primaryPlane`）
     * @param escalated 是否发生过改道
     * @param decisionReason 路由阶段原因码（如 `edge_preferred`）
     * @param escalateReason 升级阶段原因码（如 `degenerate`）
     */
    fun outcome(
        completedOnDevice: Boolean,
        escalated: Boolean,
        decisionReason: String,
        escalateReason: String,
    ): String {
        if (escalated) {
            val why = describe(escalateReason).ifEmpty { describe(decisionReason) }
            return if (why.isEmpty()) "这次改由云端完成。" else "端侧没能达标（$why），这次改由云端重答。"
        }
        val tail = describe(decisionReason)
        val head = if (completedOnDevice) "这次在本机完成" else "这次在云端完成"
        return if (tail.isEmpty()) "$head。" else "$head：$tail"
    }
}
