---
title: T7 异步与调度清单（事实表）
layer: 事实层（SSOT 工作产物）
owner: docs-eng
status: draft
version: v0.1.0
---

# T7 异步与调度清单

> 证据纪律：每条记录带 `文件:行号`；推断标【推断·待验证】；密钥/内网 IP 一律脱敏；只描述现状（as-is）。
> 范围：backend/app 全部异步/后台任务与定时调度 + docker-compose.prod.yml 服务布局 + deploy/ 下 crontab/轮询引用 + docker-compose.monitoring.yml 周期行为。
> 采集时间：2026-09（依当前仓库快照）。

## 1. 总览表

| # | 异步/调度项 | 触发方式 | 承载位置（代码） | 并发度 | 幂等性 | 失败补偿 | 可观测手段 | 状态 |
|---|---|---|---|---|---|---|---|---|
| A1 | 科技资讯日报定时刷新 | APScheduler cron（进程内） | backend/app/scheduler/scheduler.py:34-40；启动于 backend/app/api/server.py:134-139 | 单 worker 进程内单实例 | 文件锁 + 今日已存在跳过 | 锁超时跳过；无重试任务；报错仅日志 | 启动/跳过/完成均 INFO 日志 | 现役（news.enabled=true） |
| A2 | 科技资讯周报定时生成 | APScheduler cron（进程内） | scheduler.py:41-47 | 同上 | 无 skip 守卫【见 3.A2 注】 | 无；失败仅日志 | 完成日志（service.py:148-152） | 现役 |
| A3 | 科技资讯月报定时生成 | APScheduler cron（进程内） | scheduler.py:48-54 | 同上 | 同上 | 无 | 同上 | 现役 |
| A4 | 聊天偏好抽取后台任务 | API 触发的 `asyncio.create_task`（每次聊天 spawn） | backend/app/api/routes/chat.py:393-404 → 355-390；触发点 chat.py:546-547 | 无并发上限（set 防 GC，同事件循环） | 无（可能重复写画像，upsert 深合并覆盖） | 全函数 try/except + warning 日志，无重试 | 日志（chat.py:385-390,403-404） | 现役（skill=应聘助手时） |
| A5 | SSE 流式聊天图运行子任务 | API 触发的 `asyncio.create_task` | chat.py:750-775（run_task）；token 队列 out_q 745-769 | 每流 1 任务；同 (user,conv) 互斥锁 `_conv_inflight` | 会话级互斥防并发回复 | 异常转 SSE error 事件 + record_chat_error | 日志 + Prometheus | 现役 |
| A6 | 上传文件后台入库 | Starlette `BackgroundTasks`（?async_process=true） | backend/app/api/routes/upload.py:494-578 → `_background_ingest` 425-459 | 每请求 1 任务，无队列上限 | overwrite=true 先删同名再入库；MD5 防同内容重复 | 异常仅日志；临时文件 finally 清理 | 日志 + /api/v1/upload/status（仅全量条目数） | 现役（可选参数） |
| A7 | 日报生成内容并发抓取 | 单次任务内 `asyncio.Semaphore(10)` | backend/app/agents/news/service.py:25,214-231；RSS gather service 外部源 rss_fetcher.py:57-67 | 10 并发正文抓取；RSS 源全并发 | 单源失败跳过 | 单源失败 return_exceptions + 日志跳过 | 日志（rss_fetcher.py:65-69） | 现役 |
| A8 | 大类生成 LLM 重试 | 任务内循环（非调度器） | backend/app/agents/news/generator.py:255(_MAX_ATTEMPTS=3),267-303 | 串行 | — | 退避 `asyncio.sleep(2*attempt)`，3 次后放弃 | warning/error 日志 | 现役 |
| A9 | 新闻跨 worker 互斥锁 | 任务内自旋轮询（O_EXCL 文件锁，最多 600s） | news/service.py:78-97 | — | 幂等锁 | 陈旧锁 >600s 自动删 | 超时 warning 日志 | 现役（单 worker 下为冗余保险） |
| A10 | 日报保留清理 | 随 `save_daily` 触发（非独立 cron） | news/storage.py:29-39,131-144 | — | — | — | 删除时 INFO 日志 | 现役（仅清 daily_*.md，见 drift D-T7-5） |
| A11 | 知识自迭代入库 | 聊天请求内 inline `await`（非后台） | chat.py:586-611 → knowledge_ingestor.py:126+ | 与请求串行 | 重要性阈值/冲突检测去重 | 异常 catch → ingest_status=error 仅日志 | INGEST-TRACE 日志（knowledge_ingestor.py:165-172）+ sekb_knowledge_ingest_total | 现役（L3 启用时） |
| A12 | 手动触发日报/周报/月报 | HTTP API | POST /api/v1/news/refresh news.py:34-47；POST /api/v1/news/{weekly\|monthly} news.py:75-94 | — | 日报 force=false 走跳过；force=true 重跑 | 异常 → 500 | 错误日志 | 现役 |
| A13 | 新闻调度器手动触发接口 | `NewsScheduler.trigger_now()` | scheduler.py:69-76 | — | — | — | — | 死代码（无调用方） |
| A14 | 宿主机每日备份 | 推荐 crontab（仓库无 crontab 实体文件） | deploy/backup.sh:11-12（`0 3 * * *`）；scripts/backup_kb.sh:17-22（`30 3 * * *`） | — | 打包快照 | backup_kb.sh trap 兜底重启 backend | 写日志文件（脚本注释） | 外部部署项【推断·待验证：是否已装 crontab】 |
| A15 | 灰度发布监控轮询 | 手动 nohup `while true`（非 cron） | deploy/canary_monitor.sh:9-10,30,121,189（30s 间隔） | 1 进程 | 读 Prometheus 判定 | 超阈值自动回滚 | 脚本 stdout 日志 | 手动工具 |
| A16 | 前端日志批量上报 | 前端定时器 flush（5s / 队列满 20 条 / 页面隐藏） | frontend/src/utils/logger.ts:82-83,139-141,212-228 → POST /api/v1/monitoring/client-event（backend monitoring.py:50-100） | — | — | 后端恒返 202，前端不重试 | client_event_reports_total | 现役 |
| A17 | Prometheus 周期抓取/告警评估 | scrape/evaluation interval 15s | deploy/prometheus.yml:8-13；容器级 compose | — | — | — | 见 T8 | 现役 |
| A18 | 聊天/CLI 交互循环（非服务调度） | 终端 REPL `while True` | cli/chat.py:336-359 | — | — | — | — | 仅本地 CLI |
| A19 | 知识分页统计 / 系列重建（非调度） | HTTP 请求内 `while True` 分页 | knowledge.py:84-92；upload.py:889-898 | — | — | — | — | 仅请求内 |

