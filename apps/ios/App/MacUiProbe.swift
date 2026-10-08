#if os(macOS)
import SwiftUI
import AppKit

/// **macOS 视觉验证**：把一个真实的 `NSWindow` + `NSHostingView(AppShell)` 渲染成 PNG
/// （`SEKB_UI_PROBE=1` 时启用，默认关闭）。
///
/// ## 为什么不是 `screencapture`
///
/// 本机实测 `screencapture -x` 直接失败：
/// ```
/// could not create image from display
/// ```
/// 那是"屏幕录制"权限被拒（受限环境里拿不到）。而"Mac 侧完全没看过布局"是不可接受的——
/// 本仓库在"只看编译过就宣称能用"上吃过亏。
///
/// ## 为什么不用 `ImageRenderer`（第一版用了它，实测不够）
///
/// `ImageRenderer` 走的是**离屏快照**那条路，实测它**不画 `ScrollView` 的内容**：
/// 顶栏、输入栏都在，中间的消息区/列表区一片空白（四个页面的截图都是这样）。
/// 那样得到的"证据"会让人以为页面是空的——比没有截图更糟。
///
/// 现在改成：建一个真的 `NSWindow`，把 `AppShell` 放进 `NSHostingView`，
/// 让它跑一轮布局与绘制（`RunLoop` 前进 ~0.8s），再用 `cacheDisplay` 把视图树抓成位图。
/// 这条路会经过 AppKit 的真实绘制流程，`ScrollView` 的内容与 `TextField` 都能画出来。
/// 仍然不是操作系统级截图（不含窗口标题栏/阴影），这一点在验收文档里写清楚。
///
/// 渲染哪些状态由**已有的两个环境变量**决定（`SEKB_INITIAL_SCREEN` / `SEKB_DEMO_SEED`），
/// 与 iOS 侧的 `ios_app.sh ui` 用的是同一套开关——两端同一份源码、同一套视觉验证方法。
enum MacUiProbe {

    static var isRequested: Bool { env("SEKB_UI_PROBE") == "1" }

    /// 返回 `true` = 已经处理完并应当**跳过后台自检**（探针跑完自行退出）。
    static func runIfRequested() -> Bool {
        guard isRequested else { return false }
        return MainActor.assumeIsolated { render() }
    }

    @MainActor
    private static func render() -> Bool {
        let out = env("SEKB_UI_PROBE_OUT") ?? "\(NSTemporaryDirectory())/sekb-mac-ui.png"
        let width = CGFloat(Double(env("SEKB_UI_PROBE_WIDTH") ?? "") ?? 460)
        let height = CGFloat(Double(env("SEKB_UI_PROBE_HEIGHT") ?? "") ?? 860)
        let page = env("SEKB_INITIAL_SCREEN") ?? "chat"

        var ok = false
        var detail = ""

        let hosting = NSHostingView(rootView: AppShell().sekbTheme()
            .frame(width: width, height: height))
        hosting.frame = NSRect(x: 0, y: 0, width: width, height: height)

        let window = NSWindow(contentRect: hosting.frame,
                              styleMask: [.titled, .closable],
                              backing: .buffered,
                              defer: false)
        window.contentView = hosting
        window.title = "SEKB UI probe (\(page))"
        window.orderFrontRegardless()
        hosting.layoutSubtreeIfNeeded()
        // 让布局与绘制真的跑一轮（ScrollView 的内容要等这一轮之后才在位图上）
        RunLoop.current.run(until: Date().addingTimeInterval(0.8))
        hosting.displayIfNeeded()

        if let rep = hosting.bitmapImageRepForCachingDisplay(in: hosting.bounds) {
            hosting.cacheDisplay(in: hosting.bounds, to: rep)
            if let png = rep.representation(using: .png, properties: [:]) {
                do {
                    try png.write(to: URL(fileURLWithPath: out))
                    ok = true
                    detail = "\(rep.pixelsWide)x\(rep.pixelsHigh) px · 窗口 \(Int(width))x\(Int(height)) pt"
                        + " · \(png.count) bytes"
                } catch {
                    detail = "写盘失败：\(error)"
                }
            } else {
                detail = "PNG 编码失败"
            }
        } else {
            detail = "bitmapImageRepForCachingDisplay 返回空"
        }

        print("\(SekbPlatform.selfTestPrefix)_UI_PROBE page=\(page) ok=\(ok) out=\(out) \(detail)")
        fflush(stdout)
        exit(ok ? 0 : 1)
    }

    private static func env(_ key: String) -> String? {
        ProcessInfo.processInfo.environment[key]
    }
}
#endif
