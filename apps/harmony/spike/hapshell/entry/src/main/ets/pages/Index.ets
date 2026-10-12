// M6 spike 的界面：把 NAPI 调用的结果**显示出来**。
//
// 三组结果，正好回答真机日的三个问题：
//   1. `ping`        —— ArkTS → NAPI → libknspike.so → libkn.so → Kotlin 这条链路通不通；
//   2. `msSelfTest`  —— `.ms` 模型能不能**加载并跑出向量**（>0 = 输出元素数，<0 = 失败步骤码）；
//   3. `msFirstFloat`—— 输出首元素的粗量化值，用来证明**数据真的流过来了**（全零也能过第 2 项）。
//
// `.ms` 通过 rawfile 随包分发，首次启动复制到应用私有目录——`OH_AI_ModelBuildFromFile` 要的是
// **文件系统路径**，而 rawfile 不是路径。这样真机日不需要手工 push，也不会踩路径权限的坑。
import knspike from 'libknspike.so';
import { hilog } from '@kit.PerformanceAnalysisKit';
import { fileIo } from '@kit.CoreFileKit';
import { common } from '@kit.AbilityKit';
import { util } from '@kit.ArkTS';
import { probeBuffered, probeStream } from '../model/NativeHttpBridge';

const TAG: string = 'SEKB_KN_SPIKE';
const MODEL_RAWFILE: string = 'bge-small-fixed512.ms';
// H2：契约夹具在 HAP 里的位置（由 scripts/harmony_spike.sh build_hap 拷进来，
// 源头是仓库的 apps/contract/*.json —— 不新增夹具，只分发同一份）
const CONTRACT_DIR: string = 'contract/';
/**
 * H3：候选 base URL。
 *
 * 为什么是**两个候选逐个实测**而不是写死一个：宿主 Ollama 有两条常见通路
 * （`hdc rport tcp:11434 tcp:11434` → 设备侧 `127.0.0.1:11434`；
 * QEMU 网关别名 → `10.0.2.2:11434`），不同模拟器配置下哪条通不一样。
 * 判据是"至少有一条给出 code=200"，而不是"我以为的那条通"。
 */
const OLLAMA_CANDIDATES: string[] = ['http://127.0.0.1:11434', 'http://10.0.2.2:11434'];
/** 反向验证用：本机 9 号端口（discard）**没有监听**，不可能给出 200。 */
const BAD_URL: string = 'http://127.0.0.1:9/api/tags';
const NET_TIMEOUT_S: number = 20;

/**
 * 只取报告首行（`EMBED PASS 7/8` / `CONTRACT PASS 128/128` / `SMOKE PASS 11/11`）。
 *
 * ⚠️ 别把完整报告直接塞进 `Text`：实测多段十几行的诊断文本会让 ArkUI 在
 * `CustomNode::Render` 里 SIGSEGV（崩溃栈全在 libace_compatible，与本端 Kotlin 无关）。
 * 完整证据走 hilog（`scripts/harmony.sh` 也是从那里取的），界面只做"一眼看到结论"。
 */
function firstLine(report: string): string {
  const idx: number = report.indexOf('\n');
  const head: string = idx >= 0 ? report.substring(0, idx) : report;
  return head.length > 120 ? `${head.substring(0, 120)}…` : head;
}

@Entry
@Component
struct Index {
  @State ping: string = '（未调用）';
  @State echoLen: string = '（未调用）';
  @State modelPath: string = '（准备中）';
  @State msResult: string = '（未调用）';
  @State msFirstFloat: string = '（未调用）';
  @State smoke: string = '（未调用）';
  @State contract: string = '（未调用）';
  @State libProbe: string = '（未调用）';
  @State embed: string = '（未调用）';
  @State net: string = '（未调用）';
  @State cred: string = '（未调用）';
  // H5（工作流 F）：RDB 探针 + RAG 自检/检索评测（走共享层）
  @State rdb: string = '（未调用）';
  @State rag: string = '（未调用）';

