import SwiftUI

/// 从 `0xRRGGBB` 构造颜色（配色表照抄 Android 侧的字面值，写十六进制最不容易抄错）。
extension Color {
    init(hex: UInt32, opacity: Double = 1.0) {
        self.init(
            .sRGB,
            red: Double((hex >> 16) & 0xFF) / 255.0,
            green: Double((hex >> 8) & 0xFF) / 255.0,
            blue: Double(hex & 0xFF) / 255.0,
            opacity: opacity
        )
    }
}

/// SEKB 端侧宿主的配色。
///
/// ## 为什么不用系统默认配色
///
/// 网页端用 Ant Design 5 的蓝 `#1677FF`；端侧是同一个产品的另一个入口，
/// 配色不一致会让人以为是两个东西。所以主色、页面底色、语义色都对齐网页端 / Android
/// （`apps/android/app/src/main/kotlin/com/sekb/ondevice/ui/theme/SekbTheme.kt` 里的同一批值）。
///
/// ## 语义色为什么不是装饰
///
/// 端侧产品有一条硬边界：**设备专属数据不许出端**。所以"这次在哪算的"必须一眼可辨——
/// `onDevice`（绿）/ `cloud`（金）只服务于这一件事，**不允许在界面其它地方复用**
/// （否则"绿色"就不再等于"在本机算的"，承诺就退化成装饰）。
struct SekbPalette {
    let primary: Color
    let primaryContainer: Color
    let primaryDark: Color
    let bgLayout: Color
    let card: Color
    let fill: Color
    let border: Color
    let textPrimary: Color
    let textSecondary: Color
    /// 语义：本机完成（AntD green-7）
    let onDevice: Color
    /// 语义：已上云 / 端侧不可用（AntD gold-7）
    let cloud: Color
    /// 语义：失败（AntD red-7）
    let danger: Color

    /// 亮色（= Android `LightScheme` + `LightSemantic`）
    static let light = SekbPalette(
        primary: Color(hex: 0x1677FF),
        primaryContainer: Color(hex: 0xE6F0FF),
        primaryDark: Color(hex: 0x0958D9),
        bgLayout: Color(hex: 0xF5F7FA),
        card: Color(hex: 0xFFFFFF),
        fill: Color(hex: 0xF0F2F5),
        border: Color(hex: 0xE5E7EB),
        textPrimary: Color(hex: 0x1F2329),
        textSecondary: Color(hex: 0x8C8C8C),
        onDevice: Color(hex: 0x389E0D),
        cloud: Color(hex: 0xD48806),
        danger: Color(hex: 0xD4380D)
    )

    /// 暗色（= Android `DarkScheme` + `DarkSemantic`）。
    ///
    /// ⚠️ 必须**整套**切，不能只切 `bgLayout`：Android 侧踩过一次真实缺陷——
    /// 深色模式下页面仍用亮色常量（输入框近白、边框看不见），"深色模式"只在主题文件里成立。
    /// 所以这里所有颜色都走 `palette`，页面里**不出现**任何硬编码色值。
    static let dark = SekbPalette(
        primary: Color(hex: 0x4C9AFF),
        primaryContainer: Color(hex: 0x11304F),
        primaryDark: Color(hex: 0xD6E8FF),
        bgLayout: Color(hex: 0x14161A),
        card: Color(hex: 0x1C1F24),
        fill: Color(hex: 0x262A31),
        border: Color(hex: 0x3A3F47),
        textPrimary: Color(hex: 0xE6E8EB),
        textSecondary: Color(hex: 0x9BA1A9),
        // 深色底上要提亮：直接用亮色的绿/金会糊在背景里
        onDevice: Color(hex: 0x73D13D),
        cloud: Color(hex: 0xFFC53D),
        danger: Color(hex: 0xFF7875)
    )
}

private struct SekbPaletteKey: EnvironmentKey {
    static let defaultValue: SekbPalette = .light
}

extension EnvironmentValues {
    /// 当前配色。页面一律 `@Environment(\.sekb) private var pal` 取用。
    var sekb: SekbPalette {
        get { self[SekbPaletteKey.self] }
        set { self[SekbPaletteKey.self] = newValue }
    }
}

/// 把系统明暗映射到 [SekbPalette]。**一份源码两端跑**，所以不引 UIKit/AppKit。
struct SekbThemeModifier: ViewModifier {
    @Environment(\.colorScheme) private var scheme

    func body(content: Content) -> some View {
        content.environment(\.sekb, scheme == .dark ? SekbPalette.dark : SekbPalette.light)
    }
}

extension View {
    /// 装在根视图上（`AppShell` 与自检页各装一次）。
    func sekbTheme() -> some View { modifier(SekbThemeModifier()) }
}

/// 白卡片 + 圆角 + 细边框（Android 侧所有列表项/分组都是这个形状）。
struct SekbCard<Content: View>: View {
    @Environment(\.sekb) private var pal
    private let content: Content

    init(@ViewBuilder content: () -> Content) {
        self.content = content()
    }

    var body: some View {
        content
            .background(pal.card)
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .stroke(pal.border, lineWidth: 1)
            )
    }
}

/// 圆形图标底（空状态、列表头像用它，与 Android 的 `Box + CircleShape` 对应）。
struct SekbCircleIcon: View {
    @Environment(\.sekb) private var pal
    let systemName: String
    var size: CGFloat = 32
    var iconSize: CGFloat = 17
    var background: Color?
    var tint: Color?

    var body: some View {
        ZStack {
            Circle().fill(background ?? pal.primaryContainer)
            Image(systemName: systemName)
                .font(.system(size: iconSize, weight: .medium))
                .foregroundStyle(tint ?? pal.primary)
        }
        .frame(width: size, height: size)
    }
}
