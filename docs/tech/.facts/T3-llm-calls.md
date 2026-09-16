---
table: T3
title: LLM 调用点清单
scope: backend/app + backend/config.yaml
status: as-is
---

# T3 LLM 调用点清单

> ⚠️ **本表是 2026-09-09 逆向分析期的「编写用工作产物」，可能已落后。**
> 2026-09-16 复核确认：`T1-routes`（65 条 vs 实测 75 个端点）、`T2-config`、
> `T8-observability` 的**行号与计数已过期**。写文档时请以
> **代码本身 + `docs/tech/*.md`** 为准；本表仅作线索索引，不要把它的行号当断言。
> （不随技术文档一起维护的原因见 `docs/tech/漂移清单.md` DOC-11。）


> 数据来源：`backend/config.yaml`、`backend/app/core/llm_factory.py`、`backend/app/core/config.py` 与 `backend/app/` 全量 grep（`ainvoke_with_stats|astream_with_stats|llm_factory.get|.ainvoke(|.astream(|ChatOpenAI|ChatDeepseek`）。
> 证据纪律：每条带 `backend/…:行号`；推断处标注【推断·待验证】；密钥脱敏。
> 本文只描述现状（as-is）。

## 0. 汇总

- **config.yaml 定义角色数：9**（supervisor / planner / executor / critic / critic_complex / scribe / chat_simple / news_report / job_analysis，config.yaml:26-69）。全部角色均被代码引用。
- **业务源码 LLM 调用点：23 个**（backend/app 内实际向模型发起请求的位置；不含 llm_factory 内部通用执行行）
  - 走统一统计入口（`ainvoke_with_stats` / `astream_with_stats`）：**13 个**
  - 绕过统一入口（直接 `factory.get(role)` 后裸调 `ainvoke/astream`，或自带 client）：**10 个**
  - 另计 1 个**工厂内部、未统计**的调用：`llm_factory.py:603` 健康检查 ping。
- 运行期放大量：news/job 两域同一调用点会被循环/并发放大（见 §4 注）。

## 1. LLM 工厂（backend/app/core/llm_factory.py）现状

### 1.1 实例管理
| 事实 | 证据 |
|---|---|
| `LLMFactory.__init__(config)`，按角色缓存 LLM 实例（`_cache: dict[str, BaseChatModel]`），同角色只创建一次 | llm_factory.py:143-150（145） |
| `get(role)`：缓存命中直接返回；角色配置不存在抛 `LLMError`；创建失败走降级 | llm_factory.py:152-211（169-175, 189-211） |
| `get_actual_model(role)` / `is_degraded(role)`：暴露实际模型与降级标记 | llm_factory.py:213-230 |
| `_create_llm(role_config)`：优先 `langchain_deepseek.ChatDeepseek`，ImportError 时退到 `langchain_openai.ChatOpenAI`（注释：DeepSeek 兼容 OpenAI 接口） | llm_factory.py:486-509（490-493） |
| LLM kwargs：`model/api_key/base_url/temperature/max_tokens/timeout(60)/max_retries=0`（SDK 层不重试，由工厂 tenacity 管理） | llm_factory.py:495-503 |
| `response_format=="json"` 时注入 `model_kwargs={"response_format": {"type": "json_object"}}`（仅 DeepSeek 支持） | llm_factory.py:506-507 |
| base_url / api_key 全局单一：来自 `config.llm.base_url / config.llm.api_key`（DeepSeek） | llm_factory.py:497-498；config.yaml:20-21 |

### 1.2 降级链（reasoner→chat→error）
| 事实 | 证据 |
|---|---|
| `_should_fallback(model)`：模型名含 "reasoner" → 用 `config.llm.fallback.reasoner_to_chat`；否则（chat 类）→ 用 `config.llm.fallback.chat_to_error` | llm_factory.py:511-515 |
| `_get_fallback_config(original)`：仅对含 "reasoner" 的模型返回降级配置 `model="deepseek-chat"`（temperature/max_tokens/response_format 不变）；chat 模型返回 None → **无实例级降级目标，直接抛 `LLMError`** | llm_factory.py:517-527（519-526, 527） |
| 降级发生时记录 `_role_model_map[role]=降级模型`、`_degraded_roles.add(role)`，并缓存降级实例 | llm_factory.py:201-206 |
| 配置开关 | config.yaml:71-74：`reasoner_to_chat: true`、`chat_to_error: true`；默认值同（config.py:46-49, 61） |
| 结论【现状】：实际降级链仅「reasoner → chat」一级；chat 不可用时没有更低级模型，最终报错（chat_to_error=true 与 false 在该分支下行为一致——都抛出，见 1.1 `get()` 异常分支） | llm_factory.py:189-211 |

