---
title: T2-配置项清单（config.yaml + core/config.py 事实表）
table: T2
source: backend/config.yaml, backend/app/core/config.py, backend/app 全量 grep
status: draft（阶段1 SSOT 工作产物）
---

# T2 配置项清单

> ⚠️ **本表是 2026-09-09 逆向分析期的「编写用工作产物」，可能已落后。**
> 2026-09-16 复核确认：`T1-routes`（65 条 vs 实测 75 个端点）、`T2-config`、
> `T8-observability` 的**行号与计数已过期**。写文档时请以
> **代码本身 + `docs/tech/*.md`** 为准；本表仅作线索索引，不要把它的行号当断言。
> （不随技术文档一起维护的原因见 `docs/tech/漂移清单.md` DOC-11。）


> 来源：`backend/config.yaml`（417 行）+ `backend/app/core/config.py`（464 行，Pydantic v2 模型）。grep 范围：`backend/app/**/*.py`。
> 状态列：**已引用** = 代码真实读取（附 文件:行号）；**死配置** = config.yaml / 模型中有定义，但 backend/app 无读取处（含「仅模型默认、yaml 未定义但被读」单独标注）。
> 环境变量一律只写变量名，真实值不写。

## 0. 加载机制与生效时机（总览）

| 事实 | 证据 |
|---|---|
| 启动入口读取路径：`os.getenv("SEKB_CONFIG_PATH", "config.yaml")` | server.py:129 |
| 加载流程：`load_dotenv()`（不覆盖已存在 env）→ `yaml.safe_load` → 递归展开 `${VAR}` → `AppConfig(**expanded)` Pydantic 强校验 | config.py:412-450 |
| 展开语法：正则 `\$\{([^}]+)\}`，未设置 env 替换为空串 `""`（让 Pydantic 校验失败） | config.py:391-409 |
| ⚠️ **不支持 bash `:-` 默认值语法**：`${A:-default}` 整体被当作变量名 `A:-default` 查 env → 恒为 `""` | config.py:391-404 + config.yaml:228-230/276 |
| 全局单例：`get_config()` `lru_cache(maxsize=1)`，进程内只加载一次；测试可 `cache_clear()` | config.py:453-464 |
| 生效时机：全部在**进程启动/首次 get_config() 时一次性加载校验**；消费方多为启动装配（bootstrap.py）、首次 LLM 懒创建、或每次调用读缓存单例（如 create_jwt 每签发 token 读 `config.api.auth.token_expire_hours`，auth.py:70-71）。config.yaml 头注释声明「修改配置后需重启服务生效（Phase 1 不支持热加载）」 | config.yaml:5 |
| Pydantic 版本 `pydantic>=2.10.0`；模型未设 `model_config` → 默认 **extra=ignore**：yaml 中无对应模型字段的子树被**静默丢弃**（如 `tools.image_analysis.*`） | requirements.txt:19；config.py:30-464 |
| 校验错误统一抛 `ConfigError`，由调用方启动失败处理 | config.py:428-450；config.py:24 |

---

## 1. `app`（AppConfigSection，config.py:30-35）

| 键全路径 | 类型 | config.yaml 现值 | 模型默认/校验 | 读取点（backend/app） | 状态 |
|---|---|---|---|---|---|
| app.name | str | self-evolving-kb | 必填 | bootstrap.py:136；server.py:183(FastAPI title)、server.py:143(日志) | 已引用 |
| app.version | str | 0.1.0 | 必填 | bootstrap.py:137；server.py:144、184 | 已引用 |
| app.environment | str | development | "development" | bootstrap.py:138；cli/chat.py:485；cli/eval.py:140 | 已引用 |
| app.debug | bool | true | True | 无 | **死配置** |

---

## 2. `llm`（LLMConfig，config.py:52-69）+ `llm.roles.*`