## 2. 应用内调度器实现（A1–A3, A13）

- 唯一调度器：`backend/app/scheduler/scheduler.py` 的 `NewsScheduler`，基于 `APScheduler.AsyncIOScheduler` + `CronTrigger`（scheduler.py:12-13,26）。包入口 backend/app/scheduler/__init__.py。
- 启动注册：FastAPI lifespan startup 中实例化并 `start()`（server.py:134-139）；shutdown 中 `shutdown(wait=False)`（server.py:153-156）。`AppContext.news_scheduler` 字段 bootstrap.py:91-92。
- 注册的 3 个 cron job（scheduler.py:34-54）：
  - `news_daily`：日报，`agent.refresh`，cron=`config.news.daily_cron`
  - `news_weekly`：周报，`agent.generate_periodic("weekly")`，cron=`config.news.weekly_cron`
  - `news_monthly`：月报，`agent.generate_periodic("monthly")`，cron=`config.news.monthly_cron`
  - 均 `replace_existing=True`；时区 `config.news.timezone`。
- cron 默认/实际值：config.py:344-347 默认 `daily_cron="0 8 * * *"` / `weekly_cron="0 8 * * 1"` / `monthly_cron="0 8 1 * *"` / `timezone="Asia/Shanghai"`；backend/config.yaml:301-304 相同；news.enabled=true（config.yaml:295；默认 False 见 config.py:333）。
- 进程内调度前提：生产 uvicorn `--workers 1`（backend/Dockerfile:136-139 注释明示避免 scheduler 重复执行；compose 端口回环绑定 docker-compose.prod.yml:54）。APScheduler 无持久化 job store → 进程重启后按 cron 从零注册（scheduler.py:28-55）【推断：无 misfire_grace 配置，靠 enabled 门控】。
- **docker-compose.prod.yml 无独立 scheduler 服务**：services 仅 backend/frontend/rsshub/browser/postgres(with-db profile)/redis(with-db profile)（docker-compose.prod.yml:35-242）。即定时任务与 API 同进程承载。
- A13 死代码：`trigger_now()`（scheduler.py:69-76）全仓库无调用方（grep 无结果），手动触发实际走 HTTP（A12）。

