package com.sekb.ondevice.core

import org.json.JSONArray
import org.json.JSONObject

/**
 * org.json 的小工具集。
 *
 * 为什么不用 kotlinx.serialization：Android 自带 org.json，而这个 App 的 JSON 面很窄
 * （几个固定结构的请求/响应），引一个序列化框架+编译器插件不划算。
 */
object JsonX {

    /** 宽松解析：失败返回 null 而不是抛（网络返回体永远不可信）。 */
    fun parseObject(text: String?): JSONObject? =
        try {
            if (text.isNullOrBlank()) null else JSONObject(text)
        } catch (e: Exception) {
            null
        }

    fun objectOrNull(obj: JSONObject?, key: String): JSONObject? = obj?.optJSONObject(key)

    fun string(obj: JSONObject?, key: String, fallback: String = ""): String =
        obj?.optString(key, fallback) ?: fallback

    fun int(obj: JSONObject?, key: String, fallback: Int = 0): Int =
        obj?.optInt(key, fallback) ?: fallback

    fun double(obj: JSONObject?, key: String, fallback: Double = 0.0): Double =
        obj?.optDouble(key, fallback) ?: fallback

    fun boolean(obj: JSONObject?, key: String, fallback: Boolean = false): Boolean =
        obj?.optBoolean(key, fallback) ?: fallback

    fun stringMap(obj: JSONObject?, key: String): Map<String, String> {
        val node = obj?.optJSONObject(key) ?: return emptyMap()
        val out = LinkedHashMap<String, String>()
        for (k in node.keys()) out[k] = node.optString(k)
        return out
    }

    fun intMap(obj: JSONObject?, key: String): Map<String, Int> {
        val node = obj?.optJSONObject(key) ?: return emptyMap()
        val out = LinkedHashMap<String, Int>()
        for (k in node.keys()) out[k] = node.optInt(k)
        return out
    }

    fun stringList(obj: JSONObject?, key: String): List<String> {
        val node = obj?.optJSONArray(key) ?: return emptyList()
        return (0 until node.length()).map { node.optString(it) }
    }

    fun toJson(map: Map<String, Any?>): JSONObject {
        val obj = JSONObject()
        for ((k, v) in map) obj.put(k, v ?: JSONObject.NULL)
        return obj
    }

    fun stringArray(values: Collection<String>): JSONArray = JSONArray().apply {
        values.forEach { put(it) }
    }
}