| 键全路径 | 类型 | config.yaml 现值 | 模型默认/校验 | 读取点 | 状态 |
|---|---|---|---|---|---|
| llm.provider | str | deepseek | "deepseek" | cli/eval.py:141；cli/main.py:176（仅 CLI 展示，运行时建 LLM 不读它） | 已引用（弱：仅 CLI 展示） |
| llm.api_key | str | `${DEEPSEEK_API_KEY}` | 必填；校验：空或 `${…}` 未展开且 env 未设 → ConfigError | llm_factory.py:497；bootstrap 启动即校验 | 已引用（env: DEEPSEEK_API_KEY） |
| llm.base_url | str | https://api.deepseek.com/v1 | "https://api.deepseek.com/v1" | llm_factory.py:498 | 已引用 |
| llm.timeout_seconds | int | 60 | 60 | llm_factory.py:501（角色 LLM 懒创建时） | 已引用 |
| llm.max_retries | int | 2 | 2 | llm_factory.py:274（tenacity stop_after_attempt = max_retries+1） | 已引用 |
| llm.retry_backoff_seconds | list[int] | [1, 2] | [1, 2] | llm_factory.py:277-279（仅取 `[0]` 作退避下限；第 2 个元素未用） | 已引用（部分字段未用） |
| llm.fallback.reasoner_to_chat | bool | true | True | llm_factory.py:514 | 已引用 |
| llm.fallback.chat_to_error | bool | true | True | llm_factory.py:515 | 已引用 |
| llm.roles | dict[str,LLMRoleConfig] | 9 个角色 | 必填 dict | `roles.get(role)` 按名动态取，llm_factory.py:484；字段消费 llm_factory.py:496-507 | 已引用（动态字典） |

`llm.roles.<role>` 各角色字段（模型级统一校验：temperature float ge0 le2 默认0.1；max_tokens int gt0 默认1000；response_format str|None "json"|None，config.py:38-43）：

| 键全路径 | model 现值 | temperature | max_tokens | response_format | 运行时角色消费点 |
|---|---|---|---|---|---|
| llm.roles.supervisor.{model,temperature,max_tokens,response_format} | deepseek-chat | 0.1 | 500 | json | graph 监督者 |
| llm.roles.planner.{…} | deepseek-reasoner | 0.2 | 2000 | json | graph planner |
| llm.roles.executor.{…} | deepseek-chat | 0.3 | 2000 | — | graph executor |
| llm.roles.critic.{…} | deepseek-chat | 0.0 | 1000 | json | graph critic（默认） |
| llm.roles.critic_complex.{…} | deepseek-reasoner | 0.0 | 2000 | json | 复杂任务深度反思 |
| llm.roles.scribe.{…} | deepseek-chat | 0.2 | 800 | json | graph scribe |
| llm.roles.chat_simple.{…} | deepseek-chat | 0.7 | 1000 | — | 闲聊直通（share.py:56 亦用该角色） |
| llm.roles.news_report.{…} | deepseek-chat | 0.3 | 8000 | json | 科技资讯日报生成（news 启用时） |
| llm.roles.job_analysis.{…} | deepseek-chat | 0.3 | 4000 | json | 招聘分析（job 启用时） |

> 读取机制：所有角色字段最终经 `LLMFactory.get(role)`（llm_factory.py:152-211）→ `_get_role_config`（llm_factory.py:482-484）→ `_create_llm`（llm_factory.py:486-509，逐字段取 model/api_key/base_url/temperature/max_tokens/timeout，response_format=="json" 时注入 `model_kwargs.response_format`）。角色是否真的被请求取决于对应 agent 是否启用。yaml 值：config.yaml:27-69。

---

## 3. `intent_routing`（config.py:72-80）

| 键全路径 | 类型 | 现值 | 模型默认/校验 | 读取点 | 状态 |
|---|---|---|---|---|---|
| intent_routing.default_intent | str | web_default | "web_default" | planner.py:54；supervisor.py:67/82 | 已引用 |
| intent_routing.clarify_threshold | float | 0.7 | 0.7 ge0 le1 | supervisor.py:66/96 | 已引用 |
| intent_routing.kb_strict_keywords | list[str] | 4 条 | [] | supervisor.py:64（拼 prompt） | 已引用 |
| intent_routing.kb_prefer_hit_threshold | float | 0.85 | 0.85 ge0 le1 | supervisor.py:70 | 已引用 |
| intent_routing.realtime_keywords | list[str] | 6 条 | [] | supervisor.py:65 | 已引用 |
| intent_routing.enable_pre_retrieval | bool | true | True | 无 | **死配置** |
| intent_routing.pre_retrieval_top_k | int | 3 | 3 | supervisor.py:68 | 已引用 |

---

## 4. `memory`（MemoryConfig，config.py:83-138）