  /**
   * ⚠️ **串行**跑所有探针（不是各自 fire-and-forget）。
   *
   * 为什么（2026-10-25 实测崩溃）：早前这里同时调 `runEmbedSanity()` 与 `runModel()`，
   * 两者都是 async 且都会 open 一个 MindSpore Lite 会话 → 设备上抓到 cppcrash：
   *   `SIGSEGV(SEGV_MAPERR)@0x30  probably caused by NULL pointer dereference`
   *   `#00 libmindspore-lite.so mindspore::lite::MallocTensorData(Tensor*)+260`
   *   `#01 ... SubGraphKernel::MallocSubgraphInputs()+64`
   *   `#02 ... LiteOpActor::InitInputData()+152`   （Tid: OS_Actor_1）
   * 即**并发跑模型会崩在推理内部**。串行化之后同一套代码不再崩（本轮实测：EMBED PASS）。
   * 顺带 `ensureModelFile` 也改成共享同一个 in-flight Promise，避免两次并发复制 94MB 文件。
   */
  aboutToAppear(): void {
    this.runAll();
  }

  async runAll(): Promise<void> {
    // 先跑不碰模型的（几毫秒），再按"一次只有一个模型会话"的顺序跑需要模型的
    this.runSmoke();
    this.runContract();
    this.runLibProbe();
    this.runKn();
    await this.runH3Net();        // H3：网络端口（不碰模型）
    await this.runH4Cred();       // H4：凭证端口（不碰模型，HUKS）
    await this.runModel();        // 第 1 个模型会话：MindSpore 探针
    await this.runEmbedSanity();  // 第 2 个（串行）：真实嵌入验收
    this.runRdbProbe();           // H5：RDB NDK C API 探针（不碰模型）
    await this.runRag();          // H5/H6：RAG 自检 + 检索评测（用同一个模型会话，串行）
  }

  /**
   * H5（工作流 F）①：RDB NDK C API 探针（HR2："最小验证先于完整实现"）。
   *
   * 只传 `databaseDir`——**路径由平台给**，Kotlin 侧不做推断（App 沙箱路径与
   * bundle/模块名相关，自己拼必然错）。四件事各有独立判据：
   * 能打开 / 能建表 / 能插入（含 BLOB）并查回来 / **关闭重开数据还在**。
   */
  runRdbProbe(): void {
    try {
      const ctx = getContext(this) as common.UIAbilityContext;
      // 两条独立判据，**故意都跑**：
      //   ① `rdbLinkProbe` —— C++ dlopen+dlsym（不依赖链接期）→ 证明"库可用 + 签名/结构体布局成立"；
      //   ② `rdbProbe`     —— Kotlin/Native 平台 klib（**链接期**依赖）→ 证明生产路径的绑定可用。
      // 两者结论一致才叫"两个独立判据互相印证"；只有 ② 的话，一旦 ② 失败，
      // 分不清是"库/签名问题"还是"我的链接配置问题"。
      // ⚠️ **两次调用各自 try**：Kotlin 侧入口是 undefined 符号，若 libkn.so 是旧产物
      //（没带新入口），调用会抛——那不能把 dlopen 那条判据也一起吞掉，
      // 否则"库里到底有没有 RDB"这个结论就永远拿不到（本轮真的遇到了这个情形）。
      let linkPart: string = '';
      let ktPart: string = '';
      try {
        linkPart = knspike.rdbLinkProbe(ctx.databaseDir);
      } catch (e1) {
        linkPart = `RDB-DLOPEN FAIL — rdbLinkProbe 调用失败（libknspike.so 是旧产物？）：${e1}`;
      }
      try {
        ktPart = knspike.rdbProbe(ctx.databaseDir);
      } catch (e2) {
        ktPart = `RDB-PROBE FAIL — rdbProbe 调用失败（libkn.so 是旧产物，缺 sekb_spike_rdb_probe？）：${e2}`;
      }
      this.rdb = this.emit(linkPart + '\n---- Kotlin/Native 平台 klib 侧 ----\n' + ktPart);
    } catch (e) {
      this.rdb = `❌ ${e}`;
      hilog.error(0x0000, TAG, 'RDB-PROBE FAIL — threw: %{public}s', JSON.stringify(e));
    }
  }

