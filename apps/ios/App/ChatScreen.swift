import SwiftUI
import UniformTypeIdentifiers
import SharedCore

/// 聊天页（用户版）。
///
/// ## 与"调试台"的区别（照 Android 的同一取舍）
///
/// - **首页只有聊天**：配置、审计、文档列表各自进独立页面（顶栏入口）；
/// - **不出现裸 URL / 裸设备 ID**：状态用一句话 + 一个圆点表达（"端侧就绪"/"仅云端可用"）；
/// - **执行位置仍然可见但不刺眼**：气泡下方的小徽章（"本机完成"/"云端完成"/"已上云"）——
///   它是产品承诺（设备专属数据不出端），不是调试信息，所以**不能删**，只能弱化；
/// - **报错与提示走吐司**，不在输入框上方堆小字。
struct ChatScreen: View {

    @Environment(\.sekb) private var pal

    @ObservedObject var viewModel: SekbViewModel
    let onOpen: (SekbScreen) -> Void

    @State private var showImporter = false

    var body: some View {
        VStack(spacing: 0) {
            SekbTopBar(title: viewModel.state.conversationTitle.isEmpty
                            ? "SEKB" : viewModel.state.conversationTitle,
                       subtitle: edgeStatusText) {
                // 历史入口常驻：它是"存了要能拿回来"的唯一路径，藏起来等于没存
                SekbIconButton(systemName: "clock.arrow.circlepath", help: "历史对话") {
                    viewModel.refreshConversations()
                    onOpen(.history)
                }
                // 已有对话时才给"新对话"：空对话给这个按钮没有意义
                if !viewModel.state.bubbles.isEmpty {
                    SekbIconButton(systemName: "square.and.pencil", help: "新对话") {
                        viewModel.newConversation()
                    }
                }
                SekbIconButton(systemName: "doc.text", help: "本机知识库",
                               badge: viewModel.state.documents.count) {
                    viewModel.refreshDocuments()
                    onOpen(.documents)
                }
                SekbIconButton(systemName: "gearshape", help: "设置") {
                    viewModel.refreshAudit()
                    onOpen(.settings)
                }
            }

            // ⚠️ 消息区必须是**可伸缩**的那一块（`maxHeight: .infinity`），
            // 不能让空状态去"要"全部高度——Android 真机上正是这个 bug：
            // 空状态 `fillMaxSize()` 把底部输入栏挤出屏幕，整屏只有空状态、用户没法输入。
            Group {
                if viewModel.state.bubbles.isEmpty {
                    emptyState
                } else {
                    bubbleList
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)

            if viewModel.state.busy {
                thinkingRow
            }

            inputBar
        }
        .fileImporter(isPresented: $showImporter,
                      allowedContentTypes: [.plainText, .pdf, .json, .commaSeparatedText],
                      allowsMultipleSelection: true) { result in
            switch result {
            case let .success(urls):
                for url in urls { viewModel.importDocument(url: url) }
            case let .failure(error):
                viewModel.state.error = "选择文件失败：\(error.localizedDescription)"
            }
        }
    }

    // MARK: - 状态行

    /// 一句话状态 + 圆点：不给用户看 URL，但必须让他知道"端侧到底能不能用"。
    private var edgeStatusText: String {
        let st = viewModel.state
        if !st.enrolled { return "未接入云端账号" }
        switch st.edgeReachable {
        case .some(true): return "端侧就绪 · 数据不出本机"
        case .some(false): return "端侧不可用 · 将使用云端"
        case .none: return "正在检查端侧…"
        }
    }

    private var edgeStatusColor: Color {
        let st = viewModel.state
        if !st.enrolled { return pal.textSecondary }
        switch st.edgeReachable {
        case .some(true): return pal.onDevice
        case .some(false): return pal.cloud
        case .none: return pal.textSecondary
        }
    }

    // MARK: - 空状态

    /// 空状态：给"该问什么"一点提示。
    ///
    /// 这三个问题**都是这台设备真的答得了的**（分别对应端侧模型知识、本机知识库、设备工具），
    /// 不是装饰性文案——点了就会填进输入框，用户能立刻得到一次成功体验。
    private var emptyState: some View {
        let samples = [
            "端侧推理为什么更省电？",
            "我本机的知识库里有什么？",
            "现在几点了？",
        ]
        // 用 ScrollView 包住：小屏/横屏时内容再高也不会把输入栏顶出屏幕
        return ScrollView {
            VStack(spacing: 0) {
                SekbCircleIcon(systemName: "bubble.left.and.bubble.right.fill", size: 56, iconSize: 26)
                Text("SEKB 端侧助手")
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundColor(pal.textPrimary)
                    .padding(.top, 14)
                Text("文档与推理都在这台设备上完成，设备专属内容不会离开本机。")
                    .font(.system(size: 12))
                    .foregroundColor(pal.textSecondary)
                    .multilineTextAlignment(.center)
                    .padding(.top, 6)
                VStack(spacing: 8) {
                    ForEach(samples, id: \.self) { q in
                        Button {
                            viewModel.onInputChange(q)
                        } label: {
                            Text(q)
                                .font(.system(size: 14))
                                .foregroundColor(pal.textPrimary)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(.horizontal, 14)
                                .padding(.vertical, 11)
                                .background(pal.card)
                                .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                                .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous)
                                    .stroke(pal.border, lineWidth: 1))
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(.top, 20)
            }
            .padding(24)
            .frame(maxWidth: .infinity)
        }
    }

    // MARK: - 气泡列表

    private var bubbleList: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(spacing: 12) {
                    ForEach(viewModel.state.bubbles) { bubble in
                        BubbleRow(bubble: bubble).id(bubble.id)
                    }
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 12)
            }
            .onChange(of: viewModel.state.bubbles.count) { _, _ in
                if let last = viewModel.state.bubbles.last {
                    withAnimation(.easeOut(duration: 0.15)) {
                        proxy.scrollTo(last.id, anchor: .bottom)
                    }
                }
            }
        }
    }

    /// 生成中的提示：让"在算"这件事可见（端侧推理没有网络往返，不给反馈会让人以为卡住）。
    private var thinkingRow: some View {
        HStack(spacing: 8) {
            ProgressView().controlSize(.mini)
            Text(viewModel.state.thinking.isEmpty ? "正在本机生成…" : viewModel.state.thinking)
                .font(.system(size: 12))
                .foregroundColor(pal.textSecondary)
            Spacer()
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 4)
    }

    // MARK: - 输入栏

    private var inputBar: some View {
        HStack(alignment: .bottom, spacing: 8) {
            Button {
                showImporter = true
            } label: {
                Image(systemName: "plus.circle")
                    .font(.system(size: 20))
                    .foregroundColor(pal.textSecondary)
                    .frame(width: 32, height: 32)
            }
            .buttonStyle(.plain)
            .help("导入本机文档")
            .accessibilityLabel("导入本机文档")

            TextField("问点什么…", text: Binding(
                get: { viewModel.state.input },
                set: { viewModel.onInputChange($0) }), axis: .vertical)
                .textFieldStyle(.plain)
                .font(.system(size: 14))
                .foregroundColor(pal.textPrimary)
                .lineLimit(1...4)
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .background(pal.fill)
                .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
                .onSubmit { if canSend { viewModel.send() } }

            Button {
                viewModel.send()
            } label: {
                Image(systemName: "paperplane.fill")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundColor(.white)
                    .frame(width: 34, height: 34)
                    .background(canSend ? pal.primary : pal.border)
                    .clipShape(Circle())
            }
            .buttonStyle(.plain)
            .disabled(!canSend)
            .help("发送")
            .accessibilityLabel("发送")
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .background(pal.card)
        .overlay(Rectangle().fill(pal.border).frame(height: 1), alignment: .top)
    }

    private var canSend: Bool {
        !viewModel.state.input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && !viewModel.state.busy
    }
}

