// SEKB 鸿蒙侧 · 契约夹具 runner（H2）
//
// 目的与 Android 的 `ContractFixturesTest.kt` **完全对称**：同一份 `apps/contract/*.json`，
// 各端各跑一遍，37 个 case 逐条断言（routing 10 / signals 14 / privacy 13）。
//
// ## 为什么不能直接复用 Android 那份
//
// 它用 `org.json` + `junit`，而 `org.json` 只在 JVM/Android 上有实现——
// 这正是共享层当初把 JSON 门面换成 kotlinx-serialization 的原因（见 `core/Json.kt` 的演进史）。
// 所以这里用**共享层的 `JsonObject`** 重写一遍断言逻辑；断言口径逐条照抄 Android，
// 不趁移植之机"顺手改进"——那样会让四端的数字不再同一套。
//
// ## "防假绿"的三条（照 `apps/contract/README.md` 的硬要求）
//
// 1. 夹具缺失 / 解析失败 / case 数低于下限 / 6 类信号没被覆盖 → **FAIL**（不是跳过）；
// 2. 逐条带 `id` 断言，失败信息里必有 id，便于定位是哪一端不一致；
// 3. **夹具目录必须登记为该端测试的输入**（否则改夹具不重跑 = 假绿）。
//    鸿蒙这边的做法见 `scripts/harmony.sh contract`：三份夹具被拷进 HAP 的 rawfile，
//    而 `verify_contract_inputs` 用**内容哈希**核对 HAP 内夹具 == 仓库里那份，
//    并在构建前把哈希写进 manifest（改夹具 → 哈希变 → 校验会红）。
//
// ## 反向验证（"改夹具期望值必须变红"）
//
// `bash scripts/harmony.sh contract` 跑两遍：
//   ① 原样夹具 → 期望全绿；
//   ② `-Dsekb.contractRedCheck=true` 对应的设备侧变体（在内存里把 routing[0].expect.plane 反转）
//      → 期望**恰好 1 个 FAIL**（且 id 就是 routing 的第一条）。
// 只跑①的话，"runner 其实没断言任何东西"也会全绿，所以②是这条验收的关键。
@file:OptIn(kotlin.experimental.ExperimentalNativeApi::class)

package com.sekb.ohos.contract

import com.sekb.shared.core.JsonArray
import com.sekb.shared.core.JsonObject
import com.sekb.shared.model.Plane
import com.sekb.shared.route.EdgeRuntimeConfig
import com.sekb.shared.route.PlaneRouter

/** 一轮契约运行的结果。 */
class ContractOutcome(
    val lines: List<String>,
    val passed: Int,
    val total: Int,
    val redCheckApplied: Boolean,
) {
    val failed: Int get() = total - passed
    val ok: Boolean get() = failed == 0 && total > 0
}

object ContractRunner {

    private val SIX_SIGNALS = setOf(
        "json_invalid", "empty", "degenerate", "timeout", "low_confidence", "tool_hallucination",
    )

