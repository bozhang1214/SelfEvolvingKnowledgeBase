import SwiftUI

/// 端侧宿主的四个页面（与 Android `SekbScreen` **同名同顺序**）。
///
/// 为什么不上 `NavigationStack`/`TabView`：只有四个页面、层级扁平（聊天 ⇄ 历史/文档/设置），
/// **没有深链接、没有回退栈语义**。为此引入一套路由是把"以后可能需要"提前付成本，
/// 也让 back 行为需要重新学。这里用一个 `enum` + `switch` 就够了——与 Android 的
/// `AppShell` 同一取舍、同一顺序。真长出第四层页面时再换。
enum SekbScreen: String, CaseIterable {
    case chat
    case history
    case documents
    case settings

    var title: String {
        switch self {
        case .chat: return "聊天"
        case .history: return "历史对话"
        case .documents: return "本机知识库"
        case .settings: return "设置"
        }
    }

    /// 启动时停在哪个页面（默认聊天）。
    ///
    /// `SEKB_INITIAL_SCREEN` 是给**视觉验证**用的：无人值守下没法点界面，
    /// 不设它就只能截到首页，另外三页"到底长什么样"就只能靠嘴说。
    /// 与 `SEKB_DEMO_SEED` 同一性质：只读环境变量，默认为聊天页。
    static var initial: SekbScreen {
        let raw = ProcessInfo.processInfo.environment["SEKB_INITIAL_SCREEN"] ?? ""
        return SekbScreen(rawValue: raw) ?? .chat
    }
}

/// 极简导航壳 + 顶栏返回（**一份源码两端跑**，所以不用 iOS 专属的导航栏）。
struct AppShell: View {

    @Environment(\.sekb) private var pal
    @StateObject private var viewModel = SekbViewModel()
    @State private var screen: SekbScreen = SekbScreen.initial
    @State private var toast: String?
    @State private var toastToken = UUID()

    var body: some View {
        ZStack(alignment: .bottom) {
            pal.bgLayout.ignoresSafeArea()

            VStack(spacing: 0) {
                switch screen {
                case .chat:
                    ChatScreen(viewModel: viewModel, onOpen: { screen = $0 })
                case .history:
                    ConversationListScreen(viewModel: viewModel,
                                           onBack: { screen = .chat },
                                           onOpened: { screen = .chat })
                case .documents:
                    DocumentsScreen(viewModel: viewModel, onBack: { screen = .chat })
                case .settings:
                    SettingsScreen(viewModel: viewModel, onBack: { screen = .chat })
                }
            }

            if let toast {
                toastView(toast)
                    .padding(.horizontal, 16)
                    .padding(.bottom, 12)
                    .transition(.opacity)
            }
        }
        // 提示与错误统一走"一条会自己消失的吐司"：Android 侧用 Snackbar，
        // 目的是**不在输入框上方堆小字**（用户读不到也看不懂）。
        .onChange(of: viewModel.state.notice) { _, value in
            guard !value.isEmpty else { return }
            showToast(value)
            viewModel.state.notice = ""
        }
        .onChange(of: viewModel.state.error) { _, value in
            guard !value.isEmpty else { return }
            showToast("⚠️ " + value)
            viewModel.state.error = ""
        }
        .onAppear { viewModel.probeEdge() }
    }

    private func showToast(_ text: String) {
        let token = UUID()
        toastToken = token
        withAnimation { toast = text }
        DispatchQueue.main.asyncAfter(deadline: .now() + 4) {
            // 只让"最后一次"的定时器生效：连续两条提示不会互相提前关掉
            if toastToken == token { withAnimation { toast = nil } }
        }
    }

    private func toastView(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 12.5))
            .foregroundColor(pal.card)
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .background(Color.black.opacity(0.82))
            .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
            .onTapGesture { withAnimation { toast = nil } }
    }
}

/// 顶栏（标题 + 可选副标题 + 可选返回 + 右侧动作）。
///
/// 与 Android 的 `TopAppBar` 同结构：白底、一条细分隔线、标题两行（主标题 + 状态行）。
struct SekbTopBar<Trailing: View>: View {

    @Environment(\.sekb) private var pal

    let title: String
    var subtitle: String? = nil
    var onBack: (() -> Void)? = nil
    @ViewBuilder var trailing: () -> Trailing

    var body: some View {
        HStack(spacing: 6) {
            if let onBack {
                iconButton("chevron.left", help: "返回", action: onBack)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.system(size: 17, weight: .semibold))
                    .foregroundColor(pal.textPrimary)
                    .lineLimit(1)
                if let subtitle, !subtitle.isEmpty {
                    Text(subtitle)
                        .font(.system(size: 11))
                        .foregroundColor(pal.textSecondary)
                        .lineLimit(1)
                }
            }
            Spacer(minLength: 4)
            trailing()
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .background(pal.card)
        .overlay(Rectangle().fill(pal.border).frame(height: 1), alignment: .bottom)
    }

    private func iconButton(_ systemName: String, help: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: systemName)
                .font(.system(size: 16, weight: .medium))
                .foregroundColor(pal.textPrimary)
                .frame(width: 32, height: 32)
        }
        .buttonStyle(.plain)
        .help(help)
        .accessibilityLabel(help)
    }
}

/// 顶栏右侧的图标按钮（带角标）。
struct SekbIconButton: View {

    @Environment(\.sekb) private var pal

    let systemName: String
    let help: String
    var badge: Int = 0
    var tint: Color? = nil
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            ZStack(alignment: .topTrailing) {
                Image(systemName: systemName)
                    .font(.system(size: 16, weight: .medium))
                    .foregroundColor(tint ?? pal.textPrimary)
                    .frame(width: 32, height: 32)
                if badge > 0 {
                    Text("\(badge)")
                        .font(.system(size: 9, weight: .semibold))
                        .foregroundColor(.white)
                        .padding(.horizontal, 4)
                        .padding(.vertical, 1)
                        .background(pal.danger)
                        .clipShape(Capsule())
                        .offset(x: 2, y: -2)
                }
            }
        }
        .buttonStyle(.plain)
        .help(help)
        .accessibilityLabel(help)
    }
}
