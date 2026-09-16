---
title: T1-路由清单（REST API 端点事实表）
table: T1
source: backend/app/api/routes/*.py, backend/app/api/server.py
status: draft（阶段1 SSOT 工作产物）
---

# T1 路由清单

> 覆盖 backend/app/api/routes/ 下 13 个业务路由文件（auth / chat / chat_share / conversations / health / job / knowledge / metrics / monitoring / news / profile / share / upload）全部 `@router` 端点，共 **65 个**。
> 证据纪律：每条记录附 `文件:行号`；未读到的代码不写；推断标【推断·待验证】；密钥一律 `<REDACTED>`。

## 0. 总览与通用说明

### 0.1 路由注册（backend/app/api/server.py）
- 全部 13 个 router 在 `create_app()` 内延迟导入并 `include_router` 挂载：`app/api/server.py:254-280`（auth:254/268、chat:255/269、chat_share:256/270、conversations:257/271、health:258/272、job:259/273、knowledge:260/274、metrics:261/275、monitoring:262/276、news:263/277、profile:264/278、share:265/279、upload:266/280）。
- 路由注册完成日志（server.py:282-289）所列 routers 数组为 12 项，缺 `profile`（仅日志文案漏列，实际已 include_router(profile_router)，server.py:278）。
- ASGI 入口：`app/api/main.py:14`（`app = get_app()`，延迟构建）。

### 0.2 鉴权机制（全局，backend/app/core/auth.py、access.py）
- `get_current_user`：HTTPBearer（`HTTPBearer(auto_error=False)`，auth.py:19），无 Authorization 头 → 401 `"未登录"` + `WWW-Authenticate: Bearer`（auth.py:95-100）；有头则 `verify_jwt` 解 HS256 JWT（auth.py:101-102、80-88），`sub` 为用户 ID。JWT 密钥强制自环境变量 `JWT_SECRET`（≥32 字符且不含弱占位词，否则拒绝启动，auth.py:39-48）；过期/无效 → AuthError → 500/401【推断·待验证：AuthError 非 HTTPException，由 SEKBError 全局处理器兜底映射 500】。
- JWT 有效期：`config.api.auth.token_expire_hours`=2160h（90 天）（auth.py:71、config.yaml:275）；⚠️ auth.py:68 create_jwt docstring 仍写「72 小时」，与 config 实际值不一致（文档漂移）。
- `require_full_access`（access.py:50-65）：以环境变量 `ALLOWED_EMAILS`（逗号分隔邮箱白名单，access.py:19-24）判定；未配置白名单 → 全员 full；预览账号访问 → 403。
- 每个端点的“鉴权”列使用以下简写：
  - `open`：无任何鉴权
  - `登录`：`Depends(get_current_user)`（任意登录用户）
  - `full(路由级)`：router 级 `dependencies=[Depends(require_full_access)]` + 处理器内 `get_current_user`
  - `full(handler)`：仅处理器内 `Depends(require_full_access)`

### 0.3 限流（现状：已启用，双闸）
- `RateLimitMiddleware`（纯 ASGI 滑动窗口，middleware.py）已挂载并生效（server.py:204-219，`config.api.rate_limit.enabled: true`，2026-09-10 起）。
- 分组额度（前缀匹配，**可带方法**）：auth 5 / share 20 / upload 30 / job 30 / `POST /news/` 6（生成）/ `/news/` 120（读）/ 默认 60，60s 滑动窗口。
- 另有 nginx 第一道闸：`api_limit 10r/s`、`news_limit 30r/s`、`client_event_limit 2r/s`；被限流返回 429（`limit_req_status 429`）。
- 详见 `docs/tech/05-API-REFERENCE.md §1.3`。

### 0.4 错误处理总览
- 全局 SEKBError 异常处理器：server.py:205-223，映射见 server.py:75-106（LLMRateLimit/Budget→429、LLMTimeout→504、LLM/Tool→502、Security→403、StorageError 含“不存在”→404 否则 500、其余→500），响应体 `{error, message, details}`。
- 全局兜底 Exception→500，响应只含脱敏 `error_id`（server.py:226-250）。
- 各端点的 `HTTPException` 分支在表内“错误”列列出（仅限直接在该端点函数体内抛出者）。

### 0.5 端点计数
auth 6 / chat 2 / chat_share 3 / conversations 7 / health 3 / job 14 / knowledge 6 / metrics 1 / monitoring 1 / news 5 / profile 2 / share 7 / upload 8 = **65**。

---

## 1. auth（前缀 `/api/v1/auth`，auth.py:29）

> 本模块 router 无路由级依赖；register/login 故意开放，其余要求登录。

| 端点 | 鉴权 | 请求字段 | 响应 | 错误 | 副作用 | 幂等性/备注 | 证据 |
|---|---|---|---|---|---|---|---|
| POST `/register` | open | RegisterRequest：`email` str 必填，正则 `^[a-zA-Z0-9_.+-]+@…$`；`password` str 必填 min8 max128；`name` str 默认"" max50（user.py:42-46） | LoginResponse=`{user: UserPublic, token: str}`（user.py:55-58），UserPublic 字段 user_id/email/name/avatar_url/created_at/is_active/settings/access_level（user.py:29-39） | 400 `该邮箱已被注册`（邮箱已存在，auth.py:64）；500 用户存储未初始化（auth.py:37） | 写 `UserStorage.create(user)`（auth.py:72，密码 PBKDF2-SHA256 加盐哈希 auth.py:51-55）；audit `REGISTER`（auth.py:86-87）；指标 `record_register_attempt`（auth.py:63/85） | **否**：重复注册同邮箱→400；每次成功创建新用户 | auth.py:41-88 |
| POST `/login` | open | LoginRequest：`email`/`password` str 必填，无长度/格式约束（user.py:49-52） | LoginResponse（同上） | 401 `邮箱或密码错误`（用户不存在/密码错统一文案防枚举，auth.py:116/134）；500 存储未初始化 | 仅审计与指标（audit LOGIN_FAILED/LOGIN auth.py:102/119/147；record_login_attempt auth.py:115/133/146）；无持久化写 | 可重复（每次返回新 JWT；无写操作） | auth.py:91-148 |
| POST `/logout` | 登录 | 无请求体 | `{"status": "ok"}`（客户端自行清 token，auth.py:153 注释） | 无显式 HTTPException | audit `LOGOUT`（auth.py:156） | 幂等（无状态变更） | auth.py:151-157 |
| POST `/refresh` | 登录 | 无请求体 | LoginResponse | 404 `用户不存在`（auth.py:171） | 无写操作；签发新 token（auth.py:172） | 幂等（滑动续租；无状态变更） | auth.py:160-174 |
| GET `/me` | 登录 | — | UserPublic + 附加 `access_level`（auth.py:186-187） | 404 `用户不存在`（auth.py:185） | 只读 | 幂等 | auth.py:177-187 |
| PATCH `/me` | 登录 | 原始 `body: dict`（**无 Pydantic 模型**）；仅允许字段白名单 `{name, avatar_url, settings}`，其余忽略（auth.py:197-198） | UserPublic | 404 `用户不存在`（auth.py:202） | 写 `UserStorage.update(user_id, updates)`（auth.py:200）；audit `USER_UPDATE`（auth.py:203-204） | 幂等（整体覆盖语义：重复同 body 结果一致） | auth.py:190-205 |

---

## 2. chat（前缀 `/api/v1/chat`，router 级依赖 require_full_access，chat.py:44-48）

> 两端点共用 `_run_chat`（chat.py:407-623）：会话存在性/归属校验 → L1 短期记忆历史加载（空则从存储回填）→ `create_initial_state` + astream_events 跑 LangGraph（含降级兜底 chat.py:509-530）→ 持久化 user/assistant 消息 → 压缩短期记忆 → 知识自迭代入库 → 返回。真实 user_id 一律取 JWT（chat.py:425 注释；请求体里的 user_id 字段被忽略【推断·待验证：`ChatRequest.user_id` 默认值仅建模使用，路由未读取】）。
- 请求体模型 `ChatRequest`（chat.py:98-110）：`message` str 必填 min_length=1；`conversation_id` str|None 可选（None=新建会话）；`user_id` str 默认 `"default"`（_DEFAULT_USER_ID，chat.py:51）；`skill` str 默认 ""（空/“通用助手”=普通聊天，否则按技能注入内置上下文 chat.py:436-439）。

| 端点 | 鉴权 | 请求字段（见上） | 响应 | 错误 | 副作用 | 幂等性/备注 | 证据 |
|---|---|---|---|---|---|---|---|
| POST `/`（非流式） | full(路由级) | ChatRequest | ChatResponse：`conversation_id`/`message`/`intent`/`intent_confidence`(默认0.0)/`metrics`(dict)/`trace_id`/`meta`（含 ingest_status/ingest_reason，chat.py:676-687、112-121） | HTTPException（404 会话不存在/归属不符 chat.py:450-454；500 兜底 chat.py:661-671）；工作流异常→降级文案而非 500（chat.py:509-530）；record_chat_error 埋点（chat.py:656/660/667） | 写会话 JSON（append_message user/assistant chat.py:560/576）；写/压缩 L1 短期记忆（chat.py:579-584）；await 知识自迭代入库 `knowledge_ingester.ingest_conversation`（chat.py:591-599，异常仅记录并返回 ingest_status=error）；skill=应聘助手 时后台 `asyncio.create_task` 偏好抽取写用户画像（chat.py:546-547、393-404）；可能写用户画像（`<PREF>` 解析 chat.py:544、269-303） | **否**：每次调用追加一轮 user/assistant 消息并入库；同 conversation 重复发送=多轮对话 | chat.py:642-687 |
| POST `/stream`（SSE） | full(路由级) | ChatRequest | `StreamingResponse`（text/event-stream，chat.py:837-846）：SSE 事件 type ∈ thinking/token/done/error（done.meta 同非流式元数据，chat.py:813-826） | 并发防护：同一 `user|conversation_id` 已在流式则推 `error` 事件并结束（chat.py:711-719）；图运行异常→error 事件（chat.py:776-798） | 同 `/`（_run_chat 在后台 task 中执行，chat.py:751-753；真流式经 token sink 回传 chat.py:737-742、771） | **否**（同 `/`）；⚠️ 新会话（conversation_id 为空）时锁键为 `user|`，同用户并发开新会话流式会被互斥（chat.py:711） | chat.py:690-847 |

---

## 3. chat_share（前缀 `/api/v1/chat-share`，chat_share.py:31）

> 会话分享：创建/撤销仅所有者；浏览任意登录用户（分享须有效未过期）。

| 端点 | 鉴权 | 请求字段 | 响应 | 错误 | 副作用 | 幂等性/备注 | 证据 |
|---|---|---|---|---|---|---|---|
| POST ``（空路径，即 `/api/v1/chat-share`） | 登录 | CreateChatShareRequest：`conv_id` str 必填 min_length=1（chat_share.py:46） | 字典：share_id/title/share_url（`/share/chat/{share_id}`）/message_count/created_at（chat_share.py:135-141） | 500 存储未初始化（chat_share.py:56/97）；404 会话不存在（chat_share.py:101；非本人会话同样 404 不泄露存在性，102-104） | 只快照 user/assistant 且非空消息（chat_share.py:108-119）；写 `chat_share_storage.create_share`（chat_share.py:122-127） | **否**：每次调用新建一条分享记录 | chat_share.py:88-141 |
| GET `/{share_id}` | 登录 | 路径参数 share_id | 字典：share_id/title/owner_name/is_owner/permission/created_at/messages（chat_share.py:153-161） | 404 分享不存在或已撤销（chat_share.py:65）；403 分享已失效或过期（chat_share.py:67）；500 存储未初始化（chat_share.py:56） | 只读 | 幂等 | chat_share.py:144-161 |
| DELETE `/{share_id}` | 登录 | 路径参数 share_id | `{"share_id", "deleted"}`（chat_share.py:179） | 404 分享不存在（chat_share.py:174）；403 无权操作（非所有者，chat_share.py:176）；500 存储未初始化 | 写 `chat_share_storage.delete_share`（chat_share.py:178） | 重复删除→404（非幂等） | chat_share.py:164-179 |

---

## 4. conversations（前缀 `/api/v1/conversations`，router 级依赖 require_full_access，conversations.py:30-32）

> 会话 CRUD + 消息反馈；SEKBError→HTTP 映射集中在 `_handle_sekb_error`（conversations.py:66-83）：StorageError 含“不存在”→404，其余→500。

| 端点 | 鉴权 | 请求字段 | 响应 | 错误 | 副作用 | 幂等性/备注 | 证据 |
|---|---|---|---|---|---|---|---|
| GET ``（列表） | full(路由级) | Query：`limit` int 默认50 ge1 le200（conversations.py:93）；`offset` int 默认0 ge0（94） | `storage.list_conversations` 原始列表（会话 dict 数组，conversations.py:107） | 500 存储未初始化（100）；SEKBError 映射（104-105） | 只读 | 幂等 | conversations.py:90-107 |
| POST ``（创建） | full(路由级) | Query 参数：`title` str 默认 `"新会话"`（**函数裸参数，FastAPI 视为 query**，conversations.py:113） | 新建会话 dict（含 conv_id 兜底，conversations.py:126-128） | 500 存储未初始化（118-119）；SEKBError 映射 | 写 `storage.create_conversation`（122） | **否**：每次调用新建会话 | conversations.py:110-128 |
| GET `/{conv_id}` | full(路由级) | 路径参数 conv_id | `{"conversation": conv, "messages": [...]}`（157） | 404 会话不存在（147-148；非本人同样 404，149-150）；500（100/映射） | 只读 | 幂等 | conversations.py:131-157 |
| GET `/{conv_id}/messages` | full(路由级) | 路径参数 conv_id | 消息数组（186） | 404 会话不存在/非本人（176-179）；500 | 只读 | 幂等 | conversations.py:160-186 |
| PATCH `/{conv_id}` | full(路由级) | UpdateConversationRequest：`title` str|None 默认None min1 max200；`pinned` bool|None 默认None（39-43） | 更新后会话 dict（223） | 404 会话不存在/非本人（206-209）；500 | 写 `storage.update_conversation`（218） | 幂等（部分字段更新；重复同 body 结果一致） | conversations.py:189-223 |
| DELETE `/{conv_id}` | full(路由级) | 路径参数 conv_id | `{"conv_id", "deleted": True}`（253） | 404 会话不存在/非本人（242-245）；500 | 写 `storage.delete_conversation`（248） | 首次删除后重复→404（非幂等） | conversations.py:226-253 |
| POST `/{conv_id}/rate` | full(路由级) | RateRequest：`msg_id` str 必填；`rating` str 必填（thumbs_up/thumbs_down，文档注释）；`comment` str|None 默认None max1000（46-51） | `{conv_id, msg_id, rating, feedback_id, recorded: true}`（303-308） | 404 会话不存在/非本人（273-276）；404 消息不存在（285-289）；500 | 以 role=`feedback` 消息追加 `storage.append_message`（292-299） | **否**：每次调用新增一条 feedback 消息 | conversations.py:256-308 |

---

## 5. health（前缀 `/api/v1/health`，health.py:25）

| 端点 | 鉴权 | 请求字段 | 响应 | 错误 | 副作用 | 幂等性/备注 | 证据 |
|---|---|---|---|---|---|---|---|
| GET `/` | open | — | `{status: ok\|degraded, services: {llm,tools,storage,graph}, app:{name,version,environment}}`（health.py:88-96） | 无 HTTPException（子系统检查全部 try/except 吞掉，health.py:42-86） | 更新 Prometheus 健康指标 `service_health`/`service_subsystem_health`（health.py:79-85，失败静默）；调用各子系统 health_check（llm_factory.health_check health.py:43、tool_registry.health_check 51、storage 自检 63、graph 判空 71） | 幂等；storage 检查以 `user_id="__health_check__"` 只读列表（63） | health.py:28-96 |
| GET `/live` | open | — | `{"status": "ok"}`（109） | 无 | 无 | 幂等（liveness 不依赖外部） | health.py:99-109 |
| GET `/ready` | open | — | `{status: ok\|not_ready, checks:{llm,graph}}`；200 或 503 JSONResponse（132-134） | 非 200 时用 503 响应而非 HTTPException | 只读探针（llm health_check + graph 非空，126-130） | 幂等 | health.py:112-135 |

---

## 6. job（前缀 `/api/v1/job`，router 级依赖 require_full_access，job.py:22-24）

> 所有写路径先经 `_require_job_agent`（job.py:46-51）：`ctx.job_agent is None` → 503 `招聘分析未启用（job.enabled=false）`。缓存键均含 user_id（cache_key），数据按用户隔离。浏览器服务固定 `http://browser:1300`（job.py:143）。

| 端点 | 鉴权 | 请求字段 | 响应 | 错误 | 副作用 | 幂等性/备注 | 证据 |
|---|---|---|---|---|---|---|---|
| POST `/analyze` | full(路由级) | JobAnalyzeRequest：`jd_text` str 必填 min_length=1（job.py:30）；`job_meta` dict|None 可选（31-33） | 分析结果 dict + `cached: bool`（67/79） | 503 未启用（50）；400 jd_text 空（62）或 ValueError（81）；500 分析失败（84） | 写 14 天分析缓存 `save_analysis`（71）；写历史存档 `save_report(user_id,"single",…)`（78）；内部 LLM 多次调用 | 缓存命中直接返回（cached:true）；未命中重新分析并覆盖同输入缓存（save_analysis 覆盖语义）；同 (user,jd) 每 14 天只重算一次 | job.py:54-84 |
| POST `/fetch` | full(路由级) | JobFetchRequest：`keyword` str 默认""（空→cfg.default_keyword，job.py:96）；`city` str 默认""（“不限/全部/全国”→空串=不过滤，98-100）；`min_salary_k` int 默认0 ge0；`page` int 默认0 ge0；`limit` int 默认20 ge1 le40（39-43） | `{keyword, city, min_salary_k, source_count, sources, count, jobs, cached}`（107-116 / 131-140） | 503 未启用（91）；500 采集失败（127） | 写 14 天职位缓存 `save_cached_jobs(key, jobs)`（129）；调用外部采集（collector.fetch_all，124） | 缓存命中直接返回；未命中重新采集并覆盖缓存 | job.py:87-140 |
| POST `/boss/qr/start` | full(路由级) | 无请求体 | 转发自浏览器服务（含 qr data URL + qr_id，结构不透传校验） | 503 未启用；500 启动失败（168） | 无本地写；POST `http://browser:1300/login/qr/start` `{"site":"boss"}`（165） | 每次调用在浏览器服务侧新建扫码会话【推断·待验证：取决于外部服务】 | job.py:160-168、143-153 |
| POST `/boss/qr/status` | full(路由级) | BossQrStatusReq：`qr_id` str 必填 min1（157） | 转发自浏览器服务（phase/二维码/Cookie） | 503 未启用；500 查询失败（181） | 无本地写；POST `…/login/qr/status`（176-178） | 轮询接口；多次调用推进外部状态机【推断·待验证】 | job.py:171-181 |
| POST `/batch-analyze` | full(路由级) | BatchAnalyzeReq：`keyword` str 默认""（空→cfg.default_keyword，209）；`city` str 默认""（空→cfg.default_city，210）；`force` bool 默认 False（189）；`jobs` list[dict]|None 默认None（190-192） | analyze_market 结果 dict（含 report） | 503 未启用；500 批量分析失败（232） | `force=true` 先 `delete_report`（214）；写市场报告缓存（7 天，docstring job.py:201-202）；写历史存档 `save_report(user_id,"batch",…)`（228） | `jobs` 为空且 force=false 时 7 天缓存命中不重复分析（docstring 200-202）；force=true 强制重算；jobs 提供时直接分析传入列表不采集不缓存（docstring 200） | job.py:195-232 |
| DELETE `/batch-analyze` | full(路由级) | 无 | `{deleted: bool}`（242） | 503 未启用 | 写删除：`delete_report(user_id)`（241） | 重复删除返回 deleted=false（不报错） | job.py:235-242 |
| GET `/batch-analyze/cached` | full(路由级) | 无 | `{cached: bool, report: dict\|None}`（252） | 503 未启用 | 只读 | 幂等 | job.py:245-252 |
| POST `/import` | full(路由级) | multipart：`files: list[UploadFile]` 必填（File(...)，257） | `{jobs: [...], count}`；每职位含 jd_text（文件名去扩展名为 title，300-310） | 503 未启用 | 解析 .txt/.md/.markdown/.pdf/.docx（临时文件，用完 unlink，276-297）；**无持久化写**；无 LLM 调用 | 幂等（纯解析返回，无写库） | job.py:255-310 |
| POST `/refresh` | full(路由级) | RefreshJobReq：`job_url` str 默认""；`source` str 默认""（316-317） | `{jd_text, refreshed: bool}`（327） | 503 未启用；fetcher 异常未捕获→500（无显式 HTTPException 分支）【推断·待验证：异常由全局兜底处理】 | 重新抓取外部职位详情（refresh_job_jd，326）；不写缓存（docstring 330-331 提示配合 `/cache/save`） | 每次调用重新请求外部源（非幂等效果） | job.py:320-327 |
| POST `/cache/save` | full(路由级) | SaveCacheReq：`keyword` str 默认""（空→"Agent"，345）；`city` str 默认""；`min_salary_k` int 默认0 ge0；`jobs` list[dict] 默认 []（333-336） | `{saved: len(jobs)}`（351） | 503 未启用 | 覆盖写职位缓存 `save_cached_jobs`（350） | 幂等（整体覆盖语义） | job.py:339-351 |
| GET `/cache/latest` | full(路由级) | 无 | `{cached, keyword, city, min_salary_k, jobs, count}`（无则全空/0，362-363） | 503 未启用 | 只读 | 幂等 | job.py:354-363 |
| GET `/reports` | full(路由级) | 无 | `{reports: [...]}` 元信息列表（372） | 503 未启用 | 只读 | 幂等 | job.py:366-372 |
| GET `/reports/{report_id}` | full(路由级) | 路径参数 report_id | 报告全量内容（384） | 404 报告不存在（383）；503 未启用 | 只读 | 幂等 | job.py:375-384 |
| DELETE `/reports/{report_id}` | full(路由级) | 路径参数 report_id | `{deleted: bool}`（394） | 503 未启用 | 写删除存档（393） | 重复删除返回 deleted=false | job.py:387-394 |

---

## 7. knowledge（前缀 `/api/v1/knowledge`，router 级依赖 require_full_access，knowledge.py:27-29）

| 端点 | 鉴权 | 请求字段 | 响应 | 错误 | 副作用 | 幂等性/备注 | 证据 |
|---|---|---|---|---|---|---|---|
| GET `/categories` | full(路由级)（**无 get_current_user**，不绑定用户） | — | `{tree, raw}`（静态分类目录，66） | 无 | 只读 | 幂等 | knowledge.py:63-66 |
| GET `/categories/stats` | full(路由级)+登录 | — | `{stats: [{category_l1,l2,l3,count}], total}`（111）；异常返回 `{stats: []}`（94-95） | 无 HTTPException（异常吞掉返回空） | 只读（分页仅读元数据 include=["metadatas"]，86-88） | 幂等 | knowledge.py:69-111 |
| GET ``（列表） | full(路由级)+登录 | Query：`source`/`category_l1/2/3` str|None 可选；`page` int 默认1 ge1；`page_size` int 默认20 ge1 le100（117-122） | `{entries:[…], total, page, page_size}`；条目 content 截断 200 字符+“...”（44-45，154-159）；异常时 entries=[] total=0（149-153） | 无 HTTPException（异常吞掉返回空列表） | 只读 | 幂等 | knowledge.py:114-159 |
| GET `/search` | full(路由级)+登录 | Query：`q` str 必填 min1（164）；`top_k` int 默认10 ge1 le50（166） | `{entries:[含 similarity_score], total}`（180-183）；异常 entries=[]（176-178） | 无 HTTPException | 只读（kb.retrieve min_score=0.0，175） | 幂等 | knowledge.py:162-183 |
| DELETE `/{entry_id}` | full(路由级)+登录 | 路径参数 entry_id | `{status:"ok", entry_id}`（213） | 404 知识库未启用（195）；500 查询失败（201）；404 条目不存在（204）；404 条目不存在或无权操作（非所有者 208）；500 删除失败（216） | 写删除 `kb.delete(entry_id)`（211） | 重复删除第二次→404（非幂等） | knowledge.py:186-216 |
| PATCH `/{entry_id}/category` | full(路由级)+登录 | ReclassifyRequest（body）：`category_l1/l2/l3` str 均必填（226-228） | `{status:"ok", entry_id, l1, l2, l3, category_source:"manual"}`（284-291） | 400 分类不在合法目录（247）；500 查询失败（253）；404 条目不存在（256）；404 条目不存在或无权操作（259）；500 更新失败（282）；404 知识库未启用（243） | 写 `kb.update` 元数据 category_l1/2/3 + confidence=1.0 + source="manual"（263-272） | 幂等（同值重复 PATCH 结果一致） | knowledge.py:231-291 |

---

## 8. metrics（无前缀，metrics.py:20）

| 端点 | 鉴权 | 请求字段 | 响应 | 错误 | 副作用 | 幂等性/备注 | 证据 |
|---|---|---|---|---|---|---|---|
| GET `/metrics` | open（注释建议生产以网络策略限制，metrics.py:10-11） | — | Prometheus 文本格式 `text/plain; version=0.0.4`（34-37） | 无 | 只读（get_metrics() 导出当前进程指标） | 幂等（指标随埋点累计） | metrics.py:23-37 |

---

## 9. monitoring（前缀 `/api/v1/monitoring`，monitoring.py:29）

| 端点 | 鉴权 | 请求字段 | 响应 | 错误 | 副作用 | 幂等性/备注 | 证据 |
|---|---|---|---|---|---|---|---|
| POST `/client-event`（固定 202） | open（兼容未登录 login_submit_failed 上报，注释 monitoring.py:8-9） | ClientEventBatch：`events: list[ClientEvent]`（44-47）；ClientEvent：`ts` str|None 可选、`level` str 默认"info"、`event` str 必填、`fields` dict|None 可选（35-41）；单批上限 50 条，超出截断前 50（32、64-68） | `{status:"ok", accepted, rejected, total}`（95-100） | 无 HTTPException（单条处理失败计 rejected 并日志，80-88；整体恒 202 防前端重试风暴，注释 monitoring.py:12） | 每条事件经 `record_client_event` 写 Prometheus 指标（74-78）；`client_event_reports_total{status=accepted/rejected}` 计数（91-93） | **否**（指标按事件累加，重复上报重复计数；无去重） | monitoring.py:50-100 |

---

## 10. news（前缀 `/api/v1/news`，news.py:23；router **无**路由级依赖）

> `_require_news_agent`：未启用→503（news.py:26-31）。只读查看端点仅需登录（预览账号可读，docstring 52/62/103）；写端点（refresh / 周期生成）要求 full 权限。
> 注意 `/reports`、`/report` 为字面量路径，先于 `/{report_type}` 匹配（注册顺序 50/57 在 75/97 之前），`{report_type}` 仅接受 weekly/monthly。

| 端点 | 鉴权 | 请求字段 | 响应 | 错误 | 副作用 | 幂等性/备注 | 证据 |
|---|---|---|---|---|---|---|---|
| POST `/refresh` | full(handler)（news.py:37） | body dict 可选：`force` bool 默认 True（41）；false=今日已生成则跳过 | agent.refresh(force) 结果 dict（47） | 503 未启用（30）；500 日报生成失败（46） | 触发完整日报流水线：RSS/网页采集→筛选→正文抽取→LLM 生成→写日报 Markdown 到 news.report_dir（service.py:52-76、116-149）；跨进程锁文件 .lock_daily（service.py:78-97） | force=false 当日已生成则跳过（幂等，service.py:60-62）；force=true 重新生成覆盖【推断·待验证：日报写回是否覆盖，依据 news/storage.py 未读】 | news.py:34-47 |
| GET `/reports` | 登录 | — | `{reports: [...]}` 日报元信息（54） | 503 未启用 | 只读 | 幂等 | news.py:50-54 |
| GET `/report` | 登录 | Query：`date` str|None（YYYY-MM-DD，缺省取最新，59） | 日报 Markdown 全文（72） | 404 日报不存在:date（67）；404 暂无日报（71）；503 未启用 | 只读 | 幂等 | news.py:57-72 |
| POST `/{report_type}` | full(handler)（79） | 路径参数 report_type（仅 weekly/monthly）；body dict 可选：`period`、`supplement`（85-90） | agent.generate_periodic 结果（91） | 400 report_type 只支持 weekly 或 monthly（83）；503 未启用；500 周期报告生成失败（94） | 触发周报/月报生成与存储（service.generate_periodic） | 重复调用行为取决于 storage 覆盖/跳过逻辑【推断·待验证】 | news.py:75-94 |
| GET `/{report_type}` | 登录 | 路径参数 report_type；Query：`period` str|None（100） | period 给定→该期全文（109-111）；否则 `{reports:[...]}` 列表（112） | 400 非法 report_type（105）；404 报告不存在:type/period（110）；503 未启用 | 只读 | 幂等 | news.py:97-112 |

---

## 11. profile（前缀 `/api/v1/profile`，profile.py:22；模块级单例 `ProfileStorage("data/profile")`，profile.py:25）

| 端点 | 鉴权 | 请求字段 | 响应 | 错误 | 副作用 | 幂等性/备注 | 证据 |
|---|---|---|---|---|---|---|---|
| GET `` | 登录 | — | UserProfile（profile.py:35-44）；无画像→返回默认空画像（35-36） | 无显式 HTTPException | 只读 | 幂等 | profile.py:28-37 |
| PUT `` | 登录 | ProfileUpdate（body）：`bio` str|None、`skills` list[str]|None、`career_goal` str|None、`job_preferences` JobPreferences|None（target_roles/target_cities/min_salary_k/company_types/keywords，profile.py:23-30）、`news_interests` list[str]|None——全可选（45-52） | UserProfile | 无显式 HTTPException（存储异常走全局兜底→500） | 写 `ProfileStorage.upsert_update`（深合并，只合并非 None 字段，47-51）；logger（52） | 幂等（字段级合并覆盖；重复同 body 结果一致） | profile.py:40-53 |

---

## 12. share（前缀 `/api/v1/share`，router 级依赖 require_full_access，share.py:41-43）

> 分享知识库问答：检索/对话只读所有者知识库。共享 LLM 角色 `_CHAT_ROLE="chat_simple"`、`_RETRIEVE_TOP_K=5`、`_HISTORY_WINDOW=8`（share.py:56-60）。

| 端点 | 鉴权 | 请求字段 | 响应 | 错误 | 副作用 | 幂等性/备注 | 证据 |
|---|---|---|---|---|---|---|---|
| POST ``（创建） | full(路由级)+登录 | CreateShareRequest：`title` str 默认"" max100（70）；`category_l1/2/3` str 默认""（空=不限该级；全空=整库，72-74）；`expires_days` int 默认30（≤0 永不过期，76） | share_id/title/permission/category_*/category_label/created_at/share_url(`/sekb/share/{id}`)/entries_count（231-242） | 500 分享存储未初始化（92）；503 知识库未启用无法分享（182）；400 分类层级不完整（191/193）；400 分类无效（205）；400 所选范围没有内容（219）；404/403 由 _get_valid_share 内联（仅 GET 用） | 写 `share_storage.create_share`（221-228）；先 count_entries 校验范围非空（208-218） | **否**：每次调用新建 share_id | share.py:171-242 |
| GET ``（我的分享） | full(路由级)+登录 | — | `{shares:[…各分享+entries_count+is_active+has_expired], total}`（268-282） | 500 分享存储未初始化 | 只读（每个分享附带 count_entries 统计，258-266） | 幂等 | share.py:245-282 |
| GET `/{share_id}` | full(路由级)+登录 | 路径参数 share_id | share 详情 + owner_name/is_active/has_expired/entries_count/is_owner（306-320） | 404 分享不存在或已撤销（101）；403 分享已失效或过期（103）；500 存储未初始化 | 只读 | 幂等 | share.py:285-320 |
| DELETE `/{share_id}` | full(路由级)+登录 | 路径参数 share_id | `{share_id, deleted}`（338） | 404 分享不存在（333）；403 无权操作（非所有者 335） | 写 `share_storage.delete_share`（337） | 重复删除→404（非幂等） | share.py:323-338 |
| GET `/{share_id}/entries` | full(路由级)+登录 | Query：`category_l1/2/3` str|None（349-351）、`page` int 默认1 ge1、`page_size` int 默认20 ge1 le100（352-353） | `{entries:[…content 截断 300 字符…], total, page, page_size}`（402-407） | 404/403 分享校验（357）；知识库未启用→空列表（359-360） | 只读（按 share.owner_user_id 查询，371；分享限定分类时强制覆盖查询参数防越权，363-366） | 幂等 | share.py:345-407 |
| GET `/{share_id}/messages` | full(路由级)+登录 | 路径参数 share_id | `{messages:[...]}` 访问者会话历史（424） | 404/403 分享校验（421）；500 存储未初始化 | 只读 | 幂等 | share.py:414-424 |
| POST `/{share_id}/chat/stream` | full(路由级)+登录 | SharedChatRequest：`message` str 必填 min1（82） | StreamingResponse SSE：thinking/token/done/error 事件（done.meta 含 latency_ms/retrieved_count/share_id，527-528） | 404/403 分享校验（446）；503 知识库未启用（450）；流内错误→error 事件（530-536） | **写分享会话消息**：`share_storage.append_message` user 与 assistant（含 latency_ms，511-519）；RAG 检索 owner 知识库（464-472）→ LLM `astream`（role=chat_simple，498-505） | **否**：每次提问追加一轮 user/assistant | share.py:427-547 |

