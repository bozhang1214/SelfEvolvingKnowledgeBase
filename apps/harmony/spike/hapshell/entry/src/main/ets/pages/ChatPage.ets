/**
 * H8（工作流 G）· 聊天页（与 Android `ChatScreen.kt` 对齐的形态）。
 *
 * ## 版式（有两条是**看图才发现的**，别改回去）
 *
 * ```
 *   ┌──────────────────────────────────────────┐
 *   │ SEKB  ·  端侧就绪/不可用（点状指示）        │  顶栏（AppShell 提供）
 *   ├──────────────────────────────────────────┤
 *   │ [空状态]  示例问题 …                        │  layoutWeight(1) —— **必须**
 *   │ 或 消息列表（用户右 / 助手左）               │
 *   ├──────────────────────────────────────────┤
 *   │ 输入框            [发送]                    │  固定高度，永远在屏内
 *   └──────────────────────────────────────────┘
 * ```
 *
 * **`layoutWeight(1)` 是必须的**：Android 真机上出过一次"空状态把输入栏挤出屏幕"——
 * 根因就是空状态用固定高度/`justifyContent` 撑满了父容器。所以这里：
 *   · 消息区（含空状态）`layoutWeight(1)`；
 *   · 输入栏**不参与**权重分配（固定内容高度）；
 *   · 空状态本身**不设** `height('100%')`。
 *
 * ## 徽章 / 来源 / 原因
 *
 * 每条助手气泡下面跟：执行位置徽章（本机=绿 / 云端=金）+ 原因（共享层原因码经
 * `explainReason` 渲染，认不出的原样透出）+ 条数化的检索来源。
 * 这三样**挂在气泡上**，翻历史时往上滚仍能看到"这一条为什么"。
 */
import { ChatController, SAMPLE_QUESTIONS } from '../model/ChatController';
import { ChatMsg, Source, badgeColor, badgeOf, explainReason, fmtTime } from '../model/SekbTypes';
import { Theme } from '../model/Theme';

@Component
export struct ChatPage {
  /**
   * 控制器是**普通成员**（不是 `@Prop`/`@State`）：它不参与 ArkUI 的渲染观察——
   * 渲染数据在本组件自己的 `@State` 里，由控制器通过回调推过来（单向，见 `ChatController` 注释）。
   *
   * `rev` 是"外部改了会话（历史页点开/新建）"的信号：`@Watch` 一响就重新同步。
   */
  @Require ctrl: ChatController;
  /** 生成状态推给外层（输入栏据此禁用发送） */
  onBusy: (busy: boolean) => void = () => {};
  /** 让外层拿到本组件引用（输入栏在外层，发送要回调到这里） */
  ref: (self: ChatPage) => void = () => {};
  @Prop @Watch('onRev') rev: number = 0;
  @State messages: ChatMsg[] = [];
  @State input: string = '';
  @State busy: boolean = false;
  @State thinking: string = '';
  @State error: string = '';
  @State reasonLine: string = '';
  @State edgeReachable: boolean = false;

  aboutToAppear(): void {
    this.ref(this);
    // 控制器每次状态变化都会回调这里 → 同步本页状态，并把 busy 推给外层输入栏
    this.ctrl.setOnChange(() => {
      this.refresh(true);
    });
    this.refresh();
    this.ctrl.probe();
  }

  /**
   * 由外层（`AppShell`）触发的发送。
   *
   * ⚠️ **输入栏为什么在外层**：这是"看图才发现的布局事实"。输入栏原先在本页内，
   * 与消息区一起组成"内层 Column（layoutWeight）+ 固定输入栏"；在模拟器上
   * （窗口 1320×2856 全屏 + 悬浮手势条）底部两行控件被**裁掉**。
   * 把输入栏与标签栏挪到**外层根 Column** 之后位置正确 —— 少一层 `layoutWeight`
   * 嵌套，底部固定高度的排布就与直觉一致了。
   */
  sendInput(text: string): void {
    this.send(text);
  }

  /** 外部（历史页/顶栏）切换了会话 → 重新拉一次状态。 */
  onRev(): void {
    this.refresh();
    this.scrollToBottom();
  }

