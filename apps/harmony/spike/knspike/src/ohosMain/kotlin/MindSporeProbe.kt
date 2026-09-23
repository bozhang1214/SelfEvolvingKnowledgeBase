// 鸿蒙 spike 的 MindSpore Lite 入口：**真机日第一个要跑的东西**。
//
// 它回答的唯一问题是："这个 `.ms` 在设备上能不能加载、能不能真的跑出向量？"
// 刻意**不依赖共享层/分词器**——那两样接进来需要先给 `ohosMain` 补 `actual` 端口
// （`Clock`/`Hmac`/`Digests` 的 ohos 实现），是另一件事；而"模型跑不跑得起来"是
// 整条路线的前提，先独立验掉，风险最小。
//
// 返回值设计（真机上只看得到返回码，所以必须能分辨卡在哪一步）：
//   > 0 = 成功，值是**输出张量元素数**（batch=1、seq=512、hidden=512 → 期望 262144）
//   < 0 = 失败，见 MsStep 的常量
@file:OptIn(kotlin.experimental.ExperimentalNativeApi::class, kotlinx.cinterop.ExperimentalForeignApi::class)

package com.sekb.ohos.spike

/**
 * 加载 `.ms`、跑一次推理、校验输出。
 *
 * 输入用**合成 token id**（`[CLS] 若干 [SEP] 0…`），因为本函数不接分词器。
 * 它证明的是运行时链路（build → feed → predict → 读回浮点），不是语义。
 */
@CName("sekb_spike_mindspore_selftest")
fun sekbSpikeMindSporeSelfTest(modelPath: String): Int {
    var session: MsSession? = null
    return try {
        session = try {
            MsSession.open(modelPath)
        } catch (e: MsError) {
            return MsStep.BUILD_FAILED
        }
        val s = session!!
        if (s.inputs.isEmpty()) return MsStep.NO_INPUT
        if (!s.inputsAreInt64) return MsStep.BAD_DTYPE

        val seqLen = s.inputs.firstOrNull { it.first == "input_ids" }?.second ?: return MsStep.NO_INPUT
        val elemNum = s.outputElementNum
        if (elemNum <= 0) return MsStep.NO_OUTPUT
        val hidden = elemNum / seqLen
        if (hidden <= 0) return MsStep.OUTPUT_TOO_SMALL

        // 合成输入：开头 [CLS]=101，结尾 [SEP]=102，中间用可复现的伪 id，其余补 0
        val ids = LongArray(seqLen)
        val mask = LongArray(seqLen)
        val types = LongArray(seqLen)
        if (seqLen >= 2) {
            ids[0] = 101L; mask[0] = 1L
            ids[seqLen - 1] = 102L; mask[seqLen - 1] = 1L
            val body = minOf(seqLen - 2, 8)
            for (i in 1..body) {
                ids[i] = (1000 + i * 37).toLong()   // 固定、可复现
                mask[i] = 1L
            }
        } else {
            for (i in 0 until seqLen) { ids[i] = 101L; mask[i] = 1L }
        }
        s.predict(mapOf("input_ids" to ids, "attention_mask" to mask, "token_type_ids" to types))

        val v = s.readOutputFloats(hidden)
        if (v.any { it.isNaN() || it.isInfinite() }) return MsStep.PREDICT_FAILED
        elemNum
    } finally {
        session?.close()
    }
}

/**
 * 输出向量首元素的粗量化值（`|v[0]| * 1e6`）。
 *
 * 为什么单独要一个：`selftest` 返回元素数只能证明"形状对"，**证明不了数据真的流过来了**
 * （全零也能通过）。这个值非零才算推理真的算出了东西；同时它还是**可复现**的
 * （输入固定），可以拿去和宿主机对照。
 */
@CName("sekb_spike_mindspore_first_float")
fun sekbSpikeMindSporeFirstFloat(modelPath: String): Int {
    var session: MsSession? = null
    return try {
        session = MsSession.open(modelPath)
        val s = session
        val seqLen = s.inputs.firstOrNull { it.first == "input_ids" }?.second ?: return MsStep.NO_INPUT
        val ids = LongArray(seqLen)
        val mask = LongArray(seqLen)
        val types = LongArray(seqLen)
        if (seqLen >= 2) {
            ids[0] = 101L; mask[0] = 1L
            ids[seqLen - 1] = 102L; mask[seqLen - 1] = 1L
            for (i in 1..minOf(seqLen - 2, 8)) { ids[i] = (1000 + i * 37).toLong(); mask[i] = 1L }
        } else {
            for (i in 0 until seqLen) { ids[i] = 101L; mask[i] = 1L }
        }
        s.predict(mapOf("input_ids" to ids, "attention_mask" to mask, "token_type_ids" to types))
        val v0 = s.readOutputFloats(1)[0]
        if (v0.isNaN() || v0.isInfinite()) return MsStep.PREDICT_FAILED
        (kotlin.math.abs(v0) * 1e6).toInt()
    } finally {
        session?.close()
    }
}
