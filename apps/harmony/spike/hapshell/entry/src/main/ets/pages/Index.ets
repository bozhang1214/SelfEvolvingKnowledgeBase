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

const TAG: string = 'SEKB_KN_SPIKE';
const MODEL_RAWFILE: string = 'bge-small-fixed512.ms';
// H2：契约夹具在 HAP 里的位置（由 scripts/harmony_spike.sh build_hap 拷进来，
// 源头是仓库的 apps/contract/*.json —— 不新增夹具，只分发同一份）
const CONTRACT_DIR: string = 'contract/';

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
    await this.runModel();        // 第 1 个模型会话：MindSpore 探针
    await this.runEmbedSanity();  // 第 2 个（串行）：真实嵌入验收
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
