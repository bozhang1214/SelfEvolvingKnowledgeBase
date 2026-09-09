# 总体架构设计

> 文档版本：v1.0.0
> 最后更新：2026-08-12
> 状态：设计评审中

***

## 一、核心哲学：PDCA 循环

项目以 **PDCA（Plan-Do-Check-Act）** 为顶层哲学，由五个智能体角色协作落地：

```
┌────────────────────────────────────────────────────────────────┐
│                      PDCA 循环映射                              │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│   Plan  ┌──► Supervisor（监督者）：意图识别 + 路由决策          │
│         │    ► Planner（规划者）：复杂任务拆解为 Task List       │
│         │                                                      │
│   Do    ├──► Executor（执行器）：工具调用 + 草稿生成            │
│         │                                                      │
│   Check └──► Critic（审查者）：多维度反思评估，不通过则回滚      │
│                                                                    │
│   Act   ────► Scribe（记录员）：摘要 + 重要性评分 + 知识沉淀    │
│                                                                    │
└────────────────────────────────────────────────────────────────┘
```

**循环特性**：

- Critic 反思不通过 → 回滚到 Planner 重规划（最多 2 次）
- 超过重规划上限 → 返回当前最佳答案 + "置信度较低"提示
- Scribe 在对话结束后异步触发，不阻塞用户响应

***

## 二、系统总体架构

```
┌──────────────────────────────────────────────────────────────────────┐
│                        用户接入层                                    │
│   ┌──────────────────┐         ┌──────────────────┐                 │
│   │   CLI 交互入口    │         │  FastAPI HTTP    │                 │
│   │  (typer/click)   │         │   (Phase 1 同步) │                 │
│   └────────┬─────────┘         └────────┬─────────┘                 │
│            └────────────┬───────────────┘                            │
│                         ▼                                            │
├──────────────────────────────────────────────────────────────────────┤
│                      LangGraph 工作流引擎                            │
│                         ┌──────────────┐                             │
│                         │  Graph State │  (TypedDict)                │
│                         └──────┬───────┘                             │
│   ┌────────────────────────────┼────────────────────────────┐       │
│   │                            │                            │       │
│   ▼                            ▼                            ▼       │
│ [Supervisor] ──► [Router] ──► [Planner] ──► [Executor] ──► [Critic] │
│   意图识别        路由分发       任务拆解      工具调用       反思评估 │
│   + 预检索                       Task List    + 草稿生成              │
│        │                                                        │
│        │      ┌──────────────────────────────────────┐           │
│        └─────►│  简单闲聊直通 chat_simple，跳过 P/D  │           │
│               └──────────────────────────────────────┘           │
│                                                                    │
│                          Critic 不通过                             │
│                       ◄──────────────────── 回滚到 Planner         │
│                          (max_replan=2)                            │
│                                                                    │
│   最终答案 ◄──── [Scribe]（异步：摘要 + 重要性评分）                │
├──────────────────────────────────────────────────────────────────────┤
│                          工具层                                      │
│   ┌──────────────────────┐    ┌──────────────────────────────┐     │
│   │  MCP 标准协议层       │    │  本地直连层（绕过 MCP）       │     │
│   │  - 博查搜索 MCP       │    │  - 向量库检索（Phase 2）      │     │
│   │  - 文件解析 MCP       │    │  - 本地 JSON 存储             │     │
│   │  - 后续扩展工具       │    │  - 配置/日志                  │     │
│   └──────────────────────┘    └──────────────────────────────┘     │
├──────────────────────────────────────────────────────────────────────┤
│                          LLM 工厂                                    │
│   DeepSeek API  ├─ deepseek-chat     （快思考：识别/执行/摘要）       │
│                 └─ deepseek-reasoner （慢思考：规划/复杂反思）        │
├──────────────────────────────────────────────────────────────────────┤
│                          记忆系统                                    │
│   ┌────────────┐  ┌────────────┐  ┌────────────────────┐           │
│   │ L1 短期    │  │ L2 中期    │  │ L3 长期知识库      │           │
│   │ 工作记忆   │  │ 会话偏好   │  │ 向量库+结构化     │           │
│   │ (Phase1)  │  │ (Phase2)   │  │ (Phase2)          │           │
│   └────────────┘  └────────────┘  └────────────────────┘           │
├──────────────────────────────────────────────────────────────────────┤
│                          存储层                                      │
│   ┌──────────────────────┐  ┌──────────────────────────────┐       │
│   │ 本地 JSON (Phase 1)  │  │ PostgreSQL + 向量库 (Phase 2)│       │
│   │ - conversations/     │  │ - 会话表                     │       │
│   │ - index.json         │  │ - 知识表                     │       │
│   │ - traces/            │  │ - 向量索引                   │       │
│   └──────────────────────┘  └──────────────────────────────┘       │
├──────────────────────────────────────────────────────────────────────┤
│                          可观测性层                                  │
│   ┌────────────┐  ┌────────────┐  ┌────────────────────┐           │
│   │ 结构化日志 │  │ Eval 指标  │  │ Tracing            │           │
│   │ (JSON)     │  │ (9 项量化) │  │ LangSmith/本地JSON │           │
│   └────────────┘  └────────────┘  └────────────────────┘           │
└──────────────────────────────────────────────────────────────────────┘
```

