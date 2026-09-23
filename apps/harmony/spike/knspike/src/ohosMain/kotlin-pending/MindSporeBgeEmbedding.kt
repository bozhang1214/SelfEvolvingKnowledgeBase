// 鸿蒙端侧**真·**嵌入：MindSpore Lite + bge-small-zh（512 维，CLS pooling + L2 归一化）。
//
// ## 为什么是 MindSpore Lite 而不是 ONNX Runtime
//
// 原方案是"用 OHOS NDK 自建 ONNX Runtime"，实测撞死在两处：ORT 源码在 github.com（本机不可达）、
// 社区镜像只有移植补丁没有预编译产物。复核工具链后发现**方向本身错了**：
// Kotlin/Native 的 OHOS 工具链**已预置** HarmonyOS 的 MindSpore Lite 平台绑定
// （`konan/platformDef/ohos_*/MindSpore.def`：`package = platform.MindSporeLiteKit.MindSpore`、
// `linkerOpts = -lmindspore_lite_ndk`），它是**系统能力**，真实现由系统镜像提供 ——
// 比往 HAP 里塞几十 MB 的 ORT `.so` 省得多。
//
// ## 三端语义对齐（与 Android `OnnxBgeEmbedding` / iOS `OrtBgeEmbedding` 逐点一致）
//
// - 同一套分词器（共享层 `BertWordPieceTokenizer`，**不在这边重写分词规则**）；
// - 同样的 CLS pooling（取 `[0][0][:]`）与同样的 L2 归一化；
// - 同一套空间戳（`BAAI/bge-small-zh-v1.5@512`）。
//
// ## 与 iOS 侧的一个重要差异（对我方有利）
//
// iOS 的 `EmbeddingProvider.embed` 在 Kotlin 里**没有 `throws`**，Swift 抛不出异常 → 只能
// "返回零向量 + 记 lastError"。**Kotlin 侧可以直接抛**（与 Android 实现一致），
// 所以这里用 `IllegalStateException`，不做那种妥协。
@file:OptIn(kotlin.experimental.ExperimentalNativeApi::class, kotlinx.cinterop.ExperimentalForeignApi::class)

package com.sekb.ohos.spike

import com.sekb.shared.embed.BertWordPieceTokenizer
import com.sekb.shared.embed.EmbeddingProvider
import com.sekb.shared.embed.EmbeddingSpace
import kotlinx.cinterop.COpaquePointerVar
import kotlinx.cinterop.CPointer
import kotlinx.cinterop.ExperimentalForeignApi
import kotlinx.cinterop.LongVar
import kotlinx.cinterop.alloc
import kotlinx.cinterop.allocArray
import kotlinx.cinterop.get
import kotlinx.cinterop.memScoped
import kotlinx.cinterop.ptr
import kotlinx.cinterop.reinterpret
import kotlinx.cinterop.set
import kotlinx.cinterop.useContents
import kotlinx.cinterop.value
import platform.MindSporeLiteKit.MindSpore.OH_AI_ContextAddDeviceInfo
import platform.MindSporeLiteKit.MindSpore.OH_AI_ContextCreate
import platform.MindSporeLiteKit.MindSpore.OH_AI_ContextDestroy
import platform.MindSporeLiteKit.MindSpore.OH_AI_ContextSetThreadNum
import platform.MindSporeLiteKit.MindSpore.OH_AI_DATATYPE_INT64
import platform.MindSporeLiteKit.MindSpore.OH_AI_DEVICETYPE_CPU
import platform.MindSporeLiteKit.MindSpore.OH_AI_DeviceInfoCreate
import platform.MindSporeLiteKit.MindSpore.OH_AI_MODELTYPE_MINDIR
import platform.MindSporeLiteKit.MindSpore.OH_AI_ModelBuildFromFile
import platform.MindSporeLiteKit.MindSpore.OH_AI_ModelCreate
import platform.MindSporeLiteKit.MindSpore.OH_AI_ModelDestroy
import platform.MindSporeLiteKit.MindSpore.OH_AI_ModelGetInputs
import platform.MindSporeLiteKit.MindSpore.OH_AI_ModelGetOutputs
import platform.MindSporeLiteKit.MindSpore.OH_AI_ModelPredict
import platform.MindSporeLiteKit.MindSpore.OH_AI_TensorGetDataType
import platform.MindSporeLiteKit.MindSpore.OH_AI_TensorGetElementNum
import platform.MindSporeLiteKit.MindSpore.OH_AI_TensorGetMutableData
import platform.MindSporeLiteKit.MindSpore.OH_AI_TensorGetName
import platform.MindSporeLiteKit.MindSpore.OH_AI_TensorHandleArray
import platform.MindSporeLiteKit.MindSpore.OH_AI_TensorSetData
import kotlin.math.sqrt

