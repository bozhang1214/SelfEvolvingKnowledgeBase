import Foundation
import PDFKit

/// PDF 文本抽取的四种结果——**与 Android 侧同一套语义**（`ui/PdfExtractor.kt`）：
/// 端侧没有服务端兜底，所以失败必须**分类说清原因**，不能静默返回空文本。
///
/// Android 用 PdfBox-Android 抽"文本层"；iOS 用系统自带的 **PDFKit**（能力更强、零依赖）。
/// 两端都只抽文本层、不做 OCR——扫描件（无文本层）明确报 `noTextLayer`。
enum PdfExtractOutcome {
    case ok(text: String, pages: Int)
    /// 有 PDF 结构但没有文本层（扫描件/纯图片页）：本期不做 OCR，必须如实告知
    case noTextLayer
    case encrypted
    case failed(reason: String)
}

enum PdfTextExtractor {

    /// 抽取上限：与 Android 侧一致（40 万字符），防止超长 PDF 把内存吃光
    static let maxChars = 400_000

    static func extract(data: Data) -> PdfExtractOutcome {
        // 先判魔数：不是 PDF 就早退，别让 PDFKit 去猜（与 Android 侧"先判 %PDF 再判二进制"同一考虑）
        guard data.count > 4, data.prefix(4) == Data([0x25, 0x50, 0x44, 0x46]) else {
            return .failed(reason: "不是 PDF（缺少 %PDF 魔数）")
        }
        guard let doc = PDFDocument(data: data) else {
            return .failed(reason: "PDFKit 无法解析该文件")
        }
        if doc.isEncrypted && doc.isLocked {
            return .encrypted
        }
        var text = ""
        for index in 0..<doc.pageCount {
            if let page = doc.page(at: index), let pageText = page.string {
                text += pageText
            }
            if text.count >= maxChars { break }
        }
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty {
            return .noTextLayer
        }
        return .ok(text: String(trimmed.prefix(maxChars)), pages: doc.pageCount)
    }
}