***

## 三、技术栈选型

| 层                | 技术                         | 版本要求   | 选型理由                          |
| ---------------- | -------------------------- | ------ | ----------------------------- |
| **后端语言**         | Python                     | 3.11+  | LangGraph 原生生态，async 性能好      |
| **Web 框架**       | FastAPI                    | 0.110+ | 异步、自动 OpenAPI、与 Pydantic 配合好  |
| **Agent 框架**     | LangGraph                  | 0.2+   | 多 Agent 工作流最佳实践，原生支持反思/回滚     |
| **LangChain 生态** | langchain / langchain-core | 0.3+   | DeepSeek 集成、MCP 适配器、Prompt 模板 |
| **MCP 客户端**      | langchain-mcp-adapters     | 最新     | LangChain 官方 MCP 适配           |
| **LLM**          | DeepSeek API               | -      | 国内直连、chat/reasoner 双模型分级      |
| **联网搜索**         | 博查搜索 API                   | -      | 国内直连，无需翻墙                     |
| **向量库（P2）**      | ChromaDB                   | 0.5+   | 本地嵌入、与 LangChain 集成成熟         |
| **Tracing**      | LangSmith → 本地 JSON 降级     | -      | 先试 LangSmith，连不上自动降级          |
| **配置**           | Pydantic Settings + YAML   | -      | 强类型配置校验                       |
| **CLI**          | typer                      | 0.12+  | 与 Click 兼容、类型提示友好             |
| **测试**           | pytest + pytest-asyncio    | -      | 异步测试支持                        |
| **依赖管理**         | uv 或 poetry                | -      | uv 更快，poetry 更成熟              |

**前端栈（Phase 3 再定）**：Node.js + React/Vue，前端代码置于 `frontend/`。

***

## 四、目录结构

