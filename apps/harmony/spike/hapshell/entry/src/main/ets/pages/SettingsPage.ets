/**
 * H8（工作流 G）· 设置页。
 *
 * 六节（与 Android `SettingsScreen.kt` 对齐，外加 H8 要求的自检入口）：
 *   ① 设备身份  —— enroll/refresh：**无账号/无云端 → 如实显示"未接入"**（不假装已连）
 *   ② 检索阈值  —— 默认 **0.40**（标定值，见共享层 `Retriever` 注释）；改动同时推给共享层
 *   ③ 端侧嵌入  —— 模型文件 / 空间戳 / 维度 / 本机计算
 *   ④ 网络与后端 —— 端侧地址（Ollama）、后端地址、端侧模型；带"测试连接"
 *   ⑤ 自检入口  —— `SEKB_HARMONY_SELFTEST` 汇总行 + 逐项表（**界面只放摘要 + 限高列表**）
 *   ⑥ 版本信息
 *
 * ## 为什么"未接入"必须显式显示
 *
 * enroll/refresh 需要账号与云端端点。若界面让"设备身份"看起来像已就绪，
 * 使用者（或 reviewer）会以为端云链路是通的——那比明确写"未接入"危险得多。
 * 本页的做法：红色状态点 + 一行原因 + 禁用按钮。
 */
import { common } from '@kit.AbilityKit';
import { AppCtx } from '../model/AppCtx';
import { ChatController } from '../model/ChatController';
import { SelfTestItem, SelfTestReport, runSelfTest } from '../model/SelfTestRunner';
import { Theme } from '../model/Theme';
import { ragDocuments, ragSetThreshold } from '../model/SekbNative';
import { SectionCard } from './SectionCard';

@Component
export struct SettingsPage {
  @Require ctx: AppCtx;
  @Require ctrl: ChatController;
  @State edgeUrl: string = '';
  @State sekbUrl: string = '';
  @State model: string = '';
  @State threshold: number = 0.40;
  @State appliedThreshold: number = 0.40;
  @State probeNote: string = '';
  @State saveNote: string = '';
  @State embedText: string = '';
  @State docsNote: string = '';
  @State selftestRunning: boolean = false;
  @State selftestSummary: string = '';
  @State selftestItems: SelfTestItem[] = [];

  aboutToAppear(): void {
    this.edgeUrl = this.ctx.settings.edgeBaseUrl;
    this.sekbUrl = this.ctx.settings.sekbBaseUrl;
    this.model = this.ctx.settings.model;
    this.threshold = this.ctx.settings.minScore;
    this.appliedThreshold = this.ctx.settings.minScore;
    this.embedText = this.ctx.embedStatusText();
    const d = ragDocuments();
    this.docsNote = d.ok ? `文档库：${d.docs.length} 篇 / ${d.chunks} 段` : `文档库不可用：${d.reason}`;
  }

  private saveAll(): void {
    const a: boolean = this.ctx.settings.setEdgeBaseUrl(this.edgeUrl);
    const b: boolean = this.ctx.settings.setSekbBaseUrl(this.sekbUrl);
    const c: boolean = this.ctx.settings.setModel(this.model);
    this.saveNote = (a && b && c) ? '已保存（本轮起使用新配置）' : '⚠️ 保存失败：应用存储不可写';
  }

  /** 阈值：设置页 + 共享层**同时**更新（两份不一致会让"同表"变成假话）。 */
  private applyThreshold(v: number): void {
    const effective: number = this.ctx.settings.setMinScore(v);
    const shared: number = ragSetThreshold(effective);
    this.appliedThreshold = shared;
    this.threshold = shared;
    this.saveNote = `检索阈值 = ${shared.toFixed(2)}（设置页与共享层同值；非法值回落 0.40）`;
  }

  private async runCheck(red: boolean): Promise<void> {
    this.selftestRunning = true;
    this.selftestSummary = '自检中…（串行跑模型相关项，约十几秒）';
    const inst = getContext(this) as common.UIAbilityContext;
    try {
      const rep: SelfTestReport = await runSelfTest(this.ctx, inst, red);
      // 界面**只放摘要 + 汇总行**；明细走 hilog（长文本会 SIGSEGV，见 SelfTestRunner 文件头）
      this.selftestSummary = rep.uiLine();
      this.selftestItems = rep.items;
      this.embedText = this.ctx.embedStatusText();
      const d = ragDocuments();
    this.docsNote = d.ok ? `文档库：${d.docs.length} 篇 / ${d.chunks} 段` : `文档库不可用：${d.reason}`;
      this.ctrl.markRag(this.ctx.lastRagReady, this.ctx.lastRagNote);
    } catch (e) {
      this.selftestSummary = `自检抛错：${e}`;
    }
    this.selftestRunning = false;
  }

