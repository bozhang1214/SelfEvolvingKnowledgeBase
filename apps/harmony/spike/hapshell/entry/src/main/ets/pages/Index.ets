// M6 spike 第 1 条的界面：把 NAPI 调用的结果**显示出来**。
//
// 为什么要有界面而不是只打日志：DevEco 预览/真机上"HAP 能起"与"调到了 KMP"
// 是两件事，把它们放在同一屏，装上去一眼就能分辨是哪一件没成立。
//
// `libknspike.so` 是 CMake 里 `add_library(knspike SHARED ...)` 的产物；
// 它内部再链 `libkn.so`（Kotlin/Native 产物）。
import knspike from 'libknspike.so';
import { hilog } from '@kit.PerformanceAnalysisKit';

const TAG: string = 'SEKB_KN_SPIKE';

@Entry
@Component
struct Index {
  // ping：Kotlin 侧 `sekb_spike_ping()` 恒返回 42。
  // 只要屏幕上出现 42，就说明 ArkTS → NAPI → libknspike.so → libkn.so → Kotlin 整条链路通了。
  @State ping: string = '（未调用）';
  // echoLen：长度由 **Kotlin 侧**计算（ArkTS 传中文，Kotlin 返回 code unit 数）
  @State echoLen: string = '（未调用）';

  aboutToAppear(): void {
    try {
      const p: number = knspike.ping();
      this.ping = `${p}`;
      const text: string = '端侧知识库';
      const n: number = knspike.echoLen(text);
      this.echoLen = `"${text}" → ${n}`;
      hilog.info(0x0000, TAG, 'NAPI→KMP ok: ping=%{public}d echoLen=%{public}d', p, n);
    } catch (e) {
      this.ping = `❌ ${e}`;
      hilog.error(0x0000, TAG, 'NAPI→KMP failed: %{public}s', JSON.stringify(e));
    }
  }

  build() {
    Column({ space: 12 }) {
      Text('M6 spike · KMP via NAPI').fontSize(22).fontWeight(FontWeight.Bold)
      Text(`sekb_spike_ping() = ${this.ping}`).fontSize(16)
      Text(`sekb_spike_echo_len() = ${this.echoLen}`).fontSize(16)
      Text('两个值都由 Kotlin/Native 产物 libkn.so 计算').fontSize(12).fontColor(Color.Gray)
    }
    .width('100%')
    .height('100%')
    .justifyContent(FlexAlign.Center)
  }
}
