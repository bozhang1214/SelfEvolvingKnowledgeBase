---
title: 配置参考（Config Reference）
layer: 参考层
owner: SEKB Team
status: active
version: v1.0.0
last-updated: 2026-09-16
based-on-commit: 44dfed1
related: [01-ARCHITECTURE, 04-DATA-MODEL, 11-EVOLUTION]
---

# 06 · 配置参考（CONFIG-REFERENCE）

> **本文回答什么问题**：config.yaml 每个配置项怎么配？环境变量有哪些？哪些配置其实没被用？
> **适合谁读**：运维、后端开发者、部署者。
> **读完能做什么**：能改对配置、排查「改了没生效」，能识别死配置与陷阱。

> **数据源**：事实表 [.facts/T2-config.md](./.facts/T2-config.md)（基于 1ffb13c 抽取，**已落后于当前 config.yaml**：现为 15 个顶层域、含 `memory.l3_knowledge.retrieval.*`、`evaluation.ragas.*`、`cost_control.usage.*`、`job.transport/MCP` 等新键，引用该表字段数/行号时请以本文与 config.yaml 为准）。⚠️ 修改配置需**重启服务**生效（无热加载，config.yaml:5）。

---

## 1. 加载机制（必须先懂）

| 事实 | 说明 |
|------|------|
| 入口路径 | `os.getenv("SEKB_CONFIG_PATH", "config.yaml")`（server.py:129） |
| 加载流程 | `load_dotenv()` → `yaml.safe_load` → 递归展开 `${VAR}` → `AppConfig(**expanded)` Pydantic 强校验（config.py:449-487） |
| 展开语法 | 匹配 `\$\{([^}]+)\}`（config.py:410）；**支持默认值语法**——`${VAR:-default}` 与 `${VAR:default}` 等价，变量未设置或为空串时取 default（`_resolve_env_ref`，config.py:413-431） |
| 单例 | `get_config()` lru_cache(1)，进程内一次加载（config.py:490-501） |
| extra | Pydantic 默认 extra=ignore → yaml 中未建模子树被**静默丢弃**（如 tools.image_analysis.*） |
| 校验失败 | 抛 `ConfigError` → 启动失败 |

**环境变量一览**（真实值脱敏）：

| 变量 | 用途 | 读取点 |
|------|------|--------|
| `DEEPSEEK_API_KEY` | 主 LLM key | config.yaml llm.api_key → llm_factory.py:497 |
| `BOCHA_API_KEY` | web_search 工具 key | registry.py:154 |
| `BOCHA_ENDPOINT` | 博查搜索端点（默认官方端点） | bocha_server.py:414 |
| `BOCHA_MAX_RESULTS` | 博查返回条数（默认 5） | bocha_server.py:415 |
| `BOCHA_TIMEOUT_SECONDS` | 博查超时秒数（默认 10） | bocha_server.py:416 |
| `BOCHA_MAX_RETRIES` | 博查重试次数（默认 2） | bocha_server.py:417 |
| `JWT_SECRET` | JWT 签名（≥32 字符，启动强校验，不读 config） | auth.py:40-57；bootstrap.py:164-166 |
| `ALLOWED_EMAILS` | 白名单（**留空=全员完整功能；填写则名单外降级为预览**） | access.py:21 |
| `LANGSMITH_API_KEY` / `LANGSMITH_PROJECT` / `LANGSMITH_ENDPOINT` | 可选链路追踪（由 tracing.langsmith.* 写入 env） | tracing.py:40-43 |
| `NEWS_ALERT_WEBHOOK_URL` | 资讯失败告警的飞书网关地址（默认同网网关；`off`/`none`/`0` 关闭） | alerts.py:55 |
| `VISION_LLM_BASE_URL` / `VISION_LLM_API_KEY` / `VISION_LLM_MODEL` | 图片视觉 LLM（**不经 config.yaml**） | services/upload_service.py:94-102 |
| `IMAGE_ANALYSIS_ENABLED` / `OCR_ENABLED` / `OCR_LANG` | 图片分析开关（**不经 config.yaml**） | services/upload_service.py:94-102 |
| `SEKB_CONFIG_PATH` | 配置文件路径 | server.py:129 |
| `SEKB_AUDIT_DIR` | 审计日志目录（默认 `data/audit`） | audit.py:62 |

