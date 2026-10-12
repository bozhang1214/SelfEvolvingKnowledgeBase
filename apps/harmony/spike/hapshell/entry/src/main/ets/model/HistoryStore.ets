/**
 * H8（工作流 G）· 会话/文档的**本地持久化**（ArkTS 侧）。
 *
 * ## 为什么现在就能落盘（而不是"等 RDB 就绪"）
 *
 * 三种东西的**归属**不同，不该混在一起等一个依赖：
 *
 * | 数据 | 放在哪 | 理由 |
 * |---|---|---|
 * | 会话（标题/气泡/执行位置/来源） | **JSON 文件**（本文件） | 是 UI 状态，不需要向量检索；RDB 挂了也不该丢聊天记录 |
 * | 文档正文 + 向量 | 共享层 `KnowledgeIndex` + RDB（`SekbAppPort.kt`） | 需要检索；F 的实现 |
 *
 * 所以即使 F 的 RDB 链路不绿，历史页**仍然是真能力**（真的跨重启存活），
 * 文档页则如实降级成"未持久化 + 原因"（见设置页与文档页）。
 *
 * ## 写入是原子替换的（不是"清空再写"）
 *
 * `fileIo.writeSync` 直接覆盖目标文件时，一旦进程在中途被杀就会留下**半截 JSON**，
 * 下次启动整段历史消失。所以这里先写 `*.tmp` 再 `renameSync` 覆盖——
 * 崩溃最坏结果是"丢掉最后一次写入"，而不是"丢掉全部"。
 */
import { fileIo } from '@kit.CoreFileKit';
import { hilog } from '@kit.PerformanceAnalysisKit';
import { util } from '@kit.ArkTS';
import { ChatMsg, Conversation, Source } from './SekbTypes';

const TAG: string = 'SEKB_HARMONY_UI';

/** 落盘用的 DTO（避免直接序列化带方法的类实例）。 */
interface StoredSource {
  sourceId: string;
  score: number;
  snippet: string;
}

interface StoredMessage {
  id: string;
  fromUser: boolean;
  text: string;
  plane: string;
  escalated: number;
  reason: string;
  escalateReason: string;
  clientEscalated: boolean;
  sources: StoredSource[];
  atMillis: number;
}

interface StoredConversation {
  id: string;
  title: string;
  updatedAt: number;
  messages: StoredMessage[];
}

interface StoredIndex {
  currentId: string;
  conversations: StoredConversation[];
}

/** 把一条 `ChatMsg` 摊成可序列化的 DTO。 */
function toStored(m: ChatMsg): StoredMessage {
  const sources: StoredSource[] = [];
  for (const s of m.sources) {
    sources.push({ sourceId: s.sourceId, score: s.score, snippet: s.snippet });
  }
  return {
    id: m.id, fromUser: m.fromUser, text: m.text, plane: m.plane, escalated: m.escalated,
    reason: m.reason, escalateReason: m.escalateReason, clientEscalated: m.clientEscalated,
    sources: sources, atMillis: m.atMillis,
  };
}

/** 还原一条 `ChatMsg`（流式与错误标记不落盘：重启后不该还是"正在接收"）。 */
function fromStored(s: StoredMessage): ChatMsg {
  const m = new ChatMsg(s.id, s.fromUser, s.text, s.atMillis);
  m.plane = s.plane === 'cloud' ? 'cloud' : (s.plane === 'edge' ? 'edge' : '');
  m.escalated = s.escalated;
  m.reason = s.reason;
  m.escalateReason = s.escalateReason;
  m.clientEscalated = s.clientEscalated;
  const src: Source[] = [];
  if (s.sources !== undefined && s.sources !== null) {
    for (const x of s.sources) {
      src.push(new Source(x.sourceId, x.score, x.snippet));
    }
  }
  m.sources = src;
  m.streaming = false;
  m.error = '';
  return m;
}

/**
 * 会话存储：一个 JSON 文件装全部会话（历史规模在端侧是几十段量级，不值得上 RDB）。
 */
export class HistoryStore {
  private dir: string;

  constructor(dir: string) {
    this.dir = dir;
    try {
      if (!fileIo.accessSync(dir)) {
        fileIo.mkdirSync(dir);
      }
    } catch (e) {
      hilog.error(0x0000, TAG, '创建会话目录失败：%{public}s', JSON.stringify(e));
    }
  }

  private file(): string {
    return `${this.dir}/conversations.json`;
  }

