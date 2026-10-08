import Foundation
import SharedCore

/// 端侧知识库的 Apple 侧实现：**一个 JSON 文件同时存切片向量与文档元信息**。
///
/// 对应 Android 的 `rag/SqliteVectorStore`（那边用 SQLite，因为它同时实现
/// `VectorStore` 与 `DocumentRegistry`，同一张库）。这里不引 SQLite：
/// 端侧语料是"小而准"（几十份文档 / 几百切片），一个 JSON 的读写成本可以忽略，
/// 而**零额外依赖**在受限构建环境里更划算。语义与接口完全一致，
/// 换成 SQLite 只影响这个文件（`Retriever` / `KnowledgeIndex` / 工具层都不知道差别）。
///
/// ## 空间的唯一真相
///
/// 文件里记着"这些向量是用哪个空间算的"。打开时若发现**库里记的空间 ≠ 当前嵌入模型**
/// （例如刚从确定性桩切到 ONNX，或从 fp32 切到 int8），走**原地重嵌入**而不是删库——
/// 索引里的文本是设备侧唯一副本（RFC §4.5-F 的"重算"）。
///
/// ## 单篇删除不误清全库
///
/// `deleteBySource` 只删该来源的切片。**接口里必须有这个方法**：早期版本没有它，
/// `KnowledgeIndex.remove()` 用"清空整库"绕过去，删一篇文档会把整个索引清掉
/// （其余文档的文本是设备侧唯一副本，等于数据丢失）。自检项 `ui_doc_delete_isolated` 钉住这条。
final class FileKnowledgeStore: VectorStore, DocumentRegistry {

    // MARK: - 类型

    private struct Record {
        let id: String
        let sourceId: String
        let text: String
        var vector: KotlinFloatArray
    }

    // MARK: - 状态

    /// 索引里的向量属于哪个空间（构造期确定；换模型必须重建/重嵌入）。
    let space: EmbeddingSpace

    private let file: URL
    private let provider: EmbeddingProvider
    private let lock = NSLock()

    private var records: [Record] = []
    private var docs: [DocumentInfo] = []

    /// 打开时因空间不符而重嵌入的切片数（自检/排障用）。
    private(set) var reembeddedChunks: Int = 0
    /// 打开时库里记的旧空间（没有则 nil）。
    private(set) var previousSpace: String?
    /// 最近一次失败原因（接口不 throwing，只能这样暴露）。
    private(set) var lastError: String?

    // MARK: - 构造