```
SelfEvolvingKnowledgeBase/
├── docs/                               # 设计文档（本目录）
│   ├── README.md
│   ├── 01-architecture.md
│   ├── 02-phase1-design.md
│   ├── 03-evaluation-and-testing.md
│   └── 04-config-reference.md
│
├── backend/                            # Python 后端
│   ├── app/
│   │   ├── agents/                     # 五个 Agent 节点
│   │   │   ├── supervisor.py           # 监督者：意图识别 + 预检索
│   │   │   ├── planner.py              # 规划者：任务拆解
│   │   │   ├── executor.py             # 执行器：工具调用 + 草稿
│   │   │   ├── critic.py               # 审查者：反思评估（策略接口）
│   │   │   ├── scribe.py               # 记录员：摘要 + 重要性评分
│   │   │   ├── prompts/                # 各 Agent 的 Prompt 模板
│   │   │   │   ├── supervisor.py
│   │   │   │   ├── planner.py
│   │   │   │   ├── executor.py
│   │   │   │   ├── critic.py
│   │   │   │   └── scribe.py
│   │   │   └── strategies/             # 反思策略实现
│   │   │       ├── base.py             # ReflectionPolicy 抽象
│   │   │       ├── always.py           # AlwaysReflectStrategy
│   │   │       ├── adaptive.py         # AdaptiveReflectStrategy（预留）
│   │   │       └── sampling.py         # SamplingReflectStrategy（预留）
│   │   │
│   │   ├── graph/                      # LangGraph 工作流
│   │   │   ├── state.py                # State TypedDict 定义
│   │   │   ├── builder.py              # 图构建（节点+边+条件路由）
│   │   │   └── routing.py              # 条件路由函数
│   │   │
│   │   ├── core/                       # 核心引擎
│   │   │   ├── config.py               # 配置加载（YAML + 环境变量）
│   │   │   ├── llm_factory.py          # LLM 工厂（按角色返回模型实例）
│   │   │   ├── tracing.py              # Tracing 初始化（LangSmith/本地）
│   │   │   ├── logging.py              # 结构化日志配置
│   │   │   └── exceptions.py           # 自定义异常层级
│   │   │
│   │   ├── memory/                     # 记忆系统
│   │   │   ├── base.py                 # MemoryBackend 抽象
│   │   │   ├── short_term.py           # L1 短期记忆（滑动窗口+压缩）
│   │   │   ├── session.py              # L2 中期记忆接口（预留）
│   │   │   └── knowledge.py            # L3 长期知识接口（预留）
│   │   │
│   │   ├── tools/                      # 工具层
│   │   │   ├── mcp/                    # MCP 标准工具
│   │   │   │   ├── client.py           # MCP Client 统一封装
│   │   │   │   └── bocha_server.py     # 博查搜索 MCP Server（自实现）
│   │   │   ├── direct/                 # 本地直连工具（绕过 MCP）
│   │   │   │   ├── vector_store.py     # 向量检索（Phase 2）
│   │   │   │   └── json_store.py       # 本地 JSON 存储
│   │   │   └── registry.py             # 工具注册表（按意图暴露工具）
│   │   │
│   │   ├── storage/                    # 存储后端
│   │   │   ├── base.py                 # StorageBackend 抽象
│   │   │   ├── json_storage.py         # 本地 JSON 实现（Phase 1）
│   │   │   └── postgres_storage.py     # PostgreSQL 实现（Phase 2 预留）
│   │   │
│   │   ├── eval/                       # 评估体系
│   │   │   ├── metrics.py              # 9 项指标计算
│   │   │   ├── assertion.py            # 断言引擎
│   │   │   ├── runner.py               # Eval Mode 运行器
│   │   │   ├── reporter.py             # 回归报告生成
│   │   │   └── datasets/               # 黄金数据集
│   │   │       └── golden_qa.json
│   │   │
│   │   ├── api/                        # FastAPI 路由
│   │   │   ├── routes/
│   │   │   │   ├── chat.py
│   │   │   │   ├── conversations.py
│   │   │   │   └── eval.py
│   │   │   └── server.py               # FastAPI app 入口
│   │   │
│   │   └── cli/                        # CLI 入口
│   │       ├── main.py                 # 主命令
│   │       ├── chat.py                 # 交互聊天子命令
│   │       └── eval.py                 # Eval Mode 子命令
│   │
│   ├── tests/                          # 测试
│   │   ├── unit/                       # 单元测试
│   │   │   ├── agents/
│   │   │   ├── memory/
│   │   │   ├── tools/
│   │   │   └── core/
│   │   ├── integration/                # 集成测试
│   │   │   ├── test_graph_flow.py
│   │   │   └── test_storage.py
│   │   ├── e2e/                        # 端到端（接真实 API）
│   │   │   └── test_eval_mode.py
│   │   ├── fixtures/                   # 测试夹具
│   │   │   ├── llm_stubs.py            # LLM Mock
│   │   │   └── golden_qa.json          # 黄金数据集
│   │   └── reports/                    # 测试报告输出（gitignore）
│   │
│   ├── data/                           # 运行时数据（gitignore）
│   │   ├── conversations/              # 会话 JSON
│   │   ├── index.json                  # 会话索引
│   │   ├── traces/                     # 本地 trace 文件
│   │   ├── eval_logs/                  # eval 指标日志
│   │   └── logs/                       # 运行日志
│   │
│   ├── config.yaml                     # 配置文件
│   ├── .env.example                    # 环境变量示例
│   ├── pyproject.toml                  # 依赖与项目元数据
│   └── README.md                       # 后端启动说明
│
├── frontend/                           # 前端（Phase 3）
├── deploy/                             # 部署配置（用户自维护 Docker）
│   └── .gitkeep
├── .gitignore
├── .env.example
└── README.md
```

**目录约定**：

- `app/` 下按"职责"分目录，不按"层次"分（避免 MVC 式臃肿）
- 每个 Agent 一文件 + 一 Prompt 文件，一一对应便于维护
- `data/` 全部内容 gitignore，仅保留 `.gitkeep`
- `tests/` 镜像 `app/` 结构，便于定位
- 测试报告 `tests/reports/` 也 gitignore

***

## 五、LLM 角色分配策略