/// 一条气泡：用户右（主色）/ 助手左（白卡 + 边框）。
private struct BubbleRow: View {

    @Environment(\.sekb) private var pal

    let bubble: ChatBubble

    @State private var showSources = false
    @State private var showExplain = false

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            if !bubble.fromUser {
                SekbCircleIcon(systemName: "cpu", size: 30)
            }
            if bubble.fromUser { Spacer(minLength: 24) }

            VStack(alignment: bubble.fromUser ? .trailing : .leading, spacing: 4) {
                bubbleBody
                if !bubble.sources.isEmpty { sourcesRow }
                executionBadge
            }
            .frame(maxWidth: 460, alignment: bubble.fromUser ? .trailing : .leading)

            if !bubble.fromUser { Spacer(minLength: 24) }
            if bubble.fromUser {
                SekbCircleIcon(systemName: "iphone", size: 30,
                               background: pal.fill, tint: pal.textSecondary)
            }
        }
        .frame(maxWidth: .infinity,
               alignment: bubble.fromUser ? .trailing : .leading)
    }

    @ViewBuilder
    private var bubbleBody: some View {
        if bubble.fromUser {
            Text(bubble.text.isEmpty ? " " : bubble.text)
                .font(.system(size: 14))
                .foregroundColor(.white)
                .padding(.horizontal, 13)
                .padding(.vertical, 10)
                .background(pal.primary)
                .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        } else {
            // 用户自己的话按纯文本显示；**助手的回答必须渲染 Markdown**——
            // 模型输出里带 `# 标题` / `**粗体**` / 表格，原样显示就是给用户看源码
            SekbMarkdownView(text: bubble.text.isEmpty ? " " : bubble.text)
                .padding(.horizontal, 13)
                .padding(.vertical, 10)
                .background(pal.card)
                .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .stroke(pal.border, lineWidth: 1))
        }
    }

    /// 引用来源：默认收起，只给一句"引用了 N 段本机资料"——展开才能看到出处与分数。
    private var sourcesRow: some View {
        VStack(alignment: .leading, spacing: 4) {
            Button {
                showSources.toggle()
            } label: {
                HStack(spacing: 3) {
                    Text("引用 \(bubble.sources.count) 段本机资料")
                        .font(.system(size: 11))
                        .foregroundColor(pal.primary)
                    Image(systemName: showSources ? "chevron.up" : "chevron.down")
                        .font(.system(size: 9, weight: .semibold))
                        .foregroundColor(pal.primary)
                }
            }
            .buttonStyle(.plain)

            if showSources {
                SekbCard {
                    VStack(alignment: .leading, spacing: 8) {
                        ForEach(bubble.sources) { s in
                            VStack(alignment: .leading, spacing: 2) {
                                HStack(spacing: 6) {
                                    Text(s.sourceId).font(.system(size: 12, weight: .medium))
                                        .foregroundColor(pal.textPrimary)
                                    Text(String(format: "%.2f", s.score))
                                        .font(.system(size: 11))
                                        .foregroundColor(pal.textSecondary)
                                }
                                Text(s.snippet)
                                    .font(.system(size: 11))
                                    .foregroundColor(pal.textSecondary)
                                    .lineLimit(2)
                            }
                        }
                    }
                    .padding(10)
                }
            }
        }
    }

    /// 执行位置徽章——**产品承诺的可视化**。
    ///
    /// 绿色 = 在本机算的；金色 = 用到了云端。这个区分是端侧产品的核心卖点，
    /// 所以每一条回答都要标，但做成小字弱化，不抢内容。
    @ViewBuilder
    private var executionBadge: some View {
        if !bubble.fromUser && !bubble.badge.isEmpty {
            let color = bubble.completedOnDevice ? pal.onDevice : pal.cloud
            let explain = bubble.explanation
            let model = (bubble.execution?.model ?? "").isEmpty ? nil : bubble.execution?.model
            VStack(alignment: .leading, spacing: 3) {
                Button {
                    if !explain.isEmpty { showExplain.toggle() }
                } label: {
                    HStack(spacing: 4) {
                        Image(systemName: bubble.completedOnDevice ? "iphone" : "cloud")
                            .font(.system(size: 9, weight: .semibold))
                        Text(bubble.badge + (model.map { " · \($0)" } ?? ""))
                            .font(.system(size: 11))
                        if !explain.isEmpty {
                            Image(systemName: showExplain ? "chevron.up" : "chevron.down")
                                .font(.system(size: 8, weight: .semibold))
                        }
                    }
                    .foregroundColor(color)
                }
                .buttonStyle(.plain)

                if showExplain && !explain.isEmpty {
                    // 徽章必须能自己解释"为什么"：只给"已上云（端侧不达标）"这种结论，
                    // 用户（正确地）会追问"我的数据出去了吗、为什么"——而这正是端侧产品的核心承诺。
                    Text(explain)
                        .font(.system(size: 11))
                        .foregroundColor(pal.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.leading, 16)
                }
            }
        }
    }
}
