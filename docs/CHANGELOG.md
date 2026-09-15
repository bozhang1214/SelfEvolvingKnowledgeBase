# SEKB 变更日志（CHANGELOG）

> 记录所有功能迭代与问题修复。按时间倒序，最新在前。
> 维护约定：**每次功能开发或问题修复完成后，必须同步在本文件追加一条记录**，并更新文档头部「最后更新」日期。
> 最后更新：2026-09-15

---

## 2026-09-15（科技资讯周报/月报列表错位：按类型分仓状态）

- **背景**：owner 报——日报→周报→月报顺序切换，列表都正常；但**切回周报**时展示的是
  月报列表；周报里点「刷新」后再切月报又展示周报列表。

- **根因**：`News.tsx` 里周报/月报**共用一个** `pReports/pCurrent/pPeriod`，而
  `onTabChange` 用 `loadedTabs` 去重「切回已加载的 tab 不发请求」。于是切回周报时，
  `pReports` 里还留着上一次月报的数据（去重短路了重新渲染），界面就张冠李戴；反向同理。

- **改动**：`frontend/src/pages/News.tsx` 把周报/月报的列表、当前期、详情改成
  **按类型分仓**的 `Record<PeriodicType, ...>`；`loadPeriodic/loadPeriodicDetail` 只写
  自己那一仓；渲染时按当前 `tab` 取对应仓。去重（`loadedTabs`）保留——切回已加载的 tab
  依然不发请求，但展示的是**该类型自己的**数据。

- **验证**：新增 `frontend/tests/news.test.tsx` 2 条回归测试（切回周报显示周报列表、
  反复横跳各自列表请求只发一次），旧实现下会失败；前端全量 **68 passed**、`tsc --noEmit`
  无错误。

## 2026-09-15（打通 AI 对话与职位分析：聊天新增 search_jobs 工具）

- **背景**：此前 AI 对话只能 `web_search` / `rag_retrieve` / `llm_generate`，职位分析
  是独立 Agent + API + 前端页，两者断开。owner 提出「能不能在对话里直接让职位分析
  搜索并分析职位」。

- **改动**：
  - `backend/app/agents/executor.py`：`_dispatch_tool` 新增 `search_jobs` 分支（并把
    `user_id` 传入分派）；`_search_jobs` 复用 `market.analyze_market`（与「招聘分析」页
    **同一套**「采集 → 内核批量分析 → 报告」流水线，共享缓存/画像/成本统计口径），
    按对话里的 keyword/city/min_salary_k 触发，缺省回落到 `config.job` 默认值；
    `_format_market_report` 把可能很大的报告压成紧凑摘要（截断正文、只给代表职位）。
  - `backend/app/agents/prompts/templates.py`：Planner 提示词的工具清单加 `search_jobs`
    及 tool_input 示例，并提示「找职位/岗位/薪资行情」类请求优先用该工具。
  - 未知工具仍兜底 `llm_generate`，新增工具不影响既有降级。

- **验证**：新增 `test_executor_job_search.py` 8 条（调度路由、调用 analyze_market 参数
  正确、缺省回落到配置、失败抛 ToolError、未知工具仍兜底、报告摘要含关键事实/截断超长
  正文/空报告不崩）；全量 **840 passed**、ruff 全过、mypy 306 ≤ 310；Planner 模板
  渲染校验通过。

- **备注（待 owner 确认）**：`min_salary_k` 在 `analyze_market` 里只进了缓存键，
  采集时仍用 `cfg.default_min_salary_k`（历史行为，招聘分析页亦如此）——是否要真按
  对话里给的薪资过滤，见收尾疑问清单。

## 2026-09-15（启动补跑：错过的日报/周报/月报不再永久丢失）

- **背景**：9/15 的日报在 08:00 触发时因 SSL 错误中断、上周周报也没按时出来，
  只能靠人发现 + 手动点一次「重新生成」。排查后确认一个**机制性**缺口：APScheduler
  的 `misfire_grace_time` 只能兜住「错过了但还在宽限内」的运行；容器恰好在计划时刻
  **正在部署/重启**（本仓库早上 6~10 点常有部署，见 nginx 备份文件名）时，这次运行
  被跳过且**永远不会再补**。失败重试 + 状态落盘 + 飞书告警都只对「运行了但失败」有效，
  对「根本没运行」无效 —— 于是「今天日报缺失」依旧要靠肉眼。

- **改动**：
  - `backend/app/scheduler/scheduler.py`：新增 `catch_up_missing()` —— 应用启动后延迟
    60s（等 embedding/MCP 预热完）后台补跑**缺失**的报告：日报（过了当天计划时间且今天
    还没有）、周报/月报（期望的上一周期文件缺失）。幂等：只补「该有但现在没有」的那份，
    跑完写状态、失败照常推告警。带星期/日期限制的复杂 cron 不猜、不补。
  - `backend/app/agents/news/service.py`：`_period_label` 补上 `daily` 分支，并新增公开
    `expected_period(kind)` 让调度器不必去猜命名规则。

- **验证**：新增 10 条单测（`_daily_time_passed` 边界/复杂 cron 跳过、补跑只补缺失、
  已存在不重跑、未到日报时间只补周期、disabled 不补、补跑失败记状态不炸启动、expected_period 标签）；
  全量 832 passed、ruff 全过、mypy 306 ≤ 310。

## 2026-09-15（限流可被一个请求头绕过：nginx 透传客户端伪造的 X-Forwarded-For）

- **背景**：验证限流修复时顺手测了「换个假 IP 还会不会被限」，结果**线上实测可绕过**：
  连打 10 次 `POST /api/v1/news/refresh`，每次带一个不同的
  `X-Forwarded-For: 9.9.9.N` → **10 次全部 401，一次 429 都没有**
  （应用侧额度是 6 次/分钟，本该被限）。链路原因：
  - nginx 用 `$proxy_add_x_forwarded_for` 是**追加**语义：客户端自己发的 XFF 会**原样保留**，
    真实 IP 被追加在后面；
  - backend 的 uvicorn 带 `--forwarded-allow-ips *`（`always_trust`），
    `get_trusted_client_address()` 在该模式下直接取 XFF 的**最左**值作为 `scope["client"]`
    （见 uvicorn 0.52.3 `middleware/proxy_headers.py`）；
  - 于是应用侧限流按「调用方随便填的字符串」分桶 → 额度形同虚设，
    日志/审计里的来源 IP 也不可信。

- **改动**：`deploy/nginx.conf` 4 处 `proxy_set_header X-Forwarded-For` 由
  `$proxy_add_x_forwarded_for` 改为 **`$remote_addr`（覆盖）**，并在文件头写清原因与
  「将来前面加 CDN 要改用 real_ip 模块」的前提。本部署是单层 nginx 入口
  （backend 只绑 `127.0.0.1`），所以真实客户端就是 `$remote_addr`。

- **验证**：修复前线上实测 10 次伪造 IP 全通（见上）；修复后同一条命令应变成
  6×401 + 4×429（10 个请求全落到真实 IP 那一个桶）。nginx `limit_req_status 429` 与新配置
  经 `docker exec sekb-frontend grep` 确认已加载，部署 7 阶段全绿。

## 2026-09-15（部署卡在 90 秒健康检查而中止（放宽容差）+ 线上自称 development + 429 文案）

- **背景**：限流修复那次部署在**阶段 3/7「启动后端」**判定 `✗ backend 健康检查超时` 后直接中止，
  阶段 4–7（前端 nginx 重载、监控栈、端点验证）全都没跑，留下半成品状态 ——
  但事后看后端**完全正常**：`L1 短期记忆` 初始化一步就花了 **107 秒**（21:16:14 → 21:18:01，
  构建刚结束 CPU 被抢），加上 MCP 预热/LangGraph 构建，整体超过原来的 90 秒窗口。
  也就是说「假超时」的代价不只是多等，而是把一次健康部署判成失败并中断后续步骤。
  顺带发现两个小问题：线上 `/health` 与启动日志一直自称 `environment: development`
  （`config.yaml` 写死），以及前端把 429 的原因写死成「网关限流」（实际常态是应用侧限流）。

- **改动**：
  - `deploy/deploy.sh`：后端就绪等待 18×5s(90s) → **60×5s(300s)**；
    超时前先打印 `docker logs --tail 20 backend`，让「还在加载」与「启动就崩」能一眼分开；
    失败提示给出分情况的操作建议。
  - `backend/config.yaml`：`environment: development` → `${ENVIRONMENT:-development}`
    （compose 里 backend 有 `ENVIRONMENT=production`）；纯展示字段，无行为分支。
  - `frontend/src/pages/News.tsx`：429 文案不再断言是「网关限流」，
    改为「请求过于频繁，已自动重试；仍失败请等 1 分钟再试」（与应用侧 60s 窗口一致）。

- **验证**：`ENVIRONMENT=production` 下 `get_config().app.environment == "production"`；
  前端 `tsc --noEmit` 无错误；`bash -n deploy/deploy.sh` 通过；
  改动前已实测后端健康后一切正常（读额度 100/100 通过，见同批 changelog）。

## 2026-09-15（资讯 429 真凶是应用侧限流（读被生成额度限死）：读写分离 + 方法级分组）

- **背景**：上一轮把 429 归因于 nginx（`api_limit` 10r/s 被资讯页打满），按此加了
  `news_limit 30r/s + burst 60`。部署后实测**仍然 429**：走 https 连打 100 次
  `GET /sekb/api/v1/news/status` → 10 次 401 后 90 次 429。查 nginx error log：
  这 100 次里**没有任何** `limiting requests`（限流记录只有 `api_limit` 打健康检查的 21 条），
  且被拒请求的 `urt=0.001`（上游真回过）→ **429 是后端自己返回的**。
  真因：`RateLimitConfig.news_per_minute = 10` 覆盖整个 `/api/v1/news/` 前缀，
  把**读**（列表/正文/`status` 轮询）和**生成**（调 LLM）放在同一个 10 次/分钟的桶里；
  前端生成期间每 15s 轮询状态 + 切 tab 读列表正文，几下就把额度用光 ——
  这才是 owner 报的「加载周期报告列表失败 / 点第二次就失败」。
  另有 `docs/tech/05-API-REFERENCE.md` §1.3 写着「限流中间件未挂载、所有端点无生效限流」，
  与实际（`enabled: true` 自 2026-09-10）不符，是这次误判的直接原因。

- **改动**：
  - `backend/app/api/middleware.py`：路由组键支持**带方法**（`"POST /api/v1/news/"`），
    带方法的组优先于同前缀的纯前缀组；方法未知时不匹配方法组（避免被塞进小额度组误限）。
  - `backend/app/core/config.py` + `backend/config.yaml`：`news_per_minute: 10 → 120`（读），
    新增 `news_generate_per_minute: 6`（写）；`server.py` 注册两条组。
  - `deploy/nginx.conf`：显式 `limit_req_status 429;`（nginx 默认 503，前端会显示成
    「服务不可用」，与「请求过于频繁」是完全不同的用户结论）。
  - `docs/tech/05-API-REFERENCE.md`：§1.3 重写为真实的限流表（含两道闸与 429/503 区别），
    §15 漂移项 2 更正，并给出「怎么判断是 nginx 还是应用侧限流」的判据。

- **验证**：`test_rate_limit_middleware.py` 新增 6 条（POST 走方法组、GET 回落前缀组、
  未知方法不误入方法组、同种组内最长前缀仍优先、**生成额度用满不影响读**）；
  限流相关 48 passed；ruff 全过；`get_config()` 实测 `news=120 / news_generate=6`。

## 2026-09-15（应用侧告警真正接上：资讯失败推飞书（此前是死代码）+ 网关卡片/按钮/自测端点）

- **背景**：上一个提交（`4a715f3`）声称「在 `write_status` 失败时 fire-and-forget 推飞书」，
  但复核 diff 发现 `service.py` 只改了 `_period_label` 的 staticmethod —— `send_alert`
  **被定义却从未被调用**，是死代码：任务失败时一条告警都不会发。同时查
  `deploy/feishu-webhook/feishu_gateway.py` 还发现两个「改了不生效」的坑：
  网关是本地构建镜像，`docker compose up -d` 不带 `--build` 时同名 tag 不会重建；
  compose 用 `${VAR:-默认}` 时**空串会被解析成默认值**，所以「设空串=关闭告警」这条
  约定在容器里根本不成立。

- **改动**：
  - `backend/app/agents/news/service.py`：`write_status(ok=False)` 里真正调用
    `send_alert("科技资讯{日报|周报|月报}生成失败", 原因, source="news", severity="critical")`
    —— 手动触发与定时触发都收口在 `write_status`，一处接线两条路径全覆盖。
  - `backend/app/core/alerts.py`：加同故障去重（`DEDUP_WINDOW_S=300`，调度器重试/连点
    只推一张卡片）、`fire_and_forget`（无事件循环时只记日志不抛、持有 task 强引用
    避免发送前被 GC）、明确关闭开关 `off/none/0/disabled/no`（因为空串会被 compose
    解析成默认值）、`severity` 默认改为 Alertmanager 标准的 `critical`（红卡片）。
  - `deploy/feishu-webhook/feishu_gateway.py`：卡片展示 `来源: {source}`；按
    `source` 给「这是什么/建议」释义（原标题是动态中文，查不到原释义表）；
    `source=news` 时给「查看资讯」按钮（而不是点了没用的「查看对话记录」）；
    新增 `POST /test` 一键发自测卡片。
  - `deploy/deploy.sh`：监控栈启动改 `up -d --build`（否则改了网关代码部署完还是旧行为）。
  - `docker-compose.prod.yml`：backend 透传 `NEWS_ALERT_WEBHOOK_URL`（默认同网网关）。
  - `deploy/.env.prod.example`、`docs/ops/07-ALERTING-TROUBLESHOOTING.md`：补告警字段
    约定表、字段对应卡片效果、新增「故障 7：应用侧告警没收到」四个真实坑 + 一键自测。

- **验证**：`backend/tests/unit/test_alerts.py` 新增 20 条（载荷契约、去重、HTTP 500/连接
  异常不抛、关闭开关、`write_status` 失败**确实**调到告警 / 成功不调）；
  news 相关 54 passed；本地按网关代码渲染 news 卡片，标题红、含来源与释义、
  三个按钮 URL 正确（`/sekb/news`）。

## 2026-09-15（资讯失败推飞书告警 + 修监控栈漏传 env-file（飞书地址实际为空））

- **背景**：owner 要求「资讯任务失败要推飞书」。查既有实现发现 `deploy/feishu-webhook`
  是现成的网关（`POST /webhook`，接受 Alertmanager 载荷并渲染飞书卡片），
  但**只被 Alertmanager 使用**，应用侧没接。

- **⚠️ 顺带查出一个真问题**：`FEISHU_WEBHOOK_URL` 确实写在 `.env.prod` 里
  （部署前检查也正是 grep 它，所以一直显示「飞书 webhook 已配置」），
  但 `deploy/deploy.sh` 启动监控栈时是
  `docker compose -f docker-compose.monitoring.yml up -d`——**没带 `--env-file`**，
  于是 compose 插值 `${FEISHU_WEBHOOK_URL:-}` 得到**空串**，
  实测容器内该变量长度为 0 → **告警根本发不出去**。
  这是「检查项通过但运行时没生效」的典型：检查查的是文件，运行时读的是容器环境。

- **改动**：
  1. **新增 `backend/app/core/alerts.py`**：`send_alert()` 复用飞书网关
     （默认 `http://sekb-feishu-webhook:5001/webhook`，实测 backend 可解析且 `/health` 200），
     载荷用 Alertmanager 形状；**绝不抛异常**（旁路能力，发不出去不能影响生成）；
     `NEWS_ALERT_WEBHOOK_URL` 可覆盖，设为空串即关闭；
  2. **接线**：`NewsAgent.write_status()` 在 `ok=False` 时 fire-and-forget 推
     「科技资讯日报/周报/月报生成失败：<原因>」——手动触发与定时触发都会推；
  3. **修 `deploy/deploy.sh`**：监控栈 compose 加 `--env-file .env.prod`，
     让 `${FEISHU_WEBHOOK_URL}` 正确注入（否则网关永远收不到地址）。

- **修掉一个我自己引入的严重 bug（被 ruff 抓到）**：`_period_label` 原本是
  `@staticmethod`，我此前改成用 `self._today_local()` 却忘了去掉装饰器 →
  **生成周报/月报时会 `NameError` 崩溃**；测试没覆盖到该方法，是 **ruff F821** 拦下的。
  已改为实例方法，并补 2 条测试（周报=上周一、月报=上月）防回归。

- **验证**：后端 **799 passed**、ruff 全过、mypy 门禁 **306 ≤ 310**；
  `deploy.sh` 语法校验通过。部署后需实测：网关容器内 `FEISHU_WEBHOOK_URL` 非空、
  并故意触发一次失败看飞书是否收到。

## 2026-09-15（资讯三问题根因修复：nginx 限流 429 + 生成改异步提交 + 日报标签本地时区）

- **根因（nginx 访问日志实测）**：owner 报的「切 tab 报加载周期报告列表失败」与
  「再次点重新生成日报失败」**是同一个根因 —— 网关限流 429**：

  ```
  14  GET  /sekb/api/v1/news/weekly   429
  10  GET  /sekb/api/v1/news/monthly  429
   2  POST /sekb/api/v1/news/refresh  429   ← 连「生成日报」都被限流
  ```

  `/sekb/api/` 用的是一条**全站共享**的限流（`zone=api_limit rate=10r/s burst=30`）；
  资讯页切 tab 会瞬时发「列表 + 详情」多个请求，很容易打满 → 429。前端把 429 当成
  一般失败（响应没有 `detail`）就显示默认文案，看起来像功能坏了。

- **改动**：

  1. **nginx**：新增 `zone=news_limit rate=30r/s burst=60` 与
     `location /sekb/api/v1/news/`（更长前缀优先），资讯页不再挤占全站 API 配额；
  2. **生成改异步提交**（`routes/news.py`）：`POST /news/refresh`、`POST /news/{type}`
     用 `BackgroundTasks` **立即返回** `{accepted:true}`；已在生成中返回 **409**
     （明确文案「正在生成中，请稍候」）。原因：周报/月报实测约 10 分钟，同步请求会让
     浏览器先超时（日志里 `POST /news/refresh` 出现 **499** 客户端断开），而服务端还在跑、
     用户既没进度也没结果，只能反复点；
  3. **状态记录下沉到 agent**（`service.py`）：手动触发也写 `last_status.json`
     （原来只有定时任务写），因此「提交后轮询」对手动生成同样有效；
     并新增 `is_running()`（依据文件锁 + 600 秒残留判定）供并发保护；
  4. **日报标签改用配置时区**：原来用 `datetime.now(timezone.utc)`，
     北京时间 00:00–08:00 生成时标签会**差一天**（owner 已察觉日期疑问）；
  5. **前端**（`News.tsx` / `services/news.ts`）：适配「提交 + 轮询」；
     新增 `describeError()`（429 → 「请求过于频繁（网关限流），已自动重试」、
     409 → 「正在生成中」、5xx → 「服务正在重启或过载」）与 `withRetry()`
     （429/5xx 退避重试一次）；切 tab **去重**（已加载过不再重复请求）。

- **验证**：前端 `tsc --noEmit` 无错误；后端 **797 passed**；nginx 配置经
  `nginx -t` 校验（部署前）。部署后需人工确认：反复切 tab 不再报加载失败、
  连点生成第二次提示「正在生成中」而非失败、失败时顶部横幅显示原因。

## 2026-09-15（科技资讯：按钮独立 loading + 提交后轮询 + 失败横幅；backend 内存上限降到 2.5G）

- **背景**（owner 报的问题）：「点生成周报后，日报/周报/月报三个按钮都在 loading」。
  查证：前端只有**一个** `generating` 布尔值被三个 tab 共用（切 tab 只换按钮文案），
  所以确实会「三个都在转」；而根因是**请求真的没返回**——实测周报生成耗时约 10 分钟，
  中途还有 LLM 超时重试，浏览器/网关容易先超时，界面就一直转圈。

- **改动 1：按钮 loading 独立**（`frontend/src/pages/News.tsx`）
  `generating: boolean` → `generatingKey: 'daily'|'weekly'|'monthly'|null`；
  按钮 `loading={generatingKey === tab}`，**只显示当前 tab 的运行状态**；
  其他 tab 的按钮置灰（避免同时起三个重任务把后端拖垮）。

- **改动 2：提交 + 轮询**（同文件）
  提交后**立即返回**，由新加的 `pollTask()` 每 15 秒读一次 `/api/v1/news/status`，
  等「最近一次任务的 `finished_at` 变化」即判定本轮结束（最多 30 分钟），
  再按 `ok` 提示成功或**带原因的失败**，并刷新列表。这样界面不再假死。

