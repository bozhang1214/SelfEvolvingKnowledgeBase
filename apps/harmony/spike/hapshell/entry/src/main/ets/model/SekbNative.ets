/**
 * H8（工作流 G）· NAPI 入口的 **ArkTS 包装**（只做 JSON 拆包，不做语义判断）。
 *
 * ## 为什么单独一个文件
 *
 * ① 让页面只依赖"类型化的小结果"，而不是到处 `JSON.parse`；
 * ② 语义（哪些字段、失败怎么回落）都在 Kotlin/共享层，这里的形状跟着
 *    `ChatStreamPort.kt` / `SekbAppPort.kt` 的 `Packet`/`JsonObject` 走；
 * ③ 这是**唯一**知道 NAPI 方法名的地方，改名只改这里。
 *
 * ⚠️ 纪律：本文件**不翻译原因码、不判断对错**，只把 `ok`/`reason` 如实带出去。
 * 原因码的文案在 `SekbTypes.explainReason`（共享层口径）；`reason` 认不出就原样显示。
 */
import knspike from 'libknspike.so';
import { fileIo } from '@kit.CoreFileKit';
import { hilog } from '@kit.PerformanceAnalysisKit';
import { util } from '@kit.ArkTS';
import { DocInfo, RouteInfo, Source } from './SekbTypes';

const TAG: string = 'SEKB_HARMONY_UI';

/** `chatRoute`：共享层 `PlaneRouter` 的决策。 */
export function routeOf(question: string, history: string[], deviceData: boolean,
                        edgeBaseUrl: string, sekbBaseUrl: string): RouteInfo {
  const hist: string = history.join('\u0000');
  try {
    const json: string = knspike.chatRoute(question, hist, deviceData ? '1' : '0', edgeBaseUrl, sekbBaseUrl);
    return RouteInfo.fromJson(JSON.parse(json) as RouteInfo);
  } catch (e) {
    // 桩/旧产物：调用会抛（undefined symbol）。**如实回报**，不假装有决策。
    const r = new RouteInfo();
    r.reason = `edge_unavailable:route_bridge(${e})`;
    r.plane = '';
    return r;
  }
}

/** `ragOpen` 的结果。 */
export class RagOpenResult {
  ok: boolean = false;
  reason: string = '';
  space: string = '';
  dim: number = 0;
  millis: number = 0;
  db: string = '';
}

/** `ragDocuments` 的结果。 */
export class RagDocsResult {
  ok: boolean = false;
  reason: string = '';
  docs: DocInfo[] = [];
  chunks: number = 0;
  space: string = '';
  dim: number = 0;
  db: string = '';
}

/** `ragIngest` / `ragRemove` 的结果（两个入口字段不同，合成一个便于页面复用）。 */
export class RagOpResult {
  ok: boolean = false;
  reason: string = '';
  chunks: number = 0;
  zeroFilled: number = 0;
  millis: number = 0;
  removed: number = 0;
  remaining: number = 0;
  sources: number = 0;
  note: string = '';
  /** 写入后**立即回读**的条数（Kotlin 侧加；0 = 写入/可见性问题，≥1 = 问题在读取侧）。 */
  dbChunks: number = -1;
  /** `all()` 回读条数（同上，用于区分"表里没有"与"读法不对"）。 */
  allRows: number = -1;
  /** `size()` 失败原因（有值说明读取路径抛了异常，别只看 -1）。 */
  sizeErr: string = '';
  /** `all()` 失败原因（同上）。 */
  allErr: string = '';
  /** **原始 JSON 报文**（不含在正常解析里，只用于诊断"跑的是哪个版本的产物"）。 */
  rawJson: string = '';
  /** 事务诊断：`begin=? before=? inTx=? commit=? after=?`（一眼判定事务是否生效）。 */
  tx: string = '';
  /** 写后回读轮询了几次（`1` = 一次就读到；`4` = 四次都没读到，说明可见性/写入确有问题）。 */
  pollRounds: number = 0;
  /** 文档登记表的回读结果（与切片表分开：两者可能一个对一个错）。 */
  docsCount: number = -1;
  docsChunks: number = -1;
  docRounds: number = 0;
}