  /**
   * H5（工作流 F）②：RAG 自检 + 检索评测同表。
   *
   * 与 Android `SelfTest.kt` 的 `rag_*` 项同名同口径；索引是 **RDB 持久化实现**，
   * 检索逻辑**全部来自共享层**（`KnowledgeIndex`/`Retriever`/`RetrievalEvalRunner`）——
   * 鸿蒙侧没有第二套检索逻辑。
   *
   * ⚠️ 必须串在 `runEmbedSanity()` **之后**：两者都要模型，而并发跑模型会崩
   * （见 `aboutToAppear` 里那条 cppcrash 栈）。
   */
  async runRag(): Promise<void> {
    try {
      const ctx = getContext(this) as common.UIAbilityContext;
      const path: string = await this.ensureModelFile(ctx);
      const vocab: string = await this.readRawText(ctx, 'vocab.txt');
      this.rag = this.emit(knspike.ragSelfTest(path, vocab, ctx.databaseDir));
    } catch (e) {
      this.rag = `❌ ${e}`;
      hilog.error(0x0000, TAG, 'RAG FAIL — rag self-test threw: %{public}s', JSON.stringify(e));
    }
  }

  /** 逐行打 hilog（脚本从这里取证据）；界面只显示首行（见 `firstLine` 的崩溃教训）。 */
  private emit(report: string): string {
    for (const line of report.split('\n')) {
      if (line.length > 0) {
        hilog.info(0x0000, TAG, '%{public}s', line);
      }
    }
    return report;
  }

  /** H3：把响应体按共享层口径粗查（只用于挑候选/判字段，**不下结论**，结论在 Kotlin 侧）。 */
  private h3FieldsOk(body: string): boolean {
    return body.indexOf('"models"') >= 0 || body.indexOf('device_id') >= 0;
  }

  /**
   * H3 · 网络端口（`@ohos.net.http`）。
   *
   * 形状与其它探针不同：**ArkTS 主动把已观测事实交给 Kotlin**（不是 Kotlin 发起请求），
   * 因为共享层的 `HttpTransport` 是同步接口、而 ArkTS 的 HTTP 是异步的——
   * Kotlin 若在 NAPI 调用里等 ArkTS 的 `await`，回调排不进被卡住的那条线程（死锁）。
   * 理由与取舍见 `knspike/.../NetPort.kt` 与 `model/NativeHttpBridge.ets` 的文件头。
   *
   * 四条探针里有**两条是反向验证**（期望非 2xx），这是"判据不是摆设"的直接证据：
   *   · `transport_refuses_bad_endpoint`   —— 指向没人监听的端口（连不上）
   *   · `transport_refuses_404`            —— 指向**能连上**但路径不存在的端点（期望 404）
   */
  async runH3Net(): Promise<void> {
    let acc: string = '';
    let base: string = '';
    const tried: string[] = [];
    try {
      // ① 候选 base URL 逐个实测（不假设是哪条通）
      for (const cand of OLLAMA_CANDIDATES) {
        const u: string = `${cand}/api/tags`;
        const f: string = await probeBuffered(u, NET_TIMEOUT_S);
        const parts: string[] = f.split('\n');
        const head: string = parts.length > 0 ? parts[0] : '';
        const body: string = parts.length > 1 ? parts.slice(1).join('\n') : '';
        const code: string = head.split(' ')[1] !== undefined ? head.split(' ')[1] : '-1';
        tried.push(`${u}:code=${code}:bodyChars=${body.length}`);
        if (code === '200') {
          base = cand;
          const bytes: string = head.indexOf('bodyUTF8=') >= 0
            ? head.substring(head.indexOf('bodyUTF8=') + 9).split(' ')[0] : '-1';
          acc += this.emit(knspike.h3Probe(
            'transport_get_host_ollama', u, '200', code, bytes, `${body.length}`,
            this.h3FieldsOk(body) ? '1' : '0')) + '\n';
          break;
        }
      }
      if (base.length === 0) {
        acc += 'FAIL transport_get_host_ollama — 没有一条候选给出 200：' + tried.join(' | ') + '\n';
        hilog.error(0x0000, TAG, 'FAIL transport_get_host_ollama — %{public}s', tried.join(' | '));
      } else {
        acc += this.emit(`H3-CANDIDATES 通的是 ${base}（逐个实测：${tried.join(' | ')}）`) + '\n';
      }

      // ② 反向验证 A：没人监听的端口（连不上）
      const badFrame: string = await probeBuffered(BAD_URL, 8);
      const badHead: string = badFrame.split('\n')[0];
      const badCode: string = badHead.split(' ')[1] !== undefined ? badHead.split(' ')[1] : '-1';
      acc += this.emit(knspike.h3Probe(
        'transport_refuses_bad_endpoint', BAD_URL, '-1', badCode, '-1', '-1', '0')) + '\n';

      // ③ 反向验证 B：能连上但路径不存在（期望 404 —— 证明"非 200 不会被当成成功"）
      const streamBase: string = base.length > 0 ? base : OLLAMA_CANDIDATES[0];
      const missUrl: string = `${streamBase}/api/does-not-exist`;
      const missFrame: string = await probeBuffered(missUrl, NET_TIMEOUT_S);
      const missHead: string = missFrame.split('\n')[0];
      const missCode: string = missHead.split(' ')[1] !== undefined ? missHead.split(' ')[1] : '-1';
      acc += this.emit(knspike.h3Probe(
        'transport_refuses_404', missUrl, '404', missCode, '-1', '-1', '0')) + '\n';

      // ④ 流式：宿主 Ollama 的 /api/chat（NDJSON，按行流式输出）
      const streamUrl: string = `${streamBase}/api/chat`;
      knspike.h3StreamBegin();
      const streamFrame: string = await probeStream(
        streamUrl,
        '{"model":"qwen2.5:7b","messages":[{"role":"user","content":"用两个字回答"}],"stream":true}',
        NET_TIMEOUT_S * 4,
      );
      const sParts: string[] = streamFrame.split('\n');
      const sHead: string = sParts.length > 0 ? sParts[0] : '';
      const sCode: string = sHead.split(' ')[1] !== undefined ? sHead.split(' ')[1] : '-1';
      const sLines: string = sHead.indexOf('lines=') >= 0
        ? sHead.substring(sHead.indexOf('lines=') + 6).split(' ')[0] : '0';
      // Kotlin 侧实际收到的行数由 **Kotlin** 给出（判据是"行真的跨 NAPI 到了 Kotlin"）
      const knLines: string = knspike.h3PumpLines();
      acc += this.emit(knspike.h3Stream(streamUrl, sCode, sLines, knLines)) + '\n';
    } catch (e) {
      acc += `❌ ${e}\n`;
      hilog.error(0x0000, TAG, 'H3 FAIL — network port threw: %{public}s', JSON.stringify(e));
    }
    this.net = acc;
  }

