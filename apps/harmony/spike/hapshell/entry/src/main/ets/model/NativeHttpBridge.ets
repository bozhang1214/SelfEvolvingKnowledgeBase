/**
 * H3 · ArkTS 侧的 HTTP 客户端（`@ohos.net.http`）—— 共享层 `HttpTransport` 的**字节搬运层**。
 *
 * ## 为什么客户端在 ArkTS 而不在 Kotlin
 *
 * 三条硬事实（见 `apps/harmony/README.md` 的 HR9 一节）：
 *   1. 设备上 dlopen 穷举 5 个 libcurl 变体**全部 LOAD FAIL**，且没有 OpenSSL；
 *   2. OHOS sysroot 里没有 curl 头、也没有 C 级 HTTP 客户端头 → 链接期也没有；
 *   3. 平台唯一可用的 HTTP 客户端就是 ArkTS 的 `@ohos.net.http`（owner 已就此改判 C2）。
 *
 * ## 为什么用 `@ohos.net.http` 而不是 `rcp`
 *
 * `rcp`（Remote Communication Kit）在 API 22 上更"现代"（有 `onDataReceive` 之类），
 * 但它需要额外的 `@hms.collaboration.rcp` 依赖声明，而且**流式逐块的 API 面更不确定**；
 * H3 的验收只要"GET 通 + 逐行回调"，`@ohos.net.http` 的
 * `requestInStream` + `on('dataReceive')` 已在 SDK 的 `.d.ts` 里明确存在
 * （`@ohos.net.http.d.ts:2130` `requestInStream(url, options?): Promise<number>`、
 * `:2322` `on('dataReceive', Callback<ArrayBuffer>)`），且 `module.json5` 里
 * `INTERNET` 权限本来就要加。**结论：先用平台最基础的那个，少一个依赖面。**
 *
 * ## 这个文件**不做**的事（重要）
 *
 *   * 不解析 SEKB 协议的任何 JSON 字段（那是共享层 `SekbApi.kt` 的职责）；
 *   * 不实现 SSE 语义（`data:` / 空行分隔 / 事件类型）——只把**完整的一行**交回 Kotlin，
 *     由共享层 `SseParser` 解析。这样"SSE 只有一份事实"。
 *
 * ## 同步语义怎么保证（不死锁的关键）
 *
 * Kotlin 侧的 `HttpTransport` 是同步接口。解法是**调用方向反过来**：
 * ArkTS 在自己的调用栈上同步调 NAPI 进 Kotlin（`sekb_h3_transport_*`），
 * Kotlin 在这一次调用里通过本文件注册的 native 函数发起请求并把结果拿走。
 * 全程没有"Kotlin 切回 JS 线程"，所以没有死锁窗口 —— 详见 `HarmonyHttpTransport.kt` 文件头。
 */
import { http } from '@kit.NetworkKit';
import { hilog } from '@kit.PerformanceAnalysisKit';
import { BusinessError } from '@kit.BasicServicesKit';
import { util } from '@kit.ArkTS';
import knspike from 'libknspike.so';

const TAG: string = 'SEKB_KN_SPIKE';
/** ArkTS 侧响应体缓冲上限：只用来防止异常端点把内存吃光（正常响应远小于它）。 */
const BODY_LIMIT: number = 8 * 1024 * 1024;

// ⚠️ 这里**没有**"把 ArkTS 函数注册给 Kotlin"的回调桥（方案 (i)）。原因见
//    `knspike/src/ohosMain/kotlin/NetPort.kt` 文件头：共享层 `HttpTransport` 是同步接口，
//    而 Kotlin 是被 ArkTS 同步调进来的，`await` 的回调排不进被卡住的那条线程 → **结构性死锁**。
//    所以端口注入采用「网络调用留在 ArkTS」：ArkTS 把**已经拿到的**响应帧同步交给 Kotlin。

/** 最近一次缓冲式请求的元信息（帧头）。 */
let lastMeta: string = 'HTTP -1 bodyUTF8=0 inv=0';
/** 最近一次缓冲式请求的响应体。 */
let lastBody: string = '';
let invocations: number = 0;

