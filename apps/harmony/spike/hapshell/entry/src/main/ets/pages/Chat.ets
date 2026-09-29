/**
 * SEKB 端侧助手 · 鸿蒙 UI（M7 骨架）
 *
 * ## 定位与边界（务必先读）
 *
 * 这是 **M7（ArkUI UI + NAPI 桥）的 UI 部分**，当前**只做界面**：
 * 消息与回答都是本页的本地状态，**尚未接 NAPI**（真正的推理链路等 M6 第 4 条在模拟器/设备上
 * 跑通后再接，避免把未验证的运行时假设写进 UI）。
 *
 * 这样做**不违反 RFC 的 M6→M7 顺序**：界面与"用哪个推理运行时"无关
 * （`apps/shared` 的 `embed/` 只暴露"给文本返回定长向量"的契约），
 * 所以先把 UI 写出来不会因为运行时选型变化而白做。
 *
 * ## 验证级别（不许含糊）
 *
 * 目前只到 **ArkTS 编译通过 + HAP 打包成功**（`bash scripts/harmony_spike.sh hap`）。
 * **没有**在预览器/模拟器/设备上看过一眼——因此**布局与交互都还没有视觉验证**。
 * 这正是 owner 把验收环境改为"模拟器 / Preview"之后要补的那一步。
 *
 * ## 设计对齐
 *
 * 视觉语言与网页端（Ant Design）和 Android 端保持一致：
 * 主色 `#1677FF`、浅灰底、白卡片、执行位置用语义色（本机=绿、云端=金）。
 */

/** 一条消息。`plane` 为空表示还没结果（流式中）。 */
interface ChatMessage {
  id: string;
  fromUser: boolean;
  text: string;
  /** 'edge' | 'cloud' | '' —— 执行位置徽章的数据来源 */
  plane: string;
}

@Entry
@Component
struct Chat {
  @State messages: ChatMessage[] = [];
  @State input: string = '';
  /** 端侧是否就绪（真实实现应由 PolicyFetcher / 端点探针驱动） */
  @State edgeReady: boolean = true;
  private seq: number = 0;

  /** 空状态示例问题：与 Android 端一致，都是这台设备真的答得了的 */
  private readonly samples: string[] = [
    '端侧推理为什么更省电？',
    '我本机的知识库里有什么？',
    '现在几点了？',
  ];

  private badgeOf(m: ChatMessage): string {
    if (m.fromUser) return '';
    if (m.plane === 'edge') return '本机完成';
    if (m.plane === 'cloud') return '云端完成';
    return '';
  }

  private badgeColor(m: ChatMessage): string {
    return m.plane === 'edge' ? '#389E0D' : '#D48806';
  }

  private send(text: string): void {
    const t = text.trim();
    if (t.length === 0) return;
    this.seq++;
    const uid = 'u' + this.seq.toString();
    this.messages.push({ id: uid, fromUser: true, text: t, plane: '' });
    // ⚠️ 这里是**占位回答**，不是推理结果：NAPI 接线后应替换为真实流式回调。
    // 现在写成占位是为了让"界面上能看到一条回答长什么样"，而不是假装已经跑通。
    this.seq++;
    this.messages.push({
      id: 'a' + this.seq.toString(),
      fromUser: false,
      text: '（界面骨架：此处将显示端侧模型的回答。推理链路待 M6 第 4 条在模拟器上验证后接入。）',
      plane: 'edge',
    });
    this.input = '';
  }

  build() {
    Column() {
      // ── 顶栏 ──────────────────────────────────────────────
      Row() {
        Column() {
          Text('SEKB').fontSize(20).fontWeight(FontWeight.Bold).fontColor('#1F2329')
          Row() {
            Circle({ width: 6, height: 6 })
              .fill(this.edgeReady ? '#389E0D' : '#D48806')
            Text(this.edgeReady ? '端侧就绪 · 数据不出本机' : '端侧不可用 · 将使用云端')
              .fontSize(11).fontColor('#8C8C8C').margin({ left: 6 })
          }.margin({ top: 2 })
        }.alignItems(HorizontalAlign.Start).layoutWeight(1)
      }
      .width('100%').padding({ left: 16, right: 16, top: 8, bottom: 8 })
      .backgroundColor('#FFFFFF')

      // ── 消息区 / 空状态 ────────────────────────────────────
      if (this.messages.length === 0) {
        Column() {
          Circle({ width: 56, height: 56 }).fill('#E6F0FF')
          Text('鸿蒙端侧助手').fontSize(16).fontWeight(FontWeight.Bold).margin({ top: 14 })
          Text('文档与推理都在这台设备上完成，设备专属内容不会离开本机。')
            .fontSize(12).fontColor('#8C8C8C').margin({ top: 6 }).textAlign(TextAlign.Center)
          ForEach(this.samples, (q: string) => {
            Text(q)
              .fontSize(14).width('100%').padding(12)
              .backgroundColor('#FFFFFF').borderRadius(10)
              .border({ width: 1, color: '#E5E7EB' })
              .margin({ top: 8 })
              .onClick(() => { this.send(q) })
          }, (q: string) => q)
        }
        .width('100%').layoutWeight(1).padding(24).justifyContent(FlexAlign.Center)
      } else {
        List({ space: 12 }) {
          ForEach(this.messages, (m: ChatMessage) => {
            ListItem() {
              Column() {
                Row() {
                  if (!m.fromUser) {
                    Circle({ width: 30, height: 30 }).fill('#E6F0FF')
                  }
                  Text(m.text)
                    .fontSize(14).padding({ left: 13, right: 13, top: 10, bottom: 10 })
                    .backgroundColor(m.fromUser ? '#1677FF' : '#FFFFFF')
                    .fontColor(m.fromUser ? '#FFFFFF' : '#1F2329')
                    .borderRadius(14)
                    .margin({ left: m.fromUser ? 0 : 8 })
                }
                .width('100%')
                .justifyContent(m.fromUser ? FlexAlign.End : FlexAlign.Start)

                // 执行位置徽章：产品承诺的可视化，必须每条回答都标
                if (this.badgeOf(m).length > 0) {
                  Row() {
                    Circle({ width: 6, height: 6 }).fill(this.badgeColor(m))
                    Text(this.badgeOf(m)).fontSize(11).fontColor(this.badgeColor(m)).margin({ left: 4 })
                  }.margin({ top: 4, left: 38 })
                }
              }.width('100%')
            }
          }, (m: ChatMessage) => m.id)
        }
        .width('100%').layoutWeight(1).padding({ left: 12, right: 12, top: 12 })
      }

      // ── 输入栏 ────────────────────────────────────────────
      Row() {
        TextInput({ text: this.input, placeholder: '问点什么…' })
          .layoutWeight(1).height(44).borderRadius(22).backgroundColor('#F0F2F5')
          .onChange((v: string) => { this.input = v })
          .onSubmit(() => { this.send(this.input) })
        Button('发送')
          .height(40).borderRadius(20).backgroundColor('#1677FF')
          .enabled(this.input.length > 0)
          .margin({ left: 8 })
          .onClick(() => { this.send(this.input) })
      }
      .width('100%').padding({ left: 10, right: 10, top: 8, bottom: 8 })
      .backgroundColor('#FFFFFF')
    }
    .width('100%').height('100%').backgroundColor('#F5F7FA')
  }
}
