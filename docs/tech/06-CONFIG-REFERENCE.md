---
title: 配置参考（Config Reference）
layer: 参考层
owner: SEKB Team
status: active
version: v1.0.0
last-updated: 2026-09-09
based-on-commit: 1ffb13c
related: [01-ARCHITECTURE, 04-DATA-MODEL, 11-EVOLUTION]
---

# 06 · 配置参考（CONFIG-REFERENCE）

> **本文回答什么问题**：config.yaml 每个配置项怎么配？环境变量有哪些？哪些配置其实没被用？
> **适合谁读**：运维、后端开发者、部署者。
> **读完能做什么**：能改对配置、排查「改了没生效」，能识别死配置与陷阱。

> **数据源**：事实表 [.facts/T2-config.md](./.facts/T2-config.md)（config.yaml 178 叶键逐项 + file:line），本文为提炼。⚠️ 修改配置需**重启服务**生效（无热加载，config.yaml:5）。

---

## 1. 加载机制（必须先懂）

| 事实 | 说明 |
|------|------|
| 入口路径 | `os.getenv("SEKB_CONFIG_PATH", "config.yaml")`（server.py:129） |
| 加载流程 | `load_dotenv()` → `yaml.safe_load` → 递归展开 `${VAR}` → `AppConfig(**expanded)` Pydantic 强校验（config.py:412-450） |
| 展开语法 | 仅支持 `\$\{([^}]+)\}`；⚠️ **不支持 `:-` 默认值语法**（config.py:391-404） |
| 单例 | `get_config()` lru_cache(1)，进程内一次加载（config.py:453-464） |
| extra | Pydantic 默认 extra=ignore → yaml 中未建模子树被**静默丢弃**（如 tools.image_analysis.*） |
| 校验失败 | 抛 `ConfigError` → 启动失败 |

**环境变量一览**（真实值脱敏）：

| 变量 | 用途 | 读取点 |
|------|------|--------|
| `DEEPSEEK_API_KEY` | 主 LLM key | config.yaml llm.api_key → llm_factory.py:497 |
| `BOCHA_API_KEY` | web_search 工具 key | registry.py:154 |
| `JWT_SECRET` | JWT 签名（≥32 字符，启动强校验，不读 config） | auth.py:39；bootstrap.py:128-130 |
| `ALLOWED_EMAILS` | 白名单（full vs preview） | access.py:19-24 |
| `LANGSMITH_API_KEY` / `LANGSMITH_PROJECT` | 可选链路追踪 | tracing.py:110-121 |
| `VISION_LLM_BASE_URL` / `VISION_LLM_API_KEY` / `VISION_LLM_MODEL` | 图片视觉 LLM（**不经 config.yaml**） | upload.py:103-116 |
| `IMAGE_ANALYSIS_ENABLED` / `OCR_ENABLED` / `OCR_LANG` | 图片分析开关 | upload.py:103-116 |
| `SEKB_CONFIG_PATH` | 配置文件路径 | server.py:129 |

---

## 2. 分域配置字典（已提炼）

### 2.1 `app`
| 键 | 现值 | 状态 | 证据 |
|----|------|------|------|
| app.name / app.version | self-evolving-kb / 0.1.0 | 已引用 | bootstrap.py:136-137 |
| app.environment | development | 已引用 | bootstrap.py:138 |
| app.debug | true | **死配置** | 无读取 |

### 2.2 `llm`
| 键 | 现值 | 状态 | 证据 |
|----|------|------|------|
| llm.provider | deepseek | 弱引用（仅 CLI 展示） | cli/main.py:176 |
| llm.api_key / base_url | `${DEEPSEEK_API_KEY}` / api.deepseek.com/v1 | 已引用 | llm_factory.py:497-498 |
| llm.timeout_seconds / max_retries / retry_backoff_seconds | 60 / 2 / [1,2] | 已引用（backoff 只取 [0]） | llm_factory.py:270-283 |
| llm.fallback.* | reasoner→chat true / chat→error true | 已引用 | llm_factory.py:511-527 |
| llm.roles.* | 9 角色（见下） | 已引用（动态字典） | llm_factory.py:484 |

**角色表**（llm.roles.<role>：model / temperature / max_tokens / response_format）：

