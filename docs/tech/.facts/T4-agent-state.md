---
table: T4
title: AgentState 字段矩阵
scope: backend/app/graph + backend/app/agents + 图外层调用方
status: as-is
---

# T4 AgentState 字段矩阵

> ⚠️ **本表是 2026-09-09 逆向分析期的「编写用工作产物」，可能已落后。**
> 2026-09-16 复核确认：`T1-routes`（65 条 vs 实测 75 个端点）、`T2-config`、
> `T8-observability` 的**行号与计数已过期**。写文档时请以
> **代码本身 + `docs/tech/*.md`** 为准；本表仅作线索索引，不要把它的行号当断言。
> （不随技术文档一起维护的原因见 `docs/tech/漂移清单.md` DOC-11。）


> 数据来源：`backend/app/graph/state.py`、`backend/app/graph/builder.py`、`backend/app/agents/{supervisor,planner,executor,critic,scribe}.py`、`backend/app/agents/strategies/{base,always}.py`、`backend/app/api/routes/chat.py`。
> 证据纪律：每条带 `backend/…:行号`；推断处标注【推断·待验证】。
> 现状：`GraphState` 为 `TypedDict(total=False)`（state.py:123-128），LangGraph 各节点返回“部分更新 dict”，逐 key 覆盖合并。

## 0. 汇总

- GraphState 顶层字段：**28 个**（state.py:140-185）。
- 嵌套 TypedDict：`TaskStep` 9 字段、`ToolCallRecord` 6 字段、`CriticEvaluation` 9 字段、`ConversationMetrics` 17 字段（state.py:57-116）。
- 图内节点：supervisor / rag_retrieval / planner / executor / critic / scribe + 直通节点 chat_simple / clarify（builder.py:292-311）。
- 图结构（builder.py:316-356）：START→supervisor→(条件)→ {chitchat→chat_simple→scribe | clarify→clarify→scribe | 其他→rag_retrieval→planner→executor→critic→(should_replan? planner 循环 : scribe)} →scribe→END。critic 处最大重规划 2 次（config.yaml:126 `reflection.max_replan: 2`，critic.py:120-123），因此 planner→executor→critic 段最多跑 3 轮。

## 1. GraphState 字段矩阵

字段默认值/缺省以 `create_initial_state` 为准（state.py:192-242；例外单独标注）。写入/读取只列**图内节点或关键外层**；外层特指 `chat.py`（HTTP 层）/cli/eval。

