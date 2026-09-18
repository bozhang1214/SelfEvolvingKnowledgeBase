# sekb-ondevice-agent · SEKB 端侧宿主

把 SEKB 做成**端云协同**：能在端侧算的就在端侧算（延迟优先），端侧算不了/算不好的再上云，
并且**如实告诉用户这次到底在哪算的**。

设计文档（权威）：SEKB 仓库 [`docs/RFC-端云协同与端侧Agent.md`](https://100.71.24.105:3000/bo/sekb)
接口契约：SEKB 仓库 `docs/ops/16-端云协同协议.md`

---

## 这个仓库是什么 / 不是什么

| 是 | 不是 |
|---|---|
| Android 端侧宿主（Kotlin + Compose） | 不是 SEKB 服务端（在 `bo/sekb` 仓库） |
| 端侧平面路由 / 升级 / 交接 / 工具调用 / 权限审计 | 不含知识库服务端逻辑 |
| 可插拔的端侧 LLM 适配层（当前实现：OpenAI 兼容端点） | **暂不含** llama.cpp NDK 内嵌推理（见下） |

### 为什么本期不内嵌 llama.cpp（重要，别误以为是漏做）

M2 的目标是**把协议与功能做对**，而性能数字只有在真机上才有意义：
模拟器跑在 Mac 的 CPU 上，量不出手机的 decode tok/s / TTFT / 内存带宽（SEKB RFC §9.1 已列明）。
所以端侧 LLM 做成**可插拔适配层**：

* 模拟器/开发机：连**宿主机**的 Ollama（`http://10.0.2.2:11434/v1`，就是 M0 实测过的 qwen3.5-2b/4b/9b）；
* 真机阶段（M3）：把适配层换成 llama.cpp（NDK + JNI）或 MediaPipe LLM Inference，
  上层（路由、守卫、工具、审计、上报）**一行都不用改**。

## 已实现

| 模块 | 位置 | 说明 |
|---|---|---|
| 平面路由 | `route/PlaneRouter.kt` | 与服务端 `plane_router.py` **同口径**：token 估算、档位→模型、预算阈值、6 类升级信号、退化判定 |
| 流式前缀守卫 | `route/StreamGuard.kt` | 先攒 `guardChars` 再判断；命中信号则**丢弃前缀**改道云端——用户此时没看到任何字符 |
| SSE 解析 | `net/SseParser.kt` | 对齐服务端 `chat_stream` 的 `thinking/token/done/error`，`done.meta.execution` → 执行位置 |
| 设备工具 + 权限闸门 | `tools/` | 三道闸门（未注册/缺参数/未授权），每次调用写审计；产出**越权拦截率** |
| 工具调用解析 | `tools/ToolCallJson.kt` | 宽容解析（围栏/单引号/尾随逗号），把"意图对"与"语法对"分开统计 |

## 构建与测试

```bash
# 依赖：JDK 17+（本机用 Android Studio 自带 JBR）、Android SDK（platform 36.1）
export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"

./gradlew :app:testDebugUnitTest    # 纯逻辑单测（无需模拟器、无需联网）
./gradlew :app:assembleDebug        # 产出 app/build/outputs/apk/debug/app-debug.apk
```

> 首次构建前把 `local.properties` 里的 `sdk.dir` 改成本机 SDK 路径（该文件不入库）。

## 版本基线（别随手升）

Gradle **9.2.1** + AGP **9.0.0** + Kotlin **2.2.10** + Compose BOM 2024.09.00。
两个已知坑，改版本前先读：

1. **AGP 9 自带 Kotlin 支持**：不要再加 `org.jetbrains.kotlin.android` 插件，
   否则报 `Cannot add extension with name 'kotlin'`（本项目踩过）。
2. **`kotlin { compilerOptions { } }` 必须写在 `android { }` 外面**，同理。
3. 本机只有 `platforms/android-36.1`，所以用 `compileSdk = 36` + `compileSdkMinor = 1`。

## 验证状态

见 [`docs/VERIFICATION.md`](docs/VERIFICATION.md)：哪些已经验过、怎么复现、哪些**还没验**。

## 与 SEKB 的关系

端侧自己完成的推理**不经过服务端**，所以必须主动上报路由事件（`POST /api/v1/edge/route-events`，
幂等），否则服务端的"端侧完成率/升级率"只统计到一半。这两个数字是端云协同唯一的北极星指标。
