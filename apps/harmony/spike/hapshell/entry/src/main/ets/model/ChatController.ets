/**
 * H8（工作流 G）· 聊天页的**编排**（端侧平面）。
 *
 * ## 编排在这里做了什么、没做什么（改动前必读）
 *
 * **做了什么**（每条都只有一份事实）：
 *   ① 决策：`routeOf(...)` → **共享层 `PlaneRouter`**（预算/原因码/DEVICE_ONLY 硬边界）；
 *   ② 传输：`streamChat(...)` → ArkTS HTTP + 按行搬家，**每行同步进 Kotlin**；
 *   ③ 语义：增量由**共享层 `SseParser`** 解出（`ChatTransport` 里那行 `chatStreamLine`）；
 *   ④ 来源：`ragRetrieve(...)` → **共享层 `Retriever`**（阈值/空间/隐私闸门同一份）；
 *   ⑤ 徽章：执行位置 + 原因码都挂在**气泡自己身上**（不是全局"最近决策"，
 *      否则往上翻历史就看不出这一条为什么上云——Android 踩过）。
 *
 * **刻意没做**（如实说明，见 README 的"未验/未成"）：
 *   · **端侧失败自动改道云端**：那要 `ChatOrchestrator`（同步接口 + `HttpTransport` 注入），
 *     而鸿蒙的 HTTP 只能在 ArkTS 发，Kotlin 无法在其中同步等待（结构性死锁，H3 已论证）。
 *     所以本端只做"改道判定"（共享层 `route()` 给出 cloud 时不发端侧请求），
 *     不做"端侧答到一半失败再整段重发云端"。
 *   · **上报 `RouteEventPayload`**：需要 `SekbApi` + 设备凭证，而 enroll/refresh 无账号未接
 *     （设置页如实显示"未接入"）。
 *   · **前缀守卫 `StreamGuard`**：它需要"在流式过程中同步决定是否改道"，
 *     与上一条同因（无法在流中反向驱动 ArkTS）。
 */
import { hilog } from '@kit.PerformanceAnalysisKit';
import { AppCtx } from './AppCtx';
import { Conversation, ChatMsg, RouteInfo, Source } from './SekbTypes';
import { deriveTitle } from './HistoryStore';
import { streamChat, StreamOutcome, probeEdge } from './ChatTransport';
import { ragDocuments, ragRetrieve, routeOf } from './SekbNative';

const TAG: string = 'SEKB_HARMONY_UI';

/** 一次问答的完整事实（页面把它挂在气泡上）。 */
export class TurnResult {
  text: string = '';
  plane: string = '';
  reason: string = '';
  escalateReason: string = '';
  escalated: number = 0;
  clientEscalated: boolean = false;
  sources: Source[] = [];
  error: string = '';
  ttftNote: string = '';
}

/**
 * 聊天控制器：持有消息列表与会话身份，**通过 `onChange` 通知页面重绘**。
 *
 * 为什么不用 ArkUI 的状态框架：控制器要跑异步流式，而 `@State` 只观察组件内的赋值。
 * 一个 `onChange` 回调（页面里 `this.messages = ctrl.snapshot()`）比引入
 * `@ObservedV2`/`@Track` 更小、更好懂，也和 Android 的"单向状态流"等价。
 */
export class ChatController {
  messages: ChatMsg[] = [];
  conversationId: string = '';
  title: string = '';
  busy: boolean = false;
  thinking: string = '';
  lastError: string = '';
  /** 端侧端点可达性（null = 还没探过） */
  edgeReachable: boolean | null = null;
  /** 最近一次决策的说明行（页面底部"为什么"用） */
  lastReasonLine: string = '';
  /** 端侧嵌入是否就绪（决定"检索来源"这一列有没有意义） */
  ragReady: boolean = false;
  ragNote: string = '';

  private ctx: AppCtx;
  private seq: number = 0;
  private onChange: () => void;

  constructor(ctx: AppCtx, onChange: () => void) {
    this.ctx = ctx;
    this.onChange = onChange;
    this.newConversation(false);
    this.restoreLatest();
  }

  /**
   * 换一个"状态变化"通知者。
   *
   * 为什么需要它：控制器在 `AppShell` 里创建（页面还没挂载），而输入栏在**外层**、
   * 发送要落到 `ChatPage`。所以 ChatPage 挂载时把自己注册进来，之后
   * "流式增量 / busy 变化"都从这里推给页面。只有一个订阅者，够用且没有多余机制。
   */
  setOnChange(cb: () => void): void {
    this.onChange = cb;
  }

  // ────────────────────────── 会话管理 ──────────────────────────

  /** 开一段新对话（旧的那段已经落盘，不会丢）。 */
  newConversation(notify: boolean = true): void {
    this.conversationId = `c${Date.now()}-${Math.floor(Math.random() * 100000)}`;
    this.title = '';
    this.messages = [];
    this.lastError = '';
    this.thinking = '';
    this.lastReasonLine = '';
    // 记指针：新对话是空的（没有文件），重启后应仍是空对话
    this.ctx.history.saveCurrentId(this.conversationId);
    if (notify) {
      this.onChange();
    }
  }