  /**
   * H4 · 凭证端口（HUKS，纯 Kotlin/Native cinterop/dlopen；**不依赖网络**）。
   *
   * `reset='0'`：保留上一轮写入的凭证，于是 `credential_restart` 判据才是在验
   * "重启后仍有效"（脚本会再 force-stop 起一次，对比两轮的 device_id 指纹）。
   */
  async runH4Cred(): Promise<void> {
    try {
      const ctx = getContext(this) as common.UIAbilityContext;
      const report: string = knspike.credSelfTest(ctx.filesDir, '0', `${Date.now()}`);
      this.cred = this.emit(report);
    } catch (e) {
      this.cred = `❌ ${e}`;
      hilog.error(0x0000, TAG, 'H4 FAIL — credential port threw: %{public}s', JSON.stringify(e));
    }
  }

  /**
   * H3 / HR9：libcurl 三探针（①能加载并解析符号 ②HTTPS GET 通 ③写回调流式逐行）。
   *
   * 为什么先做探针而不是直接写 `HttpTransport`：HR9 明确要求"任一不通就回头找 owner"，
   * 而这条路径的风险全在"设备上到底有没有可用的 libcurl（含 TLS）"——
   * 那是**运行期事实**，只能实测（host 侧 grep 系统镜像只能说明文件在）。
   */
  runLibProbe(): void {
    try {
      const report: string = knspike.libProbe('1');
      for (const line of report.split('\n')) {
        if (line.length > 0) {
          hilog.info(0x0000, TAG, '%{public}s', line);
        }
      }
      this.libProbe = report;
    } catch (e) {
      this.libProbe = `❌ ${e}`;
      hilog.error(0x0000, TAG, 'LIBCURL PROBE FAIL — threw: %{public}s', JSON.stringify(e));
    }
  }

