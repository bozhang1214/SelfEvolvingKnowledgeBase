/**
 * H8（工作流 G）· 设置页的**卡片容器**（一个自定义组件，带 `@BuilderParam` 内容槽）。
 *
 * ## 为什么不是 `@Builder card(title, content: () => void)`
 *
 * 实测：ArkTS 编译器报 `'content();' does not meet UI component syntax`——
 * **builder 不能以普通函数参数的形式传入**（参数化构建只能走 `@BuilderParam` 槽）。
 * 所以把卡片做成组件：`SectionCard({ title: '…' }) { …内容… }` 尾随闭包进 `@BuilderParam`。
 *
 * 这样颜色的唯一来源仍是 `Theme`（页面里不出现 `'#RRGGBB'`），
 * 卡片外观也只有一份实现——后者是"改一处、处处一致"的前提。
 */
import { Theme } from '../model/Theme';

@Component
export struct SectionCard {
  @Require title: string;
  @BuilderParam content: () => void;

  build() {
    Column() {
      Text(this.title)
        .fontSize(16)
        .fontWeight(FontWeight.Bold)
        .fontColor(Theme.text)
        .width('100%')
      Column() {
        this.content();
      }
      .alignItems(HorizontalAlign.Start)
      .width('100%')
      .margin({ top: 8 })
    }
    .width('100%')
    .padding(14)
    .backgroundColor(Theme.card)
    .borderRadius(Theme.rCard)
    .margin({ left: 12, right: 12, top: 10 })
    .alignItems(HorizontalAlign.Start)
  }
}