    /**
     * 跑一遍三份夹具。
     *
     * @param routing / signals / privacy 三份夹具的**原始 JSON 文本**（ArkTS 侧从 rawfile 读入）。
     * @param redCheck 为真时，先把 routing 第一条 case 的 `expect.plane` 反转——**期望恰好 1 个 FAIL**。
     */
    fun run(
        routing: String,
        signals: String,
        privacy: String,
        redCheck: Boolean = false,
    ): ContractOutcome {
        val lines = mutableListOf<String>()
        var passed = 0
        var total = 0

        fun expect(label: String, ok: Boolean, detail: String) {
            total++
            if (ok) passed++
            lines += "${if (ok) "PASS" else "FAIL"} ${label} — ${detail}"
        }

        val routingDoc = JsonObject.parse(routing)
        val signalsDoc = JsonObject.parse(signals)
        val privacyDoc = JsonObject.parse(privacy)
        expect(
            "fixtures_parse",
            routingDoc != null && signalsDoc != null && privacyDoc != null,
            "routing=${routingDoc != null} signals=${signalsDoc != null} privacy=${privacyDoc != null}",
        )
        if (routingDoc == null || signalsDoc == null || privacyDoc == null) {
            return ContractOutcome(lines, passed, total, false)
        }

        // 反向验证：就地反转 routing 第一条 case 的期望平面（其余一律不动）
        var redApplied = false
        if (redCheck) {
            val first = casesOf(routingDoc).firstOrNull()
            val e = first?.optJSONObject("expect")
            if (e != null && e.has("plane")) {
                val flipped = if (e.optString("plane") == "edge") "cloud" else "edge"
                e.put("plane", flipped)
                redApplied = true
                lines += "RED-CHECK 已把 routing[0](${first.optString("id")}).expect.plane 反转成 ${flipped}"
            } else {
                expect("red_check_variant_applied", false, "routing[0] 没有 expect.plane，反向验证无效")
            }
        }

        // ── 1) routing（10 case）──────────────────────────────────────────────
        val rCases = casesOf(routingDoc)
        expect("routing_schema", routingDoc.optString("schema").startsWith("sekb.contract.routing/"), routingDoc.optString("schema"))
        expect("routing_case_count", rCases.size >= 10, "${rCases.size} 个 case（下限 10）")
        for (c in rCases) {
            val id = c.optString("id").ifBlank { "<no-id>" }
            val router = PlaneRouter(configOf(routingDoc.optJSONObject("defaults"), c.optJSONObject("config")))
            val d = router.decide(
                role = c.optString("role"),
                messages = messages(c.optJSONArray("messages")),
                expectedOutputTokens = if (c.has("expectedOutputTokens")) c.optInt("expectedOutputTokens") else null,
                deviceData = c.optBoolean("deviceData", false),
            )
            val e = c.optJSONObject("expect")
            if (e == null) {
                expect("[$id]", false, "case 缺 expect 子对象")
                continue
            }
            if (e.has("plane")) {
                val want = if (e.optString("plane") == "edge") Plane.EDGE else Plane.CLOUD
                expect("[$id] plane", want == d.plane, "want=${want.wire} got=${d.plane.wire}")
            }
            if (e.has("reason")) {
                expect("[$id] reason", e.optString("reason") == d.reason, "want=${e.optString("reason")} got=${d.reason}")
            }
            if (e.has("reasonPrefix")) {
                expect(
                    "[$id] reasonPrefix",
                    d.reason.startsWith(e.optString("reasonPrefix")),
                    "want前缀=${e.optString("reasonPrefix")} got=${d.reason}",
                )
            }
            if (e.has("tier")) {
                expect("[$id] tier", e.optString("tier") == d.tier, "want=${e.optString("tier")} got=${d.tier}")
            }
            if (e.has("deviceOnly")) {
                expect("[$id] deviceOnly", e.optBoolean("deviceOnly") == d.deviceOnly, "got=${d.deviceOnly}")
            }
            if (e.has("escalationAllowed")) {
                expect(
                    "[$id] escalationAllowed",
                    e.optBoolean("escalationAllowed") == d.escalationAllowed(),
                    "got=${d.escalationAllowed()}",
                )
            }
            if (e.has("outputBudget")) {
                expect("[$id] outputBudget", e.optInt("outputBudget") == d.outputBudget, "got=${d.outputBudget}")
            }
        }

        // ── 2) signals（14 case）─────────────────────────────────────────────
        val sCases = casesOf(signalsDoc)
        expect("signals_schema", signalsDoc.optString("schema").startsWith("sekb.contract.signals/"), signalsDoc.optString("schema"))
        expect("signals_case_count", sCases.size >= 13, "${sCases.size} 个 case（下限 13）")
        for (c in sCases) {
            val id = c.optString("id").ifBlank { "<no-id>" }
            val router = PlaneRouter(configOf(signalsDoc.optJSONObject("defaults"), c.optJSONObject("config")))
            val actual = router.evaluate(
                text = c.optString("text"),
                responseFormat = if (c.has("responseFormat")) c.optString("responseFormat") else null,
                ttftMillis = c.optDouble("ttftMillis", 0.0),
                availableTools = router.config.availableTools,
                partial = c.optBoolean("partial", false),
            ).sorted()
            val want = stringsOf(c.optJSONArray("expectSignals")).sorted()
            expect("[$id] signals", want == actual, "want=$want got=$actual")
        }

        // ── 3) privacy（13 case）────────────────────────────────────────────
        val pCases = casesOf(privacyDoc)
        expect("privacy_schema", privacyDoc.optString("schema").startsWith("sekb.contract.privacy/"), privacyDoc.optString("schema"))
        expect("privacy_case_count", pCases.size >= 13, "${pCases.size} 个 case（下限 13）")
        for (c in pCases) {
            val id = c.optString("id").ifBlank { "<no-id>" }
            // ⚠️ 与 Android 一致：privacy 的 config 覆盖来自 **case 自身**（不是 config 子对象）
            val router = PlaneRouter(configOf(privacyDoc.optJSONObject("defaults"), c))
            val deviceData = c.optBoolean("deviceData", false)
            val d = router.decide(
                role = c.optString("role"),
                messages = listOf(c.optString("text", "")),
                deviceData = deviceData,
            )
            if (!deviceData && !c.has("deviceOnlyRoles")) {
                // 对照组：普通数据不该被 R10 误伤（只断言这一点）
                expect("[$id] 普通数据不该被拦", !d.isBlocked(), "blocked=${d.isBlocked()}")
                continue
            }
            expect("[$id] DEVICE_ONLY 必须判给端侧", d.plane == Plane.EDGE, "plane=${d.plane.wire}")
            expect("[$id] deviceOnly 必须为 true", d.deviceOnly, "deviceOnly=${d.deviceOnly}")
            expect("[$id] DEVICE_ONLY 永不允许升级", !d.escalationAllowed(), "escalationAllowed=${d.escalationAllowed()}")
            if (c.has("blocked")) {
                expect(
                    "[$id] blocked",
                    c.optBoolean("blocked") == d.isBlocked(),
                    "want=${c.optBoolean("blocked")} got=${d.isBlocked()}",
                )
            }
            if (c.has("expectReason")) {
                expect("[$id] reason", c.optString("expectReason") == d.reason, "want=${c.optString("expectReason")} got=${d.reason}")
            } else {
                expect("[$id] reason", PlaneRouter.REASON_DEVICE_ONLY == d.reason, "got=${d.reason}")
            }

            val sigs = router.evaluate(
                text = c.optString("text", ""),
                responseFormat = if (c.has("responseFormat")) c.optString("responseFormat") else null,
                ttftMillis = c.optDouble("ttftMillis", 0.0),
                availableTools = router.config.availableTools,
            ).sorted()
            val wantSig = stringsOf(c.optJSONArray("expectSignals")).sorted()
            expect("[$id] signals", wantSig == sigs, "want=$wantSig got=$sigs")
            if (!d.isBlocked()) {
                expect("[$id] 该 case 必须真的命中升级信号", router.shouldEscalate(sigs), "signals=$sigs")
            }
        }

        // ── 4) 覆盖度自检：6 类信号逐个出现过 ────────────────────────────────
        val seen = mutableSetOf<String>()
        for (doc in listOf(routingDoc, signalsDoc, privacyDoc)) {
            for (c in casesOf(doc)) {
                for (s in stringsOf(c.optJSONArray("expectSignals"))) seen.add(s)
            }
        }
        expect("fixtures_cover_six_signals", (SIX_SIGNALS - seen).isEmpty(), "缺失=${SIX_SIGNALS - seen}")

        return ContractOutcome(lines, passed, total, redApplied)
    }

