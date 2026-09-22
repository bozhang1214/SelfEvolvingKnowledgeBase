import Foundation
import SharedCore

/// ORT 调用失败。
///
/// **为什么要有这个类型**：ORT 的 C API 全靠返回 `OrtStatus*` 报错，忽略它就会得到
/// "静默返回空向量"——那比崩溃更难查（Android 侧踩过同类坑：非直接缓冲导致
/// "任何输入都产出同一个向量"却不报错）。这里把每个 status 都过一遍 [check]。
struct OrtFailure: Error, CustomStringConvertible {
    let message: String
    var description: String { "ORT 调用失败：\(message)" }
}

/// iOS 端侧**真·**嵌入：ONNX 版 `bge-small-zh-v1.5`（512 维，CLS pooling + L2 归一化）。
///
/// 与 Android 的 `OnnxBgeEmbedding` **逐点对齐**，目的是同一份模型在两端产出同空间向量：
/// - 同一套分词器（`BertWordPieceTokenizer`，共享层代码，不是这边再实现一遍）；
/// - 同一批输入名（`input_ids` / `attention_mask` / `token_type_ids`，`token_type_ids` 恒 0）；
/// - 同样的 pooling（取 `[CLS]`，即 `[batch][0][:]`）与同样的 L2 归一化；
/// - 同一套空间戳（fp32 = `BAAI/bge-small-zh-v1.5@512`，int8 另用一个戳）。
///
/// **与 Android 的唯一实现差异**：Android 走 ORT 的 **Java** API（`ai.onnxruntime`），
/// iOS 只有 **C** API——所以这边多了一层 `OrtApi` 函数表调用 + 手工内存管理。
/// 语义上没有任何放宽。
final class OrtBgeEmbedding: NSObject, EmbeddingProvider {

    // MARK: - EmbeddingProvider

    let space: EmbeddingSpace
    let isOnDevice: Bool = true

    /// 最近一次失败的原因。
    ///
    /// ⚠️ 共享层的 `EmbeddingProvider.embed` **不是 throwing 的**（Kotlin 接口没有 throws，
    /// 导出到 Swift 后也无法 throws）。直接吞掉异常会得到"看起来正常但全空"的检索结果，
    /// 所以这里显式留一个诊断位：自检会断言它为 nil；失败时同时返回空结果，让上层的
    /// "切片数 = 嵌入数"校验也对不上，两处都能暴露。
    private(set) var lastError: String?

    // MARK: - ORT 状态（构造后不变）

    private let api: UnsafePointer<OrtApi>
    private let env: OpaquePointer
    private let session: OpaquePointer
    private let allocator: UnsafeMutablePointer<OrtAllocator>
    private let tokenizer: BertWordPieceTokenizer
    private let maxLength: Int32

    /// 输入名缓存（第一次读 session 后填上）。
    private var cachedInputNames: [String]?

    // MARK: - 构造

