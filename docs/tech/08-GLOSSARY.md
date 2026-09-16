---
title: 术语表（Glossary）
layer: 宪法层
owner: SEKB Team
status: active
version: v1.0.0
last-updated: 2026-09-16
based-on-commit: 44dfed1
related: [00-README, 01-ARCHITECTURE, 02-RUNTIME-FLOWS]
---

# 08 · 术语表（GLOSSARY）

> **本文回答什么问题**：本项目用到的术语统一指什么？哪些词容易混用、必须区分？
> **适合谁读**：所有人。本文是全文术语权威，其它文档必须与本文一致。
> **读完能做什么**：阅读/讨论/写文档时用词一致，避免同义混用。

---

## 1. 阅读说明

- 每条术语格式：定义 → 本项目所指 → 代码落点 → 相关术语。
- 「禁止混用清单」见文末 §5，是必须遵守的强制区分。

---

## 2. 核心概念

### 2.1 SEKB / SelfEvolvingKnowledgeBase
- **定义**：自迭代个人知识库 Agent——知识库 + 多 Agent 助手，核心哲学是 PDCA 工程化落地。
- **本项目所指**：仓库整体产品名；`backend/config.yaml:12` `app.name: self-evolving-kb`。
- **代码落点**：backend/config.yaml:12。

### 2.2 PDCA 循环（Plan-Do-Check-Act）
- **定义**：质量改进循环；本项目把它映射为 Agent 编排：Plan→Supervisor/Planner，Do→Executor，Check→Critic，Act→Scribe（知识沉淀）。
- **代码落点**：docs 01 §3；backend/app/graph/builder.py:305-407（节点与边装配，节点定义 builder.py:316-320）。

### 2.3 意图（Intent / IntentType）
- **定义**：对用户输入的分类决定后续路由。
- **本项目所指**：六类——`chitchat` / `kb_strict` / `kb_prefer` / `web_default` / `task_plan` / `clarify`。
- **代码落点**：state.py:26-33；route_after_supervisor builder.py:63-80。
- **相关术语**：RAG 模式（strict/prefer/auxiliary/disabled 是检索模式而非意图）。

### 2.4 反思（Reflexion）与 重规划（Re-plan）——**禁止混用**
- **定义**：反思=Critic 对答案质量的自评（groundedness/coherence/relevance）；重规划=不通过时回到 Planner 重新拆解。
- **区分**：反思是「检查并给出结论」；重规划是「结论为 needs_replan 时的循环动作」。二者不是同义词。
- **代码落点**：ReflectionResult(state.py:45-49：PASS/NEEDS_REPLAN/NEEDS_REWRITE)；critic → planner 边(builder.py:393-398)。

### 2.5 Agent 五角色（Supervisor/Planner/Executor/Critic/Scribe）
- **定义**：见 01-ARCHITECTURE §3 与 03-MODULES §2；图节点与 Agent 类一一对应。
- **常见误解**：Scribe 不是「抄写答案」，而是负责最终回答组织 + 摘要 + 重要度评分 + 知识沉淀决策。

### 2.6 会话（Conversation）vs 图任务实例
- **本项目所指**：Conversation = 用户在侧边栏的持久化会话（JSON 文件，含消息历史）；每次发消息运行一次 LangGraph 图（GraphState 是请求级临时对象，不持久化）。
- **代码落点**：JSONStorage conversation 文件；create_initial_state(state.py:194) 请求级。
- **常见误解**：后端没有"会话级 Agent Task 实例"概念；GraphState 生命周期 = 单次请求。

---

## 3. 记忆与知识

