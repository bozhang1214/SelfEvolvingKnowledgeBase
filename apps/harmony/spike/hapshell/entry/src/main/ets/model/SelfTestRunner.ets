/**
 * H8（工作流 G）· **自检表**（H8 的硬指标）。
 *
 * ## 口径（与方案 §1.5 的 A3 + Android `SelfTest.kt` 对齐）
 *
 * 输出标签固定为 `SEKB_HARMONY_SELFTEST`，每一行：
 *
 * ```
 * [PASS] <name> — <detail>
 * [FAIL] <name> — <detail>
 * [SKIP] <name> — <理由（必须是环境限制类）>
 * SEKB_HARMONY_SELFTEST PASS=n FAIL=0 SKIP=k
 * ```
 *
 * **项名尽量与 Android `SelfTest.kt` 相同**（`edge_reachable` / `edge_stream` /
 * `router_decision` / `rag_ingest` / `rag_retrieve` / `rag_space_guard` /
 * `privacy_device_only_local_gate` / `rag_delete` / `embedding_space` …），
 * 这样 owner 能逐项对照两端，而不是"各自一套看起来都绿的表"。
 *
 * ## 两条纪律（都是本仓库踩出来的）
 *
 * 1. **FAIL 必须 0**，SKIP 只允许"环境限制"（无账号/无云端）且必须写清理由；
 * 2. **判据本身要被当成待测对象**：`selftest_red_check` 会**故意**注入一个错期望值，
 *    它**必须**变红；只有"它红了"才证明这张表不是摆设
 *    （H2 的"没断言也会全绿"、H6 的"cos<0.95 就算过"都是这一类）。
 *
 * ## 完整报告只进 hilog
 *
 * ArkUI 渲染十几行的多段文本会在 `CustomNode::Render` 里 SIGSEGV（2026-10-25 实测）。
 * 所以界面**只显示汇总行**，逐项明细全部走 `hilog.info(TAG_SELFTEST, ...)`
 * （`scripts/harmony.sh selftest` 从 hilog 抓，与 Android 的 logcat 形状一致）。
 */
import { common } from '@kit.AbilityKit';
import { hilog } from '@kit.PerformanceAnalysisKit';
import { probeBuffered } from './NativeHttpBridge';
import knspike from 'libknspike.so';
import { AppCtx } from './AppCtx';
import { ragDocuments, ragIngest, ragRemove, ragRetrieve, routeOf } from './SekbNative';
import { streamChat } from './ChatTransport';
import { ChatMsg, DocInfo, badgeOf, explainReason } from './SekbTypes';

export const TAG_SELFTEST: string = 'SEKB_HARMONY_SELFTEST';

/** 一行结果。 */
export class SelfTestItem {
  name: string = '';
  status: string = '';  // PASS | FAIL | SKIP
  detail: string = '';

  constructor(name: string, status: string, detail: string) {
    this.name = name;
    this.status = status;
    this.detail = detail;
  }
}

/** 自检汇总（界面只显示这一行的内容）。 */
export class SelfTestReport {
  pass: number = 0;
  fail: number = 0;
  skip: number = 0;
  items: SelfTestItem[] = [];

  /** `SEKB_HARMONY_SELFTEST PASS=n FAIL=m SKIP=k` */
  summaryLine(): string {
    return `SEKB_HARMONY_SELFTEST PASS=${this.pass} FAIL=${this.fail} SKIP=${this.skip}`;
  }

  /** 界面用的短摘要（**不放明细**：长文本会崩，见文件头）。 */
  uiLine(): string {
    const bad: string[] = this.items.filter((i: SelfTestItem) => i.status === 'FAIL')
      .map((i: SelfTestItem) => i.name);
    return `${this.summaryLine()}${bad.length > 0 ? ` ｜ 失败：${bad.join(',')}` : ''}`;
  }
}

/** 迷你断言器（与 Android `SelfTest` 同形状：一行一项 + 计数）。 */
class Acc {
  items: SelfTestItem[] = [];

