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

import { ChatMessage, Plane, badgeOf, badgeColor, explainReason } from '../model/Types';
import { currentAgent, AgentReply } from '../model/EdgeAgent';

@Entry
@Component
struct Chat {
  @State messages: ChatMessage[] = [];
  @State input: string = '';
  /** 端侧是否就绪（真实实现应由 PolicyFetcher / 端点探针驱动） */
  @State edgeReady: boolean = true;
  @State busy: boolean = false;
  private seq: number = 0;

  /** 空状态示例问题：与 Android 端一致，都是这台设备真的答得了的 */
  private readonly samples: string[] = [
    '端侧推理为什么更省电？',
    '我本机的知识库里有什么？',
    '现在几点了？',
  ];

  private push(fromUser: boolean, text: string, plane: Plane = '',
               escalated: number = 0, reason: string = '', escalateReason: string = ''): void {
    this.seq++;
    this.messages.push({
      id: (fromUser ? 'u' : 'a') + this.seq.toString(),
      fromUser: fromUser, text: text, plane: plane,
      escalated: escalated, reason: reason, escalateReason: escalateReason,
    });
  }

  private send(text: string): void {
    const t = text.trim();
    if (t.length === 0) return;
    this.push(true, t);
    this.input = '';
    this.busy = true;

    // 走契约接口；**未接线时桩会明确抛错**，这里把错误如实显示成一条回答——
    // 而不是伪造一条"看起来像回答"的文本（那会让人以为链路通了）。
    currentAgent().ask(t).then((r: AgentReply) => {
      this.busy = false;
      this.push(false, r.text, r.plane, r.escalated, r.reason, r.escalateReason);
    }).catch((e: Error) => {
      this.busy = false;
      this.push(false, '⚠️ ' + e.message, '', 0, 'edge_unavailable', '');
    });
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
                if (badgeOf(m).length > 0) {
                  Row() {
                    Circle({ width: 6, height: 6 }).fill(badgeColor(m))
                    Text(badgeOf(m)).fontSize(11).fontColor(badgeColor(m)).margin({ left: 4 })
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