## 3. 各异步/调度项细节

### A1/A2/A3 新闻调度
- 日报幂等链：`refresh(force=False)`：今日 `daily_{date}.md` 已存在 → 跳过（news/service.py:59-62）；否则 O_EXCL 锁 `_dir/.lock_daily`（service.py:78-97，600s 超时），拿锁后二次检查（service.py:70-73）；锁在 finally 释放（service.py:75-76）。
- 周报/月报 `generate_periodic` 无 skip、无锁，直接 `_generate_report`（service.py:178-187,108-160）→ 每次执行必重跑（单 worker 下无重复风险）。
- 可观测：仅结构化日志——启动日志（scheduler.py:56-61）、跳过/锁超时 warning（service.py:61,67,72）、完成日志含 fetched/filtered/path（service.py:148-152）。**无 Prometheus 指标、无调度成功/失败计数、无执行历史落盘**。
- 下游内容链路（A7/A8/A9）：RSS 全源 gather+return_exceptions（rss_fetcher.py:57-67，单源超时 15s rss_fetcher.py:47）；正文抓取 Semaphore(10)（service.py:25,216-231）；大类生成 LLM 最多 3 次重试、指数退避 sleep(2*attempt)（generator.py:255,267-303）；web 采集与 job 侧大量 `asyncio.gather`（web_fetcher.py:37 等）属任务内并发而非调度。

### A4 偏好抽取后台任务
- 触发：仅当 `skill == "应聘助手"`（chat.py:546-547）。`_schedule_preference_extraction` 用 `asyncio.create_task` 启动 `_record_preferences_task`，task 句柄入模块级 set `_background_extract_tasks` 防 GC、done 回调 discard（chat.py:91,398-404）。
- 行为：读短期记忆最近 10 条 → 1 次 LLM 调用 `ainvoke_with_stats("chat_simple", ...)`（chat.py:360-381）→ JSON 提取 → `ProfileStorage("data/profile").upsert_update` 写画像（chat.py:329-352）。空 JSON 跳过（chat.py:384-386）。
- 失败/补偿：整函数 try/except（chat.py:389-390）+ 调度本身 try/except（chat.py:403-404），无重试、无补偿队列、无执行状态存储。
- 并发：无信号量/无上限；同一事件循环。**进程关闭时不等待该 set 中未完成任务**（lifespan shutdown 未 drain，见 server.py:149-158）。

### A5 SSE 流式
- `_conv_inflight: set[str]`（模块级，键 `user_id|conversation_id`，chat.py:54,711-719）：同会话并发流直接回 error 事件；finally discard（chat.py:835）。
- 图运行放子任务 `run_task = asyncio.create_task(_run_chat(...))`（chat.py:751-753），主生成器 `await asyncio.wait_for(out_q.get(), 1.0)` 消费 thinking/token 事件（chat.py:755-762），结束后排空队列（chat.py:765-769），`run_task.result()` 取结果或转异常为 SSE error（chat.py:774-798）。token sink contextvar 在 finally reset（chat.py:742,770-771）。
- 异常路径：HTTPException/SEKBError/Exception 均 `record_chat_error()` 后输出 error 事件（chat.py:776-798）。