  /**
   * H2：契约夹具 runner（37 case：routing 10 / signals 14 / privacy 13）。
   *
   * 夹具从 **HAP rawfile** 读（`contract/*.json`），传入 native 侧由共享层的
   * `PlaneRouter` 逐 case 断言 —— 判定逻辑与 Android 那份 runner 逐条对齐。
   *
   * 同时跑**反向验证**（`contractPair`）：把 routing 第一条 case 的期望平面反转，
   * 结果必须**恰好 1 个 FAIL**。两条结论一起看才有意义：只跑正常那条的话，
   * "runner 其实没断言任何东西"也会全绿。
   */
  async runContract(): Promise<void> {
    try {
      const ctx = getContext(this) as common.UIAbilityContext;
      const routing: string = await this.readRawText(ctx, `${CONTRACT_DIR}routing.json`);
      const signals: string = await this.readRawText(ctx, `${CONTRACT_DIR}signals.json`);
      const privacy: string = await this.readRawText(ctx, `${CONTRACT_DIR}privacy.json`);
      const report: string = knspike.contractPair(routing, signals, privacy);
      for (const line of report.split('\n')) {
        hilog.info(0x0000, TAG, '%{public}s', line);
      }
      this.contract = report;
    } catch (e) {
      this.contract = `❌ ${e}`;
      hilog.error(0x0000, TAG, 'CONTRACT FAIL — contract runner threw: %{public}s', JSON.stringify(e));
    }
  }

  /** 读 HAP rawfile 里的文本（夹具用；体积只有几 KB）。 */
  async readRawText(ctx: common.UIAbilityContext, name: string): Promise<string> {
    const bytes: Uint8Array = await ctx.resourceManager.getRawFileContent(name);
    return util.TextDecoder.create('utf-8').decodeToString(bytes);
  }

  /**
   * H1：共享层 interop 冒烟（`apps/shared/src/commonMain` 经 `:shared-core` 编进来）。
   *
   * 逐行打 hilog（`SMOKE PASS n/total` 在第一行），脚本 grep 这一行即可判定；
   * 同时也显示在界面上，便于截图取证。
   */
  runSmoke(): void {
    try {
      const report: string = knspike.smoke();
      const lines: string[] = report.split('\n');
      for (const line of lines) {
        hilog.info(0x0000, TAG, '%{public}s', line);
      }
      this.smoke = report;
    } catch (e) {
      this.smoke = `❌ ${e}`;
      hilog.error(0x0000, TAG, 'SMOKE FAIL — shared layer smoke threw: %{public}s', JSON.stringify(e));
    }
  }

  /** 第 1 组：NAPI ↔ KMP 链路。 */
  runKn(): void {
    try {
      const p: number = knspike.ping();
      this.ping = `${p}`;
      const text: string = '端侧知识库';
      this.echoLen = `"${text}" → ${knspike.echoLen(text)}`;
      hilog.info(0x0000, TAG, 'NAPI->KMP ok: ping=%{public}d', p);
    } catch (e) {
      this.ping = `❌ ${e}`;
      hilog.error(0x0000, TAG, 'NAPI->KMP failed: %{public}s', JSON.stringify(e));
    }
  }

  /** 第 2/3 组：MindSpore Lite 加载 `.ms` 并推理。 */
  async runModel(): Promise<void> {
    try {
      const ctx = getContext(this) as common.UIAbilityContext;
      const path: string = await this.ensureModelFile(ctx);
      this.modelPath = path;
      const n: number = knspike.msSelfTest(path);
      this.msResult = n > 0 ? `✅ 输出元素数=${n}` : `❌ 失败步骤码=${n}`;
      const f: number = n > 0 ? knspike.msFirstFloat(path) : 0;
      this.msFirstFloat = n > 0 ? `|v[0]|×1e6 = ${f}` : '（未跑）';
      hilog.info(0x0000, TAG, 'mindspore selftest elemNum=%{public}d firstFloat=%{public}d', n, f);
    } catch (e) {
      this.msResult = `❌ ${e}`;
      hilog.error(0x0000, TAG, 'mindspore failed: %{public}s', JSON.stringify(e));
    }
  }

  /**
   * H6：端侧嵌入验收（余弦 / L2 / 确定性 / 失败即同数量零向量）。
   *
   * 需要两样东西：`.ms` 的**真实文件路径**（先复制到私有目录）与 `vocab.txt` 的**内容**。
   */
  async runEmbedSanity(): Promise<void> {
    try {
      const ctx = getContext(this) as common.UIAbilityContext;
      const path: string = await this.ensureModelFile(ctx);
      const vocab: string = await this.readRawText(ctx, 'vocab.txt');
      const report: string = knspike.embedSanity(path, vocab);
      for (const line of report.split('\n')) {
        if (line.length > 0) {
          hilog.info(0x0000, TAG, '%{public}s', line);
        }
      }
      this.embed = report;
    } catch (e) {
      this.embed = `❌ ${e}`;
      hilog.error(0x0000, TAG, 'EMBED FAIL — embed sanity threw: %{public}s', JSON.stringify(e));
    }
  }