/** `ragRetrieve` 的结果。 */
/** 检索命中（NAPI 回来的形状：`{"source","score","snippet"}`）。 */
interface RawHit {
  /**
   * ⚠️ **Kotlin 侧发的字段名是 `source`，不是 `sourceId`**（2026-10-26 主线查明的真缺陷）。
   * 这里原来声明成 `sourceId` 并直接 `h.sourceId` 取用 → 永远 `undefined` → `Source.sourceId=''`，
   * 于是**检索来源名在界面上恒为空**、自检里 `hits[0].sourceId === srcId` 永远不成立。
   * 症状极具误导性：向量检索能给出合理分数，只有"来源"是空的，看起来像"检索没命中"。
   * 两个字段都声明（`source` 为准，`sourceId` 兼容），取值时走 `??` 兜底。
   */
  source?: string;
  sourceId?: string;
  score: number;
  snippet: string;
  /** Kotlin 侧另发的命中行 id（`<sourceId>#<片号>`）与向量维度，用于诊断"命中哪一行、是否零向量"。 */
  id?: string;
  dim?: number;
}

export class RagHitsResult {
  ok: boolean = false;
  reason: string = '';
  searchable: boolean = false;
  hits: Source[] = [];
  threshold: number = 0;
  embedMillis: number = 0;
  searchMillis: number = 0;
  space: string = '';
  cloudCompatible: boolean = false;
}

/** 打开文档库（模型 + RDB 索引）。**失败不抛**，`ok=false` + `reason`。 */
export function ragOpen(modelPath: string, vocabText: string, dbDir: string,
                        dbName: string = ''): RagOpenResult {
  const r = new RagOpenResult();
  try {
    const o = JSON.parse(knspike.ragOpen(modelPath, vocabText, dbDir, dbName)) as RagOpenResult;
    r.ok = o.ok === true;
    r.reason = o.reason !== undefined ? o.reason : '';
    r.space = o.space !== undefined ? o.space : '';
    r.dim = o.dim !== undefined ? o.dim : 0;
    r.millis = o.millis !== undefined ? o.millis : 0;
    r.db = o.db !== undefined ? o.db : '';
  } catch (e) {
    r.ok = false;
    r.reason = `桥不可用（libkn.so 是旧产物？）：${e}`;
  }
  return r;
}

/** 文档列表。 */
export function ragDocuments(): RagDocsResult {
  const r = new RagDocsResult();
  try {
    const o = JSON.parse(knspike.ragDocuments()) as RagDocsResult;
    r.ok = o.ok === true;
    r.reason = o.reason !== undefined ? o.reason : '';
    r.chunks = o.chunks !== undefined ? o.chunks : 0;
    r.space = o.space !== undefined ? o.space : '';
    r.dim = o.dim !== undefined ? o.dim : 0;
    r.db = o.db !== undefined ? o.db : '';
    const docs: DocInfo[] = [];
    if (o.docs !== undefined && o.docs !== null) {
      for (const d of o.docs) {
        docs.push(new DocInfo(d.id, d.name, d.chunks, d.bytes, d.addedAt));
      }
    }
    r.docs = docs;
  } catch (e) {
    r.ok = false;
    r.reason = `桥不可用：${e}`;
  }
  return r;
}

/** 导入一段文本。 */
export function ragIngest(sourceId: string, name: string, text: string, sizeBytes: number): RagOpResult {
  const r = new RagOpResult();
  try {
    // 保留**原始 JSON**（2026-10-26 主线加）：连着几轮"诊断字段读不到"时，唯一能终结猜测的
    // 就是原始报文本身——它同时证明"跑的是哪个版本的 Kotlin 产物"。
    const raw: string = knspike.ragIngest(sourceId, name, text, `${sizeBytes}`);
    r.rawJson = raw;
    const o = JSON.parse(raw) as RagOpResult;
    r.ok = o.ok === true;
    r.reason = o.reason !== undefined ? o.reason : '';
    r.chunks = o.chunks !== undefined ? o.chunks : 0;
    r.zeroFilled = o.zeroFilled !== undefined ? o.zeroFilled : 0;
    r.millis = o.millis !== undefined ? o.millis : 0;
    r.note = o.note !== undefined ? o.note : '';
    // ⚠️ **这四个字段原来只加在类上、没在这里解析**（2026-10-26 主线自查到的真缺陷）：
    //    于是它们恒为类默认值 -1/''，报告里看起来像"Kotlin 侧诊断没执行"，
    //    害我把方向错判成"产物陈旧/打包不同源"。
    //    这正是我自己加的那条断言 `rag_diag_fields_present` 抓出来的——断言写对了，问题在解析层。
    r.dbChunks = o.dbChunks !== undefined ? o.dbChunks : -1;
    r.allRows = o.allRows !== undefined ? o.allRows : -1;
    r.sizeErr = o.sizeErr !== undefined ? o.sizeErr : '';
    r.allErr = o.allErr !== undefined ? o.allErr : '';
    r.tx = o.tx !== undefined ? o.tx : '';
    r.pollRounds = o.pollRounds !== undefined ? o.pollRounds : 0;
    r.docsCount = o.docsCount !== undefined ? o.docsCount : -1;
    r.docsChunks = o.docsChunks !== undefined ? o.docsChunks : -1;
    r.docRounds = o.docRounds !== undefined ? o.docRounds : 0;
  } catch (e) {
    r.ok = false;
    r.reason = `桥不可用：${e}`;
  }
  return r;
}

