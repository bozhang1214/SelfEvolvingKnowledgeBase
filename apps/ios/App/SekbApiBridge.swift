import Foundation
import SharedCore

/// 桥接层返回的错误。
///
/// 这是 **Swift 侧自有**的类型：共享层现在**不会**把 `SekbApiException` 抛到 Swift
/// （原因见 [SekbApiBridge] 的说明），所以错误只能在 Swift 侧表达。
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

/// Apple 侧调用 SEKB 云端的**桥**。
///
/// ## 为什么需要它（这是共享层的一个 **API 缺口**，不是设计选择）
///
/// `SekbApi` 的 `login` / `enroll` / `refresh` / `chatStream` / `reportRouteEvent`
/// 在 Kotlin 里是 **throwing** 的，但**没有标 `@Throws`**。Kotlin/Native 的规则是：
/// 未标 `@Throws` 的函数把异常抛到 Objective-C/Swift 时**直接终止进程**，
/// 而不是变成 `NSError`。本机实测（macOS 框架，`SekbApi.login` 收到 HTTP 500）：
///
/// ```
/// Function doesn't have or inherit @Throws annotation and thus exception isn't propagated
/// from Kotlin to Objective-C/Swift as NSError. It is considered unexpected and unhandled
/// instead. Program will be terminated.
/// Uncaught Kotlin exception: com.sekb.shared.net.SekbApiException: SEKB 500: probe-500
/// Abort trap: 6
/// ```
///
/// 结论：**Apple 侧一行 `SekbApi` 都不能调**——只要服务端返回非 2xx（401/403/429/503…）
/// 或 SSE 里出现 `error` 事件，App 会当场 abort，而不是"显示接入失败"。
/// 症状会是"用得好好的，某天 token 过期就闪退"，属于最难查的那类。
///
/// ## 正确修法（属于共享层改动，本工作流不改 `apps/shared/**`）
///
/// 给这些方法加 `@Throws(SekbApiException::class)`（或 `@Throws(Throwable::class)`）：
/// Swift 侧就会看到 `throws`，可以 `do/catch`。**主线补上之后，这个文件应整体删除**，
/// `SekbContainer` 改回用 `SekbApi`。已在交付报告里登记为共享层缺口。
///
/// ## 替代方案为什么不是"第二套事实"
///
/// - **协议语义仍然只有一份**：SSE 行的解析（`data:` / `event:` /
///   `token|thinking|done|error` 四种事件、以及 `[DONE]` 与跨行收尾）走共享层 `SseParser`；
///   凭证构造走共享层 `DeviceCredentials.fromTtl`；JSON 解析走共享层 `JsonX`；
///   JSON 生成走共享层 `JsonObject`（与 Kotlin 侧同一个编码器）。
/// - 这里只重写"HTTP 调用 + 状态码判断 + 少数几个字段名"。
/// - 字段名逐条照抄 `SekbApi.kt` 与协议文档 `docs/ops/16-端云协同协议.md`
///   （§2 enroll 响应：`device_id` / `device_token` / `expires_in_hours`；
///   §3.1 SSE：`meta.conversation_id` / `meta.execution`；登录：`token`/`access_token`）。
/// - 自检项 `bridge_parse_enroll_response`、`bridge_login_token`、`bridge_sse_events`、
///   `bridge_route_event_body` 用**协议文档里的样例报文**把这几处钉住，
///   所以"字段名两边漂移"会在自检里当场变红，而不是等到联调。
final class SekbApiBridge {

    private let transport: HttpTransport
    private let baseUrl: String

    init(transport: HttpTransport, baseUrl: String) {
        self.transport = transport
        self.baseUrl = baseUrl
    }

    // MARK: - 账号与设备凭证

    func login(email: String, password: String) -> Result<String, SekbBridgeError> {
        let body = JsonObject().put(key: "email", value: email)
            .put(key: "password", value: password).description
        let resp = transport.postJson(url: "\(baseUrl)/api/v1/auth/login",
                                      headers: jsonHeaders(), body: body, timeoutSeconds: 30)
        if !resp.isOk { return .failure(SekbBridgeError(code: resp.code, detail: errorDetail(resp.body))) }
        guard let token = SekbApiBridge.parseLoginToken(resp.body) else {
            return .failure(SekbBridgeError(code: resp.code, detail: "登录响应里没有 token"))
        }
        return .success(token)
    }