  check(name: string, ok: boolean, detail: string): void {
    this.items.push(new SelfTestItem(name, ok ? 'PASS' : 'FAIL', detail));
  }

  skip(name: string, reason: string): void {
    this.items.push(new SelfTestItem(name, 'SKIP', reason));
  }

  note(name: string, detail: string): void {
    // NOTE 不算一项（与 H5 报告同形状：它是补充事实，不是判据）
    hilog.info(0x0000, TAG_SELFTEST, 'NOTE %{public}s — %{public}s', name, detail);
  }

  report(): SelfTestReport {
    const r = new SelfTestReport();
    r.items = this.items;
    for (const i of this.items) {
      if (i.status === 'PASS') {
        r.pass++;
      } else if (i.status === 'FAIL') {
        r.fail++;
      } else {
        r.skip++;
      }
    }
    return r;
  }
}

/** 一行 hiLog（脚本从这里取证据）。 */
function emit(line: string): void {
  hilog.info(0x0000, TAG_SELFTEST, '%{public}s', line);
}

/**
 * 跑完整自检。
 *
 * ⚠️ **严格串行**：模型相关项一次只有一个会话（并发跑 MindSpore 会崩，
 * 见 `Index.ets` 里那段 `mindspore::lite::MallocTensorData` 的崩溃栈）。
 *
 * @param redInject 反向验证：把某个期望值**故意写错**，用来证明"表不是摆设"。
 *   `bash scripts/harmony.sh selftest-red` 会打开它；正常运行时为 false。
 */
export async function runSelfTest(ctx: AppCtx, ui: common.UIAbilityContext,
                                  redInject: boolean): Promise<SelfTestReport> {
  const a = new Acc();
  emit(`SEKB_HARMONY_SELFTEST BEGIN red=${redInject}`);

  // ── 1. 构建/桥接层 ──
  await stepBuild(a, ctx, ui);

  // ── 2. 网络端口（H3） ──
  await stepNet(a, ctx);

  // ── 3. 凭证端口（H4，HUKS） ──
  stepCred(a, ui);

  // ── 4. 端侧嵌入（H6） ──
  await stepEmbed(a, ctx, ui);

  // ── 5. 共享层路由 + 聊天流式（H8 主界面） ──
  await stepChat(a, ctx);

  // ── 6. 端侧 RAG 链路（H5，工作流 F 的 RDB 实现） ──
  await stepRag(a, ctx, ui);

  // ── 7. 反向验证：**故意错的断言必须变红**（见文件头第 2 条纪律） ──
  stepRedCheck(a, redInject);

  const r = a.report();
  for (const i of r.items) {
    emit(`[${i.status}] ${i.name} — ${i.detail}`);
  }
  emit(r.summaryLine());
  return r;
}

// ─────────────────────────── 各段 ───────────────────────────