| 键全路径 | 类型 | 现值 | 模型默认/校验 | 读取点 | 状态 |
|---|---|---|---|---|---|
| memory.l1_working.enabled | bool | true | True | 仅启动校验：Phase1 必须 true，否则 ConfigError（config.py:124-129） | 已引用（仅校验，运行期不读） |
| memory.l1_working.max_turns | int | 8 | 8 gt0 | short_term.py:92（ShortTermMemory 构造，config 对象经 bootstrap.py:157 传入） | 已引用 |
| memory.l1_working.max_tokens | int | 3000 | 3000 gt0 | executor.py:147（get_context 截断上限）；short_term.py 亦存 self.config | 已引用 |
| memory.l1_working.hard_token_limit | int | 4000 | 4000 gt0 | short_term.py:94；校验 max_tokens<hard_token_limit（config.py:131-138） | 已引用 |
| memory.l1_working.compress_strategy | str | hierarchical | "hierarchical" | short_term.py:95 | 已引用 |
| memory.l1_working.max_compressed_summaries | int | 3 | 3 | short_term.py:96/216 | 已引用 |
| memory.l2_session.enabled | bool | false | False | 无（l2 全程零引用） | **死配置**（Phase2 预留，config.yaml:107-111） |
| memory.l2_session.max_items | int | 50 | 50 | 无 | **死配置** |
| memory.l2_session.eviction | str | lru | "lru" | 无 | **死配置** |
| memory.l3_knowledge.enabled | bool | true | False | bootstrap.py:170（L3 装配开关） | 已引用 |
| memory.l3_knowledge.retrieval_top_k | int | 5 | 5 | graph/builder.py:217（喂 RAGRetriever；retriever.py:16 另有硬编码 5 兜底） | 已引用 |
| memory.l3_knowledge.importance_threshold | float | 0.3 | 0.3 | knowledge_ingestor.py:196；graph/builder.py:218 | 已引用 |
| memory.l3_knowledge.allow_hash_fallback | bool | false | False | bootstrap.py:177（→ embedding） | 已引用 |
| memory.l3_knowledge.eviction.max_items | int | 10000 | 10000 | 无 | **死配置** |
| memory.l3_knowledge.eviction.stale_days | int | 7 | 7 | 无 | **死配置** |
| memory.l3_knowledge.eviction.similarity_merge_threshold | float | 0.95 | 0.95 | 无 | **死配置** |

---

## 5. `reflection`（ReflectionConfig，config.py:141-166）

| 键全路径 | 类型 | 现值 | 模型默认/校验 | 读取点 | 状态 |
|---|---|---|---|---|---|
| reflection.policy | str | always | "always"；校验 ∈ {always,adaptive,sampling} | strategies/factory.py:28-39（adaptive/sampling 目前降级回 always，32-39） | 已引用 |
| reflection.max_replan | int | 2 | 2 ge0 | critic.py:120 | 已引用 |
| reflection.model_switch_threshold | float | 0.7 | 0.7 ge0 le1 | planner.py:55/61；critic.py:30(注释/行为) | 已引用 |
| reflection.adaptive.skip_if_rag_hit_above | float | 0.95 | 0.95 | 无（adaptive 策略未实现） | **死配置** |
| reflection.adaptive.skip_if_intent_in | list[str] | [chitchat] | ["chitchat"] | 无 | **死配置** |
| reflection.sampling.rate | float | 0.5 | 0.5 ge0 le1 | 无（sampling 策略未实现） | **死配置** |

---

## 6. `evaluation`（EvaluationConfig，config.py:169-199）

| 键全路径 | 类型 | 现值 | 模型默认 | 读取点 | 状态 |
|---|---|---|---|---|---|
| evaluation.enabled | bool | true | True | 无 | **死配置** |
| evaluation.log_to_file | bool | true | True | 无 | **死配置** |
| evaluation.log_path | str | data/eval_logs | "data/eval_logs" | 无 | **死配置** |
| evaluation.metrics.{intent_confidence,plan_step_count,replan_count,tool_success_rate,answer_groundedness,critic_coherence_score,answer_relevance,e2e_latency_ms}.{good,warn} | float | 见 config.yaml:140-164 | 模型内置默认（config.py:177-184） | assertion.py:281-288（逐指标取阈值）→ eval/metrics.py:187-209（读 good/warn 判级） | 已引用（16 个叶子值共用上述两处消费） |
| evaluation.regression.pass_rate_drop_threshold | float | 0.05 | 0.05 | 无 | **死配置** |
| evaluation.regression.metric_drop_threshold | float | 0.05 | 0.05 | 无 | **死配置** |

