---
title: T8 可观测性清单（事实表）
layer: 事实层（SSOT 工作产物）
owner: docs-eng
status: draft
version: v0.1.0
---

# T8 可观测性清单

> ⚠️ **本表是 2026-09-09 逆向分析期的「编写用工作产物」，可能已落后。**
> 2026-09-16 复核确认：`T1-routes`（65 条 vs 实测 75 个端点）、`T2-config`、
> `T8-observability` 的**行号与计数已过期**。写文档时请以
> **代码本身 + `docs/tech/*.md`** 为准；本表仅作线索索引，不要把它的行号当断言。
> （不随技术文档一起维护的原因见 `docs/tech/漂移清单.md` DOC-11。）


> 证据纪律：每条记录带 `文件:行号`；推断标【推断·待验证】；密钥/内网 IP 一律 `<REDACTED>`；只描述现状（as-is）。
> 采集时间：2026-09（依当前仓库快照）。

## 1. Prometheus 指标清单（backend/app/core/metrics.py）

全部自定义指标以 `sekb_` 前缀命名（metrics.py:12 注释）；暴露端 `GET /metrics`（见 §2）。埋点一律 try/except 吞异常不影响主流程（metrics.py:14,310-312）。

### 1.1 指标明细表（共 23 个自定义指标 = Counter 14 / Gauge 5 / Histogram 4）

| 指标名 | 类型 | 标签 | 定义位置 | 写入位置（埋点） | 实际写入状态 |
|---|---|---|---|---|---|
| `sekb_conversations_total` | Counter | user_id | metrics.py:31-35 | 无（仅定义） | **从未 inc（未埋点）** |
| `sekb_messages_total` | Counter | user_id, intent, status(success\|error) | metrics.py:38-42 | record_chat_metrics（metrics.py:258, user_id 硬编码 "default"）；record_chat_error（metrics.py:323, user_id 硬编码 "default"） | 现役 |
| `sekb_e2e_latency_seconds` | Histogram | 无；buckets=[0.5,1,2,5,10,15,30,60,120] | metrics.py:49-53 | record_chat_metrics metrics.py:255 | 现役（仅聊天两端点） |
| `sekb_llm_call_latency_seconds` | Histogram | model, role；buckets=[0.1..60] | metrics.py:56-61 | 仅 record_llm_call（metrics.py:360）——该函数无调用方 | **实际未写入** |
| `sekb_llm_calls_total` | Counter | model, role, status | metrics.py:68-72 | record_chat_metrics（role="aggregate", status="success"，metrics.py:271）；record_llm_call（metrics.py:359，未调用） | 现役（聚合口径） |
| `sekb_llm_tokens_total` | Counter | model, direction(input\|output) | metrics.py:75-79 | record_chat_metrics metrics.py:267-268 | 现役 |
| `sekb_llm_cost_usd_total` | Counter | model | metrics.py:82-86 | record_chat_metrics metrics.py:270 | 现役 |
| `sekb_llm_retries_total` | Counter | model | metrics.py:89-93 | record_chat_metrics（metrics.py:274-276，仅当 llm_retried） | 现役 |
| `sekb_llm_degradations_total` | Counter | role | metrics.py:96-100 | record_chat_metrics（metrics.py:277-280，role="aggregate"） | 现役 |
| `sekb_daily_cost_usd` | Gauge | 无 | metrics.py:103-106 | record_chat_metrics `.inc(total_cost)` metrics.py:307-308 | 现役（**只有 inc，无 reset 代码**；注释称“由外部任务定期重置”，metrics.py:102） |
| `sekb_reflection_pass_rate` | Gauge | 无 | metrics.py:113-116 | 无 | **从未 set（未埋点）** |
| `sekb_replan_count_total` | Counter | 无 | metrics.py:119-122 | record_chat_metrics metrics.py:291-293 | 现役 |
| `sekb_answer_groundedness` | Gauge | conversation_id | metrics.py:126-130 | record_chat_metrics：先 `.clear()` 再 `set` 最近一次（metrics.py:283-289） | 现役（仅保留最近一条序列） |
| `sekb_tool_call_total` | Counter | tool, status(success\|failed) | metrics.py:137-141 | record_chat_metrics metrics.py:296-300（tool 标签**硬编码 "web_search"**） | 现役（口径受限） |
| `sekb_tool_call_latency_seconds` | Histogram | tool；buckets=[0.01..30] | metrics.py:144-149 | 无 | **从未 observe（未埋点）** |
| `sekb_service_health` | Gauge | 无（1=ok,0=degraded） | metrics.py:156-159 | health.py:80（GET /api/v1/health 每次调用更新） | 现役 |
| `sekb_service_subsystem_health` | Gauge | subsystem(llm/tools/storage/graph) | metrics.py:162-166 | health.py:81-84 | 现役 |
| `sekb_knowledge_ingest_total` | Counter | status(stored\|skipped\|error\|disabled 等) | metrics.py:169-173 | 仅 record_chat_metrics metrics.py:303-304（用聊天返回 ingest_status） | 现役（仅聊天路径；上传/手工入库不计） |
| `sekb_client_events_total` | Counter | event, level | metrics.py:183-187 | record_client_event metrics.py:419-421（白名单外归 "other"，metrics.py:381-392,419） | 现役（前端上报） |
| `sekb_login_attempts_total` | Counter | result(success\|user_not_found\|password_mismatch\|network_error\|other) | metrics.py:191-195 | record_login_attempt：auth.py:115,133,146；network_error 由前端 metrics.py:441 补 | 现役 |
| `sekb_register_attempts_total` | Counter | result(success\|email_exists\|network_error\|other) | metrics.py:199-203 | record_register_attempt：auth.py:63,85；network_error 前端 metrics.py:445 | 现役 |
| `sekb_client_api_duration_seconds` | Histogram | method, status(success\|error)；buckets=[0.05..30] | metrics.py:207-212 | record_client_event 处理 `api_request` perf 事件（metrics.py:427-435） | 现役（前端观测） |
| `sekb_client_event_reports_total` | Counter | status(accepted\|rejected) | metrics.py:215-219 | monitoring.py:91-93（每批上报后 inc） | 现役 |