### A6 上传后台入库
- `POST /api/v1/upload`（upload.py:494）：`async_process=true` 时立即返回占位 success（upload.py:561-578），`background_tasks.add_task(_background_ingest, ...)`（upload.py:563-570）在响应后执行 `_process_and_ingest`（upload.py:287-403：覆盖删除→图片/原文件保存→FileProcessor→文档分类→系列识别→分块入库）。
- 失败：`_background_ingest` 全量 try/except 记 error 日志（upload.py:445-451）；临时文件 finally 清理（upload.py:452-459）。**无任务状态/进度存储**，前端靠 GET /api/v1/upload/status（upload.py:615-647）轮询总条目数推断完成。
- 幂等：overwrite=true 先删同名旧条目（upload.py:303-307,406-422）；成功后 `_record_md5` 记文件名→MD5（upload.py:590-591）供后续同名同内容跳过【推断：跳过逻辑在 `_record_md5` 相关分支，见 upload.py:581-592】。

### A11 知识自迭代入库（现状为请求内同步，非后台）
- 触发：每次聊天 `_run_chat` 内联 await（chat.py:586-611），条件 `knowledge_ingester is not None and knowledge_base is not None`（L3 启用时装配，bootstrap.py:192-203）。
- 代码注释称「不阻塞主回复」，但实现为**响应返回前的同步 await**（chat.py:591-599）→ 非流式下增加首包延迟；流式下 token 已推送但 done 事件延迟【推断·待验证】。**注释与实现不一致（drift D-T7-4）**。
- ingest_conversation 内部：唯一追踪 id、importance 阈值（config.py:111 `importance_threshold=0.3`）、LLM 事实提取、冲突检测→新增/合并/并存（knowledge_ingestor.py:126-199 起）；失败仅返回 IngestResult + 日志，不抛（chat.py:608-611）。

### A12 手动触发 API（news 路由）
- `POST /api/v1/news/refresh`：body `force`（默认 true=重新生成；false=今日已存在则跳过），鉴权 `require_full_access`（news.py:34-47）→ 全功能白名单用户才可重新生成日报（docker-compose.prod.yml:70-72 注释说明预览账号只读）。
- `POST /api/v1/news/{weekly|monthly}`：`require_full_access`，可传 period/supplement（news.py:75-94；supplement 已不使用，service.py:184 注释）。
- 读侧 `GET /reports /report /{type}` 仅登录（get_current_user）（news.py:50-112）。

### A9/A10 锁与保留清理
- A9 锁文件放日报目录 `.lock_daily`，内容为 pid（service.py:80-87）；陈旧锁按 mtime>600s 视为失效并删除（service.py:89-93）。进程崩溃残留由下次任务清理。
- A10 `_cleanup`（storage.py:131-144）：仅删除 `daily_*.md` 早于 retention_days(70, config.yaml:300) 的旧日报；**不清理 `daily_*.json`、不清理周报/月报 md**（drift D-T7-5）；清理只挂在 `save_daily` 内（storage.py:29-39），周报/月报路径 `save_periodic` 不触发清理（storage.py:71-78）。

### A14/A15 宿主层 cron/轮询（deploy/ 与 scripts/，非容器内）
- deploy/backup.sh 头注释给出建议 cron `0 3 * * *`（backup.sh:11-12）；deploy/pre-deploy-check.sh:427-431 检查宿主机 crontab 是否含 backup.sh（`sudo crontab -l | grep backup.sh`），未配置仅 warn。
- scripts/backup_kb.sh 建议 `30 3 * * *`（scripts/backup_kb.sh:17-22），脚本内含 `trap ... EXIT` 保证退出前重启 backend。
- **仓库内无实际 crontab 文件**；以上均为“建议/校验引用”【推断·待验证：生产宿主机实际 crontab 不在仓库内】。
- deploy/canary_monitor.sh：`while true` + `sleep CHECK_INTERVAL`（默认 30s，canary_monitor.sh:30,121,189），依赖 Prometheus（地址取 `.env.prod` 的 `MONITOR_BIND_IP`，未设置回退 localhost，canary_monitor.sh:28-30）查询错误率/延迟，超阈值调 rollback；注释明确“建议 nohup 后台运行”（canary_monitor.sh:9-10）→ 非定时任务，手动工具。
- deploy/deploy.sh / restart.sh 内的 `sleep` 均为部署流程等待，非周期任务。