- **改动 3：失败横幅**（同文件 + `services/news.ts` 加 `getNewsStatus()` 与
  `NewsTaskStatus` 类型）
  挂载时读一次状态；若最近一次任务 `ok=false`，顶部显示 `Alert`：
  「最近一次 X 生成任务失败：<原因>（完成时间 …）」——用户不必再靠「咦今天怎么没日报」发现。

- **改动 4：backend 内存上限 4G → 2.5G**（`docker-compose.prod.yml`）
  宿主机总共 3.6G，原先 4G 上限等于没有上限；资讯任务/embedding 冲高时会把同机其他
  服务（含 SSH）一起拖慢。**注**：本次 9/15 日报失败已确认**不是 OOM**
  （内核 OOM 记录 0 条、容器 `OOMKilled=false`），这是独立的加固项。

- **验证**：前端 `tsc --noEmit` **无错误**；后端 797 passed / ruff / mypy 门禁 305 ≤ 310
  （后端代码本轮未改）。部署后需人工在页面确认：切 tab 只转对应按钮、失败横幅能显示。

- **仍待办**：飞书告警（需接 webhook）；#4「月报 tab 列表加载失败」待 owner 复现确认。

## 2026-09-15（定时任务不再静默失败：失败重试 + 状态落盘 + /status 接口 + misfire 宽限）

- **背景**（owner 报的两个问题）：① 没有自动生成周报；② 9/15 的日报没出来。
  查日志后确认**两者都不是「没触发」，而是跑了但失败/静默跳过**：

  - 9/15 08:00（北京）日报任务**确实触发**（00:00:06Z 就有 RSS 抓取日志），
    但 APScheduler 记录 `Job "科技资讯（每日）" raised an exception`，
    traceback 首段是 `anyio/streams/tls.py` 的 SSL 读错误；磁盘上**没有**
    `daily_2026-09-15.md`。**失败只在服务端日志里留了一行，用户完全看不到。**
  - 周一 9/14 的周报：磁盘与日志里**都没有**任何 weekly 记录/文件
    （唯一的 `weekly_2026-09-07.md` 是 owner 手动点出来的，mtime 今天 17:59）。
    三个任务都**没设 `misfire_grace_time`（APScheduler 默认仅 1 秒）**——
    容器若恰好在触发时刻前后重启，这次运行会被**静默跳过**。

- **改动**（`app/scheduler/scheduler.py`、`app/api/routes/news.py`、`app/agents/news/service.py`）：

  1. **失败重试**：任务失败后自动重试 1 次（间隔 60 秒）——网络/TLS 抖动是实测
     最常见的失败原因；
  2. **状态落盘**：每次任务执行（成功也记）写 `data/news/last_status.json`
     （`kind/ok/period/error/started_at/finished_at/duration_s`），
     落盘失败也不影响任务本身；
  3. **接口可见**：新增 `GET /api/v1/news/status`（**声明在 `/{report_type}` 之前**，
     否则会被 catch-all 匹配成 report_type —— 这一点单独写了测试守着）；
  4. **misfire 宽限**：`misfire_grace_time=3600`（1 小时）+ `coalesce=True` +
     `max_instances=1`，容器重启导致的小延迟不会再被静默丢掉。

- **验证**：新增 `tests/unit/test_news_scheduler_status.py` **10 条**用例
  （状态落盘成功/失败/不可写目录、失败重试后成功、重试用尽记为失败、
  misfire 参数、**路由顺序不被 catch-all 吃掉**、状态文件缺失/损坏不报错）；
  全量 **797 passed**、ruff 全过、mypy 门禁 **305 ≤ 310**。

- **仍未做（下一轮）**：
  - 前端展示 `/status`（现在接口有了，页面还没读）；飞书告警也还没接；
  - #3「生成周报时三个按钮一直 loading」—— 需要把生成改为「提交任务 + 轮询状态」
    或至少给前端加超时（实测周报耗时约 10 分钟且中途有 LLM 超时重试）；
  - #4「月报 tab 列表加载失败」**未能复现**（容器内 `list_periodic('monthly')` 正常、
    路由存在），怀疑是今天多次部署期间请求撞上重启窗口，待 owner 复现确认。

## 2026-09-15（周报/月报改为按自然周期取数（修复期号与内容不一致））

- **背景**（owner 报的 bug）：「点击生成周报后没有正确生成上周的周报」。
  实测：生成的期号是 `2026-09-07`（**正好是上周一**，期号没错），但内容覆盖的是
  **最近 7 天（含本周）** —— 因为采集窗口来自 `time_window_hours`，它只能表达
  「相对当前时刻往前 N 小时」。周二生成周报时，「往前 7×24 小时」= 上周二~本周二，
  于是标签写「上周」、内容却是跨周混合。月报同理（往前 30 天 ≠ 自然月）。

- **改动**：
  - `NewsFilter` 支持显式起止时间 `since`/`until`（**优先于** `time_window_hours`，
    左闭右开）；
  - `NewsAgent._period_window()`：按 **period 标签**算自然周期
    （weekly：上周一 00:00 → +7 天；monthly：该月 1 日 → 次月 1 日；含跨年边界），
    返回**本地时区**的 aware datetime（自然周/月是本地日历概念，转 UTC 再渲染会差一天
    —— 这是实现过程中实测踩到的）；
  - `_generate_report` 调整为**先算周期标签、再按标签算窗口**，把窗口交给过滤器；
  - 新增 `time_span_override`：把**真实日期区间**（如 `2026-09-07 ~ 2026-09-13`）写进
    提示词上下文，替换固定的「过去一周 / 本周」措辞（并把「本周」换成中性的「本期」），
    避免模型按错误的周期口径写作。

- **验证**：新增 `tests/unit/test_news_period_window.py` **9 条**用例
  （自然周窗口、自然月窗口、**12 月跨年**、日报返回 None、坏标签不抛异常、区间文案、
  过滤器左闭右开、显式区间优先于一年小时窗口的反证、无时间条目仍保留）；
  全量 **787 passed**、ruff 全过、mypy 门禁 **301 ≤ 310**。

- **⚠️ 已知残留**：`_PERIOD_CONTEXTS` 里 weekly/monthly 的 `forecast_horizon` 等固定措辞
  仍在，只覆盖了 `time_span` 与 `period_label`；若要彻底口径一致，需要把整段周期语境
  参数化（下一轮可做）。

## 2026-09-15（响应体异常可界定：出站体检 + self_check 自检工具（限长暂缓））

- **背景**：owner 决定**限长相关功能暂缓**（不做事后裁剪、暂不做精简版报告），改为要求
  「**能清楚地界定问题边界**」——若响应体异常，要么是我们的代码问题（必须报错暴露），
  要么是平台行为（先不管）。

- **一个重要的事实澄清（也是暂缓的依据）**：限长这件事的**原始依据是二手的**——
  Dify 一条**社区 issue**（#18731，已 closed）称响应超约 68000 字符时工具响应变空；
  千帆是**API 节点**文档写「返回内容 ≤ 1M」（不是我们走的 MCP-SSE 节点）；
  扣子/百炼**没查到**量化限制。而且我此前把它写成「最可能踩坑」**强于证据**。

  随后实测把猜测变成数字（**200 个职位 + 真 LLM**）：

  | 单次职位数 | 报表总长 | 其中 `jobs` 回显 |
  |---|---|---|
  | 12–22 | 4.5–8 KB | 68–76% |
  | **200** | **65 KB** | **85%** |

  即：几十个职位完全不接近任何阈值；两百个职位才刚好贴到那条 68K；而大头是
  **`jobs` 回显（不是模型产出，`max_chars` 管不到）**。所以限长被暂缓、且后续若要
  处理，应优先考虑「不回显 jobs」而非限长。

- **改动**（内核 `jobcopilot`，指针 → `e7592f7`）：

  1. **出站体检**（`inspect_response`，每次工具调用执行）：记录返回字节数 + 校验 JSON
     合法性；无法序列化或超过 8MB 自设上限时**显式报错**，错误信息明确写
     「**不是平台限制，是我们的输出过大**」并带确切数字 —— 防止反向误判。
  2. **`self_check` 工具**：固定 <1KB 响应（版本 / 内核 commit / 提示词来源 / 传输 /
     路径前缀）。它是「体积」变量的**对照组**：它能通而 `analyze_*` 不通 → 差异在体积
     （平台侧）；它也不通 → 链路问题（地址/令牌/Host/网络）。
  3. **`docs/ops/15-MCP-ENDPOINT.md` §5.5**：5 步判定表，把边界钉死
     （链路 → 我们的日志 → 是否超我们自设上限 → 反代是否截断 → 剩下才算平台侧）。

- **顺带修掉两处脆弱断言**：测试里写死「工具数 == 6」，加工具就假失败 → 改为断言
  「关键工具都在」；`test_mcp_server.py` 的 `EXPECTED_TOOLS` 契约补上 `self_check`。

- **验证**：274 passed / 1 skipped、ruff 全过、mypy strict 34 文件、L1+L2 与基线一致；
  部署后从公网调 `self_check` 验证（见部署记录）。

## 2026-09-15（classify_role 两级判定 + max_chars 输出长度约束（交 LLM，不裁剪））

- **背景**：两项按 owner 确认的方案实现——① `classify_role` 归类优先级（owner 选 A+C，
  **不新增桶**）；② 云端响应体积改为**把限长交给 LLM**（owner 明确：不做事后裁剪）。

- **1) `classify_role`：职能名词优先于领域词**（内核指针 → `b9a186f`）

  原实现是单表「首个命中即归类」，而「算法/模型」排在「产品经理」之前，于是**领域词
  抢走了职能判定**。改为两级：先看「这个人干什么」（职能名词），再看「什么方向」（领域词）。
  实测 **5 条归类被修正**：

  | 标题 | 改前 | 改后 |
  |---|---|---|
  | 大模型产品经理（评测方向） | 算法/模型 | **产品经理** |
  | 大模型应用产品经理 | 算法/模型 | **产品经理** |
  | 大模型平台架构师（专家岗） | 算法/模型 | **架构师/Leader** |
  | 技术售前顾问（数据平台） | 运营/策略 | **其他**（不再被「数据」这个极宽的词带走） |
  | 数据智能体工程师 | 运营/策略 | **其他** |

  **⚠️ 口径变化（用户可见）**：桶名集合**没有变**（仍是 7 桶 + 其他），但报告里
  `role_distribution` 的**数字会变**。基线不受影响（它只存通过率与提示词指纹），
  已实跑 L1+L2 确认与基线一致。

  **C（契约化）**：51 条数据集职位全部加 `expected_role` 标注，并由
  `tests/test_stats.py` 断言；另加一条「桶名集合不得变化」的断言，把「不新增桶」
  这个决定变成需要显式修改测试才能推翻的事。标注放在数据集里是安全的——
  `build_job_summaries` 只取 title/company/salary/jd_text，不会泄进提示词。

- **2) `max_chars`：把长度预算交给模型**（新增 `core/prompts/budget.py`）

  按 owner 明确要求：**不做事后裁剪**（按字节裁剪会产出非法 JSON，模型必然解析失败）。
  改为把预算写进提示词，由模型自己写短；并在指令里要求
  **JSON 键结构必须完整保留**（不得删键/截断），避免模型为了短而破坏结构。

  - 单职位 7 段均摊、批量 2 路均摊；`max_chars` 校验 300..200000，非法给可操作错误；
  - **安全系数 0.8**（实测依据见下）。

- **真 LLM 实测（DeepSeek，批量两路）**：

  | 配置 | 实际输出长度 |
  |---|---|
  | 不设限 | 5539 字符 |
  | `max_chars=3000`（直告模型） | 3291 字符（**+10%**，超限） |
  | `max_chars=3000`（加 0.8 安全系数后） | 3188 字符（**+6%**，仍超但明显收敛） |

  **结论：模型对精确字符数不精确**，这印证了之前的预警；安全系数能收敛但不能保证。

- **⚠️ 更重要的发现（需 owner 决定）**：`max_chars` 只管**模型产出**，而报表里
  `jobs` 回显 + `stats` **不是模型输出**，实测占比很高：

  | 数据集 | 职位数 | 总长 | jobs 回显 | 占比 |
  |---|---|---|---|---|
  | agent_dev | 22 | 7938 | 6105 | **76%** |
  | product | 17 | 5441 | 4073 | **74%** |
  | presales | 12 | 4538 | 3091 | **68%** |

  因此**大批量时单靠 `max_chars` 可能仍撑不进平台上限**（Dify 68K / 千帆 1M）。
  两个候选方案（待 owner 选）：① 加「不回显 jobs」开关（调用方本来就持有这些数据）；
  ② 分配预算前先扣掉非模型开销（`stats` + 回显 + prompt_meta），只把剩下的给模型。

## 2026-09-15（后端镜像改用 CPU-only torch（去掉 3.2GB CUDA 死重量））

- **背景**：服务器的后端镜像里 torch 是 `2.14.0+cu130`（CUDA 构建），但**这台机器没有
  GPU**（`torch.cuda.is_available()` 为 False）。CUDA 构建额外拖进 **19 个 nvidia-* 包**，
  实测 `site-packages/nvidia` 占 **3.2GB** —— 纯死重量，而且显著抬高每次构建的磁盘峰值
  （曾把 59G 磁盘挤到构建失败，只能靠 `docker builder prune` 临时腾空间）。

- **根因**：torch 不是 `requirements.txt` 的直接依赖，而是被 **sentence-transformers
  传递**拉进来的；PyPI（及其国内镜像）上的 Linux torch 默认就是 CUDA 构建。

- **改动**（`backend/Dockerfile`）：在装 `requirements.txt` **之前**插入一步，
  从 PyTorch 官方 CPU 索引装 `torch==2.14.0+cpu`：
  - **顺序是关键**：先装 `+cpu`，后面 pip 看到 `torch>=x` 已满足就不会再拉 CUDA 版；
  - `--extra-index-url ${PIP_INDEX}`：torch 的运行时依赖（sympy/networkx/jinja2 等）
    不在 PyTorch 索引上，必须能回落到常规源解析；
  - 写死 `+cpu` 本地版本号：它只存在于 PyTorch 官方索引，不会与镜像源歧义。

- **实测依据（改之前查证）**：
  - CPU 索引有 cp311 x86_64 的 `torch-2.14.0+cpu` 轮子，**196MB**（对比 3.2GB nvidia 包）；
  - 服务器可直连 `download.pytorch.org`（HTTP 200，0.85s），实际下载 5.6MB/s 成功。

- **验证**（部署后实测，见部署记录）：镜像内 `torch.__version__` 为 `+cpu`、
  `nvidia` 目录消失、**本地 embedding 仍可用**（sentence-transformers 实际编码一次）、
  镜像体积下降。这条必须验——本地 Embedding（L3 知识库）依赖 torch。

- **⚠️ 第一版没成功（值得记下来）**：我先用「在装 requirements **之前**单独装一次
  `torch==2.14.0+cpu`」的办法。构建日志显示那一步确实装了 CPU 轮子（196MB），
  但紧接着装 `requirements.txt` 时 pip **又拉了 CUDA 版**：

  ```
  #16 Collecting torch==2.14.0+cpu            ← 我的步骤
  #16 Downloading torch-2.14.0+cpu...whl (196.2 MB)  ✅
  #17 Collecting torch>=2.2 (from sentence-transformers>=3.0.0)
  #17 Downloading torch-2.14.0-cp311...whl (554.6 MB) ← 又装回 CUDA 版
  ```

  **根因**：Dockerfile 用 `pip install --prefix=/install`，而 `--prefix` 目标**不在
  sys.path 上**，所以后一步 pip 看不见前一步装的东西，会重新解析依赖树。结果
  `nvidia-*` 照旧被装进来，镜像反而从 10.9GB 涨到 **11.1GB**。

  **正确做法**：改用 **约束文件**（`backend/constraints-image.txt` 写死
  `torch==2.14.0+cpu`），在同一次解析里钉住版本，并加
  `--extra-index-url https://download.pytorch.org/whl/cpu` 让 `+cpu` 轮子可被命中。
  改完先用 `pip install --dry-run` 在容器里验证（输出 `Would install torch-2.14.0+cpu`、
  计划里无任何 nvidia 包），再重建镜像 —— 避免又花 15 分钟才发现。

  经验：`--prefix` 安装 + 多步 pip 是不可靠组合；**能用约束解决的，不要靠步序**。

- **⚠️ 第二版也失败了一次（根因不同，记下来）**：改用「约束 + `--extra-index-url
  https://download.pytorch.org/whl/cpu`」后，构建**超时**失败。日志显示
  `networkx` 下载速度只有 **36.5 kB/s**（第 17 步跑满 1002 秒后撞上构建的 20 分钟上限）。

  **根因**：加了 `--extra-index-url` 后 pip 会对**所有包**都去查那个境外索引，
  把普通依赖的下载也拖慢。

  **最终做法**（已验证）：分两步，让境外索引只被用来取那一颗轮子——
  1. `pip download --no-deps -d /torch-wheel --index-url <CPU 索引> torch==2.14.0+cpu`
     （196MB，实测 ~35 秒）；
  2. 主安装步骤改用 `--find-links /torch-wheel` + `-c constraints-image.txt`，
     torch 由**本地轮子**满足，其余依赖仍走国内镜像，**不引入任何境外索引**。

  改前同样先用 `pip install --dry-run` 在容器里验证：输出 `Would install
  torch-2.14.0+cpu`、无 nvidia 包、无报错。

- **✅ 最终实测结果（已上线）**：

  | 指标 | 改前 | 改后 |
  |---|---|---|
  | 后端镜像 | **10.9GB** | **3.34GB**（省 **7.5GB**） |
  | 容器内 torch | `2.14.0+cu130` | **`2.14.0+cpu`**（`cuda.is_available()` False） |
  | `site-packages/nvidia` | 3.2GB（19 个包） | **不存在** |
  | 服务器可用磁盘 | 16–24GB（每次部署要清缓存） | **32.7GB** |

  **功能回归**（本地 Embedding 依赖 torch，必须验）：
  - `SentenceTransformer` 真实编码成功，输出 384 维向量（模型走本地缓存，不需要重新下载）；
  - `/api/v1/health/` status=ok，内核 `f037c97` v0.1.0 healthy；
  - 全部端点正常；MCP 容器内存仅 45MB（复用同一镜像，未新增镜像）。

  附带收益：以后每次部署不再需要靠 `docker builder prune` 临时腾磁盘（这正是之前部署
  反复被磁盘卡住的根源）。

## 2026-09-15（修正 SSE 子路径下消息路由未带前缀（客户端 404））

- **现象**：对外暴露后，公网客户端连 `https://bos-studio.tech/jobcopilot/sse` 能收到
  `event: endpoint`（内容是 `/jobcopilot/sse/messages/?session_id=...`），但照着该
  地址 POST 回来是 **404**。

- **根因**：MCP SDK 的 `sse_app(mount_path=...)` **只改「对外声明的消息端点」路径，
  不移动实际注册的路由**（路由取的是 `settings.message_path`）。我原先只设了
  `mount_path`，于是「声明带前缀、路由停在 `/messages`」。

- **修法**（内核 `jobcopilot`，指针 → `f037c97`）：直接设置
  `settings.message_path = "<前缀>/sse/messages/"`，`mount_path` 用 `/`，
  让**声明与路由一致**（前缀由 nginx 原样透传，不需要靠 `mount_path` 拼）。

- **我的测试原本也漏了这一层**（值得记一笔）：原用例只断言 endpoint 事件里的字符串，
  没断言真实路由，所以「声明对、路由错」能通过测试。已补强为两条：
  1. `test_base_path_sse_routes_are_actually_registered` —— 断言**真实注册的路由**
     里有 `/jobcopilot/sse/messages`；
  2. `test_base_path_sse_full_session_works` —— 跑**完整** SSE 会话
     （initialize + list_tools，带 30s 硬超时），因为「消息端点路径对不对」只有真
     POST 一次才知道。

- **验证**：本地实测完整 SSE 会话成功（POST 消息端点得 202，ListTools 返回 6 个工具）；
  内核 254 passed / ruff / mypy strict 全过；L1+L2 与基线一致。

## 2026-09-15（对外暴露 JobCopilot MCP 端点（/jobcopilot/mcp 与 /sse，供云端平台接入））

- **背景**：云端平台（扣子/百炼/千帆/Dify/HiAgent）**只认 HTTP**，而 SEKB 用的内核
  MCP Server 是 backend 容器内的 **stdio 子进程**，外部访问不到。owner 已批准在既有
  限制下开放 HTTP/HTTPS，并选定**子路径**形式（免加 DNS 记录）。

