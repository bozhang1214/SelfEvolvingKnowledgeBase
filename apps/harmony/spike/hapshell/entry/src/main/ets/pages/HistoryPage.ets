/**
 * H8（工作流 G）· 历史页（会话列表 + 切换 + 删除）。
 *
 * ## 这是真能力，不是桩
 *
 * 会话由 `HistoryStore`（JSON 文件，原子替换写入）**真的落盘**：
 * 重启应用后仍能打开上一次的对话（含执行位置徽章与检索来源）。
 * 之所以不放在 RDB 里：会话是 UI 状态、不需要向量检索，RDB 链路（工作流 F）
 * 挂了也不该把聊天记录一起带走——归属不同，见 `HistoryStore` 文件头。
 *
 * 若存储真的不可写（极少数沙箱场景），页面**如实显示"未持久化"**，
 * 而不是让用户以为记录在。
 */
import { ChatController } from '../model/ChatController';
import { Conversation, fmtTime } from '../model/SekbTypes';
import { Theme } from '../model/Theme';

@Component
export struct HistoryPage {
  @Require ctrl: ChatController;
  /** 打开某段会话后要通知外层切回聊天页 */
  onOpened: () => void = () => {};
  @State items: Conversation[] = [];
  @State currentId: string = '';
  @State storeOk: boolean = true;

  aboutToAppear(): void {
    this.refresh();
  }

  private refresh(): void {
    this.items = this.ctrl.conversationList();
    this.currentId = this.ctrl.conversationId;
    this.storeOk = this.ctrl.storageWritable();
  }

  private open(id: string): void {
    this.ctrl.openConversation(id);
    this.onOpened();
  }

  private remove(id: string): void {
    this.ctrl.deleteConversation(id);
    this.refresh();
  }

  build() {
    Column() {
      if (!this.storeOk) {
        Text('⚠️ 会话未持久化：应用存储不可写，重启后记录会丢失')
          .fontSize(Theme.fsMeta)
          .fontColor(Theme.danger)
          .width('100%')
          .padding({ left: 16, right: 16, top: 6, bottom: 6 })
      }

      Row() {
        Text(`本机会话（${this.items.length}）`)
          .fontSize(Theme.fsSection)
          .fontWeight(FontWeight.Bold)
          .fontColor(Theme.text)
          .layoutWeight(1)
        Button('新对话')
          .height(32)
          .fontSize(Theme.fsMeta)
          .borderRadius(Theme.rPill)
          .backgroundColor(Theme.primary)
          .onClick(() => {
            this.ctrl.newConversation();
            this.refresh();
            this.onOpened();
          })
      }
      .width('100%')
      .padding({ left: 16, right: 16, top: 10, bottom: 10 })

      if (this.items.length === 0) {
        Column() {
          Text('还没有会话').fontSize(Theme.fsBody).fontColor(Theme.textSub)
          Text('回到「聊天」页问一句，这里就会出现记录').fontSize(Theme.fsMeta)
            .fontColor(Theme.textFaint).margin({ top: 6 })
        }
        .width('100%')
        .layoutWeight(1)
        .justifyContent(FlexAlign.Center)
      } else {
        List({ space: 8 }) {
          ForEach(this.items, (c: Conversation) => {
            ListItem() {
              Row() {
                Column() {
                  Row() {
                    Text(c.title.length > 0 ? c.title : '新对话')
                      .fontSize(Theme.fsBody)
                      .fontColor(Theme.text)
                      .fontWeight(FontWeight.Medium)
                      .layoutWeight(1)
                      .maxLines(1)
                      .textOverflow({ overflow: TextOverflow.Ellipsis })
                    if (c.id === this.currentId) {
                      Text('当前')
                        .fontSize(Theme.fsMeta)
                        .fontColor(Theme.primary)
                        .padding({ left: 6, right: 6, top: 1, bottom: 1 })
                        .backgroundColor(Theme.primarySoft)
                        .borderRadius(Theme.rPill)
                    }
                  }
                  .width('100%')
                  Text(`${c.messages.length} 条 · ${fmtTime(c.updatedAt)}`)
                    .fontSize(Theme.fsMeta)
                    .fontColor(Theme.textFaint)
                    .margin({ top: 2 })
                }
                .alignItems(HorizontalAlign.Start)
                .layoutWeight(1)

                Button('删除')
                  .height(28)
                  .fontSize(Theme.fsMeta)
                  .fontColor(Theme.danger)
                  .backgroundColor(Theme.dangerSoft)
                  .borderRadius(Theme.rPill)
                  .onClick(() => {
                    this.remove(c.id);
                  })
              }
              .width('100%')
              .padding(12)
              .backgroundColor(Theme.card)
              .borderRadius(Theme.rCard)
              .onClick(() => {
                this.open(c.id);
              })
            }
          }, (c: Conversation) => `${c.id}-${c.updatedAt}`)
        }
        .width('100%')
        .layoutWeight(1)
        .padding({ left: 12, right: 12, bottom: 12 })
      }
    }
    .width('100%')
    .height('100%')
    .backgroundColor(Theme.bg)
  }
}
