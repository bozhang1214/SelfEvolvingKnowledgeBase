# apps/contract · 语言无关的契约夹具（单一事实源）

**这份目录回答一个问题**：Android / iOS / 鸿蒙 / Mac 四端，凭什么保证"行为一致"？
答案不是"我抄得仔细"，而是**同一份夹具、各端各跑一遍、结论必须相同**。

```
apps/contract/
├── README.md          本文件（格式约定 + 各端怎么接）
├── routing.json       端云路由决策（plane / reason / tier / 预算边界 / 档位）
├── signals.json       6 类升级信号 + 退化/低置信/工具幻觉（含"前缀阶段必须排除 json_invalid"）
└── privacy.json       隐私硬边界：DEVICE_ONLY 命中所有升级信号也**不得**升云
```

## 1. 为什么需要它（而不是只靠共享代码）

共享代码（KMP `shared/`）解决"同一份实现"，但解决不了三件事：

1. **端口实现差异**：同样的逻辑接不同的传输/存储，行为可能不同（超时、编码、错误处理）；
2. **鸿蒙的特殊性**：若 spike 失败改走 ArkTS 重写，那时**没有共享代码**，只剩夹具能证明一致性；
3. **回归护栏**：共享层重构（M4 就要做）时，夹具是"不许变"的那一半——**行为变更必须显式改夹具**。

## 2. 格式约定

- 顶层有 `schema`（如 `sekb.contract.routing/1`）与 `cases` 数组；
- 每个 case 必须有 `id`（唯一、可读、`模块.场景` 命名）与 `expect`；
- **只写"能观测到的结果"**（决策字段、信号名、是否允许升级），不写实现细节；
- 字符串一律 UTF-8；长文本用 `{"repeat": {"unit": "...", "times": N}}` 展开，避免夹具文件里贴几千字；
- **夹具改了就说明行为改了**——必须在提交信息里写清"为什么改、哪一端受影响"。

## 3. 各端怎么接

| 端 | runner | 现状 |
|---|---|---|
| Android | `apps/android/app/src/test/kotlin/com/sekb/ondevice/ContractFixturesTest.kt`（JVM 单测，读本目录） | ✅ 已接（routing / signals / privacy，共 32 个 case / 4 个测试） |
| iOS | `apps/ios` 测试 target 复用同一份 JSON | ⬜ M5 |
| 鸿蒙 | ArkTS 测试或 HAP 内自检项读同一份 JSON | ⬜ M6/M7 |
| Mac | 复用 iOS 的 Swift runner | ⬜ M8 |
| JVM（桌面/CI） | 与 Android 同一份 runner（无 Android 依赖） | ✅ 已接 |

**Runner 的硬要求**（否则夹具会"假绿"）：

1. 夹具目录找不到 → **测试失败**（不是跳过）；
2. case 数量低于下限 → **测试失败**（防止夹具被截断后仍然"全绿"）；
3. 覆盖度自检：6 类升级信号必须**逐个**在夹具里出现过（`privacy.json` 或 `signals.json`）；
4. 逐条 `id` 断言，失败信息里带上 `id`，便于定位是哪一端不一致；
5. **把夹具目录登记为该端测试的输入**——否则改夹具不会触发重跑，会出现"改了期望值却依然绿"的**假绿**
   （Android 侧已实测踩到：`testDebugUnitTest` 判 UP-TO-DATE 不重跑；修法见
   `apps/android/app/build.gradle.kts` 的 `inputs.dir(contractDir)` + `-Dsekb.contractDir`，
   任何新端 runner 都必须做同样的事）。

## 4. 覆盖现状与后续

| 夹具 | 覆盖 | 追加计划 |
|---|---|---|
| `routing.json`（10 case） | 端侧优先 / 短任务档 / 输入超预算（含 150 与 170 份重复的**预算临界**）/ 输出超预算 / `prefer_cloud` / `device_only`（角色与数据两种入口，且**压过 prefer_cloud**）/ 质量档 / 512 输出预算边界 | 端侧模型档位映射、`context_overflow` 事前改判 |
| `signals.json`（14 case） | `empty` / `degenerate`（含「2 行相同不算、3 行相同才算」的**行数临界**）/ `low_confidence` / `tool_hallucination` / `json_invalid` / **前缀阶段排除 json_invalid** / `timeout` / 干净输出 | 长度临界（39/40 字符） |
| `privacy.json`（13 case） | DEVICE_ONLY × 6 类信号：**信号命中但升级被拦**（含「按角色标记」与「prefer_cloud 下仍不走云」）+ **R10 已落地**：回环地址可执行、局域网 / 模拟器宿主（`10.0.2.2`）/ 尾网一律 `blocked=true` + `device_only_requires_local_runtime`，另加一条「普通数据打远端端点不该被误伤」的对照组 | 端侧端点为本机但**模型未加载**时的降级路径 |
| `toolcall.json`（待建） | 工具调用 JSON 解析/未知工具/缺必填参数 | M4 第 2 步（去平台化）时一并建 |
| `protocol.json`（待建） | 协议路径与字段（与 `docs/ops/16-端云协同协议.md` 同源） | 与 `check_protocol_paths.py` 合并为 N 方守卫时建 |
| `retrieval.json`（待建） | 检索阈值与空间戳不变量（含"空间不符必须重算/拒绝"） | M4 第 3 步（搬 `shared/`）时建 |