  build() {
    Scroll() {
      Column() {
        // ── ① 设备身份 ──
        SectionCard({ title: '设备身份' }) {
          Row() {
            Circle({ width: 6, height: 6 }).fill(Theme.cloud)
            Text('未接入').fontSize(Theme.fsBody).fontColor(Theme.danger).margin({ left: 6 })
            Text('（无账号 / 无云端端点）').fontSize(Theme.fsMeta).fontColor(Theme.textFaint)
          }
          Text('设备凭证需要先登录账号再 enroll 换长效设备 token。本机没有账号与可用的云端端点，' +
            '因此 enroll / refresh 的端到端**未接入**；界面不显示伪造的 device_id。')
            .fontSize(Theme.fsMeta).fontColor(Theme.textSub).margin({ top: 6 })
          Row() {
            Button('接入设备（enroll）')
              .height(32).fontSize(Theme.fsMeta).borderRadius(Theme.rPill)
              .backgroundColor(Theme.inputBg).fontColor(Theme.textFaint)
              .enabled(false)
            Button('刷新凭证（refresh）')
              .height(32).fontSize(Theme.fsMeta).borderRadius(Theme.rPill)
              .backgroundColor(Theme.inputBg).fontColor(Theme.textFaint)
              .enabled(false).margin({ left: 8 })
          }
          .margin({ top: 10 })
          Text('按钮禁用是**如实**的：没有账号与云端端点时不假装可以接入。')
            .fontSize(Theme.fsMeta).fontColor(Theme.textFaint).margin({ top: 4 })
        }

        // ── ② 检索阈值 ──
        SectionCard({ title: '检索阈值' }) {
          Row() {
            Text(`相似度阈值 ${this.threshold.toFixed(2)}`)
              .fontSize(Theme.fsBody).fontColor(Theme.text).layoutWeight(1)
            Text(`生效 ${this.appliedThreshold.toFixed(2)}`)
              .fontSize(Theme.fsMeta).fontColor(Theme.primary)
          }
          Slider({
            value: this.threshold,
            min: 0.10,
            max: 0.90,
            step: 0.05,
            style: SliderStyle.OutSet,
          })
            .width('100%')
            .selectedColor(Theme.primary)
            .onChange((v: number, mode: SliderChangeMode) => {
              this.threshold = Math.round(v * 100) / 100;
              if (mode === SliderChangeMode.End) {
                this.applyThreshold(this.threshold);
              }
            })
          Text('默认 0.40 是标定值（33 条标注集：0.20 时误召回 3/3，0.40 时 0/3 且 Hit@1 不降）。' +
            '低于 0.30 会让"库里没有的问题"也开始命中——那是幻觉源。')
            .fontSize(Theme.fsMeta).fontColor(Theme.textFaint).margin({ top: 4 })
        }

        // ── ③ 端侧嵌入状态 ──
        SectionCard({ title: '端侧嵌入（本机计算）' }) {
          Text(this.embedText.length > 0 ? this.embedText : '尚未加载模型（进「文档」页或跑一次自检会加载）')
            .fontSize(Theme.fsBody).fontColor(Theme.text).maxLines(3)
          Text('空间戳是"向量能不能复用"的唯一判据：与云端不一致时禁止融合检索（共享层闸门）。')
            .fontSize(Theme.fsMeta).fontColor(Theme.textFaint).margin({ top: 4 })
        }

        // ── ④ 网络与后端地址 ──
        SectionCard({ title: '网络与后端地址' }) {
          Text('端侧地址（Ollama，模拟器：hdc rport tcp:11434 tcp:11434）')
            .fontSize(Theme.fsMeta).fontColor(Theme.textSub)
          TextInput({ text: this.edgeUrl, placeholder: 'http://127.0.0.1:11434' })
            .height(40).fontSize(Theme.fsBody).backgroundColor(Theme.inputBg)
            .borderRadius(Theme.rCard).margin({ top: 4 })
            .onChange((v: string) => {
              this.edgeUrl = v;
            })
          Text('后端地址（云端平面；本机未起后端）')
            .fontSize(Theme.fsMeta).fontColor(Theme.textSub).margin({ top: 10 })
          TextInput({ text: this.sekbUrl, placeholder: 'http://127.0.0.1:8000' })
            .height(40).fontSize(Theme.fsBody).backgroundColor(Theme.inputBg)
            .borderRadius(Theme.rCard).margin({ top: 4 })
            .onChange((v: string) => {
              this.sekbUrl = v;
            })
          Text('端侧对话模型')
            .fontSize(Theme.fsMeta).fontColor(Theme.textSub).margin({ top: 10 })
          TextInput({ text: this.model, placeholder: 'qwen2.5:0.5b' })
            .height(40).fontSize(Theme.fsBody).backgroundColor(Theme.inputBg)
            .borderRadius(Theme.rCard).margin({ top: 4 })
            .onChange((v: string) => {
              this.model = v;
            })
          Row() {
            Button('保存')
              .height(32).fontSize(Theme.fsMeta).borderRadius(Theme.rPill)
              .backgroundColor(Theme.primary)
              .onClick(() => {
                this.saveAll();
              })
            Button('测试连接')
              .height(32).fontSize(Theme.fsMeta).borderRadius(Theme.rPill)
              .backgroundColor(Theme.primarySoft).fontColor(Theme.primary)
              .margin({ left: 8 })
              .onClick(() => {
                this.ctrl.probe().then(() => {
                  this.probeNote = this.ctrl.edgeReachable === true
                    ? `连通：${this.edgeUrl}/api/tags → 200`
                    : `不可达：${this.edgeUrl}（检查 hdc rport 与宿主 Ollama）`;
                });
              })
          }
          .margin({ top: 10 })
          if (this.saveNote.length > 0) {
            Text(this.saveNote).fontSize(Theme.fsMeta).fontColor(Theme.textSub).margin({ top: 6 })
          }
          if (this.probeNote.length > 0) {
            Text(this.probeNote).fontSize(Theme.fsMeta)
              .fontColor(this.ctrl.edgeReachable === true ? Theme.edgeOk : Theme.danger)
              .margin({ top: 4 }).maxLines(2)
          }
        }

        // ── ⑤ 自检入口 ──
        SectionCard({ title: '自检（SEKB_HARMONY_SELFTEST）' }) {
          Text(this.selftestSummary.length > 0 ? this.selftestSummary : '尚未运行')
            .fontSize(Theme.fsBody)
            .fontColor(this.selftestSummary.indexOf('FAIL=0') >= 0 ? Theme.edgeOk : Theme.text)
            .maxLines(3)
          Row() {
            Button(this.selftestRunning ? '运行中…' : '运行自检')
              .height(32).fontSize(Theme.fsMeta).borderRadius(Theme.rPill)
              .backgroundColor(Theme.primary).enabled(!this.selftestRunning)
              .onClick(() => {
                this.runCheck(false);
              })
            Button('反向验证（应出现 1 个 FAIL）')
              .height(32).fontSize(Theme.fsMeta).borderRadius(Theme.rPill)
              .backgroundColor(Theme.dangerSoft).fontColor(Theme.danger)
              .enabled(!this.selftestRunning).margin({ left: 8 })
              .onClick(() => {
                this.runCheck(true);
              })
          }
          .margin({ top: 10 })
          Text('明细逐行写 hilog（TAG=SEKB_HARMONY_SELFTEST），界面只放摘要——' +
            'ArkUI 渲染多段长文本会 SIGSEGV（实测）。取值：bash scripts/harmony.sh selftest')
            .fontSize(Theme.fsMeta).fontColor(Theme.textFaint).margin({ top: 6 })

          if (this.selftestItems.length > 0) {
            // **限高 + 每项 1 行**：把明细塞满屏幕是有过的崩溃原因
            List({ space: 2 }) {
              ForEach(this.selftestItems, (it: SelfTestItem) => {
                ListItem() {
                  Row() {
                    Text(`[${it.status}]`)
                      .fontSize(Theme.fsMeta)
                      .fontColor(it.status === 'PASS' ? Theme.edgeOk
                        : (it.status === 'FAIL' ? Theme.danger : Theme.cloud))
                      .width(46)
                    Text(it.name).fontSize(Theme.fsMeta).fontColor(Theme.text).width(150)
                      .maxLines(1).textOverflow({ overflow: TextOverflow.Ellipsis })
                    Text(it.detail).fontSize(Theme.fsMeta).fontColor(Theme.textFaint)
                      .layoutWeight(1).maxLines(1).textOverflow({ overflow: TextOverflow.Ellipsis })
                  }
                  .width('100%')
                }
              }, (it: SelfTestItem) => `${it.name}-${it.status}`)
            }
            .constraintSize({ maxHeight: 260 })
            .width('100%')
            .margin({ top: 8 })
          }
        }

        // ── ⑥ 版本信息 ──
        SectionCard({ title: '版本信息' }) {
          Text(`SEKB 鸿蒙端 ${Theme.version}（${Theme.buildFlavor}）`)
            .fontSize(Theme.fsBody).fontColor(Theme.text)
          Text('端侧嵌入 bge-small-zh（MindSpore Lite · .ms 随包分发）· 向量库 RDB NDK · 凭证 HUKS')
            .fontSize(Theme.fsMeta).fontColor(Theme.textSub).margin({ top: 4 })
          Text(`会话持久化：${this.ctrl.storageWritable() ? 'JSON 文件（可写）' : '⚠️ 不可写（重启丢失）'}｜${this.docsNote}`)
            .fontSize(Theme.fsMeta).fontColor(Theme.textFaint).margin({ top: 4 }).maxLines(2)
          Text('版本号目前是源码常量（未从 bundle 元数据读），与 module.json5 的 versionName 手工同步。')
            .fontSize(Theme.fsMeta).fontColor(Theme.textFaint).margin({ top: 4 })
        }
      }
      .width('100%')
      .padding({ bottom: 16 })
    }
    .width('100%')
    .height('100%')
    .backgroundColor(Theme.bg)
    .scrollBar(BarState.Auto)
  }
}