### 1.3 重试策略（仅 ainvoke_with_stats 内置）
| 事实 | 证据 |
|---|---|
| tenacity：`retry=retry_if_exception_type((LLMTimeoutError, LLMRateLimitError, LLMRetryableError))`；`stop=stop_after_attempt(config.llm.max_retries + 1)`；`wait_exponential(multiplier=1, min=retry_backoff_seconds[0] or 1, max=10)`；`reraise=True` | llm_factory.py:270-283 |
| 全局配置：`max_retries: 2`、`retry_backoff_seconds: [1, 2]`、`timeout_seconds: 60` → 默认最多 3 次尝试、退避 1s 起步封顶 10s | config.yaml:22-24（config.py:52-61 默认同） |
| 错误分类（_call 内）：超时→`LLMTimeoutError`；429/rate limit→`LLMRateLimitError`；500/502/503/504/internal server error→`LLMRetryableError`；connection+reset/refused/timeout→`LLMRetryableError`；402/insufficient（余额不足）→`LLMRetryableError`；413/too large→`LLMError`（不可重试）；其余→`LLMError` | llm_factory.py:288-310 |
| 重试耗尽后的异常分类层级 | exceptions.py:25-54（`LLMError`→`LLMRateLimitError`/`LLMTimeoutError`/`LLMRetryableError`） |
| `astream_with_stats` **不做 tenacity 重试**（注释：流式重试需重放整个流，成本高），失败由调用方兜底 | llm_factory.py:366-378（374-377）、:390-406 |

### 1.4 成本计算与统计记录机制
| 事实 | 证据 |
|---|---|
| 单次成本：`_calculate_cost(model, in, out)` = `in/1000*price.input + out/1000*price.output`，按 **实际模型**（降级后）取价；配置无此模型 → 返回 0.0 | llm_factory.py:343-344, 529-534 |
| 定价表：deepseek-chat input 0.00014 / output 0.00028；deepseek-reasoner input 0.00055 / output 0.00219（$ / 1K tokens） | config.yaml:176-183 |
| 调用记录：`LLMCallRecord`（role/model/configured_model/input_tokens/output_tokens/latency_ms/cost_usd/success/retried/retry_count/degraded/error/timestamp） | llm_factory.py:53-68 |
| token 来源：成功响应 `usage_metadata.input_tokens/output_tokens`（缺失记 0）；流式调用 token 记 0（usage_metadata 通常缺失） | llm_factory.py:337-341, 407-421 |
| 累计统计 `LLMCallStats`（调用数/token/成本/延迟/success/failure/reasoner_calls/retry_count/degradation_count/records），异步锁保护；reasoner 判定按记录模型名含 "reasoner" | llm_factory.py:71-107, 536-558 |
| 每请求隔离：`snapshot_stats()` / `delta_stats(snapshot)`（差值取自 `stats.records[snapshot.records_len:]`）；Scribe 由此生成 per-request metrics | llm_factory.py:110-128, 427-480 |
| 统计不直接写 Prometheus；`core/metrics.py:340-344` 注明 LLMFactory 当前**未调用** `record_llm_call`，指标由对话层（record_chat_metrics 读 state.metrics）间接产生 | core/metrics.py:244-303, 340-344 |
| 每次 `_record_call` 输出 INFO 日志；重试后仍失败 / 发生降级额外输出 WARNING | llm_factory.py:560-592 |
| **健康检查调用不记账**：`health_check()` 用 `get("chat_simple")` 裸 `ainvoke(ping)`，不经过 `_record_call` | llm_factory.py:594-607（602-603） |

## 2. 角色→模型配置（backend/config.yaml:26-69；config.py:38-43 校验默认）