function decodeUtf8(bytes: Uint8Array): string {
  if (bytes.length === 0) {
    return '';
  }
  return util.TextDecoder.create('utf-8').decodeToString(bytes);
}

function parseHeaders(headersJson: string): Record<string, string> {
  const out: Record<string, string> = {};
  if (headersJson.length === 0) {
    return out;
  }
  try {
    const obj = JSON.parse(headersJson) as Record<string, string>;
    for (const k of Object.keys(obj)) {
      out[k] = obj[k];
    }
  } catch (e) {
    hilog.warn(0x0000, TAG, 'HTTP 头解析失败（忽略）：%{public}s', JSON.stringify(e));
  }
  return out;
}

/** 按 `\n` 切行，把 `\r` 去掉；返回尚未结束的尾部。 */
function drainLines(buf: string, onLine: (line: string) => void): string {
  let rest: string = buf;
  let idx: number = rest.indexOf('\n');
  while (idx >= 0) {
    let line: string = rest.substring(0, idx);
    if (line.endsWith('\r')) {
      line = line.substring(0, line.length - 1);
    }
    onLine(line);
    rest = rest.substring(idx + 1);
    idx = rest.indexOf('\n');
  }
  return rest;
}

/**
 * 缓冲式请求（`postJson` / `get` 用）。
 *
 * 返回给 Kotlin 的是 `"HTTP <code>\n<body>"` 的原始文本——Kotlin 侧**优先**读
 * `META`/`BODY`（这两个才是权威），只在拿不到时才回退解析这段文本。
 * 这样做是因为 NAPI 的字符串返回值**有长度限制**：`/api/tags` 的响应实测在 1KB 左右
 * 就被截断了，直接把状态码藏在这段文本里会得到假结论。
 */
async function runHttp(
  url: string, method: string, headersJson: string, body: string, timeoutSeconds: number): Promise<string> {
  invocations++;
  const inv: number = invocations;
  const header: Record<string, string> = parseHeaders(headersJson);
  const options: http.HttpRequestOptions = {
    method: method === 'POST' ? http.RequestMethod.POST : http.RequestMethod.GET,
    header: header,
    connectTimeout: timeoutSeconds * 1000,
    readTimeout: timeoutSeconds * 1000,
    expectDataType: http.HttpDataType.STRING,
  };
  if (body.length > 0) {
    options.extraData = body;
  }
  let code: number = -1;
  let text: string = '';
  let errText: string = '';
  // 响应体的**字节数**单独记：字符数对不上字节数（中文），而"跨 NAPI 边界是否被截断"
  // 这个判据只有字节数可比。用 TextEncoder 得到的字节数才是权威。
  let utf8Len: number = 0;
  try {
    const resp: http.HttpResponse = await http.createHttp().request(url, options);
    code = resp.responseCode;
    if (typeof resp.result === 'string') {
      text = resp.result as string;
      utf8Len = new util.TextEncoder().encodeInto(text).length;
    } else if (resp.result instanceof ArrayBuffer) {
      utf8Len = (resp.result as ArrayBuffer).byteLength;
      text = decodeUtf8(new Uint8Array(resp.result as ArrayBuffer));
    } else {
      text = JSON.stringify(resp.result);
      utf8Len = new util.TextEncoder().encodeInto(text).length;
    }
  } catch (e) {
    const be = e as BusinessError;
    errText = `code=${be.code} msg=${be.message}`;
    code = -1;
    hilog.error(0x0000, TAG, 'HTTP run 失败 url=%{public}s err=%{public}s', url, errText);
  }
  if (text.length > BODY_LIMIT) {
    text = text.substring(0, BODY_LIMIT);
  }
  lastBody = text;
  lastMeta = `HTTP ${code} bodyUTF8=${utf8Len} bodyChars=${text.length} inv=${inv}` +
    (errText.length > 0 ? ` err=${errText}` : '');
  hilog.info(0x0000, TAG, 'HTTP run #%{public}d %{public}s %{public}s → %{public}s',
    inv, method, url, lastMeta);
  return `HTTP ${code}\n${text}`;
}