### 1.2 记录辅助函数（metrics.py）

- `get_metrics()` metrics.py:226-233 → `prometheus_client.generate_latest()`。
- `record_chat_metrics(result)` metrics.py:236-312：e2e 延迟→Histogram；messages_total(成功)；LLM tokens/cost/calls(role="aggregate")；retries/degradations；groundedness（先 clear 后 set）；replan；tool（web_search 反推成功/失败，metrics.py:296-300）；knowledge_ingest；daily_cost。
- `record_chat_error(intent="unknown")` metrics.py:315-325：messages_total status="error"。
- `record_llm_call(...)` metrics.py:328-372：单次粒度 LLM 指标（含 latency/role 直方图）——**全仓库无调用方**（metrics.py:341-344 docstring 自述 LLMFactory 未接入，避免侵入）。
- `record_client_event(event,level,fields)` metrics.py:398-447：白名单（metrics.py:381-392）+ level 白名单（metrics.py:395）；`api_request`→耗时直方图；login/register failed+network_error 补漏斗。
- `record_login_attempt` / `record_register_attempt` metrics.py:450-475。

### 1.3 record_chat_metrics / record_chat_error 调用点（全量）

| 调用 | 位置 | 上下文 |
|---|---|---|
| record_chat_metrics(result) | chat.py:674 | POST /api/v1/chat（非流式）成功路径 |
| record_chat_metrics(result) | chat.py:802 | POST /api/v1/chat/stream 成功（外层 try 记 warning，chat.py:803-804） |
| record_chat_error() | chat.py:656,660,667 | 非流式：HTTPException/SEKBError/Exception |
| record_chat_error() | chat.py:777,785,793 | 流式：run_task.result 抛 HTTPException/SEKBError/Exception |

- 其余 `record_*`：login/register 埋点在 auth.py:63,85,115,133,146；health 指标在 health.py:80-84；client event 在 monitoring.py:74-93。
- **仅聊天两条路由调用聊天指标**；CLI（backend/app/cli/chat.py）与 eval 路径不调用（CLI 无埋点，仅日志）。

## 2. 指标/健康暴露端点（backend/app/api/routes/）

- `GET /metrics`（metrics.py 路由 23-37）：无鉴权、无 /api/v1 前缀，Prometheus 默认约定；返回 text/plain version=0.0.4。
- `GET /api/v1/health/`（health.py:28-96）：综合检查 llm/tools/storage/graph → 更新 service_health 与 service_subsystem_health（health.py:79-86）；返回 ok/degraded。
- `GET /api/v1/health/live`（health.py:99-109）：仅进程存活，无依赖检查；docker healthcheck 用（docker-compose.prod.yml:90-95；backend/Dockerfile:124-125）。
- `GET /api/v1/health/ready`（health.py:112-135）：llm + graph，任一不可用 503。

## 3. 客户端事件上报链路（backend/app/api/routes/monitoring.py）