---

## 7. `cost_control`（CostControlConfig，config.py:202-214）

| 键全路径 | 类型 | 现值 | 模型默认 | 读取点 | 状态 |
|---|---|---|---|---|---|
| cost_control.per_conversation_token_limit | int | 20000 | 20000 | 无 | **死配置** |
| cost_control.daily_budget_usd | float | 1.0 | 1.0 | 无（BudgetExceededError 异常类存在 exceptions.py:112-115，但无读配置处） | **死配置** |
| cost_control.reasoner_ratio_alert_above | float | 0.3 | 0.3 | 无 | **死配置** |
| cost_control.auto_downgrade_on_budget | bool | true | True | 无 | **死配置** |
| cost_control.pricing.{deepseek-chat,deepseek-reasoner}.{input,output} | float | 0.00014/0.00028 / 0.00055/0.00219 | {}（缺省无价） | llm_factory.py:531-534（按实际 model 名查价算成本） | 已引用（2 模型 × 2 方向） |

---

## 8. `tracing`（TracingConfig，config.py:217-236）

| 键全路径 | 类型 | 现值 | 模型默认 | 读取点 | 状态 |
|---|---|---|---|---|---|
| tracing.provider | str | langsmith | "langsmith" | tracing.py:110/133 | 已引用 |
| tracing.fallback_to_local_on_failure | bool | true | True | tracing.py:129 | 已引用 |
| tracing.sample_rate | float | 1.0 | 1.0 ge0 le1 | 无 | **死配置** |
| tracing.langsmith.api_key | str | `${LANGSMITH_API_KEY}` | "" | tracing.py:110（启用判断）/114（写 env LANGSMITH_API_KEY） | 已引用（env: LANGSMITH_API_KEY） |
| tracing.langsmith.project | str | self-evolving-kb | "self-evolving-kb" | tracing.py:115/121（写 env LANGSMITH_PROJECT） | 已引用 |
| tracing.langsmith.endpoint | str | https://api.smith.langchain.com | 同上 | tracing.py:116（写 env LANGSMITH_ENDPOINT） | 已引用 |
| tracing.local_json.trace_dir | str | data/traces | "data/traces" | tracing.py:134-135（LocalTraceCollector） | 已引用 |
| tracing.local_json.max_file_size_mb | int | 10 | 10 | 无（本地 trace 仅追加 JSONL，无轮转） | **死配置** |

---

## 9. `tools`（ToolsConfig，config.py:239-272）

| 键全路径 | 类型 | 现值 | 模型默认 | 读取点 | 状态 |
|---|---|---|---|---|---|
| tools.web_search.provider | str | bocha | "bocha" | registry.py:119-124 | 已引用 |
| tools.web_search.api_key | str | `${BOCHA_API_KEY}` | "" | registry.py:154（env 映射）/301/319 | 已引用（env: BOCHA_API_KEY） |
| tools.web_search.endpoint | str | https://api.bochaai.com/v1/web-search | 同上 | registry.py:155/302/321 | 已引用 |
| tools.web_search.max_results | int | 5 | 5 | registry.py:156/303/322 | 已引用 |
| tools.web_search.timeout_seconds | int | 10 | 10 | registry.py:157/304/323 | 已引用 |
| tools.web_search.max_retries | int | 2 | 2 | registry.py:158/305/324 | 已引用 |
| tools.filesystem.enabled | bool | false | False | 无 | **死配置**（预留，config.yaml:210-213） |
| tools.filesystem.allowed_paths | list[str] | [] | [] | 无 | **死配置** |
| tools.vector_store.enabled | bool | true | False | 无（实际装配开关是 memory.l3_knowledge.enabled，bootstrap.py:170） | **死配置** |
| tools.vector_store.provider | str | chroma | "chroma" | 无 | **死配置** |
| tools.vector_store.persist_path | str | data/chroma_db | "data/chroma_db" | bootstrap.py:176/182 | 已引用 |
| tools.document_parser.enabled | bool | true | False | 无 | **死配置** |
| tools.image_analysis.enabled | bool | true | —（无模型字段） | 无 | **死配置** |
| tools.image_analysis.vision_llm.base_url | str | `${VISION_LLM_BASE_URL:https://dashscope.aliyuncs.com/compatible-mode/v1}` | — | 无 | **死配置** |
| tools.image_analysis.vision_llm.api_key | str | `${VISION_LLM_API_KEY:}` | — | 无 | **死配置** |
| tools.image_analysis.vision_llm.model | str | `${VISION_LLM_MODEL:qwen-vl-plus}` | — | 无 | **死配置** |
| tools.image_analysis.ocr.enabled | bool | true | — | 无 | **死配置** |
| tools.image_analysis.ocr.lang | str | ch | — | 无 | **死配置** |
| tools.image_analysis.storage.save_original | bool | true | — | 无 | **死配置** |
| tools.image_analysis.storage.path | str | data/uploads/images | — | 无 | **死配置** |

