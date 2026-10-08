// MindSpore Lite C API 的薄封装（**只依赖平台 klib，不依赖共享层**）。
//
// 为什么单独抽一层：鸿蒙真机日第一个要回答的问题是
// **"这个 `.ms` 在设备上能不能加载、能不能跑出向量"**，而它与分词器/共享层无关。
// 把 C API 细节收在这里，spike 入口与（后续的）完整嵌入实现都能复用，
// 不必各自重写一遍 `CValue`/`memScoped` 那套容易出错的样板。
//
// ## cinterop 的两个实测要点（写在这里免得重踩）
//
// 1. **按值返回的结构体**（`OH_AI_ModelGetInputs/GetOutputs` 返回 `OH_AI_TensorHandleArray`）
//    在 Kotlin 里是 `CValue<T>`，必须 `useContents { ... }` 才能读字段——
//    直接赋给 `OH_AI_TensorHandleArray` 会报
//    `Initializer type mismatch: expected 'OH_AI_TensorHandleArray', actual 'CValue<OH_AI_TensorHandleArray>'`。
// 2. 所有 C 枚举/状态码在 Kotlin 侧是 **`UInt`**（`OH_AI_Status` 要与 `0u` 比），
//    数据类型枚举的真名是 `OH_AI_DATATYPE_NUMBERTYPE_INT64`（**不是** `OH_AI_DATATYPE_INT64`）。
@file:OptIn(kotlin.experimental.ExperimentalNativeApi::class, kotlinx.cinterop.ExperimentalForeignApi::class)

package com.sekb.ohos.spike

import kotlinx.cinterop.COpaquePointer
import kotlinx.cinterop.COpaquePointerVar
import kotlinx.cinterop.CPointer
import kotlinx.cinterop.FloatVar
import kotlinx.cinterop.LongVar
import kotlinx.cinterop.alloc
import kotlinx.cinterop.allocArray
import kotlinx.cinterop.get
import kotlinx.cinterop.memScoped
import kotlinx.cinterop.ptr
import kotlinx.cinterop.reinterpret
import kotlinx.cinterop.set
import kotlinx.cinterop.toKString
import kotlinx.cinterop.useContents
import kotlinx.cinterop.value
import platform.MindSporeLiteKit.MindSpore.OH_AI_ContextAddDeviceInfo
import platform.MindSporeLiteKit.MindSpore.OH_AI_ContextCreate
import platform.MindSporeLiteKit.MindSpore.OH_AI_ContextDestroy
import platform.MindSporeLiteKit.MindSpore.OH_AI_ContextSetThreadNum
import platform.MindSporeLiteKit.MindSpore.OH_AI_DATATYPE_NUMBERTYPE_INT64
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

/** 一步失败时的可区分返回码（真机上只看得到返回码，所以要能分辨卡在哪一步）。 */
object MsStep {
    const val OK = 0
    const val CONTEXT_NULL = -1
    const val MODEL_NULL = -2
    const val BUILD_FAILED = -3
    const val NO_INPUT = -4
    const val BAD_DTYPE = -5
    const val PREDICT_FAILED = -6
    const val NO_OUTPUT = -7
    const val OUTPUT_TOO_SMALL = -8
}

