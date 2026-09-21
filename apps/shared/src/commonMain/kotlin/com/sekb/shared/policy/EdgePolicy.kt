package com.sekb.shared.policy

import com.sekb.shared.core.JsonArray
import com.sekb.shared.core.JsonObject
import com.sekb.shared.core.JsonX
import com.sekb.shared.core.hmacSha256Hex
import com.sekb.shared.route.EdgeRuntimeConfig

/**
 * 端侧策略（L1 热修）的**校验与应用**——服务端 `GET /api/v1/edge/policy` 的客户端对应实现。
 *
 * 规矩（与 `backend/app/core/edge_policy.py` 严格对齐，改一边必须改另一边）：
 *
 * 1. **验签**：HMAC-SHA256 覆盖 `version + issuedAt + expiresAt + embeddingSpace + rollout + policy + deviceProfiles`，
 *    用**规范化 JSON**（键递归排序、无空格）。签名不符 → 整包丢弃、继续用上一份；
 * 2. **空间戳绑定**：`embeddingSpace` 与本地不一致 → 拒绝应用（改阈值必须与空间戳同源，
 *    M3 的教训：换 int8 后阈值要从 0.4 重标到 0.5）；
 * 3. **白名单**：只认 [WHITELIST] 里的键，**多一个未知键 → 整包拒绝**（不是忽略，
 *    否则一次误配置能悄悄改变端侧行为）；[RESERVED] 里的键允许出现但**不读取**；
 * 4. **有效期**：过期即拒绝（用调用方传入的时间，便于单测）；
 * 5. **灰度**：`sha256(deviceId + ":" + salt) % 100 < percent` 才应用（与服务端 `applies_to_device` 同式）。
 *
 * 验签与规范化 JSON 是**跨语言契约**，所以有专门的跨语言向量测试
 * （`EdgePolicyTest`，签名由服务端 Python 实现生成）——两边算不一致，"验签通过"就没有意义。
 */
object EdgePolicy {

    /** 允许下发的可变项（与服务端 `POLICY_KEYS` 一一对应）。 */
    val WHITELIST: Set<String> = setOf(
        "streamGuardChars", "escalateOn", "maxInputTokens", "maxOutputTokens", "maxTtftMs",
        "preferPlane", "modelTier", "promptPacks", "toolWhitelist", "retrievalMinScore",
    )

    /** 已登记但本期不启用：允许出现，**不读取**（"设备适配"留给以后）。 */
    val RESERVED: Set<String> = setOf("deviceProfiles")

    /** 签名覆盖的字段（顺序无关，规范化时会排序）。 */
    private val SIGNED_FIELDS = listOf(
        "version", "issuedAt", "expiresAt", "embeddingSpace", "rollout", "policy", "deviceProfiles",
    )

    // ── 结果类型 ────────────────────────────────────────────────────────────

    sealed interface Outcome {
        /** 应用到本地配置。 */
        data class Applied(
            val config: EdgeRuntimeConfig,
            val version: Int,
            val appliedKeys: List<String>,
            /** 命中了但本地暂不支持的键（如 `promptPacks`）——记下来，别装作应用了。 */
            val ignoredKeys: List<String>,
        ) : Outcome

        /** 拒绝应用（原因可直接进审计日志/自检输出）。 */
        data class Rejected(val reason: String) : Outcome
    }

    // ── 规范化 JSON（必须与 Python 的 json.dumps(sort_keys=True, separators=(",",":"), ensure_ascii=False) 一致）──

    fun canonicalJson(value: Any?): String {
        val sb = StringBuilder()
        write(sb, value)
        return sb.toString()
    }

    private fun write(sb: StringBuilder, value: Any?) {
        when (value) {
            null -> sb.append("null")
            is JsonObject -> {
                sb.append('{')
                var first = true
                for (k in value.keys().sorted()) {
                    if (!first) sb.append(',')
                    first = false
                    appendString(sb, k)
                    sb.append(':')
                    write(sb, value.entries()[k])
                }
                sb.append('}')
            }
            is JsonArray -> {
                sb.append('[')
                for (i in 0 until value.length()) {
                    if (i > 0) sb.append(',')
                    write(sb, value.valueAt(i))
                }
                sb.append(']')
            }
            is String -> appendString(sb, value)
            is Boolean -> sb.append(if (value) "true" else "false")
            is Int -> sb.append(value)
            is Long -> sb.append(value)
            is Double -> sb.append(formatDouble(value))
            is Float -> sb.append(formatDouble(value.toDouble()))
            else -> appendString(sb, value.toString())
        }
    }