  /** 控制器 → 页面状态（单向；控制器在流式里反复调用它）。 */
  private refresh(notifyBusy: boolean = false): void {
    this.messages = this.ctrl.messages.slice();
    this.busy = this.ctrl.busy;
    if (notifyBusy) {
      this.onBusy(this.busy);
    }
    this.thinking = this.ctrl.thinking;
    this.error = this.ctrl.lastError;
    this.reasonLine = this.ctrl.lastReasonLine;
    this.edgeReachable = this.ctrl.edgeReachable === true;
  }

  private scrollToBottom(): void {
    // 列表用 `List` 的 `scrollToIndex` 需要 controller；这里用 `Scroller` 保持简单
    this.scroller.scrollEdge(Edge.Bottom);
  }

  private scroller: Scroller = new Scroller();

  private send(text: string): void {
    const t: string = text.trim();
    if (t.length === 0 || this.busy) {
      return;
    }
    this.refresh();
    this.ctrl.send(t).then(() => {
      this.refresh();
      this.scrollToBottom();
    }).catch((e: Error) => {
      this.error = `发送失败：${e.message}`;
      this.refresh();
    });
  }

  build() {
    Column() {
      // ── 端侧状态条（与 Android 顶栏同口径：只说明"数据不出本机"是否成立） ──
      Row() {
        Circle({ width: 6, height: 6 })
          .fill(this.edgeReachable ? Theme.edgeOk : Theme.cloud)
        Text(this.edgeReachable ? '端侧就绪 · 数据不出本机' : '端侧不可达 · 检查设置里的地址')
          .fontSize(Theme.fsMeta)
          .fontColor(Theme.textFaint)
          .margin({ left: 6 })
        Blank()
        if (this.busy) {
          Text(this.thinking.length > 0 ? '思考中…' : '生成中…')
            .fontSize(Theme.fsMeta)
            .fontColor(Theme.primary)
        }
      }
      .width('100%')
      .padding({ left: 16, right: 16, top: 6, bottom: 6 })
      .backgroundColor(Theme.card)

      // ── 消息区 / 空状态：**layoutWeight(1)**（见文件头，别改成固定高度） ──
      if (this.messages.length === 0) {
        Column() {
          Circle({ width: 56, height: 56 }).fill(Theme.primarySoft)
          Text('鸿蒙端侧助手').fontSize(Theme.fsSection).fontWeight(FontWeight.Bold)
            .fontColor(Theme.text).margin({ top: 14 })
          Text('文档与推理都在这台设备上完成，设备专属内容不会离开本机。')
            .fontSize(Theme.fsMeta).fontColor(Theme.textFaint)
            .margin({ top: 6 }).textAlign(TextAlign.Center)
          ForEach(SAMPLE_QUESTIONS, (q: string) => {
            Text(q)
              .fontSize(Theme.fsBody)
              .fontColor(Theme.text)
              .width('100%')
              .padding(12)
              .backgroundColor(Theme.card)
              .borderRadius(Theme.rCard)
              .border({ width: 1, color: Theme.border })
              .margin({ top: 8 })
              .onClick(() => {
                this.send(q);
              })
          }, (q: string) => q)
        }
        .width('100%')
        .layoutWeight(1)
        .padding(24)
        .justifyContent(FlexAlign.Center)
      } else {
        List({ space: 12, scroller: this.scroller }) {
          ForEach(this.messages, (m: ChatMsg) => {
            ListItem() {
              this.bubble(m)
            }
          }, (m: ChatMsg) => m.id)
        }
        .width('100%')
        .layoutWeight(1)
        .padding({ left: 12, right: 12, top: 12, bottom: 4 })
        .scrollBar(BarState.Auto)
      }

      // ── 原因/错误提示（**只一行**，长报告不放界面：ArkUI 渲染长文本会 SIGSEGV） ──
      if (this.error.length > 0 || this.reasonLine.length > 0) {
        Text(this.error.length > 0 ? `⚠️ ${this.error}` : this.reasonLine)
          .fontSize(Theme.fsMeta)
          .fontColor(this.error.length > 0 ? Theme.danger : Theme.textFaint)
          .maxLines(2)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
          .width('100%')
          .padding({ left: 16, right: 16, top: 4, bottom: 4 })
      }
    }
    .width('100%')
    .layoutWeight(1)
    .backgroundColor(Theme.bg)
  }

