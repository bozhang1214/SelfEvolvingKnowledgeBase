# SelfEvolvingKnowledgeBase 工程分析报告

> 分析日期：2026-08-13 ｜ 分析范围：`backend/` 全部 51 个 `.py` 文件 + `config.yaml` + `docs/` 设计文档

---

## 一、工程概述

**SelfEvolvingKnowledgeBase（自迭代个人知识库 Agent）** 是一个具备"元认知"能力的个人知识助手，核心哲学是 **PDCA 循环**的工程化落地：

| 阶段 | 对应模块 | 职责 |
|---|---|---|
| **Plan** | Supervisor / Planner | 意图识别、任务拆解 |
| **Do** | Executor | 调用工具（联网搜索/知识库检索/LLM 生成） |
| **Check** | Critic | 多维度反思评估，不通过则回滚重试 |
| **Act** | Scribe | 对话蒸馏为长期记忆，实现知识库自迭代 |

工程处于 **Phase 1**（文档标"设计评审中"，但代码已基本成型），已完成：多 Agent 聊天核心、L1 短期记忆、本地 JSON 存储、评估体系、CLI/FastAPI 双入口。L2/L3 记忆、RAG 知识库、多用户、部署属于后续阶段占位。

---

## 二、目录架构总览

```
backend/
├── app/
│   ├── agents/          # 5 个 Agent + 工厂 + 反思策略 + Prompt
│   │   ├── base.py / supervisor.py / planner.py / executor.py / critic.py / scribe.py
│   │   ├── factory.py          # 依赖注入创建全部 Agent
│   │   ├── strategies/         # 反思策略（base/always/factory）
│   │   └── prompts/templates.py
│   ├── api/             # FastAPI 入口
│   │   ├── server.py           # 应用工厂 + lifespan
│   │   └── routes/chat.py / conversations.py / health.py
│   ├── cli/             # Typer CLI（main/chat/eval）
│   ├── core/            # config / llm_factory / bootstrap / logging / exceptions / tracing
│   ├── eval/            # 评估体系（runner/assertion/metrics/reporter）
│   ├── graph/           # LangGraph 工作流（builder/state）
│   ├── memory/          # L1 短期记忆（short_term）
│   ├── storage/         # 本地 JSON 存储
│   └── tools/           # 工具层（registry + MCP 客户端 + 博查搜索 server）
├── tests/               # 单元测试（16 个 py）
├── config.yaml          # 全量配置（模型/记忆/反思/成本/日志）
├── pyproject.toml / requirements.txt
└── docs/                # 7 篇设计文档（架构宪法）
```

---

## 三、核心技术架构

### 3.1 多 Agent LangGraph 工作流（`graph/builder.py` + `graph/state.py`）

```
START → Supervisor ──chitchat──→ chat_simple ──┐
            │           └─clarify──→ clarify ──┤
            └──── 其他意图 → Planner → Executor → Critic ──通过──→ Scribe → END
                                                    └──重规划（≤max_replan）→ Planner ↺
```

- `GraphState`（TypedDict）承载全流程状态：`user_input / intent / task_steps / draft_answer / final_answer / evaluation / metrics / errors / replan_count` 等
- 条件路由：`route_after_supervisor`（意图分流）、`route_after_critic`（反思结果分流，注入 `ReflectionStrategy`）
- `create_initial_state()` 预置初始字段
- 意图类型：`chitchat / kb_strict / kb_prefer / web_default / task_plan / clarify` 六类

### 3.2 五个 Agent 节点

| Agent | 模型角色 | 核心逻辑 | 降级策略 |
|---|---|---|---|
| **Supervisor** | chat | 意图识别 + 置信度 < 0.7 强制转 clarify | 解析失败默认 web_default |
| **Planner** | reasoner | 任务拆解为 TaskStep；无步骤时兜底单步 llm_generate | — |
| **Executor** | chat | 顺序执行 web_search / rag_retrieve / llm_generate，生成草稿 | — |
| **Critic** | chat/reasoner 切换 | 三维评估（groundedness/coherence/relevance），复杂度超阈值用 reasoner | 失败降级"直接通过" |
| **Scribe** | chat | 摘要(≤200字)/重要性评分/持久化判断 + 汇总 ConversationMetrics | 异常时降级 draft_answer，空指标 |

