import Foundation

/// 极简 Markdown 的**块**（与 Android `ui/Markdown.kt` 的 `Block` 一一对应）。
enum MarkdownBlock: Equatable {
    case heading(level: Int, text: String)
    case paragraph(String)
    case bullet([String])
    case numbered([String])
    case quote([String])
    case code(lang: String, code: String)
    case table(header: [String], rows: [[String]])
    case divider
}

/// 行内片段（与 Android 的 `Span` 对应）。
enum MarkdownSpan: Equatable {
    case text(String)
    case bold(String)
    case italic(String)
    case code(String)
    case link(text: String, url: String)
}

/// 极简 Markdown 解析器（**块级 + 少量行内**）——`ui/Markdown.kt` 的 Swift 移植。
///
/// ## 为什么必须做
///
/// 端侧/云端模型的回答**本来就是 Markdown**（实测回答里出现了 `# 标题`、`## 核心结论`、
/// `> ⚠️ **信息说明**`、`| 路径 | 环节 |`）。直接 `Text(bubble.text)` 会让用户看到**源码**：
/// `**粗体**`、`|---|` 原样糊在脸上。这不是"锦上添花"，是"能不能读"的问题。
///
/// ## 为什么两端各一份解析器而不是共享
///
/// 解析规则属于**渲染语义**（它服务于"渲染成什么"），而渲染是各端原生的（owner 已定 D1）。
/// 真正的纪律是**不要让第二套语义长出来**：所以这里的正则、分支顺序、
/// 甚至"未闭合标记降级为普通文本"的行为，都是逐行照抄 `Markdown.kt`——
/// 那边的版本有单测钉住（`MarkdownTest`），改这里必须先改那边。
///
/// ## 设计约束：**必须能处理"半句话"**
///
/// 流式输出时每个增量都会调用一次解析，输入可能是 `**粗体没闭合`、```` ``` ```` 还没收尾。
/// 所以解析器**不抛异常**、也不因为语法不完整而丢内容——一律降级成普通文本。
/// `NSRegularExpression` 的小包装（`SekbMarkdown` 与 `PlaneExplain` 共用）。
///
/// 用它而不是 Swift 的裸斜杠字面量：后者在 Swift 5 语言模式下需要额外的编译开关
/// （`-enable-bare-slash-regex`），而本项目的 Swift 是脚本直编的，不想引这种只在特定
/// 语言模式下成立的写法。
struct SekbRegex {
    let re: NSRegularExpression

    init(_ pattern: String) {
        // 模式是写死的常量：编不出来是**编程错误**，应当在构建期就炸
        re = try! NSRegularExpression(pattern: pattern)
    }

    private func ns(_ s: String) -> NSRange { NSRange(s.startIndex..<s.endIndex, in: s) }

    /// 整串匹配（Kotlin `Regex.matches`）
    func full(_ s: String) -> NSTextCheckingResult? {
        let range = ns(s)
        guard let m = re.firstMatch(in: s, range: range), m.range == range else { return nil }
        return m
    }

    func isFull(_ s: String) -> Bool { full(s) != nil }

    /// 搜索（Kotlin `Regex.find` / `containsMatchIn`）
    func find(_ s: String) -> NSTextCheckingResult? { re.firstMatch(in: s, range: ns(s)) }

    func all(_ s: String) -> [NSTextCheckingResult] { re.matches(in: s, range: ns(s)) }

    /// 取捕获组；**未参与的组返回空串**（与 Kotlin `groupValues[i]` 一致）。
    func group(_ m: NSTextCheckingResult, _ index: Int, _ s: String) -> String {
        guard index < m.numberOfRanges else { return "" }
        let r = m.range(at: index)
        guard r.location != NSNotFound, let range = Range(r, in: s) else { return "" }
        return String(s[range])
    }
}

enum SekbMarkdown {

    private static let heading = SekbRegex("^(#{1,6})\\s+(.*)$")
    private static let bullet = SekbRegex("^\\s*[-*+]\\s+(.*)$")
    private static let numbered = SekbRegex("^\\s*\\d+[.)]\\s+(.*)$")
    private static let fence = SekbRegex("^\\s*```\\s*([A-Za-z0-9+#-]*)\\s*$")
    private static let divider = SekbRegex("^\\s*(-{3,}|\\*{3,}|_{3,})\\s*$")
    private static let tableSep = SekbRegex("^\\s*\\|?\\s*:?-{2,}:?\\s*(\\|\\s*:?-{2,}:?\\s*)*\\|?\\s*$")

    // 顺序有讲究：**粗体** 必须在 *斜体* 之前匹配，否则 `**x**` 会被斜体规则吃掉
    private static let inlineRe = SekbRegex(
        "`([^`]*)`" +
        "|\\*\\*([^*]+)\\*\\*" +
        "|\\*([^*\\n]+)\\*" +
        "|\\[([^\\]]+)]\\(([^)]+)\\)"
    )

    // MARK: - 块级解析

