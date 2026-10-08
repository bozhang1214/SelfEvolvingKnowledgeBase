import SwiftUI

/// 把 `SekbMarkdown` 解析出来的块渲染成 SwiftUI 视图。
///
/// 视觉上对齐网页端（AntD）与 Android（`ui/MarkdownView.kt`）：标题字号递减、
/// 行内代码等宽、代码块浅灰面板、引用左侧竖线、表格横向可滚。
///
/// **不追求完整 Markdown**，只覆盖模型实际会输出的那几类——多做的部分没人用，还会引入显示 bug
/// （与 Android 侧同一条理由）。
struct SekbMarkdownView: View {
    @Environment(\.sekb) private var pal

    let text: String
    /// 正文字号（气泡里 14，来源卡片等紧凑处可调小）
    var fontSize: CGFloat = 14

    var body: some View {
        let blocks = SekbMarkdown.parse(text)
        VStack(alignment: .leading, spacing: 6) {
            // 用下标当 id：块没有稳定标识，而每次文本变化都会整体重解析（流式场景）
            ForEach(Array(blocks.enumerated()), id: \.offset) { item in
                blockView(item.element)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - 块

    @ViewBuilder
    private func blockView(_ block: MarkdownBlock) -> some View {
        switch block {
        case let .heading(level, text):
            inlineText(text)
                .font(.system(size: level == 1 ? 18 : (level == 2 ? 16 : 15), weight: .semibold))
                .foregroundColor(pal.textPrimary)

        case let .paragraph(text):
            inlineText(text)
                .font(.system(size: fontSize))
                .foregroundColor(pal.textPrimary)

        case let .bullet(items):
            VStack(alignment: .leading, spacing: 3) {
                ForEach(Array(items.enumerated()), id: \.offset) { item in
                    HStack(alignment: .top, spacing: 0) {
                        Text("•  ").font(.system(size: fontSize)).foregroundColor(pal.textPrimary)
                        inlineText(item.element)
                            .font(.system(size: fontSize))
                            .foregroundColor(pal.textPrimary)
                    }
                    .fixedSize(horizontal: false, vertical: true)
                }
            }

        case let .numbered(items):
            VStack(alignment: .leading, spacing: 3) {
                ForEach(Array(items.enumerated()), id: \.offset) { item in
                    HStack(alignment: .top, spacing: 0) {
                        Text("\(item.offset + 1).  ")
                            .font(.system(size: fontSize))
                            .foregroundColor(pal.textPrimary)
                        inlineText(item.element)
                            .font(.system(size: fontSize))
                            .foregroundColor(pal.textPrimary)
                    }
                    .fixedSize(horizontal: false, vertical: true)
                }
            }

        case let .quote(lines):
            HStack(alignment: .top, spacing: 8) {
                // 竖线用 Rectangle：它会自动撑到同行文字的高度
                Rectangle().fill(pal.border).frame(width: 3)
                inlineText(lines.joined(separator: "\n"))
                    .font(.system(size: 12))
                    .foregroundColor(pal.textSecondary)
            }
            .fixedSize(horizontal: false, vertical: true)

        case let .code(_, code):
            ScrollView(.horizontal, showsIndicators: false) {
                Text(code)
                    .font(.system(size: 12, design: .monospaced))
                    .foregroundColor(pal.textPrimary)
                    .padding(10)
            }
            .background(pal.fill)
            .clipShape(RoundedRectangle(cornerRadius: 6, style: .continuous))

        case let .table(header, rows):
            ScrollView(.horizontal, showsIndicators: false) {
                VStack(alignment: .leading, spacing: 0) {
                    tableRow(header, bold: true)
                    Rectangle().fill(pal.border).frame(height: 1)
                    ForEach(Array(rows.enumerated()), id: \.offset) { row in
                        tableRow(row.element, bold: false)
                    }
                }
            }

        case .divider:
            Rectangle().fill(pal.border).frame(height: 1)
        }
    }

    private func tableRow(_ cells: [String], bold: Bool) -> some View {
        HStack(alignment: .top, spacing: 0) {
            ForEach(Array(cells.enumerated()), id: \.offset) { cell in
                inlineText(cell.element)
                    .font(.system(size: 12, weight: bold ? .semibold : .regular))
                    .foregroundColor(pal.textPrimary)
                    .frame(width: 96, alignment: .leading)
                    .padding(.vertical, 4)
                    .padding(.horizontal, 4)
            }
        }
    }

    // MARK: - 行内

    /// 行内片段 → `Text`（拼接）。
    ///
    /// ## 为什么是拼接而不是 `AttributedString`
    ///
    /// `Text(AttributedString)` 在 SwiftUI 里**不支持背景色**（行内代码的浅灰底会静默丢掉），
    /// 而 `Text + Text` 的拼接是稳定可用的。代价是行内代码只能靠**等宽 + 变色**区分，
    /// 没有底色——这是 Apple 侧的渲染限制，不是语义缺失（块的划分与 Android 完全一致）。
    private func inlineText(_ text: String) -> Text {
        SekbMarkdown.inline(text).reduce(Text("")) { acc, span in
            switch span {
            case let .text(t):
                return acc + Text(t)
            case let .bold(t):
                return acc + Text(t).fontWeight(.semibold)
            case let .italic(t):
                return acc + Text(t).italic()
            case let .code(t):
                return acc + Text(t)
                    .font(.system(size: 12.5, design: .monospaced))
                    .foregroundColor(pal.primaryDark)
            case let .link(t, _):
                // 链接只显示文字 + 主题色：端侧场景里点开外链的机会很少，
                // 而无浏览器/无网络时点开是静默失败，不如不点（与 Android 同一取舍）
                return acc + Text(t).foregroundColor(pal.primary)
            }
        }
    }
}
