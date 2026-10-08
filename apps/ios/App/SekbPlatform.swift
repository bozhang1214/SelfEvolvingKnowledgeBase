import Foundation

/// **一份源码两端跑**所必需的平台差异，集中在这里。
///
/// 这是本仓库唯一允许出现 `#if os(...)` 的地方（除了 `KeychainCredentialStore` 里
/// 那一处 Keychain 语义差异）。页面与视图层一律不分支——否则"同一份源码"就是幻觉。
enum SekbPlatform {

    static var isMacOS: Bool {
        #if os(macOS)
        return true
        #else
        return false
        #endif
    }

    /// 自检横幅前缀。
    ///
    /// ⚠️ 曾经两端都打印 `SEKB_IOS_SELFTEST`（同一份源码的自然结果），
    /// 而 `scripts/ios_app.sh` 正是按这个前缀抓日志——所以**iOS 侧一个字都不能改**，
    /// macOS 侧换成 `SEKB_MAC_SELFTEST`（`scripts/mac_app.sh` 同步按新前缀抓）。
    static var selfTestPrefix: String { isMacOS ? "SEKB_MAC_SELFTEST" : "SEKB_IOS_SELFTEST" }

    /// enroll 时上报的 `platform` 字段（服务端按它区分端类型）。
    static var apiPlatform: String { isMacOS ? "macos" : "ios" }

    /// 端侧宿主展示名（enroll 的 `name`）。
    static var deviceName: String { isMacOS ? "Mac 端侧宿主" : "iOS 端侧宿主" }

    /// 人类可读的平台名（界面「关于」用）。
    static var displayName: String { isMacOS ? "macOS" : "iOS" }
}

/// 端侧宿主的默认参数（与 Android `app/build.gradle.kts` 的默认值对齐）。
enum SekbDefaults {

    static let appVersion = "0.1.0"

    /// 端侧推理端点。iOS 模拟器与 macOS 都共享宿主网络，所以 `127.0.0.1` 就是宿主机
    /// （Android 模拟器里对应的是 `10.0.2.2`）。
    static let edgeBaseUrl = "http://127.0.0.1:11434/v1"

    /// 云端知识库（与 Android 默认值同一个）。
    static let sekbBaseUrl = "https://bos-studio.tech/sekb"

    /// L1 策略验签密钥（只用于**校验**服务端下发的策略包，不参与加密）。
    /// 与 Android 的 `DEFAULT_EDGE_POLICY_KEY` 同值；生产由部署侧注入。
    static let policySigningKey = "edge-policy:dev-only"

    /// 检索命中阈值。
    ///
    /// ## 为什么这里默认 **0.40**，而 Android 的 `Retriever` 默认 0.5
    ///
    /// 两者不是"各端一个口味"，而是**两套嵌入空间各自的标定点**（`RETRIEVAL-EVAL.md` 的曲线）：
    /// - iOS/Mac 的 `OrtBgeEmbedding.resolveDir()` 取到的是 **fp32** 模型
    ///   （候选目录里 fp32 在前，且两端都随包分发 fp32）；
    /// - fp32 的标注结果是：阈值 **0.40** → Hit@1 26/30(87%)、Hit@3 30/30、误召回 **0/3**；
    ///   0.20 时误召回 3/3（那是幻觉的来源），0.60 时开始伤召回。
    /// - Android 优先加载 **int8**（体积 1/4），它的分数分布整体上移，同一套标注集在
    ///   0.40 下误召回会变 2/3、0.5 下才是 87%/100%/0-3 → 所以 Android 取 0.5。
    ///
    /// 换句话说：**阈值必须按实际加载的那个空间标定**，不能跨空间照抄
    /// （这正是 RFC §18.1 要求把空间戳与阈值绑在一起的原因）。
    /// 设置页允许用户改，改完立即重建检索器（阈值是构造参数，见 `SekbContainer.applyRetrievalThreshold`）。
    static let retrievalMinScore = 0.40