    static func parse(_ src: String) -> [MarkdownBlock] {
        var out: [MarkdownBlock] = []
        let lines = src.replacingOccurrences(of: "\r\n", with: "\n").components(separatedBy: "\n")

        var i = 0
        var para: [String] = []
        var bullets: [String] = []
        var numerated: [String] = []
        var quote: [String] = []

        func flush() {
            if !para.isEmpty {
                out.append(.paragraph(para.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)))
                para.removeAll()
            }
            if !bullets.isEmpty { out.append(.bullet(bullets)); bullets.removeAll() }
            if !numerated.isEmpty { out.append(.numbered(numerated)); numerated.removeAll() }
            if !quote.isEmpty { out.append(.quote(quote)); quote.removeAll() }
        }

        while i < lines.count {
            let line = lines[i]

            // 代码块：遇到未闭合的 ``` 也要把剩余内容当代码块输出（流式时的常见状态）
            if let m = fence.find(line) {
                flush()
                let lang = fence.group(m, 1, line)
                var body: [String] = []
                i += 1
                while i < lines.count && !fence.isFull(lines[i]) {
                    body.append(lines[i])
                    i += 1
                }
                if i < lines.count { i += 1 }   // 跳过结束的 ```
                out.append(.code(lang: lang, code: body.joined(separator: "\n")))
                continue
            }

            if line.trimmingCharacters(in: .whitespaces).isEmpty {
                flush()
            } else if heading.isFull(line) {
                flush()
                if let m = heading.find(line) {
                    out.append(.heading(level: heading.group(m, 1, line).count, text: heading.group(m, 2, line)
                        .trimmingCharacters(in: .whitespaces)))
                }
            } else if divider.isFull(line) {
                flush()
                out.append(.divider)
            } else if line.contains("|") && i + 1 < lines.count && tableSep.isFull(lines[i + 1]) {
                // 表格：当前行是 | a | b |，且下一行是分隔行
                flush()
                let header = splitRow(line)
                var rows: [[String]] = []
                i += 2
                while i < lines.count && lines[i].contains("|")
                    && !lines[i].trimmingCharacters(in: .whitespaces).isEmpty {
                    rows.append(splitRow(lines[i]))
                    i += 1
                }
                out.append(.table(header: header, rows: rows))
                continue
            } else if bullet.isFull(line) {
                if !para.isEmpty || !numerated.isEmpty || !quote.isEmpty { flush() }
                if let m = bullet.find(line) {
                    bullets.append(bullet.group(m, 1, line).trimmingCharacters(in: .whitespaces))
                }
            } else if numbered.isFull(line) {
                if !para.isEmpty || !bullets.isEmpty || !quote.isEmpty { flush() }
                if let m = numbered.find(line) {
                    numerated.append(numbered.group(m, 1, line).trimmingCharacters(in: .whitespaces))
                }
            } else if line.trimmingCharacters(in: .whitespaces).hasPrefix(">") {
                if !para.isEmpty || !bullets.isEmpty || !numerated.isEmpty { flush() }
                let stripped = String(line.trimmingCharacters(in: .whitespaces).dropFirst())
                quote.append(stripped.trimmingCharacters(in: .whitespaces))
            } else {
                if !bullets.isEmpty || !numerated.isEmpty || !quote.isEmpty { flush() }
                para.append(line)
            }
            i += 1
        }
        flush()
        return out
    }

    /// 解析表格行：`| a | b |` → `[a, b]`（容忍首尾没有竖线）。
    static func splitRow(_ line: String) -> [String] {
        var s = line.trimmingCharacters(in: .whitespaces)
        if s.hasPrefix("|") { s.removeFirst() }
        if s.hasSuffix("|") { s.removeLast() }
        return s.components(separatedBy: "|").map { $0.trimmingCharacters(in: .whitespaces) }
    }

    // MARK: - 行内解析

    /// 拆行内片段。**未闭合的标记按普通文本处理**（流式输出必然出现半截标记，
    /// 例如 `**粗体没闭合`；这时正则不匹配，自然降级为文本，不会丢字）。
    static func inline(_ text: String) -> [MarkdownSpan] {
        var out: [MarkdownSpan] = []
        var last = 0
        let ns = text as NSString
        for m in inlineRe.all(text) {
            if m.range.location > last {
                out.append(.text(ns.substring(with: NSRange(location: last, length: m.range.location - last))))
            }
            let value = ns.substring(with: m.range)
            let g1 = inlineRe.group(m, 1, text)
            let g2 = inlineRe.group(m, 2, text)
            let g3 = inlineRe.group(m, 3, text)
            let g4 = inlineRe.group(m, 4, text)
            let g5 = inlineRe.group(m, 5, text)
            if !g1.isEmpty || (g1.isEmpty && value.hasPrefix("`")) {
                out.append(.code(g1))
            } else if !g2.isEmpty {
                out.append(.bold(g2))
            } else if !g3.isEmpty {
                out.append(.italic(g3))
            } else if !g4.isEmpty {
                out.append(.link(text: g4, url: g5))
            } else {
                out.append(.text(value))
            }
            last = m.range.location + m.range.length
        }
        if last < ns.length {
            out.append(.text(ns.substring(from: last)))
        }
        return out
    }
}
