package com.sekb.shared.core

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray as KJsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject as KJsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.longOrNull

/**
 * 端侧唯一的 JSON 门面（`JsonObject` / `JsonArray` / [JsonX]）。
 *
 * **演进历史（值得记住，它解释了为什么长这样）**：
 * 1. 最初有 8 个文件直接 `import org.json.*`（Android 自带）——抽 KMP 时 `commonMain` 编不过；
 * 2. M4 第 2 步把 org.json **收敛到一个文件**（门面 API 与 org.json 的用到的子集同名）；
 * 3. M4 第 5 步建 `shared` 时发现：门面自己还依赖 org.json，仍然进不了 `commonMain` ——
 *    于是底层换成 **kotlinx-serialization-json**（JVM 与 iOS/HarmonyOS 都有产物），**org.json 彻底退出项目**。
 *
 * 因为 API 一直与 org.json 同名，上面 3 次演进**调用点一次都没改过**——这是这一层最大的价值。
 *
 * 两处与 org.json 的有意差异：
 * 1. `parse()` 失败返回 `null` 而不是抛异常（网络响应不可信：半截 JSON、网关 HTML 错误页都很常见）；
 * 2. `optXxx(key)` 需要显式 fallback（Kotlin 没有重载默认值那套），语义等价于 org.json 的 `optXxx(key, fallback)`。
 */
object JsonX {

    /** 宽松解析：失败返回 null 而不是抛（网络返回体永远不可信）。 */
    fun parseObject(text: String?): JsonObject? = JsonObject.parse(text)

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
     * 用固定 DTO 表达不了；这里统一把"键值都是标量"的对象转成 Map。
     */
    fun intMap(obj: JsonObject?, key: String): Map<String, Int> {
        val node = obj?.optJSONObject(key) ?: return emptyMap()
        return node.keys().associateWith { node.optInt(it) }
    }

    // ── 内部：与 kotlinx-serialization 的 JsonElement 互转 ──────────────────

    private val json = Json { isLenient = false }

    /**
     * 把"顺手塞进来的"容器也变成 JSON 树：`List` → [JsonArray]、`Map` → [JsonObject]（递归）。
     *
     * 为什么留着这个便利：调用点经常直接把 `List<String>`（如 signals）或 `Map<String, String>`
     * （如 versions）放进 `put`——如果这里不认，就会写成 `"[a, b]"` 这种字符串，静默产生错协议。
     */
    internal fun normalize(value: Any?): Any? = when (value) {
        is List<*> -> JsonArray().also { arr -> value.forEach { arr.put(normalize(it)) } }
        is Map<*, *> -> JsonObject().also { o -> value.forEach { (k, v) -> o.put(k.toString(), normalize(v)) } }
        is JsonObject, is JsonArray -> value
        else -> value
    }

    /** 我们的树 → JsonElement（序列化时用）。 */
    internal fun toElement(value: Any?): JsonElement = when (value) {
        null -> JsonNull
        is JsonObject -> KJsonObject(value.entries().mapValues { toElement(it.value) })
        is JsonArray -> KJsonArray(value.values().map { toElement(it) })
        is String -> JsonPrimitive(value)
        is Boolean -> JsonPrimitive(value)
        is Int -> JsonPrimitive(value)
        is Long -> JsonPrimitive(value)
        is Double -> JsonPrimitive(value)
        is Float -> JsonPrimitive(value.toDouble())
        else -> JsonPrimitive(value.toString())
    }

    /** JsonElement → 我们的树（解析时用）。标量保留原始形态，读取时再按需强转。 */
    internal fun fromElement(element: JsonElement): Any? = when (element) {
        is JsonNull -> null
        is KJsonObject -> JsonObject.fromMap(element.mapValues { fromElement(it.value) })
        is KJsonArray -> JsonArray.fromList(element.map { fromElement(it) })
        is JsonPrimitive -> when {
            element.isString -> element.content
            element.booleanOrNull != null -> element.booleanOrNull
            element.intOrNull != null -> element.intOrNull
            element.longOrNull != null -> element.longOrNull
            element.doubleOrNull != null -> element.doubleOrNull
            else -> element.content
        }
    }

    internal fun parseElement(text: String?): JsonElement? = try {
        if (text.isNullOrBlank()) null else json.parseToJsonElement(text)
    } catch (e: Exception) {
        null
    }
}

/**
 * 可变 JSON 对象（API 与 `org.json.JSONObject` 的用到的子集同名，见 [JsonX] 的演进说明）。
 *
 * 值的合法类型：[String] / [Boolean] / [Int] / [Long] / [Double] / [JsonObject] / [JsonArray] / null。
 */
class JsonObject internal constructor(private val map: MutableMap<String, Any?>) {