- **改动**：

  | 位置 | 内容 |
  |---|---|
  | `jobcopilot`（内核，指针 → `9fff941`） | 新增 `JOBCOPILOT_HTTP_BASE_PATH` 支持路径前缀；`build_http_app` 用前缀注册 `<前缀>/mcp` 与 `<前缀>/sse`，并把 SSE 的 message 端点写成 `<前缀>/sse/messages/` |
  | `docker-compose.prod.yml` | 新增 `jobcopilot-mcp` 服务：**复用 backend 镜像**（不新增镜像、不重新构建、不占新磁盘），`--http --port 8765`，端口只绑 `127.0.0.1`，`read_only: true`，内存上限 1G |
  | `deploy/nginx.conf` | 新增 `upstream sekb_jobcopilot_mcp` + `location /jobcopilot/`：**proxy_pass 不带 URI**（原样透传前缀）、`Host $host`（内核据此做白名单）、SSE 关缓冲 + 300s 读超时 |
  | `deploy/deploy.sh` | 新增阶段 3.5：在 frontend **之前**启动 MCP（nginx 解析不到 `jobcopilot-mcp` 会启动失败）；部署后端点检查加入 MCP（401/200 均算通过）；`warn` 补上第二个参数（原先提示被静默丢弃） |

- **安全设计（都写进了注释，不是默认值凑巧）**：
  - **容器内刻意不配任何 LLM Key** → 公网端点必须 BYOK，每个调用方带自己的 Key，
    没人能花这台服务器的额度；
  - `JOBCOPILOT_HTTP_TOKEN` 未设时内核**拒绝启动**并打印可操作提示（安全闸）；
  - `JOBCOPILOT_HTTP_ALLOWED_HOSTS=bos-studio.tech`，否则平台会收到 421；
  - `read_only: true`：`save_profile` 在共享端点上用不了是**有意的**
    （画像会串到别人身上，调用方应改用 `user_profile` 按请求传）；
  - 端口只绑回环，对外一律经 nginx（TLS + 令牌），不新开公网端口。

- **验证**：见部署后实测（本轮记录）——从本机经公网用官方 MCP 客户端连
  `https://bos-studio.tech/jobcopilot/mcp` 列工具；无令牌 401；SSE 的 endpoint 事件
  带 `/jobcopilot` 前缀。内核侧另有 2 条单测守着前缀行为（含「不带前缀的老路径返回
  404」，防止「配了前缀其实没生效」）。

## 2026-09-15（Eval L3 首次真跑：5 个评审维度均分 + 抓到一个 stub 测不出的失败）

- **背景**：评估骨架的 L3（LLM 评审）此前**从未真跑过**（缺 Key），报告里只有 L1+L2。
  计划 §3.5 明确「全量 L3 ≈ 180 次调用，成本不到 1 元」，属于发版前该做的动作，
  因此用服务器 `.env.prod` 里的 DeepSeek Key 真跑了一次。

- **实测**（`jobcopilot eval --level 123 --provider deepseek`，**275 秒**，约百次调用）：

  | L3 维度 | 均分（满分 5） |
  |---|---|
  | `actionability`（建议可执行性） | **4.67** |
  | `groundedness`（是否编造） | **4.33** |
  | `jd_coverage`（覆盖 JD 关键要求） | 4.00 |
  | `track_coverage`（赛道划分合理性） | 4.00 |
  | `salary_anchored`（薪资有据） | **3.67**（最低，符合预期：数据集有 4 条故意不给薪资） |

- **真跑抓到 stub 永远测不出的失败**：8 个单职位用例里 1 个失败——
  `single-无薪资` 的 `interview_qa` 段落为空。核对代码后确认这是**设计内的降级**：
  单步 LLM 调用失败/返回脏 JSON → 该步产出空结构并记 error 日志
  （`core/analyzers/single.py`），工具层把空段落汇总成 `warnings` 返回
  （`mcp/tools.py`），全部为空才报 `ToolError`。即系统行为正确，是
  L1 的 `sections_complete` 在真模型下抓到了质量信号。

- **由此写明两个容易误导人的事实**（已写进 `docs/eval-report.md` §2.2 与局限段）：

  1. **L1+L2 的 100% 是构造性的**：CI 用 `SkeletonStubLLM`，它永远返回骨架，
     所以 `sections_complete` 恒为 100%。**L1+L2 守的是「结构没回归」，不是「质量好」。**
     质量信号只在 L3 出现——这正是「改提示词必须附 L3 报告」这条规则的理由。
  2. **基线必须保持 stub 口径**：真 LLM 那次与基线比对会报「0.975 < 1.0」，
     因为两者不可比。因此**刻意不用 `--update-baseline`**——那会把「允许 12.5%
     段落缺失」固化进零成本门禁，反而把守门员放松了。真 LLM 分数只作趋势记录。

- **内核指针**：`17136ce → 0855470`（本次只动 `docs/eval-report.md`）。

## 2026-09-15（Eval 数据集扩到计划规模（22/12/17 + 8 个单用例，覆盖 8 个方向桶））

- **背景**：计划 §3.3 写的数据集规模是 20/10/15，实际长期停在 **6/6/6**——
  自己的评估报告把这条列为首要局限。数据集太小，`stats_exact_match`
  这条「可精确断言」的核心检查接近白给（分布太简单，碰巧对上很容易）。

- **改动**（内核 `jobcopilot`，指针 `44c399c → 17136ce`）：

  | 数据集 | 变化 | 多样性（实测） |
  |---|---|---|
  | `agent_dev` | 6 → **22** 条 | 21 家公司 / 7 个方向桶 / 7 个热词 |
  | `presales` | 6 → **12** 条 | 12 家公司 / 7 个方向桶 / 4 个热词 |
  | `product` | 6 → **17** 条 | 16 家公司 / 3 个方向桶 / 3 个热词 |
  | 单职位用例 | 3 → **8** 个 | 见下 |

  - 三个批量集合计覆盖 `classify_role` 的**全部 8 个方向桶**；
  - 单职位用例新增 5 个**结构变异**：极短 JD、超长 JD（20 条职责 + 15 条要求）、
    中英混杂、只有任职要求（残缺结构）、无薪资信息；
  - 批量集里也放了变异：缺公司/缺薪资、纯英文、超长 JD；
  - **文件名不带条数**（计划写的是 `agent_dev_20.json` 那种形式）：实际条数多于计划
    下限，带数字会让文件名与内容不符；规模下限改由测试断言守着。

- **验证**：
  - L1+L2：**11 个用例、通过率 100%、各断言 100%、与基线 `v1.json` 一致**
    （基线只比提示词指纹与通过率、不比用例 id，所以扩容无需重新基线化）；
  - 原 `test_load_datasets` 写死 `len(single) == 3`，一扩容就假失败 → 改为断言
    「结构完好 + 达到计划下限 + case_id 唯一 + 每条至少能和统计口径对上」；
  - 门禁：251 passed / 1 skipped、ruff 全过、mypy strict 33 文件无问题。

- **顺带发现的已知行为（未改，已记入评估报告与疑问清单）**：`classify_role` 是
  「首个命中即归类」，而「算法/模型」排在「产品经理」之前，所以
  **「大模型产品经理」会被算成算法岗**。规则表被刻意冻结（改它会改变报告口径），
  因此只记录不改。

- **部署**：与本次指针更新一起部署上线（构建缓存可回收 8.21GB，清理后仍留约 12GB
  可复用缓存，因此构建空间比上一次宽裕）。

## 2026-09-15（JobCopilot P6+P7 上线：内核 44c399c（双传输/BYOK）与 v0.1.0）

- **背景**：P6（云端平台适配）与 P7（开源发布）的代码此前已完成，但部署被磁盘卡住
  （可用 24606MB 未达门禁 25000MB）。本次腾出空间后一次性上线两个阶段。

- **上线内容**：
  - 内核指针 `2631ee3 → 44c399c`：HTTP 形态支持按请求传 Key（BYOK）、访问令牌
    （含 `?token=` 兜底）、Host/Origin 白名单、对外绑定安全闸、**同进程双传输
    `/mcp` + `/sse`**（千帆只吃 SSE、火山 AgentKit 只吃 Streamable HTTP）；
  - 内核版本号 `0.0.1 → 0.1.0`（P7 打包就绪的一部分）。

- **腾空间的方式**：`docker builder prune -af` 回收 **11.82GB**（这次缓存里确有可回收
  内容；此前多次为 0B——`docker system df` 的「可回收」在本机不可信，已在
  `docs/ops/13-DISK-MEMORY.md` 记录）。

- **部署过程中的三个脚本缺陷（都已修复并验证）**：
  1. 子模块对齐排在部署前检查之后 → 指针一更新就被自己的检查拦住，只能 `--skip-check`
     绕过（那等于跳过全部检查）；
  2. 部署后端点检查一次性探测 → 监控栈重启未就绪就打 `grafana: 000 FAIL` 假失败；
  3. 部署前检查自带 20GB 磁盘阈值，高于部署后稳态可用空间（~17.7GB）
     → 「每次成功部署都把下一次拦住」的自锁。

- **部署后验证（实测）**：
  - 仓库钉住 `44c399c` == 运行内核 `44c399c`（`GET /api/v1/health/`）；
  - `kernel.version = 0.1.0`、`healthy = true`、`prompts = 12`、`prompt_source = local`
    （`prompt_dir = /app/prompt/job`，宿主热改目录按设计优先）；
  - `bash scripts/check_kernel.sh` → ✅ 与钉住的 commit 完全一致；
  - 端点 health 200 / metrics 200 / 前端 200，`https://bos-studio.tech/sekb` 200；
  - 部署前自动备份 + 同天去重生效：备份目录 9 份，今天保留 2 份、`0908~0915` 每天各 1 份；
  - 部署前检查：**通过 36 项 / 失败 0 项 / 警告 2 项**（两条警告均真实有用：
    磁盘偏低、存在旧监控容器）。

- **仍未完成**：P6 的「每平台跑通一次」需要各平台账号 + 公网 HTTPS 域名；
  P7 的 PyPI 发布需要账号/token（产物体检 `twine check` 已 PASSED）。两条都已在
  疑问清单中列出，不阻塞已上线内容。

## 2026-09-15（部署验证可靠性：端点检查加重试 + 修两处假警告）

一天之内连续两次部署都出现同一批「假 FAIL / 假警告」，会让部署验证输出失去可信度
（人一旦习惯忽略它，真正的失败也会被忽略）。三处一起修。

### 1. 部署后端点检查改带重试（`deploy/deploy.sh`）

- **现象**：两次部署都打印 `grafana: 000 FAIL`，而**一分钟后 grafana 就是 healthy**。
- **根因**：一次性 `curl` 探测；监控栈（尤其 Grafana）重启后要几十秒才就绪。
- **修法**：抽出 `probe_endpoint`，每个端点最多重试 6 次 × 5 秒，并在结束时汇总
  「N 个端点在重试窗口内未就绪」，而不是逐条打 FAIL。前端 `/` 同时接受 200/301
  （nginx 会重定向到 `/sekb`）。
- **验证**：从真实脚本抽取该函数实测 5 项全过——200 立即通过、200 在允许列表内通过、
  404 重试 3 次后失败（耗时 ≥2s，证明确实重试）、端口关闭重试后失败并返回 1。
  过程中还测出我自己引入的两个瑕疵并修掉：`printf` 里 `\n` 写成了字面量、
  curl 失败时状态码被拼成 `000000`。

### 2. 定时备份 cron 假警告（`deploy/pre-deploy-check.sh`）

- **现象**：长期报「定时备份 cron 未配置」，而备份每天都在跑。
- **根因有两个**：① 只查 **root** 的 crontab，而任务装在部署用户 **bo** 的 crontab 里；
  ② grep 的模式是 `backup.sh`，而脚本名是 **`backup_kb.sh`**（中间的 `_kb` 让模式永远匹配不上）。
- **修法**：同时查 root 与当前用户，模式改为 `backup_kb\.sh|backup\.sh`。
- **验证**：服务器实测——新逻辑「✅ 已配置」；旧模式确认匹配不上（即假警告根因）；
  修复后部署日志该项变为通过。

### 3. Git remote 假警告（同文件）

- **现象**：报「Git remote 未配置」，实际有两个 remote。
- **根因**：只认名为 `origin` 的 remote，而本仓库是 `gitea` / `github`
  （`origin` 指向 GitHub SSH，中国网络经常连不上）。
- **修法**：有任意 remote 即通过，并把名字打印出来便于核对。
- **验证**：服务器实测输出「✅ Git remote 已配置（gitea github）」。

### 4. 部署前检查的磁盘阈值造成「成功即自锁」（同文件）

- **现象**：部署成功后再跑部署前检查 → `✗ 磁盘空间不足: 17750MB`（要求 20GB），
  即**每次成功部署都会把下一次部署拦住**。
- **根因**：磁盘阈值有**三处且不一致**——pre-deploy-check 的 20GB（硬失败）、
  `deploy.sh` 阶段 2 的 `NEED_MB=25000`（先清缓存再判定）、阶段 7 的缓存上限 14GB。
  而这里的 20GB 恰好**高于**部署后的稳态可用空间（~17.7GB），于是形成自锁；
  更糟的是它排在阶段 2 之前，让「先清缓存再判定」这条正确路径根本没机会执行。
- **修法**：本项改为**告警**，判定权交给阶段 2 那唯一的门禁（它知道要先清构建缓存，
  且失败时会给出可操作提示）。告警文案写明「峰值约需 17-18GB、阶段 2 会先清缓存」。
- **验证**：服务器实测由 `失败：1 项` 变为 **`通过：36 项 / 失败：0 项 / 警告：2 项`**，
  且剩下两条警告都是真实有用的（磁盘偏低、存在旧监控容器）。
  （修改中我不小心把 `DISK_AVAIL` 的赋值行一起删掉，导致 `unbound variable`——
   已在提交前修回并复测。）

## 2026-09-15（部署脚本：子模块对齐提到检查之前（指针更新后不必再 --skip-check））

- **背景**：内核指针更新（`2631ee3 → 44c399c`）后执行 `bash deploy/deploy.sh`，
  在**阶段 0 就被拦住**：`✗ 内核 jobcopilot 子模块状态异常`。

- **根因是顺序反了**：阶段 0 的 `pre-deploy-check.sh` 会校验内核子模块，而真正的
  对齐在**阶段 2**（`git submodule update`）。`git pull` 又**不会**自动更新子模块，
  所以「指针刚更新、子模块还停在旧 commit」是**预期状态**，却导致部署被自己拦住。
  后果不是「多一步」，而是**逼人加 `--skip-check` 绕过——那等于连其他所有检查
  一起跳过**（当天协作者部署时就是这么绕的）。

- **改动**：在阶段 0 之前插入子模块对齐，且**仅在子模块工作区干净时**才自动对齐；
  有本地改动则不动它，交由检查报错（不悄悄覆盖别人的改动）。

- **验证**：
  - 修复前：服务器上 `bash deploy/deploy.sh` 在阶段 0 失败（`失败：1 项`）；
  - 修复后：同一命令直接进入阶段 1 并完成部署（内核 commit 从 `2631ee3` 变为 `44c399c`）；
  - 顺带说明：本次之所以能一眼看出「期望 44c399c / 实际 2631ee3」，是因为同一天刚
    修过 `check_kernel.sh` 取错期望值的问题——否则会打印「期望=实际 却说不一致」，
    看起来像工具坏了，更容易被 `--skip-check` 糊过去。

## 2026-09-15（修正两处验证工具：内核解耦测试误报 + mypy 门禁静默绿灯）

全量验证时发现两个「工具本身不可靠」的问题——比代码 bug 更值得修，因为会误导判断。

### 1. 解耦测试误报（`test_kernel_does_not_depend_on_sekb`）

- **现象**：内核新增 HTTP 双传输后该测试失败，报「内核反向依赖 SEKB」。
- **根因**：原实现判断源码里是否出现子串 `"app."`，而新代码里的普通变量名
  `http_app.router` / `sse_app.routes` 恰好含这个子串。**内核并没有依赖 SEKB 的
  `app` 包**，是启发式太粗。
- **修法**：改为正则匹配真正的导入语句
  （`^[ \t]*(?:from|import)[ \t]+app(?:[.\s]|$)`，含缩进以覆盖函数内延迟导入），
  并在 docstring 里记下这次误报的原因。

### 2. mypy 门禁静默绿灯（`backend/scripts/mypy_gate.sh`）

- **现象**：脚本打印「✅ mypy 错误数 ≤ 基线（0 ≤ 310），无新增类型错误」。
- **根因**：脚本内用 `python -m mypy`，在当前环境找不到 mypy 时命令整体失败、
  错误行数为 0，于是 0 ≤ 310 判定通过——**门禁看起来是绿的，其实一次都没跑**。
- **修法**：执行前先探 `python -m mypy --version`，不可用直接按失败处理并给出
  激活环境的提示；另按 mypy 退出码语义（0=无错误 / 1=有类型错误 / >1=执行失败）
  拦截「执行失败但错误行为 0」的情况。
- **顺带确认真实口径**：`--ignore-missing-imports` 下当前 **301 ≤ 310 基线**，
  即存量类型债没有增加（此前看到的 318 是我漏加该参数导致的误读）。

### 验证

- 无 mypy 的环境跑门禁 → **exit 1** 且给出可操作提示（修复前是绿灯 exit 0）；
- 有 mypy 的环境跑门禁 → `301（基线 310）` 通过；
- 解耦测试 11 项全过；
- **SEKB 全量：778 passed / 0 failed**，ruff 全过。

## 2026-09-15（修正 check_kernel.sh 的「期望 commit」取值（曾打印出「期望=实际却不一致」））

- **背景**：部署内核指针后运行 `bash scripts/check_kernel.sh`，它打印出：

  ```
  期望 commit  2631ee3...  (SEKB 钉住的版本)
  实际 commit  2631ee3...
  ❌ 实际 commit 与 SEKB 钉住的不一致
  ```

  两个数字一样却判定不一致——看起来像工具坏了，实际是**期望值取错了源**。

- **根因**：`EXPECTED_SHA` 取自 `git submodule status` 的输出，而这条命令报的是
  **子模块当前检出的 commit**，不是父仓库索引里钉住的 gitlink。于是「期望」与
  「实际」永远来自同一个值，标签还写着「SEKB 钉住的版本」，纯属误导。
  （判定本身是对的：`+` 前缀确实表示子模块与索引不一致。）

- **改动**：`EXPECTED_SHA` 改为 `git rev-parse HEAD:jobcopilot`（父仓库索引里的 gitlink），
  并在脚本里写明这个坑。

- **验证**：
  - 服务器（仓库钉 44c399c、子模块停在 2631ee3）：现在正确显示
    `期望 44c399c / 实际 2631ee3 → ❌ 不一致`，即「已前移但尚未部署」；
  - 开发机（两者都是 44c399c）：显示 `✅ 与 SEKB 钉住的 commit 完全一致`；
  - 这条自检在部署前用于确认「部署/运行的就是钉住的那份代码」，取值错了会让人误判
    成工具故障，所以值得单独修。

## 2026-09-15（JobCopilot P7：开源发布就绪（贡献指南 + CI 门禁 + 打包验证））

- **背景**：P7 要把内核做成可对外开源的项目：许可、贡献指南、CI 门禁、打包发布、
  eval 基线报告、三份接入文档。内核指针 `9bc394e → 44c399c`。

- **改动**：

  | 交付 | 位置 | 要点 |
  |---|---|---|
  | 贡献指南 | `CONTRIBUTING.md` | **把评估门禁写死**；说明为何 CI 只跑 L1+L2；「没有说明的基线更新一律不接受」 |
  | CI 门禁 | `.github/workflows/ci.yml` | lint + mypy + pytest；**eval-gate（L1+L2 与基线一致）**；打包冒烟 |
  | 打包 | `pyproject.toml` / `MANIFEST.in` | PEP 639 license 表达式、`project.urls`、版本 0.0.1 → **0.1.0**、sdist 带文档 |
  | 接入文档 | `docs/integrations/dsh.md`、`sekb.md` | 补齐 P7 要求的三份（第三份扣子在 P6 已交付） |
  | 评估报告 | `docs/eval-report.md` | 实测数据 + 复现方式 + **诚实的局限** |

- **验证（可客观复现）**：

  - `python -m build` 成功；**`twine check` 两个产物 PASSED**（wheel + sdist）；
  - 干净 venv 装 wheel 后实测：`jobcopilot 0.1.0`、`jobcopilot-mcp 0.1.0`、
    **`jobcopilot eval --level 12` 100% 且与基线一致**（这一步证明 package-data
    真的随包发布，是 `CONTRIBUTING.md` 里标注「不能省」的那步）；
  - 检查 wheel 内容：新模块 `mcp/request_keys.py`、16 份提示词、数据集与基线都在；
  - CI 的打包 job 会把上面两条固化成自动化检查；
  - 门禁：251 passed / 1 skipped、ruff 全过、mypy strict 33 文件无问题。