    func enroll(userToken: String, name: String, platform: String,
                appVersion: String, embeddingSpace: String) -> Result<DeviceCredentials, SekbBridgeError> {
        let body = JsonObject()
            .put(key: "name", value: name)
            .put(key: "platform", value: platform)
            .put(key: "app_version", value: appVersion)
            .put(key: "embedding_space", value: embeddingSpace)
            .description
        let resp = transport.postJson(url: "\(baseUrl)/api/v1/device/enroll",
                                      headers: authHeaders(userToken), body: body, timeoutSeconds: 30)
        if !resp.isOk { return .failure(SekbBridgeError(code: resp.code, detail: errorDetail(resp.body))) }
        guard let creds = SekbApiBridge.parseCredentials(resp.body, nowMillis: TimeFmt.nowMillis()) else {
            return .failure(SekbBridgeError(code: resp.code, detail: "响应缺少 device_id/device_token"))
        }
        return .success(creds)
    }

    /// 设备 token 轮换：**旧 token 立即失效**（服务端语义），所以调用方必须落盘新凭证。
    func refresh(deviceToken: String) -> Result<DeviceCredentials, SekbBridgeError> {
        let resp = transport.postJson(url: "\(baseUrl)/api/v1/device/refresh",
                                      headers: authHeaders(deviceToken), body: "{}", timeoutSeconds: 30)
        if !resp.isOk { return .failure(SekbBridgeError(code: resp.code, detail: errorDetail(resp.body))) }
        guard let creds = SekbApiBridge.parseCredentials(resp.body, nowMillis: TimeFmt.nowMillis()) else {
            return .failure(SekbBridgeError(code: resp.code, detail: "响应缺少 device_id/device_token"))
        }
        return .success(creds)
    }

    // MARK: - 聊天（SSE）

    /// 云端聊天。语义与 `SekbApi.chatStream` 一致：
    /// 逐段回调 token；`done.meta.execution` 是执行位置的**权威口径**（服务端知道自己各角色落在哪）。
    func chatStream(deviceToken: String,
                    message: String,
                    conversationId: String?,
                    onToken: @escaping (String) -> Void,
                    onThinking: @escaping (String) -> Void) -> Result<SekbBridgeChatResult, SekbBridgeError> {
        let bodyObject = JsonObject().put(key: "message", value: message)
        if let cid = conversationId, !cid.isEmpty {
            bodyObject.put(key: "conversation_id", value: cid)
        }
        // 每次调用新建解析器：`SseParser` 带跨行缓冲状态，复用会把上一轮的半截行带进这一轮
        let sse = SseParser()
        var execution: ExecutionInfo?
        var convId: String? = conversationId
        var streamError: String?

        let code = transport.postJsonStream(
            url: "\(baseUrl)/api/v1/chat/stream",
            headers: authHeaders(deviceToken),
            body: bodyObject.description,
            timeoutSeconds: 600,
            onLine: { line in
                guard let event = sse.feed(line: line) else { return }
                if let token = event as? ChatEventToken {
                    onToken(token.content)
                } else if let thinking = event as? ChatEventThinking {
                    onThinking(thinking.content)
                } else if let done = event as? ChatEventDone {
                    execution = done.meta.execution
                    if !done.meta.conversationId.isEmpty { convId = done.meta.conversationId }
                } else if let err = event as? ChatEventError {
                    streamError = err.detail
                }
            })

        // 结束前把最后一段没被空行终止的数据也处理掉（与 Kotlin 侧同一处理）
        if let tail = sse.finish() {
            if let token = tail as? ChatEventToken {
                onToken(token.content)
            } else if let done = tail as? ChatEventDone {
                execution = done.meta.execution
                if !done.meta.conversationId.isEmpty { convId = done.meta.conversationId }
            } else if let err = tail as? ChatEventError {
                streamError = err.detail
            }
        }
        if let detail = streamError {
            return .failure(SekbBridgeError(code: code, detail: detail))
        }
        if code < 200 || code > 299 {
            return .failure(SekbBridgeError(code: code, detail: "流式请求失败"))
        }
        return .success(SekbBridgeChatResult(execution: execution, conversationId: convId))
    }