  private load(): StoredIndex {
    const f: string = this.file();
    const empty: StoredIndex = { currentId: '', conversations: [] };
    try {
      if (!fileIo.accessSync(f)) {
        return empty;
      }
      const stat = fileIo.statSync(f);
      if (stat.size <= 0) {
        return empty;
      }
      const buf = new ArrayBuffer(stat.size);
      const rd = fileIo.openSync(f, fileIo.OpenMode.READ_ONLY);
      try {
        fileIo.readSync(rd.fd, buf);
      } finally {
        fileIo.closeSync(rd);
      }
      const text: string = util.TextDecoder.create('utf-8').decodeToString(new Uint8Array(buf));
      const parsed = JSON.parse(text) as StoredIndex;
      if (parsed.conversations === undefined || parsed.conversations === null) {
        return empty;
      }
      return parsed;
    } catch (e) {
      // 半截文件/格式变化：当作空库，但**必须留下日志**（静默丢历史是最坏的选择）
      hilog.error(0x0000, TAG, '会话文件读取失败（按空库处理）：%{public}s', JSON.stringify(e));
      return empty;
    }
  }

  /** 写整份索引（私有：外部只该用 save/delete/saveCurrentId 这三个语义入口）。 */
  write(idx: StoredIndex): boolean {
    const f: string = this.file();
    const tmp: string = `${f}.tmp`;
    try {
      const text: string = JSON.stringify(idx);
      const buf: ArrayBuffer = new util.TextEncoder().encodeInto(text).buffer as ArrayBuffer;
      const w = fileIo.openSync(tmp, fileIo.OpenMode.CREATE | fileIo.OpenMode.READ_WRITE | fileIo.OpenMode.TRUNC);
      try {
        fileIo.writeSync(w.fd, buf);
      } finally {
        fileIo.closeSync(w);
      }
      // 原子替换：先写 tmp 再改名（见文件头）
      fileIo.renameSync(tmp, f);
      return true;
    } catch (e) {
      hilog.error(0x0000, TAG, '会话文件写入失败：%{public}s', JSON.stringify(e));
      return false;
    }
  }

  /** 全部会话（最近更新在前）。 */
  list(): Conversation[] {
    const idx = this.load();
    const out: Conversation[] = [];
    for (const c of idx.conversations) {
      const conv = new Conversation(c.id, c.title, c.updatedAt);
      for (const m of c.messages) {
        conv.messages.push(fromStored(m));
      }
      out.push(conv);
    }
    out.sort((a: Conversation, b: Conversation) => b.updatedAt - a.updatedAt);
    return out;
  }

  /** 当前会话指针（"新对话"是空的、没有消息，只能靠指针恢复）。 */
  currentId(): string {
    return this.load().currentId;
  }

  get(id: string): Conversation | null {
    for (const c of this.list()) {
      if (c.id === id) {
        return c;
      }
    }
    return null;
  }

  /** 保存/覆盖一段会话，并把它设为当前会话。 */
  save(conv: Conversation, makeCurrent: boolean): boolean {
    const idx = this.load();
    const next: StoredConversation[] = [];
    const stored: StoredConversation = {
      id: conv.id, title: conv.title, updatedAt: conv.updatedAt,
      messages: conv.messages.map((m: ChatMsg) => toStored(m)),
    };
    let replaced: boolean = false;
    for (const c of idx.conversations) {
      if (c.id === conv.id) {
        next.push(stored);
        replaced = true;
      } else {
        next.push(c);
      }
    }
    if (!replaced) {
      next.push(stored);
    }
    const out: StoredIndex = { currentId: makeCurrent ? conv.id : idx.currentId, conversations: next };
    return this.write(out);
  }

  /** 只更新"当前会话指针"（新对话用：它还没有消息，不该落一条空记录）。 */
  saveCurrentId(id: string): boolean {
    const idx = this.load();
    return this.write({ currentId: id, conversations: idx.conversations });
  }

  /** 删除一段会话；返回是否真的删掉了。 */
  delete(id: string): boolean {
    const idx = this.load();
    const next: StoredConversation[] = [];
    let hit: boolean = false;
    for (const c of idx.conversations) {
      if (c.id === id) {
        hit = true;
      } else {
        next.push(c);
      }
    }
    const current: string = idx.currentId === id ? '' : idx.currentId;
    this.write({ currentId: current, conversations: next });
    return hit;
  }

  /** 存储是否可写（设置页要如实显示这个事实）。 */
  writable(): boolean {
    try {
      const probe: string = `${this.dir}/.write-probe`;
      const w = fileIo.openSync(probe, fileIo.OpenMode.CREATE | fileIo.OpenMode.READ_WRITE);
      fileIo.closeSync(w);
      fileIo.unlinkSync(probe);
      return true;
    } catch (e) {
      return false;
    }
  }
}

/** 标题推导（与 Android `ConversationCodec.deriveTitle` 同口径：首条用户消息前 16 字）。 */
export function deriveTitle(text: string): string {
  const t: string = text.trim().replace('\n', ' ');
  if (t.length === 0) {
    return '新对话';
  }
  return t.length > 16 ? `${t.substring(0, 16)}…` : t;
}
