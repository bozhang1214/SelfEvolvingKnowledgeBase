package com.sekb.shared.policy

import com.sekb.shared.core.JsonObject
import com.sekb.shared.core.nowMillis
import com.sekb.shared.net.HttpTransport
import com.sekb.shared.route.EdgeRuntimeConfig

/** 一次策略刷新的审计记录（落日志/进自检输出；**不含策略正文**，避免日志里堆配置）。 */
data class PolicyAuditEntry(
    val atMillis: Long,
    val action: String,          // fetch_ok / fetch_http_error / rejected / rolled_back / fetch_exception
    val version: Int = 0,
    val reason: String = "",
    val appliedKeys: List<String> = emptyList(),
    val ignoredKeys: List<String> = emptyList(),
)

/** 刷新结果。 */
sealed interface PolicyRefreshResult {
    data class Applied(
        val config: EdgeRuntimeConfig,
        val version: Int,
        val appliedKeys: List<String>,
        val ignoredKeys: List<String>,
    ) : PolicyRefreshResult

    /** 没应用（网络失败/签名不符/灰度跳过…），[config] 保持调用方原来的值。 */
    data class Skipped(val reason: String) : PolicyRefreshResult
}

/**
 * 端侧策略的拉取与应用（L1 热修的"最后一公里"）。
 *
 * 设计取舍：
 * 1. **只做一次 HTTP + 一次决策**，不做重试也不阻塞启动——拉不到就继续用本地那份
 *    （策略是"锦上添花"，不能因为云端抖动让 App 启动变慢或不能聊天）；
 * 2. **失败不改状态**：任何失败（HTTP 错误、签名不符、空间戳不符、灰度跳过）都**保持原配置**，
 *    只写一条审计；下一轮再试；
 * 3. **审计只记元信息**（动作/版本/键名/原因），不记策略正文——日志里不需要重复一份配置；
 * 4. 回滚用的 `previous` 由 [PolicyStore] 维护（见其注释）。
 */
class PolicyFetcher(
    private val transport: HttpTransport,
    private val sekbBaseUrl: String,
    private val signingKey: String,
    private val store: PolicyStore,
    private val now: () -> Long = { nowMillis() },
    private val audit: (PolicyAuditEntry) -> Unit = {},
) {

    /**
     * 拉取并应用策略。
     *
     * @param deviceToken 设备凭证（策略端点按设备鉴权；无凭证时调用方应直接跳过）
     * @param current 当前运行时配置（被拒时原样返回）
     * @param deviceId 灰度分桶用
     */
    fun refresh(
        deviceToken: String,
        current: EdgeRuntimeConfig,
        deviceId: String = "",
    ): PolicyRefreshResult {
        val url = sekbBaseUrl.trimEnd('/') + POLICY_PATH
        val resp = try {
            transport.get(url, headers = mapOf("Authorization" to "Bearer $deviceToken"),
                timeoutSeconds = 15)
        } catch (e: Exception) {
            // 网络异常不是"策略有问题"：保持现状，记一条便于排查
            audit(PolicyAuditEntry(now(), "fetch_exception", reason = e.message?.take(120) ?: "unknown"))
            return PolicyRefreshResult.Skipped("policy_fetch_exception")
        }

        if (!resp.isOk) {
            audit(PolicyAuditEntry(now(), "fetch_http_error", reason = "http_${resp.code}"))
            return PolicyRefreshResult.Skipped("policy_http_${resp.code}")
        }

        val outcome = EdgePolicy.apply(
            payloadJson = resp.body,
            key = signingKey,
            current = current,
            deviceId = deviceId,
            nowMillis = now(),
        )
        return when (outcome) {
            is EdgePolicy.Outcome.Rejected -> {
                audit(PolicyAuditEntry(now(), "rejected", reason = outcome.reason))
                PolicyRefreshResult.Skipped(outcome.reason)
            }
            is EdgePolicy.Outcome.Applied -> {
                store.save(resp.body)          // 双槽：旧的 active 自动成为 previous
                audit(
                    PolicyAuditEntry(
                        atMillis = now(), action = "fetch_ok", version = outcome.version,
                        appliedKeys = outcome.appliedKeys, ignoredKeys = outcome.ignoredKeys,
                    ),
                )
                PolicyRefreshResult.Applied(
                    config = outcome.config, version = outcome.version,
                    appliedKeys = outcome.appliedKeys, ignoredKeys = outcome.ignoredKeys,
                )
            }
        }
    }

    /**
     * 回滚到上一份策略（把 previous 重新应用一次）。
     *
     * 为什么"重新应用"而不是"记住旧 config"：进程重启后内存里的旧 config 就没了，
     * 而 previous 是落盘的——重新走一遍校验才能保证回滚出来的仍然合法（签名/空间戳/白名单）。
     */
    fun rollback(
        current: EdgeRuntimeConfig,
        deviceId: String = "",
    ): PolicyRefreshResult {
        val previous = store.loadPrevious() ?: return PolicyRefreshResult.Skipped("policy_no_previous")
        val outcome = EdgePolicy.apply(previous, signingKey, current, deviceId = deviceId, nowMillis = now())
        return when (outcome) {
            is EdgePolicy.Outcome.Rejected -> {
                audit(PolicyAuditEntry(now(), "rollback_rejected", reason = outcome.reason))
                PolicyRefreshResult.Skipped(outcome.reason)
            }
            is EdgePolicy.Outcome.Applied -> {
                store.rollback()
                audit(
                    PolicyAuditEntry(
                        atMillis = now(), action = "rolled_back", version = outcome.version,
                        appliedKeys = outcome.appliedKeys, ignoredKeys = outcome.ignoredKeys,
                    ),
                )
                PolicyRefreshResult.Applied(
                    config = outcome.config, version = outcome.version,
                    appliedKeys = outcome.appliedKeys, ignoredKeys = outcome.ignoredKeys,
                )
            }
        }
    }

    /** 上次落盘的策略版本（用于自检/界面展示"当前策略是哪一版"）。 */
    fun activeVersion(): Int =
        JsonObject.parse(store.loadActive())?.optInt("version", 0) ?: 0

    /** 上次落盘的策略里携带的检索阈值（`Retriever` 构造参数用；没有则 null → 用本地默认）。 */
    fun activeRetrievalMinScore(): Double? =
        store.loadActive()?.let { EdgePolicy.retrievalMinScore(it) }

    companion object {
        const val POLICY_PATH = "/api/v1/edge/policy"
    }
}