---

## 13. upload（前缀 `/api/v1/upload`，router 级依赖 require_full_access，upload.py:53-55）

> 文档/图片上传入库（L3 ChromaDB）。常量：文档源 `document`、图片源 `image`、默认重要性 0.5、单文件上限 50MB（413）、原图/文档落盘目录 `data/uploads/images|documents`、MD5 索引 `data/uploaded_md5.json`（upload.py:58-72）。L3 未启用（ctx.vector_store None）→ 503（_require_vector_store，198-213）。

| 端点 | 鉴权 | 请求字段 | 响应 | 错误 | 副作用 | 幂等性/备注 | 证据 |
|---|---|---|---|---|---|---|---|
| POST ``（上传） | full(路由级)+登录 | multipart `file: UploadFile` 必填（497）；Query `async_process` bool 默认 False（498-501）；Query `overwrite` bool 默认 False（502-505） | UploadResponse：file_name/file_size/chunks_count/ingested_count/status(success\|partial\|error)/error/entry_ids/category/series（161-176） | 413 文件过大（上限 50MB，526-530）；503 L3 未启用（520）；500 SEKBError/兜底（595-606） | 同步或后台(BackgroundTasks `_background_ingest`，563-570)执行 解析→分块→逐块 `vector_store.add`（258-270）；保存原文件（图片→data/uploads/images/、文档→data/uploads/documents/，316-323）；LLM 自动分类（360、460-487）；系列识别（364）；成功后记录 SHA-256 到 md5 索引（591、98-100） | `overwrite=false`：**否**（重复上传新增重复条目；md5 仅返回给前端做“同名同内容”跳过，路由本身不跳过，810-814））；`overwrite=true`：先按 source_id 删除同名 document/image 旧条目再入库（304-307、406-422）≈替换语义 | upload.py:494-612 |
| GET `/status` | full(路由级)+登录 | — | KnowledgeBaseStatus：total_entries/l3_enabled/user_id（179-184；L3 未启用 total=0 l3_enabled=false，626-632） | 500 查询失败（638-641） | 只读（vector_store.count(user_id)，635） | 幂等 | upload.py:615-647 |
| DELETE `/entries/{entry_id}` | full(路由级)+登录 | 路径参数 entry_id | DeleteEntryResponse：entry_id/deleted（187-191） | 503 L3 未启用（662）；500 查询/删除失败（669-672、701-704）；404 条目不存在或无权操作（非所有者 687-690） | 写删除 `vector_store.kb.delete`（693） | 幂等：条目不存在→deleted=True 返回成功（674-677 注释“幂等语义”） | upload.py:650-707 |
| GET `/series` | full(路由级)+登录 | — | `{series: [{series, category, count, files:[{file_name, part}]}]}`（763） | 503 L3 未启用；500 查询失败（731-734） | 只读（document 源按 series 归组，726-762） | 幂等 | upload.py:710-763 |
| GET `/files` | full(路由级)+登录 | — | `{files:[{file_name, source, chunk_count, category, series, uploaded_at, md5}], count}`（808-815） | 503 L3 未启用；500 查询失败（781-784） | 只读（按 source_id 归组 + 读 md5 索引，810-814） | 幂等 | upload.py:766-815 |
| POST `/reclassify` | full(路由级)+登录 | 无 | `{files_reclassified, entries_updated}`（868） | 503 L3 未启用；500 查询失败（837-840）；更新单个条目失败仅 warning 不中断（863-864） | 对全部 document 文件逐个重跑 LLM 分类（849）并批量更新元数据 category_*+series（852-862） | 幂等（重算并覆盖元数据；每次会消耗 LLM 调用） | upload.py:818-868 |
| POST `/re-series` | full(路由级)+登录 | 无 | `{files_re_series, entries_updated}`（932） | 503 L3 未启用；500 查询失败（901-904）；500 批量更新失败（926-929） | 分页拉全量 document（887-898）；detect_series 重算（913-917）并 `update_metadata_batch`（923） | 幂等（重算覆盖 series；轻量无 LLM） | upload.py:871-932 |
| POST `/analyze` | full(路由级)+登录 | 无 | `{total_entries, document_files, category_distribution, files(前200), overview}`（974-982） | 503 L3 未启用；500 查询失败（951-954） | 只读 + 可选 LLM：统计分类分布（957-962）；`_generate_kb_overview` 用 role `job_analysis` 生成概览（970、1000-1004，失败降级空串 969-972） | 幂等（纯分析；概览每次重算） | upload.py:935-1006 |