| 字段名 | 类型 | 初始化/缺省 | 写入（文件:行号） | 读取（文件:行号） | 生命周期说明 |
|---|---|---|---|---|---|
| conversation_id | str | init: state.py:215（chat.py:475） | 仅 init（无节点改写） | executor.py:82（L1 记忆上下文键） | 会话全程不变 |
| trace_id | str | init: state.py:216（uuid4） | 仅 init | 图内无读取；外层绑定日志 ctx（chat.py:474）并回传（chat.py:620） | 元信息；trace 关联 LangSmith/local |
| user_id | str | init: state.py:217（默认 "default"） | 仅 init | executor.py:81；rag 节点 builder.py:210 | 全程不变；记忆/KB 隔离键 |
| user_input | str | init: state.py:218 | 仅 init（各节点不改写） | supervisor.py:50、planner.py:47、rag builder.py:209、executor.py:80、chat_simple builder.py:114、clarify builder.py:155、critic.py:49、scribe.py:53 | 全程只读；本轮输入 |
| conversation_history | list[BaseMessage] | init: state.py:219（history or []） | 仅 init | **图内无任何 `.get` 读取**【推断：历史实际经 ShortTermMemory.get_context 注入 Executor，executor.py:146-148】 | 疑似遗留字段（图内未消费） |
| compressed_summaries | list[str] | init: state.py:220（[]） | 仅 init | **图内无读取** | 疑似遗留字段（图内未消费） |
| intent | str | init ""（state.py:221） | supervisor.py:120（正常）/:135（降级默认意图）；意图为 clarify 时改写为 "clarify" supervisor.py:110 | route_after_supervisor builder.py:72；rag builder.py:211；planner.py:54；clarify builder.py:156（Prompt）；scribe.py:195（metrics.intent_type）；always.py:39（是否反思判断） | supervisor 产出后驱动路由；末端被 scribe 读入 metrics |
| intent_confidence | float | init 0.0（state.py:222） | supervisor.py:121 / :136（降级 0.0） | clarify builder.py:157（Prompt）；scribe.py:194（metrics）；外层 chat.py:552 | supervisor 产出；归一化 [0,1]（supervisor.py:86-91） |
| needs_clarification | bool | init False（state.py:223） | supervisor.py:122（含置信度<阈值强制 true，:96-97）/ :137 | route_after_supervisor builder.py:73 | 驱动 clarify 路由 |
| clarification_question | str | init ""（state.py:224） | supervisor.py:123（缺省文案 :105-108）/ :138 | clarify_node 异常降级兜底 builder.py:183 | needs_clarification=true 时有效 |
| pre_retrieval_results | list[dict] | init []（state.py:225） | supervisor.py:124（写死空列表，Phase1 遗留）；rag_retrieval_node builder.py:232 / 异常 :239；vector_store=None 时 :207 | rag 节点自读：builder.py:209-211；executor.py:109（工具分派）、:157（format RAG）、:237 | supervisor→rag 会被 rag 节点结果覆盖；供 Executor 注入知识库参考 |
| rag_fallback_message | str | init ""（state.py:226） | rag_retrieval_node builder.py:233（正常兜底文案）/ :240（失败打标 `检索失败: <Type>`） | **图内无读取** | RAG-09 失败标记；写入后当前无下游消费【现状】 |
| task_steps | list[TaskStep] | init []（state.py:227） | planner.py:137 / 降级单步 :161；executor 原地改 status/result/error/latency_ms 后回写 executor.py:180（改于 :94,:116-117,:124-125,:136） | executor.py:73（主循环）、scribe.py:196（metrics.plan_step_count） | planner 产出→executor 按序执行→scribe 计数 |
| task_complexity | float | init 0.0（state.py:228） | planner.py:138 / :162（降级 0.0）；归一化 [0,1] planner.py:100-105 | critic.py:57（≥threshold 切 critic_complex，:62） | 驱动 critic 模型选择 |
| plan_reasoning | str | init ""（state.py:229） | planner.py:139 / :163 | **图内无读取** | 规划推理文本；当前无下游消费 |
| tool_calls | list[ToolCallRecord] | init []（state.py:230） | executor.py:177 / :191（降级 []）；直通节点也写 []：builder.py:124,167 | critic.py:70（评估输入）；scribe.py:160（成功率 metrics）；外层 chat.py:529 降级兜底 | executor 产出记录，critic/scribe 消费 |
| draft_answer | str | init ""（state.py:231） | executor.py:178 / 降级 user_input :192；chat_simple builder.py:122；clarify builder.py:165 | critic.py:50（评估对象）；scribe.py:61（final_answer 为空时兜底）；外层 chat.py:540 | executor/直通节点产出 → critic/scribe |
| execution_context | str | init ""（state.py:232） | executor.py:179 / :193（降级 ""） | **图内无读取**（仅 executor 内部用于拼 Prompt，:140-164） | 工具结果拼接文本；字段写入后下游不消费 |
| evaluation | CriticEvaluation | init {}（state.py:233） | critic.py:152 / 降级 :165-175；chat_simple builder.py:125-135（硬编码 pass）；clarify builder.py:168-178（同上） | scribe.py:159,:200-207（metrics 分数）；always.py:66-67（result 判定重规划）；eval/metrics.py:102 | critic 产出；直通节点写“假 pass”绕过反思 |
| replan_count | int | init 0（state.py:234） | critic.py:154（should_replan 时 +1） | critic.py:58；scribe.py:197（metrics）；always.py:68（<max_replan 才重规划）；builder 路由经 always.py:70-85 | 重规划循环计数器；上限 config.reflection.max_replan=2 |
| should_replan | bool | init False（state.py:235） | critic.py:153 / 降级 :177 False | **路由不使用此字段**：route_after_critic→strategy.should_replan 重读 evaluation.result+replan_count（builder.py:94-95、always.py:66-71） | 【现状漂移】写入但路由重新判定，字段实际未被消费 |
| final_answer | str | init ""（state.py:236） | chat_simple builder.py:121；clarify builder.py:164；scribe.py:118 / 降级 :136 | scribe.py:61（输入）；外层 chat.py:538-540（回复） | 直通/scribe 产出最终回复 |
| summary | str | init ""（state.py:237） | scribe.py:119 / 降级 ""（:137） | 外层 chat.py:594（知识入库用）；cli/chat.py:279 | scribe 摘要（≤200 字约定） |
| importance_score | float | init 0.0（state.py:238） | scribe.py:120 / 降级 0.0（:138） | 外层 chat.py:595（传给知识入库） | scribe 评分；<0.3 时 should_persist 强制 false（scribe.py:90-91） |
| should_persist | bool | init False（state.py:239） | scribe.py:121 / 降级 False（:139） | **图内无读取**；入库与否实际由 knowledge_ingester 独立判断（knowledge_ingestor.py:126-…） | 疑似遗留标记，实际入库决策不读它 |
| metrics | ConversationMetrics | init {}（state.py:240） | scribe.py:122（_build_metrics :193-218） | 外层 chat.py:548（token/成本/重试/降级持久化与回填 e2e_latency :550,566-574）；core/metrics.py:250-303（Prometheus）；cli/chat.py:232,300-313 | scribe 聚合；e2e_latency_ms 由 chat.py 回填真实值（scribe 先写 0，scribe.py:208） |
| errors | list[str] | init []（state.py:241） | 各节点异常分支追加：supervisor.py:133-140、planner.py:146-164、executor.py:187-195、critic.py:161-179、scribe.py:129-142；直通节点 builder.py:141,186 | 各节点异常分支先读再追加（如 supervisor.py:132、planner.py:146、executor.py:187、critic.py:161、scribe.py:129）；外层 chat.py:529 降级结果含 errors | 错误累积通道；不入正常回复 |
| llm_stats_snapshot | Any(StatsSnapshot) | 不在 init（create_initial_state 不含） | 图外注入：chat.py:484、cli/chat.py:192、eval/runner.py:340（`snapshot_stats()`） | scribe.py:170（`delta_stats` 计算 per-request token/成本/重试/降级，:171-180；缺失时回退全局统计 :181-191） | P0-1：per-request 统计快照，避免全局污染（state.py:182-185） |