    /// - Parameters:
    ///   - modelDir: 含 `model.onnx` 与 `vocab.txt` 的目录
    ///   - spaceId: 空间戳。**量化模型必须用不同的戳**（见 `INT8_SPACE_ID`）——向量不同就不能混用（RFC §18.1）
    init(modelDir: URL, spaceId: String = EmbeddingSpace.companion.SERVER_SPACE_ID, maxLength: Int32 = 512) throws {
        guard let apiBase = OrtGetApiBase() else {
            throw OrtFailure(message: "OrtGetApiBase() 返回空（库没链进来？）")
        }
        guard let api = apiBase.pointee.GetApi(UInt32(ORT_API_VERSION)) else {
            throw OrtFailure(message: "GetApi(\(ORT_API_VERSION)) 返回空——头文件与库版本不匹配")
        }
        self.api = api
        self.space = EmbeddingSpace(id: spaceId, dim: 512)
        self.maxLength = maxLength

        // 1) 分词器（共享层实现）；此处只负责"读文件"这件事——那是平台端口该干的
        let vocabURL = modelDir.appendingPathComponent(OrtBgeEmbedding.VOCAB_FILE)
        let modelURL = modelDir.appendingPathComponent(OrtBgeEmbedding.MODEL_FILE)
        guard let vocabText = try? String(contentsOf: vocabURL, encoding: .utf8) else {
            throw OrtFailure(message: "读不到词表：\(vocabURL.path)")
        }
        guard FileManager.default.fileExists(atPath: modelURL.path) else {
            throw OrtFailure(message: "读不到模型：\(modelURL.path)")
        }
        self.tokenizer = BertWordPieceTokenizer(
            vocab: BertWordPieceTokenizer.companion.loadVocab(vocabText: vocabText),
            maxLength: maxLength,
            maxCharsPerWord: 100,
        )

        // 2) ORT：环境 → 会话选项 → 会话 → 默认分配器
        var env: OpaquePointer?
        try OrtBgeEmbedding.checkRaw(api, api.pointee.CreateEnv(ORT_LOGGING_LEVEL_WARNING, "sekb-ios", &env))
        guard let env else { throw OrtFailure(message: "CreateEnv 返回空") }

        var opts: OpaquePointer?
        try OrtBgeEmbedding.checkRaw(api, api.pointee.CreateSessionOptions(&opts))
        guard let opts else {
            api.pointee.ReleaseEnv(env)
            throw OrtFailure(message: "CreateSessionOptions 返回空")
        }
        defer { api.pointee.ReleaseSessionOptions(opts) }
        // 端侧是"单次短推理"，线程数与 Android 一致（2），避免和 UI 抢核
        try OrtBgeEmbedding.checkRaw(api, api.pointee.SetIntraOpNumThreads(opts, 2))

        var session: OpaquePointer?
        try OrtBgeEmbedding.checkRaw(api, api.pointee.CreateSession(env, modelURL.path, opts, &session))
        guard let session else {
            api.pointee.ReleaseEnv(env)
            throw OrtFailure(message: "CreateSession 返回空")
        }

        var allocator: UnsafeMutablePointer<OrtAllocator>?
        try OrtBgeEmbedding.checkRaw(api, api.pointee.GetAllocatorWithDefaultOptions(&allocator))
        guard let allocator else {
            api.pointee.ReleaseSession(session)
            api.pointee.ReleaseEnv(env)
            throw OrtFailure(message: "GetAllocatorWithDefaultOptions 返回空")
        }

        self.env = env
        self.session = session
        self.allocator = allocator
    }

    deinit {
        api.pointee.ReleaseSession(session)
        api.pointee.ReleaseEnv(env)
        // 默认分配器归 env 所有，**不要**释放（ORT 文档明确）
    }

    // MARK: - 输入 / 输出名

    private func inputNames() throws -> [String] {
        if let cachedInputNames { return cachedInputNames }
        var count = 0
        try check(api.pointee.SessionGetInputCount(session, &count))
        var names: [String] = []
        for i in 0..<count {
            names.append(try nameOfSession(at: i, output: false))
        }
        cachedInputNames = names
        return names
    }

    private func outputName() throws -> String {
        return try nameOfSession(at: 0, output: true)
    }

    private func nameOfSession(at index: Int, output: Bool) throws -> String {
        var cName: UnsafeMutablePointer<CChar>?
        if output {
            try check(api.pointee.SessionGetOutputName(session, index, allocator, &cName))
        } else {
            try check(api.pointee.SessionGetInputName(session, index, allocator, &cName))
        }
        guard let cName else { throw OrtFailure(message: "SessionGet\(output ? "Output" : "Input")Name(\(index)) 返回空") }
        // ⚠️ 顺序**不能反**：先前一版先 `AllocatorFree` 再 `String(cString:)`，读到的是已释放内存 →
        // 拿回空串，于是报"ONNX 模型出现了未预期的输入：（空）"，向量全零、查半天才发现是 use-after-free。
        let name = String(cString: cName)
        try check(api.pointee.AllocatorFree(allocator, cName))
        return name
    }

