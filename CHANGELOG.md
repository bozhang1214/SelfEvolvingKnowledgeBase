# 变更记录

> 格式：新条目在**最上方**，一条改动一次提交。发布时归档。

## 2026-09-18（M2 第二批：网络客户端 + 端云编排器）

- **改动**：
  - `net/HttpTransport.kt`：HTTP 传输抽象（唯一碰网络的地方）+ OkHttp 实现。
    抽出来的目的很实际：上层因此能用**假传输**在 JVM 单测里跑到完整的端云协同流程。
  - `net/OpenAiSseParser.kt`：OpenAI 兼容流式解析（与 SEKB 自己的 SSE 格式区分开）。
  - `net/SekbApi.kt`：登录 / enroll / refresh / heartbeat / 路由事件上报 / 聊天 SSE。
  - `edge/EdgeLlmClient.kt`：端侧 LLM 适配层（可插拔）。请求体两个关键约定：
    **关思考用 `reasoning_effort=none`**（`think` 会被 OpenAI 客户端拒掉）、
    JSON 模式用 `response_format`（约束解码开关的协议对应物）。
  - `device/DeviceCredentialStore.kt`：设备凭证用 **Keystore AES-GCM** 加密后落盘
    （长效 token 明文放着，root/备份/取证都会拿走）。
  - `chat/ChatOrchestrator.kt`：**编排器**——决策 → 端侧流式（带前缀守卫）→ 必要时改道
    云端并注入交接块 → 上报路由事件 → 单步工具轮（ReAct）。
  - `chat/ToolCallEval.kt`：工具调用合法率（分母是**尝试次数**）。

- **验证**：单测 **88 用例全绿**（新增 34：编排器全流程 10、SEKB 客户端 8、
  端侧客户端 7、凭证 5、评测 4）；APK 构建成功。
  其中 `ChatOrchestratorTest` 把"改道必须发生在用户看到任何字符之前"变成了可执行断言。

- **测试当场抓到的 4 个真缺陷**（都是自己写的）：
  1. 一次改道后回答**重复输出一遍**（工具轮没走也补发了）；
  2. "端点连不上"被误报成"模型答了空"（守卫评估跑在失败判定之前）；
  3. DEVICE_ONLY 场景下**用户看到空回答**（该改道但不许改道时，丢弃了守卫缓冲）；
  4. 端侧输出预算照抄服务端 300，导致每次聊天都被判去云端（第一批抓到）。

## 2026-09-17（M2 第一批：客户端决策内核 + 工具权限闸门）

- **背景**：端侧宿主的推理不经过服务端，所以路由决策必须能在客户端独立完成并上报；
  同时端侧 Agent 能读设备数据，必须有可审计的权限闸门。

- **改动**：
  - `route/PlaneRouter.kt`：与服务端 `plane_router.py` **同口径**的决策（token 估算、
    档位→模型、预算阈值、6 类升级信号、退化判定）。
  - `route/StreamGuard.kt`：流式前缀守卫——先攒 60 字符再判断，命中信号则丢弃前缀改道云端，
    保证"改道发生在用户看到任何字符之前"。
  - `net/SseParser.kt`：对齐 SEKB `chat_stream` 的四类 SSE 事件，解析 `done.meta.execution`。
  - `tools/`：设备工具三道闸门（未注册 / 缺参数 / 未授权）+ 权限审计（产出越权拦截率）
    + 宽容的工具调用 JSON 解析（把"意图对"与"语法对"分开统计）。
  - 构建基线：Gradle 9.2.1 + AGP 9.0.0 + Kotlin 2.2.10 + Compose BOM 2024.09.00。

- **验证**：`./gradlew :app:testDebugUnitTest` **54 用例全绿**；`:app:assembleDebug` 成功。
  详见 `docs/VERIFICATION.md`（含构建期抓到的"端侧预算 300 导致聊天永远上云"缺陷）。
