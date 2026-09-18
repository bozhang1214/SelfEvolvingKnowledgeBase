# 变更记录

> 格式：新条目在**最上方**，一条改动一次提交。发布时归档。

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