    /// 设置页可调区间（步长 0.05）。上限刻意不给到 0.99：那会变成"任何问题都拒答"。
    static let retrievalMinScoreRange = 0.20...0.80
    static let retrievalMinScoreStep = 0.05
}

/// 应用数据目录的解析。
///
/// ## 为什么需要显式解析（而不是直接写 Application Support）
///
/// 本仓库的端侧构建/运行全部发生在**受限沙箱**里（DSH workspace-write），
/// macOS 与模拟器进程都继承同一套文件策略：实测
/// `mkdir -p ~/Library/Application\ Support/xxx` → `Operation not permitted`，
/// `~/Library/Developer/CoreSimulator/Devices/...` 同样被拒。
/// 也就是说"按惯例写 Application Support"在这里会**静默失败**（文件写不进去、
/// 用户看到"历史为空"却查不出原因）。
///
/// 所以目录按下面的顺序解析，并把**实际用了哪一个**记录在 [resolvedFrom] 里，
/// 由自检断言"这个目录真的可写"：
/// 1. `SEKB_DATA_DIR`（构建脚本注入 → 落在仓库 `.tooling/` 内，可复核、可清理）；
/// 2. `Application Support/<bundle id>`（正常 App 环境的正确位置）；
/// 3. `NSTemporaryDirectory()`（最后兜底：会话不持久，但应用不会因为写不进去而崩）。
enum SekbPaths {

    private static let lock = NSLock()
    private static var cached: (url: URL, source: String)?

    /// 实际生效的数据根目录（已创建）。
    static func dataDir() -> URL {
        lock.lock()
        defer { lock.unlock() }
        if let cached { return cached.url }

        let candidates: [(URL?, String)] = [
            (envDir(), "SEKB_DATA_DIR"),
            (appSupportDir(), "applicationSupport"),
            (URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("sekb-data"), "tmp"),
        ]
        for (candidate, source) in candidates {
            guard let url = candidate else { continue }
            if ensureWritable(url) {
                cached = (url, source)
                return url
            }
        }
        // 三个都失败时**不能崩**：返回临时目录（写盘失败只影响持久化本身）
        let fallback = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("sekb-data")
        try? FileManager.default.createDirectory(at: fallback, withIntermediateDirectories: true)
        cached = (fallback, "tmp-fallback")
        return fallback
    }

    /// 数据目录是从哪来的（自检里打印，避免"历史为什么是空的"靠猜）。
    static var resolvedFrom: String {
        _ = dataDir()
        lock.lock()
        defer { lock.unlock() }
        return cached?.source ?? "unknown"
    }

    static func conversationsDir() -> URL { sub("conversations") }
    static func knowledgeDir() -> URL { sub("knowledge") }
    static func policyDir() -> URL { sub("policy") }

    private static func sub(_ name: String) -> URL {
        let url = dataDir().appendingPathComponent(name)
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    private static func envDir() -> URL? {
        guard let raw = ProcessInfo.processInfo.environment["SEKB_DATA_DIR"],
              !raw.trimmingCharacters(in: .whitespaces).isEmpty else { return nil }
        return URL(fileURLWithPath: raw)
    }

    private static func appSupportDir() -> URL? {
        guard let base = FileManager.default.urls(for: .applicationSupportDirectory,
                                                 in: .userDomainMask).first else { return nil }
        let id = Bundle.main.bundleIdentifier ?? "com.sekb.ondevice"
        return base.appendingPathComponent(id)
    }

    /// 试创建 + 试写一个探针文件。**只判"存在"是不够的**：只读目录也存在。
    @discardableResult
    static func ensureWritable(_ url: URL) -> Bool {
        let fm = FileManager.default
        do {
            try fm.createDirectory(at: url, withIntermediateDirectories: true)
        } catch {
            return false
        }
        let probe = url.appendingPathComponent(".write-probe")
        do {
            try "ok".write(to: probe, atomically: true, encoding: .utf8)
            try? fm.removeItem(at: probe)
            return true
        } catch {
            return false
        }
    }
}
