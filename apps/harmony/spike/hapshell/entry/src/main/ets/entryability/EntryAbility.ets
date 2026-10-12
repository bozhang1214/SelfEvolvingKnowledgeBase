import { AbilityConstant, ConfigurationConstant, UIAbility, Want } from '@kit.AbilityKit';
import { hilog } from '@kit.PerformanceAnalysisKit';
import { window } from '@kit.ArkUI';
import { common } from '@kit.AbilityKit';
import { AppCtx } from '../model/AppCtx';
import { SelfTestReport, runSelfTest } from '../model/SelfTestRunner';

const DOMAIN = 0x0000;
const TAG = 'SEKB_HARMONY_UI';

/** 自检的动作名（与 `scripts/harmony.sh` 的 `selftest`/`selftest-red` 两个动作对齐）。 */
const ACTION_SELFTEST = 'SEKB_SELFTEST';
const ACTION_SELFTEST_RED = 'SEKB_SELFTEST_RED';
/**
 * 直接打开第 N 页的动作名（**给截图取证用**）。
 *
 * 为什么需要它：模拟器上 `uitest uiInput click` 的坐标与页面坐标对不上（实测：点标签栏
 * 会落到滚动/滑杆上），"点四下取四张图"不可复现。用动作名开页则是**确定性**的：
 *   `aa start -a EntryAbility -b com.sekb.ohos.spike -m entry -A SEKB_PAGE_SETTINGS`
 * 这也让 README 里的截图命令可被任何人重放。
 */
const ACTION_PAGE_PREFIX = 'SEKB_PAGE_';
const PAGE_ACTIONS: string[] = ['SEKB_PAGE_CHAT', 'SEKB_PAGE_HISTORY', 'SEKB_PAGE_DOCS', 'SEKB_PAGE_SETTINGS'];
/** 页面名 → 序号（与 `AppShell.SCREENS` 同序：聊天/历史/文档/设置）。 */
function pageIndex(action: string): number {
  const i: number = PAGE_ACTIONS.indexOf(action);
  return i < 0 ? 0 : i;
}

export default class EntryAbility extends UIAbility {
  /** 本次启动是否要跑自检（由 `aa start -a <action>` 触发）。 */
  private wantSelfTest: boolean = false;
  private wantSelfTestRed: boolean = false;

  onCreate(want: Want, launchParam: AbilityConstant.LaunchParam): void {
    this.context.getApplicationContext().setColorMode(ConfigurationConstant.ColorMode.COLOR_MODE_NOT_SET);
    const action: string = want.action !== undefined && want.action !== null ? want.action : '';
    this.wantSelfTest = action === ACTION_SELFTEST || action === ACTION_SELFTEST_RED;
    this.wantSelfTestRed = action === ACTION_SELFTEST_RED;
    // 截图取证的"直接开某一页"（见 ACTION_PAGE_PREFIX 的注释）
    if (action.indexOf(ACTION_PAGE_PREFIX) === 0) {
      AppStorage.setOrCreate('sekbInitialScreen', pageIndex(action));
      hilog.info(DOMAIN, TAG, '初始页 = %{public}d（action=%{public}s）', pageIndex(action), action);
    }
    hilog.info(DOMAIN, TAG, 'Ability onCreate action=%{public}s selftest=%{public}s',
      action, `${this.wantSelfTest}`);
  }

  onDestroy(): void {
    hilog.info(DOMAIN, TAG, '%{public}s', 'Ability onDestroy');
  }

  onWindowStageCreate(windowStage: window.WindowStage): void {
    hilog.info(DOMAIN, TAG, '%{public}s', 'Ability onWindowStageCreate');

    // H8（工作流 G）：入口是**四页应用外壳**（聊天/历史/文档/设置，与 Android `SekbScreen` 同序）。
    // `pages/Index`（H1–H6 自检面板）仍注册在 main_pages.json 里、**一行未改**——
    // 工作流 F 仍在用它；本端设置页的"自检"一节提供同口径的 PASS/FAIL/SKIP 表。
    windowStage.loadContent('pages/AppShell', (err) => {
      if (err.code) {
        hilog.error(DOMAIN, TAG, 'Failed to load the content. Cause: %{public}s', JSON.stringify(err));
        return;
      }
      hilog.info(DOMAIN, TAG, 'Succeeded in loading the content.');
      if (this.wantSelfTest) {
        // 加载完成后再跑：自检里要读 rawfile、开模型、开 RDB，都需要 Ability 上下文就绪
        this.runSelfTestInBand();
      }
    });
  }

  /**
   * 由 `aa start -a SEKB_SELFTEST` 触发的自检（**可脚本化**，不需要点屏幕）。
   *
   * 为什么要有这条路径：H8 的硬指标是"自检表可复现"。靠点屏幕取证据既不稳（模拟器上
   * `uitest uiInput` 的坐标映射实测难对齐）也不可复现。所以自检入口有两个：
   *   ① 设置页的「运行自检」按钮（人看）；
   *   ② `bash scripts/harmony.sh selftest` / `selftest-red`（脚本取证据）。
   * 两条走**同一个** `runSelfTest`，判定逻辑只有一份。
   */
  private runSelfTestInBand(): void {
    const ctx = this.context as common.UIAbilityContext;
    const app = new AppCtx(ctx);
    runSelfTest(app, ctx, this.wantSelfTestRed).then((rep: SelfTestReport) => {
      hilog.info(DOMAIN, TAG, 'SELFTEST DONE %{public}s', rep.summaryLine());
    }).catch((e: Error) => {
      hilog.error(DOMAIN, TAG, 'SELFTEST THREW %{public}s', e.message);
    });
  }

  onWindowStageDestroy(): void {
    hilog.info(DOMAIN, TAG, '%{public}s', 'Ability onWindowStageDestroy');
  }

  onForeground(): void {
    hilog.info(DOMAIN, TAG, '%{public}s', 'Ability onForeground');
  }

  onBackground(): void {
    hilog.info(DOMAIN, TAG, '%{public}s', 'Ability onBackground');
  }
}