| 角色 | model | temp | max_tokens | json | 用途 |
|------|-------|------|-----------|------|------|
| supervisor | deepseek-chat | 0.1 | 500 | ✔ | 意图识别 |
| planner | deepseek-reasoner | 0.2 | 2000 | ✔ | 任务规划（TTFT 主因） |
| executor | deepseek-chat | 0.3 | 2000 | — | 工具+草稿 |
| critic / critic_complex | chat / reasoner | 0.0 | 1000/2000 | ✔ | 反思 |
| scribe | deepseek-chat | 0.2 | 800 | ✔ | 最终组织 |
| chat_simple | deepseek-chat | 0.7 | 1000 | — | 闲聊（share 亦用） |
| news_report | deepseek-chat | 0.3 | 8000 | ✔ | 日报 |
| job_analysis | deepseek-chat | 0.3 | 4000 | ✔ | 职位分析 |

### 2.3 `intent_routing`
已引用：default_intent(web_default)、clarify_threshold(0.7)、kb_prefer_hit_threshold(0.85)、pre_retrieval_top_k(3)、kb_strict_keywords、realtime_keywords。死配置：`enable_pre_retrieval`。

### 2.4 `memory`
- L1 工作记忆（已引用）：max_turns=8 / max_tokens=3000 / hard_token_limit=4000 / compress_strategy=hierarchical / max_compressed_summaries=3。
- L2 会话记忆：**全死配置**（enabled=false，Phase 预留，代码零引用）。
- L3 知识库：enabled=true（装配开关）、retrieval_top_k=5、importance_threshold=0.3、allow_hash_fallback=false（均已引用）；**eviction.*（3 项）死配置**——ChromaDB 无自动淘汰。

### 2.5 `reflection`
已引用：policy(always)、max_replan=2、model_switch_threshold=0.7。死配置：adaptive.*(2)、sampling.rate（策略仅 always 落地，adaptive/sampling 降级回 always）。

### 2.6 `evaluation`
- **已引用**：metrics.*.{good,warn}（16 叶子，eval/metrics.py:187-209 判级）。
- **死配置**：enabled / log_to_file / log_path / regression.*（5 项）——「评估日志」「回归门禁」未接线。

### 2.7 `cost_control`
- **已引用**：pricing.{deepseek-chat,deepseek-reasoner}.{input,output}（llm_factory.py:531-534 计成本）。
- **死配置**：per_conversation_token_limit / daily_budget_usd / reasoner_ratio_alert_above / auto_downgrade_on_budget（4 项）——预算控制/自动降级**未接线**。

### 2.8 `tracing`
已引用：provider(langsmith)、fallback_to_local_on_failure、langsmith.*(写 env)、local_json.trace_dir。死配置：sample_rate、local_json.max_file_size_mb。

### 2.9 `tools`
- 已引用：web_search.*（bocha）、vector_store.persist_path(data/chroma_db)。
- **死配置**：filesystem.*、vector_store.enabled/provider、document_parser.enabled、**image_analysis.*(8)**（整段未建模，被 Pydantic 静默丢弃；图片配置走环境变量）。

### 2.10 `storage`
已引用：data_dir（仅模型默认）、conversations_dir、index_file。死配置：backend(local_json)、postgres.url / pool_size（Phase2 预留）。

### 2.11 `security`（全死）
pii_masking / prompt_injection_guard / max_input_length / blocked_patterns 均无读取；实际防护走 env（JWT_SECRET/ALLOWED_EMAILS）+ logging.redact_fields。

### 2.12 `api` + `logging`
- 已引用：cors_origins、auth.token_expire_hours=2160（90 天）、logging.*（level/format/json/轮转/redact_fields）。
- 死配置：rate_limit.*(2)、auth.jwt_secret（实际读 env）、auth.password_min_length、rate_limit_login_per_minute。
- host/port 仅启动日志引用（实际绑定由启动参数）。

### 2.13 `news`
已引用：enabled、llm_role(news_report)、time_window_hours、report_dir、retention_days(70)、daily/weekly/monthly_cron、timezone(Asia/Shanghai)、rss_sources(34)、keywords、exclude_keywords、min_items_per_category、categories(11 类)。死配置：max_items。

### 2.14 `job`
已引用：enabled、llm_role、default_keyword(Agent)、default_city(北京)、default_min_salary_k(50)、exclude_companies。死配置：default_city_code。

---

## 3. 死配置清单（49 个，汇总）

