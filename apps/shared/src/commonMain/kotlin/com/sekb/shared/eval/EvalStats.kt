package com.sekb.shared.eval

import kotlin.math.sqrt

/**
 * 小样本比例的不确定性估计。
 *
 * ## 为什么必须有它（不是"加个数字显得严谨"）
 *
 * §1.4 的工具调用对照实验得到 40/40「合法」，于是写下"约束解码没有可测量收益"。
 * 但那批数据**每条提示词只跑了 1 次**，没有方差——**10/10 全对时，真实合法率的 95% 置信下界
 * 只有约 0.72**。也就是说"100%"这句结论的样本量根本撑不住它自己的语气。
 *
 * 所以再做这个实验时，必须同时给出**区间**而不是只给点估计。
 *
 * ## 为什么用 Wilson 而不是正态近似（Wald）
 *
 * 比例接近 0 或 1 时 Wald 区间会给出荒谬结果（p=1、n=10 时 Wald 的方差为 0，
 * 区间退化成 `[1, 1]`——恰好把"样本不够"这个最重要的事实藏起来）。
 * Wilson 在极端比例下仍然给出一段有意义的区间，正适合这里。
 *
 * ⚠️ **一个必须说明的局限**：把 `提示词 × 重复` 的样本当作独立个体来算区间是**乐观的**——
 * 同一提示词的多次重复彼此相关（聚类），真实区间应当更宽。
 * 所以 [promptStability] 另外给出"按提示词看稳不稳"，用来防止聚合区间掩盖不稳定。
 */
object EvalStats {

    /** Wilson 95% 区间（z = 1.96），返回 `(下界, 上界)`，均已截断到 `[0,1]`。 */
    fun wilson(successes: Int, n: Int, z: Double = 1.96): Pair<Double, Double> {
        if (n <= 0) return 0.0 to 1.0
        val p = successes.toDouble() / n
        val z2 = z * z
        val denom = 1.0 + z2 / n
        val centre = (p + z2 / (2.0 * n)) / denom
        val margin = z * sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n)) / denom
        return (centre - margin).coerceIn(0.0, 1.0) to (centre + margin).coerceIn(0.0, 1.0)
    }

    /** 给报告用的一行：`100.0% (95%CI 72.2%–100.0%, n=10)`。 */
    fun formatRate(successes: Int, n: Int): String {
        if (n <= 0) return "n/a"
        val (lo, hi) = wilson(successes, n)
        val pct = { v: Double -> "%.1f%%".format(v * 100) }
        return "${pct(successes.toDouble() / n)} (95%CI ${pct(lo)}–${pct(hi)}, n=$n)"
    }

    /**
     * 按提示词看的稳定性。
     *
     * 聚合区间会把"10 条都稳"和"有的条 1/5、有的条 5/5"混成一个数——
     * 后者其实说明**模型对某些问法就是不行**，这比整体合法率更有用。
     */
    data class Stability(
        val prompt: String,
        val attempts: Int,
        val legal: Int,
        val repeats: Int,
    ) {
        val rate: Double get() = if (attempts == 0) 0.0 else legal.toDouble() / attempts

        /** 所有重复都一致（全合法或全非法）——即"这条问法的结果稳定"。 */
        val consistent: Boolean get() = legal == 0 || legal == attempts
    }

    /**
     * @param perPrompt 键为提示词；值为**每次重复**的 (attempted, legal)
     */
    fun promptStability(perPrompt: Map<String, List<Pair<Boolean, Boolean>>>, repeats: Int): List<Stability> =
        perPrompt.entries.map { (prompt, runs) ->
            val attempted = runs.count { it.first }
            val legal = runs.count { it.first && it.second }
            Stability(prompt, attempted, legal, repeats)
        }.sortedBy { it.rate }
}