- **构建期踩到的两个坑（已写进代码注释）**：

  1. `[project.urls]` 放早了会把后面的 `dependencies` 吞进它自己的表里，报
     `project.urls.dependencies must be string`——TOML 表作用域持续到下一个表头；
  2. PEP 639 下用了 license 表达式后**必须删掉** `License :: OSI Approved :: ...`
     classifier，否则 setuptools 直接拒绝构建。

- **明确未做的部分**：**没有发布到 PyPI** —— 需要 PyPI 账号与 token（已列入疑问清单）。
  当前状态是「产物已就绪且校验证通过」，`twine upload dist/*` 即可发布。

- **未做的 DoD 项**：P7 要求的三份接入文档已齐（SEKB / DSH / 扣子），但**扣子那份的
  「真实连一次」仍缺账号**（与 P6 同一个阻塞项）。

## 2026-09-15（JobCopilot P6：云端平台适配（双传输 + BYOK + 五平台接入文档））

- **背景**：P6 要让 JobCopilot 能接到扣子 / 百炼 / 千帆 / HiAgent / Dify。计划里写的是
  「streamable HTTP + header 传 Key」，但**代码里只有环境变量传 Key**——模块文档早就写了
  「云端必须 BYOK」，实现却没跟上；且 HTTP 形态**完全没有鉴权**。

- **调研结论（决定了改造范围）**：五个平台的传输要求**互相冲突**，只做一条必挂一家——

  | 平台 | 传输 | 关键限制 |
  |---|---|---|
  | 扣子 | Streamable HTTP / SSE | **不接受 IP，必须公网域名**；内网仅企业旗舰版私网插件 |
  | 阿里百炼 | stdio / SSE / Streamable HTTP | `streamableHttp` 必须对应 `POST /mcp`；自签名证书报 `MCP_SSL_ERROR` |
  | 百度千帆 | 🔴 **仅 SSE** | 配置 JSON **没有 headers 字段** → 凭据只能放查询串 |
  | 火山 HiAgent | Streamable HTTP | 无公开文档（用官方 Go SDK 拿到字段级证据）；AgentKit 明确**不支持 SSE-only** |
  | Dify | HTTP（两种都行） | 必须关 DCR；默认超时 60 秒需调大；嵌套 object 在 OpenAPI 路线会退化成 STRING |

- **改动**（内核 `jobcopilot`，指针 `2631ee3 → 9bc394e`）：

  1. **按请求传 Key（BYOK）**：`X-JobCopilot-Api-Key`（+ 可选的 `Provider`/`Model` 覆盖头），
     带短哈希 LRU 缓存；服务端**未设**访问令牌时也接受 `Authorization: Bearer`（贴合两个
     平台 UI 的惯例——否则用户把 Key 填在那里会被静默忽略、改用服务端 Key 花钱）。
  2. **HTTP 安全闸**：非回环绑定**必须**设 `JOBCOPILOT_HTTP_TOKEN`，否则拒绝启动；
     设了令牌但没配 `JOBCOPILOT_HTTP_ALLOWED_HOSTS` 也拒绝启动（否则平台只会收到
     421，现场极难排障）。访问令牌支持 `?token=` 兜底（千帆没有 headers 字段）。
  3. **两条传输同一进程**：`POST /mcp`（Streamable HTTP）+ `GET /sse` + `POST /sse/messages/`。
     做法是合并两个子应用的 routes 并**组合 lifespan**——只合并 routes 会得到「连得上
     但没有会话」的服务，故障表现隐蔽，代码里专门留了注释。
  4. **平台兼容**：工具名匹配 `^[a-zA-Z0-9_-]{1,64}$`（有测试守着）；`analyze_jobs_batch`
     的 `jobs` 额外接受 **JSON 字符串**（平台无法声明嵌套数组），且参数路径用**严格 JSON**
     解析——原「像 JSON 但坏了就当纯文本 JD」的宽容逻辑对文件合理，对平台转发的参数
     会把「JSON 烂了」变成「分析了一段乱码 JD」，调用方察觉不到。

- **验证**：

  - **先实验再实现**：`docs/tmp/probe_dual_transport.py` 证实同一进程两条传输都可用
    （官方 sse_client 与 streamablehttp_client 各自列工具、调工具成功）；
  - 测试夹具改用**生产路径** `build_http_app`；新增 32 条 HTTP 用例，其中最关键的一条
    端到端证明 BYOK 生效：带 `X-JobCopilot-Api-Key` 调 `analyze_job` → 断言请求级 LLM
    被构造且 Key 就是调用方传的、**服务端默认 LLM 一次都没被调用**（并配了不带该头的对照组）；
  - 401（缺令牌 / 令牌错 / query 令牌错）、421（Host 不在白名单）、`/sse` 真的发出
    `event: endpoint`、工具名正则、GET 不 5xx 均有断言；
  - CLI 实测：对外绑定无令牌 → exit 2；有令牌无 Host 白名单 → exit 2；`/sse` 无令牌 401、
    带令牌收到 endpoint 事件；`POST /mcp?token=` 进到协议层；
  - 门禁：**251 passed / 1 skipped**、ruff check 全过、mypy strict 33 文件无问题；
    SEKB 侧 stdio 相关 35 个单测全过（stdio 路径行为不变）；
  - L1+L2 评估 100% 通过并与基线 `v1.json` 一致。

- **文档**：新增 `docs/integrations/`（README 对照表 + PRIVACY + DEPLOY-HTTP +
  coze/dify/bailian/qianfan/hiagent）。隐私提示按各平台条款差异写，不是套话——
  例如千帆虽承诺「不用于训练」，但其协议明确「**不得提供保密信息……我们没有保密义务**」，
  并把取得最终用户同意的责任压给开发者，这一点必须在接入文档里说清。

- **⚠️ 未完成的 DoD**：P6 的验收是「**每平台至少跑通一次完整分析**」。这需要各平台账号
  与一个**公网可达的 HTTPS 域名**，二者我都没有，因此**未能实测**。当前已具备的条件是
  代码与文档全部就绪（含正确的传输/鉴权形态），只差对外暴露与账号。已在疑问清单中列出。

## 2026-09-15（JobCopilot P5 上线：提示词自建分发源 + 内核版本可观测）

- **背景**：P5 的 DSH 接入此前已验收（工具注册 / 7 段分析 / 批量 `job_count=4`），
  但**部署一直被磁盘门禁挡住**（可用 24394MB < 25000MB）。本次把根因查清并上线。

- **改动**：本次为**上线动作**，代码改动只有 `scripts/backup_kb.sh`（见另一条碎片）。
  上线内容包括 P4/P5 的既有成果：

  | 能力 | 说明 |
  |---|---|
  | 内核版本可观测 | `GET /api/v1/health/` 返回 `kernel{version,commit,prompts,prompt_source,prompt_dir,healthy}` |
  | SEKB 走 MCP | `job.transport=mcp`，后端容器内以子进程方式拉起 `jobcopilot-mcp` |
  | 提示词自建分发源 | `https://bos-studio.tech/prompts/`（nginx `/prompts/` → `prompts-dist/`），带 `manifest.json` + 每文件 sha256 |

- **验证**（部署后实测）：

  - `kernel.commit = 2631ee386edf…` == SEKB 子模块钉住的 commit（**部署产物与仓库一致**）；
  - `healthy=true`、`prompts=12`、`prompt_source=local`（SEKB 的 `prompt/job` 热改目录按设计优先）；
  - 后端容器内存在 `jobcopilot-mcp` 子进程（MCP 链路确实在跑，不只是配置对）；
  - 自建主源 `manifest.json` HTTP 200，`version=6607d913a036`、3 packs / 15 files，
    **15 个文件 sha256 与 manifest 全部一致**；中文文件名可取（此前会 500）；
  - 前端容器已挂载 `/var/www/jobcopilot-prompts/`。

- **磁盘卡点的根因（两条，都值得记住）**：

  1. `docker system df` 的「可回收」在本机**是误报**：报 Images 可回收 10.71GB(64%)，
     但 `docker image prune -a -f` 实测只回收 **1.013MB**。原因是 Docker 已启用 containerd
     镜像存储，容器记录的 `.Image` 是平台 manifest digest，而 `docker images` 显示 index digest，
     两者不匹配导致 Docker 把 10.9G 的后端镜像当成「无人使用」。**12 个镜像实际全被引用。**
  2. 真凶是备份保留策略管不住**同一天的多份**（详见另一条碎片）：当天积了 9 份 ×112MB。
     清理后释放 1003MB，加 journal/apt 共把可用空间从 24394MB 抬到 **25609MB**。

  另实测出**构建真实峰值约 17.4GB**（最低可用降到 8208MB），印证 25000MB 门禁合理且偏保守。

- **踩坑/待办**：
  - 部署后验证报 `grafana: 000 FAIL` 是**监控栈刚重启的瞬时不可用**，随后即 `302`/healthy
    —— 验证阶段缺重试，已列入疑问清单；
  - 部署前检查有两处**假警告**（「定时备份 cron 未配置」「Git remote 未配置」），
    实际 cron 在 `bo` 的 crontab 里、remote 名为 `gitea`/`github`，检查逻辑与实际部署方式不匹配。

## 2026-09-15（招聘分析：搜索历史布局调整 + 历史报告去重 + 移除部分分析入口）

- **背景**：
  1. 「搜索历史」原放在主 Tabs 上方（全局），语义错位——它是「职位收集」的父选项；
  2. 历史报告只增不减（5 个搜索攒了 15 份批量报告），同一关键词重复分析各留一份；
  3. 「只分析部分职位」被判定为伪需求：它会让缓存报告 ≠ 职位列表，进而误报「报告已过期」。

- **改动**：
  - **布局**：搜索历史从主 Tabs 上方**移入「职位收集」tab 内**（采集筛选区上方）；「批量分析」tab 顶部新增「当前搜索：X（N 个职位）+ 切换搜索」信息行，点「切换」跳回职位收集。
  - **移除「部分分析」入口**：删掉「前往批量分析（N）」按钮，以及表格多选 / 跨页全选 / `selectedRowKeys` 相关逻辑；职位收集工具栏改为单一的「对全部 N 条做批量分析」。**报告因此永远与职位列表严格一致，不会再出现误报「报告已过期」**。
  - **历史报告去重**（`archive.py`）：批量报告按「用户 + 关键词 + 城市」**只保留最新一份**——`save_report` 写入后自动删除同组更旧报告（含 `.md`/`.json`）；新增 `dedupe_batch_reports()` 用于历史一次性清理（显式按 `created_at` 取最新，不依赖索引顺序）。单职位报告不参与去重。
  - **删除报告同步清缓存**：存档索引新增 `search_id`（`save_report(..., search_id=)`）；`DELETE /job/reports/{id}` 删除存档后**同步删除对应搜索的缓存报告**，该搜索回到「未分析」状态（搜索条目保留，可通过历史搜索重新分析恢复）。旧索引无 `search_id` 时按关键词回退匹配，并在删除前从报告 JSON 补齐 keyword/city。

- **验证**：后端 pytest **776 passed**（新增 `test_archive.py` 13 例：读写 / 批量去重 / 单职位不去重 / 跨用户隔离 / 删除返回元信息 / 旧索引 keyword 回退 / 一次性清理）；ruff 全绿、mypy 292≤310；前端 tsc 0 错、eslint 0 错误、vitest 66 passed。线上历史报告一次性清理：15 份批量报告 → 每关键词 1 份（5 份）。

- **⚠️ 过程中的一次误删与修复**：首次执行历史清理时，旧索引的批量报告条目**没有 `keyword`/`city` 字段**（该字段是本次新加），导致 15 份被全部归入同一组，只留下 1 份（预期按关键词留 5 份）。
  - **修复**：新增 `_backfill_entry_meta()`——条目缺 `keyword`/`city` 时从报告 JSON 补齐；`dedupe_batch_reports` 与 `_dedupe_batch` 都先回填再分组；并加安全护栏：**关键词为空且补不出来的条目一律不参与去重**（无法判定分组则宁可不删）。补 2 例回归测试。
  - **数据恢复**：报告本体仍在报告缓存（按 `search_id` 存），据此重建存档 → 恢复为 5 份；再跑清理删除 0 份（幂等）。
  - **另附验证**：删除存档报告后，`/job/searches` 对应搜索变为 `has_report=false`（搜索条目与职位数保留），再执行一次批量分析即恢复——即「删除报告 → 可在历史搜索中重新分析」的闭环。

## 2026-09-15（备份保留策略：同天去重（磁盘被同日备份挤爆导致部署卡住））

- **背景**：P5 部署卡在 `deploy.sh` 的构建前磁盘门禁上（可用 24394MB < 25000MB）。
  排查发现两个互相误导的现象：

  1. **Docker 的「可回收」是误报**：`docker system df` 报 Images 可回收 10.71GB(64%)，
     但 `docker image prune -a -f` 只回收 **1MB**。根因是 Docker 已启用 containerd
     镜像存储（`Storage Driver: overlayfs` + `driver-type: io.containerd.snapshotter.v1`），
     镜像实体在 `/var/lib/containerd`（18.9G 快照 + 4.9G blob），而容器引用的是
     **平台 manifest digest**、`docker images` 显示的是 **index digest**，两者不一致 →
     Docker 误判 10.9G 的后端镜像「无人使用」。12 个镜像实际全被容器引用，无可回收。
  2. **真正的浪费在备份目录**：`scripts/backup_kb.sh` 的原保留策略只有
     `find -mtime +14 -delete`（天数），**管不住同一天的多份**。而 `deploy.sh`
     每次部署前都会调本脚本备份一次 → 一天部署 N 次就留 N 份近乎相同的数据。
     2026-09-15 当天积了 **9 份 `sekb_data`**（每份 112MB），合计约 1.0G。

- **改动**（`scripts/backup_kb.sh`）：
  - 保留策略改为两条规则叠加：**(a) 超 `RETENTION_DAYS`(14) 天删**（原有）+
    **(b) 同一天只留最新一份**（新增，按文件名内嵌的 `%Y%m%d` 分组）。
  - 新增保底 `RETENTION_MIN_KEEP`（默认 2）：无论怎么去重，最新 2 份永远保留，
    避免「历史很短时被削到只剩 1 份」（单份损坏即无备份）。
  - 新增 `PRUNE_DRY_RUN=1`：只列出将删除哪些备份，一个都不删。
  - 新增 `--prune-only`：**只清理、不做备份**（磁盘紧张时用）。默认模式会先完整
    备份一次再清理，而备份要停 backend + 打包 112MB —— 只为腾空间时纯属多余。
  - `COMPOSE_DIR` / `BACKUP_DIR` 改为可用环境变量覆盖，便于本地端到端验证。
  - 清理结果改为打印「删除 N 份 / 可释放 X MB / 保留 N 份」，失败不再静默。
  - 只用 bash 3.2 就有的语法（macOS 自带 bash 3.2 无关联数组），
    这样开发机能直接跑测试脚本验证线上同一份代码。

- **验证**：
  - `docs/tmp/test_backup_retention.sh` 从**真实脚本抽取** `prune_backups` 函数体后运行，
    **34 项断言全部通过**（复刻服务器现状 / 幂等 / 超龄规则 / 同天保底 / 空目录 /
    保底可配置 / 干跑不删 / `set -euo pipefail` 下不早退 / CLI 参数 /
    `--prune-only` 端到端不产生新备份）。本地 bash 3.2 与服务器 bash 5 均通过。
  - 过程中测出并修掉两个真 bug：`sed` 未锚定导致 `day` 取成含时间的整串（**去重完全失效**）、
    `local -A` 关联数组在 bash 3.2 上直接报错。
  - 服务器实测：删除同日冗余备份 **9 份 / 释放 1003MB**，`0908~0915` 每天仍各留一份，
    备份目录 2038MB → 1035MB。
  - 部署脚本自带的备份调用随后验证：报告「无需清理（保留 9 份）」，幂等成立。

---

## 2026-09-15（JobCopilot P5：DSH 接入，L1 配置+文档）

内核查升到 `2631ee3`。交付物在 `jobcopilot-dsh-plugin` 仓库（**零 TypeScript**）。

**DoD 三项全部通过**（headless profile + 真 LLM 实测）
1. 工具注册：`mcp__jobcopilot__analyze_job` 等 **6 个工具全部出现**
2. 真实对话：贴 JD → **7 段全部非空**
3. 批量：`source_path` 读本地 jobs.json → `job_count=4`、market 5 字段、
   knowledge_iteration 6 字段，**16 秒**返回（`toolCallTimeoutMs=180000`）

### 🔴 最重要的发现：计划里的 DSH 配置写法**不会生效**

DSH 的 profile 补丁层是**「按 id 覆盖已有条目」**的语义（读 DSH 源码确认），
直接写 `- id: mcp-jobcopilot` 会被当成覆盖一个不存在的条目，日志只留一句
`patch: entry "mcp-jobcopilot" not found` 然后**静默跳过**——配置看着写对了，
插件完全不加载。**新增插件必须用 `insert:` 包裹**。文档与配置模板已按正确写法交付。

> **我差点被自己的验证脚本骗过去**：脚本第 3 步用
> `dsh --dump-config | grep -q "mcp-jobcopilot"` 判定「配置已生效」，
> 实际命中的是**那句 not found 警告本身**（也含条目名）→ 假阳性通过。
> 真正暴露问题的是第 4 步「让模型列出工具名」——模型说没有。
> 教训已记入计划文档：**配置类断言要断言目标对象真的存在，而不是输出里出现过这个名字**。

### 其他踩坑

- **DSH 版本不一致**：全局 npm 的 `dsh` 是 0.1.0-rc.7，运行中的 GUI 用本地 checkout
  0.1.5-rc.2；前者不认识当前凭据文件格式（`version must be a string`）而起不来，
  验证必须用本地 checkout 的 dsh。
- **profile 需先装包**：`@deepseek-ai/dsh-mcp-client` 不在默认 bundle 里，
  要先 `dsh plugin --profile <p> add ...`，否则条目解析不到、同样静默跳过。

### 顺带修掉的可用性问题（jobcopilot）

内核原先「没配 LLM Key 就启动即退出」，在 MCP 客户端里表现为**工具列表一片空白**——
用户完全看不出是缺 Key，而且连 `list_prompt_packs` 这类**不需要 LLM** 的工具也用不了。
改为：照常启动 + 调用需要 LLM 的工具时给可操作错误（并提前识别占位 LLM，
避免配置错误被「逐步降级」吞掉变成「7 段全空」）。

**验证**：jobcopilot 214 passed / ruff 全绿 / mypy strict 32 文件零错误 / eval 基线一致；
DSH headless 三项 DoD 实测通过；验证后已把 headless profile 补丁层还原为空数组。

---

## 2026-09-15（招聘分析：搜索历史 + 报告按搜索隔离 + 过期清理）

- **背景**：报告缓存原为「每用户一份」（`{user_id: report}`），导致「选中某次历史搜索看它的报告」无法实现；且删除职位后报告与列表不一致，刷新后批量分析 tab **静默不展示**（用户看不到原因）。
- **后端 · 搜索标识**：新增 `search_id = md5(user_id|keyword|city|min_salary_k)[:16]`（URL 安全、不含中文的稳定唯一标识，不面向用户展示）。
- **后端 · 职位缓存**（`job_cache.py`）：条目增加 `search_id` / `count`；新增
  - `list_searches(user_id)`：搜索历史列表（元数据 + `expired`/`has_jobs`，**不含 jobs**）+ **惰性清理**（超 14 天清空 `jobs`，但保留条目与 `count`，供前端打「已过期」角标）；
  - `get_by_search_id` / `expired_search_ids` / `make_search_id` / `parse_key`。
- **后端 · 报告缓存 v2**（`market.py`）：改为按 `search_id` 存（`{<sid>: {user_id, keyword, city, min_salary_k, report}}`），实现**一次搜索一份报告**；兼容读取 v1 旧格式（保留在 `__legacy` 槽，保存时不丢）；新增 `save_report_cache` / `delete_report_by_search_id`；`delete_report` 支持按 `search_id` 或整用户删除。
- **后端 · API**：
  - 新增 `GET /job/searches`（搜索历史，**三个状态标签由后端计算**：`expired` / `has_report` / `report_matched`，并在此时清理过期条目的报告）；
  - 新增 `GET /job/cache/search/{search_id}`（某次搜索的职位列表）；
  - `GET /job/batch-analyze/cached?search_id=` 按搜索取报告（不传 = 最新一份，兼容旧行为）；
  - `POST /job/batch-analyze` 支持 `search_id` / `min_salary_k`；`DELETE /job/batch-analyze?search_id=` 支持只删该搜索的报告。
