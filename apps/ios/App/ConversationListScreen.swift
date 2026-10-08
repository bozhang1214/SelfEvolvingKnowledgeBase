import SwiftUI
import SharedCore

/// 历史对话列表（会话持久化的 Phase 2）。
///
/// ## 为什么必须有这一页
///
/// Phase 1 只做到"重启恢复**最近一次**会话"——更早的对话其实**已经落盘**，
/// 但用户看不到、也点不进去。**存了却拿不出来等于没存**：用户会觉得"历史没了"，
/// 而文件明明还在。这一页就是把已存的东西变成可访问的。
///
/// 删除**不做二次确认**：会话只是本地记录、不涉及数据丢失风险，而确认弹窗会让
/// 清理多条旧对话变得很烦。删当前正在聊的那条时会把界面一并清空
/// （见 `SekbViewModel.deleteConversation`），避免"看着已删的对话继续提问"。
struct ConversationListScreen: View {

    @Environment(\.sekb) private var pal

    @ObservedObject var viewModel: SekbViewModel
    let onBack: () -> Void
    let onOpened: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            SekbTopBar(
                title: "历史对话",
                subtitle: viewModel.state.conversations.isEmpty
                    ? "本机保存的对话"
                    : "\(viewModel.state.conversations.count) 段 · 都存在这台设备上",
                onBack: onBack) { EmptyView() }

            if viewModel.state.conversations.isEmpty {
                emptyState
            } else {
                ScrollView {
                    LazyVStack(spacing: 8) {
                        ForEach(viewModel.state.conversations, id: \.id) { c in
                            row(c)
                        }
                    }
                    .padding(12)
                }
            }
        }
        .onAppear { viewModel.refreshConversations() }
    }

    private var emptyState: some View {
        VStack(spacing: 0) {
            Spacer()
            SekbCircleIcon(systemName: "clock.arrow.circlepath", size: 56, iconSize: 26)
            Text("还没有历史对话")
                .font(.system(size: 16, weight: .semibold))
                .foregroundColor(pal.textPrimary)
                .padding(.top, 14)
            Text("聊过的对话会自动存在这台设备上，重启后也能找回。")
                .font(.system(size: 12))
                .foregroundColor(pal.textSecondary)
                .multilineTextAlignment(.center)
                .padding(.top, 6)
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(32)
    }

    private func row(_ summary: ConversationSummary) -> some View {
        let current = !viewModel.state.conversationTitle.isEmpty
            && summary.title == viewModel.state.conversationTitle
        // ⚠️ 整行**不能**用 `Button` 包住删除按钮：SwiftUI 里嵌套 Button 时，
        // 内层按钮的点击会被外层吞掉（表现是"删除点了没反应"）。这里用 `onTapGesture`
        // 做"点行进会话"，删除按钮保持独立 → 两个动作都能触发。
        return SekbCard {
            HStack(spacing: 10) {
                SekbCircleIcon(systemName: "bubble.left.and.bubble.right.fill", size: 32)
                VStack(alignment: .leading, spacing: 2) {
                    Text(summary.title)
                        .font(.system(size: 14, weight: .medium))
                        .foregroundColor(pal.textPrimary)
                        .lineLimit(1)
                    // 相对时间由 TimeFmt 负责（纯逻辑，与 Android 同一套分段降级）
                    Text(TimeFmt.relative(summary.updatedAtMillis, nowMillis: TimeFmt.nowMillis())
                         + " · \(summary.messageCount) 条消息"
                         + (current ? " · 当前对话" : ""))
                        .font(.system(size: 11))
                        .foregroundColor(pal.textSecondary)
                        .lineLimit(1)
                }
                Spacer(minLength: 4)
                Button {
                    viewModel.deleteConversation(id: summary.id)
                } label: {
                    Image(systemName: "trash")
                        .font(.system(size: 14))
                        .foregroundColor(pal.textSecondary)
                        .frame(width: 30, height: 30)
                }
                .buttonStyle(.plain)
                .help("删除「\(summary.title)」")
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
            .contentShape(Rectangle())
            .onTapGesture {
                viewModel.openConversation(id: summary.id)
                onOpened()
            }
        }
    }
}