  /** 启动时恢复最近一次会话（优先按"当前会话指针"）。 */
  restoreLatest(): void {
    const pointed: string = this.ctx.history.currentId();
    if (pointed.length > 0) {
      const conv = this.ctx.history.get(pointed);
      if (conv !== null && conv.messages.length > 0) {
        this.loadConversation(conv);
        return;
      }
    }
    const all: Conversation[] = this.ctx.history.list();
    if (all.length > 0) {
      this.loadConversation(all[0]);
    }
  }

  /** 把一段已落盘的会话装进界面（历史页点开 / 启动恢复走**同一条**路）。 */
  loadConversation(conv: Conversation): void {
    this.conversationId = conv.id;
    this.title = conv.title;
    this.messages = conv.messages;
    this.lastError = '';
    this.thinking = '';
    this.ctx.history.saveCurrentId(conv.id);
  }

  /** 打开历史里的某一段。 */
  openConversation(id: string): void {
    const conv = this.ctx.history.get(id);
    if (conv === null) {
      return;
    }
    this.loadConversation(conv);
    this.onChange();
  }

  /** 删除一段会话；删的若是当前这段，顺带清空界面（避免"看着已删的对话继续提问"）。 */
  deleteConversation(id: string): void {
    this.ctx.history.delete(id);
    if (id === this.conversationId) {
      this.newConversation(false);
    }
    this.onChange();
  }

  /** 会话列表（历史页用；最近更新在前）。 */
  conversationList(): Conversation[] {
    return this.ctx.history.list();
  }

  /** 会话存储是否可写（历史页/设置页如实显示）。 */
  storageWritable(): boolean {
    return this.ctx.history.writable();
  }

  /** 落盘（在状态更新**之后**调用：存下来的就是用户实际看到的那一版）。 */
  persist(): void {
    if (this.messages.length === 0) {
      return;
    }
    if (this.title.length === 0) {
      const first: ChatMsg | null = this.messages.find((m: ChatMsg) => m.fromUser) ?? null;
      this.title = first === null ? '新对话' : deriveTitle(first.text);
    }
    const conv = new Conversation(this.conversationId, this.title, Date.now());
    conv.messages = this.messages;
    const ok: boolean = this.ctx.history.save(conv, true);
    if (!ok) {
      this.lastError = '会话落盘失败（存储不可写）；本次对话只在内存里';
    }
  }

  // ────────────────────────── 检索来源 ──────────────────────────

  /** 探端侧端点（只影响提示与可诊断性，不参与路由决策——决策在共享层）。 */
  async probe(): Promise<void> {
    try {
      const r: string = await probeEdge(this.ctx.settings.edgeBaseUrl, 6);
      const code: string = r.split('|')[0];
      this.edgeReachable = code === '200';
    } catch (e) {
      this.edgeReachable = false;
    }
    this.onChange();
  }

  /** 端侧文档库是否可用（文档页/设置页会各自再报一次，这里只缓存结论给聊天页）。 */
  markRag(ready: boolean, note: string): void {
    this.ragReady = ready;
    this.ragNote = note;
  }

  // ────────────────────────── 一次问答 ──────────────────────────

