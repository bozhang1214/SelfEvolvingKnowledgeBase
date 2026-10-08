import Foundation
import SharedCore

/// 从工具结果里解析出**检索来源**（`ui/RetrievalSources.kt` 的 Swift 移植）。
///
/// 为什么值得单独一层：端侧 RAG 的答对/答错必须可追溯——用户看到"AI 说 X"时，
/// 有权知道 X 是从哪段本机资料里来的。这也是端侧 RAG 相对云端的卖点之一（来源就在设备上）。
///
/// 输入形如（共享层 `Retriever.formatForModel` 的输出）：
/// ```
/// [0.696] doc-rag: 端侧 RAG 把知识索引放在设备上…
/// [0.612] doc-privacy: 设备专属数据永不出端…
/// ```
enum RetrievalSources {

    struct Source: Identifiable, Equatable {
        let sourceId: String
        let score: Double
        let snippet: String

        /// 列表去重用（同源不同分算两条，与 Android 一致）
        var id: String { "\(sourceId)@\(score)" }
    }

    /// ⚠️ 与 Android 侧**逐字符相同**：分数必须是 `数字.数字`（共享层 `Fmt.fixed` 保证），
    /// 冒号中英文都认（模型/来源名里两种都出现过）。
    private static let line = SekbRegex("^\\[(\\d+\\.\\d+)]\\s*([^:：]+)[:：]\\s*(.*)$")

    static func parse(_ results: [ToolResult]) -> [Source] {
        var out: [Source] = []
        for r in results {
            if !r.ok { continue }
            for raw in r.output.components(separatedBy: "\n") {
                let trimmed = raw.trimmingCharacters(in: .whitespaces)
                guard let m = line.find(trimmed) else { continue }
                let score = Double(line.group(m, 1, trimmed)) ?? 0.0
                let sourceId = line.group(m, 2, trimmed).trimmingCharacters(in: .whitespaces)
                let snippet = String(line.group(m, 3, trimmed)
                    .trimmingCharacters(in: .whitespaces).prefix(80))
                out.append(Source(sourceId: sourceId, score: score, snippet: snippet))
            }
        }
        return out.sorted { $0.score > $1.score }
    }

    /// 给界面用的一行摘要。
    static func summary(_ sources: [Source]) -> String {
        if sources.isEmpty { return "" }
        let body = sources.map { "\($0.sourceId)(\(String(format: "%.2f", $0.score)))" }
            .joined(separator: "、")
        return "检索到 \(sources.count) 段：" + body
    }
}
