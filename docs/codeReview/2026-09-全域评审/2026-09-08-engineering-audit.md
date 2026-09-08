# SelfEvolvingKnowledgeBase · 工程审计报告

> 审计日期：2026-09-08  
> 审计范围：全量源码（Agent 架构 / 核心服务 / 存储 / 前端 / Prompt / 运维 / 测试 / 文档）  
> 审计方法：静态代码分析 + 架构评审 + 最佳实践比对  
> 总文件数：141+ 后端 Python 文件 / 16 前端 TSX 文件 / 20+ 配置文件

---

## 总体评价

**亮点**：
- LangGraph 多 Agent 编排架构设计清晰（Supervisor → Planner → Executor → Critic → Scribe 流水线）
- 三层记忆体系（L1/L2/L3）设计完整，L3 ChromaDB 实现具备冲突检测 / 合并 / 并存策略
- LLMFactory 统计 + per-request 快照机制解决了跨会话污染问题
- 领域 Agent（JobAgent / NewsAgent）业务闭环完整，两级缓存 + 跨进程文件锁
- 可观测性工程化程度高（Prometheus + Grafana + Loki + 12 条告警规则）
- CI/CD 8 步自动部署 + 健康检查回滚 + 配置备份

**主要风险**：
- Executor 顺序执行忽略 `depends_on` 字段，任务依赖形同虚设
- SSE 流式响应在后端 `ainvoke` 完整运行后才逐字符推送，前端"伪流式"体验差
- L1 短期记忆基于进程内内存 dict，多 worker 部署下会话历史丢失
- 反思策略工厂 adaptive / sampling 未实现，全部降级为 always
- KnowledgeIngester 冲突检测仅按用户隔离，跨用户知识无法共享

---

## 一、架构级优化（跨模块，影响 ≥ 2 个目录）