- `base.py` 提供 `_parse_json_response()` 三级容错 JSON 解析：直接解析 → 提取代码块 → 截取花括号子串
- `factory.py` 统一依赖注入（llm_factory/config/tool_registry/memory）
- `strategies/factory.py`：`adaptive`/`sampling` 均降级为 `AlwaysReflectStrategy`（代码注释明确 Phase 2 扩展）

### 3.3 LLM 工厂（`core/llm_factory.py`）

- 按角色缓存实例（`deepseek-chat` 5 个角色 + `deepseek-reasoner` 2 个角色）
- **reasoner → chat 自动降级**（实例创建失败时）
- tenacity 重试：仅对 `LLMTimeoutError / LLMRateLimitError` 指数退避重试（max_retries+1 次）
- 全局累计统计 `LLMCallStats`（token/成本/延迟/成功率/reasoner 占比）

### 3.4 L1 短期记忆（`memory/short_term.py`）

- 纯内存 dict（`_messages` / `_summaries` / `_conv_users`）
- 滑动窗口：保留最近 `max_turns×2` 条
- tiktoken（cl100k_base）token 计数
- **层次化压缩**：超 `max_tokens` 时 LLM 压缩旧消息为摘要，摘要数超 `max_compressed_summaries` 时合并最早两条

### 3.5 工具层（`tools/registry.py` + `tools/mcp/`）

- 博查 web_search 优先走 **MCP 子进程协议**（stdio），初始化失败降级为直接封装博查 REST API
- `MCPClient` 支持 stdio/SSE 双模式；`bocha_server.py` 是 MCP Server 侧实现
- filesystem / vector_store / document_parser 均 `enabled: false`（Phase 2 占位）

### 3.6 评估体系（`eval/`）

- `runner.py`：黄金数据集批跑 LangGraph，断言引擎校验
- `assertion.py`：自定义断言
- `metrics.py`：从 state 提取 ConversationMetrics、阈值分级（good/warn/bad）、JSONL 落盘、均值/中位数/P95 聚合
- `reporter.py`：Markdown 回归报告（含 pass_rate_drop 回归检测）

### 3.7 双入口

- **FastAPI**：`POST /api/v1/chat/`（非流式）+ `/api/v1/chat/stream`（SSE）+ 会话 CRUD + health 探针
- **CLI**：`chat`（交互式）、`eval`（跑评估）、`health`（检查）

### 3.8 其他基础设施

- `core/config.py`：Pydantic 配置 + `${ENV_VAR}` 展开 + lru_cache 单例
- `core/logging.py`：structlog JSON 结构化日志、敏感字段脱敏、文件轮转、trace_id 上下文绑定
- `core/tracing.py`：LangSmith 优先，失败降级本地 JSONL collector
- `core/exceptions.py`：`SEKBError` 异常体系（LLMError/AgentError/ConfigError/MemoryError/EvaluationError…）
- `storage/json_storage.py`：`asyncio.to_thread` + 原子写入（tmp + os.replace）

---

## 四、问题清单（按严重度分级）

### 🔴 P0 — 高严重度（影响正确性/可靠性）

**1. Metrics 使用全局累计统计，跨会话/跨请求污染**
`scribe.py:170` 的 `_build_metrics()` 直接读 `self.llm_factory.stats`——这是服务启动以来的**全局累计值**。多用户并发、多轮对话下，`total_input_tokens / total_cost_usd / model_used` 反映的是全部历史调用的总和，而非本轮对话。**评估体系（eval）基于此产生的单轮指标完全失真**，成本统计也会随服务运行时间无限膨胀。

**2. LangGraph 无全局异常兜底，工作流可能整体中断**
各 Agent 采用 `except AgentError: raise`（如 `scribe.py:125`），而 `_run_chat` 调用 `ctx.graph.ainvoke(state)`（`chat.py:158`）仅 `finally clear_context()`，无异常捕获。一旦 Supervisor/Planner/Executor 抛出 `AgentError`（如 LLM 解析失败、角色配置缺失），整个工作流中断，用户收到 HTTP 500 而非任何形式的降级回复。对比之下 `chat_simple_node`/`clarify_node` 都有 try/except 降级，五个正式 Agent 反而没有。

