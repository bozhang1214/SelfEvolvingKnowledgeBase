/**
 * H8（工作流 G）· 应用级共享对象（**页面之间唯一共享的东西**）。
 *
 * 刻意不做状态管理框架（ArkUI 的 `@ObservedV2`/`@Provide` 都能用，但这里没必要）：
 * 四个页面各自持有 `@State`，共享的只有三样**不参与渲染**的东西：
 *
 *   1. `HistoryStore`  —— 会话落盘（JSON 文件，见该文件头：与 RDB 解耦）
 *   2. `SettingsStore` —— 端侧地址/后端地址/模型/阈值
 *   3. `AppCtx`        —— 把上面两个 + 应用沙箱路径 + 模型文件准备逻辑传给页面
 *
 * 这样页面之间的耦合只剩"回调用它"，而不是"共享可变状态"——后者在 ArkUI 上会引入
 * 一整套观察机制，收益不成比例。
 */
import { common } from '@kit.AbilityKit';
import { BusinessError } from '@kit.BasicServicesKit';
import { fileIo } from '@kit.CoreFileKit';
import { hilog } from '@kit.PerformanceAnalysisKit';
import { util } from '@kit.ArkTS';
import { HistoryStore } from './HistoryStore';
import { SettingsStore } from './SettingsStore';
import { ragEmbedStatus, ragOpen, ragSetThreshold } from './SekbNative';

const TAG: string = 'SEKB_HARMONY_UI';
/** 随包分发的端侧嵌入模型（H1–H6 的 rawfile，名字与 `Index.ets` 一致）。 */
const MODEL_RAWFILE: string = 'bge-small-fixed512.ms';

/** UTF-8 解码（统一走 `util.TextDecoder`，不自己拼字节）。 */
function decodeUtf8(bytes: Uint8Array): string {
  if (bytes.length === 0) {
    return '';
  }
  return util.TextDecoder.create('utf-8').decodeToString(bytes);
}

/** 应用级上下文（由 `AppShell` 在 `aboutToAppear` 里构造一次）。 */
export class AppCtx {
  filesDir: string = '';
  /** `databaseDir` —— RDB 的路径**由平台给**（自己拼必然错，见 `RdbProbe` 的注释） */
  databaseDir: string = '';
  history: HistoryStore;
  settings: SettingsStore;

  /** 最近一次 `openRag` 的结论（设置页/聊天页共享：文档库可用性只有一份事实） */
  lastRagReady: boolean = false;
  lastRagNote: string = '';

  /** 是否由 App 侧预建了库文件（设置页/自检要如实显示这个"绕过平台限制"的动作） */
  precreatedDb: boolean = false;

  private modelFilePath: string = '';
  private modelCopy: Promise<string> | null = null;

  constructor(ctx: common.UIAbilityContext) {
    this.filesDir = ctx.filesDir;
    this.databaseDir = ctx.databaseDir;
    this.history = new HistoryStore(ctx.filesDir);
    this.settings = new SettingsStore(ctx.filesDir);
    // 阈值是"共享层与设置页必须同一份"的值：启动时把设置页的值推给共享层
    this.pushThreshold(this.settings.minScore);
  }

  /** 把阈值推给共享层（回落规则在 Kotlin 侧，只有一份）。返回生效值。 */
  pushThreshold(v: number): number {
    return ragSetThreshold(v);
  }

  /**
   * `.ms` 模型的**真实文件路径**（rawfile → 私有目录，只做一次）。
   *
   * ⚠️ 并发必须共享**同一个** in-flight Promise：两个调用者各复制 94MB 时，
   * 后一个会去打开还没写完的半成品文件，现象是"推理内部崩"
   * （2026-10-25 实测栈：`mindspore::lite::MallocTensorData`，见 `Index.ets` 的注释）。
   */
  async modelPath(ctx: common.UIAbilityContext): Promise<string> {
    if (this.modelFilePath.length > 0 && fileIo.accessSync(this.modelFilePath)) {
      return this.modelFilePath;
    }
    const dest: string = `${ctx.filesDir}/${MODEL_RAWFILE}`;
    if (fileIo.accessSync(dest)) {
      this.modelFilePath = dest;
      return dest;
    }
    if (this.modelCopy === null) {
      this.modelCopy = this.copyModel(ctx, dest);
    }
    this.modelFilePath = await this.modelCopy;
    return this.modelFilePath;
  }