> ⚠️ 整段 `tools.image_analysis.*`（config.yaml:223-238）：ToolsConfig 无对应模型字段（config.py:267-272 仅 web_search/filesystem/vector_store/document_parser）→ Pydantic extra=ignore **加载时整段静默丢弃**。图片处理实际配置走环境变量直读：`IMAGE_ANALYSIS_ENABLED`/`VISION_LLM_BASE_URL`/`VISION_LLM_API_KEY`/`VISION_LLM_MODEL`/`OCR_ENABLED`/`OCR_LANG`（upload.py:103-116，非 config.yaml）。

---

## 10. `storage`（StorageConfig，config.py:275-281）

| 键全路径 | 类型 | 现值 | 模型默认 | 读取点 | 状态 |
|---|---|---|---|---|---|
| storage.backend | str | local_json | "local_json" | 无（JSONStorage 恒被实例化，bootstrap.py:151-154） | **死配置** |
| storage.data_dir | str | （yaml 无此键） | "" | bootstrap.py:206（为空则回落 `config.yaml` 所在目录/data） | 已引用（仅模型默认值被消费） |
| storage.conversations_dir | str | data/conversations | "data/conversations" | bootstrap.py:153 | 已引用 |
| storage.index_file | str | data/index.json | "data/index.json" | bootstrap.py:152 | 已引用 |
| storage.postgres.url | str | `${DATABASE_URL}` | dict 兜底{} | 无（全 app 无 DATABASE_URL 读取） | **死配置**（Phase2 预留） |
| storage.postgres.pool_size | int | 10 | — | 无 | **死配置** |

---

## 11. `security`（SecurityConfig，config.py:284-289）

| 键全路径 | 类型 | 现值 | 模型默认 | 读取点 | 状态 |
|---|---|---|---|---|---|
| security.pii_masking | bool | true | True | 无（实际脱敏由 logging.redact_fields 实现，见 §12） | **死配置** |
| security.prompt_injection_guard | bool | true | True | 无 | **死配置** |
| security.max_input_length | int | 8000 | 8000 | 无 | **死配置** |
| security.blocked_patterns | list[str] | 2 条正则 | [] | 无 | **死配置** |

---

## 12. `api` + `logging`（ApiConfig/LoggingConfig，config.py:292-322）

| 键全路径 | 类型 | 现值 | 模型默认 | 读取点 | 状态 |
|---|---|---|---|---|---|
| api.host | str | 0.0.0.0 | "0.0.0.0" | server.py:145（仅启动日志；uvicorn 实际绑定由外部启动参数决定） | 已引用（弱：仅日志） |
| api.port | int | 8000 | 8000 | server.py:146（同上） | 已引用（弱：仅日志） |
| api.cors_origins | list[str] | localhost:3000/5173/3004 | [] | middleware.py:22；server.py:288 | 已引用 |
| api.rate_limit.enabled | bool | true | False | server.py:204-219（挂载 RateLimitMiddleware） | 已生效（2026-09-10 起） |
| api.rate_limit.requests_per_minute | int | 60 | 60 | server.py:207（默认组） | 已生效 |
| api.auth.token_expire_hours | int | 2160（90 天） | 2160 | auth.py:71（每次 create_jwt） | 已引用 |
| api.auth.jwt_secret | str | `${JWT_SECRET:-sekb-dev-secret-change-in-production}` | "" | 无（鉴权直接读 env `JWT_SECRET`：auth.py:39、启动校验 bootstrap.py:128-130；该 key 恒为空，见 §0 展开缺陷） | **死配置**（双重失效） |
| api.auth.password_min_length | int | 8 | 8 | 无（注册模型硬编码 min_length=8，user.py:45） | **死配置** |
| api.auth.rate_limit_login_per_minute | int | 5 | 5 | 无 | **死配置** |
| logging.level | str | INFO | "INFO" | logging.py:80/84/88 | 已引用 |
| logging.format | str | json | "json" | logging.py:67 | 已引用 |
| logging.log_dir | str | data/logs | "data/logs" | logging.py:93-96 | 已引用 |
| logging.max_file_size_mb | int | 50 | 50 | logging.py:97 | 已引用 |
| logging.backup_count | int | 7 | 7 | logging.py:98 | 已引用 |
| logging.redact_fields | list[str] | [api_key, authorization, token] | 同上 | logging.py:54（_redact_processor，33） | 已引用 |