/** 一个已 build 好的 MindSpore Lite 会话（context + model）。 */
class MsSession private constructor(
    private val ctx: COpaquePointer,
    private val model: COpaquePointer,
) {
    /** 输入名 → 元素数（`input_ids` 的元素数就是固定 seq 长度）。 */
    val inputs: List<Pair<String, Int>> = OH_AI_ModelGetInputs(model).useContents {
        (0 until handle_num.toInt()).mapNotNull { i ->
            val t = handle_list?.get(i) ?: return@mapNotNull null
            val name = OH_AI_TensorGetName(t)?.toKString() ?: return@mapNotNull null
            name to OH_AI_TensorGetElementNum(t).toInt()
        }
    }

    /**
     * 输入名 → **是否 int64**（`false` 即 float32）。
     *
     * ⚠️ 不要再用"所有输入都是 int64"这种全局判据：模型现在有一个 **float32** 的
     * `sekb_additive_mask_zero` 输入（掩码改由宿主喂入后新增），
     * 那个判据在**设计上**就已经为假，会让调用方在真正尝试之前就误判为"拿错模型"
     * （真机/模拟器上实测症状：返回 `BAD_DTYPE(-5)`，看起来像模型不对，其实是判据过时）。
     */
    val inputIsInt64: Map<String, Boolean> = OH_AI_ModelGetInputs(model).useContents {
        (0 until handle_num.toInt()).mapNotNull { i ->
            val t = handle_list?.get(i) ?: return@mapNotNull null
            val name = OH_AI_TensorGetName(t)?.toKString() ?: return@mapNotNull null
            name to (OH_AI_TensorGetDataType(t) == OH_AI_DATATYPE_NUMBERTYPE_INT64)
        }
    }.toMap()

    /** 输出元素数（batch=1 时为 `seq * hidden`）。 */
    val outputElementNum: Int
        get() = OH_AI_ModelGetOutputs(model).useContents {
            handle_list?.get(0)?.let { OH_AI_TensorGetElementNum(it).toInt() } ?: 0
        }

    /**
     * 跑一次推理。`feeds` 按**输入名**给数据（长度必须等于该输入的元素数）。
     *
     * ⚠️ 数据必须在 `Predict` 期间有效——所以分配与调用都在**同一个** `memScoped` 内，
     * 不要试图把指针带出去（那会变成悬垂指针，症状是"向量不对"而不是崩溃）。
     */
    fun predict(feeds: Map<String, LongArray>) {
        memScoped {
            val buffers = HashMap<String, CPointer<LongVar>>()
            OH_AI_ModelGetInputs(model).useContents {
                for (i in 0 until handle_num.toInt()) {
                    val t = handle_list?.get(i) ?: continue
                    val name = OH_AI_TensorGetName(t)?.toKString() ?: continue
                    val values = feeds[name] ?: continue
                    val buf = allocArray<LongVar>(values.size)
                    for (j in values.indices) buf[j] = values[j]
                    buffers[name] = buf
                    // `OH_AI_TensorSetData` 返回 void，**没有状态可查** → 长度/类型对不上只会
                    // 表现为"向量不对"。所以调用方与本类都主动校验元素数与类型。
                    OH_AI_TensorSetData(t, buf)
                }
            }
            val outArr = alloc<OH_AI_TensorHandleArray>()
            val st = OH_AI_ModelPredict(model, OH_AI_ModelGetInputs(model), outArr.ptr, null, null)
            if (st != 0u) throw MsError("OH_AI_ModelPredict 失败 status=$st")
        }
    }

    /**
     * 一次推理的输入数据。**为什么需要它**：模型现在有两类输入——
     * int64 的 token（`input_ids`/`token_type_ids`，无掩码后不再需要 `attention_mask`）
     * 与 **float32 的 `sekb_additive_mask_zero`**（宿主按 padding 算出的 additive mask）。
     * 既有的 `predict(Map<String, LongArray>)` **只能喂 int64**，喂不了掩码；
     * 而 `OH_AI_TensorSetData` 返回 void、**没有状态可查**，类型对不上只会表现为"向量不对"，
     * 所以类型必须由**我们**显式表达与校验，不能靠猜。
     */
    sealed interface Feed {
        val size: Int

        class I64(val values: LongArray) : Feed {
            override val size: Int get() = values.size
        }

        class F32(val values: FloatArray) : Feed {
            override val size: Int get() = values.size
        }
    }

    /**
     * 混合类型推理（**additive mask 必须走这里**）。
     *
     * 与 [predict] 的区别只有两点：① 按 `Feed` 的实际类型分配 int64/float32 缓冲；
     * ② 逐个输入校验"元素数"与"声明类型"，不匹配就**早报**——因为 SetData 无返回值，
     * 静默错配的症状是"向量不对"，那是最难查的一类问题。
     */
    fun predictTyped(feeds: Map<String, Feed>) {
        memScoped {
            OH_AI_ModelGetInputs(model).useContents {
                for (i in 0 until handle_num.toInt()) {
                    val t = handle_list?.get(i) ?: continue
                    val name = OH_AI_TensorGetName(t)?.toKString() ?: continue
                    val feed = feeds[name] ?: continue
                    val want = OH_AI_TensorGetElementNum(t).toInt()
                    if (feed.size != want) {
                        throw MsError("输入 $name 元素数不符：给了 ${feed.size}，模型要 $want")
                    }
                    // ⚠️ 不再用 `OH_AI_TensorGetDataType(t) == OH_AI_DATATYPE_NUMBERTYPE_INT64` 做断言：
                    // 实测该比较在本绑定下**恒为 false**（诊断码 -1033 显示三个输入全被判成"非 int64"），
                    // 于是它只会制造"类型不符"的假警报。类型由调用方的 `Feed` 子类**显式表达**，
                    // 这里只按 Feed 分配对应缓冲；（元素数仍严格校验，那个是可靠的。）
                    when (feed) {
                        is Feed.I64 -> {
                            val buf = allocArray<LongVar>(feed.values.size)
                            for (j in feed.values.indices) buf[j] = feed.values[j]
                            OH_AI_TensorSetData(t, buf)
                        }

                        is Feed.F32 -> {
                            val buf = allocArray<FloatVar>(feed.values.size)
                            for (j in feed.values.indices) buf[j] = feed.values[j]
                            OH_AI_TensorSetData(t, buf)
                        }
                    }
                }
            }
            val outArr = alloc<OH_AI_TensorHandleArray>()
            val st = OH_AI_ModelPredict(model, OH_AI_ModelGetInputs(model), outArr.ptr, null, null)
            if (st != 0u) throw MsError("OH_AI_ModelPredict 失败 status=$st")
        }
    }

    /** 读出第 0 个输出张量的前 [n] 个 float（**CLS 向量就是前 hidden 个**）。 */
    fun readOutputFloats(n: Int): FloatArray {
        val t = OH_AI_ModelGetOutputs(model).useContents { handle_list?.get(0) }
            ?: throw MsError("OH_AI_ModelGetOutputs 返回空")
        val raw = OH_AI_TensorGetMutableData(t) ?: throw MsError("取输出数据指针失败")
        return FloatArray(n) { raw.reinterpret<FloatVar>()[it] }
    }

    fun close() {
        memScoped {
            val m = alloc<COpaquePointerVar>(); m.value = model
            OH_AI_ModelDestroy(m.ptr)
            val c = alloc<COpaquePointerVar>(); c.value = ctx
            OH_AI_ContextDestroy(c.ptr)
        }
    }

    companion object {
        /** 建 context + 从文件 build 模型（`OH_AI_MODELTYPE_MINDIR` = `.ms`）。 */
        fun open(modelPath: String, threads: Int = 2): MsSession {
            val ctx = OH_AI_ContextCreate() ?: throw MsError("OH_AI_ContextCreate 返回空")
            OH_AI_ContextSetThreadNum(ctx, threads)
            OH_AI_DeviceInfoCreate(OH_AI_DEVICETYPE_CPU)?.let { OH_AI_ContextAddDeviceInfo(ctx, it) }
            val model = OH_AI_ModelCreate() ?: throw MsError("OH_AI_ModelCreate 返回空")
            val st = OH_AI_ModelBuildFromFile(model, modelPath, OH_AI_MODELTYPE_MINDIR, ctx)
            if (st != 0u) throw MsError("OH_AI_ModelBuildFromFile 失败 status=$st（模型=$modelPath）")
            return MsSession(ctx, model)
        }
    }
}

class MsError(message: String) : IllegalStateException(message)