| Agent 角色         | 模型                  | Temperature | 选型理由                        | 预估成本占比 |
| ---------------- | ------------------- | ----------- | --------------------------- | ------ |
| **Supervisor**   | `deepseek-chat`     | 0.1         | 简单分类任务，强制 JSON 输出，要快、要稳、要便宜 | 低      |
| **Planner**      | `deepseek-reasoner` | 0.2         | 复杂任务需深度推理拆解，CoT 能力强         | 中      |
| **Executor**     | `deepseek-chat`     | 0.3         | function calling 稳定，工具调用响应快 | 低      |
| **Critic**（默认）   | `deepseek-chat`     | 0.0         | Phase 1 默认 chat 省钱，结构化评分    | 低      |
| **Critic**（复杂任务） | `deepseek-reasoner` | 0.0         | 任务复杂度高于阈值时切换 R1，提升幻觉检出      | 中      |
| **Scribe**       | `deepseek-chat`     | 0.2         | 标准摘要 + 重要性打分任务              | 低      |
| **chat\_simple** | `deepseek-chat`     | 0.7         | 闲聊直通，跳过 P/D 节点              | 低      |

**模型切换策略**：

- Critic 的模型切换由 `ReflectionPolicy.select_model(state)` 决定
- 任务复杂度由 Planner 在 Task List 中标注（`complexity: 0~1`）
- 切换阈值由 `config.yaml` 配置，默认 `0.7`

**降级链**：

- `deepseek-reasoner` 不可用 → 降级到 `deepseek-chat` + 日志告警
- `deepseek-chat` 不可用 → 触发限流/熔断，返回降级提示
- 触发日预算上限 → 强制全链路降级到 `deepseek-chat`

***

## 六、架构决策记录（ADR）

### ADR-001: 后端使用 Python + LangGraph 而非 Node.js + LangGraph.js

- **状态**：已采纳
- **日期**：2026-08-12
- **背景**：用户初始需求提到"聊天页面 Node.js"，但又指定"用 LangChain、LangGraph 构建 Agent"。
- **决策**：Agent 后端用 Python（FastAPI + LangGraph），Node.js 仅做前端 BFF/静态服务层。
- **理由**：
  1. LangGraph Python 版本是主推生态，多 Agent/反思/回滚能力最完整
  2. LangGraph.js 功能滞后、社区资源少，反思型工作流踩坑成本高
  3. RAG、向量库、评估工具链（Ragas/LangSmith）Python 标准栈最完整
  4. FastAPI 异步性能足够，与前端通过 HTTP 解耦
- **后果**：前后端分语言，需通过 HTTP API 通信。但解耦后前端可独立迭代。

### ADR-002: 联网搜索使用博查 API 而非 Tavily

- **状态**：已采纳
- **日期**：2026-08-12
- **背景**：Tavily 是 LLM 友好的搜索 API，但属国外服务。
- **决策**：使用博查搜索 API（国内直连），自封装为 MCP Server。
- **理由**：
  1. 用户明确要求"尽量用国内平台、不翻墙"
  2. 博查提供 AI 搜索摘要能力，与 Tavily 对等
  3. 自封装 MCP Server 约 50 行代码，不增加显著维护成本
- **后果**：博查暂无官方 MCP，需自行实现 `bocha_server.py`。

### ADR-003: 默认联网增强，RAG 作为辅助信号

- **状态**：已采纳
- **日期**：2026-08-12
- **背景**：传统 RAG 系统默认走内部知识库，仅知识库缺失时联网。
- **决策**：反转默认行为——默认联网，仅用户明确要求"基于已有知识"时才走纯 RAG。
- **理由**：
  1. 用户明确要求"除非严格要求基于已有知识做答复，否则所有回答都应联网查询"
  2. 个人知识库覆盖度有限，联网能保证信息时效性
  3. Supervisor 仍做 Top-3 预检索，命中度作为辅助信号传给后续节点
- **后果**：联网 API 调用成本增加，需通过 `cost_control` 限制。Phase 1 知识库尚未建立，预检索命中度为 0，等价于全联网。

### ADR-004: 三层记忆架构 L1/L2/L3

- **状态**：已采纳
- **日期**：2026-08-12
- **背景**：需同时解决"长期记忆溢出"和"短期记忆不足"两个问题。
- **决策**：三层记忆架构，Phase 1 仅实现 L1。
  - L1 短期（Working Memory）：滑动窗口 + 触发压缩
  - L2 中期（Session Memory）：跨会话偏好，Phase 2 实现
  - L3 长期（Knowledge Base）：向量库 + 结构化，Phase 2 实现
- **理由**：
  1. 分层避免长记忆全量拼 prompt 导致溢出
  2. L3 走检索召回 top-k，永不整库加载
  3. L1 不足时自动从 L3 检索补全
- **后果**：Phase 1 仅 L1，需保证接口抽象允许 Phase 2 平滑接入 L2/L3。

### ADR-005: 反思每次必做，策略接口可扩展