    // ─────────────────────────── 夹具读取辅助（逐条对齐 Android）────────────────

    private fun casesOf(doc: JsonObject): List<JsonObject> {
        val arr = doc.optJSONArray("cases") ?: return emptyList()
        return (0 until arr.length()).mapNotNull { arr.optJSONObject(it) }
    }

    private fun stringsOf(arr: JsonArray?): List<String> {
        if (arr == null) return emptyList()
        return (0 until arr.length()).map { arr.optString(it) }
    }

    /**
     * messages 元素：字符串，或 `{"repeat":{"unit":"...","times":N}}`。
     *
     * 用 `JsonObject.parse(元素.toString())` 而不是 `valueAt` 取子对象：
     * 门面里**没有**暴露 `JsonArray.optJSONObject` 之外的取值路径，
     * 而 `toString()` → `parse()` 是门面自身保证的往返（`JsonX.toElement`/`fromElement` 成对），
     * 比起猜内部结构更不容易随门面演进而悄悄坏掉。
     */
    private fun messages(arr: JsonArray?): List<String> {
        if (arr == null) return emptyList()
        return (0 until arr.length()).map { i ->
            when (val v = arr.valueAt(i)) {
                is String -> v
                is JsonObject -> {
                    val r = v.optJSONObject("repeat")
                    val unit = r?.optString("unit") ?: ""
                    val times = r?.optInt("times") ?: 0
                    unit.repeat(times.coerceAtLeast(0))
                }
                else -> ""
            }
        }
    }

