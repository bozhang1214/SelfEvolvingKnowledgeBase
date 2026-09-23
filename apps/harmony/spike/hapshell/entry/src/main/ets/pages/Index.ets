// M6 spike 的界面：把 NAPI 调用的结果**显示出来**。
//
// 三组结果，正好回答真机日的三个问题：
//   1. `ping`        —— ArkTS → NAPI → libknspike.so → libkn.so → Kotlin 这条链路通不通；
//   2. `msSelfTest`  —— `.ms` 模型能不能**加载并跑出向量**（>0 = 输出元素数，<0 = 失败步骤码）；
//   3. `msFirstFloat`—— 输出首元素的粗量化值，用来证明**数据真的流过来了**（全零也能过第 2 项）。
//
// `.ms` 通过 rawfile 随包分发，首次启动复制到应用私有目录——`OH_AI_ModelBuildFromFile` 要的是
// **文件系统路径**，而 rawfile 不是路径。这样真机日不需要手工 push，也不会踩路径权限的坑。
import knspike from 'libknspike.so';
import { hilog } from '@kit.PerformanceAnalysisKit';
import { fileIo } from '@kit.CoreFileKit';
import { common } from '@kit.AbilityKit';

const TAG: string = 'SEKB_KN_SPIKE';
const MODEL_RAWFILE: string = 'bge-small-fixed512.ms';

@Entry
@Component
struct Index {
  @State ping: string = '（未调用）';
  @State echoLen: string = '（未调用）';
  @State modelPath: string = '（准备中）';
  @State msResult: string = '（未调用）';
  @State msFirstFloat: string = '（未调用）';

  aboutToAppear(): void {
    this.runKn();
    this.runModel();
  }

  /** 第 1 组：NAPI ↔ KMP 链路。 */
  runKn(): void {
    try {
      const p: number = knspike.ping();
      this.ping = `${p}`;
      const text: string = '端侧知识库';
      this.echoLen = `"${text}" → ${knspike.echoLen(text)}`;
      hilog.info(0x0000, TAG, 'NAPI->KMP ok: ping=%{public}d', p);
    } catch (e) {
      this.ping = `❌ ${e}`;
      hilog.error(0x0000, TAG, 'NAPI->KMP failed: %{public}s', JSON.stringify(e));
    }
  }

  /** 第 2/3 组：MindSpore Lite 加载 `.ms` 并推理。 */
  async runModel(): Promise<void> {
    try {
      const ctx = getContext(this) as common.UIAbilityContext;
      const path: string = await this.ensureModelFile(ctx);
      this.modelPath = path;
      const n: number = knspike.msSelfTest(path);
      this.msResult = n > 0 ? `✅ 输出元素数=${n}` : `❌ 失败步骤码=${n}`;
      const f: number = n > 0 ? knspike.msFirstFloat(path) : 0;
      this.msFirstFloat = n > 0 ? `|v[0]|×1e6 = ${f}` : '（未跑）';
      hilog.info(0x0000, TAG, 'mindspore selftest elemNum=%{public}d firstFloat=%{public}d', n, f);
    } catch (e) {
      this.msResult = `❌ ${e}`;
      hilog.error(0x0000, TAG, 'mindspore failed: %{public}s', JSON.stringify(e));
    }
  }

  /** rawfile → 应用私有目录（只做一次）。返回真实文件路径。 */
  async ensureModelFile(ctx: common.UIAbilityContext): Promise<string> {
    const dest: string = `${ctx.filesDir}/${MODEL_RAWFILE}`;
    if (fileIo.accessSync(dest)) {
      return dest;
    }
    const data: Uint8Array = await ctx.resourceManager.getRawFileContent(MODEL_RAWFILE);
    const f = fileIo.openSync(dest, fileIo.OpenMode.CREATE | fileIo.OpenMode.READ_WRITE);
    try {
      fileIo.writeSync(f.fd, data.buffer as ArrayBuffer);
    } finally {
      fileIo.closeSync(f);
    }
    return dest;
  }

  build() {
    Column({ space: 10 }) {
      Text('M6 spike · KMP via NAPI').fontSize(20).fontWeight(FontWeight.Bold)
      Text(`sekb_spike_ping() = ${this.ping}`).fontSize(15)
      Text(`echo_len() = ${this.echoLen}`).fontSize(15)
      Divider()
      Text('MindSpore Lite 端侧嵌入').fontSize(17).fontWeight(FontWeight.Bold)
      Text(`模型: ${this.modelPath}`).fontSize(11).fontColor(Color.Gray).maxLines(2)
      Text(this.msResult).fontSize(15)
      Text(this.msFirstFloat).fontSize(15)
      Text('元素数应=262144（1×512×512）；首元素非零才算真算出东西')
        .fontSize(11).fontColor(Color.Gray)
    }
    .width('100%')
    .height('100%')
    .padding(16)
    .justifyContent(FlexAlign.Center)
  }
}
