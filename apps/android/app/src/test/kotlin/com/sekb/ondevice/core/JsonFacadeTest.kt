package com.sekb.ondevice.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test
import com.sekb.shared.core.*

/**
 * JSON 门面（[JsonObject] / [JsonArray]）的专测。
 *
 * **为什么值得单独测**：M4 第 2 步把 8 个文件的 `org.json` 收敛到了这一个文件，
 * 于是它成了**所有网络响应解析的单点**——而网络返回体是不可信输入。
 * 这里钉住三件最容易出事的事：
 * 1. **失败必须返回 null 而不是抛**（半截 JSON、空体、HTML 错误页）；
 * 2. **转义/中文/嵌套必须往返一致**（手写 JSON 实现最常死在这里，所以用同义反复测试把它钉死）；
 * 3. **宽松读法给默认值、严格读法该抛就抛**（调用点靠这个区分"可选字段"与"协议被改坏"）。
 */
class JsonFacadeTest {

    // ── 1. 失败路径：不抛，返回 null ──────────────────────────────────────────

    @Test
    fun `parse returns null instead of throwing on bad input`() {
        assertNull("空字符串", JsonObject.parse(""))
        assertNull("空白", JsonObject.parse("   "))
        assertNull("null 字面量", JsonObject.parse(null))
        assertNull("半截 JSON（流式截断的典型形态）", JsonObject.parse("""{"choices":[{"delta":"""))
        assertNull("HTML 错误页（网关 502 的典型形态）", JsonObject.parse("<html><body>502</body></html>"))
        assertNull("数组不是对象", JsonObject.parse("[1,2,3]").let { it })   // 能解析，但不是对象 → 见下
    }

    @Test
    fun `parse array rejects non-array`() {
        assertNull(JsonArray.parse("""{"a":1}"""))
        assertNull(JsonArray.parse(""))
    }

    // ── 2. 往返：转义 / 中文 / 嵌套 ───────────────────────────────────────────

    @Test
    fun `round trip preserves quotes newlines and chinese`() {
        val tricky = "他说：\"这是引号\"，换行\n第二行\t制表 \\反斜杠 与 emoji 🙂"
        val obj = JsonObject().put("text", tricky).put("n", 42)
        val reparsed = JsonObject.parse(obj.toString())
        assertEquals("转义后往返必须一字不差", tricky, reparsed?.optString("text"))
        assertEquals(42, reparsed?.optInt("n"))
    }

    @Test
    fun `nested objects arrays lists and maps are unwrapped correctly`() {
        val obj = JsonObject()
            .put("inner", JsonObject().put("a", 1))
            .put("arr", JsonArray().put("x").put(2))
            .put("list", listOf("p", "q"))
            .put("map", mapOf("k" to 7))
            .put("nullable", null)

        val back = JsonObject.parse(obj.toString())!!
        assertEquals(1, back.optJSONObject("inner")?.optInt("a"))
        assertEquals(2, back.optJSONArray("arr")?.length())
        assertEquals("x", back.optJSONArray("arr")?.optString(0))
        assertEquals("q", back.optJSONArray("list")?.optString(1))
        assertEquals(7, back.optJSONObject("map")?.optInt("k"))
        assertTrue("显式 null 应保留键", back.has("nullable"))
    }

    @Test
    fun `put returns this so chaining works`() {
        val obj = JsonObject()
        assertSame(obj, obj.put("a", 1))
        val arr = JsonArray()
        assertSame(arr, arr.put("a"))
    }

    // ── 3. 宽松 vs 严格 ──────────────────────────────────────────────────────

    @Test
    fun `opt accessors fall back when key is missing or type differs`() {
        val obj = JsonObject.parse("""{"s":"x","i":3,"d":1.5,"b":true}""")!!
        assertEquals("x", obj.optString("s"))
        assertEquals("默认", obj.optString("missing", "默认"))
        assertEquals(3, obj.optInt("i"))
        assertEquals(9, obj.optInt("missing", 9))
        assertEquals(1.5, obj.optDouble("d"), 1e-9)
        assertTrue(obj.optBoolean("b"))
        assertFalse(obj.optBoolean("missing"))
        assertNull(obj.optJSONObject("s"))
        assertNull(obj.optJSONArray("i"))
    }

    @Test
    fun `strict accessors throw when the protocol is broken`() {
        val obj = JsonObject.parse("""{"ok":true}""")!!
        var threw = false
        try {
            obj.getString("nope")
        } catch (e: Exception) {
            threw = true
        }
        assertTrue("getString 缺失必须抛（协议被改坏要早暴露）", threw)
    }

    @Test
    fun `keys length and has reflect content`() {
        val obj = JsonObject.parse("""{"a":1,"b":2}""")!!
        assertEquals(setOf("a", "b"), obj.keys())
        assertEquals(2, obj.length())
        assertTrue(obj.has("a"))
        assertFalse(obj.has("c"))
        assertTrue(JsonObject().isEmpty())
    }

    // ── 4. JsonX 助手（路由事件等调用点直接用） ───────────────────────────────

    @Test
    fun `JsonX helpers tolerate null receivers`() {
        assertEquals("", JsonX.string(null, "k"))
        assertEquals(7, JsonX.int(null, "k", 7))
        assertEquals(0.5, JsonX.double(null, "k", 0.5), 1e-9)
        assertFalse(JsonX.bool(null, "k"))
        assertNull(JsonX.objectOrNull(null, "k"))
        assertTrue(JsonX.intMap(null, "k").isEmpty())
    }

    @Test
    fun `intMap reads dynamic keys`() {
        val obj = JsonObject.parse("""{"by_plane":{"edge":2,"cloud":1}}""")!!
        assertEquals(mapOf("edge" to 2, "cloud" to 1), JsonX.intMap(obj, "by_plane"))
    }

    @Test
    fun `intMap treats missing or scalar value as empty`() {
        assertEquals(emptyMap<String, Int>(), JsonX.intMap(JsonObject(), "by_plane"))
        assertEquals(emptyMap<String, Int>(), JsonX.intMap(JsonObject().put("by_plane", 3), "by_plane"))
    }
}