  private async copyModel(ctx: common.UIAbilityContext, dest: string): Promise<string> {
    const data: Uint8Array = await ctx.resourceManager.getRawFileContent(MODEL_RAWFILE);
    const f = fileIo.openSync(dest, fileIo.OpenMode.CREATE | fileIo.OpenMode.READ_WRITE);
    try {
      fileIo.writeSync(f.fd, data.buffer as ArrayBuffer);
    } finally {
      fileIo.closeSync(f);
    }
    hilog.info(0x0000, TAG, '模型已复制到私有目录：%{public}s（%{public}d B）', dest, data.length);
    return dest;
  }

  /** 读 rawfile 文本（vocab / 契约夹具）。 */
  async rawText(ctx: common.UIAbilityContext, name: string): Promise<string> {
    const bytes: Uint8Array = await ctx.resourceManager.getRawFileContent(name);
    return decodeUtf8(bytes);
  }

  /**
   * 打开文档库（模型 + RDB 索引）。**失败不抛**，返回空串表示成功、非空表示原因。
   *
   * 这条路径正是"文档页 + 聊天页检索来源"的共享层入口；
   * 若工作流 F 的 RDB 链路还没绿，这里会拿到一个明确的 `reason`，
   * 由页面显示"未持久化 + 原因"，而不是假装有库。
   */
  async openRag(ctx: common.UIAbilityContext): Promise<string> {
    return this.openRagIsolated(ctx, '');
  }

  /**
   * 打开（可指定库名的）文档库。空串 = 界面的固定库 `sekb_docs.db`。
   *
   * 自检必须用**隔离库**：用户的文档库是设备侧唯一副本，跑一次自检不该往里塞文档，
   * 更不该删它（上一轮就是这么把"删前 2 篇"变成了不可解释的现象）。
   */
  async openRagIsolated(ctx: common.UIAbilityContext, dbName: string): Promise<string> {
    try {
      // ① **目录是硬前置**：`OH_Rdb_GetOrOpen` 自己不会建目录，目录不存在时它回
      //    `14800037 = RDB_E_CREATE_FOLDER_FAIL`（查 `relational_store_error_code.h`）。
      //    这里把"建目录 → 立刻复核 → 失败即返回"写成**不可跳过**的一步：
      //    第一版把 mkdir 的异常吞掉然后继续调 RDB，结果根因被 RDB 的错误码盖住了
      //    （外面只看到"打开 RDB 失败：errCode=14800037"，看不出是目录没建成）。
      const dirErr: string = this.ensureDatabaseDir(dbName);
      if (dirErr.length > 0) {
        this.lastRagReady = false;
        this.lastRagNote = dirErr;
        return dirErr;
      }
      const mp: string = await this.modelPath(ctx);
      const vocab: string = await this.rawText(ctx, 'vocab.txt');
      const r = ragOpen(mp, vocab, this.databaseDir, dbName);
      hilog.info(0x0000, TAG, 'ragOpen ok=%{public}s space=%{public}s dim=%{public}d reason=%{public}s',
        `${r.ok}`, r.space, r.dim, r.reason);
      this.lastRagReady = r.ok;
      this.lastRagNote = r.ok ? '' : (r.reason.length > 0 ? r.reason : '未知原因');
      return this.lastRagNote;
    } catch (e) {
      this.lastRagReady = false;
      this.lastRagNote = `${e}`;
      return this.lastRagNote;
    }
  }