    /**
     * 默认值 + 覆盖（覆盖来自 `config` 子对象**或** case 自身）。
     *
     * 硬编码默认值必须与 Android `ContractFixturesTest.configOf` 一致——夹具里虽然也有
     * `defaults`，但两端都以同一份硬编码为准，才能发现"夹具 defaults 被改而没人注意"。
     *
     * 实现：**逐字段显式取值** + "有就用、没有就用硬编码默认"。
     * 为什么不用"合并成一个对象再读"：共享层 JSON 门面没暴露"把某键的值原样取出"的入口
     * （`JsonObject` 只有 `optXxx/getXxx`，`valueAt` 只在 `JsonArray` 上），
     * 硬凑合并会写出比断言逻辑本身还长的样板，且更容易随门面演进而悄悄坏。
     * 这里的取值顺序与 Android 完全一致：**来源按 `defaults` → `config` → case 依次覆盖**。
     */
    private fun configOf(defaults: JsonObject?, vararg overrideSources: JsonObject?): EdgeRuntimeConfig {
        val sources = (listOf(defaults) + overrideSources.toList()).filterNotNull()
        // 后者覆盖前者：从后往前找第一个"有该键"的来源
        fun first(has: (JsonObject) -> Boolean): JsonObject? = sources.lastOrNull(has)

        val edgeBaseUrl = first { it.has("edgeBaseUrl") }?.optString("edgeBaseUrl", "http://127.0.0.1:11434/v1")
            ?: "http://127.0.0.1:11434/v1"
        val maxInputTokens = first { it.has("maxInputTokens") }?.optInt("maxInputTokens", 2048) ?: 2048
        val maxOutputTokens = first { it.has("maxOutputTokens") }?.optInt("maxOutputTokens", 512) ?: 512
        val maxTtftMs = first { it.has("maxTtftMs") }?.optInt("maxTtftMs", 800) ?: 800
        val guardChars = first { it.has("guardChars") }?.optInt("guardChars", 60) ?: 60
        val preferEdge = first { it.has("preferEdge") }?.optBoolean("preferEdge", true) ?: true
        val deviceOnlyRoles = first { it.has("deviceOnlyRoles") }
            ?.optJSONArray("deviceOnlyRoles")?.let { stringsOf(it).toSet() } ?: emptySet()
        val availableTools = first { it.has("availableTools") }
            ?.optJSONArray("availableTools")?.let { stringsOf(it).toSet() }
            ?: setOf("device_time", "device_network", "device_contacts_search", "kb_search")

        return EdgeRuntimeConfig(
            edgeBaseUrl = edgeBaseUrl,
            sekbBaseUrl = "https://example.test/sekb",
            maxInputTokens = maxInputTokens,
            maxOutputTokens = maxOutputTokens,
            maxTtftMs = maxTtftMs,
            guardChars = guardChars,
            preferEdge = preferEdge,
            deviceOnlyRoles = deviceOnlyRoles,
            availableTools = availableTools,
        )
    }
}