    /** Python 的 `json.dumps` 对整数值的浮点写 `600.0`；这里保持一致（避免签名跨语言漂移）。 */
    private fun formatDouble(v: Double): String =
        if (v == v.toLong().toDouble()) "${v.toLong()}.0" else v.toString()

    private fun appendString(sb: StringBuilder, s: String) {
        sb.append('"')
        for (c in s) {
            when (c) {
                '"' -> sb.append("\\\"")
                '\\' -> sb.append("\\\\")
                '\n' -> sb.append("\\n")
                '\r' -> sb.append("\\r")
                '\t' -> sb.append("\\t")
                else -> if (c < ' ') sb.append("\\u%04x".formatChar(c)) else sb.append(c)
            }
        }
        sb.append('"')
    }

    private fun String.formatChar(c: Char): String {
        val hex = "0123456789abcdef"
        val v = c.code
        return buildString(4) {
            append(hex[(v shr 12) and 0xF]); append(hex[(v shr 8) and 0xF])
            append(hex[(v shr 4) and 0xF]); append(hex[v and 0xF])
        }
    }

    /** 取签名覆盖的那部分字段（缺失的字段不参与，与服务端一致）。 */
    fun signedPayload(payload: JsonObject): JsonObject {
        val out = JsonObject()
        for (k in SIGNED_FIELDS) if (payload.has(k)) out.put(k, payload.entries()[k])
        return out
    }

    fun signatureOf(payload: JsonObject, key: String): String =
        hmacSha256Hex(key, canonicalJson(signedPayload(payload)))

    fun verifySignature(payload: JsonObject, key: String): Boolean {
        val sig = payload.optString("signature", "")
        if (sig.isEmpty()) return false
        return constantTimeEquals(sig, signatureOf(payload, key))
    }

    /** 定长比较：不因"第几位不同"而提前返回（虽然这里是本地校验，习惯要正）。 */
    private fun constantTimeEquals(a: String, b: String): Boolean {
        if (a.length != b.length) return false
        var diff = 0
        for (i in a.indices) diff = diff or (a[i].code xor b[i].code)
        return diff == 0
    }

    // ── 校验 + 应用 ────────────────────────────────────────────────────────

