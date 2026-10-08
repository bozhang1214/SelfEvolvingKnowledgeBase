import Foundation

/// 会话列表用的相对时间文案（`ui/TimeFmt.kt` 的 Swift 移植）。
///
/// ## 为什么不直接显示完整时间戳
///
/// 用户扫列表时判断"这是我什么时候聊的"靠的是**模糊的正确**（"刚刚"/"昨天"），
/// 不是精确到秒。而列出 `2026-09-25 15:42:07` 会让每一行都长得一样、眼睛抓不到重点。
/// 但超过一周后模糊就没意义了（"37 天前"反而不如日期直观），所以做分段降级。
///
/// `timeZone`/`now` 可注入：跨时区/跨端一致性与单测要能钉住结果，不依赖运行环境默认值。
enum TimeFmt {

    static func relative(_ epochMillis: Int64, nowMillis: Int64, timeZone: TimeZone = .current) -> String {
        if epochMillis <= 0 { return "" }   // <=0 视为"未知"（返回空串，让调用方决定怎么显示）
        let diff = nowMillis - epochMillis
        // 未来时间（设备时钟被调整过）不显示"负几分钟前"，按"刚刚"处理更不容易误解
        if diff < 60_000 { return "刚刚" }
        if diff < 3_600_000 { return "\(diff / 60_000) 分钟前" }

        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timeZone
        let target = Date(timeIntervalSince1970: Double(epochMillis) / 1000.0)
        let now = Date(timeIntervalSince1970: Double(nowMillis) / 1000.0)
        let days = calendar.dateComponents([.day],
                                           from: calendar.startOfDay(for: target),
                                           to: calendar.startOfDay(for: now)).day ?? 0
        if days <= 0 { return hhmm(target, timeZone) }
        if days == 1 { return "昨天 " + hhmm(target, timeZone) }
        if days < 7 { return "\(days) 天前" }
        return ymd(target, timeZone)
    }

    private static func hhmm(_ date: Date, _ zone: TimeZone) -> String { format("HH:mm", date, zone) }

    private static func ymd(_ date: Date, _ zone: TimeZone) -> String { format("yyyy-MM-dd", date, zone) }

    private static func format(_ pattern: String, _ date: Date, _ zone: TimeZone) -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = zone
        f.dateFormat = pattern
        return f.string(from: date)
    }

    /// 当前毫秒时间戳（`Clock.nowMillis()` 的 Swift 对应物）。
    static func nowMillis() -> Int64 { Int64(Date().timeIntervalSince1970 * 1000.0) }

    /// "8.4 KB" / "1.2 MB" / "512 B"——人类的体积单位。
    ///
    /// ⚠️ 不要再退回"整数 KB"：小文件会被显示成 `0KB`，看起来像坏了
    /// （Android 侧上一版就是这样，还得靠 `if (sizeBytes < 1024)` 特判）。
    static func humanSize(_ bytes: Int64) -> String {
        if bytes < 1024 { return "\(bytes) B" }
        if bytes < 1024 * 1024 { return String(format: "%.1f KB", Double(bytes) / 1024.0) }
        return String(format: "%.1f MB", Double(bytes) / (1024.0 * 1024.0))
    }
}