  /**
   * 保证 `databaseDir` 存在（**建目录是硬前置**，见 `openRagIsolated` 的注释）。
   *
   * 返回空串 = 就绪；非空 = 失败原因（调用方必须**原样**把它显示出来，
   * 不要再继续去撞 RDB 的错误码——那会让根因变成"打开 RDB 失败"）。
   *
   * 三条诊断一起打（这轮就是靠它们把根因钉死的）：
   * 父目录存在？目标目录之前/之后存在？`mkdirSync` 抛的 `code`/`message`？
   * ⚠️ `JSON.stringify(me)` 对 BusinessError 往往给 `{}`，所以必须取 `code` 与 `message`。
   */
  ensureDatabaseDir(dbName: string = ''): string {
    const dir: string = this.databaseDir;
    const parent: string = dir.substring(0, dir.lastIndexOf('/'));
    let parentOk: boolean = false;
    let before: boolean = false;
    try {
      parentOk = fileIo.accessSync(parent);
      before = fileIo.accessSync(dir);
    } catch (e) {
      parentOk = false;
      before = false;
    }
    let mkErr: string = '';
    let parentMk: string = '未尝试';
    try {
      // ⚠️ **父目录也要建**（2026-10-26 主线实测的根因）：
      //    设备上 `/data/storage/el2/database/` **整个不存在**（`ls` 报 No such file or directory），
      //    所以"只 mkdir 目标目录"必然失败（mkdir 不建中间目录），而 RDB 自己也不会建 →
      //    报 `14800037 = RDB_E_CREATE_FOLDER_FAIL`。之前的诊断把这条盖住了：
      //    日志里只看到 `父目录存在=false`，但失败原因被笼统写成"mkdir 未报错但仍不存在"。
      //    先建父目录、再建目标目录，两步各自记结果（出问题时能一眼看出是哪一步）。
      if (!parentOk) {
        try {
          fileIo.mkdirSync(parent);
          parentMk = 'ok';
        } catch (pe) {
          parentMk = `code=${(pe as BusinessError).code} message=${(pe as BusinessError).message}`;
        }
      } else {
        parentMk = '父目录本就存在';
      }
      if (!before) {
        fileIo.mkdirSync(dir);
      }
    } catch (me) {
      mkErr = `code=${(me as BusinessError).code} message=${(me as BusinessError).message}`;
    }
    let after: boolean = false;
    try {
      after = fileIo.accessSync(dir);
    } catch (e) {
      after = false;
    }
    hilog.info(0x0000, TAG,
      'RDB 目录自检：path=%{public}s 父目录存在=%{public}s 之前存在=%{public}s 之后存在=%{public}s mkdir=%{public}s',
      dir, `${parentOk}`, `${before}`, `${after}`, `父=${parentMk} 目标=${mkErr.length > 0 ? mkErr : 'ok'}`);
    if (!after) {
      return `RDB 目录创建失败：${dir}（原父目录存在=${parentOk}，建父=${parentMk}，建目标=${mkErr.length > 0 ? mkErr : '未报错但仍不存在'}）`;
    }
    // ② 试过但**不生效**的一条路（写下来免得下一位重试）：既然目录存在、而新库名仍报
    //    `14800037`，我试过"由 App 侧先把库文件建成空文件，把新建变成打开已存在"——
    //    实测**仍然失败**（`预建=true 存在=true` 之后 RDB 依旧报 14800037）。
    //    所以这不是"文件不存在"的问题，本文件不再做那个 hack（留着空文件只会污染沙箱）。
    //    现象与三条诊断事实见 `apps/harmony/README.md` 的 H8「未验/未成」一节。
    return '';
  }

  /** 嵌入状态（设置页显示模型/空间戳/维度）。 */
  embedStatusText(): string {
    const s = ragEmbedStatus();
    if (!s.ok) {
      return `未就绪：${s.diagnostics}`;
    }
    return `${s.modelFile} · 空间=${s.space} · 维度=${s.dim} · 本机计算=true`;
  }
}