  /**
   * 发一条消息：决策（共享层）→ 端侧流式（逐字）→ 定稿（徽章 + 来源）。
   *
   * 注意：**端侧嵌入/检索失败不影响回答**——来源列表为空时如实说明，
   * 而不是把整轮对话判失败（端侧方案里"能答但没出处"比"直接报错"有用）。
   */
  async send(userText: string): Promise<void> {
    const q: string = userText.trim();
    if (q.length === 0 || this.busy) {
      return;
    }
    this.seq++;
    const userMsg = new ChatMsg(`u${this.seq}`, true, q, Date.now());
    this.messages.push(userMsg);
    const answer = new ChatMsg(`a${this.seq}`, false, '', Date.now());
    answer.streaming = true;
    this.messages.push(answer);
    this.busy = true;
    this.thinking = '';
    this.lastError = '';
    this.lastReasonLine = '';
    this.onChange();

    // ① 决策（**共享层 PlaneRouter**）：输入预算 / 输出预算 / DEVICE_ONLY 硬边界
    const historyUserOnly: string[] = this.messages
      .filter((m: ChatMsg) => m.fromUser && m.id !== userMsg.id)
      .map((m: ChatMsg) => m.text);
    const route: RouteInfo = routeOf(q, historyUserOnly, false,
      this.ctx.settings.edgeBaseUrl, this.ctx.settings.sekbBaseUrl);
    this.lastReasonLine = `${route.plane} · ${route.reason}`;

    // ② 被拦下（DEVICE_ONLY + 非本机端侧端点）→ **一个请求都不发**，如实说清原因
    if (route.blocked) {
      answer.streaming = false;
      answer.plane = 'edge';
      answer.reason = route.reason;
      answer.escalateReason = `escalation_blocked:${route.blockedReason}`;
      answer.error = `设备专属内容不能发往非本机端点（${route.blockedReason}）；本次请求未发出。`;
      this.finishTurn(answer, '', false);
      return;
    }

    // ③ 决策为云端：本机**没有**可用的云端平面（无账号/无后端），如实说明而不是偷偷改本机
    if (route.plane === 'cloud') {
      answer.streaming = false;
      answer.plane = 'cloud';
      answer.reason = route.reason;
      answer.error = '共享层判定这次应当由云端完成，但本端尚未接入云端平面（无账号/无后端地址可用）。';
      this.finishTurn(answer, '', false);
      return;
    }

    // ④ 端侧流式（ArkTS 搬行、共享层解析；见 ChatTransport 的职责表）
    const history: string[] = historyUserOnly.slice(-6);
    let outcome: StreamOutcome;
    try {
      outcome = await streamChat(
        this.ctx.settings.edgeBaseUrl, this.ctx.settings.model, q, history, 120,
        (piece: string) => {
          answer.text += piece;
          this.onChange();
        },
        (piece: string) => {
          this.thinking += piece;
          this.onChange();
        },
      );
    } catch (e) {
      answer.streaming = false;
      answer.clientEscalated = true;
      answer.plane = 'cloud';
      answer.reason = route.reason;
      answer.error = `端侧调用抛错：${e}`;
      this.finishTurn(answer, '', false);
      return;
    }

    answer.streaming = false;
    if (!outcome.ok) {
      // 端侧不可用：**如实报**（本端没有可用的云端平面可改道，不假装成功）
      answer.clientEscalated = true;
      answer.plane = 'cloud';
      answer.reason = route.reason;
      answer.escalateReason = 'edge_unavailable';
      answer.error = `端侧未能完成（${outcome.error}）。本端尚未接入云端平面，因此没有改道。`;
      this.finishTurn(answer, outcome.summary, false);
      return;
    }

    answer.plane = 'edge';
    answer.reason = route.reason;
    this.finishTurn(answer, outcome.summary, true);

    // ⑤ 检索来源（**共享层 Retriever**）：放在回答定稿之后，绝不阻塞首字
    await this.attachSources(answer, q);
  }

  /** 定稿：busy 复位 + 落盘 + 通知（顺序固定，避免"存的是半截"）。 */
  private finishTurn(answer: ChatMsg, summary: string, keepText: boolean): void {
    this.busy = false;
    this.thinking = '';
    if (!keepText && answer.text.length === 0) {
      answer.text = '';
    }
    hilog.info(0x0000, TAG, 'turn done plane=%{public}s reason=%{public}s err=%{public}s summary=%{public}s',
      answer.plane, answer.reason, answer.error, summary);
    this.persist();
    this.onChange();
  }

  /**
   * 把本机检索来源挂到这条回答上。
   *
   * 三条纪律：
   *   ① 来源是**加分项**，失败不改变回答本身（只写一行说明）；
   *   ② 空结果是**正常结果**（阈值 0.40 下"库里没有"就该 0 命中，不许硬塞）；
   *   ③ 阈值与空间戳都来自共享层，不在这里重算。
   */
  private async attachSources(answer: ChatMsg, query: string): Promise<void> {
    if (!this.ragReady) {
      answer.sources = [];
      if (this.ragNote.length > 0) {
        this.lastReasonLine = `${this.lastReasonLine} ｜ 检索未启用：${this.ragNote}`;
      }
      this.onChange();
      return;
    }
    try {
      const docs = ragDocuments();
      if (!docs.ok || docs.docs.length === 0) {
        answer.sources = [];
        this.lastReasonLine = `${this.lastReasonLine} ｜ 本机文档库为空（先到「文档」页导入）`;
        this.onChange();
        return;
      }
      const r = ragRetrieve(query, 3);
      if (!r.ok) {
        answer.sources = [];
        this.lastReasonLine = `${this.lastReasonLine} ｜ 检索失败：${r.reason}`;
      } else if (!r.searchable) {
        answer.sources = [];
        this.lastReasonLine = `${this.lastReasonLine} ｜ 不可检索：${r.reason}`;
      } else {
        answer.sources = r.hits;
        if (r.hits.length === 0) {
          this.lastReasonLine = `${this.lastReasonLine} ｜ 本机资料里没有相关片段（阈值 ${r.threshold}）`;
        }
      }
    } catch (e) {
      answer.sources = [];
      this.lastReasonLine = `${this.lastReasonLine} ｜ 检索异常：${e}`;
    }
    this.persist();
    this.onChange();
  }
}

/** 空状态示例问题：与 Android 端一致，都是这台设备真的答得了的。 */
export const SAMPLE_QUESTIONS: string[] = [
  '端侧推理为什么更省电？',
  '我本机的知识库里有什么？',
  '用一句话解释什么是检索增强生成',
];
