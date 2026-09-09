---
title: 模块详解（Modules）
layer: 设计层
owner: SEKB Team
status: active
version: v1.0.0
last-updated: 2026-09-09
based-on-commit: 1ffb13c
related: [01-ARCHITECTURE, 02-RUNTIME-FLOWS, 05-API-REFERENCE, 08-GLOSSARY]
---

# 03 · 模块详解（MODULES）

> **本文回答什么问题**：每个模块/子域干什么、怎么实现、有哪些坑？
> **适合谁读**：所有开发者（按自己负责模块查阅）。
> **读完能做什么**：定位模块职责/接口/依赖/配置/陷阱，知道扩展点与已知问题。

> 各模块字段级细节见对应事实表（T3 LLM 调用 / T4 State / T5 工具 / T6 存储 / T7 调度 / T8 可观测）。

---

## 1. 模块总览

```mermaid
flowchart TB
    subgraph 前端
      FE[pages/stores/services]
    end
    subgraph 接入层 app/api
      R[routes ×13]
      MID[middleware/鉴权]
      SVR[server.py]
    end
    subgraph 编排 app/graph+agents
      G[graph/builder]
      AG[agents: 5 节点+策略+factory]
      KL[agents/knowledge_ingestor]
    end
    subgraph 能力 app
      MEM[memory: short_term/kb]
      STO[storage: json/user/share/news/profile]
      SV[services: classifier/series]
      EVA[eval: runner/metrics/assertion]
      SCH[scheduler]
      CLI[cli: main/chat/eval]
      MOD[models: user/share/profile/chat_share]
    end
    subgraph 工具 app/tools
      TR[registry]
      RD[rag/retriever+format]
      VS[direct/vector_store]
      FP[file/image_processor]
    end
    subgraph 核心 app/core
      CFG[config] LF[llm_factory] AUTH[auth] LOG[logging] MET[metrics] BOOT[bootstrap] TOK[token_sink] TRC[tracing] UTIL[utils]
    end
    FE --> R
    R --> G
    G --> AG
    AG --> MEM
    AG --> TR
    AG --> LF
    AG --> KL
    R --> STO
    R --> SV
    R --> SCH
    BOOT --> CFG
    BOOT --> LF
    BOOT --> G
```

---

## 2. Agent 节点与图（编排层）

### 2.1 Graph 构建器 `app/graph/builder.py`
- **一句话职责**：组装 LangGraph 状态图（节点 + 条件边 + 编译）。
- **对外**：`GraphBuilder(config, llm_factory, tool_registry, memory, reflection_strategy, vector_store)` → `build()` 返回编译图（builder.py:220-362）。
- **节点**：supervisor/planner/executor/critic/scribe + chat_simple/clarify/rag_retrieval。
- **边/路由**：见 02 §3（意图路由 builder.py:319-327；反思路由 346-353）。
- **已知问题**：chat_simple/clarify 写「假 evaluation pass」绕过反思（T4 漂移）；RAG 结果 rag_fallback_message 图内无下游消费。

### 2.2 Supervisor `app/agents/supervisor.py`
- **职责**：意图识别 + 澄清判定 + 路由参数（读 intent_routing 配置）。
- **输出**：intent / intent_confidence / needs_clarification / clarification_question / pre_retrieval_results=[]（会被 rag 节点覆盖）。
- **LLM**：角色 supervisor（deepseek-chat，JSON）。

### 2.3 Planner `app/agents/planner.py`
- **职责**：拆解任务步骤 task_steps（JSON）、任务复杂度、plan_reasoning。
- **LLM**：角色 planner（**deepseek-reasoner**）——TTFT 主因。
- **已知问题**：plan_reasoning 图内无消费（T4）。

### 2.4 Executor `app/agents/executor.py`
- **职责**：按计划执行工具（web_search/rag_retrieve/llm_generate），生成草稿答案 draft_answer。
- **流式**：token sink 存在时 `astream_with_stats` 逐 token 回传（executor.py:350-367）；失败兜底用工具结果/输入。
- **已知问题**：tool_calls 记录、execution_context 图内下游消费有限（T4）。