- **前端**：
  - **Tabs 上方新增「搜索历史」区**（作为「当前搜索」全局上下文）：展示 `关键词 · 职位数 · 状态角标`（已过期 / 有报告 / 报告待更新 / 未分析）；
  - 选中历史搜索 → 回填筛选条件 + 载入该搜索的职位列表与报告；
  - **批量分析三态**：① 报告与列表严格一致 → 正常展示；② 有报告但不一致 → 「报告已过期」+ 重新分析按钮；③ 无报告 → 「当前职位列表还未做过批量分析」+ 马上分析按钮；④ 过期搜索 → 「该搜索已过期」提示；
  - 删除职位后刷新搜索历史并清空会话内报告（交由三态判断）；新采集自动成为「当前搜索」。
- **数据迁移**：一次性回填——把历史存档里各关键词最新一份批量报告按 `search_id` 写入新报告缓存。线上 5 个搜索（第一层·解决方案售前 208 / 技术型产品 52 / 解决方案·售前 47 / Agent 88 / Agent开发 74）**全部回填成功**，`has_report` 与 `report_matched` 均为 True。
- **测试**：`test_job_cache.py` 新增 18 例（search_id / 搜索历史 / 惰性清理 / 按 id 取 / 过期 id）；新增 `test_market_report_cache.py` 11 例（v2 读写 / 最新一份 / TTL / v1 兼容 / 删除）。
- 验证：后端 pytest **763 passed**、ruff 全绿、mypy 292≤310；前端 tsc 0 错、eslint 0 错误、vitest 66 passed；已部署（版本 `80a0f46`）并线上验证三个接口 + 一致性判定四种情形（完整一致 True / 少一个 False / 多一个 False / 无报告 False）。

---

## 2026-09-15（JobCopilot P4：SEKB 分析链路切到 MCP）

内核查升到 `6c5f7cd`。**这是第一个动到 SEKB 生产分析链路的阶段。**

**改动**：采集链路不变；分析链路从「直接 import 内核」改为
`MCPClient → stdio → jobcopilot-mcp`，SEKB 成为内核的**客户端**而非调用方。
`transport: direct` 保留为**紧急回滚开关**（改一行配置 + 重启即可退回旧路径）。

**新增 `app/agents/job/mcp_client.py`**：常驻 stdio 客户端
- **共享连接**：单职位与批量分析共用一条子进程（不各起一个）
- **命令解析**：PATH 找不到时回落到当前解释器同目录（venv 场景常见）
- **启动期预热**：MCPClient 内部用 anyio `AsyncExitStack`，要求进入/退出同一 task；
  懒建连会变成「请求 task 建、lifespan task 关」并触发告警，故放在启动任务里建连

**DoD 3：错误必须清晰**：子进程起不来 / 超时 / 内核返回结构化错误 → 一律转成带
排查方向的 `KernelMCPError`，不让路由抛 500 堆栈。

**补上两个切 MCP 后必然出现的缺口**
1. **多用户画像**：内核的 `save_profile` 是**进程级全局状态**，而 SEKB 是多用户系统——
   没有按请求注入画像的能力，就会把 A 的画像用到 B 的分析上。两个分析工具都加了
   `user_profile` 参数。
2. **费用可见性**：走 MCP 后是**内核自己调 LLM**，SEKB 的 LLMFactory 看不到这些调用，
   精心做的 token/费用统计会直接漏掉招聘分析。现在内核回报**本次调用**的增量用量，
   SEKB 落到结构化日志（Loki 可检索）。

**修掉一个很隐蔽的真 bug**：`JOBCOPILOT_PROMPTS_DIR` 文档里写了读、**代码里从未读**。
后果是宿主以为自己把 `prompt/job` 传给了内核，实际内核用的是包内 base——
**现网提示词热改（bind mount）静默失效**。已修 + 补测试。

**验证**
- SEKB 721 → **739 passed**（+18 MCP 接线测试，真实拉起子进程走 stdio 协议）；
  ruff 全绿；mypy 门禁 302 ≤ 310；P0 逐字段比对仍全等
- jobcopilot 213 passed / ruff / mypy strict 全绿
- **部署前真 LLM 验证**（生产容器内）：单职位 7 段全非空 21s、批量 5+6 字段 8.4s、
  **提示词生效来源 `{'local'}`**（SEKB 的 prompt/job 真被采用）
- 子进程泄漏检查：3 轮建连/断连后残留 0

---

## 2026-09-15（JobCopilot P3：提示词分发端点 + 三级回退）

内核查升到 `a24c7af`。SEKB 侧新增 nginx `/prompts/` 静态分发端点。

**端点**：`https://bos-studio.tech/prompts/manifest.json` 可访问（证书正常）；
`autoindex off`（不列目录）、`access_log off`（少一份可用于画像的访问记录）、
一律按 `text/plain` 返回。内容由 `deploy.sh` 用**一次性容器**跑
`jobcopilot publish` 生成到 `prompts-dist/`，宿主机无需装 Python 依赖。

**消费端三级回退**：自建主源 → jsDelivr CDN / GitHub raw → 包内兜底。
实测国内到 `raw.githubusercontent.com` **间歇性读写超时**（同一小时内既有 0.59s 成功、
也有读超时），故把 jsDelivr 排在 raw 之前；代价是 jsDelivr 对 `@main` 有缓存
（可能滞后数小时），但 manifest 与文件来自同一份缓存，内部始终自洽。

**完整性校验**：manifest 带每个文件的 sha256，不一致即**中止同步**——
提示词会被注入 LLM，被篡改的后果比下载失败严重得多。

**发布闸门**：`jobcopilot publish` 会**拒绝**任何破坏 base JSON 输出骨架的 pack。
成因值得记：章节块的边界是「到下一个标题为止」，pack 若覆盖**最后一个标题**，
它后面的非标题内容（很可能正是 JSON 输出骨架）会被整块替换——组合结果看着正常，
实际已丢掉输出契约。发布是所有消费方上游，在这里拦住代价最小。
`version` 由内容决定，发布幂等。

**修掉的问题**
1. **中文文件名未做 URL 编码**：真实 HTTPS 下 urllib 抛
   `UnicodeEncodeError: 'ascii' codec can't encode`。`file://` 不走那条路径，
   本地单测**发现不了**——已补专门断言。
2. **发布脚本容器权限**：镜像默认以 appuser(uid 1000) 运行，挂载出的目录属部署用户
   → `PermissionError`。改为 `--user "$(id -u):$(id -g)"`。
3. macOS 常缺 CA 导致 `CERTIFICATE_VERIFY_FAILED` → 优先用 certifi 的 CA 包。
4. 重构时把「本地目录留 manifest」写丢了 → 补回（记录来源与版本，排障用）。

**验证**：jobcopilot 206 passed / ruff 全绿 / mypy strict 32 文件零错误；
SEKB 721 passed 无回归。DoD 三项：端点可访问 ✅、主源挂→备源 ✅、全挂→包内 ✅。

---

## 2026-09-15（JobCopilot P2：MCP Server + CLI run）

内核查升到 `3b441c3`（子模块指针同步）。**SEKB 侧未接入 MCP（P4 才切），行为完全不变。**

**MCP 工具集（6 个）**：`analyze_job` / `analyze_jobs_batch` / `get_profile` /
`save_profile` / `list_prompt_packs` / `sync_prompts`。双传输：
`jobcopilot-mcp`（stdio，给 DSH / Claude Desktop / Cursor）与
`jobcopilot-mcp --http`（streamable HTTP，给云端平台）。

**本轮最重要的发现：API 边界不能静默失败。** 内核「每步独立降级」在引擎里是对的，
但在 MCP 边界上，LLM 全挂时会返回 **7 个空段落**——调用方看起来像「调用成功但没内容」，
完全看不出是 Key 失效 / 余额不足 / 网络不通。已改为：全失败 → 结构化错误（含排查方向）；
部分失败 → 结果照常返回但带 `warnings` 标出不可信段落。这正是 P4 DoD 3 要防的模式。

**安全默认**：HTTP 形态下默认**禁用 `source_path`**。该参数让服务端读本地文件
（88 个职位当参数传会烧大量 token），但 HTTP 面向「别人的服务器 + 多用户」，
开放任意路径读取等于暴露宿主机文件系统。确需启用要显式开，并可用
`JOBCOPILOT_SOURCE_ROOT` 限定目录。

**报错可排障**：实测 OpenAI 不可达时错误信息是「OpenAI 调用失败: 」（冒号后空白，
因 httpx 网络异常的 `str()` 为空），完全无从下手。已带上异常类型与兜底说明。

**验证**：jobcopilot **177 passed**（+34）/ ruff 全绿 / mypy strict 30 文件零错误。
- **协议级**：官方 MCP 客户端真实拉起子进程，走完 initialize → list_tools（6 个工具）→
  call_tool；HTTP 形态同样端到端覆盖，并断言 `source_path` 被拒。
- **DoD 3 真 LLM 超时验证**（生产容器内、stdio 全链路）：50 职位 **12.5s** 返回
  （限值 180s，余量 14×），market 5 字段 / knowledge 6 字段全非空。
- **DoD 4 多 provider**：DeepSeek 真实调用成功；千问 / Kimi / 豆包 / 智谱**端点正确且返回干净 401**；
  OpenAI 国内网络不可达（ConnectTimeout，非代码问题）。

---

## 2026-09-14（运维：备份/恢复对称化 + 首次恢复演练通过 —— G1）

审查发现的 **G1（备份/恢复不对称）** 是本轮唯一评为「高」的问题，已修复并**实测验证**。

**问题**：`backup_kb.sh` 早已统一为「一个脚本备份双卷」，而 `restore_kb.sh`（53 行）按设计
**只恢复 `sekb_data`**（`VOLUME_NAME="sekb_data"`），**完全不认 `sekb_gitea_data_*.tar.gz`**——
Gitea 的恢复只有一段手工命令、**且从未演练**。备份已覆盖的权威源码，实际处于
「有备份、无法证明确实能恢复」的状态。

**改造 `scripts/restore_kb.sh`**（与备份脚本对称）：

- 支持**双卷**：`--sekb` / `--gitea` / `--both`，也可按文件名**自动识别**卷类型；
- `--both` **先做成对预检**（两个文件都存在且可解包）再动手，杜绝「恢复一半」的半成品状态；
- 解包前 `tar tzf` **完整性校验**，避免清空卷后才发现备份损坏（顺带落地 OPS-12 的诉求）；
- **目标卷必须已存在**才恢复——否则 `docker run -v` 会静默创建空卷，把失败伪装成「恢复成功」；
- `trap EXIT` 改为**只兜底重启本次真正停掉的服务**（旧版硬编码只认 backend）；
- 清卷补 `.[!.]*` / `/data/..?*`，不再漏隐藏文件；
- 新增 **`--drill` 旁路卷演练模式**：只往旁路卷回灌，**不停止/重启任何线上服务**，
  并带**安全联锁**——`--drill` 一旦发现目标是生产卷即拒绝退出。这让恢复演练可反复进行。

**首次双卷恢复演练（2026-09-14，备份时间戳 `20260914_182934`）——✅ 通过**：

- 旁路卷回灌 + 起临时 Gitea（仅绑 `127.0.0.1:3300`）→ `healthz` OK；
- 用**生产 token** 查到 **4 个仓库**、`private` 标记正确 ⇒ 凭据与仓库元数据完整恢复；
- Push Mirror 配置（`remote_address` + `interval: 8h0m0s`）同在 ⇒ 恢复后镜像可继续工作；
- 从旁路实例 `git ls-remote` 成功（`main` → `6f44ad0`，即 18:29 快照时点）⇒ git 对象完整；
- `sekb_data` 旁路卷 23 个顶层条目齐全（`chroma_db` 93.6M / `conversations` / `uploads` / `news` …）；
- **演练全程生产容器 uptime 未变**（backend / gitea 始终 healthy），旁路与生产 `gitea.db` md5 不同
  ⇒ 确实未触碰线上；演练资源（临时容器 + 两个旁路卷）已全部清理，无残留。

**文档**：`12-GITEA.md` §6.2 重写为统一恢复流程 + §6.3 新增演练手册与**演练记录表**；
`13-DISK-MEMORY.md` 关联段同步；`BACKLOG.md` G1 标记已解决。

> 其余发现（G2 镜像无告警 / G3 跟踪分支 / G4 cron 脚本漂移 / G5 端口绑定 / G6 错配 remote）
> 按用户选择**暂不动服务器**，保留在 BACKLOG 待排期。

---

## 2026-09-14（运维：Gitea 链路实现审查 + 文档纠偏）

对用户已实现的自建 Gitea 链路做代码/服务器实测审查。**结论：功能主体正常**——四仓库
Push Mirror 地址正确且最近同步全部成功、`sekb` 为 private、`backup_kb.sh` 已覆盖双卷、
公网端口实测不可达。以下为发现的**缺口与错配**（已登记 `BACKLOG.md` G1–G6）：

- **G1（高）备份/恢复不对称**：`restore_kb.sh`（53 行）按设计**只恢复 `sekb_data`**
  （`VOLUME_NAME="sekb_data"`），**不认 `sekb_gitea_data_*.tar.gz`**；Gitea 恢复只有
  `12-GITEA.md` §6.2 的手工步骤，且**无演练记录**。备份已统一为单脚本双卷，恢复却割裂
  ⇒ 灾难恢复易「恢复了知识库、丢了源码仓库」。
- **G2 镜像静默停摆无告警**：失败仅写 Gitea `last_error`，无指标/告警规则/定时巡检；
  最危险是 **GitHub PAT 过期**后永久失败而无人知。`gitea_mirror.py status` 本可用退出码判定，
  但无人定时执行。
  （**注**：审查中顺带**实测闭环成功**——本次 `push gitea` 后 Gitea 经 `sync_on_commit`
  **自动**镜像到 GitHub，三点 SHA 一致（`dee8b47`），`last_update` 由 18:55 自动推进到 19:27，
  无需人工 `sync`。故 G2 是**纯可观测性缺口**，镜像机制本身工作正常。）
- **G3 本地跟踪分支错**：Mac 上 `main` 跟踪 `origin/main`（**GitHub 归档镜像**）而非权威源
  `gitea/main` ⇒ 裸敲 `git push`/`pull` 会打到 GitHub；`git status` 的「领先 139」为
  `origin/main` 长期不 fetch 的**陈旧计数**（已在文档注明并给出 `git branch -u gitea/main`）。
  （本地 remotes 本身与 §4.1 文档**完全一致**，无误。）
- **G4 cron 脚本漂移**：cron 执行 `/opt/self-evolving-kb/backup_kb.sh`，仓库版在
  `.../SelfEvolvingKnowledgeBase/scripts/backup_kb.sh`——**是两个文件**且仓库内无任何同步逻辑
  （实测逐字节一致属手工巧合，`deploy.sh` 只做相对调用）。
- **G5 端口绑 `0.0.0.0` + 部分仓库 public**：当前公网不可达**依赖 `EnableUserlandProxy: true`**
  （使流量走 INPUT 链被 ufw `DROP`）；若改 `--userland-proxy=false` 则转 FORWARD 链被 Docker
  直插 `ACCEPT` **绕过 ufw** 即暴露。另 `jobcopilot*` 三仓为 public（`sekb` 为 private ✅）。
- **G6 服务器残留错配 remote `github`**：指向 `http://localhost:3000/bo/SelfEvolvingKnowledgeBase.git`，
  名为 `github` 实则指向本地 Gitea，且该仓库**不存在**（带 token 的认证 API 返回 `404`，
  真实名是 `sekb`）——正是文档「坑 2」描述的静默失效模式。

**文档调整**：

- **修正 ops 编号冲突**：`11-CHANGE-RELEASE-POLICY.md` 与 `11-MONITORING.md` **同时占用 11**
  （`00-README.md` 索引里两行都写 `| 11 |`）。已将较新建的变更发布规约移至
  **`14-CHANGE-RELEASE-POLICY.md`**（保留已建立的 `11-MONITORING`，只改 2 处引用，避免牵连
  `13-DISK-MEMORY.md`）。
- `12-GITEA.md`：补 §4.1 跟踪分支告警、§4.2 服务器 `github` remote 错配说明、§5「镜像静默
  停摆无告警」缺口、§6.1 cron 脚本漂移风险、§6.2 恢复清卷补 `.[!.]*`＋恢复不对称警告＋
  「尚无演练记录」、§7 排障表补「静默停摆」「推到错误 remote」两行。
- `13-DISK-MEMORY.md`：关联段标注备份脚本**双文件无同步**、恢复脚本**不含 Gitea 卷**。

> 审查方法：Gitea 认证 API（`/user/repos`、`/push_mirrors`）＋ `docker logs` ＋ 卷/权限实测；
> 公网可达性用 Mac 侧 `curl` 直连 `49.232.42.91:3000/2222/8000` 验证（均 `HTTP 000`）。

---

## 2026-09-15（JobCopilot P1：提示词职能分层 + Eval 骨架）

内核查升到 `340144a`（子模块指针同步更新）。**SEKB 侧行为不变**——SEKB 仍用自己的
`prompt/job`（本地目录优先级最高），且包内 base 提示词一字未改。

**提示词 pack（只写差异章节）**
- 新增章节级合并：pack 里写到的标题覆盖 base 同名标题，其余原样继承，
  **JSON 输出骨架永远来自 base**（防 schema 漂移）；匹配按标题序号，pack 可自由改写文案；
- 内置三个职能 pack：`presales`（客户/云厂商赛道 + 年包口径）、`product`（产品线赛道 +
  端云协同/评测体系 + 车载标注）、`engineering`（技术职能 + 框架源码深度）；
- `jobcopilot pull` 把**合并后的完整提示词**拉到本地目录（可直接改、立即生效）。

**Eval 骨架（三层）**
- L1 程序化断言（零 LLM）：结构 / job_count / **stats 精确比对** / 段落非空 /
  **输出与提示词声明的 JSON 骨架一致**；
- L2 基线回归（零 LLM）：**提示词指纹** + 指标不得低于基线；
- L3 LLM-as-Judge：覆盖度 / 如实性 / 可执行性 / 赛道 / 薪资依据；
- 黄金数据集：3 组批量（研发 8 / 售前 6 / 产品 6）+ 3 条单职位用例；
- **守门员方案 B**：CI 只跑 L1+L2（无需 Key、零成本）。L2 的提示词指纹把
  「改了提示词就必须重新基线化」变成零成本强制。

**CLI**：`jobcopilot pull / pack / eval / doctor`。

**可追溯性**：批量报告新增 `prompt_meta`（pack / 指纹 / 覆盖章节），
可从缓存或存档报告反查提示词版本。

**修掉两个真 bug**
1. **伪 JSON 骨架导致断言假绿**：提示词骨架用裸词占位（`"job_count": 招聘量`），
   严格 `json.loads` 必然失败 → 两条最重要的批量提示词**静默跳过校验**；
2. **`to_dict` 返回内部 dict 引用**：调用方一改就污染报告本身，使差异比对静默失效。

**验证**：jobcopilot 143 passed / ruff 全绿 / mypy strict 零错误（26 文件）；
SEKB 721 passed / ruff 全绿 / mypy 门禁 300 ≤ 310；**P0 逐字段回归比对仍全等**。

---

## 2026-09-14（运维：GitHub token 轮换 + 镜像同步跑通）

- **Token 轮换**：旧 PAT 实测已失效（`curl /user` 返回 `Bad credentials` ✅）；
  新 PAT 写入 `/home/bo/.github-token`（600）。
  **顺带发现旧 token 还残留在 `/home/bo/.git-credentials` 的 github.com 行**——已一并更新
  （只换这一行，Gitea 凭据不动）。
- **四个推送镜像全部重建并同步成功**：`sekb` / `jobcopilot` 与 Gitea 逐字节一致。
- **新增 `scripts/gitea_mirror.py`**（`status` / `sync` / `rebuild`），把本轮两个坑固化下来：
  1. **触发同步的端点不是 `mirror-sync`** —— 那是给拉取镜像用的，对推送镜像返回
     `400 Repository is not a mirror`（**既不代表成功也不代表失败**）。正确的是
     `push_mirrors-sync`。上一轮我据此误判过「已触发同步」，实际那次是 push 提交时
     `sync_on_commit` 生效的；
  2. **Gitea 仓库名 ≠ GitHub 仓库名** —— Gitea 侧 `sekb`、GitHub 侧 `SelfEvolvingKnowledgeBase`。
     我按 Gitea 名拼出了 `https://github.com/bozhang1214/sekb.git`（**不存在的仓库，且不报错**），
     差点让 SEKB 镜像静默失效；现已改成显式映射表。
