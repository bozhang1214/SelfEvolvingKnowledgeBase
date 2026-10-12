/**
 * H8（工作流 G）· 聊天流式的**传输层**（ArkTS）。
 *
 * ## 职责边界（这条边界是整个 H8 的关键，改动前先读）
 *
 * ```
 *   ArkTS（本文件）                     Kotlin（ChatStreamPort.kt）           共享层
 *   ─────────────────────────           ──────────────────────────          ──────────────
 *   @ohos.net.http requestInStream  ──▶ chatStreamLine(line)  ──▶  SseParser.feed(line)
 *   按 \n 切行（只切行，不解释语义）       线格式适配（只有字段名）        事件类型/done.meta
 *   把每行**同步**交给 NAPI               事件 → 一行小 JSON 回给 ArkTS   （唯一解析者）
 * ```
 *
 * **本文件不做的事**：不 `JSON.parse` 增量、不判断 `data:`/空行、不认 `finish_reason`、
 * 不决定"本机还是云端"。那些全在 Kotlin/共享层——ArkTS 里再写一遍就等于造第二套事实
 * （H2/H6 两条教训同源）。
 *
 * ## 为什么用 Ollama 的 OpenAI 兼容端点
 *
 * 本机端侧运行时是宿主 Ollama（`hdc rport tcp:11434`）。它有两个聊天端点：
 *   * `/api/chat` —— **NDJSON**（没有 `data:` 前缀、没有空行分隔）→ 喂给共享层 `SseParser`
 *     等于绕过它；
 *   * `/v1/chat/completions` —— **真正的 SSE**（`data: {...}` + 空行 + `data: [DONE]`）。
 * 所以选后者：**行是真的 SSE 行**，"解析"仍然发生在共享层。
 * 实测依据（`curl -N` 原始输出）见 `apps/harmony/README.md` H8 一节。
 *
 * `reasoning_effort:"none"` 对应共享配置 `EdgeRuntimeConfig.disableThinking = true`
 * （该字段注释：同一任务 2283ms → 89ms）。端侧若仍输出思考，适配器会把
 * `delta.reasoning` 映射成共享层的 `thinking` 事件，界面显示"思考中"而不是丢掉。
 */
import { http } from '@kit.NetworkKit';
import { hilog } from '@kit.PerformanceAnalysisKit';
import { BusinessError } from '@kit.BasicServicesKit';
import { util } from '@kit.ArkTS';
import knspike from 'libknspike.so';
import { probeBuffered } from './NativeHttpBridge';

const TAG: string = 'SEKB_HARMONY_UI';

/** 一次流式请求的结果。 */
export class StreamOutcome {
  ok: boolean = false;
  status: number = 0;
  /** 收到的字节数（跨 NAPI 的搬运量；用于区分"没回复"与"根本没连上"） */
  bytes: number = 0;
  /** 失败原因（含 BusinessError 的 code/message） */
  error: string = '';
  /** 正文（**由 Kotlin 侧从共享层事件里逐段回传**，本文件不解析） */
  text: string = '';
  /** 思考片段（共享层 `ChatEvent.Thinking`） */
  thinking: string = '';
  /** Kotlin 侧本轮汇总（事件数/首字延迟；判据用） */
  summary: string = '';

  constructor(ok: boolean, status: number, bytes: number, error: string, text: string,
              thinking: string, summary: string) {
    this.ok = ok;
    this.status = status;
    this.bytes = bytes;
    this.error = error;
    this.text = text;
    this.thinking = thinking;
    this.summary = summary;
  }
}

/** `chatStreamLine` 回来的一行小 JSON（字段名见 `ChatStreamPort.kt` 的 `Packet`）。 */
interface LinePacket {
  kind: string;
  piece?: string;
  role?: string;
  done?: boolean;
  error?: string;
  conversationId?: string;
  executionPlane?: string;
  executionModel?: string;
  escalated?: number;
  executionReason?: string;
}

/** OpenAI 兼容请求里的一条消息（显式接口：ArkTS 不允许无类型对象字面量）。 */
interface ChatTurn {
  role: string;
  content: string;
}

/** OpenAI 兼容的请求体。 */
interface ChatBody {
  model: string;
  messages: ChatTurn[];
  stream: boolean;
  reasoning_effort: string;
}

/** 把响应体按 `\n` 切行（**只切行**；`\r` 去掉）；返回未结束的尾部。 */
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
 * 一次流式聊天。`onDelta` 每收到一段正文增量就被调一次（**逐字渲染**）。
 */
