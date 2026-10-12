import Foundation
import SharedCore

/// 桥接层返回的错误。
///
/// 保留这个类型是为了让调用点（`SekbContainer` / 自检）**不必处处写 `do/catch`**；
/// 它现在只是对共享层 `SekbApiException` 的**薄包装**，不再承载任何协议语义。
struct SekbBridgeError: Error, CustomStringConvertible {
    let code: Int32
    let detail: String

    var description: String { "SEKB \(code): \(detail)" }
}

/// 云端一次聊天（SSE）的回执：执行位置 + 会话 ID（会话 ID 必须回流，否则每轮都是新会话）。
struct SekbBridgeChatResult {
    let execution: ExecutionInfo?
    let conversationId: String?
}

/// Apple 侧调用 SEKB 云端的**薄适配层**（不是"桥"了）。
///
/// ## 历史（这段是给后来人省时间的）
///
/// 2026-10-25 之前这里是一个 **248 行的 `SekbApiBridge`**：它自己拼 URL、自己解析响应字段、
/// 自己驱动 `SseParser`。存在的原因很具体——当时共享层 `SekbApi` 的方法**没有标 `@Throws`**，
/// Kotlin/Native 把未声明的异常抛到 Swift 时会**直接终止进程**：
///
/// ```
/// Function doesn't have or inherit @Throws annotation and thus exception isn't propagated
/// from Kotlin to Objective-C/Swift as NSError ... Program will be terminated.
/// Uncaught Kotlin exception: com.sekb.shared.net.SekbApiException: SEKB 500: probe-500
/// Abort trap: 6
/// ```
///
/// 后果是"用得好好的，某天 token 过期（401）就闪退"。为了不崩，Apple 侧抄了一份客户端——
/// 那是**第二套事实**（协议字段名与路径两处维护，漂移只能靠自检发现）。
///
/// ## 现在为什么可以直接用共享层
///
/// 主线已给 `SekbApi` 的 8 个公开方法补上 `@Throws(SekbApiException::class)`；生成的 ObjC 头里
/// 可以逐条看到契约生效：
///
/// ```objc
/// - (NSString * _Nullable)loginEmail:... error:(NSError **)error
///     __attribute__((swift_name("login(email:password:)")));
/// // @note This method converts instances of SekbApiException to errors.
/// ```
///
/// 于是 Swift 侧 `try` 就能拿到错误，`SekbApiException` 还是 `KotlinException` 的子类
/// （`SharedCoreSekbApiException : SharedCoreKotlinException`），可以判类型、取 `code`。
/// **协议路径、请求体、SSE 解析、字段名因此重新回到"只有一份"**（全在共享层 Kotlin 里）。
///
/// 本文件现在只做一件事：把 `throws` 转成调用点习惯的 `Result`，**不含任何协议知识**。
/// 判断它是否又长回"第二套事实"的标准很简单：**这里出现 `/api/v1/` 或字段名字符串就说明退化了。**
final class SekbApiShim {

    private let api: SekbApi

    init(transport: HttpTransport, baseUrl: String) {
        // ⚠️ Kotlin 的**默认参数不跨语言**：`SekbApi(transport:baseUrl:now:)` 三个都必须显式给，
        //    少一个就是 `missing argument for parameter 'now' in call`（本轮实测踩到）。
        //    `now` 是墙上时钟毫秒（与 `TimeFmt.nowMillis()` 同一口径），不是单调时钟。
        self.api = SekbApi(transport: transport, baseUrl: baseUrl, now: { KotlinLong(longLong: TimeFmt.nowMillis()) })
    }

    // MARK: - 账号与设备凭证

    func login(email: String, password: String) -> Result<String, SekbBridgeError> {
        do { return .success(try api.login(email: email, password: password)) }
        catch { return .failure(Self.asError(error)) }
    }

    /// 首次接入：用户 token → 设备凭证。
    ///
    /// `appVersion` / `embeddingSpace` 会**原样进请求体**（服务端 `EnrollRequest` 有这两个字段，
    /// 其中 `embedding_space` 是跨端可比性的判据）——它们不是"给 UI 看的"，别在这里吞掉。
    func enroll(userToken: String,
                name: String,
                platform: String,
                appVersion: String,
                embeddingSpace: String) -> Result<DeviceCredentials, SekbBridgeError> {
        do {
            return .success(try api.enroll(userToken: userToken, name: name, platform: platform,
                                           appVersion: appVersion, embeddingSpace: embeddingSpace))
        } catch { return .failure(Self.asError(error)) }
    }

    func refresh(deviceToken: String) -> Result<DeviceCredentials, SekbBridgeError> {
        do { return .success(try api.refresh(deviceToken: deviceToken)) }
        catch { return .failure(Self.asError(error)) }
    }

    // MARK: - 聊天（SSE）

    func chatStream(deviceToken: String,
                    message: String,
                    conversationId: String?,
                    onToken: @escaping (String) -> Void,
                    onThinking: @escaping (String) -> Void) -> Result<SekbBridgeChatResult, SekbBridgeError> {
        do {
            let r = try api.chatStream(deviceToken: deviceToken,
                                       message: message,
                                       conversationId: conversationId,
                                       onToken: onToken,
                                       onThinking: onThinking)
            return .success(SekbBridgeChatResult(execution: r.execution,
                                                 conversationId: r.conversationId))
        } catch {
            return .failure(Self.asError(error))
        }
    }

    // MARK: - 路由事件上报

    /// 上报一条路由事件（幂等：`event_id` 唯一，重传不会重复统计）。
    ///
    /// **失败不能影响回答**：上报只是统计口径，返回 `false` 即可，绝不抛给调用方。
    func reportRouteEvent(deviceToken: String, event: RouteEventPayload) -> Bool {
        do { return try api.reportRouteEvent(deviceToken: deviceToken, event: event) }
        catch {
            NSLog("SEKB route_event_report_failed — %@", String(describing: error))
            return false
        }
    }

    // MARK: - 错误归一

    /// 把 Swift 侧的 `Error` 归一成 `(code, detail)`。
    ///
    /// `SekbApiException` 继承自 `KotlinException`，Swift 侧以
    /// `NSError(domain: "KotlinException", ...)` 形式出现，`userInfo["KotlinException"]`
    /// 里是原对象；取不到就退化为 `-1` + 描述文本（**绝不假装成 200**）。
    static func asError(_ error: Error) -> SekbBridgeError {
        let ns = error as NSError
        if let kotlin = ns.userInfo["KotlinException"] as? SekbApiException {
            return SekbBridgeError(code: kotlin.code, detail: kotlin.detail)
        }
        // `KotlinException` 的 message 走 localizedDescription；再兜一层 description
        let detail = ns.userInfo["KotlinException"].map { String(describing: $0) }
            ?? (ns.localizedDescription.isEmpty ? String(describing: error) : ns.localizedDescription)
        return SekbBridgeError(code: Int32(ns.code), detail: detail)
    }
}