- `12-GITEA.md` 补：仓库名映射表、token 轮换流程（含「旧 token 必须验证 Bad credentials」）、
  `scripts/gitea_mirror.py` 用法、凭据位置补 `.gitea-token`。

---

## 2026-09-14（工程：内核子模块状态在四个时机全部显性化）

**要解决的问题**：子模块最大的风险是**静默过期**——`git pull` 完 SEKB 子模块纹丝不动、
改完内核忘了回 SEKB 提交指针、新机器不知道还要拉子模块，**全都不报错**。

- **0）`.gitmodules` 改用公开 GitHub 地址**（原为 Tailscale 私网 `ssh://git@100.71.24.105:2222/...`）。
  这意味着**任何人从 GitHub clone SEKB 都拉不到子模块**——比「不知道是不是最新」更严重。
  本机/服务器用**全局 URL 重写**走自托管 Gitea，不动 `.gitmodules`（避免弄脏跟踪文件）。
- **1）首次接触**：新增 `scripts/check_kernel.sh`，一条命令看清
  是否初始化 / commit 是否与 SEKB 钉住的一致 / 工作区是否干净 / 提示词是否完整 /
  能否被 Python 导入，异常时给出**可直接复制**的修复命令。`--strict` 让警告也致命，`--quiet` 供 CI。
- **2）部署前硬闸门**：`deploy.sh` 从「目录存在」升级为 `check_kernel.sh --strict`——
  未初始化 / commit 不符 / 工作区脏 → **直接中止**，不再「静默部署非钉住版本的内核」；
  紧急绕过 `SKIP_KERNEL_CHECK=1`。`pre-deploy-check.sh` 接入同一脚本，标准一致。
- **3）运行时可见**：内核 commit 在构建期烧进镜像（build arg → ENV），
  容器启动日志打印，并在 `GET /api/v1/health/` 新增 `kernel` 字段
  （`version` / `commit` / `prompts` / `prompt_source` / `prompt_dir` / `healthy`）。
  线上一条命令即可核对：`git submodule status` 的 commit 与 `/health` 的 `kernel.commit` 一致 ⇒ 跑的就是钉住的那份。
- **4）CI**：给需要后端的 5 个 job 补 `submodules: true`
  （**此前我引入子模块后 CI 必挂**），`lint` job 前置 `check_kernel.sh --strict` 闸门。

**踩到的两个真坑**（都写进了代码注释）：
1. BSD（macOS）的 `tr -d '+-U'` 会把 `+-U` 当成 **ASCII 区间**（`+`=43 到 `U`=85，**含全部数字**），
   结果把 commit SHA 里的数字全删光（`a4b1885f...` → `abfcdfacdebaac`）→ 改用 `sed` 精确去首字符；
2. `pre-deploy-check.sh` 是 `set -euo pipefail`，写成 `VAR="$(失败命令)"` 再判 `$?` 会让脚本
   **直接退出**——门禁失败反而变成静默中断整个预检 → 必须写成 `if VAR="$(cmd)"; then`。

**顺带发现（未修，另案）**：GitHub Actions **从 2026-08-20 起 0/30 全部失败，且每次运行
job 数都是 0**（工作流 active、YAML 合法、Actions 已启用），与本次改动无关，是又一个
「一直红着但没人追」的问题。

- 验证：SEKB 715 → **721 passed**；ruff 全绿；mypy 300 ≤ 基线 310；
  `deploy.sh` 完整跑通（EXIT=0，含新的内核硬闸门与部署前备份）；
  线上 `/health` 报 `commit=a4b1885...`，与 `git submodule status` 钉住的完全一致。

---

## 2026-09-14（修复：部署前数据备份从未真正执行 + 预检永久失败）

第一次跑完整 `deploy.sh` 时暴露两个「一直红着但没人追」的问题：

- **部署前数据备份从未真正执行**（日志只写「数据备份失败（非致命）」）：
  1. `deploy.sh` 把备份日志重定向到 `/var/log/sekb-deploy-backup.log`，而部署用户 `bo`
     对 `/var/log` **无写权限** → 重定向即失败，脚本根本没跑起来；
  2. `deploy/backup.sh` 写死 `/backup` 目录，`bo` 同样无权限
     （实测 `mkdir: cannot create directory '/backup/…': Permission denied`）；
  3. 该脚本还把 `.env.prod` **明文打进备份并 sync 到 S3**（安全审查 SEC-09），
     且**不覆盖 `sekb_gitea_data`**（RFC D-02 起是版本管理权威源）。
  - **修复**：改用维护中的 `scripts/backup_kb.sh`（覆盖 `sekb_data` + `sekb_gitea_data`，
    带 `EXIT trap` 兜底重启服务），日志落到 gitignore 的 `logs/deploy-backup.log`；
    `deploy/backup.sh` 标记废弃并写明三条原因。实测：`EXIT=0`，产出 112M + 2.2M 两份备份。
- **部署前检查第 6 节永久失败**：`prom/alertmanager` 镜像的 ENTRYPOINT 是 `/bin/alertmanager`，
  所以 `docker run prom/alertmanager:latest amtool check-config …` 会把 `amtool` 当成
  alertmanager 的参数，报 `unexpected amtool, try --help` → 整个预检不通过。
  - **修复**：加 `--entrypoint amtool`。实测 `SUCCESS`（global config / route / 1 inhibit rules / 3 receivers）。
- **结果**：`deploy.sh` 首次完整跑通（`EXIT=0`），七阶段全绿；构建阶段正确打印内核查 commit
  （`a4b1885 version = "0.0.1"`），阶段 7 的缓存上限也确认生效。

---

## 2026-09-14（工程：jobcopilot 转为 git 子模块 + GitHub 镜像打通）

- **P-1 阻塞全部解除**：
  - GitHub 邮箱验证完成后，SEKB 推送镜像一次补齐落后的 **119 个 commit**（GitHub 上已是 `db6f1f9`）；
  - JobCopilot 三个 GitHub 仓库创建成功（`HTTP 201`），四个仓库的 push mirror 全部 ✅ 正常。
  - **踩坑**：在邮箱验证**之前**创建的 mirror 配置会持续报 `OpenSSL SSL_read: unexpected eof`
    （网络实测正常、`git ls-remote` 也通）。**删除镜像配置后重建即恢复** —— 配置早于前置条件的残留，重建比排查快。
- **jobcopilot 从「gitignore 的独立检出」改为 git 子模块**：
  - 原状态别扭：物理上在仓库内却被 gitignore，还带自己的 `.git`；新机器 clone 完 SEKB 直接构建会失败，
    必须有人口头告诉你"还要再 clone 一个仓库到根目录"——**隐藏知识**。
  - 改为子模块后：SEKB 记录内核的**固定 commit**（此前服务器 `pull` 会拿到 main 上任意版本，部署不可复现）、
    新机器 `git clone --recurse-submodules` 一次到位、构建零改动（子模块就是普通目录）。
  - 服务器用**全局 URL 重写**走 HTTP（不写进 `.gitmodules`，避免弄脏跟踪文件）：
    `git config --global url."http://localhost:3000/".insteadOf "ssh://git@100.71.24.105:2222/"`
  - `deploy.sh` 缺内核时的提示改为 `git submodule update --init`，并在构建前打印子模块实际 commit 便于溯源。
- **代价（已知并接受）**：每次改内核后必须回 SEKB 提交一次子模块指针，忘了会部署到旧内核
  （deploy.sh 会打印实际 commit 便于发现）；P4 切 MCP 后本耦合消失，`git submodule deinit` 一行移除。
- 验证：SEKB 715 passed、jobcopilot 78 passed、`docker compose config` 正常解析命名上下文、服务器完整部署跑通。

---

## 2026-09-14（重构：招聘分析内核抽到 jobcopilot 独立包 · P0）

- **背景**：招聘助手要作为独立产品发布，先做「抽内核」——把分析能力从 SEKB 里剥出来，SEKB 改为**直接依赖**该包。
- **新仓库 `jobcopilot`**（Gitea 主 + GitHub 镜像）：零宿主耦合、**零第三方依赖**的 Python 包。
  - `analyzers/single`：单职位 7 步流水线（深度分析 → 知识优先级/差距分析 → 面试Q&A/简历建议/项目迭代/求职策略），每步独立降级；
  - `analyzers/batch`：批量分析（市场行情 + 职位知识迭代，两路并行）；
  - `analyzers/apply_plan`：投递计划 + 大厂冷冻期计算；
  - `stats`：程序化统计（公司/方向/热点关键词），Eval L1 层可零成本断言；
  - `prompts`：多级回退（请求级 override → 宿主本地目录 → packs/职能族 → base）；
  - `providers`：OpenAI 兼容客户端 + DeepSeek/千问/Kimi/豆包/智谱 预设，**BYOK**（Key 只走环境变量）。
- **SEKB 侧保留**：爬虫（collector/sources/fetcher）、缓存（job_cache/analysis_cache）、历史存档（archive）、用户画像与存储接线（profile）。
- **新增适配层** `app/agents/job/llm_adapter.py`：中性 Message ↔ LangChain 消息互转，SEKB 的模型路由/计费/重试/降级**完全不变**。
- **可观测性**：内核默认用标准库 logging 会绕过 SEKB 的脱敏处理器（日志含 LLM 原文片段），故新增 `set_logger_factory` 注入点，把内核日志接进 structlog 管道。
- **隐私**：`prompt/job/README.md` 含真实姓名/公司/年龄/薪资，**刻意不进开源包**；加了两道护栏（文件名黑名单 + 逐字节来源比对）。
- **构建**：`backend/Dockerfile` 通过命名构建上下文 `COPY --from=jobcopilot` 安装内核包（`docker-compose.prod.yml` 的 `additional_contexts`），`deploy.sh` 增加内核查检出前置检查。
- **验证**：SEKB **704 → 715 passed**（原 704 一条不差）；ruff 全绿；mypy 门禁 300 ≤ 基线 310（类型债降 10）；jobcopilot 自身 78 例 + ruff + mypy strict 全绿。
- **回归比对**：`docs/tmp/p0_regression_check.py` 用同一个假 LLM 驱动「git HEAD 旧实现」与「新实现」，比对发给 LLM 的调用序列（含消息类型与正文）、7 段结构化输出、批量报告、降级路径 —— **全部完全一致**。

---

## 2026-09-14（运维：服务器磁盘回收 19.5G + 部署脚本加固）

- **背景**：`deploy.sh` 反复报「磁盘需 ≥20G 空闲」预检不过，排查发现真凶是 Docker 构建缓存。
- **实测回收**：
  - `docker builder prune -af`：构建缓存 `20.2G → 1.16G`，**回收 19.05G**；磁盘 `39G/69% → 25G/43%`（可用 18G→33G）；
  - journal 限容（`SystemMaxUse=200M` / `SystemKeepFree=1G` / `MaxRetentionSec=2week`）+ `apt-get clean`：`/var/log` `378M → 146M`。
- **内存结论（反直觉，已固化为判读方法）**：内存**从未紧张** —— PSI `memory some avg10=0.03`、`vmstat si/so=0/0`、available 1.6G；swap 里 649M 是 rsshub / playwright / dockerd 的冷页，属正常。**今后先看 `docker system df`，不要先看 `free`**。
- **内存小优化**：停用云主机上的无用常驻服务 `fwupd`（+ `fwupd-refresh.timer`，static 需 mask）与 `multipathd`（实测 `/dev/mapper/` 仅 control，无多路径设备），释放约 57M，PSI 归零。
- **根因加固**：`deploy/deploy.sh` 阶段 7 新增 `docker builder prune -af --max-used-space 2GB`（保留 2G 热缓存，旧版 Docker 自动退化为整体清空），防止缓存再次无限增长。
- **新增文档**：`docs/ops/13-DISK-MEMORY.md`（体检三命令 / 清理清单 / PSI 判读 / 加固说明 / 一键巡检脚本），索引登记第 13 行。
- **遗留隐患（待确认）**：7 份备份仍在**同一块磁盘**上，`backup_kb.sh` 无任何 cos/oss/rclone/rsync 上传逻辑 —— 磁盘损坏即数据+备份同时丢失，建议接入腾讯云 COS 做异地副本。

---

## 2026-09-14（基础设施：自托管 Gitea 版本管理底座 + 发布链路切换）

- **背景**：GitHub 在国内访问不稳、且 JobCopilot 独立仓库需要权威源，按 RFC D-02/D-08/D-09 自建 Gitea 作为版本管理底座。
- **服务**（`docker-compose.monitoring.yml`）：新增 `gitea` 服务（`gitea/gitea:1.27.3`，SQLite 单机，512M/0.5cpu，健康检查 `/api/healthz`），端口 `3000:3000` / `2222:22`，独立网络 `sekb_gitea_net` 与数据卷 `sekb_gitea_data`；关闭注册（`DISABLE_REGISTRATION=true`）、锁安装（`INSTALL_LOCK=true`）。
- **仓库**：`bo/sekb`（私有，SEKB 权威源）、`bo/jobcopilot` / `bo/jobcopilot-prompts` / `bo/jobcopilot-dsh-plugin`（公开，JobCopilot 三仓）。
- **发布链路切换**：原「本地 `git bundle` → `scp` → 服务器 `fetch`+`merge`」**已废弃**，改为 `Mac: git push gitea main` → `服务器: git pull`（服务器 `main` 已 track `gitea/main`）；远端 URL 一律不带凭据。
- **备份**：`scripts/backup_kb.sh` 纳入 `sekb_gitea_data`（版本管理权威源必须备份），恢复时自动拉起 `backend` + `gitea`。
- **文档**：新增 `docs/ops/12-GITEA.md`（拓扑 / 仓库清单 / 凭据位置 / 日常运维 / 推镜像 / 备份恢复 / 排障），`00-README.md` 索引登记第 12 行。
- **凭据纪律**：token/PAT 只落盘到 `/home/bo/`（`600`），**不进版本库、不写进 remote URL、不写进文档**。
- **已知阻塞**：Gitea → GitHub 单向推送镜像已配置（`sync_on_commit`，8h 间隔），但 GitHub 账号邮箱未验证导致 `403 You must verify your email address`；验证后镜像即自动补齐，JobCopilot 三个 GitHub 仓库的创建同样等待该验证。
- 验证：Gitea 容器 healthy（约 101 MiB）、`/api/healthz` 200、Gitea 卷备份实测 1.9M 且恢复后服务正常、Mac `push gitea main` 与服务器 `git pull` 双向实测通过。

---

## 2026-09-14（新功能：职位列表联动三增强）

- **背景**：投递计划录入职位要手打、批量分析/历史报告看不到原始职位、赛道热力的「招聘 N 个」是死数字——三处都缺「职位列表」的联动入口。
- **① 投递计划可从缓存职位库选填**：
  - 后端新增 `GET /job/cache/list`（`job_cache.list_all_cached`）——返回某用户**全部未过期**的缓存职位集合（按 ts 倒序，含 count/jobs）；
  - 前端投递弹窗顶部加可搜索下拉（展平全部缓存职位，label 形如 `[关键词] 公司 · 岗位 · 薪资`），选中即自动填入公司 / 岗位 / 链接。
- **② 批量分析 + 历史批量报告增加「查看职位列表」入口**：
  - 批量分析页：Alert 增加 action 按钮，弹出该报告的职位列表；
  - 历史报告详情弹窗：footer 增加「查看职位列表（N）」按钮（仅批量类型且有 jobs 时显示）。
- **③ 赛道热力「招聘 N 个」标签可点击**：
  - `MarketSection` 新增可选 `onShowJobs` 回调，标签变可点击（带 `›` 提示）；
  - 点击后按**赛道名关键词粗筛**相关职位弹出（`filterJobsByTrack`，无匹配自动回退全部，避免空列表体验落差）。
- **共用组件**：`render.tsx` 新增 `JobListModal`（职位列表弹框）——支持关键词过滤，职位名复用 `JobTitle`（点击跳原文 + **悬浮看 JD 详情**）；批量分析 / 历史报告 / 赛道热力三处共用同一弹框。
- **测试**：`tests/unit/test_job_cache.py` 10 例（缓存往返 / 取最新 / 列表倒序 / TTL 过期 / 用户隔离 / 条数上限）。
- 验证：后端 pytest 704 passed、ruff 全绿、mypy 307≤310；前端 tsc 0 错、eslint 0 错误、vitest 66 passed；已部署并线上验证（`/job/cache/list` 已注册、容器 healthy、构建产物含新功能）。

---

## 2026-09-14（新功能：招聘分析「投递作战计划」）

- **背景**：大厂社招普遍有「冷冻期」——面试失败后 6~12 个月内无法再投同一公司/岗位；盲目海投会白白消耗机会，需要一个工具管理投递进度与冷却期。
- **后端**：新增 `app/agents/job/apply_plan.py`（按用户隔离的 JSON 存储）：
  - 记录字段：公司 / 岗位 / 分层（①主攻 ②过渡 ③保底）/ 状态（计划投 / 已投 / 面试中 / 已挂 / Offer）/ 投递日期 / 结果日期 / 冷却月数 / 链接 / 备注；
  - **冷却期自动计算**：状态=已挂且有结果日期+冷却月数时，算出 `cooldown_until` / `cooling` / `days_left`，前端直接展示倒计时（含月末溢出处理，如 1/31+1月→2/28）；
  - 字段白名单规范化：tier / status / cooldown_months 脏值一律回退默认，不因脏数据报错。
- **API**：`GET/POST /job/apply-plan`、`DELETE /job/apply-plan/{plan_id}`。
- **前端**：`Job.tsx` 新增「投递计划」tab（进度统计卡 6 项 + 投递表格 + 新增/编辑弹窗 + 挂面冷却提示 `Alert`）。
- **测试**：`tests/unit/test_apply_plan.py` 14 例（CRUD / 用户隔离 / 规范化 / 冷却期 / 月末溢出 / 统计）。
- 验证：后端 pytest 694 passed、ruff 全绿、mypy 306≤310；前端 tsc 0 错、eslint 0 错误、vitest 66 passed；已部署并线上验证（路由已注册、页面 healthy、构建产物含新功能）。

---

## 2026-09-11（Bug 修复：批量分析/历史报告关键词恒为 Agent）

- **根因**：前端 `handleBatchAnalyze` 调 `batchAnalyze({ jobs, force })` 未传 `keyword`，后端回退到 `config.job.default_keyword="Agent"`，导致报告关键词永远是 Agent（职位数正确、仅关键词错）。
- **修复**：批量分析时带上 `lastFetchKeyword`/`fetchCity`；`syncJobCache` 的硬编码 `'Agent'` 兜底改为 `lastFetchKeyword || fetchKeyword || '未指定'`。
- **存量数据修复**：按 `job_count` 映射回真实搜索关键词，修正 8 条历史报告标题 + `.json` keyword + `.md` 标题，并修正批量报告缓存（`Agent`→`技术型产品`）；原文件已备份 `*.bak`。

---

## 2026-09-10（对话费用展示 + 修改/忘记密码）

### 对话 token / 费用展示
- 新增 `services/usage_service.py`：按「对话」与「用户」两个维度用 Redis Hash（HINCRBY/HINCRBYFLOAT）累计 token 与费用；Redis 不可用静默降级。
- `bootstrap` 条件装配 `usage_service`；`chat.py` 每次回复后累加本轮 token/费用；新增 `GET /api/v1/chat/usage` 返回当前对话 + 用户累计（人民币）。
- 配置：`cost_control.usd_to_cny: 7.2`（固定汇率）+ `cost_control.usage`（Redis）。
- 前端：对话输入框下方显示「该对话用了 N tokens，费用约 ¥X 元」；会话列表底部显示「所有对话累计使用 N tokens，费用约 ¥X 元」。

### 修改密码 / 忘记密码（邮箱登录保持不变）
- `POST /auth/change-password`（已登录，校验原密码）→ 设置页新增「修改密码」卡片。
- `POST /auth/reset-password`（忘记密码，邮箱 + 新密码直接重置，无邮件验证；已限流 + 审计）→ 登录页新增「忘记密码？」入口。

> 注：预览模式**仍不开放 AI 聊天**（按确认调整），故不做预览限额。

---

## 2026-09-10（模型切换：全量迁移到 DeepSeek V4.1 Flash）

### 模型统一切换到 deepseek-flash
- V4.1 Flash 官方 API 模型名为 `deepseek-flash`（性能超 V4 Pro，聊天/推理均可）。
- `config.yaml` 所有角色（含 planner/critic_complex 原 reasoner）模型统一改为 `deepseek-flash`；定价改 `deepseek-flash` 单档（近似值，峰谷计价待精确）。
- `llm_factory` 降级链 fallback 目标改 `deepseek-flash`；`User.settings.model` 默认值改 `deepseek-flash`。

