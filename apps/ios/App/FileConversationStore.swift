import Foundation
import SharedCore

/// 会话落盘的 Apple 侧实现：**每个会话一个 JSON 文件**（`chat/FileConversationStore.kt` 的对应物）。
///
/// ## 为什么不是一个"大 JSON 全都会话"
///
/// 单文件更简单，但**一次写坏 = 用户所有历史全没**。一文件一会话的失败面小得多：
/// 某个文件损坏只影响那一段对话，其余照常打开。
///
/// ## 三条刻意的行为（与 Android 逐条对齐）
///
/// 1. **坏文件跳过而不是抛异常**——`list()` 遇到解不开的文件就跳过并计数。
///    用户宁可少看到一段对话，也不该看到"历史列表打不开"。
/// 2. **上限裁剪**：超过 `maxConversations` 段就删最旧的。端侧存储有限，
///    而"无限增长的聊天记录"是典型的隐性磁盘泄漏。
/// 3. **写盘原子**：`Data.write(options: .atomic)`（内部就是"写临时文件 + rename"）。
///    直接覆写时若进程被杀会留下半截 JSON，再次打开就是一段坏数据。
///
/// 落盘格式**复用共享层的 `ConversationCodec`**：两端同一份 JSON，才不会出现
/// "Android 存的会话 Apple 读不出来"。
final class FileConversationStore: ConversationStore {

    private let dir: URL
    private let maxConversations: Int
    private let lock = NSLock()

    /// 最近一次 [list] 遇到并跳过的坏文件数（自检/排障用）。
    private(set) var lastSkippedCorrupt: Int = 0

    init(dir: URL, maxConversations: Int = FileConversationStore.maxConversationsDefault) {
        self.dir = dir
        self.maxConversations = maxConversations
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    }

    /// 应用私有目录下的固定位置。
    static func defaultDir() -> URL { SekbPaths.conversationsDir() }

    /// 50 段对话：按每段几十 KB 估，占用在个位数 MB，对端侧足够。
    static let maxConversationsDefault = 50

    // MARK: - 路径

    private func fileOf(_ id: String) -> URL { dir.appendingPathComponent("\(id).json") }

    /// 当前会话指针。单独一个文件，**不叫 .json**，因此不会被 [list] 当会话读。
    private var pointerFile: URL { dir.appendingPathComponent("current.id") }

    // MARK: - ConversationStore

    func saveCurrentId(id: String) {
        lock.lock(); defer { lock.unlock() }
        try? id.write(to: pointerFile, atomically: true, encoding: .utf8)
    }

    func currentId() -> String? {
        lock.lock(); defer { lock.unlock() }
        guard let raw = try? String(contentsOf: pointerFile, encoding: .utf8) else { return nil }
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    func list() -> [ConversationSummary] {
        lock.lock(); defer { lock.unlock() }
        return listLocked()
    }

    private func listLocked() -> [ConversationSummary] {
        var skipped = 0
        var out: [ConversationSummary] = []
        let files = (try? FileManager.default.contentsOfDirectory(
            at: dir, includingPropertiesForKeys: nil, options: [.skipsHiddenFiles])) ?? []
        for f in files where f.pathExtension == "json" {
            let id = f.deletingPathExtension().lastPathComponent
            guard let raw = try? String(contentsOf: f, encoding: .utf8),
                  let c = ConversationCodec.shared.decode(json: raw) else {
                skipped += 1
                continue
            }
            out.append(ConversationSummary(
                id: c.id.isEmpty ? id : c.id,
                title: c.title,
                updatedAtMillis: c.updatedAtMillis,
                messageCount: Int32(c.messages.count)))
        }
        lastSkippedCorrupt = skipped
        return out.sorted { $0.updatedAtMillis > $1.updatedAtMillis }
    }

    func load(id: String) -> Conversation? {
        lock.lock(); defer { lock.unlock() }
        return loadLocked(id)
    }

    private func loadLocked(_ id: String) -> Conversation? {
        let f = fileOf(id)
        guard FileManager.default.fileExists(atPath: f.path) else { return nil }
        guard let raw = try? String(contentsOf: f, encoding: .utf8) else { return nil }
        return ConversationCodec.shared.decode(json: raw)
    }

    func save(c: Conversation) {
        lock.lock(); defer { lock.unlock() }
        let json = ConversationCodec.shared.encode(c: c)
        guard let data = json.data(using: .utf8) else { return }
        // `.atomic` = 写临时文件 + 原子改名（与 Android 的 tmp+rename 同一考虑）
        try? data.write(to: fileOf(c.id), options: .atomic)
        trimLocked()
    }

    func delete(id: String) -> Bool {
        lock.lock(); defer { lock.unlock() }
        do {
            try FileManager.default.removeItem(at: fileOf(id))
            return true
        } catch {
            return false
        }
    }

    func clear() {
        lock.lock(); defer { lock.unlock() }
        let files = (try? FileManager.default.contentsOfDirectory(
            at: dir, includingPropertiesForKeys: nil, options: [.skipsHiddenFiles])) ?? []
        for f in files where f.pathExtension == "json" {
            try? FileManager.default.removeItem(at: f)
        }
    }

    /// Kotlin 接口里的默认方法在 Swift 侧**不算已实现**（导出的协议把两个方法都标成 `@required`），
    /// 所以 `latest()` 必须显式写一份。
    func latest() -> Conversation? {
        lock.lock(); defer { lock.unlock() }
        guard let first = listLocked().first else { return nil }
        return loadLocked(first.id)
    }

    /// 只保留最近的 [maxConversations] 段。
    private func trimLocked() {
        let all = listLocked()
        if all.count <= maxConversations { return }
        for s in all.dropFirst(maxConversations) {
            try? FileManager.default.removeItem(at: fileOf(s.id))
        }
    }
}
