import Foundation
import SharedCore

/// iOS 侧的网络传输实现（M5 端口之一）：用 `URLSession` 实现共享层的 `HttpTransport`。
///
/// 为什么这层必须各端自己写（见 `docs/多端跨端-工程议题（网络层·包体·热修复）.md` §1）：
/// 共享层只规定"发 JSON、收 SSE"，真正的网络栈在 iOS 上当属 `URLSession`——
/// 它自带 ATS、系统代理/VPN、证书链与后台传输语义，换成自建 libcurl 反而要自己维护这些。
///
/// 实现要点（都是踩过的坑）：
/// 1. **同步语义**：Kotlin 侧接口是同步的（编排器刻意同步、易测），所以这里用 `DispatchSemaphore`
///    把异步回调等成同步返回——注意**不能在主线程调用**（会死锁主线程）。
/// 2. **SSE 必须逐行回调**：用 `URLSessionDataDelegate` 收字节流、按 `\n` 切行；
///    一次性 `dataTask` 会把整个流缓冲起来，token 就不是"流"了。
/// 3. `Accept: text/event-stream` 由共享层负责加（调用点传 headers），这里不越权加。
final class UrlSessionTransport: NSObject, HttpTransport {

    private let session: URLSession

    override init() {
        let cfg = URLSessionConfiguration.default
        cfg.timeoutIntervalForRequest = 300
        cfg.timeoutIntervalForResource = 600
        cfg.requestCachePolicy = .reloadIgnoringLocalCacheData
        // 端侧要连本机/局域网端点（127.0.0.1、10.0.2.2、尾网 IP）：那些是 HTTP，
        // 默认 ATS 会拦。生产接入云端时应当是 HTTPS，这里的例外只针对端侧宿主。
        cfg.httpAdditionalHeaders = ["User-Agent": "SEKB-OnDevice-iOS/0.1"]
        session = URLSession(configuration: cfg)
        super.init()
    }

    func postJson(url: String, headers: [String: String], body: String, timeoutSeconds: Int64) -> HttpResponse {
        var req = URLRequest(url: URL(string: url)!)
        req.httpMethod = "POST"
        req.httpBody = body.data(using: .utf8)
        req.setValue("application/json; charset=utf-8", forHTTPHeaderField: "Content-Type")
        for (k, v) in headers { req.setValue(v, forHTTPHeaderField: k) }
        req.timeoutInterval = TimeInterval(timeoutSeconds)
        return performSync(req)
    }

    func get(url: String, headers: [String: String], timeoutSeconds: Int64) -> HttpResponse {
        var req = URLRequest(url: URL(string: url)!)
        req.httpMethod = "GET"
        for (k, v) in headers { req.setValue(v, forHTTPHeaderField: k) }
        req.timeoutInterval = TimeInterval(timeoutSeconds)
        return performSync(req)
    }

    func postJsonStream(url: String, headers: [String: String], body: String,
                        timeoutSeconds: Int64, onLine: @escaping (String) -> Void) -> Int32 {
        var req = URLRequest(url: URL(string: url)!)
        req.httpMethod = "POST"
        req.httpBody = body.data(using: .utf8)
        req.setValue("application/json; charset=utf-8", forHTTPHeaderField: "Content-Type")
        for (k, v) in headers { req.setValue(v, forHTTPHeaderField: k) }
        req.timeoutInterval = TimeInterval(timeoutSeconds)

        let sem = DispatchSemaphore(value: 0)
        var status: Int32 = -1

        let delegate = StreamDelegate(onLine: { line in onLine(line) },
                                     onFinish: { code in status = code; sem.signal() })
        let streamSession = URLSession(configuration: session.configuration,
                                       delegate: delegate, delegateQueue: nil)
        streamSession.dataTask(with: req).resume()
        _ = sem.wait(timeout: .now() + TimeInterval(timeoutSeconds))
        streamSession.invalidateAndCancel()
        return status
    }

    private func performSync(_ req: URLRequest) -> HttpResponse {
        let sem = DispatchSemaphore(value: 0)
        var out = HttpResponse(code: -1, body: "")
        session.dataTask(with: req) { data, resp, _ in
            let code = (resp as? HTTPURLResponse)?.statusCode ?? -1
            out = HttpResponse(code: Int32(code), body: String(data: data ?? Data(), encoding: .utf8) ?? "")
            sem.signal()
        }.resume()
        _ = sem.wait(timeout: .now() + req.timeoutInterval + 5)
        return out
    }
}

/// SSE / 流式响应的 delegate：**按行**把数据交回共享层（不缓冲整个响应体）。
private final class StreamDelegate: NSObject, URLSessionDataDelegate {
    private let onLine: (String) -> Void
    private let onFinish: (Int32) -> Void
    private var buffer = Data()
    /// HTTP 状态码必须在这里取：`didCompleteWithError` 里 `task.response` 有时拿不到类型化响应
    /// （实测 statusCode 得到 -1），而 `didReceive response` 是**可靠**的那一次回调。
    private let lock = NSLock()
    private var _statusCode: Int32 = -1
    /// 是否真的收到了 `didReceive response`（排查用：为 false 说明该回调没来，而不是状态码丢了）
    private(set) var sawResponse = false

    var statusCode: Int32 {
        lock.lock(); defer { lock.unlock() }
        return _statusCode
    }

    init(onLine: @escaping (String) -> Void, onFinish: @escaping (Int32) -> Void) {
        self.onLine = onLine
        self.onFinish = onFinish
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask,
                    didReceive response: URLResponse,
                    completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        lock.lock()
        _statusCode = Int32((response as? HTTPURLResponse)?.statusCode ?? -1)
        sawResponse = true
        lock.unlock()
        completionHandler(.allow)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        buffer.append(data)
        while let idx = buffer.firstIndex(of: 0x0A) {          // \n
            let lineData = buffer.subdata(in: buffer.startIndex..<idx)
            buffer.removeSubrange(buffer.startIndex...idx)
            onLine(String(data: lineData, encoding: .utf8) ?? "")
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        // 兜底顺序：didReceive 记下的 → task.response → 无错误且已收到数据则视为 200
        let fromTask = ((task.response as? HTTPURLResponse)?.statusCode).map(Int32.init) ?? -1
        let code = statusCode != -1 ? statusCode : (fromTask != -1 ? fromTask : (error == nil ? 200 : -1))
        if !buffer.isEmpty {
            onLine(String(data: buffer, encoding: .utf8) ?? "")
            buffer.removeAll()
        }
        onFinish(Int32(code))
    }
}