### 设置页隐藏模型设置
- `Settings.tsx` 隐藏「默认模型 / 温度 / 最大 Token」三项（模型统一由服务端配置指定），保留「发送快捷键」；卡片改名「偏好」。

---

## 2026-09-10（集群3.2/3.3 + 文档回填）

### JWT jti 黑名单（SEC-02）
- `create_jwt` 增加 `jti`；`revoke_token`/`revoke_jwt` 吊销；`verify_jwt` 校验黑名单；`POST /auth/logout` 吊销当前 token（登出即失效）。

### PromptRegistry
- 新增 `agents/prompts/registry.py`：路径配置化 + 缓存 + 热重载 + 版本哈希（sha256 短哈希）；`bootstrap` 装配 `prompt_registry`（`prompt/` 目录），供硬编码模板逐步迁移。

### 文档回填
- `docs/tmp/简历补强与SEKB增强待办.md`：标记 1/2/3a/3b 已完成、4/5 部分完成。
- `docs/BACKLOG.md`：顶部新增「合并集群索引」，对齐集群2/5/1/3 状态与提交。

---

## 2026-09-10（集群3.1：反思 needs_rewrite 分支 + RAG 评测基线）

### needs_rewrite 分支（关闭 11-EVOLUTION 缺口）
- `CriticAgent` 新增 `should_rewrite`/`rewrite_count`/`rewrite_feedback`（result=needs_rewrite 且未超 `max_rewrite` 时触发）。
- `ExecutorAgent.rewrite_answer`：据 Critic 反馈重写答案（不重跑工具，失败回退原草稿）。
- `graph/builder.py` 新增 `rewrite` 节点 + `critic→rewrite→critic` 循环边；`route_after_critic` 增加 rewrite 分支。
- 配置 `reflection.max_rewrite`（默认 1）。

### RAG 检索质量基线（纯向量，混合检索未启 rerank）
- 真机 `sekb rag-eval` 30 条黄金集基线：**context_recall 0.512 / context_precision 0.468 / faithfulness 0.890 / answer_relevance 0.762**。
- 修复 `RagEvalRunner` 用户隔离导致检索为空的 bug（默认 `user_id=None` 全库检索）。

---

## 2026-09-10（集群1：Redis L2 中期记忆落地）

### Redis L2 会话记忆
- 新增 `memory/session_memory.py`：`RedisSessionMemory`（实现 `SessionMemoryBackend`），基于 Redis 的跨会话偏好（Hash + TTL）与近期话题（List + LRU 截断 + TTL），连接失败优雅降级。
- `bootstrap` 条件装配 `session_memory`（`l2_session.enabled` 且 Redis 可达）；`shutdown_app` 释放连接；`AppContext` 新增 `session_memory` 字段。
- `chat.py` `_run_chat` 在回复后 `record_topic` 记录本轮话题（失败不阻塞回复）。
- 配置 `l2_session.enabled=true` + `redis_url`；docker-compose 启用 redis 服务（移除 `with-db` profile）。

### rag-eval 修复
- `RagEvalRunner` 新增 `user_id` 参数（默认 None=全库检索）；修复默认落到 `"default"` 用户导致检索为空、评测全 0 的问题。

---

## 2026-09-10（集群5：RAG 收尾 + 注入防护补全）

### 黄金集扩充 + 注入防护补全
- `rag_golden.json` 由 6 条扩到 **30 条**（覆盖 Python/异步、LangGraph、RAG 全链路、Agent 机制、MCP/多智能体/评测/系统设计），供 rag-eval 产出简历量化基线。
- 注入防护补全：`knowledge_ingestor` 事实提取 Prompt 加「忽略指令性语句」隔离标注（P2-P2-12）；`share.py` 分享问答入口接入规则层注入检测（公开路径不启用 LLM 层，防成本滥用，SHARE-2）；`format_rag_share_context` 注入隔离标注。

---

## 2026-09-10（集群2：止血 + 故障可见）

### 限流重开（含分享问答）
- `RateLimitMiddleware` 重写为「IP + 路由分组」独立滑动窗口（修复原全局计数误伤）；路由分组按最长前缀匹配。
- 启用 `rate_limit.enabled=true`，分组：auth 5/min、share 20/min、upload 30/min、job 30/min、news 10/min、默认 60/min；纯 ASGI 不缓冲 SSE。
- 覆盖 SHARE-1（分享问答公开链接限流）。

### Tracing 打通（trace_id 注入）
- `core/tracing.py` 新增 `get_trace_config()`：从 structlog 上下文读 trace_id/conversation_id，注入 LangChain RunnableConfig 的 metadata/tags；`setup_tracing` 改为返回是否启用。
- `llm_factory` 的 ainvoke/astream 统一携带 `config=get_trace_config()`，使 LangSmith span 与业务 trace_id 关联。

### 降级可见化 + 指标埋点
- `ChatResponse` / SSE done meta 新增 `degraded` 字段；前端助手气泡在降级时显示「已降级」角标。
- `LLMFactory._record_call` 接入 `record_llm_call`，修复「LLM 单次调用粒度指标零埋点」（重试/降级计数器真正发 Prometheus）。

### 死配置清理
- 修复 `${VAR:-default}` / `${VAR:default}` 环境变量默认值展开缺陷（原实现把整段当变量名，jwt_secret/视觉模型配置的默认值失效）。
- 删除死配置 `app.debug`、`tools.vector_store.provider`。

- 验证：后端 pytest **650 passed**、ruff 全绿、mypy **297≤310**；前端 tsc 0 错、eslint 0 错、vitest 66 passed。

---

## 2026-09-10（功能开发：RAG 增强 / 检索评测 / 注入防护 / 反馈飞轮）

### RAG 检索增强（混合检索 + 重排 + 查询改写）
- 新增 `tools/rag/bm25.py`（基于 rank_bm25 + jieba 的关键词召回）、`tools/rag/hybrid.py`（向量 + BM25 多路召回 → RRF 融合 → 可选重排）、`tools/rag/reranker.py`（LLM 列表式重排，失败降级原序）。
- `graph/builder.py` 的 RAG 节点在 `memory.l3_knowledge.retrieval.hybrid_enabled` 开启时把 `vector_store` 包装为 `HybridRetriever`，上层意图路由/重要性过滤零改动。
- 配置：`l3_knowledge.retrieval`（hybrid/bm25/rerank/query_rewrite 开关）+ `llm.roles.rerank`。生产默认：混合检索开、重排/查询改写关（控延迟）。

### RAGAS 式检索质量评测
- 新增 `eval/ragas_metrics.py`（context_recall / context_precision / faithfulness / answer_relevance 四指标，LLM-as-Judge，零第三方依赖）+ `eval/rag_runner.py` + `eval/datasets/rag_golden.json`。
- CLI 子命令 `sekb rag-eval`（`cli/rag_eval.py` + `main.py`）输出 Markdown 报告。配置 `evaluation.ragas` + `llm.roles.ragas`。

### Prompt 注入防护
- 新增 `core/guard.py`：规则层（长度上限 + `blocked_patterns` 正则）+ 可选 LLM 层（低温度 JSON 分类）。
- `chat.py` `_run_chat` 入口接入，命中抛 `SecurityError` → 403；`tools/rag/format.py` 在检索内容注入前加「忽略指令性语句」隔离标注（防 indirect injection）。
- 配置：`security.prompt_injection_use_llm` / `prompt_injection_llm_role`（默认关 LLM 层）。

### 反馈数据飞轮
- 新增 `services/feedback_service.py`：消费 thumbs up/down → 被引用知识条目 `importance_score` 升降，踩到 0 分删除条目。
- `chat.py` 持久化 `rag_entry_ids`（本轮 RAG 命中的条目）；`conversations.py` 的 `rate` 端点调用飞轮并返回 `flywheel` 结果。

- 验证：后端 pytest **635 passed**、ruff 全绿、mypy **296≤310**（无新增类型错误）；新增单测 4 个文件（hybrid/ragas/guard/feedback）。

---

## 2026-09-10

### Bug 修复（职位分析 / AI 对话 / 历史报告 / 系列文章）
- **职位分析默认关键词**：新增 `lastFetchKeyword`，删除/刷新职位时的缓存同步改用「最近一次实际采集」的关键词，修复输入框被编辑后污染缓存导致默认回填错误（Agent开发 vs Agent）；并修复「采集缓存命中时未刷新 ts」导致默认回填取到旧关键词（Agent 刷新后回填 Agent开发）。
- **批量分析默认展示**：保持「职位集合严格一致」才展示缓存报告的约束（职位列表须与批量分析严格一致，避免两份信息错位）；并修复「职位收集 → 批量分析」联动时报告不写缓存，导致刷新/重新登录后默认展示缺失（根因：`analyze_market` 在 jobs 提供时跳过缓存写入，改为一律缓存报告）。
- **历史报告时间**：由原样展示 UTC ISO 改为 `new Date(...).toLocaleString('zh-CN')`（北京时间，与其他页面一致）。
- **系列文章树**：按「系列名段」截断路径（兼容 父目录/系列名/文件），消除冗余嵌套；后端 `list_series` 按「系列名之后的相对路径」去重，修复重传（不同父路径）导致的重复计数。
- **系列/文件计数截断（根因）**：新增 `_list_all_entries()` 分页拉取全部条目，替换 `list_series`/`list_files`/`reclassify`/`analyze` 的 `limit=5000`（文档块已达 10883，截断导致「3-技术文章汇总」只统计到 8/应为 12）。修复后线上验证：3-技术文章汇总=12、2-Agent全栈开发学习实践=90、3-Agent开发框架学习=20。
- **数据清理**：物理删除「3-技术文章汇总」旧 12 条（带 `2-Agent全栈开发学习实践/` 父前缀的 570 块），避免 RAG 检索重复内容；该系列现仅剩 12 条干净条目。
- **新会话标题**：后端按「首条提问 + 首条答复」用 LLM 提炼标题并回写会话，标题随 done meta 返回；前端流式完成后更新会话列表标题。
- 验证：后端 ruff/mypy 294≤310/pytest 586 passed；前端 tsc 0 错/eslint 0 错误/vitest 66 passed（+2 系列树单测）。

## 2026-09-09

### 深度代码重构（WP7：前端上帝组件拆分 Job/Chat/Files）
- **Job.tsx**（1635→907 行）：展示组件 + 纯函数下沉到 `features/job/render.tsx`（SectionRenderer/MarketSection/KnowledgeSection/JobTitle/JsonBlock + classifyRole/isEmptyValue/getMatchScore 等）。
- **Chat.tsx**（970→899 行）：CodeBlock 组件 + buildConversationMarkdown/joinSelectedMessages 下沉到 `features/chat/markdown.tsx`。
- **Files.tsx**（890→781 行）：SUPPORTED_EXTENSIONS/filterSupportedFiles/flattenItems/readEntry/buildSeriesTree 下沉到 `features/files/helpers.tsx`。
- 验证：tsc --noEmit 0 错、eslint 0 错误、vitest 64 passed。

### 深度代码重构（WP6：死配置/死代码/占位配置关删）
- **死代码清理**：删除 `scheduler.trigger_now`（无调用方）、`verify_phase3.py`（阶段性验证脚本）。
- **死配置删除**：`TracingConfig.sample_rate`（无消费，P2-12）、`VectorStoreConfig.enabled`（bootstrap 只查 l3_knowledge.enabled，P2-P2-05）。
- **D3 占位配置关删（P1-6）**：删除 adaptive/sampling 反思占位（factory 原降级为 always）及 config.yaml 段；删除 cost_control 预算占位字段（per_conversation_token_limit/daily_budget_usd/reasoner_ratio_alert_above/auto_downgrade_on_budget，均无消费），仅保留 pricing；实现项列入 11-EVOLUTION。
- 全量 586 passed；ruff 全绿、mypy 297≤310。

### 深度代码重构（WP5：记忆压缩加锁 + 恢复压缩 + 配置语义）
- **P2-15**：`ShortTermMemory.compress_if_needed` 增加按 conv_id 粒度的 `asyncio.Lock`（抽 `_compress_impl`），并发压缩串行化。
- **NEW-D**：API `_run_chat` 与 CLI `_restore_history_to_memory` 在历史恢复后调用一次压缩，避免超大历史堆积。
- **P2-13**：`model_switch_threshold` 补 description + critic.py 语义注释（`task_complexity >= 阈值 → reasoner`）。
- **Q-4.3 复核**：JSONStorage 已有 `self._lock`（json_storage.py:77/356），跟踪项过期。
- 全量 587 passed；ruff 全绿、mypy 299≤310。

### 深度代码重构（WP4：LLM 统一入口收口 + 限流接线）
- **LLM 统一入口**：9 处绕过统一入口的裸调用（news×4 / job×2 / classifier / upload_service / share 流式）改为 `ainvoke_with_stats`/`astream_with_stats`（带统计/重试/降级记录）；删除 share.py 死代码 `_extract_stream_text`。
- **RateLimitMiddleware 重开（D4）**：由注释死代码改为 `config.api.rate_limit.enabled` 驱动的条件挂载，阈值取 config（`requests_per_minute` / `rate_limit_login_per_minute`）；默认仍关闭，开启前需验证 SSE。
- **Q-4.7 复核**：`_record_call` 已有 `async with self._lock`（llm_factory.py:150/538），跟踪项过期，无需改动。
- 全量 587 passed（-5 移除 `_extract_stream_text` 测试）；ruff 全绿、mypy 299≤310。

### 深度代码重构（WP2 收尾 + WP3：画像下沉 profile_service 并删除 skill）
- **新增 `services/profile_service.py`**：画像偏好抽取（方案 A `<PREF>` 提取 + 方案 B 记录员 LLM 后台抽取）从 chat.py 下沉；共享 `_build_pref_patch`/`_upsert_profile` 消除原 `_extract_and_update_profile` 与 `_apply_pref_to_profile` 的重复逻辑。
- **删除 skill（D2）**：移除 `ChatRequest.skill`、`_build_skill_context`、`_build_job_analysis_context` 及 `_run_chat` 的 skill 注入；反馈闭环 A 保留（无 `<PREF>` 时 no-op），闭环 B 随 skill 解耦（函数保留，待「求职意图」识别后按意图触发）。
- chat.py 828→538 行；非 skill 路径行为一致。新增 test_profile_service 7 例；全量 592 passed；ruff 全绿、mypy 300≤310。

### 深度代码重构（WP2：job 路由文件导入解析下沉）
- **新增 `services/job_service.py`**：`parse_job_files(files)` 提取 import_jobs 的 55 行文件解析循环（FileProcessor + 临时文件 + 编码回退），job.py 净减 ~50 行并移除不再使用的 os 导入。
- 新增 test_job_service 3 例；全量 585 passed；ruff 全绿、mypy 301≤310。

### 深度代码重构（WP2：share/chat_share 重复助手收敛）
- **新增 `services/share_service.py`**：`get_valid_share`（404/403 校验）与 `owner_display_name`（脱敏展示名，fallback 参数化）单一实现，消除 share.py 与 chat_share.py 各自复制的两份逻辑。
- 两路由保留各自 `_require_share_storage`（不同存储后端）与薄委托包装，调用点/行为不变。
- 新增 test_share_service 7 例；全量 582 passed；ruff 全绿、mypy 301≤310。

### 深度代码重构（WP2：upload 路由入库流水线下沉）
- **新增 `services/upload_service.py`**：从 `api/routes/upload.py`（1006→618 行）提取 MD5 去重索引、图片/文档原文件持久化、入库流水线（process_and_ingest/ingest_chunks/background_ingest/classify_document）、LLM 概览。
- **领域类型 IngestResult**：服务返回领域结果，路由转换为 UploadResponse，消除「服务→路由模型」反向依赖。
- 行为不变；test_image_processor 改从 upload_service 导入；新增 test_upload_service 6 例；全量 575 passed；ruff 全绿、mypy 301≤310。

### 深度代码重构（WP2：服务层下沉 · 首个子提取）
- **新增 `services/browser_client.py`**：从 `api/routes/job.py` 内联的 `_call_browser` + 硬编码 `_BROWSER_BASE` 提取为 `BrowserClient`（可配置 base_url、可注入/单测）。
- **job.py 路由瘦身**：BOSS 扫码两处调用点改走 `_browser.post(...)`，行为不变。
- 新增 test_browser_client.py 2 例；全量 569 passed；ruff 全绿、mypy 306≤310。

### 深度代码重构（WP1：公共工具收敛重复清零）
- **新增 `core/utils.py`**：收敛 `now_iso`（原 graph/state + storage/base 双份）、`fmt_dt`（原 share/knowledge/chat_share 三份）、`safe_float`（原 critic/scribe 双份）、`to_state_dict`（原 chat/cli/eval 三份）为单一实现。
- **新增 `tools/rag/format.py`**：三处 RAG 上下文格式化归一（retriever「知识库参考」/ executor「相关度」/ share「来源编号」三种风格集中维护）。
- **重复清零**：registry `except (MCPError, Exception)` 冗余简化为 `except Exception`。
- **测试**：新增 test_core_utils + test_rag_format 共 39 例；全量 567 passed（528+39）；ruff 全绿、mypy 307≤310。
- 覆盖跟踪项：P2-14 / Q-4.5 / Q-4.6 / Q-4.11 / P2-P2-03 / NEW-E。

### 文档工程重构（按 1-6 工程逆向分析提示词，完整版 12 篇）
- **备份**：原 `docs/` 历史内容清理：旧技术文档/问题记录/测试用例文档已删（git 历史可查），codeReview 迁移至 `docs/codeReview/`，运维手册至 `docs/ops/`，活文档至 docs 根。
- **保留**：运维/部署手册副本 → `docs/ops/`（10 篇，真实环境操作）；CHANGELOG/BACKLOG 活文档副本 → `docs/` 根继续维护。
- **新工程 `docs/tech/`**：完整版 12 篇证据驱动文档（00-README 地图 + 01-ARCHITECTURE C4/ADR + 02-RUNTIME-FLOWS + 03-MODULES + 04-DATA-MODEL + 05-API(65端点/SSE/CLI) + 06-CONFIG + 07-DESIGN-PATTERNS + 08-GLOSSARY + 09-OBSERVABILITY + 10-TESTING + 11-EVOLUTION），全部带 `file:line`、Mermaid、编号/术语一致。
- **事实表 SSOT**：`docs/tech/.facts/T1-T8`（路由 65/配置 178/LLM 23 调用/AgentState/工具/存储/异步/可观测），多子代理并行抽取。
- **发现与记录**：49 死配置、10 处绕过 LLM 统一入口、4 死指标、7 无消费 State 字段、`${VAR:-default}` 语法缺陷、多处注释-实现漂移（→ `docs/tech/漂移清单.md` + `待确认项清单.md`）。
- **代码-文档联动**：`.validation/check-doc-sync.sh`（pre-commit hook：改路由→提示 05、改 config→06、改 graph/agents→02/03、改存储→04、改前端→03、改测试→10）+ `check-freshness.sh`（last-updated/based-on-commit 过期标记）+ `check-links.sh`（链接/孤儿，本地与服务器均全绿）。
- 根 `README.md` 文档导航更新为新工程结构。

### AI 对话体验优化（技能按钮移除 + 消息操作 + 滚动跟随 + 真流式确认）
- **移除技能按钮（任务1）**：删除 AI 对话输入区「通用/应聘/科技资讯助手」技能按钮及 `skill` 参数链路（前端 service/store/Chat + 后端 ChatRequest/`_build_skill_context`/`_schedule_preference_extraction` 调用）。技能模式后续有更具体需求再开发。
- **消息操作按钮（任务2）**：AI 答复下新增 复制/重生成/转发；用户输入下新增 复制/编辑。store 新增 `regenerateAssistant`（截断到该用户消息后重发）+ `editUserMessage`（替换内容+删除其后消息重发），两者复用 `runStream` 走真流式。
- **滚动跟随（任务3）**：用户上滚（距底 >80px）即停止自动跟随，右下角出现悬浮「回到底部」按钮；点击恢复跟随。
- **真流式确认与思考进度修复（任务4）**：作答 token 已确认是**真流式**（`astream_with_stats` → token sink）。首字延迟主因是顺序多智能体链（supervisor → RAG → **deepseek-reasoner planner 15~35s** → executor 逐次 LLM 调用），非推送端问题。修复「思考过程停在旧文案」：`astream`（仅节点结束推送）→ `astream_events`（节点**开始**即推送「正在…」进度），长耗时 LLM 节点期间进度条实时可见。实测节点进度随执行实时推进（0s 理解意图→1s 检索/规划→3s 执行→6s 反思→7s 生成），简单问题首字 5.8s，复杂问题首字延迟取决于 reasoner 规划时长。