> 完整可复制模板见 `backend/.env.example`（本地开发，已逐项注释用途/默认值/不填的后果）与 `deploy/.env.prod.example`（生产，含 JWT_SECRET/图片分析/告警/DB 等）。

---

## 2. 分域配置字典（已提炼）

### 2.1 `app`
| 键 | 现值 | 状态 | 证据 |
|----|------|------|------|
| app.name / app.version | self-evolving-kb / 0.1.0 | 已引用 | bootstrap.py:172-173 |
| app.environment | `${ENVIRONMENT:-development}`（compose 里 backend 设 `ENVIRONMENT=production`） | 已引用（纯展示：启动日志 + `/health`） | bootstrap.py:174；health.py:95 |

### 2.2 `llm`
| 键 | 现值 | 状态 | 证据 |
|----|------|------|------|
| llm.provider | deepseek | 弱引用（仅 CLI 展示） | cli/main.py:221；cli/eval.py:141 |
| llm.api_key / base_url | `${DEEPSEEK_API_KEY}` / api.deepseek.com/v1 | 已引用 | llm_factory.py:497-498 |
| llm.timeout_seconds / max_retries / retry_backoff_seconds | 60 / 2 / [1,2] | 已引用（backoff 只取 [0]） | llm_factory.py:276-282；timeout 用法 llm_factory.py:503 |
| llm.fallback.* | reasoner_to_chat true / chat_to_error true | 已引用（降级目标已改为 deepseek-flash） | llm_factory.py:513-533 |
| llm.roles.* | **11 角色**（见下） | 已引用（动态字典） | llm_factory.py:484-486 |

**角色表**（llm.roles.<role>：model / temperature / max_tokens / response_format）。⚠️ 全部角色的 `model` 现均为 `deepseek-flash`（config.yaml:30-81）：

| 角色 | model | temp | max_tokens | json | 用途 |
|------|-------|------|-----------|------|------|
| supervisor | deepseek-flash | 0.1 | 500 | ✔ | 意图识别 |
| planner | deepseek-flash | 0.2 | 2000 | ✔ | 任务规划（TTFT 主因） |
| executor | deepseek-flash | 0.3 | 2000 | — | 工具+草稿 |
| critic / critic_complex | deepseek-flash | 0.0 | 1000/2000 | ✔ | 反思 |
| scribe | deepseek-flash | 0.2 | 800 | ✔ | 摘要+重要性评分（L1 压缩亦用） |
| chat_simple | deepseek-flash | 0.7 | 1000 | — | 闲聊（share 亦用） |
| news_report | deepseek-flash | 0.3 | 8000 | ✔ | 日报 |
| job_analysis | deepseek-flash | 0.3 | 4000 | ✔ | 职位分析 |
| rerank | deepseek-flash | 0.0 | 1000 | ✔ | 检索重排 / 查询改写 |
| ragas | deepseek-flash | 0.0 | 1000 | ✔ | RAG 质量裁判；亦作 LLM 注入检测角色 |

### 2.3 `intent_routing`
已引用：default_intent(web_default)、clarify_threshold(0.7)、kb_prefer_hit_threshold(0.85)、pre_retrieval_top_k(3)、kb_strict_keywords、realtime_keywords。死配置：`enable_pre_retrieval`。