| 角色 | 模型 | temperature | max_tokens | response_format | 证据 |
|---|---|---|---|---|---|
| supervisor | deepseek-chat | 0.1 | 500 | json | config.yaml:27-31 |
| planner | deepseek-reasoner | 0.2 | 2000 | json | config.yaml:32-36 |
| executor | deepseek-chat | 0.3 | 2000 | 无 | config.yaml:37-40 |
| critic | deepseek-chat | 0.0 | 1000 | json | config.yaml:41-45 |
| critic_complex | deepseek-reasoner | 0.0 | 2000 | json | config.yaml:46-50 |
| scribe | deepseek-chat | 0.2 | 800 | json | config.yaml:51-55 |
| chat_simple | deepseek-chat | 0.7 | 1000 | 无 | config.yaml:56-59 |
| news_report | deepseek-chat | 0.3 | 8000 | json | config.yaml:60-64 |
| job_analysis | deepseek-chat | 0.3 | 4000 | json | config.yaml:65-69 |

`LLMRoleConfig` Pydantic 默认：temperature 0.1 / max_tokens 1000 / response_format None（config.py:38-43）。`LLMConfig` 校验 api_key 非空且必须展开 `${DEEPSEEK_API_KEY}`（config.py:63-69；密钥值脱敏 `<REDACTED>`）。

## 3. 统一统计入口调用点（13 个，均 `ainvoke_with_stats`/`astream_with_stats`）

调用点均为 `backend/app/…:行号`；「超时/重试」未另注者 = 工厂级 timeout 60s + tenacity（max_retries 2，指数退避 1–10s）。

| # | 调用方 文件:行号 | 角色 | 触发场景 | 入口 | 备注 |
|---|---|---|---|---|---|
| 1 | agents/supervisor.py:75-77 | supervisor | 意图识别+路由决策（JSON） | ainvoke_with_stats | 输出解析 base.py:77-142 |
| 2 | graph/builder.py:115 | chat_simple | chitchat 直通回复 | ainvoke_with_stats | chat_simple_node |
| 3 | graph/builder.py:159 | chat_simple | clarify 澄清话术生成 | ainvoke_with_stats | clarify_node |
| 4 | agents/planner.py:65-67 | planner | 任务拆解（reasoner，JSON） | ainvoke_with_stats | 可被重规划循环重复执行 |
| 5 | agents/executor.py:272-275 | executor | 单步 `llm_generate` 工具 | ainvoke_with_stats | `_llm_generate` |
| 6 | agents/executor.py:357-359 | executor | 草稿生成（**流式**，有 token sink 时） | astream_with_stats | 无 tenacity；失败回退工具结果 |
| 7 | agents/executor.py:369-371 | executor | 草稿生成（非流式） | ainvoke_with_stats | 失败回退 tool_results |
| 8 | agents/critic.py:88-90 | critic 或 critic_complex | 反思评估；task_complexity≥0.7 切 reasoner | ainvoke_with_stats | 角色选择 critic.py:60-67 |
| 9 | agents/scribe.py:77-79 | scribe | 摘要+重要性+metrics | ainvoke_with_stats | 会话末节点 |
| 10 | memory/short_term.py:306-309 | scribe（默认 `llm_role="scribe"`） | L1 记忆压缩摘要 | ainvoke_with_stats | 构造函数 short_term.py:57 |
| 11 | memory/short_term.py:346-349 | scribe（同上） | L1 摘要合并 | ainvoke_with_stats | 同上 |
| 12 | agents/knowledge_ingestor.py:517 | scribe | 对话事实提取入库 | ainvoke_with_stats | 上传/聊天后自迭代 |
| 13 | api/routes/chat.py:381 | chat_simple | 应聘助手「偏好记录员」后台抽取（JSON prompt） | ainvoke_with_stats | 后台任务，chat.py:355-404 |

## 4. 绕过统一统计入口的调用点（10 个）

一律标记「绕过统一入口」。共同点：`llm_factory.get(role)` 拿到的是**带角色温度/max_tokens/response_format 的实例**（实例由工厂创建），但**不经 ainvoke_with_stats/astream_with_stats** → 无 LLM 调用记录、无成本/token 统计、无 tenacity 重试（除 news 分类另有自研重试），SDK `max_retries=0`。