## 2026-09-09

### 测试工具链整合（代码审查批次 1+2，含精简去重）
- **约定确立**：每次提交前跑改动范围**增量测试**（`scripts/incremental_test.sh`，支持 `--staged` 提交前对比）；每两周/每月跑一次**全量测试**（`scripts/full_test.sh`）。
- **增量/全量脚本**：`scripts/incremental_test.sh`（改动映射：后端 ruff+mypy 回归门禁+相关 pytest，前端 tsc+eslint+vitest）+ `scripts/full_test.sh`（后端全量单测/集成/门禁 + 前端全量/构建）；后端测试跑在自动构建的 `sekb-toolbox` 镜像（backend 镜像 + ruff/mypy/pytest/feedparser）。
- **后端门禁**：ruff 覆盖面扩到 `tests/` 并清零存量 139 处违规（`86443f9`，行宽 100→120，适配存量中文/SSE 长行）；mypy 改从 backend 目录执行使 `[tool.mypy] strict` 真正生效，存量 310 条类型债转为**回归门禁**（`scripts/mypy_gate.sh` + `backend/mypy-baseline.txt`，超基线才失败，防新增不阻塞迭代）。
- **测试补强**：修复 2 个 API 聊天测试的 mock（`_run_chat` 已改 `astream` 流式执行，`ba79b75`），后端 521→528 passed；新增 TestClient 路由级 API 测试 7 例（知识列表/搜索参数透传、kb 未启用降级、401/403 门禁）；前端接入 @testing-library/react + jest-dom（组件测试 3 例，58 passed）。
- **前端门禁**：ESLint 9 flat config（typescript-eslint + react-hooks）接入 CI 并清零存量 error（`c431ada`），`no-explicit-any` 存量 114 条降 warning 逐轮收窄；tsc --noEmit 保留。
- **pre-commit**：`.pre-commit-config.yaml`（ruff + mypy 回归门禁走 docker，tsc/eslint 走本机 node）。
- **安全扫描**：CI 新增 security-scan job——gitleaks（密钥扫描）+ pip-audit（Python 依赖漏洞）；frontend-build 增加 `npm audit --omit=dev --audit-level=high` 阻断门禁（生产依赖当前仅 5 条 moderate，react-router 链）+ 全量 audit 上报（devDeps 漏洞非阻断）。
- **覆盖率门禁**：CI `--cov-fail-under=40`（当前 43%，逐轮上调）。
- **工具清单精简去重**：safety 并入 pip-audit、hadolint 并入 Trivy；文档守卫类（lychee/OpenAPI/清单）推迟到文档轮次（D15）；node_exporter/cAdvisor 属监控运维轮次。详见 `docs/BACKLOG.md`「五、测试工具链清单与状态」。



## 2026-09-08

### P0 止血（合并 Qoder 深度审查后）
- **修复（CON-02 并发丢数据，已动态复现）**：JSON 存储层引入 `asyncio.Lock` 保护 index 读-改-写临界区；uvicorn `--workers 2` → `--workers 1`（消除多进程共享 ChromaDB/SQLite 并发写、Prometheus 指标失真、scheduler 重复执行）。
- **修复（SEC-01 JWT 弱密钥）**：`get_jwt_secret` 改为 fail-closed（未配置/过短/含占位词即拒绝启动），移除硬编码回退；启动期校验；**已轮换生产密钥**（旧 token 全部失效，需重新登录）。
- **修复（SEC-02 端口暴露）**：backend `8000:8000` → `127.0.0.1:8000:8000`，仅经 nginx 反代对外。

### R2-06 答案 token 真流式
- **新增**：`llm_factory.astream_with_stats` 流式接口 + `core/token_sink.py`（contextvar token 回传）+ Executor 流式生成草稿答案逐 token 回传。答案在生成阶段即流式输出（实测 27.5s 开始出 token，618 token），替代「全量生成后逐字推」。

### 文档工程（第一刀）
- **导航收敛（C2）**：`docs/README.md` 补 8 篇孤儿（ISSUES-FIXES/boss-jd-cookie-manual/job-sources/INCREMENTAL-TEST-CASES/codeReview 等）+ 归档标注 + 新增「Phase 5 能力速览」与阶段路线图 Phase 5。

### 安全止血 + 访问控制 + 体验增强（批次 A0 + 白名单 + B1/B2）
- **安全修复（批次 A0）**：chat 路由补会话归属校验（SEC-01 越权 IDOR）；全局异常脱敏返回 `error_id`（SEC-04）；资讯只读接口补登录鉴权（SEC-05）；分享 `is_scoped` 改任意级非空 + 层级完整性校验（R2-01）+ 默认 30 天过期（R2-02）。
- **可靠性修复**：`backup_kb.sh` 加 `trap` 兜底启动 + 新增 `restore_kb.sh`（R2-03）；前端队列卡死补 `flushQueue` + 非流式清空队列入口（R2-05）；前端单测修复（streamChat 7 参 + user init 会话恢复）（R2-18）；RAG 检索失败打标记（RAG-09）。
- **新增（用户白名单）**：`ALLOWED_EMAILS` 环境变量（逗号分隔邮箱）——白名单内=完整功能，非白名单=预览（仅功能说明 + 科技资讯只读，不能重新生成日报）。`/me` 返回 `access_level`，前端按级别收窄菜单/路由，后端 router 级 `require_full_access` 依赖拦截。
- **新增（B1 思考过程流式）**：聊天由固定「正在思考…」改为按图节点流式推送真实进度（理解意图→检索知识库→规划→执行→反思→生成回答），用 `astream` + `asyncio.Queue` 并发消费。
- **新增（B2 发送快捷键）**：设置页增加「发送快捷键」选项（Enter 发送 / Cmd+Ctrl+Enter 发送），聊天输入框按设置生效。

### 知识库（关键故障修复 + 加固）
- **故障**：ChromaDB HNSW 段损坏导致 `/upload/status` 超时、`/upload/files` 500、上传 502（`chromadb.errors.InternalError: Failed to apply logs to the hnsw segment writer`，为 1.5.9 已知 HNSW bloat-guard bug，官方暂无修复版）。
- **纠正**：此前「删除 VECTOR 段从 WAL 重建」的修复**误删了向量**（WAL 早已合并进段，删除后重建出空段，导致检索返回 0）。真正修复为**重嵌入重建**：`scripts/rebuild_chroma_vectors.py` 从 SQLite 取出 10603 条文档 → 用同一 bge-small-zh 模型重嵌入 → 重建 collection，检索恢复（探针命中、RAG 查询分数 0.74）。
- **性能修复**：`count`/`list_entries` 不再加载 embedding（`include=[]` / `include=["documents","metadatas"]`），避免大库慢查询导致接口超时。

### 知识库加固（L0/L1/L2）
- **L0 锁版本**：`chromadb>=0.5.0` → `chromadb==1.5.9`（当前最新，含 bug 但无修复版，锁死防漂移；待官方修复版再升级）。
- **L1 原始文件落盘**：文档（md/pdf/docx/txt）上传时按相对路径保存到 `data/uploads/documents/`（此前只有图片存原图），作为 ChromaDB 全毁时的最终重灌源。
- **L2 每日备份**：`scripts/backup_kb.sh`（停 backend → 打包整个 `sekb_data` 卷 → 重启 → 保留 14 天），已装 crontab 每天 3:30 执行；已手动跑通首份备份（124M）。
- **L6 模型缓存持久化（D10）**：`HF_HOME=/app/data/hf_cache` 指向数据卷，embedding 模型缓存随 `sekb_data` 卷持久化——重建后端容器不再从 hf-mirror 重下 ~100MB 模型（已把旧缓存迁移进卷，冷启动后检索 0.08s，且每日备份一并覆盖模型缓存，恢复完全自足）。

### 账号隔离（审查 + 修复）
- **审查结论**：会话、职位缓存（3 个）、知识库 ChromaDB、分享均已按 user_id 隔离；**资讯 news 定性为「系统级公共资源」**（登录即可看），维持全局。
- **修复**：文件原始存储（L1）路径与 `uploaded_md5.json` 索引补上 user_id 隔离（`{user_id}/相对路径`、`{user_id}|文件名`），并迁移存量 133 条 MD5。

### 聊天增强
- **新增（对话排队自动发）**：回复进行中时新输入进入队列，当前回复结束后自动发送下一条，避免误打断。前端队列（`pendingQueue`+`flushQueue`）+ 后端按会话加并发锁（同一会话重复流式请求返回错误事件）。
- **新增（历史会话右侧快速导航）**：对话区顶栏加「历史会话」按钮，悬停弹出历史会话浮层（置顶/切换/删除），参考 deepseek 的深色浮层，方便快速查看与切换历史会话。

### 多功能联动地基（第 4 条前置）
- **新增（用户画像）**：`UserProfile` 模型 + `ProfileStorage`（按 user_id 隔离，`data/profile/{user_id}.json`）+ `GET/PUT /api/v1/profile`。含求职偏好（目标岗位/城市/薪资/公司类型/关键词）、技能、职业目标、资讯关注大类——供「应聘助手 / 科技资讯助手」skill 与招聘分析、资讯模块联动使用；聊天中的偏好回流将存入画像。

### 聊天增强 vol.2
- **性能修复**：知识库分类统计 `/knowledge/categories/stats` 由「载入全部文档全文」改为「仅载元数据」，耗时从 ~18.9s 降到秒级——修复分类目录下每个分类无条目数（前端拿不到 counts）。
- **修复（分类目录数量为 0）**：`list_entries` 用 `get(key, [])` 兜底，但当 ChromaDB `include` 不请求某字段（如 `documents`）时，该键存在但值为 `None`，导致 `len(None)` 抛错、统计返回空 `{"stats":[]}`。改用 `get(key) or []` 兜底，统计恢复正常（全部/各分类有真实条目数）。
- **改进（系列文章树对齐）**：系列/子目录/文件改用 antd `showIcon` + 统一图标（📖 读、📁 文件夹、📄 文件），替代 emoji 混排，避免 `[+]` 开关与标题图标错位。
- **改进（应聘助手主动提问 + 画像回流）**：应聘助手**不再强依赖用户画像**——画像不全时主动向用户提问（目标岗位/城市/薪资/技能/职业目标）；用户给出后，回复末尾输出 `<PREF>{...}</PREF>` 结构化标签，后端解析并写入用户画像（反馈闭环），并把标签从展示内容中去除，避免用户看到。
- **新增（应聘助手与职位分析共享数据）**：应聘助手注入近期的职位分析结果（最近 2 份存档报告的「关键词/城市/职位数 + 热门方向 Top3 + 建议补强 Top3 + 代表职位 Top4」+ 职位市场概况）——实现「先分析职位、再据此沟通应聘事宜」的工作流。
- **改进（方案 B：记录员 LLM 抽取偏好）**：不再只依赖主 LLM 输出 `<PREF>` 标签（方案 A 保留为轻量兜底）。新增后台「记录员 LLM」独立分析对话，提取求职偏好写入画像——**异步、不阻塞主回复**，更可靠。
- **新增（D14-职位：画像反向影响职位分析）**：批量分析「一键市场」与单职位分析均按 `user_id` 读取用户实时画像（聊天积累的求职偏好/技能/职业目标），作为「用户实时更新偏好」补充注入分析提示词——分析与用户真实诉求更贴合。

### D14-资讯（定案：保持公共流，不个性化）
- **决策**：资讯为「系统级公共资源」，日报**不做按用户个性化注入**（方案①）。用户画像中的 `news_interests` 不参与日报生成；保持公共流干净自洽。若将来需要「为你推荐」类个人化，放到前端做，不改公共日报生成。
- **已知现象（非本次引入）**：聊天工作流首 token 延迟较高（supervisor→RAG→planner→executor 多步 LLM 调用），通用助手与应聘助手一致；SSE 渐进流出不影响接收。
- **改进（右侧导航改为「对话内历史提问」）**：右侧边缘触发条 + 右侧 Drawer 改为**列出当前对话内的用户历史提问**（用户侧输入，非会话列表），点击某条提问即**滚动定位到消息流中该提问位置并短暂高亮**——方便回看当时上下文。
- **新增（skill 技能按钮 + 调度）**：聊天框下技能按钮（通用助手 / 应聘助手 / 科技资讯助手，参考豆包）。切换技能 → 聊天请求带 `skill` 参数 → 后端构建技能上下文（应聘助手注入求职画像 + 职位市场概况；资讯助手注入关注大类）注入本次 LLM 输入，且**不写入会话历史**。用户画像地基 + skill 注入为「深入沟通」核心；「聊天偏好→画像→反向影响招聘/资讯」的反馈闭环记入 D14。

### 系列识别
- **新增**：`detect_series` 支持「dN 第N天」编号（如 `2-Agent全栈开发学习实践/2-s1-w1/d1-xxx.md`），系列名取**顶级目录名**；`N-` 数字前缀的根目录文件（总纲/学习计划/补充资料）保持独立、不误入系列。
- **性能修复**：`/upload/re-series` 由逐条 `update_metadata` 改为**批量元数据更新**（1 次 get + 1 次 update），并把单次 `limit=5000` 改为分页拉全量，避免大库下超时/漏文件。耗时从 >280s 降到约 15s。
- **修复**：系列树**默认折叠**（移除 `defaultExpandedKeys`），刷新页面即自动加载系列列表（此前因 ChromaDB 500 导致加载为空）。

### 文件上传
- **修复（去重不生效）**：同名去重/跳过此前用 `file.name`（basename）对比后端入库的 `file_name`（完整相对路径），导致文件夹重传时永远匹配不上、不弹「同名同内容跳过」框。现统一改用相对路径标识 `fileKey = webkitRelativePath || name`。
- **改进（续传弹窗）**：检测到未完成上传时，弹窗改为**每个文件前加复选框**，并**只列出尚未入库的文件**（过滤掉已成功入库的残留项）；确认后按勾选的文件续传（文件夹场景自动用文件夹选择器以保留相对路径）。

### 知识库分享（按分类限定范围）
- **新增**：分享不再只支持整个知识库，现在可在创建分享时用**分类级联选择**（大类 → 子类 → 细类）限定范围，选到哪一级就只分享到哪一级；不选则分享整个知识库。
- **后端**：`SharedKnowledge` 增加 `category_l1/l2/l3`；创建/列出/详情均返回 `category_label`；`list_shared_entries` 与分享问答的 RAG 检索都**按分享范围强制过滤**（防止越权看其他分类）；`retrieve`/`search` 增加三级分类过滤参数。
- **前端**：分享弹窗加 `Cascader` 分类选择、分享列表与结果页显示分享范围；分享访问页头部显示分类范围标签。

---

## 2026-09-07

### 文件上传
- **改进**：上传循环迁移到全局 store（zustand），切换左侧导航（组件卸载/重挂）不中断上传，重挂后从 store 读回进度。
- **新增（轻量刷新续传）**：上传时把未完成文件名写入 localStorage，刷新后检测到未完成列表时弹窗列出，让用户确认后重新选择这些文件继续上传（不存文件内容，File 对象跨刷新无法序列化）。

### 资讯日报（分类升级 + 信息源扩充）
- **新增**：分类从「关键词匹配」升级为「批量 LLM 语义分类」（单归属），能按内容区分「Agent(应用层) vs 大模型(算法层)」，失败回退关键词。效果：大模型 7→20 条、安全 0→20 条。
- **新增**：`WebFetcher` 网页采集器（CSDN 热榜 + 魔搭模型库，公开 JSON 接口无需 Token），接入资讯采集流水线。

### 文件上传 / 系列识别
- **新增**：系列文章改用 Tree 多级展示（系列 → 子目录 → 文件），默认展开第一级、子级折叠。
- **修复**：系列文章识别不准确——`detect_series` 支持「第N天/课/讲/期」等常见单位，文件名本身无系列名时用**文件夹名**兜底（利用「同一文件夹下多为同一系列」规则）。
- **新增**：`POST /upload/re-series` 重新识别存量文件系列（不重新分类，轻量）；前端「系列文章」卡片加「重新识别系列」按钮。

### 文件上传
- **修复**：上传失败 502/503 给出明确提示（后端重启/初始化中），并支持「一键重试失败的文件」。
- **新增**：同名文件按内容 SHA-256 对比——同名且内容一致时可「跳过」（含复选框批量跳过），同名但内容不同才询问覆盖。
  - 后端上传时计算并记录文件哈希（`data/uploaded_md5.json`），`/upload/files` 返回 `md5`。

### 资讯日报
- **回退**：取消「有限多归属」，恢复**严格单归属**（一条只归一个类）；不足条目的类通过「扩大信息源」解决，而非分类层凑数。
- **新增**：信息源扩充掘金分类/标签（前端/后端/Android/iOS/LLM/Agent/Flutter），filtered 从 95 → 162 条。
- **改进**：归类改用「标题 + 摘要 + 正文前 500 字符」匹配，提高与大类的相关性。
- **改进**：单类输出加 20 条硬上限，防止某类过载。

### 批量分析
- **回退**：恢复「职位列表严格一致才默认展示最后一次报告」，避免展示过时报告引起误解。

---

## 2026-09-07（更早）

### 资讯日报
- **新增**：`_generate_category` 对 LLM 输出 items 按 title 去重（消除重复条目）。
- **调整**：大类顺序让热门具体类（Agent/RAG/鸿蒙/跨端）优先于宽泛类，避免被「大模型」等抢光。
- **新增**：历史报告存档额外落结构化 JSON，前端用与批量/单职位一致的语义化布局渲染。
- **新增**：批量分析 tab 默认展示最后一次缓存报告（后按用户要求回退为严格一致）。

### 文件上传
- **新增**：支持文件 + 文件夹混合拖入上传，文件夹递归到最深层（`webkitGetAsEntry` 递归 + `webkitdirectory` 选择文件夹按钮）。

### 职位分析
- **新增**：缓存职位默认展示 + 筛选选项回填（`/job/cache/latest`）。
- **新增**：历史报告分「批量分析 / 单职位分析」两个子 tab。
- **新增**：批量分析默认展示最后一次缓存报告（`/job/batch-analyze/cached`）。
- **新增**：资讯日报单归属（一条只归一个类）+ 摘要关键词加粗高亮。

### 资讯日报（归类修复）
- **修复**：去掉「大模型发布/更新」的裸「模型」关键词、「Agent」类的裸「框架」关键词，避免误匹配数据模型/车型/前端框架等。
- **修复**：exclude「早报」，排除 IT早报 类每日汇总栏目。

---

## 2026-09-04 ~ 09-05

### 职位分析（重构）
- **新增**：批量分析接入求职者视角提示词（`批量职位分析.md` + `职位知识迭代.md`），并行两次 LLM 产出「市场行情」+「知识迭代」，喂 JD 摘要。
- **新增**：单职位分析融入「JD 潜台词翻译」，简历建议强化「diff 微调」（不重写全文），移除 per-JD 学习计划。
- **新增**：批量/单职位报告改为中文语义化布局（替代生硬 JSON 表格）。

### 职位收集
- **新增**：跨页全选（「全选全部 N 条」按钮 + `preserveSelectedRowKeys`），解决分页 100 条上限无法全选全部职位。

### 监控与告警
- **新增**：飞书告警卡片按钮拆分为「查看告警(Prometheus) / 查看看板(Grafana) / 查看对话记录」，与告警源一致。
- **修复**：前端健康检查误报 unhealthy（改用 HTTPS + `--no-check-certificate`）。
- **新增**：监控页公网暴露需求记录到 BACKLOG D5（暂缓）。

### 职位筛选
- **修复**：城市筛选从「前缀匹配」改为「包含匹配」，识别「浙江 / 北京市」这类多地职位。
- **修复**：mokahr 城市归一化优先取 cityName（省名兜底），避免「浙江」顶替「杭州」。

### 飞书告警深链
- **新增**：飞书告警「查看对话记录」深链定位到具体会话：
  - 后端 `answer_groundedness` 增加 `conversation_id` 标签；
  - `alerts.yml` 透传 `conversation_id`；
  - feishu_gateway 按钮跳 `FRONTEND_URL/chat?conversation_id=xxx`；
  - 前端 Chat 支持 `?conversation_id=` 深链，未登录时保留目标登录后回跳。

---

## 附：文档维护约定

1. 每次功能迭代或问题修复完成后，**必须**在本文件追加记录，并更新头部日期。
2. 涉及 `config.yaml` 参数变更的，同步更新 [04-config-reference.md](./04-config-reference.md)。
3. 新增架构级决策的，在 [01-architecture.md](./01-architecture.md) 追加 ADR。
4. 需求暂缓/推进的，同步更新 [BACKLOG.md](./BACKLOG.md)。