### 2.4 `memory`
- L1 工作记忆（已引用）：enabled=true（Pydantic 校验**必须为 true**，否则启动失败）/ max_turns=8 / max_tokens=3000 / hard_token_limit=4000 / compress_strategy=hierarchical / max_compressed_summaries=3。另校验 max_tokens < hard_token_limit（config.py:139-153；short_term.py:96-100）。
- L2 会话记忆：**已接线启用**（config.yaml:121 enabled=true，2026-09-10 起）：enabled / max_items=50 / ttl_days=30 / redis_url 均由 bootstrap 读取装配 RedisSessionMemory（bootstrap.py:199-209）。死配置：`l2_session.eviction`（字符串 "lru"，无读取方）。
- L3 知识库：enabled=true（装配开关）、retrieval_top_k=5、importance_threshold=0.3、allow_hash_fallback=false（均已引用：bootstrap.py:252-264；builder.py:241-242）。**eviction.*（3 项）死配置**——`eviction.max_items/stale_days/similarity_merge_threshold` 仅建模，ChromaDB 无自动淘汰（config.py:101-105，全仓无读取方）。
- L3 `retrieval.*`（**新增，9 键，全部已引用**）：hybrid_enabled=true / bm25_enabled=true / bm25_cache_ttl=300 / bm25_page_size=1000 / rerank_enabled=false / rerank_top_n=0 / rerank_role=rerank / query_rewrite_enabled=false / query_rewrite_role=rerank，统一在 builder.py:221-234 消费（hybrid.py 内生效）。

### 2.5 `reflection`
已引用：policy(always，Pydantic 白名单仅 `always`)、max_replan=2、max_rewrite=1、model_switch_threshold=0.7（strategies/factory.py:28-32；base.py:26；critic.py:122-128；planner.py:54-61）。
**已删除的死键**：`adaptive.*`、`sampling.*` 已按 D3 从 yaml 与模型中移除，不再是配置项（若旧文档仍列出，属过期信息）。

### 2.6 `evaluation`
- **已引用**：metrics.*.{good,warn}（16 叶子，判级在 eval/assertion.py:281-288 读取阈值）。
- **已引用（新增）**：`ragas.role`(ragas) / `ragas.top_k`(5)——RAGAS 式检索评测裁判角色与检索条数（eval/rag_runner.py:100-103）。
- **死配置**：enabled / log_to_file / log_path / regression.*（5 项）——「评估日志」「回归门禁」未接线（全仓无读取方）。

### 2.7 `cost_control`
- **已引用**：`pricing.deepseek-flash.{input,output}`（0.00014/0.00028，llm_factory.py:533-538 按**模型名**查表计成本；表里只有 deepseek-flash，故所有角色都命中）。`usd_to_cny`(7.2) 在 bootstrap.py:230 传入 UsageService 做人民币换算（usage_service.py:105）。
- **已引用**：`usage.enabled`(true) / `usage.redis_url`——装配对话 token/费用统计（bootstrap.py:224-233）。
- **已删除的死键**：`per_conversation_token_limit`/`daily_budget_usd`/`reasoner_ratio_alert_above`/`auto_downgrade_on_budget` 已按 D3 从 yaml 与模型中移除；预算/自动降级功能仍未实现，但**已无对应配置键**（勿再按旧文档去配）。

### 2.8 `tracing`
已引用：provider(langsmith，非该值时追踪不启用) / langsmith.api_key / langsmith.project / langsmith.endpoint——写入 `LANGSMITH_*` 环境变量并设 `LANGCHAIN_TRACING_V2=true`（tracing.py:34-43）。
**已删除的死键**：`fallback_to_local_on_failure`、`local_json.trace_dir`、`local_json.max_file_size_mb`（本地 JSON 降级已按 D6 删除，yaml 中已无这些键）。

### 2.9 `tools`
- 已引用：web_search.*（provider/api_key/endpoint/max_results/timeout_seconds/max_retries → registry.py:118-158 以 `BOCHA_*` env 注入 MCP 子进程）；vector_store.persist_path(data/chroma_db → bootstrap.py:258,264)。
- **死配置**：filesystem.enabled / filesystem.allowed_paths、document_parser.enabled（仅建模，无读取方）。
- **image_analysis.*（8 叶子）死配置**：整段未建模，被 Pydantic extra=ignore 静默丢弃；图片配置实际走环境变量（见 §4.2）。`vector_store.enabled` / `vector_store.provider` 这类旧键**已从 yaml 删除**，不再是配置项。