### A1. Executor 任务并行执行 & 依赖拓扑排序
**位置**：[executor.py](file:///Users/zhangbo/VSCodeSpace/SelfEvolvingKnowledgeBase/backend/app/agents/executor.py) L87-L137  
**问题**：Executor 用 `for step in task_steps` 顺序执行，完全忽略 Planner 产生的 `depends_on` 字段。Planner 输出的依赖关系（如"先搜索 A → 再检索 B → 最后综合"）形同虚设。  
**影响**：多步串行延迟高（3 步 = 3×LLM 调用延迟），复杂任务响应时间线性增长。  
**改进方案**：引入拓扑排序 + `asyncio.gather` 并行执行独立步骤。伪代码：
```python
# 1. 构建依赖图
graph = {s["step_id"]: s.get("depends_on", []) for s in task_steps}
# 2. Kahn 算法分层
layers = topological_layers(graph)
# 3. 同层并行，层间串行
for layer in layers:
    results = await asyncio.gather(*[dispatch(step) for step in layer])
```
**预期收益**：3 步任务延迟从 3T → max(T1, T2, T3)，平均 40-60% 改善  
**工作量**：M（2-3 天）

### A2. SSE 真流式改造：将 LangGraph ainvoke 拆为 astream_events
**位置**：[chat.py](file:///Users/zhangbo/VSCodeSpace/SelfEvolvingKnowledgeBase/backend/app/api/routes/chat.py) L167 调用 `ainvoke` + L280-289 `_stream_tokens` 逐字符推送  
**问题**：当前"流式"是**伪流式** —— 等待整个 LangGraph 工作流（Supervisor→Planner→Executor→Critic→Scribe）完整跑完后，才把 final_answer 逐字符推给前端。用户看到的"思考中..."只是占位动画，实际等待时间 = 完整工作流耗时（30-60s）。  
**改进方案**：
1. 用 `graph.astream_events(state, version="v2")` 替代 `ainvoke`，实时获取每个节点的开始/结束事件
2. Executor 完成后立即推送"执行结果"事件
3. Critic 完成后立即推送"评估结论"事件
4. 最终 Scribe 输出 final_answer 时按 token 真流式推送（LangGraph 的 `CustomStream` 或 LLM 的 streaming=True）
5. 前端根据不同 event type 展示对应阶段的进度 UI  
**预期收益**：用户感知从"等 45s 后突然出现" → "看到 5 个节点逐个完成，最后实时出答案"  
**工作量**：L（5-7 天，涉及后端 graph 改造 + 前端事件处理重构）

### A3. L1 短期记忆持久化（多 worker 兼容）
**位置**：[short_term.py](file:///Users/zhangbo/VSCodeSpace/SelfEvolvingKnowledgeBase/backend/app/memory/short_term.py) L78-L82 `self._messages: dict[str, list[BaseMessage]] = {}`  
**问题**：L1 记忆基于进程内 dict。当前 docker-compose 单 worker 没问题，但 CI 部署文档提到 `reservation 1G/limit 4G`，若未来扩容多 worker（uvicorn workers=N），会话历史会在不同 worker 间丢失。  
**改进方案**：
1. 新增 `RedisShortTermMemory` 实现 `ShortTermMemoryBackend`
2. Phase 1 默认继续用内存，生产部署配置 `memory.l1_working.backend = "redis"` 时切换
3. 使用 Redis List 存储消息、TTL 自动过期、支持 pub/sub 跨进程同步压缩事件
4. 抽象层已就绪（base.py 的 ShortTermMemoryBackend 接口），实现层可插拔  
**预期收益**：支持水平扩容 worker；Redis 256MB/worker 配置可支撑数千并发会话  
**工作量**：M（3-4 天）

### A4. 反思策略工厂补全（Adaptive + Sampling）
**位置**：[strategies/factory.py](file:///Users/zhangbo/VSCodeSpace/SelfEvolvingKnowledgeBase/backend/app/agents/strategies/factory.py) L30-39  
**问题**：配置文件声明 `policy: adaptive / sampling` 时，工厂返回 AlwaysReflectStrategy 的降级版本。Critic 对每条对话都执行完整反思链路，高 QPS 时不必要地消耗 reasoner 模型 token。  
**改进方案**：
1. **AdaptiveReflectStrategy**：当 RAG 命中度 > 0.95 或 intent ∈ {chitchat, clarify} 时跳过反思；当 groundness_score 历史均值 > 0.85 时降低反思频率
2. **SamplingReflectStrategy**：按 config.reflection.sampling.rate（默认 0.5）概率抽样执行反思
3. 实现后在 config.yaml 中切到 adaptive，预估节省 30-50% reasoner token  
**预期收益**：token 成本下降 30-50%；高 QPS 时 P95 延迟降低  
**工作量**：S（1-2 天，逻辑简单，主要是策略阈值调优）

### A5. KnowledgeIngester 冲突检测跨用户扩展 & 知识共享策略
**位置**：[knowledge_ingestor.py](file:///Users/zhangbo/VSCodeSpace/SelfEvolvingKnowledgeBase/backend/app/agents/knowledge_ingestor.py) L526-586 `_detect_conflicts`  
**问题**：当前 `retrieve(query, user_id=user_id, ...)` 仅在当前用户知识空间内做冲突检测。如果用户 A 和用户 B 都问了相同问题，会产生两条高度相似但分属不同 user_id 的条目，无法共享学习。  
**改进方案**：
1. 新增 `shared_knowledge` 概念：当 similarity > 0.98 且来源可信（非用户私有笔记）时，检测跨 user_id 的已有条目
2. 跨用户匹配命中时，新建条目标记 `shared_from = existing_entry_id`，并在新条目的 metadata 中记录
3. RAG 检索时返回结果附带"这条知识来自其他用户但高度相关"的标注，让 Planner 决定是否使用
4. 需要在 ChromaKnowledgeBase 层面支持 `include_all_users=True` 的检索模式  
**预期收益**：知识复用率提升；避免知识库膨胀  
**工作量**：L（5-7 天，涉及向量库检索逻辑改造 + 权限模型设计）

---

## 二、Agent 层增强建议

### 可新增的 Agent 角色

| 角色 | 职责 | 价值 | 工作量 |
|------|------|------|--------|
| **ContextCompressor** | 在 Planner 之前，将 Supervisor 识别的 intent + 历史对话 + RAG 预检索结果压缩为紧凑的"任务上下文摘要"，注入 Planner 的 system prompt | 减少 Planner 的输入 token 量（预估 50%+），加快规划速度 | S |
| **ParallelCoordinator** | 对 Planner 输出的并行步骤做结果合并和冲突消解（如：两个并行 web_search 返回的矛盾信息） | 提升复杂多源检索任务的答案质量 | M |
| **ConfidenceCalibrator** | Critic 输出后，基于历史 grounding_score 分布做置信度校准（Platt Scaling / Isotonic Regression） | 让 passed/needs_replan 的决策阈值更精准，减少不必要的重规划 | M |
| **KnowledgeCurator** | 在 KnowledgeIngester 之后，对高频引用的知识条目自动提升 importance_score、添加 tags/topics、纠正分类 | 提升知识库条目质量，让 RAG 检索更精准 | S |

### 可增强的协作模式

1. **Plan-and-Execute 分解优化**：当前 Planner 一次生成所有步骤，可以改为"先规划 1-2 步 → 执行 → 观察结果 → 再规划"的动态规划模式，更适合不确定环境
2. **Critic 多维度评审扩展**：当前 Critic 只做 groundedness/coherence/relevance 三维评估，可加入：
   - **verifiability**：答案中每个关键声明是否有对应工具结果支撑（可自动提取 claim-evidence pair）
   - **completeness**：工具调用是否覆盖了用户问题的所有维度（如用户问"A vs B"，但只查了 A）
3. **Supervisor 路由增强**：当前 Supervisor 输出意图后直接路由，可以引入 IntentEmbedding + 向量检索预路由（硬编码关键词 → 嵌入相似度匹配 → LLM 二次确认），减少 LLM 路由的不稳定性

### 现有 Agent 具体改进

| Agent | 文件 | 问题 | 建议 |
|-------|------|------|------|
| **Planner** | planner.py L72-127 | 兜底只生成单步 llm_generate，丢失了 rag_retrieve / web_search 的选择 | 兜底逻辑改为：根据 intent 选择默认工具组合（kb_strict→rag_retrieve, web_default→web_search+llm_generate） |
| **Executor** | executor.py L87 | 忽略 depends_on | 见 A1 |
| **Critic** | critic.py L119-125 | `should_replan = result_str == "needs_replan"`，但 LLM 可能返回 needs_rewrite 而代码没处理 needs_rewrite 路径 | 补充 needs_rewrite → 回到 Executor（不重规划，只重生成草稿） |
| **Scribe** | scribe.py L89 | 重要性 < 0.3 强制不持久化，但有些对话虽然不重要却是用户明确要求记住的 | 加一个 `user_explicit_memorize` 标记（由前端发送），有此标记时 importance 阈值放宽 |
| **Supervisor** | supervisor.py L93-110 | needs_clarification 触发时强制意图改写为 clarify，丢失了原始意图 | 保留原始 intent 到 original_intent 字段，clarify 只是临时路由 |

---

## 三、功能增强清单

### P0 级（立即修复）

| # | 模块 | 功能项 | 说明 |
|---|------|--------|------|
| P0-1 | API | 修复 StreamingResponse 缓冲 header | `Transfer-Encoding: chunked` 在某些 nginx 版本下无效，应改为 `X-Accel-Buffering: no` 单独一行 |
| P0-2 | 前端 | Chat store 的 `cancelStream` 不清理 pending SSE buffer | abort controller 后应清空 `streamingContent` 并发送 error 事件给 store |
| P0-3 | 后端 | Executor 兜底 fallback 在 planner 步骤为空时使用 llm_generate，但 rag_retrieve 工具也应该有对应 fallback | 每个已注册工具都应注册 fallback handler |
| P0-4 | 安全 | RateLimitMiddleware 被注释掉 | 恢复限流，最低限度保护 auth 端点 |

### P1 级（一个 Sprint 内）

| # | 模块 | 功能项 | 说明 |
|---|------|--------|------|
| P1-1 | Agent | SSE 真流式（见 A2） | 核心体验升级 |
| P1-2 | Agent | Executor 并行执行（见 A1） | 核心性能升级 |
| P1-3 | Agent | 反思策略补全 Adaptive + Sampling（见 A4） | Token 成本优化 |
| P1-4 | 前端 | 知识库页面增加全文搜索 + 标签筛选 + 批量操作 | 当前知识库页面只有分类筛选 |
| P1-5 | 前端 | 聊天消息支持重新生成（regenerate） + 编辑 | 当前只能删除会话 |
| P1-6 | 前端 | Dark mode 支持 | antd ConfigProvider 切换 |
| P1-7 | 后端 | CSV 导入知识库条目 | 支持人工批量入库 |
| P1-8 | 领域 | 资讯 Agent 支持手动编辑报告后再发布 | 当前一键生成无法修改 |
| P1-9 | 监控 | 告警增加知识库条目数趋势 / 每日入库量 | 当前监控主要覆盖 LLM 和 HTTP |
| P1-10 | API | KnowledgeIngester 异步化（用 asyncio.create_task） | 当前在 _run_chat 中 await，阻塞响应 |

### P2 级（两个 Sprint 内）

| # | 模块 | 功能项 | 说明 |
|---|------|--------|------|
| P2-1 | Agent | 多 Agent 并行辩论模式 | Critic 维度不足时，2-3 个 Critic 并行评估取投票 |
| P2-2 | Agent | 自我进化：Critic 评估历史反向优化 Planner prompt | 从过往 needs_replan 案例中提炼 prompt 改进 |
| P2-3 | 存储 | Postgres / Redis 存储后端正式接入 | 当前 JSON 文件存储在大库时性能下降 |
| P2-4 | 前端 | 移动端适配 | antd 栅格 + viewport 调整 |
| P2-5 | 前端 | 语音输入 / 语音输出 | Web Speech API |
| P2-6 | Agent | L2 中期记忆实现（用户偏好自动提取） | preferences upsert_preference 在每轮对话后调用 |
| P2-7 | 监控 | Grafana 看板增加 Agent 节点级时序图 | Supervisor/Planner/Executor/Critic/Scribe 各节点耗时 + 成功率 |
| P2-8 | 评估 | Golden QA 支持 diff 报告（相比上一版本通过率变化） | 当前 eval 只输出当前报告 |

---

## 四、代码级改进（逐文件）

### 后端核心

| # | 文件 | 行号 | 问题 | 建议 | 复杂度 |
|---|------|------|------|------|--------|
| C1 | executor.py | 87 | 顺序执行忽略 depends_on | 拓扑排序 + asyncio.gather | L |
| C2 | chat.py | 167-189 | `ainvoke` 异常兜底返回 dict 但丢失了 evaluation/tool_calls 等中间态 | 兜底返回中增加 `errors` 列表记录工作流每个节点的异常 | S |
| C3 | chat.py | 280-289 | `_stream_tokens` 逐字符推送（await sleep(0)）效率极低 | 改为逐 word 或按 10-20 字符 chunk，减少事件循环切换 | S |
| C4 | llm_factory.py | 314-321 | retry_count 追踪不准（tenacity 内部重试次数未暴露给外部） | 用 tenacity 的 `before_sleep_log` 或自定义 `retry` counter 装饰器 | M |
| C5 | llm_factory.py | 345 | `retried = retry_count > 0 or error_msg is not None and "rate limit" in (error_msg or "").lower()` 逻辑有优先级歧义 | 加括号明确：`retry_count > 0 or (error_msg is not None and "rate limit" in error_msg.lower())` | XS |
| C6 | state.py | 193-243 | create_initial_state 创建了 30+ 个字段全部赋值，即使大部分不需要 | 改为只传 user_input/conversation_id/trace_id/user_id，其余用默认值填充 | S |
| C7 | base.py (agents) | 28-31 | `_JSON_CODE_BLOCK_PATTERN` 在 knowledge_ingestor.py L45-49 重复定义 | 抽到公共 utils/parser.py 消除重复 | XS |
| C8 | knowledge_ingestor.py | 188-189 | `has_factual_info = bool(summary and summary.strip())` 用摘要非空判断事实性太粗糙 | 改为检查 summary 中是否包含数字/日期/专有名词，或用 LLM 做一次快速判断 | S |
| C9 | bootstrap.py | 126-210 | 11+ 步初始化全在一个函数里，某个步骤失败时已初始化的组件不会回滚 | 改为 try/finally + 显式顺序关闭，或用依赖注入框架（injector/container） | M |
| C10 | chat_share_storage.py | - | 分享存储和 conversation 存储都用 JSON 文件，并发写时可能冲突 | 加文件锁（fcntl/flock）或改用 SQLite | M |
| C11 | config.py | 301 | `token_expire_hours: 2160` = 90 天太长，配合前端滑动续租几乎等于免登录 | 改为默认 7 天 + 滑动续租 7 天 | S |
| C12 | graph/builder.py | 87-103 | route_after_critic 把 reflection_strategy 作为闭包参数传入，但 LangGraph conditional_edges 只接收 state | 改成 state 中预置 should_replan 标记，从 state 读取而非 strategy.should_replan | S |

### 前端

| # | 文件 | 问题 | 建议 | 复杂度 |
|---|------|------|------|--------|
| F1 | chat store | `(window as any).__stream_controller` 全局单例，多标签页冲突 | 用 zustand store 自己管理 controller，不用 window | XS |
| F2 | Chat.tsx | 612 行巨型组件，所有状态/事件/样式内联 | 提取 Sidebar / MessageList / InputArea / CodeBlock 为独立组件 | M |
| F3 | chat.ts (api service) | streamChat 的 fetch catch 不区分 AbortError | 已处理，done ✅ | - |
| F4 | Chat.tsx | style={} 内联样式遍布 | 抽 CSS module 或 emotion/styled-components | M |
| F5 | 类型 | ChatMeta / Message / Conversation 类型未做运行时验证（zod/valibot） | 加一层 API 响应 runtime 校验，类型漂移早发现 | S |
| F6 | chat store | loadConversations / selectConversation / deleteConversation 错误全部静默吞掉 | 统一错误处理，至少 console.error 或上报 logger | XS |
| F7 | Chat.tsx | 思考中动画用 CSS keyframes 每次重新注入 style 标签 | 抽到全局 index.css | XS |

### Prompt 层

| # | 文件 | 问题 | 建议 | 复杂度 |
|---|------|------|------|--------|
| P1 | templates.py | SUPERVISOR_PROMPT 和 CHAT_SIMPLE_PROMPT 都有 system prompt 直接注入关键词列表，关键词为空时显示"（无）"导致 LLM 困惑 | 关键词为空时跳过该段，用条件分支 `f"关键词：{', '.join(kws) if kws else '暂无'}."` | S |
| P2 | templates.py | PLANNER_PROMPT 工具列表硬编码，如果后续新增工具需要改 prompt | 改为从 ToolRegistry 动态注入可用工具列表 | M |
| P3 | templates.py | 所有 prompt 的 few-shot 示例缺失 | 每个 agent 的 system prompt 末尾补充 2-3 个高质量 few-shot 示例 | M |
| P4 | templates.py | COMPRESS_HISTORY_PROMPT 定义了但 ShortTermMemory._generate_summary 用的是硬编码 prompt | 统一用 COMPRESS_HISTORY_PROMPT，便于版本管理 | XS |
| P5 | job/01-09 提示词 | 20+ 个 job 提示词散落在 prompt/job/ 目录，没有注册机制 | 建立 prompt registry + 版本号 + 可被 LLM 动态检索选择 | L |

### 运维层

| # | 文件 | 问题 | 建议 | 复杂度 |
|---|------|------|------|--------|
| O1 | docker-compose.prod.yml | backend 用 Python 容器跑 Playwright Chromium（browser service 独立），但没有显式 healthcheck 验证 Chromium 可用 | 增加 `curl http://localhost:1300/health` 为额外 healthcheck | XS |
| O2 | ci.yml | lint / mypy 标记为非阻断 | 改为 PR 时阻断合入，main 时允许跳过（已有历史债务） | S |
| O3 | alerts.yml | `LowGroundedness` 告警基于 critic.evaluation.groundedness_score，但 chitchat 直通路径强制返回 1.0 | 在指标聚合时排除 intent=chitchat/clarify 的对话 | S |
| O4 | nginx.conf | `/api/v1` 反代 backend 时没有 `proxy_buffering off` | 流式端点需要关闭缓冲，建议在 location ~* /chat/stream 路径下单独配置 | S |
| O5 | loki-config.yml | 无日志保留期限制（retention_days / max_retention_chunks） | 加 30 天保留 | XS |
| O6 | backup.sh | 备份没有上传到 S3/COS/OSS | 增加 COS 上传（已有 Tencent Cloud 部署文档） | M |

---

## 五、Prompt 工程优化

### 当前问题

1. **无版本管理**：prompt/templates.py 中的模板没有版本号、变更日志、A/B 测试框架
2. **静态注入**：工具列表、few-shot 示例、用户画像都是硬编码字符串拼接
3. **无效果评估**：templates.py 中的 prompt 好坏只能靠手动测试，没有自动化的 prompt 评估流水线
4. **job/news 提示词分散**：prompt/job/ 和 prompt/news/ 是独立 MD 文件，和 backend/app/agents/prompts/templates.py 两套体系互不关联

### 建议方案

| 阶段 | 方案 | 工作量 |
|------|------|--------|
| S1 | 为每个 prompt 模板加 `__version__` 字段 + 变更日志注释 | S |
| S2 | 建立 Prompt Registry：所有 prompt（templates.py + prompt/job/ + prompt/news/）统一管理，支持按场景检索和动态选择 | M |
| S3 | 引入 promptfoo 或 ragas 做 prompt 自动化评估：基于 golden_qa.json 跑 baseline，改动后自动对比 groundedness / coherence 等指标 | L |
| S4 | 实现 A/B 测试框架：对关键 prompt（SUPERVISOR / CRITIC）支持按 traffic 比例切分版本 | L |

### 可新增的 prompt 模板

| 模板名 | 用途 | 优先级 |
|--------|------|--------|
| **EXTRACT_WARNING_SIGNALS** | Critic 额外的"风险信号检测"子 prompt，检查答案是否有幻觉迹象 | P1 |
| **KNOWLEDGE_DENY** | 当 L3 知识库无命中时，生成礼貌拒答 + 引导用户提供信息的 prompt | P1 |
| **MULTI_SOURCE_SYNTHESIS** | 当 Planner 产生多个并行 web_search 结果时，综合去重 / 消解矛盾的 prompt | P2 |

---

## 六、文档工程优化

### 文档现状诊断

| 维度 | 现状 | 问题 |
|------|------|------|
| 数量 | 20+ 文档（架构 / 设计 / 部署 / Backlog / Code Review） | ✅ 基本完整 |
| 与代码同步度 | 部分设计文档停留在 Phase 1（如 05-phase2-design.md），代码已到 Phase 5 | ⚠️ 漂移 |
| API 文档 | 无自动生成 | ❌ 需要手动维护 |
| 测试文档 | docs/testCase/ 有 TEST-CASES.md 和 INCREMENTAL-TEST-CASES.md | ⚠️ 但与 pytest 用例脱节 |
| 运维文档 | 6 份部署文档（CLOUD-DEPLOY / DEPLOYMENT / PRODUCTION-DEPLOY / TENCENT-CLOUD-DEPLOY / TAILSCALE-ACCESS / PRE-DEPLOY-CHECKLIST） | ⚠️ 分散，入口混乱 |

### 优化建议

| # | 建议 | 说明 | 优先级 |
|---|------|------|--------|
| D1 | 用 `@apidoc` 或 redoc 自动生成 FastAPI API 文档 | FastAPI 自带 /docs 路由，但可以用 Pydantic response_model + docstring 自动生成中文 API 手册 | P1 |
| D2 | 建立 doc version 与代码 phase 对齐 | 文档头部加 `sync-with-code: Phase 5, 2026-09` 标记，过期自动提醒 | P1 |
| D3 | 运维文档收敛为一份 `OPS-RUNBOOK.md` | 把 CLOUD-DEPLOY + DEPLOYMENT + PRODUCTION-DEPLOY 合并，TENCENT 作为 cloud-agnostic 的特例附录 | P1 |
| D4 | 架构图用 Mermaid 嵌入 | 01-architecture.md 和 02-phase1-design.md 中的 ASCII 图替换为 Mermaid，渲染更清晰 | P2 |
| D5 | 新增 Prompt 手册 | 每个 prompt 的参数、版本、预期效果、已知问题 | P2 |
| D6 | 新增 Runbook 目录结构 | docs/runbooks/{backend-degraded, chromium-crash, chromadb-corrupt, llm-cost-spike} | P2 |
| D7 | docs/README.md 作为索引 | 把现有 README 拆成顶层索引，各子文档自说明 | S |

### 建议补充的文档

| 文档 | 用途 | 优先级 |
|------|------|--------|
| **API.md** | 自动生成的 REST API 完整手册 | P1 |
| **AGENT-TROUBLESHOOTING.md** | LangGraph 节点失败排查指南（每个节点的常见异常 + 排查步骤） | P1 |
| **VECTOR-DB-ADMIN.md** | ChromaDB 日常运维（备份 / 恢复 / 条目清理 / 重建索引） | P2 |
| **COST-CONTROL.md** | LLM 成本监控与预算告警说明 | P2 |
| **PHASE-STATUS.md** | 各 Phase 功能完成度追踪（Phase 1→Phase 5 已完成/未完成清单） | P1 |

---

## 七、落地路线图

### Sprint 1（1 周 · 紧急修复）

| 目标 | 任务 | 前置依赖 | 验收标准 |
|------|------|----------|----------|
| 恢复安全防护 | 恢复 RateLimitMiddleware | 无 | CI lint 通过；auth 端点 5 req/min 限制生效 |
| 修复流式体验 | 真流式改造（A2） | 无 | 前端能看到节点进度条；答案按 token 真流式推送 |
| 修复 SSE 缓冲 | nginx conf + StreamingResponse header 修正 | 无 | 生产环境 SSE 不再被 nginx 缓冲 |
| 补充 Critic 分支 | needs_rewrite → 回到 Executor | 无 | Critic 返回 needs_rewrite 时 Executor 自动重跑 |
| KnowledgeIngester 异步化 | create_task 不阻塞响应 | 无 | chat 端点 P95 下降 > 20% |

### Sprint 2（2 周 · 核心优化）

| 目标 | 任务 | 前置依赖 | 验收标准 |
|------|------|----------|----------|
| Executor 并行化 | 拓扑排序 + asyncio.gather | Sprint 1 完成 | 3 步任务延迟下降 > 40% |
| 反思策略补全 | Adaptive + Sampling 实现 | 无 | reasoner token 消耗下降 > 30% |
| 前端 Chat 组件重构 | 拆分为 4 个独立组件 | 无 | Chat.tsx 从 600+ 行降到 < 200 行 |
| 文档收敛 | 运维 Runbook 合并 + API 自动生成 | 无 | docs/ 入口清晰；/docs API 可访问 |
| 代码去重 | _JSON_CODE_BLOCK_PATTERN 抽取 | 无 | 消除 knowledge_ingestor.py 与 base.py 的重复 |

### Sprint 3（2 周 · 增强功能）

| 目标 | 任务 | 前置依赖 | 验收标准 |
|------|------|----------|----------|
| L1 持久化 | Redis 后端实现 | Sprint 2 完成 | 多 worker 部署会话不丢失 |
| Prompt 工程体系 | Prompt Registry + 版本管理 | 无 | 所有 prompt 可版本化；支持按场景选择 |
| Agent 增强 | ContextCompressor + KnowledgeCurator | 无 | Planner 输入 token 减少 > 50%；知识库条目自动优化 |
| 监控增强 | Grafana Agent 节点看板 + 知识库指标 | 无 | 看板能看到每个节点耗时 + 成功率 + 知识库入库量 |
| 跨用户知识 | KnowledgeIngester 跨用户共享 | 无 | 不同用户相同问题可复用已有知识 |

---

## 附录：Agent 架构图示（当前 vs 建议演进）

### 当前架构

```
START → Supervisor → [chitchat/clarify/rag_retrieval → Planner]
                         ↓
                    Executor（顺序执行所有 step）
                         ↓
                    Critic（always 反思）
                      ↙↓
                 [needs_replan / pass → Scribe → END]
```

### 建议演进架构（Sprint 3 后）

```
START → Supervisor
            ↓
      ContextCompressor（压缩上下文）
            ↓
   [chitchat/clarify → Scribe / rag_retrieval → Planner]
                         ↓
                 Executor（并行 + 动态规划）
                         ↓
                     Critic（Adaptive + 多维度）
                      ↙↓
             [needs_replan → Planner / needs_rewrite → Executor / pass → Scribe]
                                                                      ↓
                                                              KnowledgeCurator（自动优化条目）
                                                                      ↓
                                                              END
```

---

*审计报告结束。所有建议均基于静态代码分析，未引入环境变更或修改任何源码。建议按 Sprint 3 阶段推进，优先落地 P0 修复和 A1/A2/A4 三个核心架构改进。*
