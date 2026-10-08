import SwiftUI
import SharedCore

/// 设置页：把配置、隐私审计、自检分组收进来。
///
/// 分组顺序按"用户会不会动它"排：
/// 1. 账号与设备——最常见的动作（第一次用必须接入）；
/// 2. 连接——只有联调/自建后端才改，默认折叠；
/// 3. 检索——阈值是**会影响答对/答错**的参数，所以给上标定过的默认值与解释；
/// 4. 端侧嵌入——模型/空间戳/维度（答"检索为什么是这个结果"的第一手信息）；
/// 5. 隐私与权限——不是配置而是**知情权**，所以放在显眼处但只读；
/// 6. 自检——一条命令/一个入口跑自检（F9），并可重跑；
/// 7. 关于——版本与策略。
struct SettingsScreen: View {

    @Environment(\.sekb) private var pal

    @ObservedObject var viewModel: SekbViewModel
    let onBack: () -> Void

    @State private var advanced = false
    @State private var threshold: Double = SekbDefaults.retrievalMinScore
    @State private var didLoadThreshold = false
    @State private var showSelfTestDetail = false

    var body: some View {
        VStack(spacing: 0) {
            SekbTopBar(title: "设置", onBack: onBack) { EmptyView() }

            ScrollView {
                VStack(spacing: 12) {
                    accountCard
                    connectionCard
                    retrievalCard
                    embeddingCard
                    privacyCard
                    selfTestCard
                    aboutCard
                }
                .padding(12)
            }
        }
        .onAppear {
            if !didLoadThreshold {
                threshold = viewModel.currentThreshold
                didLoadThreshold = true
            }
        }
    }

    // MARK: - 分组容器

    private func section<Content: View>(_ title: String,
                                        @ViewBuilder content: () -> Content) -> some View {
        SekbCard {
            VStack(alignment: .leading, spacing: 10) {
                Text(title)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundColor(pal.textSecondary)
                content()
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(14)
        }
    }

    private func statRow(_ label: String, _ value: String) -> some View {
        HStack {
            Text(label).font(.system(size: 13)).foregroundColor(pal.textPrimary)
            Spacer()
            Text(value).font(.system(size: 13, weight: .medium)).foregroundColor(pal.textPrimary)
        }
    }

    // MARK: - 账号与设备

    private var accountCard: some View {
        section("账号与设备") {
            if viewModel.state.enrolled {
                HStack(spacing: 8) {
                    Image(systemName: "checkmark.circle.fill")
                        .font(.system(size: 18))
                        .foregroundColor(pal.onDevice)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("已接入").font(.system(size: 14)).foregroundColor(pal.textPrimary)
                        // 设备 ID 是排障要用的，所以保留，但不占主视觉
                        Text("设备 \(String(viewModel.state.deviceId.prefix(8)))…")
                            .font(.system(size: 11, design: .monospaced))
                            .foregroundColor(pal.textSecondary)
                    }
                    Spacer()
                }
            } else {
                Text("接入后即可使用云端知识库；端侧推理与本地检索不需要接入。")
                    .font(.system(size: 12))
                    .foregroundColor(pal.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                labeledField("账号", text: Binding(
                    get: { viewModel.state.email }, set: { viewModel.onEmailChange($0) }),
                    secure: false)
                labeledField("密码", text: Binding(
                    get: { viewModel.state.password }, set: { viewModel.onPasswordChange($0) }),
                    secure: true)
                Button {
                    viewModel.enroll()
                } label: {
                    Text(viewModel.state.busy ? "接入中…" : "接入设备")
                        .font(.system(size: 14, weight: .medium))
                        .foregroundColor(.white)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 9)
                        .background(viewModel.state.busy ? pal.border : pal.primary)
                        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                }
                .buttonStyle(.plain)
                .disabled(viewModel.state.busy)
            }
        }
    }