    /**
     * 处理一份策略包：验签 → 灰度 → 有效期 → 空间戳 → 白名单 → 合并进 [current]。
     *
     * @param nowMillis 由调用方给时间（单测可注入，避免"测试依赖真实时钟"）
     * @param deviceId 灰度分桶用
     */
    fun apply(
        payloadJson: String,
        key: String,
        current: EdgeRuntimeConfig,
        deviceId: String = "",
        nowMillis: Long = com.sekb.shared.core.nowMillis(),
        checkRollout: Boolean = true,
    ): Outcome {
        val payload = JsonObject.parse(payloadJson)
            ?: return Outcome.Rejected("policy_not_json")

        if (!verifySignature(payload, key)) return Outcome.Rejected("policy_bad_signature")

        val expiresAt = payload.optInt("expiresAt", 0)
        if (expiresAt in 1 until nowMillis) return Outcome.Rejected("policy_expired")

        val space = payload.optString("embeddingSpace", "")
        if (space.isNotEmpty() && space != current.embeddingSpace) {
            // 空间戳不符：阈值/模型档位这些参数与嵌入空间强相关，宁可不应用
            return Outcome.Rejected("policy_space_mismatch:remote=$space,local=${current.embeddingSpace}")
        }

        val policy = payload.optJSONObject("policy") ?: return Outcome.Rejected("policy_missing_body")
        val unknown = policy.keys() - WHITELIST - RESERVED
        if (unknown.isNotEmpty()) {
            return Outcome.Rejected("policy_unknown_key:${unknown.sorted().joinToString(",")}")
        }

        if (checkRollout && !appliesToDevice(payload, deviceId)) {
            return Outcome.Rejected("policy_rollout_skip")
        }

        val applied = mutableListOf<String>()
        val ignored = mutableListOf<String>()
        var updated = current

        if (policy.has("streamGuardChars")) {
            updated = updated.copy(guardChars = policy.optInt("streamGuardChars", current.guardChars))
            applied += "streamGuardChars"
        }
        if (policy.has("escalateOn")) {
            val set = policy.optJSONArray("escalateOn")?.let { arr ->
                (0 until arr.length()).map { arr.optString(it) }.toSet()
            } ?: emptySet()
            updated = updated.copy(escalateOn = set)
            applied += "escalateOn"
        }
        if (policy.has("maxInputTokens")) {
            updated = updated.copy(maxInputTokens = policy.optInt("maxInputTokens", current.maxInputTokens))
            applied += "maxInputTokens"
        }
        if (policy.has("maxOutputTokens")) {
            updated = updated.copy(maxOutputTokens = policy.optInt("maxOutputTokens", current.maxOutputTokens))
            applied += "maxOutputTokens"
        }
        if (policy.has("maxTtftMs")) {
            updated = updated.copy(maxTtftMs = policy.optInt("maxTtftMs", current.maxTtftMs))
            applied += "maxTtftMs"
        }
        if (policy.has("preferPlane")) {
            updated = updated.copy(preferEdge = policy.optString("preferPlane", "edge") != "cloud")
            applied += "preferPlane"
        }
        if (policy.has("modelTier")) {
            val tiers = policy.optJSONObject("modelTier")?.let { o ->
                o.keys().associateWith { o.optString(it) }
            } ?: emptyMap()
            if (tiers.isNotEmpty()) {
                updated = updated.copy(models = tiers)
                applied += "modelTier"
            }
        }
        if (policy.has("toolWhitelist")) {
            val tools = policy.optJSONArray("toolWhitelist")?.let { arr ->
                (0 until arr.length()).map { arr.optString(it) }.toSet()
            } ?: emptySet()
            if (tools.isNotEmpty()) {
                updated = updated.copy(availableTools = tools)
                applied += "toolWhitelist"
            }
        }
        if (policy.has("retrievalMinScore")) {
            // 检索阈值不在 EdgeRuntimeConfig 里（它属于 Retriever 的构造参数），
            // 所以这里只**识别**它，由调用方取用：ignoredKeys 里如实列出，避免"以为生效了"。
            ignored += "retrievalMinScore"
        }
        if (policy.has("promptPacks")) {
            // 提示词包要改真实 LLM 调用点，本期不接（owner 已确认后置）
            ignored += "promptPacks"
        }

        return Outcome.Applied(
            config = updated,
            version = payload.optInt("version", 0),
            appliedKeys = applied.sorted(),
            ignoredKeys = ignored.sorted(),
        )
    }

    /** 灰度：`sha256(deviceId:salt) % 100 < percent`（与服务端 `applies_to_device` 同式）。 */
    fun appliesToDevice(payload: JsonObject, deviceId: String): Boolean {
        val rollout = payload.optJSONObject("rollout") ?: return true
        val percent = rollout.optInt("percent", 100)
        if (percent >= 100) return true
        if (percent <= 0) return false
        val salt = rollout.optString("salt", "")
        val digest = com.sekb.shared.core.sha256Hex("$deviceId:$salt")
        val bucket = digest.take(8).toLongOrNull(16)?.rem(100) ?: return true
        return bucket < percent
    }

    /** 从策略包里取检索阈值（`retrievalMinScore` 的专用读取口，见 [apply] 的说明）。 */
    fun retrievalMinScore(payloadJson: String): Double? {
        val policy = JsonObject.parse(payloadJson)?.optJSONObject("policy") ?: return null
        return if (policy.has("retrievalMinScore")) policy.optDouble("retrievalMinScore", 0.5) else null
    }
}