| 术语 | 定义 | 本项目落点 | 生命周期 | 代码落点 |
|------|------|-----------|---------|---------|
| **L1 短期记忆**（Short-term） | 工作记忆：当前对话上下文 | ShortTermMemory 纯内存 dict，超长压缩为摘要 | 请求内填充，进程重启丢失 | short_term.py:80-84 |
| **L2 中期记忆**（会话/偏好） | 跨会话偏好与近期话题 | RedisSessionMemory（Redis，懒连接，失败不阻断） | 已启用（2026-09-10） | config.yaml:120-125；bootstrap.py:199-209；session_memory.py |
| **L3 长期知识库** | 向量库持久知识 | ChromaDB `knowledge` 集合，user 隔离 | 持久（磁盘） | knowledge_base.py |
| **RAG 检索模式** | 检索策略分级 | strict/prefer/auxiliary/disabled（按意图选） | — | retriever.py:120-214（mode 赋值 158-175） |
| **知识自迭代（Ingest）** | 高质量对话自动入库 | KnowledgeIngester：事实提取→冲突检测→版本 | 对话后 await 执行 | knowledge_ingestor.py |

---

## 4. 前端 / 协议术语

| 术语 | 定义 | 本项目所指 | 代码落点 |
|------|------|-----------|---------|
| **思考事件 thinking** | SSE 中表示进度 | 节点开始时推送「正在…」文案 | chat.py:500-514；on_chain_start chat.py:217-220 |
| **Token 事件 token** | SSE 中答案逐字 | executor 流式生成（真流式，token sink）回传 | executor.py:375-390；core/token_sink.py |
| **流式状态（per-conv）** | 前端按会话隔离的 stream | `streamingByConv`（content+thinking） | stores/chat.ts |
| **队列（queueByConv）** | 前端按会话排队 | 会话流式期间新输入入队 | stores/chat.ts |
| **access_level** | 白名单两级访问 | `full` / `preview` | app/core/access.py |
| **Temp 会话** | 前端新建会话占位 ID | `temp_xxx`，首条回复后迁移真实 ID | stores/chat.ts |

---

## 5. 缩写展开与禁止混用清单

### 5.1 缩写
| 缩写 | 展开 | 说明 |
|------|------|------|
| RAG | Retrieval-Augmented Generation | 检索增强生成 |
| PDCA | Plan-Do-Check-Act | 见 §2.2 |
| MCP | Model Context Protocol | 招聘分析内核 jobcopilot 的 stdio 工具协议（7 工具：analyze_job / analyze_jobs_batch / self_check / get_profile / save_profile / list_prompt_packs / sync_prompts，mcp_client.py:41-53）；联网搜索走博查 HTTP API，不经 MCP |
| CoT | Chain-of-Thought | 思维链（reasoner 隐含） |
| TTFT | Time To First Token | 首 token 延迟 |
| SSE | Server-Sent Events | 单工流式 |
| JWT | JSON Web Token | 登录态；有效期 `token_expire_hours`（`config.yaml:302`，2026-09-16 起 **168h=7 天**；`config.py` 默认同为 168），前端静默滑动续租 |
| SLO/SLI | Service Level Objective/Indicator | 服务目标/指标 |
| Groundedness | 锚定度 | 防幻觉评分 |
| ChromaDB | — | 向量库 |

### 5.2 禁止混用清单（强制区分）
| 词 A | 词 B | 区别 |
|------|------|------|
| 反思（Reflexion） | 重规划（Re-plan） | 反思是评估动作；重规划是循环动作（§2.4） |
| 意图（Intent） | RAG 模式（mode） | 意图是 6 类路由；mode 是 4 档检索强度 |
| 会话（Conversation） | 图任务（Graph run） | 前者持久；后者单次请求 |
| 草稿答案（draft_answer） | 最终答案（final_answer） | Executor 产出草稿；Scribe 产出最终 |
| 短期记忆（L1） | 长期知识库（L3） | 内存 vs 向量库；生命周期不同 |
| ~~skill 技能模式~~ | 任务规划（plan） | skill **已整体移除**（2026-09：前端技能按钮 `c9d948a` + 后端分支 `daa76bd`）；plan 是图内环节 |
| thinking（思考事件） | token（答案事件） | SSE 两种不同事件类型 |

---

## 相关文档

- [00-README.md](./00-README.md)
- [01-ARCHITECTURE.md](./01-ARCHITECTURE.md)
- 事实表：[.facts/T4-agent-state.md](./.facts/T4-agent-state.md)