/** 单篇删除（走共享层 `KnowledgeIndex.remove` → `deleteBySource`，不误清全库）。 */
export function ragRemove(id: string): RagOpResult {
  const r = new RagOpResult();
  try {
    const o = JSON.parse(knspike.ragRemove(id)) as RagOpResult;
    r.ok = o.ok === true;
    r.reason = o.reason !== undefined ? o.reason : '';
    r.removed = o.removed !== undefined ? o.removed : 0;
    r.remaining = o.remaining !== undefined ? o.remaining : 0;
    r.sources = o.sources !== undefined ? o.sources : 0;
    r.note = o.note !== undefined ? o.note : '';
  } catch (e) {
    r.ok = false;
    r.reason = `桥不可用：${e}`;
  }
  return r;
}

/** 检索（聊天页"检索来源"）。`deviceOnly` 恒为 true（本机文档即设备专属）。 */
export function ragRetrieve(query: string, topK: number): RagHitsResult {
  const r = new RagHitsResult();
  try {
    const o = JSON.parse(knspike.ragRetrieve(query, `${topK}`)) as RagHitsResult;
    r.ok = o.ok === true;
    r.reason = o.reason !== undefined ? o.reason : '';
    r.searchable = o.searchable === true;
    r.threshold = o.threshold !== undefined ? o.threshold : 0;
    r.embedMillis = o.embedMillis !== undefined ? o.embedMillis : 0;
    r.searchMillis = o.searchMillis !== undefined ? o.searchMillis : 0;
    r.space = o.space !== undefined ? o.space : '';
    r.cloudCompatible = o.cloudCompatible === true;
    const hits: Source[] = [];
    if (o.hits !== undefined && o.hits !== null) {
      for (const h of o.hits as RawHit[]) {
        // 取 `source`（Kotlin 侧真名），再退 `sourceId`（旧声明）；两者都没有才用空串
        const src: string = h.source !== undefined && h.source !== null
          ? h.source
          : (h.sourceId !== undefined && h.sourceId !== null ? h.sourceId : '');
        const hit = new Source(src, h.score, h.snippet);
        hit.id = h.id !== undefined ? h.id : '';
        hit.dim = h.dim !== undefined ? h.dim : -1;
        hits.push(hit);
      }
    }
    r.hits = hits;
  } catch (e) {
    r.ok = false;
    r.reason = `桥不可用：${e}`;
  }
  return r;
}

/** 设置检索阈值（共享层回落规则：非法值 → 0.40）。返回生效值。 */
export function ragSetThreshold(v: number): number {
  try {
    const s: string = knspike.ragSetThreshold(`${v}`);
    const n: number = Number.parseFloat(s);
    return Number.isNaN(n) ? 0.4 : n;
  } catch (e) {
    return 0.4;
  }
}

/** 端侧嵌入状态（模型/空间戳/维度）。 */
export class EmbedStatus {
  ok: boolean = false;
  modelFile: string = '';
  modelPath: string = '';
  space: string = '';
  dim: number = 0;
  openMillis: number = 0;
  diagnostics: string = '';
}

export function ragEmbedStatus(): EmbedStatus {
  const r = new EmbedStatus();
  try {
    const o = JSON.parse(knspike.ragEmbedStatus()) as EmbedStatus;
    r.ok = o.ok === true;
    r.modelFile = o.modelFile !== undefined ? o.modelFile : '';
    r.modelPath = o.modelPath !== undefined ? o.modelPath : '';
    r.space = o.space !== undefined ? o.space : '';
    r.dim = o.dim !== undefined ? o.dim : 0;
    r.openMillis = o.openMillis !== undefined ? o.openMillis : 0;
    r.diagnostics = o.diagnostics !== undefined ? o.diagnostics : '';
  } catch (e) {
    r.diagnostics = `桥不可用：${e}`;
  }
  return r;
}

