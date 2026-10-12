/**
 * H8（工作流 G）· 应用外壳：**四页 + 导航**（与 Android `SekbScreen` 同名同顺序）。
 *
 * ## 刻意不用路由框架
 *
 * 四页是"一个应用的四块"，不是可深链的独立页面：不需要 URL、不需要返回栈恢复、
 * 不需要传参序列化。所以这里就是一个 `screen` 状态 + 顶栏返回 + 底部四标签——
 * 引 `Navigation`/`router` 只会多一层状态同步，而且 `@Entry` 只能有一个入口。
 *
 * ```
 *   ┌───────────────────────────────────────────────┐
 *   │ ← SEKB  聊天                    [操作区]        │  顶栏（非首页时出现 ←）
 *   ├───────────────────────────────────────────────┤
 *   │  当前页内容                                     │
 *   ├───────────────────────────────────────────────┤
 *   │  💬聊天   🕘历史   📄文档   ⚙️设置              │  底部标签
 *   └───────────────────────────────────────────────┘
 * ```
 *
 * ## 入口为什么不是 `Index.ets`
 *
 * `Index.ets` 是 **H1–H6 的自检面板**，工作流 F 本轮正在改它（协作约定：G 不编辑）。
 * 于是 `EntryAbility` 改为加载本页（`pages/AppShell`），`Index.ets` 仍注册在
 * `main_pages.json` 里、仍可从代码里 `pushUrl` 打开——**F 的自检面板一行未动**。
 * 设置页的"自检"一节等价提供同样的 PASS/FAIL/SKIP 表（H8 的硬指标）。
 */
import { common } from '@kit.AbilityKit';
import { hilog } from '@kit.PerformanceAnalysisKit';
import { AppCtx } from '../model/AppCtx';
import { ChatController } from '../model/ChatController';
import { Theme } from '../model/Theme';
import { ragDocuments } from '../model/SekbNative';
import { ChatPage } from './ChatPage';
import { ChatInputBar } from './ChatInputBar';
import { HistoryPage } from './HistoryPage';
import { DocumentsPage } from './DocumentsPage';
import { SettingsPage } from './SettingsPage';

const TAG: string = 'SEKB_HARMONY_UI';

/** 四个屏（**与 Android `SekbScreen` 同名同顺序**）。 */
const SCREENS: string[] = ['聊天', '历史', '文档', '设置'];

@Entry
@Component
struct AppShell {
  @State screen: number = 0;
  /** 会话被外部切换过（历史页点开/新建）→ 聊天页据此重新同步 */
  @State chatRev: number = 0;
  @State ready: boolean = false;
  @State docsBadge: string = '';
  /** 聊天页是否在生成（输入栏据此禁用发送；由 `ChatPage.onBusy` 推上来） */
  @State chatBusy: boolean = false;

  /**
   * 聊天页组件引用：用于把外层输入栏的文本交给它发送。
   *
   * 为什么用引用而不是把输入框的状态全放到外层：输入框的"正在输入"高频变化属于
   * 输入组件自己的状态，上提会让外层每次按键都重绘整页（含消息列表）。
   */
  private chatRef: ChatPage | null = null;

  private ctx: AppCtx | null = null;
  private controller: ChatController | null = null;

  aboutToAppear(): void {
    // 由 `aa start -A SEKB_PAGE_*` 指定的初始页（默认 0 = 聊天）；见 EntryAbility 的注释
    const initial: number | undefined = AppStorage.get<number>('sekbInitialScreen');
    if (initial !== undefined) {
      this.screen = initial;
    }
    const ability = getContext(this) as common.UIAbilityContext;
    const ctx = new AppCtx(ability);
    this.ctx = ctx;
    this.controller = new ChatController(ctx, () => {
      // 控制器在流式里反复回调：这里只需要把会话指针推给聊天页（它自己持有渲染状态）
      this.chatRev++;
    });
    this.ready = true;
    this.refreshBadge();
    hilog.info(0x0000, TAG, 'AppShell 就绪：edge=%{public}s model=%{public}s 会话存储可写=%{public}s',
      ctx.settings.edgeBaseUrl, ctx.settings.model, `${ctx.history.writable()}`);
  }

  /** 底部标签上的文档数（进文档页/跑过自检后刷新）。 */
  private refreshBadge(): void {
    const d = ragDocuments();
    this.docsBadge = d.ok ? `${d.docs.length}` : '';
  }

  private go(i: number): void {
    this.screen = i;
    if (i === 1 || i === 2) {
      this.chatRev++;
    }
    if (i === 2) {
      this.refreshBadge();
    }
  }

