/**
 * H8（工作流 G）· 设置项（端侧地址 / 后端地址 / 模型 / 检索阈值）。
 *
 * ## 为什么设置项要落盘
 *
 * 端侧地址在模拟器上是 `127.0.0.1:11434`（`hdc rport`），在真机上是宿主局域网 IP，
 * 桌面宿主上又是 Tailscale IP——写死一个值意味着"换环境必须改代码重装"。
 * 所以它是**配置**，不是常量；落盘后重启仍在。
 *
 * 检索阈值的默认值 **0.40** 不是拍的：它是 Android/iOS 用 33 条标注集标定出来的
 * （见共享层 `Retriever` 类注释的表格：0.2 时误召回 3/3，0.4 时 0/3 且 Hit@1 不降）。
 * 这里的默认值必须与共享层同一口径，否则"同表"就是假的。
 */
import { JsonFile } from './AppFiles';

/** 落盘的形状（加字段时给默认值，老文件仍能读）。 */
interface SettingsDto {
  edgeBaseUrl: string;
  sekbBaseUrl: string;
  model: string;
  minScore: number;
}

const DEFAULTS: SettingsDto = {
  // 模拟器：宿主 Ollama 经 `hdc rport tcp:11434 tcp:11434` 映射进来的地址
  edgeBaseUrl: 'http://127.0.0.1:11434',
  // SEKB 后端（云端平面）。本机没起后端，所以默认值只是"可编辑的占位"，不是"已连上"
  sekbBaseUrl: 'http://127.0.0.1:8000',
  // 端侧对话模型：Ollama 里最小的那个（模拟器上 9B 太慢，会让人误以为流式没工作）
  model: 'qwen2.5:0.5b',
  minScore: 0.40,
};

export class SettingsStore {
  private file: JsonFile;
  private dto: SettingsDto = DEFAULTS;
  private writableFlag: boolean = false;

  constructor(dir: string) {
    this.file = new JsonFile(`${dir}/settings.json`);
    this.load();
  }

  private load(): void {
    const text: string = this.file.readText();
    if (text.length === 0) {
      this.dto = DEFAULTS;
      this.writableFlag = true;
      return;
    }
    try {
      const o = JSON.parse(text) as SettingsDto;
      this.dto = {
        edgeBaseUrl: o.edgeBaseUrl !== undefined && o.edgeBaseUrl.length > 0 ? o.edgeBaseUrl : DEFAULTS.edgeBaseUrl,
        sekbBaseUrl: o.sekbBaseUrl !== undefined && o.sekbBaseUrl.length > 0 ? o.sekbBaseUrl : DEFAULTS.sekbBaseUrl,
        model: o.model !== undefined && o.model.length > 0 ? o.model : DEFAULTS.model,
        minScore: o.minScore !== undefined && o.minScore > 0 ? o.minScore : DEFAULTS.minScore,
      };
      this.writableFlag = true;
    } catch (e) {
      // 坏文件：回落默认值，但把"不可写/读到坏值"这件事记下来（设置页如实显示）
      this.dto = DEFAULTS;
      this.writableFlag = false;
    }
  }

  /** 持久化是否可用（设置页要如实显示"未持久化"）。 */
  writable(): boolean {
    return this.writableFlag;
  }

  get edgeBaseUrl(): string {
    return this.dto.edgeBaseUrl;
  }

  get sekbBaseUrl(): string {
    return this.dto.sekbBaseUrl;
  }

  get model(): string {
    return this.dto.model;
  }

  get minScore(): number {
    return this.dto.minScore;
  }

  private persist(): boolean {
    const ok: boolean = this.file.writeText(JSON.stringify(this.dto));
    this.writableFlag = ok;
    return ok;
  }

  setEdgeBaseUrl(v: string): boolean {
    this.dto.edgeBaseUrl = v.trim();
    return this.persist();
  }

  setSekbBaseUrl(v: string): boolean {
    this.dto.sekbBaseUrl = v.trim();
    return this.persist();
  }

  setModel(v: string): boolean {
    this.dto.model = v.trim();
    return this.persist();
  }

  /**
   * 设置检索阈值。非法值（NaN / ≤0 / >1）**回落 0.40** 并落盘——
   * 与共享层 `SekbApp.setMinScore` 同一条回落规则（阈值 0 会让检索变成幻觉源）。
   */
  setMinScore(v: number): number {
    const good: number = (Number.isNaN(v) || v <= 0 || v > 1) ? DEFAULTS.minScore : v;
    this.dto.minScore = good;
    this.persist();
    return good;
  }
}