/** 一行摘要（设置页/自检表）。 */
export function ragSummary(): string {
  try {
    return knspike.ragSummary();
  } catch (e) {
    return `桥不可用：${e}`;
  }
}

/** 状态（`ok=false` 时 `reason` 说明为什么不可用）。 */
export function ragStatus(): RagOpResult {
  const r = new RagOpResult();
  try {
    const o = JSON.parse(knspike.ragStatus()) as RagOpenResult;
    r.ok = o.ok === true;
    r.reason = o.reason !== undefined ? o.reason : '';
  } catch (e) {
    r.ok = false;
    r.reason = `桥不可用：${e}`;
  }
  return r;
}

// ─────────────────────────────────────────────────────────────────────────────
// 文档导入的**文件读取**（放在这里而不是页面里：页面只管展示，读盘要能被复用/单测口径统一）
// ─────────────────────────────────────────────────────────────────────────────

/** 导入目录：`<filesDir>/import`（用户把 .md/.txt 放到这里，文档页列出来点一下导入）。 */
export function importDirName(): string {
  return 'import';
}

/**
 * 读一个文本文件（UTF-8）。返回空串表示读失败（原因见 hilog）。
 *
 * ⚠️ 只做 UTF-8 文本：**PDF 后置**（Android 用 PDFBox；鸿蒙侧没有等价物，
 * 硬做会引入一个未经评估的原生依赖）。见 README 的"未验/未成"。
 */
export function readTextFile(path: string): string {
  try {
    if (!fileIo.accessSync(path)) {
      return '';
    }
    const size: number = fileIo.statSync(path).size;
    if (size <= 0) {
      return '';
    }
    const buf = new ArrayBuffer(size);
    const rd = fileIo.openSync(path, fileIo.OpenMode.READ_ONLY);
    try {
      fileIo.readSync(rd.fd, buf);
    } finally {
      fileIo.closeSync(rd);
    }
    return util.TextDecoder.create('utf-8').decodeToString(new Uint8Array(buf));
  } catch (e) {
    hilog.error(0x0000, TAG, '读文本失败 %{public}s：%{public}s', path, JSON.stringify(e));
    return '';
  }
}

/** 文件字节数（-1 = 取不到）。 */
export function bytesOf(path: string): number {
  try {
    return fileIo.statSync(path).size;
  } catch (e) {
    return -1;
  }
}

/** 造出导入目录并返回它的路径（不存在则建）。 */
export function ensureImportDir(filesDir: string): string {
  const dir: string = `${filesDir}/import`;
  try {
    if (!fileIo.accessSync(dir)) {
      fileIo.mkdirSync(dir);
    }
  } catch (e) {
    hilog.error(0x0000, TAG, '建导入目录失败：%{public}s', JSON.stringify(e));
  }
  return dir;
}

/** 写一个文件（供"写入示例文档"用；已存在则不覆盖，返回是否新写）。 */
export function writeTextFile(path: string, text: string): boolean {
  try {
    if (fileIo.accessSync(path)) {
      return false;
    }
    const buf: ArrayBuffer = new util.TextEncoder().encodeInto(text).buffer as ArrayBuffer;
    const w = fileIo.openSync(path, fileIo.OpenMode.CREATE | fileIo.OpenMode.READ_WRITE);
    try {
      fileIo.writeSync(w.fd, buf);
    } finally {
      fileIo.closeSync(w);
    }
    return true;
  } catch (e) {
    hilog.error(0x0000, TAG, '写文件失败 %{public}s：%{public}s', path, JSON.stringify(e));
    return false;
  }
}

/** 只列文本类文件（`.txt` / `.md` / `.markdown`）。 */
export function listImportables(dir: string): string[] {
  const out: string[] = [];
  try {
    if (!fileIo.accessSync(dir)) {
      return out;
    }
    for (const name of fileIo.listFileSync(dir)) {
      const lower: string = name.toLowerCase();
      if (lower.endsWith('.txt') || lower.endsWith('.md') || lower.endsWith('.markdown')) {
        out.push(name);
      }
    }
  } catch (e) {
    hilog.error(0x0000, TAG, '列导入目录失败：%{public}s', JSON.stringify(e));
  }
  return out;
}