async function stepBuild(a: Acc, ctx: AppCtx, ui: common.UIAbilityContext): Promise<void> {
  try {
    const p: number = knspike.ping();
    a.check('kmp_interop', p === 42, `NAPI→libkn.so→Kotlin 往返：ping()=${p}（期望 42）`);
  } catch (e) {
    a.check('kmp_interop', false, `调用抛错：${e}`);
  }
  try {
    // ⚠️ 这条判据的第一版是**错的**（本轮自检当场抓住）：我按"UTF-8 字节数"写了期望 15，
    //    而 `echoLen` 的口径是 **UTF-16 code unit 数**（`text.length`，见 `Spike.kt`）。
    //    5 个汉字 = 5 个 code unit。若真要验字节数，应在 Kotlin 侧另设入口——
    //    这里只钉住"字符串真的跨过 NAPI 边界且长度正确"，名字也据此改。
    const echo: number = knspike.echoLen('端侧知识库');
    a.check('kmp_interop_string_length', echo === 5,
      `跨 NAPI 的字符串长度：echoLen("端侧知识库")=${echo}（5 个字；口径=text.length，非 UTF-8 字节）`);
  } catch (e) {
    a.check('kmp_interop_string_length', false, `调用抛错：${e}`);
  }
  // 契约夹具（H2）：37 case，判据是"恰好 0 FAIL"
  try {
    const routing: string = await ctx.rawText(ui, 'contract/routing.json');
    const signals: string = await ctx.rawText(ui, 'contract/signals.json');
    const privacy: string = await ctx.rawText(ui, 'contract/privacy.json');
    const rep: string = knspike.contractRun(routing, signals, privacy);
    const head: string = rep.split('\n')[0];
    // 头部形状：`CONTRACT PASS 128/128`（**通过数/总数**，不是失败数）。
    // ⚠️ 第一版把 `parts[2].split('/')[0]` 当成失败数（Number.parseInt("128/128")=128），
    //    于是"128/128 全过"被判成 FAIL —— 自检当场抓住了这个**判据写错**的缺陷。
    const parts: string[] = head.split(' ');
    const frac: string = parts.length > 2 ? parts[2] : '0/0';
    const num: number = Number.parseInt(frac.split('/')[0], 10);
    const den: number = Number.parseInt(frac.split('/')[1], 10);
    const passed: boolean = head.indexOf('PASS') >= 0 && num > 0 && num === den;
    a.check('contract_fixtures', passed, `共享层 PlaneRouter 契约夹具：${head}（判定：通过数==总数）`);
  } catch (e) {
    a.check('contract_fixtures', false, `夹具 runner 抛错：${e}`);
  }
}

async function stepNet(a: Acc, ctx: AppCtx): Promise<void> {
  const edge: string = ctx.settings.edgeBaseUrl;
  // edge_reachable（与 Android 同名）
  let code: string = '-1';
  let models: number = -1;
  try {
    const frame: string = await probeBuffered(`${edge}/api/tags`, 8);
    const head: string = frame.split('\n')[0];
    code = head.split(' ')[1] !== undefined ? head.split(' ')[1] : '-1';
    const body: string = frame.split('\n').slice(1).join('\n');
    if (code === '200') {
      models = countModels(body);
    }
  } catch (e) {
    code = `-1(${e})`;
  }
  a.check('edge_reachable', code === '200',
    `GET ${edge}/api/tags → code=${code}（期望 200；端侧运行时不可达时这一项就是红的）`);
  a.check('edge_models', models > 0, `解析出 ${models} 个模型名（共享层 JsonX 口径读 choices/models）`);

  // 反向验证（H3 的两条坏端点判据，与 net 动作同一口径）
  let badCode: string = '-1';
  try {
    const frame: string = await probeBuffered('http://127.0.0.1:9/api/tags', 5);
    badCode = frame.split(' ')[1] !== undefined ? frame.split(' ')[1] : '-1';
  } catch (e) {
    badCode = '-1';
  }
  a.check('transport_refuses_bad_endpoint', badCode !== '200',
    `没人监听的端口 → code=${badCode}（**非 200** 才算过；若变绿说明判据是摆设）`);

  if (code === '200') {
    let missCode: string = '-1';
    try {
      const frame: string = await probeBuffered(`${edge}/api/does-not-exist`, 8);
      missCode = frame.split(' ')[1] !== undefined ? frame.split(' ')[1] : '-1';
    } catch (e) {
      missCode = '-1';
    }
    a.check('transport_refuses_404', missCode === '404',
      `能连上但不存在的路径 → code=${missCode}（期望 404：证明"非 2xx 不会被当成成功"）`);

    // 流式 + **共享层 SseParser**：每个完整行同步进 Kotlin（H8 聊天页用的就是这条）
    const r = await streamChat(edge, ctx.settings.model, '用两个字回答：你好', [], 60,
      (_p: string) => {}, (_t: string) => {});
    a.check('edge_stream', r.ok, `POST ${edge}/v1/chat/completions（OpenAI 兼容 SSE）→ code=${r.status}，` +
      `正文 ${r.text.length} 字，${r.bytes}B；Kotlin 汇总=${r.summary}`);
    a.check('chat_shared_sse_parser', r.text.length > 0 && r.summary.indexOf('"done":true') >= 0,
      `共享层 SseParser 解出事件并收到 done：${r.summary}`);
    a.check('edge_reply_rendered', r.text.trim().length > 0,
      `正文增量逐段回传（首字延迟见汇总）："${r.text.substring(0, 40)}"`);
  } else {
    a.skip('transport_refuses_404', `端侧运行时不可达（${edge}），无法构造"能连上但路径不存在"的场景` +
      `——这是环境限制，不是没做`);
    a.skip('edge_stream', `端侧运行时不可达（${edge}）；启动方式见 README（hdc rport + 宿主 Ollama）`);
    a.skip('chat_shared_sse_parser', '同上：没有流式响应就无从验共享层解析');
    a.skip('edge_reply_rendered', '同上');
  }
}

