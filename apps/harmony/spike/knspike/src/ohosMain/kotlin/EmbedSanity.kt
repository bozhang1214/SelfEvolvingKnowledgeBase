// SEKB 鸿蒙侧 · 端侧嵌入验收探针（H6 的"同空间"三项断言）
//
// Android 的对应物是 `SelfTest.kt` 里的 `rag_embed_sanity`（`apps/android/.../SelfTest.kt:248-263`）。
// 这里把同样三件事做成一个可在设备上跑的入口：
//
//   ① **两段语义无关文本的余弦** —— Android 基线 **0.244**（`A4`）。
//      若接近 1.000，说明"所有向量都一样"（输入张量/掩码构造错了的典型症状，Android 踩过）；
//   ② **L2 范数 = 1.0000** —— 证明归一化真的跑了（否则余弦尺度不可比）；
//   ③ **确定性** —— 同一文本两次嵌入**逐位相同**（否则检索结果不可复现）。
//   ④ 附带：**失败时返回同数量零向量**（HR8 第 4 条：返回空数组会让 `ingest` 越界崩溃）
//      用一个必然失败的路径（不存在的模型）验证这一点。
//
// 返回多行报告，脚本 grep `EMBED`：
//   EMBED PASS 4/4
//   PASS embed_sanity_cosine — cos=0.244（Android 基线 0.244）
//   ...
@file:OptIn(kotlin.experimental.ExperimentalNativeApi::class, kotlinx.cinterop.ExperimentalForeignApi::class)

package com.sekb.ohos.spike

import com.sekb.shared.rag.VectorMath
import kotlin.math.abs
import kotlin.math.sqrt

private class EmbedReport {
    val lines = mutableListOf<String>()
    var failed = 0
    var total = 0
    fun check(name: String, ok: Boolean, detail: String) {
        total++
        if (!ok) failed++
        lines += "${if (ok) "PASS" else "FAIL"} ${name} — ${detail}"
    }
    fun render(): String =
        (listOf("EMBED ${if (failed == 0) "PASS" else "FAIL"} ${total - failed}/${total}") + lines)
            .joinToString("\n")
}

/** 四位小数，避免报告里出现 `0.24400000000000002` 这种噪声。 */
private fun f4(v: Double): String {
    val scaled = kotlin.math.round(abs(v) * 10000).toLong()
    val sign = if (v < 0) "-" else ""
    return "$sign${scaled / 10000}.${(scaled % 10000).toString().padStart(4, '0')}"
}

private fun l2(v: FloatArray): Double {
    var s = 0.0
    for (x in v) s += x.toDouble() * x
    return sqrt(s)
}

