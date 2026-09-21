package com.sekb.shared.policy

/**
 * 策略持久化（**双槽**）。各端实现：Android → `SharedPreferences`（与设备凭证同一处）；
 * Apple → 文件 / `NSUserDefaults`。
 *
 * **为什么是双槽而不是单槽**：策略是"远程可改的行为参数"，一旦下发了一份坏策略
 * （阈值离谱、信号全关），单槽设计就只能"等下一份好的"——期间端侧行为是坏的。
 * 双槽的意义是：**新策略应用失败（或应用后用户报告异常）可以立刻回滚到上一份**。
 */
interface PolicyStore {

    /** 当前生效的策略包原文（没有则 null）。 */
    fun loadActive(): String?

    /** 上一份（回滚目标；没有则 null）。 */
    fun loadPrevious(): String?

    /**
     * 写入新策略：**当前 active 先挪到 previous**，再写入新的。
     *
     * 顺序很重要：先覆盖 active 再存 previous 的话，中途失败就两份都丢了。
     */
    fun save(payloadJson: String)

    /** 回滚：previous → active（previous 清空）。返回回滚后的原文，没有可回滚的则 null。 */
    fun rollback(): String?

    fun clear()
}

/** 内存实现：单测与"不落盘"场景用。 */
class InMemoryPolicyStore(
    private var active: String? = null,
    private var previous: String? = null,
) : PolicyStore {

    override fun loadActive(): String? = active
    override fun loadPrevious(): String? = previous

    override fun save(payloadJson: String) {
        previous = active
        active = payloadJson
    }

    override fun rollback(): String? {
        val prev = previous ?: return null
        active = prev
        previous = null
        return prev
    }

    override fun clear() {
        active = null
        previous = null
    }
}