/**
 * @param modelPath `.ms` 模型路径（MindSpore Lite **只吃 MINDIR/`.ms`**，不吃 ONNX——
 *        所以 ONNX→`.ms` 的转换是必要前置，见 `scripts/model_to_ms.sh`）
 * @param vocabText `vocab.txt` 的内容（**传文本而不是路径**：读文件是平台的事，分词规则是共享层的事）
 * @param spaceId 空间戳。量化模型必须换戳（见 [INT8_SPACE_ID]）
 */
class MindSporeBgeEmbedding(
    private val modelPath: String,
    vocabText: String,
    override val space: EmbeddingSpace = EmbeddingSpace(EmbeddingSpace.SERVER_SPACE_ID, 512),
    maxLength: Int = 512,
) : EmbeddingProvider, AutoCloseable {

    override val isOnDevice: Boolean = true

    private val tokenizer = BertWordPieceTokenizer(
        BertWordPieceTokenizer.loadVocab(vocabText), maxLength, 100,
    )

    private val ctx = OH_AI_ContextCreate()
    private val model = OH_AI_ModelCreate()

    /** 模型要求的输入宽度（由输入张量元素数推出，**不硬编码 512**）。 */
    private val seqLen: Int

    /** 诊断串（自检里打印，避免"模型在哪/什么形状"靠猜）。 */
    val diagnostics: String

    init {
        if (ctx == null) throw IllegalStateException("OH_AI_ContextCreate 返回空")
        if (model == null) throw IllegalStateException("OH_AI_ModelCreate 返回空")
        OH_AI_ContextSetThreadNum(ctx, 2)
        // 端侧单次短推理：线程数与 Android/iOS 一致（2），避免和 UI 抢核
        OH_AI_DeviceInfoCreate(OH_AI_DEVICETYPE_CPU)?.let { OH_AI_ContextAddDeviceInfo(ctx, it) }

        val st = OH_AI_ModelBuildFromFile(model, modelPath, OH_AI_MODELTYPE_MINDIR, ctx)
        if (st != 0u) {
            throw IllegalStateException("OH_AI_ModelBuildFromFile 失败，status=$st（模型=$modelPath）")
        }

        val shapes = mutableListOf<String>()
        var width = 0
        var wantLong = true
        OH_AI_ModelGetInputs(model).useContents {
            for (i in 0 until handle_num.toInt()) {
                val t = handle_list?.get(i) ?: continue
                val name = OH_AI_TensorGetName(t)?.let { kotlinx.cinterop.toKString(it) } ?: "?"
                val elem = OH_AI_TensorGetElementNum(t).toInt()
                if (OH_AI_TensorGetDataType(t) != OH_AI_DATATYPE_INT64) wantLong = false
                if (name == "input_ids") width = elem
                shapes += "$name=$elem"
            }
        }
        if (width <= 0) throw IllegalStateException("拿不到 input_ids 的元素数（输入=${shapes.joinToString()}）")
        if (!wantLong) throw IllegalStateException("输入张量不是 INT64（期望 input_ids/attention_mask/token_type_ids 均为 int64）")
        seqLen = width
        diagnostics = "模型=${modelPath.substringAfterLast('/')} 输入=[${shapes.joinToString()}] seqLen=$seqLen"
    }

    override fun embed(texts: List<String>): List<FloatArray> {
        if (texts.isEmpty()) return emptyList()
        // ⚠️ `.ms` 是**固定形状**（batch=1, seq=512），所以批量只能逐条推理。
        // 这是"静态化换转换成功"的代价，已在 apps/harmony/README.md 里写明。
        return texts.map { embedOneText(it) }
    }

    private fun embedOneText(text: String): FloatArray {
        // 单条编码 → 补齐/截断到模型要求的 seqLen（共享层 `encode` 已按 maxLength 截断）
        val ids = tokenizer.encode(text)
        val row = LongArray(seqLen)
        val mask = LongArray(seqLen)
        for (i in 0 until minOf(ids.size, seqLen)) {
            row[i] = ids[i].toLong()
            mask[i] = 1L
        }

        memScoped {
            // 数据必须在 `Predict` 期间一直有效 → 在本 memScoped 内分配并填充
            val idsArr = allocArray<LongVar>(seqLen)
            val maskArr = allocArray<LongVar>(seqLen)
            val typeArr = allocArray<LongVar>(seqLen)   // token_type_ids 恒为 0（sentence-transformers 模板）
            for (i in 0 until seqLen) {
                idsArr[i] = row[i]
                maskArr[i] = mask[i]
                typeArr[i] = 0L
            }

            OH_AI_ModelGetInputs(model).useContents {
                for (i in 0 until handle_num.toInt()) {
                    val t = handle_list?.get(i) ?: continue
                    val name = OH_AI_TensorGetName(t)?.let { kotlinx.cinterop.toKString(it) } ?: continue
                    val src: CPointer<LongVar> = when (name) {
                        "input_ids" -> idsArr
                        "attention_mask" -> maskArr
                        "token_type_ids" -> typeArr
                        // 与 Android 一样：模型出现未预期输入就明确报错，不静默忽略
                        else -> throw IllegalStateException("模型出现了未预期的输入：$name")
                    }
                    // 注意：`OH_AI_TensorSetData` 返回 void，**没有状态可查**；
                    // 若形状对不上，症状会是"向量不对"而不是报错 —— 所以上面才要主动校验数据类型。
                    OH_AI_TensorSetData(t, src)
                }
            }

            val outArr = alloc<OH_AI_TensorHandleArray>()
            val st = OH_AI_ModelPredict(model, OH_AI_ModelGetInputs(model), outArr.ptr, null, null)
            if (st != 0u) throw IllegalStateException("OH_AI_ModelPredict 失败，status=$st")
            // `outArr` 只是 `Predict` 的出参容器；随后统一走 `OH_AI_ModelGetOutputs`，
            // 所以这里不保留它（保留反而会诱使人在 memScoped 之外用它 → 悬垂指针）
            @Suppress("UNUSED_EXPRESSION") outArr
        }

        // 输出形状 [batch=1, seq, 512] → CLS pooling = 取 batch 0 的第 0 个 token
        val data = OH_AI_ModelGetOutputs(model).useContents { handle_list?.get(0) }
            ?: throw IllegalStateException("OH_AI_ModelGetOutputs 返回空")
        val raw = OH_AI_TensorGetMutableData(data) ?: throw IllegalStateException("取输出数据指针失败")
        val elemNum = OH_AI_TensorGetElementNum(data).toInt()
        if (elemNum < space.dim) {
            throw IllegalStateException("输出元素数 $elemNum < 空间维度 ${space.dim}")
        }
        val floats = raw.reinterpret<kotlinx.cinterop.FloatVar>()
        val v = FloatArray(space.dim) { floats[it] }   // CLS = 前 dim 个浮点
        return l2Normalize(v)
    }

    override fun close() {
        memScoped {
            val m = alloc<COpaquePointerVar>(); m.value = model
            OH_AI_ModelDestroy(m.ptr)
            val c = alloc<COpaquePointerVar>(); c.value = ctx
            OH_AI_ContextDestroy(c.ptr)
        }
    }

    private fun l2Normalize(v: FloatArray): FloatArray {
        var sum = 0.0
        for (x in v) sum += x.toDouble() * x
        val norm = sqrt(sum)
        if (norm > 0) for (i in v.indices) v[i] = (v[i] / norm).toFloat()
        return v
    }

    companion object {
        /** int8 量化模型的空间戳（与 Android/iOS 同值）。量化向量与 fp32 余弦只有 ~0.96–0.97，不能混用 */
        const val INT8_SPACE_ID = "BAAI/bge-small-zh-v1.5-int8@512"
    }
}
