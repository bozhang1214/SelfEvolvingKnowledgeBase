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

    /** 输入是否都是 int64（不是就说明拿错模型了，早报比"向量不对"好查得多）。 */
    val inputsAreInt64: Boolean = OH_AI_ModelGetInputs(model).useContents {
        (0 until handle_num.toInt()).all { i ->
            handle_list?.get(i)?.let { OH_AI_TensorGetDataType(it) == OH_AI_DATATYPE_NUMBERTYPE_INT64 } ?: false
        }
    }

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