  build() {
    Column() {
      // ── 顶栏：非首页时出现返回箭头（Android 同形态） ──
      Row() {
        if (this.screen > 0) {
          Text('←')
            .fontSize(22)
            .fontColor(Theme.text)
            .width(36)
            .textAlign(TextAlign.Center)
            .onClick(() => {
              this.go(0);
            })
        }
        Column() {
          Text(this.screen === 0 ? 'SEKB' : SCREENS[this.screen])
            .fontSize(Theme.fsTitle)
            .fontWeight(FontWeight.Bold)
            .fontColor(Theme.text)
          if (this.screen === 0) {
            Text('端侧助手 · 数据不出本机')
              .fontSize(Theme.fsMeta)
              .fontColor(Theme.textFaint)
              .margin({ top: 2 })
          } else if (this.screen === 1) {
            Text('本机会话记录（JSON 落盘）')
              .fontSize(Theme.fsMeta).fontColor(Theme.textFaint).margin({ top: 2 })
          } else if (this.screen === 2) {
            Text('导入本机文档并建立端侧检索索引')
              .fontSize(Theme.fsMeta).fontColor(Theme.textFaint).margin({ top: 2 })
          } else {
            Text('设备身份 / 阈值 / 嵌入 / 网络 / 自检 / 版本')
              .fontSize(Theme.fsMeta).fontColor(Theme.textFaint).margin({ top: 2 })
          }
        }
        .alignItems(HorizontalAlign.Start)
        .layoutWeight(1)

        if (this.screen > 0) {
          Text(`第 ${this.screen + 1}/4 页`)
            .fontSize(Theme.fsMeta)
            .fontColor(Theme.textFaint)
        }
      }
      .width('100%')
      .padding({ left: 12, right: 16, top: 10, bottom: 10 })
      .backgroundColor(Theme.card)

      // ── 页面（四选一） ──
      if (this.ready && this.ctx !== null && this.controller !== null) {
        if (this.screen === 0) {
          ChatPage({
            ctrl: this.controller,
            rev: this.chatRev,
            onBusy: (b: boolean) => {
              this.chatBusy = b;
            },
            ref: (r: ChatPage) => {
              this.chatRef = r;
            },
          })
        } else if (this.screen === 1) {
          HistoryPage({
            ctrl: this.controller,
            onOpened: () => {
              this.chatRev++;
              this.screen = 0;
            },
          })
        } else if (this.screen === 2) {
          DocumentsPage({
            ctx: this.ctx,
            onRagState: (ready: boolean, note: string) => {
              this.controller?.markRag(ready, note);
              this.refreshBadge();
            },
          })
        } else {
          SettingsPage({
            ctx: this.ctx,
            ctrl: this.controller,
          })
        }
      } else {
        Column() {
          Text('初始化中…').fontSize(Theme.fsBody).fontColor(Theme.textSub)
        }
        .width('100%')
        .layoutWeight(1)
        .justifyContent(FlexAlign.Center)
      }

      // ── 聊天页的输入栏（**在外层**，见 ChatPage 里"输入栏为什么在外层"的注释） ──
      if (this.ready && this.screen === 0) {
        ChatInputBar({
          placeholder: '问点什么…',
          busy: this.chatBusy,
          onSend: (t: string) => {
            this.chatRef?.sendInput(t);
          },
        })
      }

      // ── 底部四标签（与 Android `SekbScreen` 同序） ──
      // `padding.bottom: 12` 是**看图之后加的**：模拟器上应用窗口是整屏
      // （`hidumper WindowManagerService`：spike 窗口 1320×2856 全屏，手势条 98px 悬浮在底部），
      // 不留余量时输入框会被手势条压掉一截。代价是底部多 12 的留白，换来输入栏完整可见。
      Row() {
        ForEach(SCREENS, (name: string, index: number) => {
          Column() {
            Text(this.tabIcon(index))
              .fontSize(18)
              .fontColor(this.screen === index ? Theme.primary : Theme.textFaint)
            Text(index === 2 && this.docsBadge.length > 0 ? `${name}·${this.docsBadge}` : name)
              .fontSize(Theme.fsMeta)
              .fontColor(this.screen === index ? Theme.primary : Theme.textFaint)
              .margin({ top: 2 })
          }
          .layoutWeight(1)
          .height(50)
          .justifyContent(FlexAlign.Center)
          .onClick(() => {
            this.go(index);
          })
        }, (name: string) => name)
        // 手势导航条留白（模拟器上它悬浮在窗口底部，98px）：**看图才发现**
        // 输入栏被它压掉一截（只剩上沿）。显式占位块，`padding` 在这条链路上没生效。
        Blank().height(24)
      }
      .width('100%')
      .backgroundColor(Theme.card)
      .border({ width: { top: 1 }, color: Theme.border })
    }
    .width('100%')
    .height('100%')
    .backgroundColor(Theme.bg)
  }

  /**
   * 标签图标：用**文字符号**而不是图片资源。
   *
   * 理由：仓库里没有为这四个页面准备图标素材，临时造 PNG 会引入"看起来像设计稿、
   * 其实随手画的"东西；文字符号在模拟器上的可读性与截图可辨识度都够，
   * 且不增加包体与资源审查面。将来有正式素材再换（一行）。
   */
  private tabIcon(i: number): string {
    if (i === 0) {
      return '💬';
    }
    if (i === 1) {
      return '🕘';
    }
    if (i === 2) {
      return '📄';
    }
    return '⚙️';
  }
}