**3. LLM 重试覆盖面不足**
`llm_factory.py:198-207` 的 `_call()` 中：`TimeoutError→LLMTimeoutError`（重试）、`429/rate limit→LLMRateLimitError`（重试）、**其余一切异常直接 `raise LLMError`（不重试）**。这意味着 5xx 服务器错误、连接重置、余额不足（402）、token 超限（413）等暂时性/可恢复错误**一次都不重试**就上抛，加上问题 2 无兜底，会直接击穿工作流。

### 🟠 P1 — 中严重度（影响扩展性/准确性/体验）

**4. 多 worker 部署下 L1 记忆完全不一致**
`ShortTermMemory` 是纯内存 dict。一旦 uvicorn 多 worker / 多进程部署（文档规划面向线上生产），会话 A 的请求落在 worker1、下次落在 worker2，记忆即丢失。且 storage 恢复逻辑（`chat.py:129-142`）仅在 memory **为空**时触发——进程重启后虽能从 storage 全量恢复，但**压缩摘要（`_summaries`）不持久化**，且恢复后立即全量重压缩，浪费 LLM 调用。

**5. reasoner 降级后统计失真**
`ainvoke_with_stats` 中 `model_name = role_config.model`（`llm_factory.py:178`）取的是**原始配置名**。reasoner 实例创建失败降级为 chat 后，统计仍记作 reasoner，`_calculate_cost` 也按 reasoner 单价计价——成本虚高、`reasoner_ratio` 失真，进而污染问题 1 中的全局统计。

**6. "已配置未执行"的占位功能（Phase 2 预留）**
- `kb_strict / kb_prefer` 意图已定义、`kb_strict_keywords / kb_prefer_hit_threshold` 已配置，但 `rag_retrieve` 工具**未实现**（返回空占位），两类意图实际行为与 `web_default` 无异；
- `reflection.adaptive / sampling` 策略在工厂中**降级为 always**（代码注释注明 Phase 2）；
- `cost_control`（`daily_budget_usd` / `per_conversation_token_limit` / `auto_downgrade_on_budget`）配置齐全，但**代码中无任何位置消费这些配置**——预算检查、自动降级从未执行。

**7. SSE 流式是"伪流式"**
`/stream` 端点先 `await _run_chat()` **完整跑完整个 LangGraph**（数秒~数十秒），再 `_stream_tokens` 按**字符**逐字推送（`chat.py:213-222`）。首 token 延迟 = 全量端到端延迟，流式体验形同虚设。真正的流式应在 Executor 阶段以 `streaming=True` 逐步产出。

**8. clarify 流程无闭环**
Supervisor 识别出需要澄清后，`clarify_node` 仅提问一次即结束流程（→ Scribe → END），**没有追问状态回写、也没有"用户回答后重新识别意图"的二次循环**。低置信度问题得不到二次确认，多轮澄清交互能力缺失。

**9. `e2e_latency_ms` 恒为 0，评估指标失真**
`scribe.py:190` 中 `"e2e_latency_ms": 0` 注释"由外层 wrapper 填充"，但 `_run_chat` 虽然计算了 `latency_ms`（`chat.py:162`）却**从未写回 metrics**。评估体系里的延迟指标永远是 0，`evaluation.metrics.e2e_latency_ms` 的 good/warn 阈值（5s/10s）形同虚设。

### 🟡 P2 — 低严重度（代码卫生/一致性）

**10. 死代码**：`chat_simple_node` 与 `clarify_node` 中 `llm = llm_factory.get("chat_simple")` 取到实例但从未使用（`builder.py:121, 162`），实际调用走 `ainvoke_with_stats`。

**11. 本地 trace collector 未接入**：`setup_tracing()` 返回的 `LocalTraceCollector` 未被 bootstrap 保存使用，LangSmith 失败后的降级追踪实际可能未生效。

**12. 配置语义不一致**：`retry_backoff_seconds: [1, 2]` 列表仅用 `[0]` 作为退避下限，第二值闲置；`security.prompt_injection_guard / blocked_patterns`、`api.rate_limit`、`tracing.sample_rate` 等配置项在代码中未见消费点。