- `POST /api/v1/monitoring/client-event`（monitoring.py:50-100）：**不强制鉴权**（monitoring.py:8-10 设计说明：兼容未登录 login_submit_failed；生产靠 Nginx 限流/网络策略【推断·待验证：nginx.conf 是否实际限流】）。
- 入参 ClientEventBatch{events≤50}（monitoring.py:32-48,64-68）；字段校验失败跳过（monitoring.py:80-88）；恒返 202（monitoring.py:12,58）。
- 单事件转写 → record_client_event（monitoring.py:74-79）→ client_events_total 等（metrics.py:419-445）。
- 前端侧驱动：frontend/src/utils/logger.ts:82-83（队列 20/5s）、139-141、212-228、288-289 → 周期性 POST 到该端点。

## 4. 日志（backend/app/core/logging.py + config）

- structlog 处理链（logging.py:57-65）：merge_contextvars → add_logger_name → add_log_level → TimeStamper(iso) → 脱敏处理器 → StackInfoRenderer → format_exc_info；renderer 依 `logging.format`（默认 json，logging.py:67-70；config.yaml logging.format=json）。
- 脱敏：`redact_fields` 默认 [api_key, authorization, token]（config.py:322；config.yaml redact_fields 同）；字段名小写匹配即值替换为 `"***REDACTED***"`（logging.py:28-37,54）。
- 输出：stdout + `data/logs/app.log` RotatingFileHandler（50MB×backup_count 7，logging.py:92-103；config.py:315-323 默认）。
- 级别：config `logging.level`（默认 INFO）。
- 上下文注入点（bind_context/clear_context，structlog.contextvars）：
  - chat.py:474 `bind_context(conversation_id=..., trace_id=..., user_id=...)`；chat.py:532 finally `clear_context()`（chat 两端点共用 _run_chat）。
  - cli/chat.py:178-182 绑定、221 finally clear（CLI 交互路径）。
  - logging.py:119-133 为定义处。
  - **无请求级中间件对非 chat 路由注入 context**；其余路由的日志字段靠各调用点显式 kwargs。
- 注入字段集：`trace_id` / `conversation_id` / `user_id`（chat 与 CLI chat 两处）。
- 日志事件：全仓库大量结构化 INFO/WARNING/ERROR（例：server.py:141-147 启动完成、chat.py:446 建会话、errors 路径 server.py:237-242 脱敏 error_id）。
- 无自定义日志 query/持久化查询接口；日志经 promtail→Loki（§6）。

## 5. trace 现状（LangSmith / OpenTelemetry）

- **应用代码零 OpenTelemetry 使用**：backend/app 内无 `opentelemetry`/`otel` import（grep 全库仅 config.py:232/235 与 tracing.py 的配置串 "langsmith"）。venv 中 opentelemetry 包存在但属传递依赖【推断·待验证】。
- **无显式 langsmith import/span**：grep 结果只在 config.py:217-221（LangSmithConfig）与 tracing.py:110-117（设环境变量）。
- 生效机制：`setup_tracing`（tracing.py:91-139）——provider=langsmith 且 api_key 非空时写环境变量 LANGSMITH_API_KEY/LANGSMITH_PROJECT/LANGSMITH_ENDPOINT/LANGCHAIN_TRACING_V2=true（tracing.py:110-117），**依赖 LangChain/LangGraph SDK 自动上报**（langsmith 0.11.0 已在 venv，requirements.txt 未直接声明，为传递依赖【推断·待验证】）。
- 降级路径：api_key 缺失/异常 → LocalTraceCollector 写 `data/traces/{date}.jsonl`（tracing.py:132-136,27-88）；`fallback_to_local_on_failure=true`（config.py:233）。**但 LocalTraceCollector 的 start_trace/add_event/end_trace 全仓库无调用方**，且 bootstrap.py:143 调用 setup_tracing 时丢弃返回值（bootstrap.py:141-145）→ 本地 JSON trace 从未被写入【推断：需验证 data/traces 是否为空】。
- 配置现状：config.yaml:187-192 tracing.provider=langsmith、api_key=${LANGSMITH_API_KEY}；.env.prod 含 LANGSMITH_API_KEY（值已脱敏）→ 生产走 LangSmith 环境变量路径。
- trace_id 业务化：chat.py:473 生成 uuid → 存入 graph state（create_initial_state，chat.py:475-481）、写入会话消息 user/assistant 记录的 trace_id 字段（chat.py:555-576）、返回响应 meta；CLI 同（cli/chat.py:177）。Promtail json 提取 trace_id（deploy/promtail-config.yml:53）。
- 结论：**trace 现状 = LangSmith 经 env 隐式接入（SDK 级），应用层无显式 span/OTel；本地 JSON 兜底为“已定义未接线”**。