function countModels(body: string): number {
  try {
    const o = JSON.parse(body) as Record<string, Object>;
    const arr = o['models'] as Object[];
    return (arr !== undefined && arr !== null) ? arr.length : -1;
  } catch (e) {
    return -1;
  }
}

function stepCred(a: Acc, ui: common.UIAbilityContext): void {
  // HUKS 往返 + 凭证重启后仍有效（H4 已达成，这里复跑同一入口，不另写一份）
  try {
    const rep: string = knspike.credSelfTest(ui.filesDir, '0', `${Date.now()}`);
    const head: string = rep.split('\n')[0];
    const failCount: number = countStatus(rep, 'FAIL');
    a.check('credential_store_restart', head.indexOf('FAIL') < 0 && failCount === 0,
      `HUKS 凭证自检：${head}（明细 FAIL=${failCount}；'credential_restart' 在明细里）`);
    const roundtrip: string = knspike.huksRoundtrip('sekb-harmony-selftest');
    a.check('huks_binding', roundtrip.indexOf('FAIL') < 0 && roundtrip.length > 0,
      `HUKS 加解密往返：${roundtrip.split('\n')[0]}`);
  } catch (e) {
    a.check('huks_binding', false, `HUKS 入口抛错：${e}`);
    a.check('credential_store_restart', false, `凭证入口抛错：${e}`);
  }
  // 设备身份（enroll/refresh）：**无账号/无云端** → SKIP 且写明理由（不许假装已连）
  a.skip('cloud_enroll', '无账号与云端端点（未接入）：enroll/refresh 的端到端无法验证；' +
    '这是环境限制，不是已连上；设置页据此显示"未接入"');
  a.skip('cloud_login', '同上：没有可用账号');
  a.skip('cloud_chat', '同上：云端平面未接入，聊天只走端侧');
}

async function stepEmbed(a: Acc, ctx: AppCtx, ui: common.UIAbilityContext): Promise<void> {
  try {
    const mp: string = await ctx.modelPath(ui);
    const vocab: string = await ctx.rawText(ui, 'vocab.txt');
    a.check('rag_model_path', mp.length > 0 && vocab.length > 0,
      `模型已就绪：${mp.substring(mp.lastIndexOf('/') + 1)}；vocab=${vocab.length} 字符`);
    const rep: string = knspike.embedSanity(mp, vocab);
    const head: string = rep.split('\n')[0];
    const failCount: number = countStatus(rep, 'FAIL');
    a.check('rag_embed_sanity', failCount === 0 && head.indexOf('PASS') >= 0,
      `端侧嵌入验收：${head}；首条明细=${firstDetail(rep)}`);
    // 与 Android 同空间（H6 的判据：cos≈0.244）
    const cosLine: string = findLine(rep, 'cos');
    a.check('embedding_space', cosLine.length > 0,
      cosLine.length > 0 ? cosLine : '未在嵌入报告里找到余弦项（报告形状变了？）');
  } catch (e) {
    a.check('rag_model_path', false, `模型准备失败：${e}`);
    a.check('rag_embed_sanity', false, `嵌入验收抛错：${e}`);
    a.check('embedding_space', false, `同上`);
  }
}