- **状态**：已采纳
- **日期**：2026-08-12
- **背景**：反思是质量保障核心，但每次反思增加成本与延迟。
- **决策**：Phase 1 默认 `AlwaysReflectStrategy`，但策略做成接口。
- **理由**：
  1. 用户明确要求"反思每次对话都需要"
  2. API 费用后续可能调整反思频次，需预留扩展点
  3. 策略模式零成本扩展 `Adaptive`/`Sampling` 策略
- **后果**：每次对话多一次 LLM 调用。Phase 1 默认 Critic 用 `chat` 控制成本。

### ADR-006: 外部工具走 MCP，本地高频组件直连

- **状态**：已采纳
- **日期**：2026-08-12
- **背景**：MCP 是 Anthropic 推出的标准协议，但本地高频调用走 MCP 有额外开销。
- **决策**：
  - 外部工具（联网搜索、文件解析、后续扩展）→ MCP 标准协议
  - 本地高频组件（向量检索、JSON 存储）→ Python 直接调用
- **理由**：
  1. MCP 提供标准化接口，外部工具可替换、可分布式部署
  2. 向量检索是延迟敏感型，毫秒级响应不能走 IPC
  3. 存储/配置/日志等基础设施本就不应走 MCP
- **后果**：工具层有两套调用方式，需在 `tools/registry.py` 统一注册。

### ADR-007: 配置驱动一切可调参数

- **状态**：已采纳
- **日期**：2026-08-12
- **背景**：项目有大量可调参数（模型、阈值、策略、成本上限）。
- **决策**：所有可调参数集中在 `config.yaml`，业务代码只读配置不硬编码。
- **理由**：
  1. 用户明确要求"盲点做成配置文件可调"
  2. 配置驱动便于 A/B 测试与灰度
  3. 强类型 Pydantic Settings 保证配置校验
- **后果**：业务代码所有节点需注入 config 对象，构造函数略复杂。

### ADR-008: Tracing 优先 LangSmith，失败降级本地 JSON

- **状态**：已采纳
- **日期**：2026-08-12
- **背景**：LangSmith 是 LangChain 官方 tracing，集成最深，但属国外服务。
- **决策**：先尝试 LangSmith，连不上自动降级到本地 JSON trace。
- **理由**：
  1. LangSmith 功能最强，能直接可视化 LangGraph 节点流转
  2. 用户希望先试 LangSmith
  3. 降级机制保证国内网络环境也能正常运行
- **后果**：需实现 tracing provider 抽象与降级检测逻辑。

***

## 七、跨阶段演进策略

为保证 Phase 1 代码平滑演进到 Phase 2/3/4，所有跨阶段能力必须通过接口抽象：

| 能力      | Phase 1      | Phase 2/3/4       | 抽象接口                  |
| ------- | ------------ | ----------------- | --------------------- |
| 存储后端    | 本地 JSON      | PostgreSQL        | `StorageBackend`      |
| 记忆 L2   | 不实现          | Redis             | `SessionMemory`       |
| 记忆 L3   | 不实现          | 向量库               | `KnowledgeBase`       |
| 多用户     | 单用户          | 多用户               | `user_id` 字段预留        |
| 鉴权      | 无            | JWT               | FastAPI dependency 预留 |
| Tracing | LangSmith/本地 | + Langfuse 自部署    | `TracingProvider`     |
| 工具      | 博查 MCP       | + 文件解析 MCP + 向量直连 | `ToolRegistry`        |

**演进原则**：Phase 1 接口必须设计完整（签名+docstring），但实现可以是 `raise NotImplementedError`。Phase 2 实现时不允许修改接口签名。

***

## 八、可观测性三件套

### 1. Logging（结构化日志）

- 所有日志 JSON Lines 格式，写入 `data/logs/app_YYYY-MM-DD.jsonl`
- 每条日志必含 `trace_id`、`conv_id`、`timestamp`、`level`、`event`、`payload`
- 用 `structlog` 或 `python-json-logger`

### 2. Metrics（量化指标）

- 每次对话结束输出 9 项 eval 指标（详见 [03-evaluation-and-testing.md](./03-evaluation-and-testing.md)）
- 写入 `data/eval_logs/metrics_YYYY-MM-DD.jsonl`
- 每条指标含 `value`、`meaning`、`interpretation`、`status`

### 3. Tracing（链路追踪）

- LangSmith 在线可视化（首选）
- 降级到本地 JSON trace 文件 `data/traces/{trace_id}.json`
- 每个 trace 记录所有节点的输入、输出、token、延迟

**三者通过** **`trace_id`** **串联**，任一问题可从指标告警反查 trace 再反查日志。
