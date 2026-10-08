import Foundation

/// 一份文档的导入结果（`ui/DocumentImporter.kt` 的 Swift 对应物）。
enum DocumentImportResult {
    case ok(name: String, text: String, sizeBytes: Int64)
    case rejected(name: String, reason: String)
}

/// 本机文档导入：**纯文本类 + PDF**。
///
/// 两条与 Android 侧一致的纪律：
/// 1. **先认 PDF 魔数**（`%PDF`），再判二进制——顺序反了会把所有 PDF 拒掉（PDF 里必然有二进制字节）；
/// 2. 二进制文件**如实拒绝**，而不是把乱码灌进索引。脏索引比空索引更糟：
///    检索会命中一堆乱码，用户还以为是"AI 乱答"。
///
/// PDF 走已有的 `PdfTextExtractor`（PDFKit），**四类结果各有可读原因**
/// （有文本层 / 无文本层 / 加密 / 解析失败），不静默返回空文本。
enum SekbDocumentImporter {

    /// 纯文本单文件上限：端侧索引是"小而准"，塞进超大文件只会拖慢嵌入且几乎必然切得很碎。
    static let maxBytes: Int64 = 2 * 1024 * 1024
    /// PDF 单文件上限（PDF 是容器，体积天然更大；限制的是**原始文件**而不是抽取后的文本）。
    static let maxPdfBytes: Int64 = 20 * 1024 * 1024
    /// 抽取后的文本上限：防止一个 PDF 抽出几 MB 文本把索引灌爆。
    static let maxExtractedChars = 400_000

    /// 从用户选中的 URL 读入。iOS 的 `fileImporter` 给的是**安全作用域** URL，
    /// 不 start/stop 就可能在真机上读不到（模拟器常常"碰巧能读"，于是这个问题到真机才暴露）。
    static func read(url: URL) -> DocumentImportResult {
        let name = url.lastPathComponent.isEmpty ? "未命名" : url.lastPathComponent
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }

        let limit = looksLikePdfName(name) ? maxPdfBytes : maxBytes
        let size = (try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize).map(Int64.init) ?? -1
        if size > limit {
            return .rejected(name: name, reason: "文件超过 \(limit / 1024 / 1024)MB 上限")
        }
        guard let data = try? Data(contentsOf: url) else {
            return .rejected(name: name, reason: "无法读取该文件")
        }
        if Int64(data.count) > limit {
            return .rejected(name: name, reason: "文件超过 \(limit / 1024 / 1024)MB 上限")
        }
        return decide(bytes: data, name: name)
    }

    /// 纯判定：字节 → 导入结果（不碰文件系统，便于自检直接跑）。
    static func decide(bytes: Data, name: String) -> DocumentImportResult {
        if isPdf(bytes) {
            switch PdfTextExtractor.extract(data: bytes) {
            case let .ok(text, pages):
                let clipped = text.count > maxExtractedChars ? String(text.prefix(maxExtractedChars)) : text
                _ = pages   // 页数只用于诊断，不进结果（Android 侧同样把页数写进拒绝原因里）
                return .ok(name: name, text: clipped, sizeBytes: Int64(bytes.count))
            case .noTextLayer:
                return .rejected(name: name,
                                 reason: "这份 PDF 没有文本层（多半是扫描件）；本期不做 OCR，请先用工具转成带文本的 PDF")
            case .encrypted:
                return .rejected(name: name, reason: "PDF 已加密，需要密码")
            case let .failed(reason):
                return .rejected(name: name, reason: "PDF 解析失败：\(reason)")
            }
        }
        if looksBinary(bytes) {
            return .rejected(name: name,
                             reason: "看起来是二进制/压缩格式（本期支持纯文本与 PDF；Word/Excel 见 BACKLOG）")
        }
        // `String(decoding:as:)` 会把非法字节替换成 U+FFFD 而不是返回 nil——
        // 与 Android 的 `String(bytes, UTF_8)` 行为一致（不因为一个坏字节丢掉整份文件）
        return .ok(name: name, text: String(decoding: bytes, as: UTF8.self),
                   sizeBytes: Int64(bytes.count))
    }

    /// PDF 判定看**魔数**（`%PDF`），不看扩展名：用户从聊天软件存的文件常常没有扩展名。
    static func isPdf(_ bytes: Data) -> Bool {
        bytes.count >= 4 && bytes.prefix(4) == Data([0x25, 0x50, 0x44, 0x46])
    }

    private static func looksLikePdfName(_ name: String) -> Bool {
        name.lowercased().hasSuffix(".pdf")
    }

    /// 二进制判定：前 8KB 里出现 NUL 字节，或不可打印字符占比超过 10%。
    /// 为什么不用扩展名：用户从聊天软件存的文件经常没有扩展名，而内容判断更可靠。
    static func looksBinary(_ bytes: Data) -> Bool {
        if bytes.isEmpty { return false }
        let head = bytes.prefix(8192)
        if head.contains(0x00) { return true }
        var weird = 0
        for b in head {
            let v = Int(b)
            let printable = v == 9 || v == 10 || v == 13 || (v >= 32 && v <= 126) || v >= 0x80
            if !printable { weird += 1 }
        }
        return Double(weird) / Double(head.count) > 0.10
    }
}
