---
title: 设计模式与原则评价（Design Patterns）
layer: 评价层
owner: SEKB Team
status: active
version: v1.0.0
last-updated: 2026-09-09
based-on-commit: 1ffb13c
related: [01-ARCHITECTURE, 11-EVOLUTION]
---

# 07 · 设计模式与原则评价（DESIGN-PATTERNS）

> **本文回答什么问题**：代码里用了哪些设计模式？设计原则符合度如何？有哪些反模式？
> **适合谁读**：架构师、技术决策者。
> **读完能做什么**：了解架构质量现状与改进方向；为 11-EVOLUTION 提供依据。
> **纪律**：每条评价有代码证据（file:line）或标注【推断·待验证】；as-is 描述，改进归 11。

---

## 1. 设计模式识别与评价

### 1.1 工厂模式：LLMFactory / AgentFactory
- **落点证据**：`app/core/llm_factory.py:143-211`（LLMFactory.get 缓存+降级）；`app/agents/factory.py`（AgentFactory）；`app/core/config.py:453-464`（get_config 单例工厂）。
- **实现**：LLM 按角色缓存、创建失败降级 reasoner→chat；Agent 统一 create_all。
- **恰当性评价**：
  - ✅ LLMFactory 集中了角色配置/降级/统计，是好的封装点。
  - ✅ 延迟装配（bootstrap 惰性 import）避免启动重依赖。
  - ⚠️ LLM 创建链路耦合 api_key/base_url 全局单源，多供应商扩展需重构（T3）。
  - 🔧 若需多模型/多供应商，可引入 provider 注册表。
- **过度设计？** 否。

### 1.2 门面模式：AppContext / Bootstrap
- **落点证据**：`app/core/bootstrap.py:52-95`（AppContext dataclass 聚合全部组件）、`:97-267`（initialize_app 装配）、`get_app_context()`（:270-274）。
- **评价**：✅ 统一装配顺序清晰、CLI/API 共用；✅ 条件装配（L3/news/job）用 enabled 开关。⚠️ 全局单例 `_app_context` 使测试需要 reset/替换（test_p0_fixes 已处理 get_app 懒加载）；可观测为模块级单例。

### 1.3 策略模式：ReflectionStrategy
- **落点证据**：`app/agents/strategies/`（always/adaptive/sampling）+ `factory.py:28-39`。
- **评价**：✅ 提供了策略接口，配置 `reflection.policy` 选择。⚠️ **adaptive/sampling 实际降级回 always**（factory.py:32-39）——策略抽象大于实现（YAGNI 见 §2）。

### 1.4 模板方法：BaseAgent
- **落点证据**：`app/agents/base.py`（BaseAgent，子类实现 `__call__`）。
- **评价**：✅ 统一 agent 骨架，5 节点结构一致。⚠️ 抽象较薄（无强约束的钩子契约），子类自由度大。

### 1.5 仓储/适配器：KnowledgeBaseBackend + DirectVectorStore
- **落点证据**：`app/memory/base.py`（KnowledgeBaseBackend 抽象）、`app/memory/knowledge_base.py`（Chroma 实现）、`app/tools/direct/vector_store.py`（适配返回 dict）。
- **评价**：✅ 存储抽象隔离了 Chroma 实现细节，便于未来换向量库。⚠️ JSONStorage 未走统一存储抽象（与 memory/base 分离）；两套存储体系并存（T6）。

### 1.6 注册表：ToolRegistry / 路由注册
- **落点证据**：`app/tools/registry.py`、`app/api/server.py:268-280`（include_router）。
- **评价**：✅ 工具注册与路由注册集中。⚠️ 实际注册工具仅 web_search（T5），注册表能力>使用；`metrics`/`monitoring` 路由注释建议网关保护。

### 1.7 装饰器/中间件（鉴权/限流/日志）
- **落点**：路由 `Depends`（get_current_user / require_full_access）、FastAPI 异常处理器、middleware.py RateLimitMiddleware。
- **评价**：✅ 鉴权用依赖注入清晰；✅ 全局异常统一脱敏（server.py:226-250）。⚠️ **RateLimitMiddleware 实现但未挂载**（server.py:195-201）——装饰能力未启用；⚠️ 日志上下文注入无请求级中间件（T8），仅 chat 路由手动 bind。

### 1.8 观察者/发布-订阅（SSE、后台任务）
- **落点**：chat_stream 的 asyncio.Queue（chat.py:719-758）+ token sink（core/token_sink.py）；知识入库/偏好抽取 create_task。
- **评价**：✅ 流式生产者-消费者模型清晰（图任务→queue→SSE）。⚠️ 后台任务无完成回调/编排（fire-and-forget + GC set），缺 shutdown drain（T7）。

### 1.9 单例模式
- **落点**：get_config（lru_cache）、_app_context 全局、metrics 模块级、ProfileStorage 模块级单例（profile.py:25）。
- **评价**：✅ 配置单例合理。⚠️ 过多模块级可变单例使并行测试需要小心清理。

### 1.10 建造者：GraphBuilder
- **落点**：graph/builder.py GraphBuilder.build()。
- **评价**：✅ 图构建集中。⚠️ 与节点依赖（agents/factory）耦合，扩展节点需改 builder。