### 2.5 Critic `app/agents/critic.py`
- **职责**：反思评估（groundedness/coherence/relevance + issues/suggestions），产出 ReflectionResult；replan 判定（max_replan）。
- **LLM**：critic(chat) / critic_complex(reasoner，按 task_complexity 切)。
- **策略**：`reflection.policy`（always 落地；adaptive/sampling 降级回 always）。

### 2.6 Scribe `app/agents/scribe.py`
- **职责**：组织最终回答 final_answer + 摘要 + 重要度 + should_persist + metrics（含 per-request LLM delta）。
- **LLM**：scribe(chat，JSON)。
- **落点**：summary/importance_score 供知识入库决策。

### 2.7 Agent 工厂与基类
- `app/agents/factory.py`：create_all() 统一创建 5 节点（builder.py:272-279 引用）。
- `app/agents/base.py`：BaseAgent 抽象（模板方法），子类实现 __call__。

### 2.8 知识自迭代引擎 `app/agents/knowledge_ingestor.py`
- **职责**：对话 → 事实提取 → 冲突检测 → 版本/合并 → 写 ChromaDB（ingest_conversation）。
- **触发**：chat.py:591（await）、cli/chat.py:276。
- **配置**：l3_knowledge.importance_threshold 等。

---

## 3. 记忆系统（app/memory）

### 3.1 L1 短期记忆 `app/memory/short_term.py`
- 内存 dict 存储（_messages/_summaries/_conv_users），滑动窗口 + LLM 压缩摘要（角色 scribe）。**重启即失**。
- 配置：l1_working.max_turns/max_tokens/hard_token_limit/compress_strategy/max_compressed_summaries。
- 接口：get_messages/add_message/get_context/compress_if_needed。

### 3.2 L3 知识库 `app/memory/knowledge_base.py` + `knowledge_entry.py`
- ChromaDB PersistentClient + `knowledge` 集合；KnowledgeEntry 建模；retrieve/count/list 等（详见 04 §3）。
- 隔离：user_id where 过滤。
- **已知问题**：eviction 配置死配置、无自动淘汰。

---

## 4. RAG / 知识库检索

| 组件 | 位置 | 职责 |
|------|------|------|
| RAGRetriever | app/tools/rag/retriever.py | 意图→模式(strict/prefer/auxiliary/disabled)；检索参数封装 |
| format.py | app/tools/rag/format.py | 三处 RAG 上下文格式化归一（WP1）：format_rag_reference / executor / share |
| DirectVectorStore | app/tools/direct/vector_store.py | ChromaDB 直连（绕 MCP），返回 dict |
| rag_retrieval 节点 | graph/builder.py:194-241 | 图内预检索，写 pre_retrieval_results |

---

## 5. 工具层（app/tools）

| 组件 | 职责 | 细节 |
|------|------|------|
| ToolRegistry | MCP 工具注册/初始化/关闭 | 仅 web_search（博查）通过 MCP stdio（T5） |
| web_search | 联网搜索 | 博查 API；超时 10s/重试 2；MCP 失败进程内降级 |
| FileProcessor | 文档解析分块 | txt/md/pdf/docx/图片 |
| ImageProcessor | 图片 OCR + 视觉 | PaddleOCR + 独立 ChatOpenAI(qwen-vl-plus，**不经 llm_factory**，T3 漂移) |
| DirectVectorStore | 向量检索直连 | 见 §4 |

---

## 6. 存储层（app/storage）

