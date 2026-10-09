// 鸿蒙端侧**真·**嵌入：MindSpore Lite + bge-small-zh（512 维，CLS pooling + L2 归一化）。
//
// ## 这份是**重写**，不是搬运 `kotlin-pending/` 那一份
//
// `kotlin-pending/MindSporeBgeEmbedding.kt` 的输入口径**已过期**：它按名字喂
// `input_ids`/`attention_mask`/`token_type_ids`，而当前可运行的 `.ms`
// （`bge-maskinput.ms`）里 **`attention_mask` 已被裁掉**——它在图内的唯一作用就是展开成
// additive mask，而那段动态链正是"0 CPU 挂起"的根因（见 `scripts/model_to_ms.sh` 头部）。
// 现在模型吃的是 **`sekb_additive_mask_zero`（float32，形状 [1,1,seq,seq]）**，由**宿主**算好喂入。
//
// 所以本文件的关键差异（三处，都不是措辞问题）：
//   1. 输入集合 = {2 个 int64 token 输入, 1 个 float32 additive mask}；
//   2. 角色识别**不看名字、不看 dtype**，只看**元素数**（掩码 512×512=262144 vs token 512，
//      相差 512 倍，毫无歧义）。依据：`.ms` 里存的是 ONNX 原名，但运行时暴露的可能是转换器
//      内部的 `Parameter_N`；而 `OH_AI_TensorGetDataType(...) == ..._INT64` 在本绑定下**恒为 false**
//      （实测诊断码 -1033 = 3 输入 / 0 个 int64 / 3 个"非 int64"）——两条判据都不可靠；
//   3. **失败返回同数量零向量**，不抛异常。口径见下。
//
// ## 失败时的契约（与 Android/iOS 一致，别改成抛异常）
//
// `EmbeddingProvider.embed` 的契约是"**失败即降级**"：返回与 `texts` **同数量**的向量。
// 理由不是"懒得报错"，而是 `KnowledgeIndex.ingest` 是按**切片数索引**嵌入结果的
// （`vectors[i]` 对应 `chunks[i]`）——抛异常或返回短数组都会打断那条既有不变量，
// iOS 侧就踩过 `EXC_BREAKPOINT`。所以：
//   · 推理失败 → 该条返回零向量，并把原因记在 [lastError]（自检里打出来）；
//   · **绝不返回空数组**：空数组会让 `ingest` 越界崩溃（HR8 第 4 条）。
@file:OptIn(kotlin.experimental.ExperimentalNativeApi::class, kotlinx.cinterop.ExperimentalForeignApi::class)

package com.sekb.ohos.spike

import com.sekb.shared.embed.BertWordPieceTokenizer
import com.sekb.shared.embed.EmbeddingProvider
import com.sekb.shared.embed.EmbeddingSpace
import kotlin.math.sqrt

/**
 * @param modelPath `.ms` 模型路径（MindSpore Lite 只吃 MINDIR/`.ms`）
 * @param vocabText `vocab.txt` 的**内容**（传文本而不是路径：读文件是平台的事，分词是共享层的事）
 * @param spaceId 空间戳。量化模型必须换戳（`-int8@512`），量化向量与 fp32 余弦只有 0.96–0.97，不能混用
 */