---

## 2. 设计原则符合度评分（0-5）

| 原则 | 评分 | 证据（好/坏） | 改进建议 |
|------|------|--------------|---------|
| 单一职责 SRP | 5/5 | 好：AppContext/各 storage 单域；chat.py 已瘦身（828→538，画像/skill 下沉 profile_service，WP2/WP3） | 已收口 |
| 开闭 OCP | 5/5 | 好：向量库/反思/工具可扩展；LLM 绕过点已收口（WP4，仅剩 image_processor 独立视觉模型） | 已收口 |
| 里氏替换 LSP | 4/5 | BaseAgent/KnowledgeBaseBackend 子类可替换；证据：base.py 接口 | 补充抽象契约测试 |
| 接口隔离 ISP | 3/5 | storage.base 接口较全但 JSONStorage 与 memory/base 未统一 | 统一存储抽象 |
| 依赖倒置 DIP | 4/5 | 延迟导入 + AppContext 注入（bootstrap.py:39-48）；坏：部分模块直接 import 单例 | 收敛依赖方向 |
| DRY | 5/5 | 好：llm_factory 统一统计 + core/utils + rag/format 收敛重复（WP1）；LLM 绕过 9 处已收口（WP4） | 已收口 |
| KISS | 5/5 | 整体直白；adaptive/sampling 占位已删除（D3） | 已清理 |
| YAGNI | 4/5 | 49 死配置已删 11 键（WP6）+ 本地 trace 已裁剪（D6）；l2 记忆预留 | 剩余 Phase2 预留已记 06-CONFIG |
| 关注点分离 | 4/5 | 分层清晰；chat/upload/share/job 已下沉服务层（WP2，services 2→7 模块） | 服务层已抽取 |
| 依赖方向 | 4/5 | 分层无环；靠目录纪律 | 加架构约束测试 |
| 失败显式化 | 3/5 | 好：异常统一映射/error_id；坏：多处静默降级（RAG 吞异常、知识入库吞错、图兜底） | 区分可预期降级 vs 静默错误 |
| 可测试性 | 4/5 | 528+ 单测全绿、TestClient/隔离重构；坏：全局单例需小心清理 | 依赖注入收口 |
| 可观测性 | 3/5 | 23 指标+12 告警；坏：4 指标未埋点、LLM 单次延迟缺失（T8） | 补埋点 + record_llm_call 接线 |
| 最小惊讶 | 4/5 | REST 语义直观；坏：知识入库"注释不阻塞"实为 await（T7） | 修注释/改实现 |
| 契约优先 | 3/5 | Pydantic 强校验；坏：image_analysis 配置未建模被静默丢（T2） | 显式 schema |

---

## 3. 反模式清单（证据）

| 反模式 | 落点 | 影响 |
|--------|------|------|
| 上帝路由 | ~~chat.py:407-623（单函数承载 7 步流程）~~ → 已下沉服务层（WP2/WP3，chat.py 828→538） | 已修复 |
| 静默吞错 | knowledge_ingestor 异常仅日志（chat.py:597-600）；RAG 异常降级空（builder.py:235-241） | 问题不可见 |
| 注释-实现漂移 | chat.py:586「不阻塞」vs await；auth.py:68「72h」vs 90 天 | 误导排障 |
| 死配置/死代码 | ~~49 死配置 + trigger_now + LocalTraceCollector~~ → 已删 11 键 + trigger_now + LocalTraceCollector（WP6/D6） | 已清理 |
| 硬编码标签 | metrics user_id="default"、tool="web_search"（T8） | 观测失真 |
| 配置两套来源 | image_analysis yaml 段丢弃 vs 环境变量（T2） | 配置陷阱 |

---

## 4. 技术术语落地程度（摘要）

| 术语 | 落地 |
|------|------|
| Plan-and-Execute / Reflexion / Re-plan | ✅ 完整（Supervisor/Planner/Executor/Critic/重规划循环） |
| RAG（Vector Search / Chunking / Embedding） | ✅ 完整；混合检索/Rerank/Query Rewrite 缺失（11） |
| TTFT | 已测（02 §8）；reasoner planner 是热点 |
| 流式（SSE / 真流式） | ✅ token sink + astream_events |
| 记忆分层 | L1 ✅ / L2 ✗ 预留 / L3 ✅ |
| 成本控制 | ⚠️ 定价/统计在，预算控制未接线 |
| 限流/熔断 | ✗ 限流未挂载、无熔断 |
| 幂等 | 部分覆盖（upload overwrite/job 缓存/news force） |

---

## 5. 已知缺口与待确认项

- 模式评价中 1.4-1.6 等基于既有代码阅读，部分细部（如 factory 完整字段）建议以 .facts 交叉复核。
- 「单一职责」改进（chat 服务层抽取）是否纳入 11 路线由架构决策。

---

## 相关文档

- [01-ARCHITECTURE.md](./01-ARCHITECTURE.md)（ADR）
- [11-EVOLUTION.md](./11-EVOLUTION.md)（改进路线与技术债）
- 事实表：T2/T3/T7/T8