## 6. 监控栈与告警（docker-compose.monitoring.yml + deploy/）

### 6.1 服务与采集目标

- 栈服务：prometheus、grafana、loki、promtail、alertmanager、feishu-webhook（docker-compose.monitoring.yml:24-202）；external 网络 sekb_network 接入应用栈（monitoring.yml:204-210）。
- Prometheus 抓取目标（deploy/prometheus.yml:27-52）：
  - job `backend`：`backend:8000/metrics`，scrape 15s，timeout 10s，标签 service=backend / app=self-evolving-kb；
  - job `backend-health`：`backend:8000/api/v1/health`，scrape 30s，timeout 5s；
  - job `prometheus`：localhost:9090。
- 存储：tsdb 保留 30d/10GB（monitoring.yml:33-34）；evaluation_interval 15s（prometheus.yml:10）。
- Loki：单节点，文件存储；**retention 336h=14 天**（deploy/loki-config.yml limits_config.retention_period: 336h）；ruler 配置无本地规则（alertmanager_url 空）。
- Promtail：docker_sd 采集 label `sekb-logs=backend/frontend` 容器日志（docker-compose.prod.yml:50-51,114-115 打标；deploy/promtail-config.yml:25-61 backend job / 63-82 frontend job）；backend 管道 json 提取 level/event/trace_id/user_id/error 并转标签（promtail-config.yml:49-61）；frontend 管道 regex 解析 nginx 日志。
- Grafana：数据源 Prometheus + Loki（datasources.yml）；自动 provisioning 看板 sekb-overview.json（dashboards.yml:10-21）；看板默认刷新 15s（docker-compose.monitoring.yml:71）。

### 6.2 Grafana 看板面板（deploy/grafana/provisioning/dashboards/sekb-overview.json，12 面板 + 1 行标题）

消息处理速率 QPS（sum(rate(sekb_messages_total[5m]))）、错误率（error/total 比值，阈值绿/黄 0.02/红 0.05）、日累计成本（sekb_daily_cost_usd，阈值 3/5）、服务健康状态（sekb_service_health）、端到端延迟分布、消息按意图分布、LLM 调用次数（按模型）、LLM Token 用量（按方向）、LLM 成本累计、重试与降级、知识入库状态分布、子系统健康状态。

### 6.3 Alertmanager（deploy/alertmanager.yml）

- 路由：group_by [alertname,severity,service]；group_wait 30s / group_interval 5m / repeat_interval 4h（alertmanager.yml:29-33）。
- critical → receiver feishu-critical（group_wait 10s / repeat 1h）；warning → feishu-warning（group_wait 1m / repeat 4h）（alertmanager.yml:36-46）。
- 抑制：critical 抑制同 alertname+service 的 warning（alertmanager.yml:49-54）。
- 接收器：default/feishu-critical/feishu-warning 均 webhook → `http://feishu-webhook:5001/webhook` + email admin@sekb.local（alertmanager.yml:58-80）；SMTP 默认 localhost:25 占位（alertmanager.yml:17-21）。
- 通知目标默认内网地址已在 compose 中（PROMETHEUS_URL/GRAFANA_URL 默认值含内网 IP，docker-compose.monitoring.yml:183-185 → 脱敏记 `<REDACTED>`）；飞书密钥经 FEISHU_WEBHOOK_URL/FEISHU_SECRET 注入（.env.prod 有值，脱敏）。

### 6.4 告警规则清单（deploy/alerts.yml，共 **12 条**，分组 6）

