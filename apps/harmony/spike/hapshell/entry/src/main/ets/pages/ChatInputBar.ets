/**
 * H8（工作流 G）· 聊天输入栏（**独立组件**，由 `AppShell` 放在根 Column 的底部）。
 *
 * ## 为什么它不是 `ChatPage` 的一部分
 *
 * 看图才发现的事实：模拟器上窗口是**整屏**（1320×2856）+ 悬浮手势条，而
 * "内层 Column（`layoutWeight`）+ 固定高度输入栏"这种两层嵌套的底部排布会被裁掉——
 * 输入栏只剩上沿、标签栏完全看不见。把输入栏与标签栏都提到**外层根 Column** 之后位置正确
 * （少一层权重嵌套）。代价是输入栏与聊天页的对话要通过回调/引用传递，见 `AppShell`。
 *
 * ## 空状态**没有**把输入栏挤出屏幕
 *
 * Android 真机上出过这个 bug。本端的两条防线：① 输入栏在外层，`ChatPage` 无论空不空
 * 都只占中间那块（`layoutWeight(1)`）；② 空状态自身不设 `height('100%')`。
 * 这两条都在截图里核过（`docs/screenshots/ui-chat-empty.jpeg`）。
 */
import { Theme } from '../model/Theme';

@Component
export struct ChatInputBar {
  @Prop placeholder: string = '问点什么…';
  /** 生成中 → 禁用发送（避免并发两轮把流式状态搅在一起） */
  @Prop busy: boolean = false;
  onSend: (text: string) => void = () => {};
  @State input: string = '';

  build() {
    Row() {
      TextInput({ text: this.input, placeholder: this.placeholder })
        .layoutWeight(1)
        .height(44)
        .borderRadius(Theme.rPill)
        .backgroundColor(Theme.inputBg)
        .onChange((v: string) => {
          this.input = v;
        })
        .onSubmit(() => {
          this.submit();
        })
      Button('发送')
        .height(40)
        .borderRadius(Theme.rPill)
        .backgroundColor(Theme.primary)
        .enabled(this.input.length > 0 && !this.busy)
        .margin({ left: 8 })
        .onClick(() => {
          this.submit();
        })
    }
    .width('100%')
    .padding({ left: 10, right: 10, top: 8, bottom: 8 })
    .backgroundColor(Theme.card)
  }

  private submit(): void {
    const t: string = this.input.trim();
    if (t.length === 0 || this.busy) {
      return;
    }
    this.input = '';
    this.onSend(t);
  }
}
