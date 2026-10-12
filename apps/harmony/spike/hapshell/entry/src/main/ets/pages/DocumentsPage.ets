/**
 * H8（工作流 G）· 文档页（导入 + 列表 + 单篇删除）。
 *
 * ## 走的就是 `ragSelfTest` 那条链路（**没有第二套检索逻辑**）
 *
 * ```
 *   读取文本（ArkTS 读盘，只读字节）
 *        │  ragIngest(id, name, text, bytes)
 *        ▼
 *   共享层 KnowledgeIndex.ingest（分块 → 嵌入 → 向量入库 → 文档登记）
 *        │  底层 = RdbVectorStore（工作流 F 的 RDB NDK 实现）
 *        ▼
 *   ragDocuments()  → 列表（名称/片段数/体积）
 *   ragRemove(id)   → KnowledgeIndex.remove → VectorStore.deleteBySource
 * ```
 *
 * ## 单篇删除**不误清全库**（Android 真出过这个缺陷）
 *
 * 早期 `KnowledgeIndex.remove()` 用"清空整库"糊过去（接口里当时没有 `deleteBySource`），
 * 结果删一篇文档会把整个索引清掉——**其余文档的正文是设备侧唯一副本，等于数据丢失**。
 * 共享层现已修好，本页只调它，并且删除后**回读列表核对剩余条数**（页面显示的
 * "剩余 N 篇 / M 段"就是这条核对的结果），不是删完就完事。
 *
 * ## 未就绪时如实降级
 *
 * 若 F 的 RDB 链路不可用，`ragOpen` 返回 `ok=false` + `reason`：
 * 本页顶部显示"文档未持久化 + 原因"，导入按钮禁用（**不假装能导**）。
 */
import { common } from '@kit.AbilityKit';
import { AppCtx } from '../model/AppCtx';
import { DocInfo, fmtBytes, fmtTime } from '../model/SekbTypes';
import { Theme } from '../model/Theme';
import {
  bytesOf, ensureImportDir, listImportables, ragDocuments, ragIngest, ragRemove, readTextFile, writeTextFile,
} from '../model/SekbNative';

/** 一键写入的示例文档（让"没有导入文件"的设备也能看到真能力，而不是空页）。 */
const SAMPLE_DOC: string = `端侧知识库导入说明

本文件由「文档」页的「写入示例文档」按钮生成，用于验证本机检索链路：
导入 → 分块 → 端侧嵌入（MindSpore Lite + bge-small）→ RDB 向量库 → 检索并给出处。

端侧 RAG 把知识索引放在设备上，检索不出网，因此隐私更好、延迟更低。
端侧推理省电的原因：计算留在本机，没有网络传输，也没有云端排队等待。
删除一份文档时只应删除它自己的切片，其余文档的正文是设备侧唯一副本，不能误清。`;

@Component
export struct DocumentsPage {
  @Require ctx: AppCtx;
  /** 文档库可用性变化时通知外层（聊天页的"检索来源"依赖它） */
  onRagState: (ready: boolean, note: string) => void = () => {};
  @State docs: DocInfo[] = [];
  @State chunks: number = 0;
  @State storageNote: string = '';
  @State ready: boolean = false;
  @State busy: boolean = false;
  @State notice: string = '';
  @State error: string = '';
  @State candidates: string[] = [];
  @State importDir: string = '';

  aboutToAppear(): void {
    this.ensureReady();
  }

  /** 打开共享层文档库（幂等；失败把原因显示出来，不抛）。 */
  async ensureReady(): Promise<void> {
    this.busy = true;
    const ctx = getContext(this) as common.UIAbilityContext;
    const reason: string = await this.ctx.openRag(ctx);
    this.ready = reason.length === 0;
    this.storageNote = reason;
    this.importDir = ensureImportDir(this.ctx.filesDir);
    this.candidates = listImportables(this.importDir);
    this.reload();
    this.busy = false;
    this.onRagState(this.ready, this.storageNote);
  }