### 2.10 `storage`
已引用：data_dir（**已接线**：为空串时回退到 config.yaml 同级的 `data/`，作为 user/share/chat_share 存储根目录 → bootstrap.py:288-296）、conversations_dir + index_file（bootstrap.py:189-191）。死配置：backend(local_json)、postgres.url / pool_size（Phase2 预留，`postgres` 建为 `dict[str,Any]`，无读取方）。

### 2.11 `security`（**部分已接线**）
- **已引用**：prompt_injection_guard(true) / max_input_length(8000) / blocked_patterns(2 条正则) / prompt_injection_use_llm(false) / prompt_injection_llm_role(ragas)——`/chat` 与 `/share` 入口调用 guard（chat.py:135-145；share.py:414-421；guard.py:64-115）。规则层 = 长度上限 + 正则；LLM 层默认关（额外延迟）。
- **死配置**：`pii_masking`——无读取方；日志脱敏实际由 `logging.redact_fields` 实现（logging.py:28-33,54）。
- 鉴权/JWT 的防护走 env（`JWT_SECRET`/`ALLOWED_EMAILS`），见 §1。

### 2.12 `api` + `logging`
- 已引用：cors_origins（server.py:332；middleware.py:41，为空**不再**回退 `"*"`）、logging.*（level/format/json/log_dir/max_file_size_mb/backup_count/redact_fields → core/logging.py:54-102）。
- **rate_limit.*：已启用并生效**（enabled=true → server.py:204-217 挂载纯 ASGI 限流中间件，按「IP+路由前缀+方法」计数）：requests_per_minute=60 / share_per_minute=20 / upload_per_minute=30 / job_per_minute=30 / **news_per_minute=120（资讯读）** / **news_generate_per_minute=6（资讯生成，`POST /api/v1/news/`）**。
- auth.token_expire_hours=**168（7 天）**（auth.py:83 读取；config.yaml:300-302 记录 2026-09-16 从 2160h 收紧）。
- 死配置：auth.jwt_secret（**已废弃**，实际只读 env `JWT_SECRET`）、auth.password_min_length。
- 已引用：auth.rate_limit_login_per_minute=5（作为 `/api/v1/auth/` 路由限额 → server.py:210）。
- host/port 仅启动日志引用（server.py:143-147；实际绑定由启动参数 `uvicorn --host/--port` 决定）。

### 2.13 `news`
已引用：enabled、llm_role(news_report)、time_window_hours(24)、report_dir、retention_days(70)、daily/weekly/monthly_cron、timezone(Asia/Shanghai)、rss_sources(**38 条**)、keywords(**20 条**，含移动端/前端/跨端)、exclude_keywords([早报])、min_items_per_category(10)、categories(11 类)。死配置：max_items(100，无读取方)。

### 2.14 `job`
已引用：enabled、llm_role、default_keyword(Agent)、default_city(北京)、default_min_salary_k(50)、exclude_companies。死配置：default_city_code。
**MCP 传输（新增，全部已引用）**：`transport`(mcp，`direct` 为回滚开关) / `mcp_command`(jobcopilot-mcp) / `mcp_timeout_s`(180.0) / `mcp_connect_timeout_s`(30.0)——bootstrap.py:119-126 预热内核，agents/job/market.py:196-198 分发，agents/job/mcp_client.py:235-238 建连与超时。

---

## 3. 死配置清单（汇总）

> **状态（基于 HEAD 44dfed1）**：此前 D3/D6/P2 已删除的死键——`reflection.adaptive.*`/`sampling.*`、`cost_control` 预算 4 项、`tracing.sample_rate`/`local_json.*`、`tools.vector_store.enabled`/`provider`、`app.debug`——**已从 config.yaml 中移除，不再是配置项**。同时若干旧「死配置」已接线（`memory.l2_session.*`、`cost_control.usage.*`、`api.rate_limit.*`、`security.prompt_injection_*`/`max_input_length`/`blocked_patterns`、`evaluation.ragas.*`），勿再按本表历史版本误判。当前仍无读取方的死键如下：