### A16 前端周期性上报（backend 侧接收）
- 接收端：`POST /api/v1/monitoring/client-event`（无鉴权，monitoring.py:6-13,50-100）：单批上限 50（monitoring.py:32,64-68），单事件异常跳过（monitoring.py:80-88），恒返 202 防重试风暴（monitoring.py:12,58）。
- 发送端驱动：frontend logger.ts 队列满 20 立即 flush（logger.ts:82,139-141）、定时 5s flush（logger.ts:83,212-228）、页面隐藏 flush、手动 flush。
- 后端无独立定时器；纯 HTTP 被动接收。

### A17 监控栈周期行为（docker-compose.monitoring.yml）
- Prometheus scrape_interval 15s / evaluation_interval 15s（deploy/prometheus.yml:8-10）；tsdb 保留 30d/10GB（monitoring compose command，docker-compose.monitoring.yml:33-34）。
- Grafana provisioning 看板刷新 updateIntervalSeconds 30（deploy/grafana/provisioning/dashboards/dashboards.yml:16）；GF_DASHBOARDS_DEFAULT_REFRESH 15s（docker-compose.monitoring.yml:71）。
- Promtail docker_sd refresh_interval 5s（deploy/promtail-config.yml:28,67）。
- 无“容器内 crontab/定时器”，告警触发是 Prometheus 周期评估（详见 T8）。

## 4. 生命周期钩子（bootstrap）
- startup：`initialize_app`（bootstrap.py:97-267）顺序——配置→JWT 强度校验→日志→tracing（失败仅 warning，bootstrap.py:142-145）→LLM 工厂→JSON 存储→L1 记忆→工具注册表 initialize（启 MCP 子进程，bootstrap.py:160-161）→反思策略→条件装配 L3/ingester（bootstrap.py:170-203）→用户/分享存储（bootstrap.py:206-215）→Graph 构建（bootstrap.py:217-228）→news_agent（enabled 时，bootstrap.py:231-236）→job_agent（enabled 时，bootstrap.py:239-244）。调度器启动在 initialize 之后（server.py:134-139）。
- shutdown：`shutdown_app`（bootstrap.py:277-301）仅关工具注册表（MCP 断开/子进程终止，bootstrap.py:291-294）；调度器在 server.py lifespan finally 中先 `shutdown(wait=False)`（server.py:153-156）。**不 drain 偏好抽取后台任务（A4）、不等待 A6 BackgroundTasks**（FastAPI 会等当前请求的 BackgroundTasks 完成；lifespan 不感知跨请求任务）【推断】。
- 无 SIGTERM 自定义 handler；优雅停机依赖 uvicorn + stop_grace_period 30s（docker-compose.prod.yml:99-100）。

## 5. 漂移/异常清单（drift）
- D-T7-1：`NewsScheduler.trigger_now()` 无调用方（scheduler.py:69-76）——预留手动触发接口未接线。
- D-T7-2：周报/月报（A2/A3）无“已生成则跳过”幂等（service.py:178-187）；若将来多 worker 会重复执行。
- D-T7-3：所有调度/后台项无可观测指标（scheduler、upload bg、偏好抽取、news 均无 Prometheus 计数）；唯一事件驱动指标是聊天侧 record_chat_metrics。
- D-T7-4：chat.py:586 注释「不阻塞主回复」与实现（inline await，chat.py:591-599）不一致——知识入库实际在响应路径内同步等待。
- D-T7-5：日报保留清理仅删 `daily_*.md`（storage.py:136-144），`daily_*.json` 与周期报告无清理逻辑。
- D-T7-6：A4 任务集无上限、无 shutdown 等待；高并发聊天（应聘助手）时可能积压后台 LLM 任务【推断·待验证】。
- D-T7-7：生产单 worker（Dockerfile:136-139）保证调度单实例；dev 模式 `--reload` 单进程亦单实例，但多容器扩容时会绕过此前提（NewsAgent 文件锁仅覆盖日报，见 D-T7-2）。