  /** 一条气泡 + 徽章 + 原因 + 来源。 */
  @Builder
  bubble(m: ChatMsg) {
    Column() {
      Row() {
        if (!m.fromUser) {
          Circle({ width: 30, height: 30 }).fill(Theme.primarySoft)
        }
        Column() {
          if (m.text.length > 0) {
            Text(m.text)
              .fontSize(Theme.fsBody)
              .fontColor(m.fromUser ? Theme.card : Theme.text)
              .padding({ left: 13, right: 13, top: 10, bottom: 10 })
              .backgroundColor(m.fromUser ? Theme.primary : Theme.card)
              .borderRadius(Theme.rBubble)
          } else if (m.streaming) {
            Text('▍')
              .fontSize(Theme.fsBody)
              .fontColor(Theme.primary)
              .padding({ left: 13, right: 13, top: 10, bottom: 10 })
              .backgroundColor(Theme.card)
              .borderRadius(Theme.rBubble)
          }
          // 执行位置徽章：产品承诺的可视化，**每条助手回答都要有**
          if (badgeOf(m).length > 0) {
            Row() {
              Circle({ width: 6, height: 6 }).fill(badgeColor(m))
              Text(badgeOf(m)).fontSize(Theme.fsMeta).fontColor(badgeColor(m)).margin({ left: 4 })
              if (m.reason.length > 0) {
                Text(`· ${explainReason(m.reason)}`)
                  .fontSize(Theme.fsMeta)
                  .fontColor(Theme.textFaint)
                  .margin({ left: 4 })
                  .layoutWeight(1)
                  .maxLines(2)
                  .textOverflow({ overflow: TextOverflow.Ellipsis })
              }
            }
            .width('100%')
            .margin({ top: 4 })
          }
          // 升级原因单独一行（与路由原因分开存，才能分别解释）
          if (m.escalateReason.length > 0) {
            Text(`升级：${explainReason(m.escalateReason)}`)
              .fontSize(Theme.fsMeta)
              .fontColor(Theme.cloud)
              .margin({ top: 2 })
              .maxLines(2)
          }
          // 错误如实显示（不静默）
          if (m.error.length > 0) {
            Text(m.error)
              .fontSize(Theme.fsMeta)
              .fontColor(Theme.danger)
              .margin({ top: 2 })
              .maxLines(3)
              .textOverflow({ overflow: TextOverflow.Ellipsis })
          }
          // 检索来源
          if (m.sources.length > 0) {
            Column() {
              Text(`检索来源（${m.sources.length}）`)
                .fontSize(Theme.fsMeta).fontColor(Theme.textFaint).margin({ bottom: 2 })
              ForEach(m.sources, (s: Source) => {
                Row() {
                  Text(`· ${s.sourceId}`)
                    .fontSize(Theme.fsMeta).fontColor(Theme.textSub)
                  Text(s.score.toFixed(3))
                    .fontSize(Theme.fsMeta).fontColor(Theme.primary).margin({ left: 6 })
                  Text(s.snippet)
                    .fontSize(Theme.fsMeta).fontColor(Theme.textFaint)
                    .layoutWeight(1).maxLines(1)
                    .textOverflow({ overflow: TextOverflow.Ellipsis })
                    .margin({ left: 6 })
                }
                .width('100%')
              }, (s: Source) => `${s.sourceId}-${s.score}`)
            }
            .width('100%')
            .padding(8)
            .backgroundColor(Theme.card)
            .borderRadius(Theme.rCard)
            .margin({ top: 4 })
          }
        }
        .alignItems(m.fromUser ? HorizontalAlign.End : HorizontalAlign.Start)
        .margin({ left: m.fromUser ? 0 : 8 })
        .layoutWeight(1)
      }
      .width('100%')
      .justifyContent(m.fromUser ? FlexAlign.End : FlexAlign.Start)

      Text(fmtTime(m.atMillis))
        .fontSize(Theme.fsMeta)
        .fontColor(Theme.textFaint)
        .margin({ top: 2 })
    }
    .width('100%')
  }
}