    // MARK: - 推理

    func embed(texts: [String]) -> [KotlinFloatArray] {
        do {
            lastError = nil
            return try embedChecked(texts: texts)
        } catch {
            lastError = "\(error)"
            // 必须**立刻**打日志：自检的汇总是在最后一次性打印的，中途崩溃就什么都看不到
            NSLog("SEKB_IOS_SELFTEST embed_onnx_fail — \(error)")
            // 失败时返回**同数量的零向量**，绝不能返回空数组或短数组：
            // 共享层 `KnowledgeIndex.ingest` 会按切片数索引嵌入结果，返回短列表会让 Kotlin 侧
            // 下标越界并**当场崩溃**（实测：EXC_BREAKPOINT in Kotlin_NSArrayAsKList_get，
            // 栈顶是 __SwiftNativeNSArrayWithContiguousStorage._objectAt）。
            // 零向量与任何向量的余弦都是 0 → 检索必然不命中，属于"安全失败"。
            return texts.map { _ in KotlinFloatArray(size: Int32(space.dim)) }
        }
    }

    /// 单条便捷方法。
    ///
    /// Kotlin 那边 `embedOne` 有默认实现，但**默认实现在 ObjC/Swift 侧不算可实现**
    /// （导出的协议把两个方法都标成 `@required`），所以必须显式写一份。
    ///
    /// 失败时返回**零向量**而不是崩溃：零向量与任何向量的余弦都是 0 → 检索必然不命中，
    /// 属于"安全失败"；同时 [lastError] 被置上、自检会断言它为空，两处都能暴露问题。
    func embedOne(text: String) -> KotlinFloatArray {
        if let v = embed(texts: [text]).first { return v }
        return KotlinFloatArray(size: Int32(space.dim))
    }