### 1.1 嵌套类型（写入即按 TypedDict key）
- TaskStep（state.py:57-71）：step_id/description/tool/tool_input/depends_on/status/result/error/latency_ms —— planner.py:81-95 构造、executor 更新 status/result/error/latency_ms。
- ToolCallRecord（state.py:74-81）：tool_name/input/output/success/latency_ms/error —— executor.py:97-137 构造并追加。
- CriticEvaluation（state.py:84-94）：passed/result/groundedness_score/coherence_score/relevance_score/issues/suggestions/reasoning/model_used —— critic.py:127-137 构造。
- ConversationMetrics（state.py:97-116，17 键）：intent_confidence/intent_type/plan_step_count/replan_count/tool_success_rate/answer_groundedness/critic_coherence_score/answer_relevance/e2e_latency_ms/total_input_tokens/total_output_tokens/total_cost_usd/model_used/llm_retried/llm_retry_count/llm_degraded/llm_degradation_count —— scribe.py:193-218 填充。

## 2. 各节点读/写摘要（供交叉核对）

| 节点（builder.py 注册行） | 读 | 写 |
|---|---|---|
| supervisor（:292） | user_input、errors（异常） | intent、intent_confidence、needs_clarification、clarification_question、pre_retrieval_results（空）、errors（异常） |
| rag_retrieval（:311，闭包 :306） | user_input、user_id、intent、vector_store | pre_retrieval_results、rag_fallback_message |
| chat_simple（:309，闭包 :299） | user_input | final_answer、draft_answer、task_steps=[],tool_calls=[],evaluation(假 pass)、errors(异常) |
| clarify（:310，闭包 :302） | user_input、intent、intent_confidence、clarification_question(降级) | final_answer、draft_answer、task_steps=[],tool_calls=[],evaluation(假 pass)、errors(异常) |
| planner（:293） | user_input、intent、errors(异常) | task_steps、task_complexity、plan_reasoning、errors(异常) |
| executor（:294） | task_steps、user_input、user_id、conversation_id、pre_retrieval_results、errors(异常) | tool_calls、draft_answer、execution_context、task_steps(状态回写)、errors(异常) |
| critic（:295） | user_input、draft_answer、task_complexity、replan_count、tool_calls、errors(异常) | evaluation、should_replan、replan_count、errors(异常) |
| scribe（:296） | user_input、final_answer/draft_answer、evaluation、tool_calls、task_steps、intent、intent_confidence、replan_count、llm_stats_snapshot、errors(异常) | final_answer、summary、importance_score、should_persist、metrics、errors(异常) |
| 条件路由 | intent/needs_clarification（builder.py:72-73）；evaluation.result/replan_count（经 always.py:66-71） | — |