  /**
   * rawfile → 应用私有目录（只做一次）。返回真实文件路径。
   *
   * ⚠️ 并发调用必须共享**同一个** in-flight Promise：两个调用者各自复制 94MB 时，
   * 后一个会在前一个还没写完时去打开那个半成品文件——模型构建拿到半截数据，
   * 表现就是"推理内部崩"（见 aboutToAppear 的崩溃栈）。
   */
  private modelCopy: Promise<string> | null = null;

  async ensureModelFile(ctx: common.UIAbilityContext): Promise<string> {
    const dest: string = `${ctx.filesDir}/${MODEL_RAWFILE}`;
    if (fileIo.accessSync(dest)) {
      return dest;
    }
    if (this.modelCopy === null) {
      this.modelCopy = this.copyModelFile(ctx, dest);
    }
    return this.modelCopy;
  }

  private async copyModelFile(ctx: common.UIAbilityContext, dest: string): Promise<string> {
    const data: Uint8Array = await ctx.resourceManager.getRawFileContent(MODEL_RAWFILE);
    const f = fileIo.openSync(dest, fileIo.OpenMode.CREATE | fileIo.OpenMode.READ_WRITE);
    try {
      fileIo.writeSync(f.fd, data.buffer as ArrayBuffer);
    } finally {
      fileIo.closeSync(f);
    }
    return dest;
  }

  build() {
    Column({ space: 10 }) {
      Text('M6 spike · KMP via NAPI').fontSize(20).fontWeight(FontWeight.Bold)
      Text(`sekb_spike_ping() = ${this.ping}`).fontSize(15)
      Text(`echo_len() = ${this.echoLen}`).fontSize(15)
      Divider()
      Text('共享层冒烟（apps/shared → ohosArm64）').fontSize(17).fontWeight(FontWeight.Bold)
      // ⚠️ 只显示**首行**：完整报告在 hilog 里（脚本从 hilog 取证据）。
      //    实测教训（2026-10-25）：把多段完整报告（各十几行）都塞进 Text 渲染，
      //    ArkUI 在 `CustomNode::Render` 里 SIGSEGV 崩掉——**界面不是放日志的地方**。
      Text(firstLine(this.smoke)).fontSize(12).maxLines(3)
      Divider()
      Text('契约夹具（apps/contract 37 case + 反向验证）').fontSize(17).fontWeight(FontWeight.Bold)
      Text(firstLine(this.contract)).fontSize(12).maxLines(3)
      Divider()
      Text('H3/HR9 · libcurl 三探针（dlopen）').fontSize(17).fontWeight(FontWeight.Bold)
      Text(firstLine(this.libProbe)).fontSize(12).maxLines(3)
      Divider()
      Text('H6 · 端侧嵌入验收（余弦/L2/确定性/降级）').fontSize(17).fontWeight(FontWeight.Bold)
      Text(firstLine(this.embed)).fontSize(12).maxLines(3)
      Divider()
      Text('H3 · 网络端口（@ohos.net.http + 共享层解析）').fontSize(17).fontWeight(FontWeight.Bold)
      Text(firstLine(this.net)).fontSize(12).maxLines(3)
      Divider()
      Text('H4 · 凭证端口（HUKS 加解密 + 重启后仍有效）').fontSize(17).fontWeight(FontWeight.Bold)
      Text(firstLine(this.cred)).fontSize(12).maxLines(3)
      Divider()
      Divider()
      Text('H5 · RAG（共享层 + RDB NDK C API）').fontSize(17).fontWeight(FontWeight.Bold)
      Text(firstLine(this.rdb)).fontSize(12).maxLines(2)
      Text(firstLine(this.rag)).fontSize(12).maxLines(2)
      Divider()
      Text('MindSpore Lite 端侧嵌入').fontSize(17).fontWeight(FontWeight.Bold)
      Text(`模型: ${this.modelPath}`).fontSize(11).fontColor(Color.Gray).maxLines(2)
      Text(this.msResult).fontSize(15)
      Text(this.msFirstFloat).fontSize(15)
      Text('元素数应=262144（1×512×512）；首元素非零才算真算出东西')
        .fontSize(11).fontColor(Color.Gray)
    }
    .width('100%')
    .height('100%')
    .padding(16)
    .justifyContent(FlexAlign.Center)
  }
}