    constructor() : this(LinkedHashMap())

    fun put(key: String, value: Any?): JsonObject {
        map[key] = JsonX.normalize(value)
        return this
    }

    fun optString(key: String, fallback: String = ""): String = when (val v = map[key]) {
        null -> fallback
        is String -> v
        is JsonObject, is JsonArray -> fallback
        else -> v.toString()
    }

    fun optInt(key: String, fallback: Int = 0): Int = when (val v = map[key]) {
        is Int -> v
        is Long -> if (v in Int.MIN_VALUE.toLong()..Int.MAX_VALUE.toLong()) v.toInt() else fallback
        is Double -> v.toInt()
        is String -> v.toIntOrNull() ?: fallback
        is Boolean -> if (v) 1 else 0
        else -> fallback
    }

    fun optDouble(key: String, fallback: Double = 0.0): Double = when (val v = map[key]) {
        is Double -> v
        is Int -> v.toDouble()
        is Long -> v.toDouble()
        is String -> v.toDoubleOrNull() ?: fallback
        else -> fallback
    }

    fun optBoolean(key: String, fallback: Boolean = false): Boolean = when (val v = map[key]) {
        is Boolean -> v
        is String -> v.toBooleanStrictOrNull() ?: fallback
        is Int -> v != 0
        else -> fallback
    }

    fun optJSONObject(key: String): JsonObject? = map[key] as? JsonObject

    fun optJSONArray(key: String): JsonArray? = map[key] as? JsonArray

    /** 严格读法：缺失即抛。只在"缺了就是程序错误"的地方用（协议被改坏要早暴露）。 */
    fun getString(key: String): String {
        if (!map.containsKey(key)) throw NoSuchElementException(key)
        return optString(key)
    }

    fun getInt(key: String): Int {
        if (!map.containsKey(key)) throw NoSuchElementException(key)
        return optInt(key)
    }

    fun getJSONObject(key: String): JsonObject =
        optJSONObject(key) ?: throw NoSuchElementException(key)

    fun getJSONArray(key: String): JsonArray =
        optJSONArray(key) ?: throw NoSuchElementException(key)

    fun has(key: String): Boolean = map.containsKey(key)

    fun keys(): Set<String> = map.keys.toSet()

    fun length(): Int = map.size

    fun isEmpty(): Boolean = map.isEmpty()

    internal fun entries(): Map<String, Any?> = map

    override fun toString(): String = JsonX.toElement(this).toString()

    override fun equals(other: Any?): Boolean = other is JsonObject && map == other.map

    override fun hashCode(): Int = map.hashCode()

    companion object {
        internal fun fromMap(map: Map<String, Any?>): JsonObject = JsonObject(LinkedHashMap(map))

        /** 解析；失败返回 null（不抛）。 */
        fun parse(text: String?): JsonObject? =
            JsonX.parseElement(text)?.let { JsonX.fromElement(it) as? JsonObject }
    }
}

/** 可变 JSON 数组（API 同 `org.json.JSONArray` 的用到的子集）。 */
class JsonArray internal constructor(private val list: MutableList<Any?>) {

    constructor() : this(mutableListOf())

    fun put(value: Any?): JsonArray {
        list.add(JsonX.normalize(value))
        return this
    }

    fun length(): Int = list.size

    fun optJSONObject(index: Int): JsonObject? = list.getOrNull(index) as? JsonObject

    fun optJSONArray(index: Int): JsonArray? = list.getOrNull(index) as? JsonArray

    fun optString(index: Int, fallback: String = ""): String = when (val v = list.getOrNull(index)) {
        null -> fallback
        is String -> v
        is JsonObject, is JsonArray -> fallback
        else -> v.toString()
    }

    fun optInt(index: Int, fallback: Int = 0): Int = when (val v = list.getOrNull(index)) {
        is Int -> v
        is Long -> v.toInt()
        is Double -> v.toInt()
        is String -> v.toIntOrNull() ?: fallback
        else -> fallback
    }

    fun optDouble(index: Int, fallback: Double = 0.0): Double = when (val v = list.getOrNull(index)) {
        is Double -> v
        is Int -> v.toDouble()
        is Long -> v.toDouble()
        is String -> v.toDoubleOrNull() ?: fallback
        else -> fallback
    }

    internal fun values(): List<Any?> = list

    override fun toString(): String = JsonX.toElement(this).toString()

    companion object {
        internal fun fromList(list: List<Any?>): JsonArray = JsonArray(list.toMutableList())

        /** 解析；失败返回 null（不抛）。 */
        fun parse(text: String?): JsonArray? =
            JsonX.parseElement(text)?.let { JsonX.fromElement(it) as? JsonArray }
    }
}