| 域 | 死配置 | 建议（归 11-EVOLUTION） |
|----|--------|------------------------|
| intent_routing | enable_pre_retrieval | 清理（预检索实际恒执行） |
| memory.l2 | eviction | 字符串占位，无读取方 → 删 |
| memory.l3 | eviction.max_items/stale_days/similarity_merge_threshold | ChromaDB 淘汰未实现 → 需实现或删 |
| evaluation | enabled/log_to_file/log_path/regression.*(2) | 评估日志/回归未接线 |
| tools | filesystem(2)/document_parser(1)/image_analysis(8) | image_analysis 走 env；filesystem/document_parser 未接线 |
| storage | backend/postgres(2) | 后端未切换预留 |
| security | pii_masking(1) | 日志脱敏已由 logging.redact_fields 覆盖 |
| api | auth.jwt_secret / auth.password_min_length | jwt 走 env；密码长度未校验 |
| news/job | max_items / default_city_code | 清理 |

---

## 4. 配置陷阱（务必知晓）

1. **`${VAR:-default}` 语法有效**（旧文档曾记为无效，已不成立）：`_resolve_env_ref` 同时支持 `:-` 与 `:` 默认值（config.py:413-431）。当前 config.yaml 有 4 处使用：`app.environment` 的 `${ENVIRONMENT:-development}` 与 `tools.image_analysis.vision_llm` 3 键——前者的默认值确实生效；后 3 键因整段未建模仍被丢弃（见下条）。
2. **tools.image_analysis.* 有两套来源**：yaml 段被 Pydantic 丢弃，真实走 `VISION_LLM_*`/`OCR_*`/`IMAGE_ANALYSIS_ENABLED` 环境变量（services/upload_service.py:94-102）。配置图片能力请改 env，不是 config.yaml。
3. **JWT_SECRET 不读 config**：改 `api.auth.jwt_secret`（现值 `""`，已废弃）无效；须设 env `JWT_SECRET`（≥32 字符、无弱占位词，否则启动 fail-fast，auth.py:40-57）。
4. **限流已生效（勿再当摆设）**：`api.rate_limit.*` 已启用并挂载中间件，资讯**读(120)/生成(6)** 是分开限额的；单 IP 超出即 429。预算控制仍未实现，但对应配置键已删除；注入防护的规则层（长度+正则）已接线，LLM 层默认关。
5. **模型默认与 yaml 漂移**（l3.enabled/l1.enabled/news.enabled/job.enabled）：以 config.yaml 现值为准生效。

---

## 5. 配置变更影响矩阵（示例）

| 改动 | 影响面 |
|------|--------|
| llm.roles.planner.model 改 deepseek-reasoner | 规划质量可能提升，TTFT 与成本上升（现全角色为 deepseek-flash） |
| memory.l3_knowledge.enabled=false | L3 不装配：RAG 全跳过、上传/知识路由 503、分享知识禁用 |
| memory.l3_knowledge.retrieval.hybrid_enabled=false | 退回纯向量检索（去掉 BM25 多路召回 + RRF 融合） |
| api.rate_limit.enabled=false | 全局限流中间件不挂载（含 auth/share/upload/job/news 各组限额） |
| news.enabled=false | 资讯 Agent 与调度器不启动；news API 503 |
| job.enabled=false | JobAgent 不启动；job API 503 |
| job.transport=direct | 回滚为进程内直连 jobcopilot（不经 MCP 子进程） |
| logging.level=DEBUG | 全量结构化日志（含脱敏字段处理） |
| api.cors_origins | 前端跨域来源白名单 |

---

## 6. 已知缺口与待确认项

- 死配置是否清理或实现（ChromaDB 淘汰、evaluation 日志/回归、filesystem/document_parser）需决策 → 归 11-EVOLUTION/BACKLOG。
- 与 `docs/tech/漂移清单.md` 已对齐：D-05（预算键已删）、D-06（注入防护 4/5 已接线，余 `pii_masking`）、D-12（`:-` 默认值已支持）均已同步。

---

## 相关文档

- [01-ARCHITECTURE.md](./01-ARCHITECTURE.md)
- [04-DATA-MODEL.md](./04-DATA-MODEL.md)
- [11-EVOLUTION.md](./11-EVOLUTION.md)
- 事实表：[.facts/T2-config.md](./.facts/T2-config.md)
