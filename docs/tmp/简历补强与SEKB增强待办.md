# 简历补强与 SEKB 增强待办清单（临时）

> 用途：记录「求职转型（Agent 开发岗 + Agent 产品岗）」需要补的内容，以及 SEKB 项目需要增强的功能点。
> 生成日期：2026-09-10
> 状态：临时文档，确认后归档/合并到 BACKLOG.md 或 11-EVOLUTION。

---

## 一、需要补的内容（个人知识 / 技能 / 简历材料）

### A. 开发岗（AI Agent 应用 / 平台开发）

**核心定位**：10 年+ 客户端/系统架构经验 + 近 1 年 AI Agent 工程化落地。

#### 1. 后端短板（最大缺口，用 SEKB 当练兵场补）
| 补什么 | 方式 | 对应报告考点 |
|---|---|---|
| asyncio 并发进阶 | 打开限流、并发控制、背压 | Python 异步 / 高并发 |
| Redis | L2 中期记忆真接 Redis（缓存/TTL/分布式锁） | 缓存 / 中间件 |
| PostgreSQL | 存储从 local_json 迁 PG（事务/索引/连接池） | 数据库 |
| 消息队列 | news/job 采集接 MQ 异步化（解耦/削峰） | 消息队列 / 分布式 |
| 系统设计题 | 用 SEKB 画「高并发 Agent 服务」架构图 | 系统设计 |

#### 2. 简历材料（成果物）
- [x] GitHub：`https://github.com/bozhang1214/SelfEvolvingKnowledgeBase`
- [x] 线上 Demo：`https://bos-studio.tech/sekb`
- [x] 技术博客：`https://blog.csdn.net/weixin_36848576`（近百篇 AI 学习笔记）
- [x] 专利编号：已确认 `CN114866799B`《一种数据回源的调度方法、装置及 CDN 网络》
- [ ] 目标 JD：确认后做针对性简历版本

#### 3. 简历上「暂不能写、补完才能写」的
- Redis / PostgreSQL / 消息队列（当前 SEKB 里是占位，未真用过 → 补完再上技术栈）
- AI 项目的量化成果（检索命中率、评测分数 → 等 RAGAS 评测做完再补）

---

### B. 产品岗（AI Agent 平台产品经理 · 技术型 / To B）

**核心定位**：10 年+ 技术研发 + 5 年+ 技术管理，独立打造过 AI Agent 产品，具备 AI-Native 产品实践。

#### 需要补的知识（产品侧 KB 基本空白）
1. **产品方法论**：需求分析、用户研究、竞品分析、PRD 撰写、数据分析 / AB 测试
2. **AI-Native 产品思维**：理解 Agent 交互范式（对话式 / 意图 / 工具调用 / 记忆），会设计「Agent 的体验」而非「App 的体验」
3. **Agent 生态与商业化**：MCP / A2A 协议、开发者生态、B 端 / 企业级 AI 落地商业模式（Coze / Dify / 飞书 / 钉钉智能体平台产品逻辑）
4. **案例储备**：能说清「一个 Agent 产品为什么好用/不好用」（豆包 / 扣子 / 元宝等的产品问题）

#### 需要补充的简历材料
- 产品化数据证据：SEKB 是个人产品，缺用户/增长数据 → 补充迭代记录、功能规划、职位分析报告等产出物
- 薪资已调整为 45-60K（市场产品岗 22-50K，专家可达 50K+）

---

### C. 其他待确认项
- [ ] 英文简历是否出（建议出简洁版，英文简历 ≠ 英文面试）
- [x] 优秀员工：**两家公司均评为优秀员工**（北京爱奇艺 + 中科开元），已纳入「荣誉」栏
- [x] 高绩效：爱奇艺 A++×3 / A+×10，已纳入「荣誉」栏
- [x] 电话已更新：13581761586

---

## 二、SEKB 项目需要增强的功能点

> 这些增强点「一箭三雕」：补开发岗后端短板 + 关闭报告里 foundation/highlights 的缺口 + 给简历提供可量化的项目成果。
> 状态更新：2026-09-10（1/2/3a/3b 已完成，4/5 部分完成）。

### 1. RAG 检索增强 ✅ 已完成
- BM25 关键词召回 + LLM 列表式 Rerank + 可选 Query Rewrite + RRF 融合
- 已接入 `rag_retrieval_node`（`hybrid_enabled` 控制），混合检索+BM25 生产开启，Rerank/Query Rewrite 默认关（控延迟）
- 文件：`tools/rag/{bm25,hybrid,reranker}.py`

### 2. RAGAS 式检索质量评测 ✅ 已完成
- `context_recall` / `context_precision` / `faithfulness` / `answer_relevance`（LLM-as-Judge）已实现，`sekb rag-eval` CLI 可用
- 黄金集 30 条；真机基线（纯向量）：**recall 0.512 / precision 0.468 / faithfulness 0.890 / relevance 0.762**
- 待办：开启 rerank 后重跑，产出「纯向量 vs 混合+rerank」对比数字回填简历

### 3a. Prompt 注入防护 ✅ 已完成
- `guard.py` 规则层（长度+正则）+ 可选 LLM 层；已接 `chat.py` 入口 + `share.py` 公开路径 + `knowledge_ingestor` 隔离标注
- 检索内容隔离标注（防 indirect injection）

### 3b. 反馈数据飞轮 ✅ 已完成
- thumbs 反馈 → 命中知识条目 importance 升降 → 低于阈值删除
- chat 流程已把 RAG 命中 entry_id 写进消息 meta；`conversations.py` rate 端点消费

### 4. 后端中间件补强（部分完成）
- Redis：L2 会话记忆 ✅ 已落地（`memory/session_memory.py` + `l2_session.enabled=true` + docker-compose redis 服务）
- PostgreSQL：⏸️ 暂缓（03 路线图 D7「等权限下推时一并做」；简历标注「已评估方案」）
- 消息队列：⏳ 待做（用 news/job 异步化轻量替代，不必真上 Kafka）

### 5. 其他（部分完成）
- 限流重开 ✅ 已完成（`rate_limit.enabled=true`，auth/share/upload/job/news 分组，含分享问答）
- LangSmith tracing ✅ trace_id 注入已打通（`get_trace_config` → LLM 调用 metadata/tags）；启用仍需配 LANGSMITH_API_KEY
- 多智能体并行调度（`depends_on`）⏳ 待做
- 反思 needs_rewrite 分支 ✅ 已完成（`rewrite` 节点 + critic→rewrite→critic 循环）
- JWT jti 黑名单（登出吊销）✅ 已完成

---

## 三、执行顺序建议

1. RAG 检索增强（接线）
2. RAGAS 评测（依赖 1 的检索产出做对比）
3. 3a 注入防护 + 3b 反馈飞轮
4. 后端补强（Redis → PG → MQ）
5. 用评测数字回填简历

> 产品岗知识（产品方法论 / AI-Native 思维 / 生态商业化）与 SEKB 开发增强并行推进，互相独立。