    private func labeledField(_ label: String, text: Binding<String>, secure: Bool) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label).font(.system(size: 11)).foregroundColor(pal.textSecondary)
            Group {
                if secure {
                    // 必须打码：明文密码在截图/投屏/旁人一瞥下就直接泄漏
                    SecureField("", text: text)
                } else {
                    TextField("", text: text)
                }
            }
            .textFieldStyle(.plain)
            .font(.system(size: 13))
            .foregroundColor(pal.textPrimary)
            .padding(.horizontal, 10)
            .padding(.vertical, 7)
            .background(pal.fill)
            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        }
    }

    // MARK: - 连接

    private var connectionCard: some View {
        section("连接") {
            HStack(spacing: 8) {
                Circle()
                    .fill(connectionDotColor)
                    .frame(width: 6, height: 6)
                Text(connectionText)
                    .font(.system(size: 13))
                    .foregroundColor(pal.textPrimary)
                Spacer()
                Button("重新检查") { viewModel.probeEdge() }
                    .buttonStyle(.plain)
                    .font(.system(size: 12))
                    .foregroundColor(pal.primary)
            }
            Button(advanced ? "收起高级设置" : "高级设置") { advanced.toggle() }
                .buttonStyle(.plain)
                .font(.system(size: 12))
                .foregroundColor(pal.primary)
            if advanced {
                labeledField("端侧推理地址", text: Binding(
                    get: { viewModel.state.edgeUrl }, set: { viewModel.onEdgeUrlChange($0) }),
                    secure: false)
                labeledField("云端知识库地址", text: Binding(
                    get: { viewModel.state.sekbUrl }, set: { viewModel.onSekbUrlChange($0) }),
                    secure: false)
                Button {
                    viewModel.applyConfig()
                } label: {
                    Text("应用地址")
                        .font(.system(size: 13, weight: .medium))
                        .foregroundColor(.white)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 8)
                        .background(pal.primary)
                        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                }
                .buttonStyle(.plain)
            }
        }
    }

    private var connectionDotColor: Color {
        switch viewModel.state.edgeReachable {
        case .some(true): return pal.onDevice
        case .some(false): return pal.cloud
        case .none: return pal.textSecondary
        }
    }

    private var connectionText: String {
        switch viewModel.state.edgeReachable {
        case .some(true): return "端侧可用：推理在本机完成"
        case .some(false): return "端侧不可用：将改道云端"
        case .none: return "正在检查端侧…"
        }
    }

    // MARK: - 检索阈值

    /// 阈值不是"随便一个数"：0.40 是**在当前实际加载的嵌入空间上标定出来的**
    /// （fp32：0.40 → Hit@1 87% / 误召回 0/3；0.20 时误召回 3/3，那是幻觉的来源）。
    /// 所以这里既给滑块，也把"为什么是这个值"写在界面上——参数可解释，用户才敢动。
    private var retrievalCard: some View {
        section("检索") {
            statRow("当前阈值", String(format: "%.2f", threshold))
            statRow("来源", viewModel.thresholdSourceText)
            Slider(value: $threshold,
                   in: SekbDefaults.retrievalMinScoreRange,
                   step: SekbDefaults.retrievalMinScoreStep) { editing in
                if !editing { viewModel.applyThreshold(threshold) }
            }
            .tint(pal.primary)
            Text("默认 0.40 是按本机实际加载的嵌入空间标定的：0.40 时命中率不降而"
                 + "「库里没有答案」的误召回为 0；调低会开始硬答，调高会开始漏答。"
                 + "改动会立即重建检索器与工具（阈值是构造参数）。")
                .font(.system(size: 11))
                .foregroundColor(pal.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
            Button("恢复默认 0.40") {
                threshold = SekbDefaults.retrievalMinScore
                viewModel.applyThreshold(SekbDefaults.retrievalMinScore)
            }
            .buttonStyle(.plain)
            .font(.system(size: 12))
            .foregroundColor(pal.primary)
        }
    }

    // MARK: - 端侧嵌入

    private var embeddingCard: some View {
        section("端侧嵌入") {
            Text(viewModel.providerDetail)
                .font(.system(size: 12))
                .foregroundColor(pal.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
            Text("空间戳决定两份向量能不能混用：换模型必须重建/重嵌入索引，"
                 + "否则会返回「看起来相关但其实错」的结果。")
                .font(.system(size: 11))
                .foregroundColor(pal.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: - 隐私与权限

    private var privacyCard: some View {
        section("隐私与权限") {
            statRow("设备工具调用", "\(viewModel.state.auditTotal) 次")
            statRow("被权限闸门拦截", "\(viewModel.state.auditDenied) 次")
            statRow("越权拦截率", viewModel.state.deniedRateText)
            Text(viewModel.state.toolEvalSummary)
                .font(.system(size: 11))
                .foregroundColor(pal.textSecondary)
            Button("清空统计") { viewModel.clearAudit() }
                .buttonStyle(.plain)
                .font(.system(size: 12))
                .foregroundColor(pal.primary)
        }
    }

    // MARK: - 自检

    private var selfTestCard: some View {
        section("自检") {
            HStack(spacing: 8) {
                Text(viewModel.selfTest.results.isEmpty
                     ? "还没有跑过自检" : viewModel.selfTest.summary)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundColor(selfTestColor)
                Spacer()
                Button(viewModel.selfTest.running ? "自检中…" : "重新运行") {
                    viewModel.rerunSelfTest()
                }
                .buttonStyle(.plain)
                .font(.system(size: 12))
                .foregroundColor(pal.primary)
                .disabled(viewModel.selfTest.running)
            }
            if !viewModel.selfTest.results.isEmpty {
                Button(showSelfTestDetail ? "收起明细" : "展开明细（\(viewModel.selfTest.results.count) 项）") {
                    showSelfTestDetail.toggle()
                }
                .buttonStyle(.plain)
                .font(.system(size: 12))
                .foregroundColor(pal.primary)
                if showSelfTestDetail {
                    VStack(alignment: .leading, spacing: 6) {
                        ForEach(viewModel.selfTest.results) { r in
                            HStack(alignment: .top, spacing: 6) {
                                Image(systemName: r.ok == nil ? "questionmark.circle"
                                      : (r.ok! ? "checkmark.circle.fill" : "xmark.circle.fill"))
                                    .font(.system(size: 11))
                                    .foregroundColor(r.ok == nil ? pal.cloud : (r.ok! ? pal.onDevice : pal.danger))
                                VStack(alignment: .leading, spacing: 1) {
                                    Text(r.name)
                                        .font(.system(size: 11, design: .monospaced))
                                        .foregroundColor(pal.textPrimary)
                                    Text(r.detail)
                                        .font(.system(size: 10))
                                        .foregroundColor(pal.textSecondary)
                                        .fixedSize(horizontal: false, vertical: true)
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    private var selfTestColor: Color {
        let st = viewModel.selfTest
        if st.results.isEmpty { return pal.textSecondary }
        return st.fail > 0 ? pal.danger : (st.skip > 0 ? pal.cloud : pal.onDevice)
    }

    // MARK: - 关于

    private var aboutCard: some View {
        section("关于") {
            statRow("版本", SekbDefaults.appVersion)
            statRow("平台", SekbPlatform.displayName)
            statRow("策略版本", viewModel.policyVersionText())
            Text("最近一次策略：\(viewModel.lastPolicyText)")
                .font(.system(size: 11))
                .foregroundColor(pal.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
            Text("文档索引与向量都存在这台设备上；标记为「设备专属」的内容不会被上传。")
                .font(.system(size: 11))
                .foregroundColor(pal.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}