  private reload(): void {
    const r = ragDocuments();
    if (!r.ok) {
      this.ready = false;
      this.storageNote = r.reason;
      this.docs = [];
      this.chunks = 0;
      return;
    }
    this.docs = r.docs;
    this.chunks = r.chunks;
  }

  /** 导入一个文件（异步读盘，导入本身在 Kotlin 侧同步完成）。 */
  async importFile(name: string): Promise<void> {
    if (!this.ready || this.busy) {
      return;
    }
    this.busy = true;
    this.notice = '';
    this.error = '';
    const path: string = `${this.importDir}/${name}`;
    const text: string = readTextFile(path);
    const size: number = bytesOf(path);
    if (text.length === 0) {
      this.error = `「${name}」读不到内容（空文件或非 UTF-8 文本）；PDF 尚未支持`;
      this.busy = false;
      return;
    }
    const r = ragIngest(name, name, text, size);
    if (!r.ok) {
      this.error = `导入失败：${r.reason}`;
    } else {
      this.notice = r.note.length > 0 ? r.note : `已导入「${name}」：${r.chunks} 段`;
      if (r.zeroFilled > 0) {
        this.error = `⚠️ 有 ${r.zeroFilled} 段嵌入失败并被补成零向量（检索不到，需查模型）`;
      }
    }
    this.reload();
    this.onRagState(this.ready, this.storageNote);
    this.busy = false;
  }

  /** 单篇删除 + **回读核对**（见文件头：不误清全库）。 */
  remove(id: string): void {
    if (this.busy) {
      return;
    }
    const before: number = this.docs.length;
    const r = ragRemove(id);
    if (!r.ok) {
      this.error = `删除失败：${r.reason}`;
      return;
    }
    this.reload();
    const after: number = this.docs.length;
    if (before > 0 && after === 0 && before > 1) {
      // 这是"清库"缺陷的现场特征：必须大声报，不能静默
      this.error = `⚠️ 删一篇却清空了全库（删前 ${before} 篇 → 删后 0 篇）：共享层 deleteBySource 退化`;
    } else {
      this.notice = `已删除「${id}」：${r.removed} 段；剩余 ${after} 篇 / ${r.remaining} 段`;
    }
    this.onRagState(this.ready, this.storageNote);
  }

  private writeSample(): void {
    ensureImportDir(this.ctx.filesDir);
    const path: string = `${this.importDir}/端侧知识库导入说明.md`;
    const fresh: boolean = writeTextFile(path, SAMPLE_DOC);
    this.candidates = listImportables(this.importDir);
    this.notice = fresh ? '已写入示例文档，点它即可导入' : '示例文档已存在，直接点它导入';
  }

