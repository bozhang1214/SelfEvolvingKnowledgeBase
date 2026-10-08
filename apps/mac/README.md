# apps/mac · macOS 端侧宿主（M8）

**状态：功能完整可用**（2026-10-08）：与 iOS **同一份 SwiftUI 源码**（`apps/ios/App/*.swift`）跑出
聊天 / 历史 / 文档 / 设置四页；Logic 全在 `macosArm64` 的 `SharedCore.framework`，
嵌入走同一个 ONNX Runtime xcframework 的 `macos-arm64_x86_64` 切片。

```bash
bash scripts/mac_app.sh build   # 产出 .tooling/tmp/mac-app/Sekb.app
bash scripts/mac_app.sh run     # build + 直接跑二进制并抓自检输出
bash scripts/mac_app.sh ui      # build + 逐页渲染 PNG → apps/mac/docs/screenshots/
```

## 验收：与 iOS 同一张表

```
macOS  SEKB_MAC_SELFTEST PASS=42 FAIL=0 SKIP=1
```

> **构建口径（完整路径）**：`bash scripts/mac_app.sh run` 先重编 `macosArm64` 的
> `SharedCore.framework`（本轮实测 `Oct 9 02:0x` 重新链接），再编 App、直接 exec 二进制抓 stdout。

> 自检横幅前缀**按平台区分**（iOS `SEKB_IOS_SELFTEST` / macOS `SEKB_MAC_SELFTEST`）。
> 先前两端都打 `SEKB_IOS_SELFTEST`（同一份源码的自然结果），导致"这条日志是哪一端跑出来的"无从判断；
> `scripts/mac_app.sh` 现在按 `SEKB_MAC_SELFTEST` 抓（同时兼容旧前缀）。
> **iOS 侧的前缀一个字都没改**——`scripts/ios_app.sh` 与历史验收记录都按它抓。

关键项实测值（与 iOS、与 Android 的真机数字可对）：

| 自检项 | macOS 实测 |
|---|---|
| `embed_onnx_session` | 模型=fp32 空间=`BAAI/bge-small-zh-v1.5@512` 维度=512 |
| `embed_onnx_sanity` | 无关文本余弦 **0.244**、L2 范数 **1.0000/1.0000** |
| `embed_onnx_deterministic` | 两次嵌入逐位差 **0.00e+00** |
| `rag_retrieve` | 命中=1 首条分 **0.630** |
| `rag_eval_calibrate` | 阈值 0.40 → **Hit@1=26/30(87%)、Hit@3=30/30、MRR=0.928、误召回=0/3** |
| `transport_get_host_ollama` | code=200 bytes=3474 |
| `pdf_extract_text_layer` | 页数=1 字符=78（与 Android/iOS 同一份样本一致） |
| `ui_doc_delete_isolated` | 删除前=2 删除=1 剩余=1 重开=1 剩余文档=doc-b |
| `bridge_http_error_is_swift_error` | 401 → Swift 错误码=401（进程存活） |

**三端同表**：Android 真机 fp32（`apps/android/docs/RETRIEVAL-EVAL.md`）、iOS 模拟器、macOS ——
五个阈值（0.2/0.3/0.4/0.5/0.6）的 Hit@1/Hit@3/MRR/误召回**逐项相同**（RFC §4.5-F「同一张表」）。

## 视觉验证（`ui`）：**离屏渲染**，不是屏幕截图

本机 `screencapture -x` 实测失败：

```
could not create image from display
```

那是"屏幕录制"权限被拒（受限环境里拿不到）。所以 macOS 这一端不用屏幕截图，改为
`SEKB_UI_PROBE=1`（`apps/ios/App/MacUiProbe.swift`）：建一个真的 `NSWindow` + `NSHostingView(AppShell)`，
跑一轮布局与绘制后用 `cacheDisplay` 抓成 PNG。

> **第一版用的是 `ImageRenderer`，实测不够用**：它不画 `ScrollView` 的内容——
> 顶栏和输入栏都在，中间的消息区/列表区一片空白。那样的"证据"会让人以为页面是空的，比没有截图更糟。
> 现在这条路会经过 AppKit 的真实绘制流程，列表与输入框都能画出来。

| 图 | 证明了什么 |
|---|---|
| [01-chat-empty.png](docs/screenshots/01-chat-empty.png) | 空状态 + 输入栏完整可见（**输入栏没有被空状态挤出窗口**） |
| [02-chat.png](docs/screenshots/02-chat.png) | 会话标题恢复、气泡分栏、Markdown、引用来源、绿色/金色执行位置徽章 |
| [03-history.png](docs/screenshots/03-history.png) | 会话列表（相对时间 / 条数 / 当前对话） |
| [04-documents.png](docs/screenshots/04-documents.png) | 文档列表（含**真走 PDFKit** 的 `sample-text.pdf`） |
| [05-settings.png](docs/screenshots/05-settings.png) | 账号与设备、连接状态、检索阈值 0.40、端侧嵌入状态 |