| # | 调用方 文件:行号 | 角色 | 模型参数来源 | 场景 / 备注 |
|---|---|---|---|---|
| 1 | services/classifier.py:91,96 | supervisor（`_CLASSIFY_ROLE`，classifier.py:40） | supervisor 配置（0.1/500/json） | 文档上传三级分类；单发无重试 |
| 2 | api/routes/share.py:498,500 | chat_simple（`_CHAT_ROLE`，share.py:56） | chat_simple 配置（0.7/1000） | 知识库分享问答 SSE **流式** 回复 |
| 3 | agents/news/generator.py:216-217 | news_report（service.py:131-137 传 `config.news.llm_role`） | news_report（0.3/8000/json） | 语义分类（失败回退关键词） |
| 4 | agents/news/generator.py:265-270 | news_report | 同上 | 逐类生成；**自研重试**：最多 3 次+`sleep(2*attempt)`（generator.py:267,296-301）；单类输入≤30/输出≤20 条 |
| 5 | agents/news/generator.py:382-383 | news_report | 同上 | 头条深度分析（失败返回空串） |
| 6 | agents/news/generator.py:422-423 | news_report | 同上 | 跨类关联+预测综合分析 |
| 7 | agents/job/generator.py:212-213 | job_analysis（service.py:42 传 `config.job.llm_role`） | job_analysis（0.3/4000/json） | `_call_step` 底层；单 JD 分析最多 **7 步**（02/03/04/05/06/08/09 提示词，generator.py:31-39,91-116），每步一次 LLM；无重试 |
| 8 | agents/job/market.py:113,116 | job_analysis | 同上 | `_llm_call`；`analyze_market` 并发 2 次（market 行情+知识迭代，market.py:246-249） |
| 9 | api/routes/upload.py:1000-1001 | job_analysis | 同上 | 知识库概览 `_generate_kb_overview`（**复用 job_analysis 角色生成非招聘文本——角色/场景不匹配【现状漂移】**, 976-1006） |
| 10 | tools/image_processor.py:306-313,337 | **无角色（非 llm_factory）**：独立 `langchain_openai.ChatOpenAI`（vision 模型） | 硬编码：model 默认 `qwen-vl-plus`、base_url/api_key 来自 `_get_image_config()` 读环境变量 `VISION_LLM_*`（upload.py:103-116）或调用方传入的 dict；max_tokens=500、temperature=0.3、timeout=30 | 图片多模态打标签+描述；**完全不经 LLMFactory**（不共享 DeepSeek key/base_url/降级/统计） |

绕过入口的**业务调用点所属模块**：classifier 1、share 1、news generator 4、job generator 1（×7 步/次）、job market 1（×2 次并发）、upload KB overview 1、image_processor 1 → 合计 10 个源码点。

注：
- config.yaml:223-238 `tools.image_analysis.*`（含 `vision_llm`、`ocr`）**未被 config.py 的 `ToolsConfig` 建模**（config.py:267-272 仅 web_search/filesystem/vector_store/document_parser，无 image_analysis）→ 该段在 `AppConfig(**expanded)` 时被 Pydantic 忽略【现状漂移】；实际视觉配置走环境变量（upload.py:103-116）。
- `news.service` / `job.service` / scheduler 只是编排，不直接发 LLM 请求（news/service.py:131-137、job/service.py:38-43）。

## 5. 内部 / 非业务调用点（不计入业务清单）

| 位置 | 说明 |
|---|---|
| llm_factory.py:287 | `ainvoke_with_stats` 内部 `_call` 的 `llm.ainvoke`（tenacity 包裹的通用执行行） |
| llm_factory.py:385 | `astream_with_stats` 内部 `llm.astream` |
| llm_factory.py:603 | `health_check()` 内部 `chat_simple` ping（**未记账**） |
| eval/runner.py:347、cli/chat.py:198 | 对 **LangGraph 图** `graph.ainvoke`（图内部才发 LLM） |
| agents/executor.py:223 | `tool.ainvoke`（web_search 工具，非 LLM） |
| core/embedding.py | bge 本地 embedding（非 LLM chat 域） |