@CName("sekb_spike_embed_sanity")
fun sekbSpikeEmbedSanity(modelPath: String, vocabText: String): String {
    val r = EmbedReport()

    // ── ④ 先验"失败即降级"契约：给一个不存在的模型路径 ─────────────────────
    // 放在最前面，因为它是**唯一不依赖模型真的能跑**的一条，先拿到它再去跑推理。
    run {
        val broken = MindSporeBgeEmbedding(modelPath + ".does-not-exist", vocabText)
        val texts = listOf("甲", "乙", "丙")
        val vs = try {
            broken.embed(texts)
        } catch (e: Throwable) {
            r.check("embed_failure_returns_zeros", false, "竟然抛了异常：${e::class.simpleName}: ${e.message}")
            return@run
        }
        val allZero = vs.all { v -> v.size == 512 && v.all { it == 0f } }
        r.check(
            "embed_failure_returns_zeros",
            vs.size == texts.size && allZero,
            "条数=${vs.size}（期望 ${texts.size}）全零=${allZero} 维度=${vs.firstOrNull()?.size} lastError=${broken.lastError}",
        )
        broken.close()
    }

    // ── 打开真模型 ────────────────────────────────────────────────────────
    val emb = MindSporeBgeEmbedding(modelPath, vocabText)
    try {
        emb.open()
    } catch (e: Throwable) {
        r.check("embed_open_model", false, "打开模型失败：${e::class.simpleName}: ${e.message}")
        return r.render()
    }
    r.check("embed_open_model", true, emb.diagnostics)

    val textA = "端侧推理为什么省电"
    val textB = "北京今天多云转晴，适合骑行"

    val vA = emb.embedOne(textA)
    val vB = emb.embedOne(textB)
    if (emb.lastError != null) {
        r.check("embed_first_call", false, "推理失败：${emb.lastError}")
        emb.close()
        return r.render()
    }
    r.check("embed_dim", vA.size == 512 && vB.size == 512, "维度=${vA.size}/${vB.size}")

    // ⓪ token 诊断（对齐 Android 的 `rag_token_debug`）：两段文本的 token id 必须不同，
    //    否则"余弦不对"其实是分词器的锅，与推理无关。
    val idsA = emb.debugTokenIds(textA)
    val idsB = emb.debugTokenIds(textB)
    r.check(
        "embed_token_debug",
        idsA.isNotEmpty() && idsA != idsB,
        "A=${idsA.take(8)}(${idsA.size}) B=${idsB.take(8)}(${idsB.size})",
    )

    // ⓪′ **写入读回**：把 additive mask 写进模型缓冲后立刻读回，确认"写的内容 == 期望的内容"。
    //     这一条是给"余弦不对"准备的可观测事实（设备上没有调试器可看缓冲）。
    run {
        val n = 512
        val mask = LongArray(n) { if (it < 11) 1L else 0L }   // 与 textA 的实际 token 数一致
        val expect = FloatArray(n * n)
        for (j in 0 until n) {
            if (mask[j] == 0L) for (i in 0 until n) expect[i * n + j] = Float.NEGATIVE_INFINITY
        }
        try {
            emb.debugWriteMask(expect)
            val back = emb.debugReadMask(expect.size)
            val wantInf = expect.count { it == Float.NEGATIVE_INFINITY }
            val gotInf = back.count { it == Float.NEGATIVE_INFINITY }
            val firstDiff = expect.indices.firstOrNull { expect[it] != back[it] }
            val detail = if (firstDiff == null) {
                "写入 -inf 个数=$wantInf 读回=$gotInf，逐位一致"
            } else {
                "写入 -inf 个数=$wantInf 读回=$gotInf，首处不同 index=$firstDiff " +
                    "expect=${expect[firstDiff]} back=${back[firstDiff]}"
            }
            r.check("embed_mask_write_readback", firstDiff == null && wantInf == gotInf, detail)
        } catch (e: Throwable) {
            r.check("embed_mask_write_readback", false, "读回失败：${e::class.simpleName}: ${e.message}")
        }
    }

    // ① 余弦（Android 基线 0.244）
    val cos = VectorMath.cosine(vA, vB)
    // ⚠️ 判据是"**与 Android 基线同空间**"，不是"小于 1 就行"。
    //    上一版把阈值放成 `< 0.95`，结果 cos=0.8154 也算 PASS —— 那是**假绿**：
    //    0.8154 说明两段无关文本在嵌入空间里高度相似，检索排序虽仍能跑，
    //    但"端侧与云端同空间"这条硬边界已经失效（换端索引不可复用、阈值全部失准）。
    //    实测根因是 ids/token_type_ids 互换了位置（见 MindSporeBgeEmbedding 的说明）。
    r.check(
        "embed_sanity_cosine",
        abs(cos - 0.244) <= 0.02,
        "cos=${f4(cos)}（Android 基线 0.244，容差 ±0.02；明显偏大说明掩码约定或输入角色不对）",
    )

    // ①′ **掩码取值扫描**：默认 `-inf` 在设备上给出 0.8008，而 host 用 `-inf` 是 0.2439
    //     （且读回证明掩码写对了）→ 差异在 MindSpore 对 `-inf` 的处理上。
    //     很多运行时的 attention 融合核要求**有限**掩码值，所以把候选值一次扫完，
    //     让"正确约定"成为设备上的实测事实，而不是我的猜测。
    val candidates = listOf(
        -1.0e9f to "-1e9",
        -65504.0f to "-65504(fp16 最小有限值)",
        -1.0e4f to "-1e4",
        Float.NEGATIVE_INFINITY to "-inf(默认)",
    )
    var best: Pair<Float, Double>? = null
    val scan = StringBuilder()
    for ((fill, label) in candidates) {
        val c = try {
            emb.debugPairCosine(textA, textB, fill)
        } catch (e: Throwable) {
            scan.append("$label=异常(${e::class.simpleName}) ")
            continue
        }
        scan.append("$label=${f4(c)} ")
        if (best == null || abs(c - 0.244) < abs(best!!.second - 0.244)) best = fill to c
    }
    val bestOk = best != null && abs(best!!.second - 0.244) <= 0.02
    r.check(
        "embed_mask_value_scan",
        bestOk,
        "扫描：${scan.toString().trim()}｜最接近 0.244 的是 ${best?.first}（cos=${best?.second?.let { f4(it) }}）",
    )
    if (bestOk) {
        // 让后续调用（含真实检索）用上刚测出来的正确约定
        emb.maskFill = best!!.first
    }

    // ② L2 范数
    val nA = l2(vA)
    r.check("embed_l2_norm", abs(nA - 1.0) < 1e-4, "‖vA‖=${f4(nA)}（期望 1.0000）")

    // ③ 确定性：同一文本两次逐位相同
    val vA2 = emb.embedOne(textA)
    val identical = vA.size == vA2.size && vA.indices.all { vA[it] == vA2[it] }
    r.check("embed_deterministic", identical, if (identical) "两次嵌入逐位相同" else "两次嵌入不同（不可复现）")

    emb.close()
    return r.render()
}