**局限（不冒充截图）**：`cacheDisplay` 渲染的是视图树，不含窗口标题栏/阴影；渲染尺寸固定为
`SEKB_UI_PROBE_WIDTH`×`HEIGHT`（默认 460×860，@2x）。**未做屏幕级像素比对**
（没有基线图，也就没有"像素回归"这一层保障）——这一条如实记为缺口。

## 与前端的差异（都是**有意的**，不是遗漏）

| 项 | iOS | macOS |
|---|---|---|
| 源码 | `apps/ios/App/*.swift` | **同一份文件**（`#if os(...)` 只在自检前缀 / 最小窗口尺寸 / Keychain 语义三处） |
| 共享层产物 | `iosSimulatorArm64/debugFramework` | `macosArm64/debugFramework` |
| ONNX Runtime | `ios-arm64_x86_64-simulator` 切片 | `macos-arm64_x86_64` 切片（**同一个 xcframework**，同一份下载产物） |
| 包结构 | 扁平（`Sekb.app/{Sekb,Info.plist,models/}`） | `Contents/{MacOS,Resources,Info.plist}` |
| 抓自检 | `simctl launch` + `log show --predicate`（模拟器里 `print` 不进统一日志） | **直接 exec 二进制，stdout 直出**（这条比 iOS 干净） |
| 签名 | **故意不签**（未签名→Keychain -34018 与 ad-hoc→SpringBoard 拒启动 互斥） | **ad-hoc 签**（`codesign -s -`；macOS 没有 SpringBoard 那道门） |
| 退出方式 | 模拟器上常驻 | `SEKB_SELFTEST_ONLY=1` → 自检跑完自行 `exit(0)`；`SEKB_UI_PROBE=1` → 渲染完自行退出 |
| 数据目录 | `SEKB_DATA_DIR`（由 `ios_app.sh` 指向 `.tooling/tmp/`） | `SEKB_DATA_DIR`（由 `mac_app.sh` 指向 `.tooling/tmp/`） |

> ⚠️ macOS **没有 GNU `timeout`**（实测 `timeout: command not found`），而 GUI 应用会一直跑事件循环。
> 所以不能用 `timeout` 收尾（kill 还有截断未 flush 的 stdout 的风险），改为**由应用自己跑完即退**
> （`SekbApp.init` 里的 `SEKB_SELFTEST_ONLY`）。脚本另配纯 bash 看门狗兜底。

> ⚠️ macOS 的 `~/Library/Application Support` 在受限沙箱里不可写（实测 `Operation not permitted`），
> 所以数据目录由 `SEKB_DATA_DIR` 显式指定到仓库内；不指路时应用会退到临时目录
> （功能不坏，但"重启后历史还在吗"就验不了）。

## 唯一缺口：Keychain 往返（SKIP，已把原因量到"码"这一级）

原计划是"macOS 上 ad-hoc 签名能用 Keychain → 顺带把 iOS 那个 SKIP 补上"。**实测没成**，
但把原因从"一个模糊的 -34018"变成了**三种失败模式**，这比原来有价值：

| 配置 | 写状态 | 读状态 | 结论 |
|---|---|---|---|
| iOS 未签名（模拟器） | 未记 | **-34018** | 缺 entitlement |
| macOS + 数据保护钥匙串（`kSecUseDataProtectionKeychain`）+ ad-hoc | **-34018** | -25300 | 数据保护钥匙串要求 `keychain-access-group` entitlement，而它需要真实 team ID |
| macOS + 传统登录钥匙串 + ad-hoc | **100001** | -25300 | 也未成功 |

**根因是签名身份，不是实现**：`KeychainCredentialStore` 的读写查询在三种配置下都是同一套代码。
所以自检里把它记为 **SKIP（未验 + 带实测码的原因）**，并**限定只在这三个环境限制类状态码下才 SKIP**——
其它任何失败仍然记 FAIL，这条 SKIP 不会掩盖真正的实现缺陷。
要真验需要：真实签名身份（Apple Developer team）+ 真机，或 Xcode 工程里的 Keychain Sharing capability。

## 共享层缺口（与 iOS 同一条）

`SekbApi` 的 throwing 方法**没有标 `@Throws`**，Kotlin/Native 会把异常直接终止进程
（实测 `Abort trap: 6`），所以 Apple 侧用 `SekbApiBridge.swift` 顶上。
详情、证据与"主线补上 `@Throws` 后应删除该文件"的说明见
[`apps/ios/README.md`](../ios/README.md) 的「共享层缺口」一节。

## 未验清单（如实列）

- **真实 enroll / refresh**（需要真实账号与可达后端）与**真实云端 SSE 聊天**：未验。
- **Keychain 往返**：SKIP（见上）。
- **屏幕级像素回归**：未做（见"视觉验证"的局限）。
- **真机性能**：未验（macOS 侧的性能数字只在 M0 的 Ollama 基线上量过，与本期 UI 无关）。