---

## 13. `news`（NewsConfig，config.py:331-347；模型默认多为 False/空）

| 键全路径 | 类型 | 现值 | 模型默认 | 读取点 | 状态 |
|---|---|---|---|---|---|
| news.enabled | bool | true | False | bootstrap.py:232（NewsAgent 装配开关）；server.py:135-139（调度器启动条件） | 已引用 |
| news.llm_role | str | news_report | "chat_simple" | service.py:133（传给日报生成器，取 LLM 角色） | 已引用 |
| news.time_window_hours | int | 24 | 24 | service.py:113 | 已引用 |
| news.max_items | int | 100 | 100 | 无 | **死配置** |
| news.report_dir | str | data/news | "data/news" | service.py:50（NewsStorage） | 已引用 |
| news.retention_days | int | 70 | 70 | service.py:50 | 已引用 |
| news.daily_cron | str | "0 8 * * *" | 同上 | scheduler.py:36（CronTrigger.from_crontab） | 已引用（news 启用时） |
| news.weekly_cron | str | "0 8 * * 0" | 同上 | scheduler.py:43（APScheduler day_of_week 0=周一，写 1 会落到周二） | 已引用 |
| news.monthly_cron | str | "0 8 1 * *" | 同上 | scheduler.py:50 | 已引用 |
| news.timezone | str | Asia/Shanghai | "Asia/Shanghai" | scheduler.py:36/43/50（from_crontab timezone） | 已引用 |
| news.rss_sources | list[str] | 34 个 URL（含容器内 `http://rsshub:1200/…` 5 类 8 条） | [] | bootstrap.py:236（数量日志）；service.py:38（RSSFetcher） | 已引用 |
| news.keywords | list[str] | 20 条（AI/Agent/大模型/LLM/RAG/多模态/…/Web） | [] | service.py:44（∪ 大类关键词去重） | 已引用 |
| news.exclude_keywords | list[str] | [早报] | [] | service.py:48/122 | 已引用 |
| news.min_items_per_category | int | 10 | 10 | service.py:136（getattr 兜底） | 已引用 |
| news.categories | list[{name,keywords}] | 11 大类 | [] | service.py:45/134（NewsFilter/生成器） | 已引用 |

---

## 14. `job`（JobConfig，config.py:350-364）

| 键全路径 | 类型 | 现值 | 模型默认 | 读取点 | 状态 |
|---|---|---|---|---|---|
| job.enabled | bool | true | False | bootstrap.py:240（JobAgent 装配开关） | 已引用 |
| job.llm_role | str | job_analysis | "job_analysis" | bootstrap.py:244（装配日志；JobAgent 内部按此取 LLM） | 已引用 |
| job.default_keyword | str | Agent | "Agent" | routes/job.py:96/209 | 已引用 |
| job.default_city | str | 北京 | "北京" | routes/job.py:210 | 已引用 |
| job.default_city_code | str | "010" | "010" | 无 | **死配置** |
| job.default_min_salary_k | int | 50 | 50 | agents/job/market.py:232 | 已引用 |
| job.exclude_companies | list[str] | 16 项大厂名 | 内置同 16 项 | agents/job/market.py:233；routes/job.py:121（collector） | 已引用 |

---

## 15. 汇总与异常清单（T2 域）