  build() {
    Column() {
      // ── 就绪状态（**如实**：不可用就把原因写出来） ──
      Row() {
        Circle({ width: 6, height: 6 }).fill(this.ready ? Theme.edgeOk : Theme.cloud)
        Text(this.ready
          ? `文档库已就绪（RDB 持久化）· 共 ${this.docs.length} 篇 / ${this.chunks} 段`
          : `文档未持久化：${this.storageNote}`)
          .fontSize(Theme.fsMeta)
          .fontColor(this.ready ? Theme.textFaint : Theme.danger)
          .margin({ left: 6 })
          .layoutWeight(1)
          .maxLines(2)
      }
      .width('100%')
      .padding({ left: 16, right: 16, top: 8, bottom: 8 })
      .backgroundColor(Theme.card)

      if (this.notice.length > 0) {
        Text(this.notice).fontSize(Theme.fsMeta).fontColor(Theme.edgeOk)
          .width('100%').padding({ left: 16, right: 16, top: 4 }).maxLines(2)
      }
      if (this.error.length > 0) {
        Text(this.error).fontSize(Theme.fsMeta).fontColor(Theme.danger)
          .width('100%').padding({ left: 16, right: 16, top: 4 }).maxLines(3)
      }

      // ── 待导入文件 ──
      Row() {
        Text(`待导入（${this.candidates.length}）`).fontSize(Theme.fsBody).fontWeight(FontWeight.Bold)
          .fontColor(Theme.text).layoutWeight(1)
        Button('写入示例文档')
          .height(30).fontSize(Theme.fsMeta).borderRadius(Theme.rPill)
          .backgroundColor(Theme.primarySoft).fontColor(Theme.primary)
          .onClick(() => {
            this.writeSample();
          })
      }
      .width('100%').padding({ left: 16, right: 16, top: 10, bottom: 6 })

      if (this.candidates.length === 0) {
        Text(`把 .md / .txt 放进应用目录的 import/ 下（${this.importDir}），或点上面的按钮写入示例`)
          .fontSize(Theme.fsMeta).fontColor(Theme.textFaint).width('100%')
          .padding({ left: 16, right: 16, bottom: 6 }).maxLines(2)
      } else {
        Scroll() {
          Column() {
            ForEach(this.candidates, (name: string) => {
              Row() {
                Text(name).fontSize(Theme.fsBody).fontColor(Theme.text)
                  .layoutWeight(1).maxLines(1).textOverflow({ overflow: TextOverflow.Ellipsis })
                Button('导入')
                  .height(28).fontSize(Theme.fsMeta).borderRadius(Theme.rPill)
                  .backgroundColor(Theme.primary).enabled(this.ready && !this.busy)
                  .onClick(() => {
                    this.importFile(name);
                  })
              }
              .width('100%').padding(10).backgroundColor(Theme.card)
              .borderRadius(Theme.rCard).margin({ bottom: 6 })
            }, (name: string) => name)
          }
        }
        .constraintSize({ maxHeight: 170 })
        .width('100%').padding({ left: 12, right: 12 })
      }

      // ── 已导入列表 ──
      Text(`已导入（${this.docs.length} 篇）`).fontSize(Theme.fsBody).fontWeight(FontWeight.Bold)
        .fontColor(Theme.text).width('100%')
        .padding({ left: 16, right: 16, top: 8, bottom: 6 })

      if (this.docs.length === 0) {
        Column() {
          Text('还没有导入任何文档').fontSize(Theme.fsBody).fontColor(Theme.textSub)
          Text('导入后，聊天页的回答下方会给出「检索来源」').fontSize(Theme.fsMeta)
            .fontColor(Theme.textFaint).margin({ top: 6 })
        }
        .width('100%').layoutWeight(1).justifyContent(FlexAlign.Center)
      } else {
        List({ space: 8 }) {
          ForEach(this.docs, (d: DocInfo) => {
            ListItem() {
              Row() {
                Column() {
                  Text(d.name).fontSize(Theme.fsBody).fontColor(Theme.text)
                    .maxLines(1).textOverflow({ overflow: TextOverflow.Ellipsis })
                  Text(`${d.chunks} 段 · ${fmtBytes(d.bytes)} · ${fmtTime(d.addedAt)}`)
                    .fontSize(Theme.fsMeta).fontColor(Theme.textFaint).margin({ top: 2 })
                }
                .alignItems(HorizontalAlign.Start).layoutWeight(1)
                Button('删除')
                  .height(28).fontSize(Theme.fsMeta).fontColor(Theme.danger)
                  .backgroundColor(Theme.dangerSoft).borderRadius(Theme.rPill)
                  .onClick(() => {
                    this.remove(d.id);
                  })
              }
              .width('100%').padding(12).backgroundColor(Theme.card).borderRadius(Theme.rCard)
            }
          }, (d: DocInfo) => `${d.id}-${d.chunks}`)
        }
        .width('100%').layoutWeight(1).padding({ left: 12, right: 12, bottom: 12 })
      }
    }
    .width('100%').height('100%').backgroundColor(Theme.bg)
  }
}