    private func embedChecked(texts: [String]) throws -> [KotlinFloatArray] {
        if texts.isEmpty { return [] }
        let names = try inputNames()

        // 分词：**复用共享层的 encodeBatch**（padding 规则只该有一份，不在这边重写）
        let encoded = tokenizer.encodeBatch(texts: texts)
        let idsArr = encoded.first!
        let maskArr = encoded.second!
        let batch = Int(idsArr.size)
        guard batch == texts.count else {
            throw OrtFailure(message: "encodeBatch 行数 \(batch) ≠ 输入条数 \(texts.count)")
        }
        let width = batch > 0 ? Int(idsArr.get(index: 0)!.size) : 0
        guard width > 0 else { throw OrtFailure(message: "encodeBatch 宽度为 0") }
        guard width <= Int(maxLength) else {
            throw OrtFailure(message: "序列长度 \(width) 超过 maxLength \(maxLength)")
        }

        let shape: [Int64] = [Int64(batch), Int64(width)]
        var flatIds = [Int64](repeating: 0, count: batch * width)
        var flatMask = [Int64](repeating: 0, count: batch * width)
        // `token_type_ids` 恒为 0（sentence-transformers 的模板如此），与 Android 一致
        let flatTypes = [Int64](repeating: 0, count: batch * width)
        for i in 0..<batch {
            let rowIds = idsArr.get(index: Int32(i))!
            let rowMask = maskArr.get(index: Int32(i))!
            for j in 0..<width {
                flatIds[i * width + j] = rowIds.get(index: Int32(j))
                flatMask[i * width + j] = rowMask.get(index: Int32(j))
            }
        }

        var values: [OpaquePointer?] = []
        var namePtrs: [UnsafeMutablePointer<CChar>] = []
        defer {
            for v in values { if let v { api.pointee.ReleaseValue(v) } }
            for p in namePtrs { free(p) }
        }
        for name in names {
            let data: [Int64]
            switch name {
            case "input_ids": data = flatIds
            case "attention_mask": data = flatMask
            case "token_type_ids": data = flatTypes
            default:
                throw OrtFailure(message: "ONNX 模型出现了未预期的输入：\(name)")
            }
            values.append(try makeInt64Tensor(data, shape: shape))
            guard let dup = strdup(name) else { throw OrtFailure(message: "strdup 失败") }
            namePtrs.append(dup)
        }

        let outName = try outputName()
        guard let outDup = strdup(outName) else { throw OrtFailure(message: "strdup(输出名) 失败") }
        defer { free(outDup) }

        var outputs: [OpaquePointer?] = [nil]
        let inputPtrs: [UnsafePointer<CChar>?] = namePtrs.map { UnsafePointer($0) }
        let outPtrs: [UnsafePointer<CChar>?] = [UnsafePointer(outDup)]
        let inputVals: [OpaquePointer?] = values
        try inputPtrs.withUnsafeBufferPointer { ip in
            try outPtrs.withUnsafeBufferPointer { op in
                try inputVals.withUnsafeBufferPointer { iv in
                    try check(api.pointee.Run(
                        session, nil,
                        ip.baseAddress, iv.baseAddress, inputVals.count,
                        op.baseAddress, 1,
                        &outputs,
                    ))
                }
            }
        }

        guard let out = outputs[0] else { throw OrtFailure(message: "Run 返回空输出") }
        defer { api.pointee.ReleaseValue(out) }

        // 形状 [batch, seq, hidden]：CLS pooling = 取 batch 0 的第 0 个 token 的 hidden 向量
        let dims = try dimensions(of: out)
        guard dims.count == 3 else {
            throw OrtFailure(message: "输出维度数 \(dims.count) ≠ 3（形状 \(dims)）")
        }
        let seq = Int(dims[1])
        let hidden = Int(dims[2])
        guard hidden == space.dim else { throw OrtFailure(message: "隐藏维 \(hidden) ≠ 空间维度 \(space.dim)") }

        var raw: UnsafeMutableRawPointer?
        try check(api.pointee.GetTensorMutableData(out, &raw))
        guard let raw else { throw OrtFailure(message: "GetTensorMutableData 返回空") }
        let floats = raw.assumingMemoryBound(to: Float.self)

        var result: [KotlinFloatArray] = []
        result.reserveCapacity(batch)
        for b in 0..<batch {
            let base = b * seq * hidden
            let arr = KotlinFloatArray(size: Int32(hidden))
            var sum = 0.0
            for i in 0..<hidden { sum += Double(floats[base + i]) * Double(floats[base + i]) }
            let norm = sum.squareRoot()
            for i in 0..<hidden {
                let x = Float(floats[base + i])
                arr.set(index: Int32(i), value: norm > 0 ? Float(Double(x) / norm) : x)
            }
            result.append(arr)
        }
        return result
    }

    // MARK: - ORT 小工具

    private func dimensions(of value: OpaquePointer) throws -> [Int64] {
        var info: OpaquePointer?
        try check(api.pointee.GetTensorTypeAndShape(value, &info))
        guard let info else { throw OrtFailure(message: "GetTensorTypeAndShape 返回空") }
        defer { api.pointee.ReleaseTensorTypeAndShapeInfo(info) }
        var n = 0
        try check(api.pointee.GetDimensionsCount(info, &n))
        var dims = [Int64](repeating: 0, count: n)
        try check(api.pointee.GetDimensions(info, &dims, n))
        return dims
    }

