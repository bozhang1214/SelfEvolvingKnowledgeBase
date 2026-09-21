package com.sekb.ondevice

import com.sekb.shared.model.Plane
import com.sekb.shared.route.EdgeRuntimeConfig
import com.sekb.shared.route.PlaneRouter
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * 契约夹具 runner（Android/JVM 侧）。
 *
 * **设计目的**（见 `apps/contract/README.md`）：四端行为一致性不能靠"抄得仔细"，
 * 必须由同一份夹具各端各跑一遍来证明。这个 runner 就是 Android/JVM 那一遍。
 *
 * 三条"防假绿"的硬要求（缺一条夹具就会变成摆设）：
 * 1. 夹具目录/文件缺失 → **失败**，不是跳过；
 * 2. case 数量低于下限 / 6 类信号没被覆盖 → **失败**；
 * 3. 逐条按 `id` 断言，失败信息带 id，便于定位是哪一端不一致。
 *
 * 夹具目录默认 `apps/contract`（相对模块目录 `apps/android/app`），
 * 可用 `-Dsekb.contractDir=<path>` 覆盖（CI / 未来 iOS 侧复用同一份 JSON 时用）。
 */
class ContractFixturesTest {

    private val dir: File =
        File(System.getProperty("sekb.contractDir") ?: "../../contract").canonicalFile

    private val sixSignals = setOf(
        "json_invalid", "empty", "degenerate", "timeout", "low_confidence", "tool_hallucination",
    )

    // ─────────────────────────── 夹具读取 ───────────────────────────

    private fun load(name: String): JSONObject {
        val f = File(dir, name)
        assertTrue(
            "契约夹具缺失：${f.absolutePath}（默认相对 apps/android/app 为 ../../contract，" +
                "可用 -Dsekb.contractDir 覆盖）",
            f.isFile,
        )
        return JSONObject(f.readText(Charsets.UTF_8))
    }

    private fun JSONArray.toStrings(): Set<String> =
        (0 until length()).map { getString(it) }.toSet()

    /** messages 元素：字符串，或 {"repeat": {"unit": "...", "times": N}}（避免夹具里贴长文本）。 */
    private fun messages(arr: JSONArray?): List<String> {
        if (arr == null) return emptyList()
        return (0 until arr.length()).map { i ->
            when (val v = arr.get(i)) {
                is String -> v
                is JSONObject -> {
                    val r = v.getJSONObject("repeat")
                    r.getString("unit").repeat(r.getInt("times"))
                }
                else -> throw AssertionError("messages[$i] 类型不支持：$v")
            }
        }
    }

    /** 默认值 + 覆盖（覆盖可以来自 `config` 子对象，也可以直接写在 case 上）。 */
    private fun configOf(defaults: JSONObject, vararg overrideSources: JSONObject?): EdgeRuntimeConfig {
        val merged = JSONObject(defaults.toString())
        for (src in overrideSources) {
            if (src == null) continue
            for (key in src.keys()) merged.put(key, src.get(key))
        }
        return EdgeRuntimeConfig(
            edgeBaseUrl = merged.optString("edgeBaseUrl", "http://127.0.0.1:11434/v1"),
            sekbBaseUrl = "https://example.test/sekb",
            maxInputTokens = merged.optInt("maxInputTokens", 2048),
            maxOutputTokens = merged.optInt("maxOutputTokens", 512),
            maxTtftMs = merged.optInt("maxTtftMs", 800),
            guardChars = merged.optInt("guardChars", 60),
            preferEdge = merged.optBoolean("preferEdge", true),
            deviceOnlyRoles = merged.optJSONArray("deviceOnlyRoles")?.toStrings() ?: emptySet(),
            availableTools = merged.optJSONArray("availableTools")?.toStrings()
                ?: setOf("device_time", "device_network", "device_contacts_search", "kb_search"),
        )
    }

    private fun casesOf(doc: JSONObject): List<JSONObject> {
        val arr = doc.optJSONArray("cases")
            ?: throw AssertionError("夹具缺少 cases 数组：schema=${doc.optString("schema")}")
        return (0 until arr.length()).map { arr.getJSONObject(it) }
    }

    private fun assertSchema(doc: JSONObject, expectPrefix: String) {
        val s = doc.optString("schema")
        assertTrue("schema 前缀不符：$s（期望 $expectPrefix…）", s.startsWith(expectPrefix))
    }

    // ─────────────────────────── 1) 路由决策 ───────────────────────────

