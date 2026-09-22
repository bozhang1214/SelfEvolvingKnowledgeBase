// 探针 + 最小真实调用：确认 Kotlin/Native OHOS 工具链预置的 MindSpore Lite 平台 klib 可用。
//
// 两条实测结论（第一次编译就暴露出来的）：
// 1. `package = platform.MindSporeLiteKit.MindSpore`（来自工具链的 `MindSpore.def`）——
//    这是**平台库**，无需在 build.gradle.kts 里写 cinterops，直接 import 即可；
// 2. 但所有 `OH_AI_*` 都要 `@OptIn(ExperimentalForeignApi::class)`（cinterop 生成的 API 默认需要）。
@file:OptIn(kotlin.experimental.ExperimentalNativeApi::class, kotlinx.cinterop.ExperimentalForeignApi::class)

package com.sekb.ohos.spike

import kotlinx.cinterop.alloc
import kotlinx.cinterop.convert
import kotlinx.cinterop.memScoped
import kotlinx.cinterop.ptr
import kotlinx.cinterop.value
import platform.MindSporeLiteKit.MindSpore.OH_AI_ContextCreate
import platform.MindSporeLiteKit.MindSpore.OH_AI_ContextDestroy
import platform.MindSporeLiteKit.MindSpore.OH_AI_ContextSetThreadNum
import platform.MindSporeLiteKit.MindSpore.OH_AI_DeviceInfoCreate
import platform.MindSporeLiteKit.MindSpore.OH_AI_ContextAddDeviceInfo
import platform.MindSporeLiteKit.MindSpore.OH_AI_DEVICETYPE_CPU
import platform.MindSporeLiteKit.MindSpore.OH_AI_TensorHandleArray

/**
 * 造一个 MindSpore Lite context 并立刻销毁。
 *
 * 返回值：0 = 成功拿到 context；-1 = 创建失败。
 * 这里**不碰模型**——本函数的意义是让 `OH_AI_*` 符号真的进入 `libkn.so` 的动态符号表，
 * 从而能在产物上用 `llvm-nm` 验证"运行时由系统 libmindspore_lite_ndk.so 提供"。
 */
@CName("sekb_spike_mindspore_probe")
fun sekbSpikeMindSporeProbe(): Int = memScoped {
    val ctx = OH_AI_ContextCreate() ?: return@memScoped -1
    OH_AI_ContextSetThreadNum(ctx, 2)
    // CPU 设备信息：`OH_AI_DeviceInfoHandle` 由 MindSpore 自己持有，AddDeviceInfo 后归 context 管
    OH_AI_DeviceInfoCreate(OH_AI_DEVICETYPE_CPU)?.let { dev -> OH_AI_ContextAddDeviceInfo(ctx, dev) }
    // Destroy 吃的是"句柄的地址"（`OH_AI_ContextHandle*`），所以要先放进可寻址的存储
    val holder = alloc<kotlinx.cinterop.COpaquePointerVar>()
    holder.value = ctx
    OH_AI_ContextDestroy(holder.ptr)
    0
}

/** 只用来确认 `OH_AI_TensorHandleArray` 这个结构体类型在 Kotlin 侧的映射形态（编译期证据）。 */
@CName("sekb_spike_mindspore_tensor_count")
fun sekbSpikeMindSporeTensorCount(handleNum: Long): Long = memScoped {
    val arr = alloc<OH_AI_TensorHandleArray>()
    arr.handle_num = handleNum.convert()
    arr.handle_list = null
    arr.handle_num.toLong()
}