| 组 | 告警名 | 表达式 | for | 严重度 |
|---|---|---|---|---|
| service_availability | BackendDown | `up{job="backend"} == 0` | 1m | critical |
| service_availability | BackendDegraded | `sekb_service_health == 0` | 2m | warning |
| service_availability | SubsystemUnhealthy | `sekb_service_subsystem_health == 0` | 3m | warning |
| performance | HighLatencyP95 | `histogram_quantile(0.95, rate(sekb_e2e_latency_seconds_bucket[5m])) > 15` | 5m | warning |
| performance | VeryHighLatencyP99 | `histogram_quantile(0.99, rate(sekb_e2e_latency_seconds_bucket[5m])) > 60` | 5m | critical |
| cost | BudgetExceeded | `sekb_daily_cost_usd > 5` | 10m | warning |
| cost | HighPerRequestCost | `rate(sekb_llm_cost_usd_total[5m]) / rate(sekb_messages_total[5m]) > 0.05` | 10m | warning |
| quality | ReflectionFailureHigh | `rate(sekb_replan_count_total[5m]) > 0.5` | 10m | warning |
| quality | LowGroundedness | `sekb_answer_groundedness < 0.6`（透传 conversation_id 标签） | 15m | warning |
| quality | LLMDegradationHigh | `rate(sekb_llm_degradations_total[10m]) > 0.1` | 15m | warning |
| tools | ToolFailureHigh | `sum(rate(sekb_tool_call_total{status="failed"}[5m])) by (tool) / sum(rate(sekb_tool_call_total[5m])) by (tool) > 0.1` | 5m | critical |
| business | HighErrorRate | `sum(rate(sekb_messages_total{status="error"}[5m])) / sum(rate(sekb_messages_total[5m])) > 0.05` | 5m | critical |

- 规则评估：Prometheus 每 15s（prometheus.yml:10）。告警基于的指标口径缺陷见 §8 drift。

## 7. 其他可观测辅助

- 未处理异常兜底：server.py:226-250 生成 `error_id`（uuid 前 12 位）仅回日志与响应，不外泄原始异常。
- SEKBError 全局 handler 记 error_type/message/status_code/details（server.py:205-223）。
- canary_monitor.sh（部署运维工具）：读 Prometheus 查询错误率阈值 0.05、P95 延迟阈值 30s，每 30s 轮询并自动回滚；地址优先取 `.env.prod` 的 `MONITOR_BIND_IP`（生产绑 Tailscale，`localhost` 不可用），未设置才回退 `http://localhost:9091`（deploy/canary_monitor.sh:13-18,24-30,121,189）——非告警规则，属发布期人工巡检工具。
- 日志脱敏仅按字段名（api_key/authorization/token），structlog 事件里未覆盖 request header 全量【推断·待验证】。

## 8. 漂移/异常清单（drift）

- D-T8-1：4 个指标定义后从未写入：`sekb_conversations_total`、`sekb_reflection_pass_rate`、`sekb_tool_call_latency_seconds`；`sekb_llm_call_latency_seconds` 只在无调用方的 record_llm_call 中被 observe（metrics.py:31,113,144,56/360）。
- D-T8-2：`record_llm_call` 整体无调用方（metrics.py:328-372）——LLM 单次延迟/角色维度指标未接入；LLM 成本/token 由 LLMFactory 内存统计快照（llm_factory.py:427+ snapshot_stats，scribe 算 delta）经 record_chat_metrics 反推（metrics.py:261-280）。
- D-T8-3：`messages_total` 的 user_id 标签硬编码 "default"（metrics.py:258,323），与真实 JWT 用户体系不符 → 用户维度标签失真（metrics.py:13 注释只说明 user_id 仅用于计数避免高基数，未实现按真实用户区分）。conversations_total 未接 → 会话数不可从指标观测。
- D-T8-4：`tool_call_total` 的 tool 标签固定 "web_search"（metrics.py:296-300），仅用单次工具成功率反推，不反映真实工具分布/延迟。
- D-T8-5：`daily_cost_usd` 只增不减（metrics.py:307-308），无每日重置代码（metrics.py:102 注释寄望“外部任务”，仓库内未见）→ 告警 BudgetExceeded（>5）长期会被当日累计历史抬高（需靠 30d tsdb 保留手工重置）。
- D-T8-6：`answer_groundedness` 每次 `.clear()` 后只留最近一次会话单序列（metrics.py:288-289）——持续低于 0.6 才会触发 LowGroundedness，中间态无历史分布。
- D-T8-7：知识入库指标只覆盖聊天触发路径（metrics.py:303-304）；上传/批量导入不计入。
- D-T8-8：knowledge_ingest_total 与 health 指标更新点分散在路由层/record_chat_metrics；无统一指标注册模块外显（除 metrics.py）。
- D-T8-9：本地 JSON trace 兜底“已定义未接线”：LocalTraceCollector 方法无调用方（tracing.py:27-88），bootstrap 丢弃 setup_tracing 返回值（bootstrap.py:141-145）。
- D-T8-10：client-event 上报端点无鉴权（monitoring.py:8-10 自述设计如此）——依赖网关限流【推断·待验证】。
- D-T8-11：监控栈无“告警规则自监控”（Prometheus self 仅抓自身；Loki healthcheck disable，monitoring.yml:112 注释依赖抓取失败告警间接保障）。