> **重构后状态（2026-09-09，WP6）**：已删除 11 个死键——`reflection.adaptive.*(2)`/`sampling.rate`（D3）、`cost_control` 预算 4 项（D3）、`tracing.sample_rate`/`local_json.*(2)`（P2-12/D6）、`tools.vector_store.enabled`（P2-P2-05）。剩余多为「Phase 2 预留」（memory.l2/eviction、tools.filesystem/image_analysis、storage postgres）或低价值死键（evaluation 5 项、app.debug、intent_routing.enable_pre_retrieval、vector_store.provider、document_parser.enabled、news/job 2 项），待后续清理或在 11-EVOLUTION 归口。

| 域 | 死配置 | 建议（归 11-EVOLUTION） |
|----|--------|------------------------|
| app | debug | 删除或接 logger |
| intent_routing | enable_pre_retrieval | 清理 |
| memory.l2 | enabled/max_items/eviction | Phase 预留，标注 |
| memory.l3 | eviction.max_items/stale_days/similarity_merge_threshold | ChromaDB 淘汰未实现 → 需实现或删 |
| reflection | adaptive.*(2)/sampling.rate | 策略未实现 |
| evaluation | enabled/log_to_file/log_path/regression.*(2) | 评估日志/回归未接线 |
| cost_control | 4 项预算 | 预算控制未接线（注释宣称有） |
| tracing | sample_rate/max_file_size_mb | 未接线 |
| tools | filesystem(2)/vector_store(2)/document_parser(1)/image_analysis(8) | image_analysis 走 env；vector_store 开关错位 |
| storage | backend/postgres(2) | 后端未切换预留 |
| security | 4 项 | 注入防护/长度限制未实现（BACKLOG P1） |
| api | rate_limit(2)/auth(3) | 限流未挂载、jwt 走 env |
| news/job | max_items / default_city_code | 清理 |

---

## 4. 配置陷阱（务必知晓）

1. **`${VAR:-default}` 语法无效**：config.yaml 现 4 处使用（jwt_secret + vision_llm 3 键），展开后恒为空串。若未来用于在用配置会导致启动失败。**避免使用 `:-` 语法**；用 `${VAR}` + env 兜底，或 Pydantic 默认值。
2. **tools.image_analysis.* 有两套来源**：yaml 段被丢弃，真实走 `VISION_LLM_*`/`OCR_*`/`IMAGE_ANALYSIS_ENABLED` 环境变量（upload.py:103-116）。配置图片能力请改 env，不是 config.yaml。
3. **JWT_SECRET 不读 config**：改 `api.auth.jwt_secret` 无效；须设 env `JWT_SECRET`（≥32 字符、无弱占位词，否则启动 fail-fast）。
4. **限流/预算/注入防护目前不生效**：对应键已定义但未接线——别指望这些配置挡住请求；实际保护是 nginx limit_req + 白名单 env。
5. **模型默认与 yaml 漂移**（l3.enabled/news/job/vector_store.enabled）：以 config.yaml 现值为准生效。

---

## 5. 配置变更影响矩阵（示例）

| 改动 | 影响面 |
|------|--------|
| llm.roles.planner.model 改 chat | 规划延迟大降，规划质量可能下降（ADR-05） |
| memory.l3_knowledge.enabled=false | L3 不装配：RAG 全跳过、上传/知识路由 503、分享知识禁用 |
| news.enabled=false | 资讯 Agent 与调度器不启动；news API 503 |
| job.enabled=false | JobAgent 不启动；job API 503 |
| logging.level=DEBUG | 全量结构化日志（含脱敏字段处理） |
| api.cors_origins | 前端跨域来源白名单 |

---

## 6. 已知缺口与待确认项

- 死配置是否清理或实现（预算控制、ChromaDB 淘汰、限流挂载）需决策 → 归 11-EVOLUTION/BACKLOG。
- config.yaml 头注释引用的 `docs/04-config-reference.md` 旧路径（现为 docs/tech/04-DATA-MODEL.md）待修正。

---

## 相关文档

- [01-ARCHITECTURE.md](./01-ARCHITECTURE.md)
- [04-DATA-MODEL.md](./04-DATA-MODEL.md)
- [11-EVOLUTION.md](./11-EVOLUTION.md)
- 事实表：[.facts/T2-config.md](./.facts/T2-config.md)
