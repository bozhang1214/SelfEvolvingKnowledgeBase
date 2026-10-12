/**
 * H8（工作流 G）· 鸿蒙端的**主题令牌**——所有颜色只在这里出现一次。
 *
 * ## 为什么必须集中（Android 真的因此出过缺陷）
 *
 * Android 端曾把语义色（本机=绿 / 云端=金）散落在各页面里硬编码，
 * 结果"绿色"既表示"本机完成"又表示"成功提示"，用户看不出徽章在说什么。
 * 所以本端的规定是：**页面里不得出现 `'#RRGGBB'` 字面量**，
 * 一律从 `Theme` 取；语义色只在 `Theme.edgeOk` / `Theme.cloud` 上出现。
 *
 * 口径与 Android `ui/theme/SekbTheme.kt`、网页端 Ant Design 一致：
 * 主色 `#1677FF`、浅灰底 `#F5F7FA`、白卡片、圆角 12/14。
 */
export class Theme {
  /** 主色（按钮、用户气泡、进行中指示） */
  static readonly primary: string = '#1677FF';
  /** 主色淡底（头像、图标底板） */
  static readonly primarySoft: string = '#E6F0FF';
  /** 页面底色 */
  static readonly bg: string = '#F5F7FA';
  /** 卡片/气泡底 */
  static readonly card: string = '#FFFFFF';
  /** 主文字 */
  static readonly text: string = '#1F2329';
  /** 次要文字 */
  static readonly textSub: string = '#595959';
  /** 弱化文字（时间戳、说明） */
  static readonly textFaint: string = '#8C8C8C';
  /** 描边 */
  static readonly border: string = '#E5E7EB';
  /** 输入框底 */
  static readonly inputBg: string = '#F0F2F5';
  /** 语义色：**本机完成**（只在执行位置徽章上用，别处不许用绿） */
  static readonly edgeOk: string = '#389E0D';
  /** 语义色：**云端完成 / 已上云**（只在徽章上用） */
  static readonly cloud: string = '#D48806';
  /** 失败/危险（删除按钮、错误文本） */
  static readonly danger: string = '#CF1322';
  /** 危险淡底 */
  static readonly dangerSoft: string = '#FFF1F0';

  /** 圆角 */
  static readonly rCard: number = 12;
  static readonly rBubble: number = 14;
  static readonly rPill: number = 20;

  /**
   * 版本信息（设置页显示）。
   *
   * ⚠️ 这是**手写常量**，不是从 bundle 元数据读的（ArkTS 读 `bundleInfo.versionName` 需要
   * `@kit.AbilityKit` 的 `bundleManager`，本轮没接）。所以它必须与
   * `entry/src/main/module.json5` 的 `versionName` **同口径**——改一处要改两处，
   * 设置页的"版本"一节把这条限制写出来，免得被当成权威版本号。
   */
  static readonly version: string = '0.1.0';
  /** HAP 版本号（与 module.json5 的 versionName 对应；release 打包记录见 README） */
  static readonly buildFlavor: string = 'debug/arm64';

  /** 字号 */
  static readonly fsTitle: number = 20;
  static readonly fsSection: number = 16;
  static readonly fsBody: number = 14;
  static readonly fsMeta: number = 11;
}