| 组件 | 位置 | 数据 | 原子性 |
|------|------|------|--------|
| JSONStorage | json_storage.py | 会话/消息 index.json + conversations/*.json | asyncio.Lock + os.replace |
| UserStorage | user_storage.py | users.json（用户/密码 hash/access） | 无锁整表写 |
| ShareStorage | share_storage.py | shares.json + 分享目录 | 无锁 |
| ChatShareStorage | chat_share_storage.py | chat_shares.json | 无锁 |
| ProfileStorage | profile_storage.py | profile/{user_id}.json | os.replace 无锁 RMW |
| NewsStorage | agents/news/storage.py | news/*.md/.json | 直接 write_text |
| Job archive/cache | agents/job/ | 报告+缓存 | 无锁非原子 |

详见 04-DATA-MODEL §5/§6。

---

## 7. API 接入层（app/api）

- server.py：create_app/lifespan（初始化 NewsScheduler、装配）、异常处理器（SEKBError 映射 + error_id）、14 路由挂载。
- 鉴权：get_current_user（JWT）、require_full_access（白名单）、access_level。
- 中间件：RateLimitMiddleware **已实现未挂载**。
- 各 routes：见 05-API-REFERENCE。

---

## 8. 调度系统（app/scheduler）

- **NewsScheduler**：APScheduler AsyncIOScheduler；3 个 cron（日报/周报/月报，Asia/Shanghai）；随 FastAPI lifespan 启停（server.py:134-156）。
- ⚠️ 与 API 同进程承载（无独立 scheduler 容器）；`--workers 1` 前提。
- trigger_now() 死代码无调用方（T7）。

---

## 9. 横切与可观测性模块（app/core：utils/metrics/logging/tracing）

| 组件 | 说明 |
|------|------|
| utils.py | 通用工具（WP1 收敛单实现）：now_iso / fmt_dt / safe_float / to_state_dict |
| metrics.py | 23 Prometheus 指标 + record_chat_metrics/error/client_event |
| logging.py | structlog JSON + 脱敏 + contextvars |
| tracing.py | 设 env 走 LangSmith SDK；LocalTraceCollector 未接线 |
| token_sink.py | 流式 token contextvar（R2-06） |

---

## 10. CLI（app/cli）

| 命令 | 功能 |
|------|------|
| main.py | Typer app（入口 `sekb`/`python -m app.cli.main`） |
| chat.py | 交互/单发聊天（有自己的 bind_context 日志注入） |
| eval.py | 评估运行 |

---

## 11. 服务层（app/services）

| 组件 | 职责 | 说明 |
|------|------|------|
| classifier.py | LLM 分类 | 复用 supervisor 角色；**绕过统一统计入口**（T3） |
| series.py | 系列识别 | 文件名启发式 + 数值/语义 |
| browser_client.py | 通用浏览器服务客户端（sekb-browser） | WP2 从 job.py 内联 `_call_browser` 提取；可配置 base_url |

---

## 12. 评测（app/eval）

| 组件 | 说明 |
|------|------|
| runner.py | 评估执行（_get_graph 延迟导入） |
| metrics.py | 判级（读 evaluation.metrics.* 阈值） |
| assertion.py | 断言与阈值 |
| reporter.py | 报告 |
| datasets/ | golden 数据集 |

---

## 13. 模型定义（app/models）

user.py / share.py / profile.py / chat_share.py：各域 Pydantic 模型（UserProfile、ShareInfo 等），见 04 §5 与 05。

---

## 14. 前端（frontend/src）

| 层 | 内容 | 说明 |
|----|------|------|
| pages/ | Login/Register/Chat/Files/Knowledge/News/Job/Settings/Shared* | 页面路由见 App.tsx |
| components/ | Layout/AppLayout（菜单 + preview 收窄） | 共享布局 |
| stores/ | user / chat / upload | chat store 按会话隔离流式（streamingByConv/queueByConv） |
| services/ | api/auth/chat/file/job/news/share | REST + SSE（streamChat 解析 thinking/token/done/error） |
| utils/ | logger（事件上报队列）/ clipboard | 日志批处理 20/5s |
| types/ | chat/user/api | 类型契约 |

**前端调用契约**：SSE 消费、错误处理、队列/停止语义详见 02 §2 与 05 §13；store 设计要点见 CHANGELOG（任务轮）。

---

## 15. 已知问题与扩展点（跨模块）

- **扩展点**：新增 Agent 节点 → graph/builder 注册；新增工具 → ToolRegistry + 配置；新增存储 → storage 抽象。
- **跨模块陷阱**：绕过 llm_factory 的 10 个调用点（news/job/classifier/image_processor/share）无统一统计/重试；多处无锁 RMW 依赖单 worker。

---

## 相关文档

- [01-ARCHITECTURE.md](./01-ARCHITECTURE.md)
- [02-RUNTIME-FLOWS.md](./02-RUNTIME-FLOWS.md)
- [04-DATA-MODEL.md](./04-DATA-MODEL.md)
- [05-API-REFERENCE.md](./05-API-REFERENCE.md)
- 事实表：T3/T4/T5/T6/T7/T8（.facts/）
