/**
 * H8（工作流 G）· 端侧会话的**领域类型 + 语义函数**（鸿蒙端）。
 *
 * ## 与既有 `Types.ets` / `EdgeAgent.ets` 的关系（为什么不直接改它们）
 *
 * `Types.ets` 与 `EdgeAgent.ets` 是 **M7 骨架**（`pages/Chat.ets` 在用），
 * 而自检页 `pages/Index.ets` 正被**工作流 F** 使用中（协作约定：G 不编辑 F 的文件）。
 * 所以 H8 不去改动那两个文件，而是把"消息 + 徽章 + 原因码"的完整口径放在本文件里，
 * 页面（`ChatPage.ets` 等）只依赖本文件。
 *
 * ⚠️ `explainReason` 与 `Types.ets` 里那份**逐行同口径**（同一组原因码、同一条
 * "认不出就原样透出"的纪律）。这是有意的重复，不是漂移：骨架那一份的 import 图
 * 已被 F 的文件占用，动它的风险大于这里多一份 20 行的表。两份一旦不一致，
 * 以**共享层 `PlaneRouter` 的常量**为准（这才是唯一真相）。
 */

/** 执行位置。`''` = 还没结果（流式中）。 */
export type Plane = 'edge' | 'cloud' | '';

/** 一条检索来源（端侧 RAG 的可追溯性：答对答错都要能看出处）。 */
export class Source {
  sourceId: string = '';
  score: number = 0;
  snippet: string = '';

  constructor(sourceId: string = '', score: number = 0, snippet: string = '') {
    this.sourceId = sourceId;
    this.score = score;
    this.snippet = snippet;
  }
}

/** 一条气泡。字段与 Android `ChatViewModel.Bubble` 对齐，便于三端同语义。 */
export class ChatMsg {
  id: string = '';
  fromUser: boolean = false;
  text: string = '';
  /** 服务端/端侧回传的执行位置（决定徽章颜色） */
  plane: Plane = '';
  /** **服务端**回传的升级次数；>0 表示"已上云（端侧不达标）" */
  escalated: number = 0;
  /** 路由阶段原因码（如 `edge_preferred`）——**挂在这一条上**，不是全局"最近决策" */
  reason: string = '';
  /** 升级阶段原因码（如 `degenerate`）；未升级为空 */
  escalateReason: string = '';
  /** 客户端自己改道（端侧不可用 → 云端）；服务端并不知道这件事 */
  clientEscalated: boolean = false;
  /** 本机检索来源 */
  sources: Source[] = [];
  /** 这一条还在流式接收中 */
  streaming: boolean = false;
  /** 失败文本（用户必须看见，不能静默） */
  error: string = '';
  atMillis: number = 0;

  constructor(id: string, fromUser: boolean, text: string, atMillis: number) {
    this.id = id;
    this.fromUser = fromUser;
    this.text = text;
    this.atMillis = atMillis;
  }
}

/** 一段会话（历史页列表 / 持久化单元）。 */
export class Conversation {
  id: string = '';
  title: string = '';
  updatedAt: number = 0;
  messages: ChatMsg[] = [];

  constructor(id: string = '', title: string = '', updatedAt: number = 0) {
    this.id = id;
    this.title = title;
    this.updatedAt = updatedAt;
  }
}

/** 一份本机文档（文档页列表）。 */
export class DocInfo {
  id: string = '';
  name: string = '';
  chunks: number = 0;
  bytes: number = 0;
  addedAt: number = 0;

  constructor(id: string = '', name: string = '', chunks: number = 0, bytes: number = 0, addedAt: number = 0) {
    this.id = id;
    this.name = name;
    this.chunks = chunks;
    this.bytes = bytes;
    this.addedAt = addedAt;
  }
}

/** 共享层路由决策（`chatRoute` 的返回，字段名与 `RouteDecision` 对齐）。 */
export class RouteInfo {
  plane: Plane = '';
  reason: string = '';
  tier: string = '';
  model: string = '';
  inputTokens: number = 0;
  outputBudget: number = 0;
  deviceOnly: boolean = false;
  blocked: boolean = false;
  blockedReason: string = '';

  static fromJson(o: RouteInfo | null): RouteInfo {
    const r = new RouteInfo();
    if (o === null) {
      return r;
    }
    r.plane = o.plane === 'cloud' ? 'cloud' : (o.plane === 'edge' ? 'edge' : '');
    r.reason = o.reason !== undefined ? o.reason : '';
    r.tier = o.tier !== undefined ? o.tier : '';
    r.model = o.model !== undefined ? o.model : '';
    r.inputTokens = o.inputTokens !== undefined ? o.inputTokens : 0;
    r.outputBudget = o.outputBudget !== undefined ? o.outputBudget : 0;
    r.deviceOnly = o.deviceOnly === true;
    r.blocked = o.blocked === true;
    r.blockedReason = o.blockedReason !== undefined ? o.blockedReason : '';
    return r;
  }
}