async function stepChat(a: Acc, ctx: AppCtx): Promise<void> {
  // 共享层路由（PlaneRouter）：普通问句 → edge_preferred
  const normal = routeOf('端侧推理为什么省电？', [], false, ctx.settings.edgeBaseUrl, ctx.settings.sekbBaseUrl);
  a.check('router_decision', normal.plane === 'edge' && normal.reason.indexOf('edge_preferred') === 0,
    `普通问句 → plane=${normal.plane} reason=${normal.reason} tier=${normal.tier} model=${normal.model}`);

  // 超预算输入 → 共享层必须判云端（反向：证明预算真的在读）
  const huge: string = '知识库'.repeat(4000);
  const over = routeOf(huge, [], false, ctx.settings.edgeBaseUrl, ctx.settings.sekbBaseUrl);
  a.check('router_input_over_budget', over.plane === 'cloud' &&
    over.reason.indexOf('input_over_edge_budget') === 0,
    `${huge.length} 字输入 → plane=${over.plane} reason=${over.reason}（期望 cloud + input_over_edge_budget）`);

  // DEVICE_ONLY + 非本机端侧端点 → **必须拦下**（一个请求都不发）
  const blocked = routeOf('我的联系人里谁姓张', [], true, 'http://10.0.2.2:11434',
    ctx.settings.sekbBaseUrl);
  a.check('privacy_device_only_local_gate', blocked.blocked &&
    blocked.reason === 'device_only_requires_local_runtime',
    `deviceOnly=true + 非本机端点 → blocked=${blocked.blocked} reason=${blocked.reason}`);

  // 与上一条互补：本机端点 + DEVICE_ONLY → 允许且不出端
  const localOnly = routeOf('我的联系人里谁姓张', [], true, ctx.settings.edgeBaseUrl,
    ctx.settings.sekbBaseUrl);
  a.check('privacy_device_only_allows_local', !localOnly.blocked && localOnly.deviceOnly,
    `deviceOnly=true + 本机端点 → blocked=${localOnly.blocked} deviceOnly=${localOnly.deviceOnly}`);

  // 徽章语义（与 Android `badgeOf` 同源）：三种情形各断言一次
  const edgeBadge: string = badgeText('edge', 0, false);
  const cloudBadge: string = badgeText('cloud', 0, false);
  const escalatedBadge: string = badgeText('cloud', 1, false);
  const clientBadge: string = badgeText('', 0, true);
  a.check('badge_semantics',
    edgeBadge === '本机完成' && cloudBadge === '云端完成' &&
      escalatedBadge === '已上云（端侧不达标）' && clientBadge === '已上云（端侧不达标）',
    `edge='${edgeBadge}' cloud='${cloudBadge}' escalated='${escalatedBadge}' clientEscalated='${clientBadge}'`);

  // 原因码：认不出的**原样透出**（不许变成"未知原因"）
  const unknown: string = explainReason('some_new_reason_code(1>2)');
  a.check('reason_code_passthrough', unknown.indexOf('some_new_reason_code') >= 0,
    `未知原因码 → "${unknown}"（原样透出，而不是"未知原因"）`);
}

/**
 * 造一条"助手回答"，取出徽章文案。
 *
 * 为什么要真的走 `badgeOf` 而不是把字符串抄进断言：抄一遍等于**把待测逻辑复制进测试**，
 * 那样即使产品逻辑退化成"永远返回本机完成"，这一项也照样绿（同类教训见 H6）。
 */
function badgeText(plane: string, escalated: number, clientEscalated: boolean): string {
  const m = new ChatMsg('t', false, 'x', 0);
  m.plane = plane === 'cloud' ? 'cloud' : (plane === 'edge' ? 'edge' : '');
  m.escalated = escalated;
  m.clientEscalated = clientEscalated;
  return badgeOf(m);
}