---

## 14. 异常与漂移清单（T1 域）

1. **无鉴权端点（6 个）**：health 3 个（`/api/v1/health/`、`/live`、`/ready`）、metrics `/metrics`、monitoring `/client-event`、另有 auth `/register`+`/login` 开放属设计（登录/注册必须开放）。其中 `/client-event` 与 `/metrics` 接受任意来源请求（有 Nginx/网络层保护建议注释：metrics.py:10-11、monitoring.py:8-9）。
2. **限流已启用（双闸）**：`RateLimitMiddleware` 已挂载生效（server.py:204-219，`enabled: true`），按路由分组限流（auth 5 / job 30 / news 读写分离 6/120 / 默认 60）；另有 nginx 层 `api_limit/news_limit/client_event_limit`。
3. **POST 幂等性小结**：绝大多数写 POST 非幂等（register、chat、chat_stream、rate、create_share、create_chat_share、upload(overwrite=false)、monitoring/client-event、job/analyze 与 fetch 与 batch-analyze 依赖 14/7 天缓存近似幂等）；upload `overwrite=true`、job/cache/save 为覆盖式（近似幂等）；news refresh force=false 当日跳过（幂等）、force=true 覆盖；DELETE/删除类多数重复调用返回 404 或 deleted=false。
4. **chat 会话并发防护为进程内集合**（`_conv_inflight`，chat.py:54、711-719）：多 worker 部署下不能跨进程互斥【推断·待验证：生产 worker 数未知】；且锁键对新会话（conversation_id 空）退化到 `user|`，同用户并发新建会话互斥。
5. **文档/注释漂移**：create_jwt docstring 写「72 小时」，实际读 config 2160h=90 天（auth.py:68 vs 71）；server.py:285-287 路由注册日志缺 profile；share.py:138-141 中 `_HISTORY_WINDOW*2` 截断实现（注释与实现一致，无漂移）。chat.py:586 注释「不阻塞主回复」但代码为 `await` 同步执行（chat.py:591）——知识入库实际在返回前同步等待（仅失败被捕获），表述与实现有出入【标注现状】。
6. auth 使用 `_get_user_storage()` 每次经 `get_app_context()` 现取（auth.py:32-38）；`LoginRequest`/`RegisterRequest` 约束集中在 user.py:42-52。