export async function streamChat(
  baseUrl: string,
  model: string,
  userText: string,
  history: string[],
  timeoutSeconds: number,
  onDelta: (piece: string) => void,
  onThinking: (piece: string) => void,
): Promise<StreamOutcome> {
  let text: string = '';
  let thinking: string = '';
  let bytes: number = 0;
  let status: number = -1;
  let connError: string = '';
  let eventKind: string = '';

  // ① 让共享层起一轮新的行泵（重置计数；否则上一轮的事件会混进来）
  knspike.chatStreamBegin();

  // ② 组 messages（OpenAI 形状；**历史只放用户/助手文本**，与端侧 history 同口径）
  const messages: ChatTurn[] = [];
  for (const h of history) {
    if (h.length > 0) {
      messages.push({ role: 'user', content: h });
    }
  }
  messages.push({ role: 'user', content: userText });
  const body: ChatBody = {
    model: model,
    messages: messages,
    stream: true,
    // 与共享配置 disableThinking 一致（见文件头）
    reasoning_effort: 'none',
  };

  const options: http.HttpRequestOptions = {
    method: http.RequestMethod.POST,
    header: { 'Content-Type': 'application/json' },
    extraData: JSON.stringify(body),
    connectTimeout: 8000,
    readTimeout: timeoutSeconds * 1000,
  };

  const req: http.HttpRequest = http.createHttp();
  let pending: string = '';

  const handleLine = (line: string): void => {
    // ⚠️ 这一行是 H8 的核心接线：ArkTS 只搬行，**语义由共享层 SseParser 决定**
    const packetJson: string = knspike.chatStreamLine(line);
    if (packetJson.length === 0) {
      return;
    }
    let p: LinePacket;
    try {
      p = JSON.parse(packetJson) as LinePacket;
    } catch (e) {
      hilog.error(0x0000, TAG, 'chatStreamLine 返回非 JSON：%{public}s', packetJson.substring(0, 120));
      return;
    }
    eventKind = p.kind;
    if (p.kind === 'token' && p.piece !== undefined && p.piece.length > 0) {
      text += p.piece;
      onDelta(p.piece);
    } else if (p.kind === 'thinking' && p.piece !== undefined && p.piece.length > 0) {
      thinking += p.piece;
      onThinking(p.piece);
    } else if (p.kind === 'error') {
      eventKind = `error:${p.error !== undefined ? p.error : '?'}`;
    }
  };

  req.on('headersReceive', (h: Object) => {
    const obj = h as Record<string, Object>;
    const code: Object | undefined = obj['responseCode'];
    if (code !== undefined) {
      status = Number(code);
    }
  });
  req.on('dataReceive', (chunk: ArrayBuffer) => {
    bytes += chunk.byteLength;
    pending = drainLines(pending + util.TextDecoder.create('utf-8').decodeToString(new Uint8Array(chunk)),
      handleLine);
  });

  try {
    const code: number = await req.requestInStream(`${baseUrl}/v1/chat/completions`, options);
    if (status < 0) {
      status = code;
    }
    if (pending.length > 0) {
      handleLine(pending);
      pending = '';
    }
    // 收尾：共享层 `SseParser.finish()` 处理最后一段没被空行终止的数据
    knspike.chatStreamFinish();
  } catch (e) {
    const be = e as BusinessError;
    connError = `code=${be.code} msg=${be.message}`;
    hilog.error(0x0000, TAG, 'chat stream 失败 url=%{public}s %{public}s', baseUrl, connError);
    status = -1;
  } finally {
    req.off('dataReceive');
    req.off('headersReceive');
    req.destroy();
  }

  const summary: string = knspike.chatStreamSummary();
  hilog.info(0x0000, TAG,
    'chat stream %{public}s code=%{public}d bytes=%{public}d textChars=%{public}d last=%{public}s summary=%{public}s',
    baseUrl, status, bytes, text.length, eventKind, summary);
  const ok: boolean = status === 200 && text.length > 0;
  const err: string = ok ? '' : (connError.length > 0 ? connError
    : `HTTP ${status}；已收 ${bytes}B，正文 ${text.length} 字（事件=${eventKind}）`);
  return new StreamOutcome(ok, status, bytes, err, text, thinking, summary);
}

/**
 * 探活（设置页"网络与后端地址"）：GET `/api/tags`，解析模型名数量。
 *
 * 走的仍是 H3 已有的 `probeBuffered` 口径（ArkTS 发请求 → Kotlin 判定），
 * 这里只额外把模型名数出来给界面看。
 */
export async function probeEdge(baseUrl: string, timeoutSeconds: number): Promise<string> {
  const frame: string = await probeBuffered(`${baseUrl}/api/tags`, timeoutSeconds);
  const parts: string[] = frame.split('\n');
  const head: string = parts.length > 0 ? parts[0] : '';
  const body: string = parts.length > 1 ? parts.slice(1).join('\n') : '';
  const code: string = head.split(' ')[1] !== undefined ? head.split(' ')[1] : '-1';
  let models: number = -1;
  try {
    const obj = JSON.parse(body) as Record<string, Object>;
    const arr = obj['models'] as Object[];
    if (arr !== undefined && arr !== null) {
      models = arr.length;
    }
  } catch (e) {
    models = -1;
  }
  return `${code}|${models}|${body.length}`;
}