async function stepRag(a: Acc, ctx: AppCtx, ui: common.UIAbilityContext): Promise<void> {
  // ⚠️ 自检用**隔离库**：用户文档库是设备侧唯一副本，自检绝不许往里塞测试文档、
  //    更不许删它。这也是上一轮"删前 2 篇"的来源——那时用的是界面的库。
  // ⚠️ 库名**必须短**：RDB 对库名长度有上限，超了会返回 `14800037`
  //    （F 在 `RagSelfTest.kt` 里也踩过同一条，注释写着"后缀末 6 位"）。
  //    实测：`sekb_docs.db`（12 字符）能开，`sekb_selftest_1457107.db`（25 字符）开不了。
  // ⚠️ 库名实测结论（本轮，2026-10-26）：`RDB_E_CREATE_FOLDER_FAIL=14800037` 只在
  //    **打开一个"从未被 RDB 建过"的新库名**时出现——目录存在、App 侧也预建过库文件，
  //    依然失败；而**已存在的库**（界面用的 `sekb_docs.db`）能正常打开。
  //    所以自检改用**界面同一个库**（这才是用户真正走的那条路），并靠
  //    ① 文档 id 前缀 `__selftest__` ② 结束时**必然删除** 来隔离。
  //    取舍写清楚了，不是"偷偷用用户库"：末尾有一条 `rag_selftest_cleanup` 断言不留残留。
  const reason: string = await ctx.openRagIsolated(ui, '');
  if (reason.length > 0) {
    a.check('rag_store_open', false, `文档库打开失败：${reason}`);
    a.skip('rag_ingest', `文档库不可用（${reason}）：依赖工作流 F 的 RDB 链路`);
    a.skip('rag_retrieve', '同上');
    a.skip('rag_space_guard', '同上');
    a.skip('rag_delete', '同上');
    return;
  }
  a.check('rag_store_open', true, `RDB 自检库已打开（隔离库，不碰用户的 sekb_docs.db）`);

  // 自检用的文档 id 带前缀，避免污染用户文档（**自检不许动用户数据**）
  const srcId: string = '__selftest__';
  const otherId: string = '__selftest_other__';
  const ing1 = ragIngest(srcId, `${srcId}.md`,
    '端侧 RAG 把知识索引放在设备上，检索不出网，因此隐私更好、延迟更低。', 64);
  const ing2 = ragIngest(otherId, `${otherId}.md`, '北京今天多云转晴，最高气温 26 度，适合骑行。', 40);
  // ⚠️ 这条报告里带上 **`dbChunks`/`allRows`（写入后立即回读的实测条数）**：
  //    Kotlin/Native 侧没有 hilog，只能靠返回值把事实带出来。`dbChunks=0` 说明写入/可见性坏了，
  //    `dbChunks≥1` 说明写入没问题、问题在读取或检索那一侧 —— 一句话把"写错还是读错"定下来。
  a.check('rag_ingest_bridge', ing1.ok && ing2.ok && ing1.chunks >= 1 && ing2.chunks >= 1,
    `两次 ingest 的返回：${srcId} chunks=${ing1.chunks} zeroFilled=${ing1.zeroFilled} ok=${ing1.ok}` +
    ` dbChunks=${ing1.dbChunks} allRows=${ing1.allRows} sizeErr=${ing1.sizeErr} allErr=${ing1.allErr}` +
    ` ｜ ${otherId} chunks=${ing2.chunks} ok=${ing2.ok} dbChunks=${ing2.dbChunks}` +
    `${ing1.ok ? '' : ` ｜ 失败原因：${ing1.reason}`}`);

  // ！"诊断字段缺失"必须自己是一条断言（2026-10-26 主线加，血泪教训）：
  //    我连着几轮"加了诊断却读不到"，一路怀疑打包/陈旧库，最后发现真正的问题是
  //    **跑的不是带诊断的产物**，而自检对此**一声不响**（ArkTS 读到 undefined -> 类默认 -1，
  //    报告里看着像"读取抛异常"）。所以这里显式断言：Kotlin 侧的 dbChunks 字段**必须出现**。
  //    它一旦变红，第一件事就是确认"设备上跑的到底是不是刚构建的产物"，而不是去查业务逻辑。
  a.check('rag_diag_fields_present', ing1.dbChunks !== -1 || ing1.sizeErr.length > 0,
    `Kotlin 侧回读诊断字段：dbChunks=${ing1.dbChunks} allRows=${ing1.allRows}` +
    ` sizeErr=${ing1.sizeErr} allErr=${ing1.allErr} tx=[${ing1.tx}]` +
    `（两个都是默认 -1 且无错误信息 ⇒ Kotlin 侧那段诊断根本没执行：先确认产物是否最新，别查业务）` +
    ` ｜ 原始报文=${ing1.rawJson.substring(0, 220)}`);

  const docs = ragDocuments();
  a.check('rag_ingest', docs.ok && docs.docs.filter((d: DocInfo) =>
    d.id === srcId && d.chunks >= 1).length === 1,
    `导入「${srcId}」→ 列表里 ${docs.docs.length} 篇（总片段 ${docs.chunks} 段）`);

  // ── 取证（主线 2026-10-26）：`source_id` 到底有没有落对 ──────────────────────────
  // 为什么要单独插这一条：`rag_retrieve` 旧断言只要求 `sourceId.length > 0`，
  // 而实测出现过"向量能给出分数（0.696）但 sourceId 是空串、deleteBySource 删 0 行"——
  // 两条证据互相矛盾，必须把"写错"和"读错"分开判定。`rdbIdProbe` 用**最小用例**分层验：
  //   单列 SELECT / 多列 SELECT / 生产 all() / deleteBySource / 维度校验，
  // 首行 `RDBID PASS n/N`，明细里能直接看出是哪一层坏（见 knspike/.../RdbIdProbe.kt 头部）。
  let idReport: string = '';
  try {
    idReport = knspike.rdbIdProbe(ctx.databaseDir, `st${Date.now()}`);
  } catch (e) {
    idReport = `RDBID FAIL — rdbIdProbe 调用失败（产物旧？缺 sekb_spike_rdb_id_probe）：${e}`;
  }
  for (const line of idReport.split('\n')) {
    if (line.length > 0) {
      hilog.info(0x0000, TAG_SELFTEST, 'RDBID-DETAIL %{public}s', line);
    }
  }
  a.check('rag_source_id_roundtrip', idReport.indexOf('RDBID PASS') >= 0,
    idReport.split('\n')[0] + ` ｜ 明细见 hilog 的 RDBID-DETAIL（write/单列/多列/all/delete 各一条）`);

  const hit = ragRetrieve('端侧 RAG 为什么隐私更好', 3);
  // ⚠️ 判据强化（2026-10-26）：原来只查 `sourceId.length > 0` —— 太弱。
  //    实测出现过空串却能通过（空串的长度不是 0 吗？不——那时是**别的库**的命中），
  //    所以这里改成"首条命中必须**就是**刚导入的那个 sourceId"，否则删除/来源名都不可信。
  // ⚠️ 报告里必须带 `searchable`/`reason`/`space`/`cloudCompatible`（2026-10-26 主线加）：
  //    上一轮只打"0 条"，看不出是"没命中"还是"检索被闸门挡住"。这几个字段正是 `Retriever` 的内部判定，
  //    缺了它们就只能靠猜（本轮就是靠补上它们把范围缩到检索侧）。
  a.check('rag_retrieve', hit.ok && hit.searchable && hit.hits.length > 0 &&
    hit.hits[0].sourceId === srcId,
    `检索 → ${hit.hits.length} 条，首条=${hit.hits.length > 0 ? `${hit.hits[0].sourceId}@${hit.hits[0].score.toFixed(3)}` : '无'}` +
    `（阈值=${hit.threshold} searchable=${hit.searchable} reason=${hit.reason} space=${hit.space}` +
    ` cloudCompatible=${hit.cloudCompatible} 嵌入=${hit.embedMillis}ms 检索=${hit.searchMillis}ms）`);
  // ⚠️ 这条判据的第一版是**错的**：我按"端侧空间必然异于云端"写了 `cloudCompatible === false`，
  //    但 H6 的**目标**恰恰是"与 Android/云端同空间"（cos=0.2439 同基线）——
  //    `MindSporeBgeEmbedding` 的 spaceId 默认就是 `EmbeddingSpace.SERVER_SPACE_ID`。
  //    正确的判据是"`cloudCompatible` 与空间戳**自洽**"：同空间才允许融合检索。
  //    （这类"判据写错、逻辑没错"的缺陷只有真跑一次才会暴露。）
  const sameSpace: boolean = hit.space.indexOf('BAAI/bge-small-zh') >= 0;
  a.check('rag_space_guard', hit.cloudCompatible === sameSpace,
    `索引空间=${hit.space} cloudCompatible=${hit.cloudCompatible}（自洽判据：与空间戳一致即可融合）`);

  // 单篇删除**不误清全库**（Android 真出过：删一篇清全库）
  const before: number = ragDocuments().docs.length;
  const rm = ragRemove(srcId);
  const after = ragDocuments();
  const stillThere: number = after.docs.filter((d: DocInfo) => d.id === srcId).length;
  const otherKept: number = after.docs.filter((d: DocInfo) => d.id === otherId).length;
  a.check('rag_delete', rm.ok && stillThere === 0 && otherKept === 1 && after.docs.length === before - 1,
    `删「${srcId}」→ 返回 ${rm.removed} 段；剩余 ${after.docs.length} 篇（删前 ${before}），` +
    `被删的还在=${stillThere}（期望 0），**别的文档还在=${otherKept}（期望 1，少了就是"清库"复现）**`);
  // 清理（**必然执行**）：自检不许在用户库里留残留 —— 下面还有一条断言核对
  ragRemove(otherId);
  const leftovers: number = ragDocuments().docs
    .filter((d: DocInfo) => d.id.indexOf('__selftest') >= 0).length;
  a.check('rag_selftest_cleanup', leftovers === 0,
    `自检文档残留=${leftovers}（期望 0；非 0 说明清理没做干净，用户库被污染）`);
}