    init(file: URL, provider: EmbeddingProvider) {
        self.file = file
        self.provider = provider
        self.space = provider.space
        try? FileManager.default.createDirectory(
            at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        loadFromDisk()
    }

    static func defaultFile() -> URL {
        SekbPaths.knowledgeDir().appendingPathComponent("index.json")
    }

    // MARK: - 持久化

    private func loadFromDisk() {
        lock.lock(); defer { lock.unlock() }
        guard let data = FileManager.default.contents(atPath: file.path),
              let root = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else {
            return   // 首次运行：空库
        }
        let storedSpace = (root["space"] as? String) ?? ""
        let storedDim = (root["dim"] as? NSNumber)?.intValue ?? 0

        if let rawRecords = root["records"] as? [[String: Any]] {
            for r in rawRecords {
                guard let id = r["id"] as? String, let text = r["text"] as? String else { continue }
                let sourceId = (r["sourceId"] as? String) ?? id
                let values = (r["vector"] as? [NSNumber])?.map { Float(truncating: $0) } ?? []
                records.append(Record(id: id, sourceId: sourceId, text: text,
                                      vector: floatArray(values)))
            }
        }
        if let rawDocs = root["documents"] as? [[String: Any]] {
            for d in rawDocs {
                guard let id = d["id"] as? String else { continue }
                docs.append(DocumentInfo(
                    id: id,
                    name: (d["name"] as? String) ?? id,
                    sizeBytes: Int64((d["sizeBytes"] as? NSNumber)?.int64Value ?? 0),
                    chunks: Int32((d["chunks"] as? NSNumber)?.intValue ?? 0),
                    addedAtMillis: Int64((d["addedAtMillis"] as? NSNumber)?.int64Value ?? 0),
                    deviceOnly: (d["deviceOnly"] as? Bool) ?? true))
            }
        }

        // 空间不符 → 原地重嵌入（**不删库**：文本是设备侧唯一副本）
        if storedSpace != space.id || storedDim != Int(space.dim) {
            previousSpace = storedSpace.isEmpty ? nil : storedSpace
            reembedLocked()
        }
    }

    /// 用当前嵌入器把已有切片**重算**（切片边界不变，只换向量空间）。
    private func reembedLocked() {
        guard !records.isEmpty else { return }
        let vectors = provider.embed(texts: records.map { $0.text })
        guard vectors.count == records.count else {
            lastError = "重嵌入返回条数 \(vectors.count) ≠ 切片数 \(records.count)"
            return
        }
        for i in records.indices { records[i].vector = vectors[i] }
        reembeddedChunks = records.count
        persistLocked()
    }

    private func persistLocked() {
        var jsonRecords: [[String: Any]] = []
        for r in records {
            var values: [Double] = []
            values.reserveCapacity(Int(r.vector.size))
            for i in 0..<Int(r.vector.size) { values.append(Double(r.vector.get(index: Int32(i)))) }
            jsonRecords.append(["id": r.id, "sourceId": r.sourceId, "text": r.text, "vector": values])
        }
        let jsonDocs: [[String: Any]] = docs.map { d in
            ["id": d.id, "name": d.name, "sizeBytes": d.sizeBytes, "chunks": Int(d.chunks),
             "addedAtMillis": d.addedAtMillis, "deviceOnly": d.deviceOnly]
        }
        let root: [String: Any] = [
            "v": 1,
            "space": space.id,
            "dim": Int(space.dim),
            "records": jsonRecords,
            "documents": jsonDocs,
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: root, options: []) else {
            lastError = "索引序列化失败"
            return
        }
        do {
            try data.write(to: file, options: .atomic)
        } catch {
            lastError = "索引写盘失败：\(error.localizedDescription)"
        }
    }

    private func floatArray(_ values: [Float]) -> KotlinFloatArray {
        let arr = KotlinFloatArray(size: Int32(values.count))
        for (i, v) in values.enumerated() { arr.set(index: Int32(i), value: v) }
        return arr
    }

    // MARK: - VectorStore

    func upsert(records newRecords: [VectorRecord]) {
        lock.lock(); defer { lock.unlock() }
        for r in newRecords {
            // 维度不符的记录**拒绝写入**（与共享层 InMemoryVectorStore 的 require 同一判据，
            // 但这里不崩：接口不 throwing，崩了就是用户丢数据）
            if Int(r.vector.size) != Int(space.dim) {
                lastError = "向量维度不符：期望 \(space.dim)，实际 \(r.vector.size)"
                continue
            }
            let record = Record(id: r.id, sourceId: r.sourceId, text: r.text, vector: r.vector)
            if let idx = records.firstIndex(where: { $0.id == record.id }) {
                records[idx] = record
            } else {
                records.append(record)
            }
        }
        persistLocked()
    }

    func deleteBySource(sourceId: String) -> Int32 {
        lock.lock(); defer { lock.unlock() }
        let before = records.count
        records.removeAll { $0.sourceId == sourceId }
        persistLocked()
        return Int32(before - records.count)
    }

    func search(query: KotlinFloatArray, topK: Int32) -> [VectorHit] {
        lock.lock(); defer { lock.unlock() }
        if Int(query.size) != Int(space.dim) { return [] }
        if topK <= 0 { return [] }
        // 语义与共享层 `VectorMath.topK` 一致：余弦降序，**分数相同按 id 升序**（结果稳定可测）
        let scored = records.map { (record: $0, score: VectorMath.shared.cosine(a: query, b: $0.vector)) }
        let sorted = scored.sorted { a, b in
            a.score != b.score ? a.score > b.score : a.record.id < b.record.id
        }
        return sorted.prefix(Int(topK)).map {
            VectorHit(id: $0.record.id, sourceId: $0.record.sourceId,
                      text: $0.record.text, score: $0.score)
        }
    }

    func size() -> Int32 {
        lock.lock(); defer { lock.unlock() }
        return Int32(records.count)
    }

    func clear() {
        lock.lock(); defer { lock.unlock() }
        records.removeAll()
        docs.removeAll()
        persistLocked()
    }

    func all() -> [VectorRecord] {
        lock.lock(); defer { lock.unlock() }
        return records.map { VectorRecord(id: $0.id, sourceId: $0.sourceId, text: $0.text, vector: $0.vector) }
    }

    // MARK: - DocumentRegistry

    func upsert(info: DocumentInfo) {
        lock.lock(); defer { lock.unlock() }
        if let idx = docs.firstIndex(where: { $0.id == info.id }) {
            docs[idx] = info
        } else {
            docs.append(info)
        }
        persistLocked()
    }

    /// 按导入时间倒序（最近导入的在最前）。
    func list() -> [DocumentInfo] {
        lock.lock(); defer { lock.unlock() }
        return docs.sorted { $0.addedAtMillis > $1.addedAtMillis }
    }

    func get(id: String) -> DocumentInfo? {
        lock.lock(); defer { lock.unlock() }
        return docs.first { $0.id == id }
    }

    func delete(id: String) -> Bool {
        lock.lock(); defer { lock.unlock() }
        let before = docs.count
        docs.removeAll { $0.id == id }
        persistLocked()
        return docs.count != before
    }
}