/** 加密钥探针结果（设置页"网络与后端地址"）。 */
export class EndpointProbe {
  url: string = '';
  ok: boolean = false;
  detail: string = '';
  models: number = 0;

  constructor(url: string = '', ok: boolean = false, detail: string = '', models: number = 0) {
    this.url = url;
    this.ok = ok;
    this.detail = detail;
    this.models = models;
  }
}

/**
 * 执行位置徽章文案。
 *
 * ⚠️ 与 Android `ChatViewModel.badgeOf` **同源语义**：`clientEscalated` 必须单独看——
 * 服务端回传的 `escalated` 只统计服务端内部的升级，而"客户端刚因为端侧不可用改道"
 * 服务端并不知道；只看服务端字段会把这种情况显示成平平无奇的"云端完成"。
 */
export function badgeOf(m: ChatMsg): string {
  if (m.fromUser) {
    return '';
  }
  if (m.plane === 'edge') {
    return '本机完成';
  }
  if (m.plane === 'cloud' || m.clientEscalated) {
    return (m.clientEscalated || m.escalated > 0) ? '已上云（端侧不达标）' : '云端完成';
  }
  return '';
}

/** 语义色：**只在徽章上用**（别处用绿会让"绿色"不再等于"在本机算的"）。 */
export function badgeColor(m: ChatMsg): string {
  return m.plane === 'edge' ? '#389E0D' : '#D48806';
}

/**
 * 原因码 → 用户能懂的一句话；**认不出的码原样透出**（`原因：<code>`），
 * 绝不返回"未知原因"（那会让新原因码静默消失，Android 端踩过）。
 */
export function explainReason(code: string): string {
  if (code.length === 0) {
    return '';
  }
  if (code.indexOf('edge_preferred') === 0) {
    return '这次请求在设备能力范围内，按设计优先由本机处理。';
  }
  if (code.indexOf('input_over_edge_budget') === 0) {
    return '输入超出端侧能处理的规模，按设计直接交给云端。';
  }
  if (code.indexOf('output_over_edge_budget') === 0) {
    return '预期回答长度超出端侧预算，按设计直接交给云端。';
  }
  if (code.indexOf('prefer_cloud') === 0) {
    return '端侧偏好被配置为"优先云端"。';
  }
  if (code.indexOf('escalation_blocked:device_only_requires_local_runtime') === 0) {
    return '这是设备专属内容，但当前端侧地址不是本机；为不让数据出端，这次请求没有发出。';
  }
  if (code.indexOf('escalation_blocked:device_only') === 0) {
    return '这是设备专属内容，不允许改道云端。';
  }
  if (code.indexOf('edge_unavailable') === 0) {
    return '端侧运行时不可用（连不上或调用失败）。';
  }
  if (code.indexOf('degenerate') === 0) {
    return '端侧回答中途开始重复、退化。';
  }
  if (code.indexOf('timeout') === 0) {
    return '端侧生成超时。';
  }
  if (code.indexOf('low_confidence') === 0) {
    return '端侧对这次回答没有把握。';
  }
  if (code.indexOf('json_invalid') === 0) {
    return '端侧要调用的工具参数不是合法格式。';
  }
  if (code.indexOf('empty') === 0) {
    return '端侧没有产出有效内容。';
  }
  if (code.indexOf('tool_hallucination') === 0) {
    return '端侧试图调用不存在的工具。';
  }
  if (code.indexOf('context_overflow') === 0) {
    return '上下文超出端侧能容纳的长度。';
  }
  return '原因：' + code;
}

/** 体积显示（文档页）：B/KB/MB，保留 1 位小数。 */
export function fmtBytes(n: number): string {
  if (n < 1024) {
    return `${n} B`;
  }
  if (n < 1024 * 1024) {
    return `${(n / 1024).toFixed(1)} KB`;
  }
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

/** 时间显示（历史页）：今天只给时分，否则给月-日 时:分。 */
export function fmtTime(ms: number): string {
  if (ms <= 0) {
    return '';
  }
  const d = new Date(ms);
  const now = new Date();
  const hh = `${d.getHours()}`.padStart(2, '0');
  const mm = `${d.getMinutes()}`.padStart(2, '0');
  if (d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate()) {
    return `${hh}:${mm}`;
  }
  const mo = `${d.getMonth() + 1}`.padStart(2, '0');
  const dd = `${d.getDate()}`.padStart(2, '0');
  return `${mo}-${dd} ${hh}:${mm}`;
}
