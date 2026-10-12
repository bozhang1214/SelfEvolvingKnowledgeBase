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

import kotlinx.cinterop.ByteVar
import kotlinx.cinterop.COpaquePointer
import kotlinx.cinterop.COpaquePointerVar
import kotlinx.cinterop.FloatVar
import kotlinx.cinterop.alloc
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
import platform.MindSporeLiteKit.MindSpore.OH_AI_TensorGetDataSize
import platform.MindSporeLiteKit.MindSpore.OH_AI_TensorGetDataType
import platform.MindSporeLiteKit.MindSpore.OH_AI_TensorGetElementNum
import platform.MindSporeLiteKit.MindSpore.OH_AI_TensorGetMutableData
import platform.MindSporeLiteKit.MindSpore.OH_AI_TensorGetName
import platform.MindSporeLiteKit.MindSpore.OH_AI_TensorHandleArray

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
     * 把 int64 数据写进**模型自己的**输入缓冲。
     *
     * ⚠️ 为什么不用 `OH_AI_TensorSetData(t, buf)`（曾经就是这么写的，**会崩**）：
     * 该接口把**我们的**指针交给张量，张量认为内存归它所有，在析构时 free 它——
     * 而我们的缓冲是 `memScoped` 里 `allocArray` 出来的，离开作用域时**已经被 free 过一次**
     * → **double free**。实测崩溃栈（2026-10-08 cppcrash，SIGTRAP + "may double free"）：
     *
     *     OH_AI_ModelDestroy → Graph::~Graph → LiteSession::~LiteSession
     *       → Tensor::~Tensor → Tensor::FreeData → musl 检测到 double free
     *
     * 改为写 `OH_AI_TensorGetMutableData` 返回的**模型自有**缓冲：既不转移所有权，
     * 也没有悬垂指针，调用方不必关心生命周期（元素数已由调用方与本类双重校验）。
     *
     * ⚠️⚠️ **必须按"缓冲实际字节数"决定打包宽度**——这一条是被一次真实缺陷逼出来的（2026-10-26）：
     * 那个 `.ms`（`bge-maskinput.ms`）在 flatbuffer 里把 `input_ids`/`token_type_ids` 标成
     * `dtype=34`，而 OHOS 的 `OH_AI_DATATYPE_NUMBERTYPE_INT32` **正好是 34**（INT64 是 35）——
     * 也就是说**模型声明的就是 int32**，但老代码写死了 `LongArray` 的 8 字节布局，
     * 每次喂 512 个 id 就写 **4096 B**，而张量缓冲只有 **2048 B** → **越界 2048 B**。
     * Linux 形态下这一写直接 `malloc(): invalid size (unsorted)` 崩掉（可复现）；
     * 设备上 musl 不报，于是后半段 id 静默写进隔壁分配块，模型只读到前 256 个 id。
     * 症状极具误导性：向量能算出来、L2 是 1.0、写读回也对，只是**余弦与 Android 差三倍**。
     *
     * 所以这里不再"假设 int64"：由 [OH_AI_TensorGetDataSize] 拿到**真实缓冲字节数**，
     * 与元素数一除得到每元素宽度（4 或 8），再据此打包；打包后**校验字节数正好相等**，
     * 越界写被结构性地排除（C 侧的 `reinterpret<T>()` 没有边界检查，只能自己拦）。
     */
    private fun fillIds(t: COpaquePointer, values: LongArray) {
        val num = OH_AI_TensorGetElementNum(t).toInt()
        if (num <= 0) throw MsError("输入元素数异常 =$num")
        val bytes = OH_AI_TensorGetDataSize(t).toLong()
        val name = OH_AI_TensorGetName(t)?.toKString() ?: "?"
        if (bytes <= 0 || bytes % num != 0L) {
            throw MsError("输入 $name 缓冲字节数=${bytes}、元素数=${num}——无法判定每元素宽度，拒绝写入")
        }
        val stride = (bytes / num).toInt()
        if (stride != 4 && stride != 8) {
            throw MsError(
                "输入 $name 每元素 ${stride} B（元素数=${num}、缓冲=${bytes} B）——" +
                    "既不是 int32 也不是 int64，拒绝写入（宁可真失败，也不要静默算错向量）",
            )
        }
        setInputStride(name, stride)
        writePacked(t, packIds(values, stride), bytes.toInt())
    }

    /** 同 [fillIds]，float32 版本（additive mask 走这条）。 */
    private fun fillF32(t: COpaquePointer, values: FloatArray) {
        val dst = OH_AI_TensorGetMutableData(t) ?: throw MsError("取输入数据指针失败")
        val p = dst.reinterpret<FloatVar>()
        for (j in values.indices) p[j] = values[j]
    }

    /**
     * 把一个整数 id 序列按 [bytesPerElem] 打包成字节数组（**只打包，不写**）。
     *
     * 单独拎出来是为了让"要写多少字节"在写入前就**可校验**：见 [writePacked]。
     */
    private fun packIds(values: LongArray, bytesPerElem: Int): ByteArray {
        val buf = ByteArray(values.size * bytesPerElem)
        for (j in values.indices) {
            val v = values[j]
            if (v < Int.MIN_VALUE || v > Int.MAX_VALUE) {
                throw MsError("token id $v 超出 int32 范围（词表只有 ~21k），拒绝截断")
            }
            val x = v.toInt()
            val base = j * bytesPerElem
            for (b in 0 until bytesPerElem) {
                buf[base + b] = ((x ushr (8 * b)) and 0xFF).toByte()
            }
        }
        return buf
    }

    /**
     * 把打包好的字节写进张量缓冲，并**在写之前**核对"要写的字节数 == 缓冲字节数"。
     *
     * 这条断言就是本次缺陷的守卫：越界写在 C 侧是静默的（没有边界检查），
     * 而它造成的后果（张量只收到一半 id）只会表现为"向量不对"，极难反查。
     */
    private fun writePacked(t: COpaquePointer, bytes: ByteArray, bufferBytes: Int) {
        if (bufferBytes != bytes.size) {
            throw MsError(
                "写入字节数与目标张量缓冲不符：打算写 ${bytes.size} B，缓冲 ${bufferBytes} B" +
                    "（元素数=${OH_AI_TensorGetElementNum(t)}）——拒绝越界写：" +
                    "越界是静默的，只会表现为'向量不对'。",
            )
        }
        val dst = OH_AI_TensorGetMutableData(t) ?: throw MsError("取输入数据指针失败")
        val p = dst.reinterpret<ByteVar>()
        for (j in bytes.indices) p[j] = bytes[j]
    }

    /**
     * 输入缓冲里**每个元素实际占几字节**（4 或 8），由 [fillIds] 按实测缓冲大小登记。
     *
     * 为什么要登记它：[diagnostics] 要把"模型**声明**的 dtype"与"缓冲**实测**的宽度"
     * 分开打印——本次缺陷正是这两者不一致（flatbuffer 声明 int32，而代码当 int64 写）造成的。
     * 打印出来之后，设备日志里就能直接看到"输入布局"，不必再靠猜。
     */
    private val inputStrides = mutableMapOf<String, Int>()

    private fun setInputStride(name: String, bytesPerElem: Int) {
        inputStrides[name] = bytesPerElem
    }

    /** 已实测的 stride（诊断打印用）。 */
    fun strideOf(name: String): Int = inputStrides[name] ?: 0

    /**
     * 写一个整数 id 输入（公开入口，NAPI 测试用）：与 [fillIds] 同一套判据。
     */
    fun writeIds(t: COpaquePointer, values: LongArray) = fillIds(t, values)

    /** 每个输入"声明 dtype + 缓冲字节数 + 实测每元素宽度"的诊断串（自检打印）。 */
    val inputLayout: String
        get() = OH_AI_ModelGetInputs(model).useContents {
            (0 until handle_num.toInt()).mapNotNull { i ->
                val t = handle_list?.get(i) ?: return@mapNotNull null
                val name = OH_AI_TensorGetName(t)?.toKString() ?: return@mapNotNull null
                val num = OH_AI_TensorGetElementNum(t).toInt()
                val b = OH_AI_TensorGetDataSize(t).toLong()
                val s = inputStrides[name] ?: 0
                "$name(元素=$num, 声明dtype=${OH_AI_TensorGetDataType(t)}=0x${
                    OH_AI_TensorGetDataType(t).toString(16)
                }, 缓冲=${b}B, 每元素=${if (s == 0) "未实测" else "${s}B"})"
            }.joinToString(" ")
        }

    /**
     * 跑一次推理。`feeds` 按**输入名**给数据（长度必须等于该输入的元素数）。
     *
     * 数据写进模型自有缓冲（见 [fillIds]），所以**不需要**在 `Predict` 期间持有任何指针。
     */
    fun predict(feeds: Map<String, LongArray>) {
        memScoped {
            OH_AI_ModelGetInputs(model).useContents {
                for (i in 0 until handle_num.toInt()) {
                    val t = handle_list?.get(i) ?: continue
                    val name = OH_AI_TensorGetName(t)?.toKString() ?: continue
                    val values = feeds[name] ?: continue
                    // `OH_AI_TensorSetData` 返回 void，**没有状态可查** → 长度/类型对不上只会
                    // 表现为"向量不对"。所以调用方与本类都主动校验元素数与**缓冲字节数**。
                    fillIds(t, values)
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
        // ⚠️ 这里**不能**用 `feeds[name] ?: continue` 静默跳过（本函数早前就是那么写的）。
        // 后果是实测出来的：名字一旦对不上，那个输入**根本不会被写**，而模型照样能跑出一个
        // "看起来正常"的向量——症状是"余弦与 Android 差三倍"（设备 0.8154 vs Android 0.244、
        // 而 host 上用同一张图 + 同一套 id + 同一个掩码约定算出来是 0.2439），
        // 极难从现象反推回"某个输入没喂进去"。
        // 所以：**给的必须都被吃掉、模型要的必须都被喂到**，任何一边不齐都当场报错并列出名字。
        val seen = mutableSetOf<String>()
        memScoped {
            OH_AI_ModelGetInputs(model).useContents {
                for (i in 0 until handle_num.toInt()) {
                    val t = handle_list?.get(i) ?: continue
                    val name = OH_AI_TensorGetName(t)?.toKString() ?: continue
                    val feed = feeds[name]
                    if (feed == null) {
                        throw MsError(
                            "输入 $name 没有喂数据（本次喂的是 ${feeds.keys}）。" +
                                "静默跳过会让推理算出'看起来正常、其实是错的'向量，所以这里直接失败。",
                        )
                    }
                    seen += name
                    val want = OH_AI_TensorGetElementNum(t).toInt()
                    if (feed.size != want) {
                        throw MsError("输入 $name 元素数不符：给了 ${feed.size}，模型要 $want")
                    }
                    // ⚠️ **不要**用 `OH_AI_TensorGetDataType(t)` 判断 token 输入该按几字节写。
                    // 两次实测都说明它不可信：
                    //   · 早前：`== OH_AI_DATATYPE_NUMBERTYPE_INT64` **恒为 false**
                    //     （诊断码 -1033 显示三个输入全被判成"非 int64"）→ 只会制造假警报；
                    //   · 2026-10-26 `bge-maskinput.ms`：对三个输入**全返回 34**，
                    //     而 34 是 `OH_AI_DATATYPE_NUMBERTYPE_INT32`（掩码本该是 FLOAT32=43）。
                    // 可靠判据只有一个：`OH_AI_TensorGetDataSize` —— 由"缓冲字节数 / 元素数"
                    // 得到真实宽度（见 [fillIds]）。这正是本缺陷的教训：**声明不可信，缓冲才可信**。
                    when (feed) {
                        is Feed.I64 -> fillIds(t, feed.values)
                        is Feed.F32 -> fillF32(t, feed.values)
                    }
                }
            }
            val unknown = feeds.keys - seen
            if (unknown.isNotEmpty()) {
                throw MsError("喂了模型不认识的输入：$unknown（模型输入=$seen）")
            }
            val outArr = alloc<OH_AI_TensorHandleArray>()
            val st = OH_AI_ModelPredict(model, OH_AI_ModelGetInputs(model), outArr.ptr, null, null)
            if (st != 0u) throw MsError("OH_AI_ModelPredict 失败 status=$st")
        }
    }

    /** **诊断**：把 float32 写进指定输入（不跑推理），配合 [readInputF32] 做写入读回。 */
    fun writeInputF32(name: String, values: FloatArray) {
        var target: COpaquePointer? = null
        OH_AI_ModelGetInputs(model).useContents {
            for (i in 0 until handle_num.toInt()) {
                val t = handle_list?.get(i) ?: continue
                if (OH_AI_TensorGetName(t)?.toKString() == name) {
                    target = t
                    break
                }
            }
        }
        val t = target ?: throw MsError("没有名为 $name 的输入")
        val want = OH_AI_TensorGetElementNum(t).toInt()
        if (values.size != want) throw MsError("$name 元素数不符：给了 ${values.size}，模型要 $want")
        fillF32(t, values)
    }

    /**
     * **诊断**：读回模型自有输入缓冲里的前 [n] 个 float32（在 `predict` **之前**调用）。
     *
     * 为什么需要它：写输入缓冲**没有任何状态可查**（`OH_AI_TensorSetData` 返回 void），
     * 所以"我到底有没有把 additive mask 写进去"在设备上原本没有判据。
     * 而 host 侧实验已证明"同一张图 + 同一套 token id + 正确的 additive mask → 余弦 0.2439"，
     * 设备上却是 0.8154 —— 那就必须把"写了什么"变成**可观测事实**，而不是继续猜。
     */
    fun readInputF32(name: String, n: Int): FloatArray {
        var target: COpaquePointer? = null
        OH_AI_ModelGetInputs(model).useContents {
            for (i in 0 until handle_num.toInt()) {
                val t = handle_list?.get(i) ?: continue
                if (OH_AI_TensorGetName(t)?.toKString() == name) {
                    target = t
                    break
                }
            }
        }
        val t = target ?: throw MsError("没有名为 $name 的输入")
        val raw = OH_AI_TensorGetMutableData(t) ?: throw MsError("取 $name 数据指针失败")
        return FloatArray(n) { raw.reinterpret<FloatVar>()[it] }
    }

    /**
     * **诊断**：`(输出张量个数, 第 0 个输出的元素数)`；拿不到第 0 个时元素数记 -1。
     *
     * 为什么要它：`outputElementNum` 返回 0 时，无法区分"一个输出都没有"与"输出存在但元素数为 0"——
     * 前者像图/转换问题，后者像 shape 推断问题，**排查方向完全不同**。
     * 作为**成员函数**（不是 companion）以免插错位置截断类体（上次就是这么把编译搞挂的）。
     */
    fun outputProbe(): Pair<Int, Int> = OH_AI_ModelGetOutputs(model).useContents {
        val n = handle_num.toInt()
        val e = if (n > 0) {
            handle_list?.get(0)?.let { OH_AI_TensorGetElementNum(it).toInt() } ?: -1
        } else {
            -1
        }
        n to e
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

/**
 * **进程内单例**的 MindSpore 会话。
 *
 * ## 为什么必须有它（不是"顺手做的优化"）
 *
 * 同一进程里反复 `OH_AI_ModelBuildFromFile` + `OH_AI_ModelDestroy`（94MB 模型）时，
 * 实测在模拟器上崩过**两条不同的栈**：
 * 1. predict 路径：`MallocTensorData → SubGraphKernel::MallocSubgraphInputs → LiteOpActor::InitInputData`
 *    （`SIGSEGV@0x30`，Tid `OS_Actor_1`）；
 * 2. destroy 路径：`OH_AI_ModelDestroy → LiteModel::Destroy → LiteModel::Free`（`SIGSEGV@0x8`）。
 * 两者都只发生在"第二次及以后"的 build/destroy 周期里——第一次总是好的。
 *
 * 而端侧真实形态本来就该**只建一个会话**（换文本不换模型），所以这不是绕开问题，
 * 而是把用法收敛到应该有的样子。副作用：进程退出前不释放模型内存（对端侧 App 是正常代价）。
 */
object MsSessionCache {
    private var cached: MsSession? = null
    private var cachedPath: String? = null

    // ⚠️ **不要**在这里写 `@Synchronized`：那是 **JVM 专有**注解，Kotlin/Native 上
    // `Unresolved reference 'Synchronized'`（本轮实测的编译错误）。
    // 不加锁是**有依据的取舍**：所有 NAPI 入口都由 ArkTS 的 UI 线程调用，
    // 而探针在 `Index.ets` 里已改成**串行**执行（并发跑模型会崩，见 aboutToAppear 的说明），
    // 所以这里不存在真正的并发。若将来有后台线程调用，要换成 Kotlin/Native 的
    // `kotlin.native.concurrent` 原语（或 platform.posix 的 pthread mutex），**不能**用 @Synchronized。
    fun get(modelPath: String): MsSession {
        val c = cached
        if (c != null && cachedPath == modelPath) return c
        val s = MsSession.open(modelPath)
        cached = s
        cachedPath = modelPath
        return s
    }

    fun clear() {
        cached = null
        cachedPath = null
    }
}

class MsError(message: String) : IllegalStateException(message)