    // MARK: - 路由事件上报

    /// 上报一条路由事件（幂等：`event_id` 唯一，重传不会重复统计）。
    ///
    /// **失败不能影响回答**：上报只是统计口径，返回 `false` 即可，绝不抛。
    func reportRouteEvent(deviceToken: String, event: RouteEventPayload) -> Bool {
        let resp = transport.postJson(url: "\(baseUrl)/api/v1/edge/route-events",
                                      headers: authHeaders(deviceToken),
                                      body: SekbApiBridge.routeEventBody(event),
                                      timeoutSeconds: 20)
        return resp.isOk
    }

    // MARK: - 可被自检直接调用的纯逻辑

    /// 登录响应里的 token 字段名以服务端 `LoginResponse` 为准：`{"user": {...}, "token": "..."}`。
    /// （第一版按 OAuth 习惯猜了 `access_token` → 明明 200 OK 却被判成"登录失败"，
    ///   是模拟器真机 E2E 抓出来的：服务端日志有 200，客户端说失败。）
    static func parseLoginToken(_ body: String) -> String? {
        guard let obj = JsonX.shared.parseObject(text: body) else { return nil }
        let primary = JsonX.shared.string(obj: obj, key: "token", fallback: "")
        if !primary.isEmpty { return primary }
        let legacy = JsonX.shared.string(obj: obj, key: "access_token", fallback: "")
        return legacy.isEmpty ? nil : legacy
    }

    /// 凭证响应解析（`device_id` / `device_token` / `expires_in_hours`）。
    /// TTL 由服务端给，端侧**不猜**（服务端改 TTL 后端侧算出的总时长不会错）。
    static func parseCredentials(_ body: String, nowMillis: Int64) -> DeviceCredentials? {
        guard let obj = JsonX.shared.parseObject(text: body) else { return nil }
        let deviceId = JsonX.shared.string(obj: obj, key: "device_id", fallback: "")
        let token = JsonX.shared.string(obj: obj, key: "device_token", fallback: "")
        if deviceId.isEmpty || token.isEmpty { return nil }
        let defaultTtl = Int32(DeviceCredentials.companion.DEFAULT_TTL_HOURS)
        let ttlHours = JsonX.shared.int(obj: obj, key: "expires_in_hours", fallback: defaultTtl)
        return DeviceCredentials.companion.fromTtl(
            deviceId: deviceId, token: token, nowMillis: nowMillis, ttlHours: Int64(ttlHours))
    }

    /// 路由事件请求体（字段名与 `SekbApi.reportRouteEvent` 逐条对齐）。
    static func routeEventBody(_ event: RouteEventPayload) -> String {
        let signals = JsonArray()
        for s in event.signals { signals.put(value: s) }
        let versions = JsonObject()
        for (k, v) in event.versions { versions.put(key: k, value: v) }
        return JsonObject()
            .put(key: "event_id", value: event.eventId)
            .put(key: "role", value: event.role)
            .put(key: "plane", value: event.plane)
            .put(key: "reason", value: event.reason)
            .put(key: "model", value: event.model)
            .put(key: "tier", value: event.tier)
            .put(key: "input_tokens", value: Int(event.inputTokens))
            .put(key: "output_tokens", value: Int(event.outputTokens))
            .put(key: "latency_ms", value: event.latencyMs)
            .put(key: "escalated", value: event.escalated)
            .put(key: "escalate_reason", value: event.escalateReason)
            .put(key: "signals", value: signals)
            .put(key: "versions", value: versions)
            .description
    }

    // MARK: - 内部

    private func errorDetail(_ body: String) -> String {
        if let obj = JsonX.shared.parseObject(text: body) {
            let detail = JsonX.shared.string(obj: obj, key: "detail", fallback: "")
            if !detail.isEmpty { return detail }
        }
        return String(body.prefix(200))
    }

    private func jsonHeaders() -> [String: String] { ["Content-Type": "application/json"] }

    private func authHeaders(_ token: String) -> [String: String] {
        ["Content-Type": "application/json", "Authorization": "Bearer \(token)"]
    }
}