### 15.1 配置键数量
- config.yaml 叶键共 **178**（含列表元素）；本表按「键」粒度列出 **约 105 个键**（列表类键合并为一行，如 rss_sources/keywords/categories 等）。
- **死配置：49 个**（backend/app 全量无读取处）：
  - `app.debug`(1)
  - `intent_routing.enable_pre_retrieval`(1)
  - `memory.l2_session.*`(3：enabled/max_items/eviction)
  - `memory.l3_knowledge.eviction.*`(3)
  - `reflection.adaptive.*`(2) + `reflection.sampling.rate`(1)
  - `evaluation.{enabled,log_to_file,log_path,regression.*}`(5)
  - `cost_control.{per_conversation_token_limit,daily_budget_usd,reasoner_ratio_alert_above,auto_downgrade_on_budget}`(4)
  - `tracing.{sample_rate,local_json.max_file_size_mb}`(2)
  - `tools.filesystem.*`(2)、`tools.vector_store.{enabled,provider}`(2)、`tools.document_parser.enabled`(1)、`tools.image_analysis.*`(8)
  - `storage.{backend,postgres.*}`(3)
  - `security.*`(4)
  - `api.rate_limit.*`(2)、`api.auth.{jwt_secret,password_min_length,rate_limit_login_per_minute}`(3)
  - `news.max_items`(1)、`job.default_city_code`(1)

### 15.2 发现的异常/漂移
1. **`${VAR:-default}` 语法不被支持**（config.yaml 4 处：`api.auth.jwt_secret`、`tools.image_analysis.vision_llm.{base_url,api_key,model}`）：`_expand_env_vars` 把整串当变量名（config.py:391-404）→ 无论 env 是否设置都展开为 `""`。其中 jwt_secret 与 image_analysis 本身又是死配置，影响被“双保险”掩盖；但若将来有人把 `:-` 语法用于在用配置（如 base_url/api_key），会导致空值/启动校验失败。
2. **密钥不读 config**：JWT 密钥由 auth.py:39 直接读 env `JWT_SECRET`（bootstrap.py:128-130 启动强校验长度≥32、无弱占位词），`api.auth.jwt_secret` 键形同虚设；yaml 中该键示例值即含弱占位词 `sekb-dev…`（config.yaml:276），即便语法修正也不该直接使用。
3. **`tools.image_analysis.*` 整段未建模**：ToolsConfig（config.py:267-272）没有 image_analysis 字段，yaml 该段被 Pydantic 静默丢弃；图片处理实际由 upload.py:103-116 读同名环境变量（IMAGE_ANALYSIS_ENABLED/VISION_LLM_*/OCR_*）。yaml 与实现两套来源。
4. **限流与白名单配置分离**：`api.rate_limit.*` 与 `security.*`（注入防护、输入长度、pii）全部死配置；实际鉴权相关行为来自 env（`JWT_SECRET`、`ALLOWED_EMAILS`，access.py:19-24）而非 config.yaml。
5. **模型默认与 yaml 现值存在差异**（历史漂移）：`memory.l3_knowledge.enabled`（模型 False / yaml true）、`news.*`（模型全 False/空 / yaml 启用并填全）、`job.enabled`（模型 False / yaml true）、`tools.vector_store.enabled`（模型 False / yaml true）——以 yaml 为准生效。
6. **`evaluation`/`cost_control` 体系与注释不符**：yaml 与 config.py 都完整定义（阈值、回归、预算、定价），但运行代码仅消费 `evaluation.metrics.*` 阈值与 `pricing` 定价；`enabled/log_to_file/log_path/regression`、预算四项全部无人读取——注释宣称的“评估日志”“预算控制/自动降级”现状未接线【推断·待验证：后端 tests/ 或外部脚本可能消费，不在 backend/app 范围】。
7. **JWT 时长文案漂移**：`api.auth.token_expire_hours` 现值与注释一致为 90 天（config.yaml:275-276），但 auth.py:68 docstring 写“72 小时”。
8. **yaml 头注释声称 docs/04-config-reference.md**（config.yaml:7）：docs/ 目录实际为 00-README~09-OBSERVABILITY 编号（docs/tech/ 见 00-README.md 等），04 文件名已变，注释指旧路径【推断·待验证：docs/tech/04-DATA-MODEL.md 与 04-config-reference 不是同一文件】。