## 3. supervisor→…状态传递摘要

沿边状态流（边定义 builder.py:316-356；路由函数 builder.py:63-97）：

1. **START → supervisor**（:316）：输入 state 仅含 init 字段（conversation_id/trace_id/user_id/user_input/conversation_history…，chat.py:475）。
2. **supervisor 输出**：intent/intent_confidence/needs_clarification/clarification_question/pre_retrieval_results=[]（supervisor.py:119-125）。
3. **路由 1**（builder.py:319-327）：`needs_clarification||intent==clarify`→**clarify**；`intent==chitchat`→**chat_simple**；其余→**rag_retrieval**。
   - **chat_simple 分支**：直接产出 final_answer=draft_answer+假 evaluation → scribe（builder.py:330）。不经过 planner/executor/critic。
   - **clarify 分支**：产出澄清话术为 final_answer（builder.py:164）→ scribe（:331）。
4. **rag_retrieval**（:334→planner）：按 user_input/intent 检索知识库，写入 pre_retrieval_results + rag_fallback_message；L3 未启用（vector_store=None）时置空并跳过（builder.py:205-207）。
5. **planner**（:337→executor）：写 task_steps/task_complexity/plan_reasoning；空步骤兜底单步 llm_generate（planner.py:110-127）。
6. **executor**（:340→critic）：逐 step 执行工具（web_search/rag_retrieve/llm_generate），写 tool_calls/draft_answer/execution_context 并回写 task_steps 状态；draft 生成时可流式（executor.py:350-367）。
7. **critic**：写 evaluation/replan_count/should_replan。
8. **路由 2**（builder.py:346-353 + always.py:70-85）：critic 未通过（result=needs_replan）且 replan_count<2 → 回 **planner**（整段 planner→executor→critic 重跑，state 中 task_steps 等被覆盖重写）；否则 → **scribe**。chat_simple/clarify 不经此路由（无 critic）。
9. **scribe**（:356→END）：基于 final_answer/draft_answer+evaluation+llm_stats_snapshot 写 final_answer/summary/importance_score/should_persist/metrics。
10. **图外消费**（chat.py:537-576）：读 final_answer/draft_answer/metrics/intent/intent_confidence/summary/importance_score 组装 HTTP 响应并持久化；对 eval/cli 同构（eval/runner.py:340、cli/chat.py:192）。

## 4. 现状漂移/注意点清单

1. `should_replan`、`rag_fallback_message`、`plan_reasoning`、`execution_context`、`should_persist`、`conversation_history`、`compressed_summaries`：写入但图内无消费或仅部分消费（见矩阵，含【推断·待验证】项）。
2. supervisor 仍写 `pre_retrieval_results=[]`（supervisor.py:124），但 Phase2 起该字段在规划路径上会立即被 rag_retrieval 节点结果覆盖（builder.py:232）【现状漂移】。
3. chat_simple/clarify 写入“硬编码 evaluation(假 pass)”绕过反思（builder.py:125-135,168-178），scribe 的 metrics 会把这些 1.0/10.0 分计入对话指标。
4. 图外重建 final_state 采用各节点输出 dict 覆盖式合并（chat.py:492-508 `{**dict(state), **acc}`），多节点同名 list 字段（如 errors）在覆盖语义下保留最后写入者【实现说明】。
