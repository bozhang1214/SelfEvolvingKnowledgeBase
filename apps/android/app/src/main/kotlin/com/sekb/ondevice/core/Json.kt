package com.sekb.ondevice.core

import org.json.JSONArray as OrgJsonArray
import org.json.JSONObject as OrgJsonObject

/**
 * 端侧唯一的 JSON 门面（`JsonObject` / `JsonArray` 见本文件下半部分）。
 *
 * **为什么要有这一层**：抽 KMP `shared/` 时，`commonMain` 里不能出现 `org.json`（JVM/Android 专有）。
 * 在 M4 第 2 步之前，有 8 个文件直接 `import org.json.*`；现在把 org.json **只关在这一个文件里**，
 * 其余代码只认 `com.sekb.ondevice.core.JsonObject` / `JsonArray`。
 *
 * **将来怎么变成真 KMP**（不改调用点）：
 * - `shared/` 落地时把这两个类改成 `expect class`，各端给 `actual`：
 *   Android/JVM → 仍然用 org.json（已在本机缓存里，零风险）；iOS → `NSJSONSerialization`；
 *   鸿蒙 → ArkTS `JSON`（经 NAPI）或 C 侧解析。
 * - 或者引入 `kotlinx-serialization-json` 写一份 common 实现，把 actual 全部删掉。
 *
 * ⚠️ **本轮为什么没用 kotlinx-serialization-json**：本机 Gradle 缓存里**没有**这个产物
 * （实测只有 `kotlinx-serialization-core`），引它会触发下载；而这一步的目标是"org.json 收敛"，
 * 不是"换 JSON 库"。换库留到 `shared/` 落地时连同 actual 一起评估（见 `docs/多端跨端-KMP方案.md` §1.2）。
 */
object JsonX {

    /** 宽松解析：失败返回 null 而不是抛（网络返回体永远不可信）。 */
    fun parseObject(text: String?): JsonObject? = JsonObject.parse(text)

    /** 宽松解析数组（同样是"不可信输入"口径）。 */
    fun parseArray(text: String?): JsonArray? = JsonArray.parse(text)

    fun objectOrNull(obj: JsonObject?, key: String): JsonObject? = obj?.optJSONObject(key)

    fun string(obj: JsonObject?, key: String, fallback: String = ""): String =
        obj?.optString(key, fallback) ?: fallback

    fun int(obj: JsonObject?, key: String, fallback: Int = 0): Int =
        obj?.optInt(key, fallback) ?: fallback

    fun double(obj: JsonObject?, key: String, fallback: Double = 0.0): Double =
        obj?.optDouble(key, fallback) ?: fallback

    fun bool(obj: JsonObject?, key: String, fallback: Boolean = false): Boolean =
        obj?.optBoolean(key, fallback) ?: fallback

    /**
     * `{"edge": 2, "cloud": 1}` 这类"字符串 → 整数"的小映射。
     *
     * 为什么单独一个方法：路由事件里 `by_plane` 是动态键（平面名由配置决定），
     * 用固定 DTO 表达不了；这里统一把"键值都是标量"的对象转成 Map，避免每个调用点各写一遍。
     */
    fun intMap(obj: JsonObject?, key: String): Map<String, Int> {
        val node = obj?.optJSONObject(key) ?: return emptyMap()
        return node.keys().associateWith { node.optInt(it) }
    }

    /** 把任意值塞进 `put`（调用点不必区分原始类型与嵌套对象）。 */
    internal fun unwrap(value: Any?): Any? = when (value) {
        null -> OrgJsonObject.NULL
        is JsonObject -> value.raw
        is JsonArray -> value.raw
        is List<*> -> OrgJsonArray().also { arr -> value.forEach { arr.put(unwrap(it)) } }
        is Map<*, *> -> OrgJsonObject().also { o -> value.forEach { (k, v) -> o.put(k.toString(), unwrap(v)) } }
        else -> value
    }
}

/**
 * 可变 JSON 对象。API 刻意与 `JsonObject` 的**子集同名**，这样从 org.json 迁过来时
 * 调用点只改 import（`JsonObject()` → `JsonObject()`）。
 *
 * 与 org.json 的两处**语义差异**（都是故意的）：
 * 1. 构造函数不解析字符串——解析走 [parse]（返回 null 而不是抛异常，因为网络响应不可信）；
 * 2. `optXxx(key)` 必须给 fallback（Kotlin 没有重载默认值那套），语义与 org.json 的 `optXxx(key, fallback)` 一致。
 */
class JsonObject internal constructor(internal val raw: OrgJsonObject) {

    constructor() : this(OrgJsonObject())

    fun put(key: String, value: Any?): JsonObject {
        raw.put(key, JsonX.unwrap(value))
        return this
    }

    fun optString(key: String, fallback: String = ""): String = raw.optString(key, fallback)
    fun optInt(key: String, fallback: Int = 0): Int = raw.optInt(key, fallback)
    fun optDouble(key: String, fallback: Double = 0.0): Double = raw.optDouble(key, fallback)
    fun optBoolean(key: String, fallback: Boolean = false): Boolean = raw.optBoolean(key, fallback)
    fun optJSONObject(key: String): JsonObject? = raw.optJSONObject(key)?.let { JsonObject(it) }
    fun optJSONArray(key: String): JsonArray? = raw.optJSONArray(key)?.let { JsonArray(it) }

    /** 严格读法：缺失即抛（与 org.json 一致）。只在"缺了就是程序错误"的地方用。 */
    fun getString(key: String): String = raw.getString(key)
    fun getInt(key: String): Int = raw.getInt(key)
    fun getJSONObject(key: String): JsonObject = JsonObject(raw.getJSONObject(key))
    fun getJSONArray(key: String): JsonArray = JsonArray(raw.getJSONArray(key))

    fun has(key: String): Boolean = raw.has(key)
    fun keys(): Set<String> = raw.keys().asSequence().toSet()
    fun length(): Int = raw.length()
    fun isEmpty(): Boolean = raw.length() == 0

    override fun toString(): String = raw.toString()
    override fun equals(other: Any?): Boolean = other is JsonObject && raw.toString() == other.raw.toString()
    override fun hashCode(): Int = raw.toString().hashCode()

    companion object {
        /** 解析；失败返回 null（不抛）。 */
        fun parse(text: String?): JsonObject? = try {
            if (text.isNullOrBlank()) null else JsonObject(OrgJsonObject(text))
        } catch (e: Exception) {
            null
        }
    }
}

/** 可变 JSON 数组（API 同 `JsonArray` 的用到的子集）。 */
class JsonArray internal constructor(internal val raw: OrgJsonArray) {

    constructor() : this(OrgJsonArray())

    fun put(value: Any?): JsonArray {
        raw.put(JsonX.unwrap(value))
        return this
    }

    fun length(): Int = raw.length()
    fun optJSONObject(index: Int): JsonObject? = raw.optJSONObject(index)?.let { JsonObject(it) }
    fun optJSONArray(index: Int): JsonArray? = raw.optJSONArray(index)?.let { JsonArray(it) }
    fun optString(index: Int, fallback: String = ""): String = raw.optString(index, fallback)
    fun optInt(index: Int, fallback: Int = 0): Int = raw.optInt(index, fallback)
    fun optDouble(index: Int, fallback: Double = 0.0): Double = raw.optDouble(index, fallback)

    override fun toString(): String = raw.toString()

    companion object {
        /** 解析；失败返回 null（不抛）。 */
        fun parse(text: String?): JsonArray? = try {
            if (text.isNullOrBlank()) null else JsonArray(OrgJsonArray(text))
        } catch (e: Exception) {
            null
        }
    }
}
