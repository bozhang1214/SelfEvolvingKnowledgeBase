# apps/mac · macOS 端侧宿主（M8）

**状态：功能核心已跑通。** 复用 iOS 的 SwiftUI 源码 + `macosArm64` 共享层 + ONNX Runtime 的 macOS 切片。

```bash
bash scripts/mac_app.sh build   # 产出 .tooling/tmp/mac-app/Sekb.app
bash scripts/mac_app.sh run     # build + 直接跑二进制并抓自检输出
```

## 与前端的差异（都是**有意的**，不是遗漏）

| 项 | iOS | macOS |
|---|---|---|
| 源码 | `apps/ios/App/*.swift` | **同一份文件**（`grep 'import UIKit\|UIApplication'` 为空，无需分叉） |
| 共享层产物 | `iosSimulatorArm64/debugFramework` | `macosArm64/debugFramework` |
| ONNX Runtime | `ios-arm64_x86_64-simulator` 切片 | `macos-arm64_x86_64` 切片（**同一个 xcframework**，同一份下载产物） |
| 包结构 | 扁平（`Sekb.app/{Sekb,Info.plist,models/}`） | `Contents/{MacOS,Resources,Info.plist}` |
| 抓自检 | `simctl launch` + `log show --predicate`（模拟器里 `print` 不进统一日志） | **直接 exec 二进制，stdout 直出**（这条比 iOS 干净） |
| 签名 | **故意不签**（未签名→Keychain -34018 与 ad-hoc→SpringBoard 拒启动 互斥） | **ad-hoc 签**（`codesign -s -`；macOS 没有 SpringBoard 那道门） |
| 退出方式 | 模拟器上常驻 | `SEKB_SELFTEST_ONLY=1` → 自检跑完自行 `exit(0)` |

> ⚠️ macOS **没有 GNU `timeout`**（实测 `timeout: command not found`），而 GUI 应用会一直跑事件循环。
> 所以不能用 `timeout` 收尾（kill 还有截断未 flush 的 stdout 的风险），改为**由应用自己跑完即退**
> （`SekbApp.init` 里的 `SEKB_SELFTEST_ONLY`）。脚本另配纯 bash 看门狗兜底。

## 验收：与 iOS 同一张表（26 PASS / 0 FAIL / 1 SKIP）

```
macOS  SEKB_IOS_SELFTEST PASS=26 FAIL=0 SKIP=1
```

关键项实测值（与 iOS、与 Android 的真机数字可对：

| 自检项 | macOS 实测 |
|---|---|
| `embed_onnx_session` | 模型=fp32 空间=`BAAI/bge-small-zh-v1.5@512` 维度=512 |
| `embed_onnx_sanity` | 无关文本余弦 **0.244**、L2 范数 **1.0000/1.0000** |
| `embed_onnx_deterministic` | 两次嵌入逐位差 **0.00e+00** |
| `rag_retrieve` | 命中=1 首条分 **0.630** |
| `rag_eval_calibrate` | 阈值 0.40 → **Hit@1=26/30(87%)、Hit@3=30/30、MRR=0.928、误召回=0/3** |
| `transport_get_host_ollama` | code=200 bytes=3474 |
| `pdf_extract_text_layer` | 页数=1 字符=78（与 Android/iOS 同一份样本一致） |

**三端同表**：Android 真机 fp32（`apps/android/docs/RETRIEVAL-EVAL.md`）、iOS 模拟器、macOS —— 
五个阈值（0.2/0.3/0.4/0.5/0.6）的 Hit@1/Hit@3/MRR/误召回**逐项相同**（RFC §4.5-F「同一张表」）。

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

## 已知的与 M8 无关的缺口

- `apps/ios/App/SekbApp.swift` 里的自检横幅仍打印 `SEKB_IOS_SELFTEST` 前缀（三端共用同一份源码的
  自然结果）。**不影响正确性**，但如果将来要按端区分日志，应改成按平台前缀——留待 M8 收尾时统一处理。