    @Test
    fun `routing fixtures match client router`() {
        val doc = load("routing.json")
        assertSchema(doc, "sekb.contract.routing/")
        val defaults = doc.getJSONObject("defaults")
        val cases = casesOf(doc)
        assertTrue("routing 夹具过少（${cases.size}），疑似被截断", cases.size >= 10)

        for (c in cases) {
            val id = c.getString("id")
            val router = PlaneRouter(configOf(defaults, c.optJSONObject("config")))
            val d = router.decide(
                role = c.getString("role"),
                messages = messages(c.optJSONArray("messages")),
                expectedOutputTokens = if (c.has("expectedOutputTokens")) c.getInt("expectedOutputTokens") else null,
                deviceData = c.optBoolean("deviceData", false),
            )
            val e = c.getJSONObject("expect")

            if (e.has("plane")) {
                val want = if (e.getString("plane") == "edge") Plane.EDGE else Plane.CLOUD
                assertEquals("[$id] plane", want, d.plane)
            }
            if (e.has("reason")) assertEquals("[$id] reason", e.getString("reason"), d.reason)
            if (e.has("reasonPrefix")) {
                assertTrue("[$id] reason 应以 ${e.getString("reasonPrefix")} 开头，实际=${d.reason}",
                    d.reason.startsWith(e.getString("reasonPrefix")))
            }
            if (e.has("tier")) assertEquals("[$id] tier", e.getString("tier"), d.tier)
            if (e.has("deviceOnly")) assertEquals("[$id] deviceOnly", e.getBoolean("deviceOnly"), d.deviceOnly)
            if (e.has("escalationAllowed")) {
                assertEquals("[$id] escalationAllowed", e.getBoolean("escalationAllowed"), d.escalationAllowed())
            }
            if (e.has("outputBudget")) assertEquals("[$id] outputBudget", e.getInt("outputBudget"), d.outputBudget)
        }
    }

    // ─────────────────────────── 2) 升级信号 ───────────────────────────

    @Test
    fun `signal fixtures match client evaluator`() {
        val doc = load("signals.json")
        assertSchema(doc, "sekb.contract.signals/")
        val defaults = doc.getJSONObject("defaults")
        val cases = casesOf(doc)
        assertTrue("signals 夹具过少（${cases.size}），疑似被截断", cases.size >= 13)

        for (c in cases) {
            val id = c.getString("id")
            val router = PlaneRouter(configOf(defaults, c.optJSONObject("config")))
            val actual = router.evaluate(
                text = c.getString("text"),
                responseFormat = if (c.has("responseFormat")) c.getString("responseFormat") else null,
                ttftMillis = c.optDouble("ttftMillis", 0.0),
                availableTools = router.config.availableTools,
                partial = c.optBoolean("partial", false),
            ).sorted()
            val want = c.getJSONArray("expectSignals").let { a -> (0 until a.length()).map { a.getString(it) } }.sorted()
            assertEquals("[$id] 信号集合不一致（多一个/少一个都算行为变更）", want, actual)
        }
    }

    // ─────────────────────────── 3) 隐私硬边界 ───────────────────────────

    @Test
    fun `privacy fixtures never let DEVICE_ONLY escalate`() {
        val doc = load("privacy.json")
        assertSchema(doc, "sekb.contract.privacy/")
        val defaults = doc.getJSONObject("defaults")
        val cases = casesOf(doc)
        assertTrue("privacy 夹具过少（${cases.size}），疑似被截断", cases.size >= 13)

        for (c in cases) {
            val id = c.getString("id")
            val router = PlaneRouter(configOf(defaults, c))
            val deviceData = c.optBoolean("deviceData", false)
            val d = router.decide(
                role = c.getString("role"),
                messages = listOf(c.optString("text", "")),
                deviceData = deviceData,
            )
            if (!deviceData && !c.has("deviceOnlyRoles")) {
                // 对照组：普通数据不该被 R10 误伤（只断言这一点）
                assertFalse("[$id] 普通数据不该被拦", d.isBlocked())
                continue
            }
            assertEquals("[$id] DEVICE_ONLY 必须判给端侧", Plane.EDGE, d.plane)
            assertTrue("[$id] deviceOnly 必须为 true", d.deviceOnly)
            assertFalse("[$id] DEVICE_ONLY 数据永不允许升级（硬边界）", d.escalationAllowed())
            if (c.has("blocked")) {
                assertEquals("[$id] blocked（是否一个请求都不发）", c.getBoolean("blocked"), d.isBlocked())
            }
            if (c.has("expectReason")) {
                assertEquals("[$id] reason", c.getString("expectReason"), d.reason)
            } else {
                assertEquals("[$id] reason", PlaneRouter.REASON_DEVICE_ONLY, d.reason)
            }

            val signals = router.evaluate(
                text = c.optString("text", ""),
                responseFormat = if (c.has("responseFormat")) c.getString("responseFormat") else null,
                ttftMillis = c.optDouble("ttftMillis", 0.0),
                availableTools = router.config.availableTools,
            ).sorted()
            val want = c.getJSONArray("expectSignals").let { a -> (0 until a.length()).map { a.getString(it) } }.sorted()
            assertEquals("[$id] 信号集合不一致", want, signals)
            // 被 R10 拦下的 case：请求根本没发出去，自然没有"升级信号命中"这回事——
            // 它要证明的是"不发"，由 blocked/reason 断言负责（编排器层面另有单测钉住"零调用"）。
            if (!d.isBlocked()) {
                assertTrue("[$id] 该 case 必须真的命中升级信号（否则测不到「拦住」这件事）",
                    router.shouldEscalate(signals))
            }
        }
    }

    // ─────────────────────────── 4) 覆盖度自检 ───────────────────────────

    @Test
    fun `fixtures cover all six escalation signals`() {
        val seen = mutableSetOf<String>()
        for (name in listOf("signals.json", "privacy.json", "routing.json")) {
            for (c in casesOf(load(name))) {
                c.optJSONArray("expectSignals")?.let { a ->
                    for (i in 0 until a.length()) seen.add(a.getString(i))
                }
            }
        }
        assertEquals("6 类升级信号必须逐个出现在夹具里，缺失=$sixSignals",
            emptySet<String>(), sixSignals - seen)
    }
}