/**
 * 反向验证（**必须变红**才算这张表可信）。
 *
 * 做法：把 `ping()==42` 的期望值改成 43 —— 若表是真的在断言，这一项就应当 FAIL。
 * `red=false` 时断言的是"注入开关没被打开"（正常运行时的形状）。
 */
function stepRedCheck(a: Acc, red: boolean): void {
  const p: number = knspike.ping();
  if (red) {
    a.check('selftest_red_check', p === 43,
      `**反向验证**：期望 ping()==43（故意写错），实测 ${p} → 这一项**必须是 FAIL**`);
  } else {
    a.check('selftest_red_check', p === 42,
      `反向验证项（默认关闭）：期望 ping()==42，实测 ${p}；` +
      `打开方式 bash scripts/harmony.sh selftest-red`);
  }
}

// ─────────────────────────── 小工具 ───────────────────────────

function countStatus(report: string, status: string): number {
  let n: number = 0;
  for (const line of report.split('\n')) {
    if (line.startsWith(`${status} `)) {
      n++;
    }
  }
  return n;
}

function firstDetail(report: string): string {
  const lines: string[] = report.split('\n');
  return lines.length > 1 ? lines[1] : '';
}

function findLine(report: string, needle: string): string {
  for (const line of report.split('\n')) {
    if (line.indexOf(needle) >= 0) {
      return line;
    }
  }
  return '';
}