/**
 * 流式请求（`postJsonStream` 用）：`requestInStream` + `on('dataReceive')`，
 * 边收边切行，**每个完整行**同步回调 `onLine`（`onLine` 是 Kotlin 侧通过 NAPI 传进来的函数引用）。
 *
 * 为什么状态码要专门记：`requestInStream` 的 Promise 在**流结束**时才 resolve，
 * 而 `on('headersReceive')` 的回调里拿得到 `responseCode`（实测它比 Promise 早得多）。
 */
async function streamHttp(
  url: string, method: string, headersJson: string, body: string,
  timeoutSeconds: number, onLine: (line: string) => void): Promise<number> {
  const header: Record<string, string> = parseHeaders(headersJson);
  const options: http.HttpRequestOptions = {
    method: method === 'POST' ? http.RequestMethod.POST : http.RequestMethod.GET,
    header: header,
    connectTimeout: timeoutSeconds * 1000,
    readTimeout: timeoutSeconds * 1000,
  };
  if (body.length > 0) {
    options.extraData = body;
  }
  const req: http.HttpRequest = http.createHttp();
  let status: number = -1;
  let pending: string = '';
  let total: number = 0;
  req.on('headersReceive', (h: Object) => {
    const obj = h as Record<string, Object>;
    const code: Object | undefined = obj['responseCode'];
    if (code !== undefined) {
      status = Number(code);
    }
  });
  req.on('dataReceive', (chunk: ArrayBuffer) => {
    total += chunk.byteLength;
    pending = drainLines(pending + decodeUtf8(new Uint8Array(chunk)), onLine);
  });
  try {
    const code: number = await req.requestInStream(url, options);
    if (status < 0) {
      status = code;
    }
    // 收尾：最后一行可能没有换行符结尾，也必须交出去（共享层 `SseParser.finish()` 依赖这一点）
    if (pending.length > 0) {
      onLine(pending);
      pending = '';
    }
  } catch (e) {
    const be = e as BusinessError;
    hilog.error(0x0000, TAG, 'HTTP stream 失败 url=%{public}s err=code=%{public}d msg=%{public}s',
      url, be.code, be.message);
    status = -1;
  } finally {
    req.off('dataReceive');
    req.off('headersReceive');
    req.destroy();
  }
  hilog.info(0x0000, TAG, 'HTTP stream %{public}s → code=%{public}d bytes=%{public}d', url, status, total);
  return status;
}

/**
 * H3 自检用：发一次**缓冲式**请求，返回交给 Kotlin 的帧
 * `"HTTP <code> <utf8Bytes>
<响应体原文>"`。
 *
 * 为什么"发请求 + 组帧"必须在同一个模块状态里完成：中间若夹进别的请求，
 * 帧里的头和体就会来自两次不同的调用（能编过、但结论是错的）。
 */
export async function probeBuffered(url: string, timeoutSeconds: number): Promise<string> {
  const raw: string = await runHttp(url, 'GET', '{}', '', timeoutSeconds);
  const nl: number = raw.indexOf('\n');
  return `${lastMeta}\n${nl >= 0 ? raw.substring(nl + 1) : raw}`;
}

/**
 * H3 自检用：发一次**流式**请求，每个完整行同步喂给 Kotlin（`h3StreamLine`），
 * 结束后返回摘要帧 `"HTTP <code> lines=<n>
<首行样本>"`。
 *
 * 这一行 `knspike.h3StreamLine(line)` 就是"ArkTS 搬行、共享层 `SseParser` 解释"的接口。
 */
export async function probeStream(url: string, body: string, timeoutSeconds: number): Promise<string> {
  let lines: number = 0;
  let first: string = '';
  const code: number = await streamHttp(url, 'POST', '{"Content-Type":"application/json"}', body,
    timeoutSeconds, (line: string) => {
      lines++;
      if (first.length === 0 && line.length > 0) {
        first = line;
      }
      knspike.h3StreamLine(line);
    });
  return `HTTP ${code} lines=${lines}\n${first}`;
}