    /// 建 int64 张量。
    ///
    /// ⚠️ 数据必须拷进 **ORT 自己的缓冲**（`CreateTensorAsOrtValue` + `GetTensorMutableData`），
    /// 而不是用 `CreateTensorWithDataAsOrtValue` 指向 Swift 数组——后者要求调用方保证
    /// 内存在 `Run` 期间有效，而 Swift 数组的存储不保证不被移动，症状同样是"向量不对"。
    private func makeInt64Tensor(_ data: [Int64], shape: [Int64]) throws -> OpaquePointer {
        var value: OpaquePointer?
        var shapeArr = shape
        try check(api.pointee.CreateTensorAsOrtValue(
            allocator, &shapeArr, shape.count, ONNX_TENSOR_ELEMENT_DATA_TYPE_INT64, &value,
        ))
        guard let value else { throw OrtFailure(message: "CreateTensorAsOrtValue 返回空") }
        var raw: UnsafeMutableRawPointer?
        try check(api.pointee.GetTensorMutableData(value, &raw))
        guard let raw else {
            api.pointee.ReleaseValue(value)
            throw OrtFailure(message: "GetTensorMutableData(输入) 返回空")
        }
        raw.assumingMemoryBound(to: Int64.self).update(from: data, count: data.count)
        return value
    }

    private func check(_ status: OpaquePointer?) throws {
        try OrtBgeEmbedding.checkRaw(api, status)
    }

    /// 把 `OrtStatus*` 转成 Swift 错误。`nil` 表示成功。
    private static func checkRaw(_ api: UnsafePointer<OrtApi>, _ status: OpaquePointer?) throws {
        guard let status else { return }
        let msg = api.pointee.GetErrorMessage(status).map { String(cString: $0) } ?? "(无错误信息)"
        api.pointee.ReleaseStatus(status)
        throw OrtFailure(message: msg)
    }
}

// MARK: - 模型目录解析

extension OrtBgeEmbedding {
    static let MODEL_FILE = "model.onnx"
    static let VOCAB_FILE = "vocab.txt"

    /// int8 量化模型的空间戳（与 Android 同值）。
    ///
    /// ⚠️ **必须与 fp32 不同**：量化向量的余弦只有 ~0.96–0.97，分数分布整体上移，
    /// 同一阈值下误召回会变多。用同一个戳就是"两套向量混用"（RFC §18.1 明令禁止）。
    static let INT8_SPACE_ID = "BAAI/bge-small-zh-v1.5-int8@512"

    /// 模型是否齐备（缺任一就退回确定性桩，并在自检里如实标注）。
    static func isAvailable(modelDir: URL) -> Bool {
        let fm = FileManager.default
        return fm.fileExists(atPath: modelDir.appendingPathComponent(MODEL_FILE).path)
            && fm.fileExists(atPath: modelDir.appendingPathComponent(VOCAB_FILE).path)
    }

    /// 候选目录（**App bundle 内优先**：模型随包分发，模拟器/真机都能读）。
    ///
    /// 与 Android 的差异要写清楚：Android 走 `run-as` 写内部私有目录（外部目录读不到，
    /// 是属主/组权限问题）；iOS 的 App bundle 是只读但**一定可读**，最省事，
    /// 所以主路径是 bundle，Documents 作为"运行时下载/手工放置"的候选。
    static func candidateDirs() -> [URL] {
        var out: [URL] = []
        if let res = Bundle.main.resourceURL {
            out.append(res.appendingPathComponent("models/bge-small-zh-v1.5"))
            out.append(res.appendingPathComponent("models/bge-small-zh-v1.5-int8"))
        }
        if let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first {
            out.append(docs.appendingPathComponent("models/bge-small-zh-v1.5"))
            out.append(docs.appendingPathComponent("models/bge-small-zh-v1.5-int8"))
        }
        return out
    }

    /// 实际可用的模型目录（都没有则返回 nil → 上层退回桩嵌入）。
    static func resolveDir() -> URL? {
        candidateDirs().first { isAvailable(modelDir: $0) }
    }

    /// 诊断串：每个候选目录是否存在模型文件（自检里打印，避免"模型在哪"靠猜）。
    static func diagnostics() -> String {
        candidateDirs()
            .map { "\($0.lastPathComponent)\($0.path.contains("-int8") ? "(int8)" : "(fp32)")=\(isAvailable(modelDir: $0))" }
            .joined(separator: " | ")
    }
}