class MindSporeBgeEmbedding(
    private val modelPath: String,
    vocabText: String,
    spaceId: String = EmbeddingSpace.SERVER_SPACE_ID,
    private val maxLength: Int = 512,
) : EmbeddingProvider, AutoCloseable {

    override val space: EmbeddingSpace = EmbeddingSpace(spaceId, 512)
    override val isOnDevice: Boolean = true

    private val tokenizer = BertWordPieceTokenizer(
        BertWordPieceTokenizer.loadVocab(vocabText), maxLength, 100,
    )

    /** 最近一次失败原因（null = 最近一次成功）。自检把它打出来，避免"零向量但不知道为什么" */
    var lastError: String? = null
        private set

    private var session: MsSession? = null

    /** 打开会话（走进程内单例，见 [MsSessionCache]）；失败则整个对象不可用。 */
    fun open() {
        session = MsSessionCache.get(modelPath)
    }

    /**
     * 只解除本对象对会话的引用，**不销毁共享会话**。
     *
     * 理由见 [MsSessionCache]：反复 build/destroy 会崩；端侧本来就只该有一个会话。
     * 所以 `close()` 在这里的语义是"我不再用它了"，而不是"把模型卸掉"。
     */
    override fun close() {
        session = null
    }

    /** 角色识别依据（"按名字"还是"按声明顺序"）——自检里打印，避免这一层静默出错。 */
    var roleSource: String? = null
        private set

    /** 诊断串（自检打印：模型名、输入元素数、角色识别依据）——"模型在哪/怎么分辨输入"不靠猜。 */
    val diagnostics: String
        get() {
            val s = session ?: return "未打开"
            return "模型=${modelPath.substringAfterLast('/')} 输入=${s.inputs} 角色识别=${roleSource ?: "尚未推理"}"
        }

    /** 诊断用：把一段文本的 token id 暴露出来（"向量不对"时先分清是分词还是推理的锅）。 */
    fun debugTokenIds(text: String): List<Int> = tokenizer.encode(text)

    private fun maskName(): String =
        session?.inputs?.firstOrNull { it.second == MASK_ELEMENTS }?.first
            ?: throw IllegalStateException("找不到 additive mask 输入：${session?.inputs}")

    /** 诊断用：把掩码写进模型缓冲（**不跑推理**）。 */
    fun debugWriteMask(values: FloatArray) {
        val s = session ?: throw IllegalStateException("会话未打开")
        // 只喂掩码是喂不全的，所以直接写目标张量；这里复用 `predictTyped` 的写路径。
        val dst = s.writeInputF32(maskName(), values)
        @Suppress("UNUSED_EXPRESSION") dst
    }

    /** 诊断用：读回模型缓冲里的掩码（`debugWriteMask` 之后调用）。 */
    fun debugReadMask(n: Int): FloatArray {
        val s = session ?: throw IllegalStateException("会话未打开")
        return s.readInputF32(maskName(), n)
    }

    /** 模型要求的 token 宽度（由 token 输入的元素数推出，**不硬编码 512**）。 */
    private val seqLen: Int
        get() = session?.inputs?.firstOrNull { it.second != MASK_ELEMENTS }?.second ?: 512

    override fun embed(texts: List<String>): List<FloatArray> {
        if (texts.isEmpty()) return emptyList()
        val s = session
        if (s == null) {
            // 契约：同数量零向量（不是空数组、不是抛）
            lastError = "会话未打开（先 open()）"
            return List(texts.size) { FloatArray(space.dim) }
        }
        return texts.map { t ->
            try {
                embedOneText(s, t).also { lastError = null }
            } catch (e: Throwable) {
                // ⚠️ 这里**必须捕获**：单条失败不该让整批失败（同数量零向量是契约）
                lastError = "${e::class.simpleName}: ${e.message}"
                FloatArray(space.dim)
            }
        }
    }

    private fun embedOneText(s: MsSession, text: String, fill: Float = maskFill): FloatArray {
        val ids = tokenizer.encode(text)
        val n = seqLen
        val row = LongArray(n)
        val mask = LongArray(n)
        val types = LongArray(n)              // token_type_ids 恒为 0（sentence-transformers 模板）
        for (i in 0 until minOf(ids.size, n)) {
            row[i] = ids[i].toLong()
            mask[i] = 1L
        }

        // ── 角色识别：**先按名字**，名字不可辨认时退回元素数/声明顺序 ────────────────
        // 实测发现（本轮最重要的一处）：**光靠元素数区分 token 输入是不够的**——
        // 掩码能靠 262144 vs 512 唯一确定，但 `input_ids` 与 `token_type_ids` 元素数**完全一样**，
        // 只能靠"声明顺序"。而声明顺序并不稳定：同一份 `.ms` 两次运行里顺序不同，
        // 于是 ids 与 types 互换了位置 → 嵌入值全错。**症状极具误导性**：
        // 余弦 0.8154（看起来"能用"），而 Android 基线是 0.244 —— 差了三倍多，
        // 检索仍能排序、所以不会崩，只会让"同空间"这条硬边界静默失效。
        // 好在诊断码证明运行时**暴露的是真实 ONNX 名字**（不是 `Parameter_N`）：
        //   `输入=[(input_ids, 512), (token_type_ids, 512), (sekb_additive_mask_zero, 262144)]`
        // 所以按名字取（与 Android `OnnxBgeEmbedding` 的做法一致），名字辨认不出时才退回顺序。
        val maskEntry = s.inputs.firstOrNull { it.second == MASK_ELEMENTS }
            ?: throw IllegalStateException("没有找到元素数为 $MASK_ELEMENTS 的 additive mask 输入：${s.inputs}")
        val tokenEntries = s.inputs.filter { it.second != MASK_ELEMENTS }
        if (tokenEntries.size != 2) {
            throw IllegalStateException("token 输入应为 2 个、实为 ${tokenEntries.size}（全部输入=${s.inputs}）")
        }
        val byName = tokenEntries.firstOrNull { it.first.contains("type", ignoreCase = true) }
        val typesName: String
        val idsName: String
        if (byName != null) {
            typesName = byName.first
            idsName = tokenEntries.first { it.first != typesName }.first
            if (roleSource == null) roleSource = "按名字（可靠）"
        } else {
            // 名字被转换器改写成 `Parameter_N` 时的退路：按声明顺序（与 ONNX 一致）
            idsName = tokenEntries[0].first
            typesName = tokenEntries[1].first
            if (roleSource == null) roleSource = "按声明顺序（名字不可辨认，**顺序不稳定时会有风险**）"
        }

        s.predictTyped(
            mapOf(
                idsName to MsSession.Feed.I64(row),
                typesName to MsSession.Feed.I64(types),
                maskEntry.first to MsSession.Feed.F32(additiveMask(mask, fill)),
            ),
        )

        val elemNum = s.outputElementNum
        if (elemNum <= 0) throw IllegalStateException("predict 后输出元素数=$elemNum（NO_OUTPUT）")
        val hidden = elemNum / n
        if (hidden < space.dim) {
            throw IllegalStateException("每次 token 的隐藏维 $hidden < 空间维度 ${space.dim}（elemNum=$elemNum seqLen=$n）")
        }
        // 输出 [1, seq, hidden] → **CLS pooling = 取第 0 个 token 的 hidden 维**
        val v = s.readOutputFloats(space.dim)
        return l2Normalize(v)
    }

    /**
     * additive mask：可见位 `0`、被掩位 `-inf`，形状 `[1,1,seq,seq]`（对 heads 维广播）。
     *
     * ⚠️ **只按 key 掩**（列）：若把 query 行也掩成全 -inf，softmax 会出现 nan；
     * 原模型就是只掩 key（实测四层产生的张量完全相同，故单个输入即可）。
     */
    private fun additiveMask(mask: LongArray, fill: Float = maskFill): FloatArray {
        val n = mask.size
        val out = FloatArray(n * n)           // 零初始化 = 全部可见
        for (j in 0 until n) {
            if (mask[j] != 0L) continue
            for (i in 0 until n) out[i * n + j] = fill
        }
        return out
    }

    /**
     * 被掩位的填充值（默认 `-inf`）。
     *
     * ⚠️ **这个值是可调的，而它不是实现细节**：host 侧（onnxruntime）用 `-inf` 算出的余弦
     * 是 0.2439（= Android 基线），但**同一张图转成 `.ms` 后在设备上算出 0.8008**，
     * 且已用"写入读回"证明掩码内容逐位正确（256512 个 `-inf`）。
     * 也就是说差异出在 **MindSpore Lite 对 `-inf` 的处理/融合**上，而不是我的喂数逻辑。
     * 很多推理运行时的 attention 融合核要求**有限**的掩码值（常见约定 `-1e9` / `-65504`），
     * 因为 `-inf` 会在 `max()` 归约与 `exp(x - max)` 里产生 `inf - inf = nan` 而被内核降级处理。
     * 所以这里留一个可调值，并用设备上的**取值扫描**（见 `EmbedSanity`）把正确约定测出来——
     * 而不是继续猜。
     */
    var maskFill: Float = Float.NEGATIVE_INFINITY

    /** 诊断：用指定掩码填充值算两条文本的余弦（找出 MindSpore 接受的约定）。 */
    fun debugPairCosine(textA: String, textB: String, fill: Float): Double {
        val s = session ?: throw IllegalStateException("会话未打开")
        val a = embedOneText(s, textA, fill)
        val b = embedOneText(s, textB, fill)
        return com.sekb.shared.rag.VectorMath.cosine(a, b)
    }

    private fun l2Normalize(v: FloatArray): FloatArray {
        var sum = 0.0
        for (x in v) sum += x.toDouble() * x
        val norm = sqrt(sum)
        if (norm > 0.0) for (i in v.indices) v[i] = (v[i] / norm).toFloat()
        return v
    }

    companion object {
        /** additive mask 的元素数（seq=512 → 512×512）。用于按元素数识别角色 */
        const val MASK_ELEMENTS = 512 * 512
    }
}