**13. 阈值方向判定 hack**：`evaluate_threshold` 用 `good <= warn` 推断指标方向（`metrics.py:218`）。`plan_step_count`（good=5, warn=8）按此逻辑被判定为"越小越好"，但 3 步拆解是好事却会被判 BAD——**方向语义与直觉相反**，`replan_count` 同理，需人工记住翻转规则，易错。

**14. CLI 与 API 重复实现**：`cli/chat.py` 与 `api/routes/chat.py` 各自实现完整聊天流程（会话创建/记忆加载/持久化），无共享抽象层，后续演进（如加预算检查）需改两处。

**15. 内存复合操作无并发保护**：`compress_if_needed` 是"读取→LLM 调用→改写"的异步复合操作，同一会话并发请求可能产生竞态（摘要重复合并/消息丢失）。单线程 asyncio 下风险较低，但多 worker 下问题 4 放大了该风险。

---

## 五、优化建议（按优先级排序）

| 优先级 | 建议 | 解决 |
|---|---|---|
| **P0** | 为 `LLMFactory` 增加 **per-request stats 快照**：`_run_chat` 开头记录基线 `(total_calls, tokens, cost)`，Scribe 计算时取差值，或改用 contextvar 绑定的会话级计数器 | 问题 1 |
| **P0** | 在 `_run_chat` 的 `ainvoke` 外包一层全局兜底：捕获 `AgentError/LLMError` → 返回含降级文案 + `errors` 的响应；或给图增加统一的 `END` 前降级节点 | 问题 2 |
| **P0** | `_call()` 中把 5xx / 连接错误 / 402 / 413 也映射为重试类异常（`LLMRateLimitError` 或新增 `LLMRetryableError`），并给十次重试后再失败提供 chat 降级 | 问题 3 |
| P1 | L1 记忆持久化：压缩摘要随会话写入 storage；恢复时优先加载摘要；为后续 Redis 缓存预留接口 | 问题 4 |
| P1 | 统计时读取**实际缓存实例**的模型名（`self._cache[role].model_name` 或降级时回写），保证计价与占比真实 | 问题 5 |
| P1 | 落地 Phase 2 最小版：实现 `rag_retrieve`（ChromaDB）或明确禁用 kb 意图；实现成本预算检查（每会话 token 上限 + 日预算熔断）；为 adaptive/sampling 策略实现真实判定逻辑（`skip_if_rag_hit_above` 等配置已就绪） | 问题 6 |
| P1 | 流式改造：LangGraph 各节点以 `astream` 驱动，Executor 阶段 LLM `streaming=True` 边生成边推送；SSE 事件类型区分 `token/thinking/done` | 问题 7 |
| P1 | clarify 闭环：回写 `clarification_question` 到 state，增加"澄清→再次 Supervisor"的条件边 | 问题 8 |
| P1 | 在 `_run_chat` 计算 latency 后回填 `metrics["e2e_latency_ms"]`（一行代码） | 问题 9 |
| P2 | 清理死代码、统一配置消费点（安全守卫/采样率）、修正阈值方向判定（显式声明 direction 字段）、提取 CLI/API 共享的 chat service 层 | 10-15 |

---

## 六、结论

**工程优点**：设计文档先行、配置驱动、接口预留清晰（MCP 优先 + 直连降级）、异常体系与 JSON 容错解析健壮、评估体系完整（黄金集 + 断言 + 回归检测 + 指标聚合）、日志可观测性（trace_id + structlog）扎实。作为一个 Phase 1 骨架，架构边界和演进方向（L2/L3、RAG、多用户、部署）划分合理。

**核心风险**：当前的三个 P0 问题（全局统计污染、无异常兜底、重试覆盖不足）直接影响**生产可用性与数据真实性**；P1 的"配置已就绪但代码未执行"（成本控制、kb 意图、自适应反思）属于**文档承诺与实现之间的落差**，应在进入 Phase 2 前明确：要么实现，要么关闭配置项，避免配置误导。

**建议**：先修复 3 个 P0 + `e2e_latency_ms` 回填（改动量小、收益大），再进入 Phase 2 开发；开发时同步补 `tests/` 中缺失的工作流集成测试（当前以单元测试为主）。
