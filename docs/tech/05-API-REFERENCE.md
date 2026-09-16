---
title: 接口参考（API Reference）
layer: 参考层
owner: SEKB Team
status: active
version: v1.0.0
last-updated: 2026-09-16
based-on-commit: 44dfed1
related: [02-RUNTIME-FLOWS, 08-GLOSSARY]
---

# 05 · 接口参考（API-REFERENCE）

> **本文回答什么问题**：对外有哪些接口？每个端点怎么调、返回什么、有什么副作用？
> **适合谁读**：前端开发者、接口调用方、后端新成员。
> **读完能做什么**：能对着任意端点写出 curl；理解 SSE 事件协议与 CLI 命令；定位错误处理。

> **完整证据**：端点的逐字段表见事实表 [.facts/T1-routes.md](./.facts/T1-routes.md)（带 file:line）。本文为可读手册，正文引用 T1 深表。
> ⚠️ 现状：实际 `@router` 端点共 **75 个**<!-- fact:api_endpoints=75 -->（auth 8 / chat 3 / chat_share 3 / conversations 7 / health 3 / job 20 / knowledge 6 / metrics 1 / monitoring 1 / news 6 / profile 2 / share 7 / upload 8）。T1 事实表仍按 65 个统计，未含 `/chat/usage`、`/news/status`、job 搜索历史与投递计划等新增端点，待同步（本文正文已逐条核对代码）。

---

## 1. 通用说明

### 1.1 基础
- Base URL：`/sekb/api/v1`（生产经 nginx 反代，nginx.conf `proxy_pass /api/`；本地 `http://localhost:8000/api/v1`）。
- 认证：`Authorization: Bearer <JWT>`。登录/注册开放；其余大多需登录；部分 router 需 full 访问（白名单）。
- 响应：统一 `{...}` JSON（成功无包层）；错误经全局异常处理器。

### 1.2 鉴权与访问级别
| 级别 | 含义 | 来源 |
|------|------|------|
| open | 无需登录（register/login/reset-password/health/metrics/monitoring） | T1 §0 |
| 登录 | 任意有效 JWT | `get_current_user`（auth.py） |
| full | 白名单完整用户（`require_full_access` 路由级） | app/core/access.py |
| preview | 白名单外用户：仅功能说明+资讯只读 | 前端菜单/后端路由双收窄 |

- JWT：HS256，**7 天**有效期（`token_expire_hours: 168`，`backend/config.yaml:302`；代码默认 `config.py:312` 为 2160h=90 天，以配置为准，`create_jwt` 读配置：auth.py:80-90）、滑动续租（`/auth/refresh`）；JWT 密钥取环境变量 `JWT_SECRET`（auth.py:40，启动强校验）。
- ALLOWED_EMAILS：逗号分隔白名单（`.env.prod`，脱敏）。

### 1.3 限流（现状：**ASGI 中间件已生效** + nginx 两道闸）

> ⚠️ 2026-09-15 修正：本节此前写「全局限流中间件未挂载 / 所有端点无生效限流」是**过期**信息
> （`enabled: true` 自 2026-09-10 起）。照旧文档排查会把资讯 429 **误判成只有 nginx 一个原因**，
> 而真实主因在应用侧（见下面 news 读/写分离）。改限流行为前先看这张表。

- `RateLimitMiddleware` 已挂载（`server.py:204-219`），开关 `api.rate_limit.enabled`（`backend/config.yaml` 为 `true`）。
- 粒度：`(客户端 IP, 路由组)` 的 **60 秒滑动窗口**；超限返回 `429` + `Retry-After: 60` +
  `X-RateLimit-Limit/Remaining`。路由组按**最长前缀**匹配，键可写成 `"POST /api/v1/news/"`
  这种**带方法**的形式（带方法的组优先于同前缀的纯前缀组）。
- nginx 是第一道闸：`api_limit 10r/s burst 30`（`/sekb/api/`）、`news_limit 30r/s burst 60`
  （`/sekb/api/v1/news/`）；已显式 `limit_req_status 429`（默认是 `503`，会被前端显示成
  「服务不可用」，与「请求过于频繁」是完全不同的用户结论）。
- 客户端事件上报另有 `client_event_limit`（nginx，2r/s + burst 10）。

| 路由组（键） | 额度（次/分钟） | 配置项 | 说明 |
|---|---|---|---|
| `/api/v1/auth/` | 5 | `auth.rate_limit_login_per_minute` | 登录，防枚举 |
| `/api/v1/share/` | 20 | `rate_limit.share_per_minute` | 公开分享链接 |
| `/api/v1/upload` | 30 | `rate_limit.upload_per_minute` | 文件上传 |
| `/api/v1/job/` | 30 | `rate_limit.job_per_minute` | 招聘采集/批量分析 |
| `POST /api/v1/news/` | 6 | `rate_limit.news_generate_per_minute` | **资讯生成**（真调 LLM） |
| `/api/v1/news/` | 120 | `rate_limit.news_per_minute` | 资讯读取（列表/正文/`status` 轮询） |
| 其它 `/api/` | 60 | `rate_limit.requests_per_minute` | 默认 |

> **为什么资讯读写必须分两条**：前端在生成期间每 15s 轮询 `/news/status`，切 tab 还要读
> 列表 + 多篇正文；原来整组只有 10/分钟（配置注释写的是「资讯刷新成本高」，但前缀把读也覆盖了），
> 于是用户翻两下页面就 429 —— owner 报的「加载周期报告列表失败 / 点第二次就失败」即此。

### 1.4 错误映射（全局异常处理器）
| HTTP | 条件 | 证据 |
|------|------|------|
| 400 | 业务校验失败（如邮箱已注册） | 各路由 `HTTPException(400)`，经 server.py:252-253 原样透传 |
| 401 | 未认证 / 密码错（防枚举统一文案） | auth.py |
| 403 | 无权限（preview 访问 full 资源）/ 分享过期 | access.py；T1 |
| 404 | 资源不存在 / 会话归属不符（不泄露存在性） | 多处 |
| 429 | LLM rate limit / budget 超限（SEKBError 映射）/ 限流中间件 | server.py:75-106；§1.3 |
| 500 | 其它异常（响应只含脱敏 `error_id`） | server.py:244-267（HTTPException 5xx 脱敏）；server.py:270-294（未处理异常兜底） |

> **5xx 出口脱敏**：全局 `HTTPException` 处理器（server.py:244-267）对 `status_code >= 500` 统一返回
> `{"detail":"服务内部错误，请稍后重试","error_id":"<12位hex>"}`，不再回显原始异常文本（路径/表名等）；
> **4xx 原样透传** `{"detail": <原 detail>}`。未处理异常兜底见 server.py:270-294（`error`/`error_id`/`message`）。
> `SEKBError` 子类走 server.py:223-241，返回 `{"error","message","details"}`（与 HTTPException 两种 body 不同）。

---

## 2. auth（`/api/v1/auth`）

| 端点 | 鉴权 | 请求 | 响应 | 说明 | 证据 |
|------|------|------|------|------|------|
| POST `/register` | open | RegisterRequest(email, password≥8, name?) | LoginResponse(user, token) | 写 UserStorage+audit；邮箱已存在→400 | auth.py:46-93 |
| POST `/login` | open | LoginRequest(email, password) | LoginResponse | 只审计+指标，无写；错→401 统一文案 | auth.py:96-153 |
| POST `/logout` | 登录 | — | {status:ok} | 吊销当前 token 的 jti（即时失效）+audit | auth.py:156-168 |
| POST `/change-password` | 登录 | ChangePasswordRequest(old_password, new_password≥8) | {status:ok} | 校验原密码；错→400「原密码错误」 | auth.py:171-191 |
| POST `/reset-password` | open | ResetPasswordRequest(email, old_password, new_password≥8) | {status:ok} | **必须带原密码**；未知邮箱或原密码错→401「邮箱或原密码不正确」（防枚举） | auth.py:194-213 |
| POST `/refresh` | 登录 | — | LoginResponse | 滑动续租新 JWT（7 天） | auth.py:216-230 |
| GET `/me` | 登录 | — | UserPublic(+access_level) | 只读 | auth.py:233-243 |
| PATCH `/me` | 登录 | 原始 body，白名单 {name,avatar_url,settings} | UserPublic | 写 UserStorage.update+audit | auth.py:246-261 |

**RegisterRequest 字段**：email（正则校验）、password（8-128）、name（默认""，≤50）（models/user.py:42-46）。
**ChangePasswordRequest**：old_password（1-128）、new_password（8-128）（models/user.py:55-58）。
**ResetPasswordRequest**：email（正则）、old_password（1-128）、new_password（8-128）（models/user.py:61-69）。
**UserPublic 字段**：user_id/email/name/avatar_url/created_at/is_active/settings/access_level（user.py:29-39）。
**前端调用方**：`frontend/src/services/auth.ts` → Login/Register/Settings 页。
**curl 示例**：
```bash
curl -X POST https://bos-studio.tech/sekb/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"<REDACTED>","password":"<REDACTED>"}'
```

---

## 3. chat（`/api/v1/chat`，router 级 require_full_access）

> 两端点共用 `_run_chat`（chat.py:108-378）。请求体 `ChatRequest`：`message` 必填（min_length=1）、`conversation_id`?（None=新建）、`user_id`（被忽略，真实取 JWT）（chat.py:79-86）。
**`skill` 字段已移除**（2026-09，skill 模式整体删除）；未知字段按 Pydantic 默认被忽略。

| 端点 | 请求/响应 | 说明 | 证据 |
|------|-----------|------|------|
| POST `/` | ChatRequest → ChatResponse | 非流式；跑完整 LangGraph 后一次返回 | chat.py:419-466 |
| POST `/stream` | ChatRequest → SSE | 真流式（thinking/token/done）；同会话并发互斥 | chat.py:469-632 |
| GET `/usage` | ?conversation_id | {enabled, conversation, total} | 会话/用户累计 token 与费用；带 conversation_id 且非本人会话→404；Redis 未启用 enabled=false | chat.py:649-675 |

**ChatResponse**：conversation_id/message/intent/intent_confidence/metrics/trace_id/meta{title,ingest_status,ingest_reason}/degraded（chat.py:89-99）。
**副作用**：写会话 JSON（append_message）+ L1 记忆（add_message/compress）+ await 知识入库（chat.py:286-344）。
**SSE 协议**详见 §13。
**前端调用方**：`frontend/src/services/chat.ts` streamChat；`stores/chat.ts`。

---

## 4. chat_share（`/api/v1/chat-share`）

| 端点 | 鉴权 | 请求 | 响应 | 说明 | 证据 |
|------|------|------|------|------|------|
| POST `` | 登录 | CreateChatShareRequest(conv_id) | share_id/title/share_url/message_count/created_at | 快照非空 user/assistant 消息；非本人会话也 404 | chat_share.py:67-120 |
| GET `/{share_id}` | 登录 | — | share 详情+messages | 分享有效未过期才可读 | chat_share.py:123-140 |
| DELETE `/{share_id}` | 登录 | — | {share_id,deleted} | 仅所有者（否则 403） | chat_share.py:143-158 |

---

## 5. conversations（`/api/v1/conversations`，router 级 require_full_access）

| 端点 | 鉴权 | 请求 | 响应 | 说明 | 证据 |
|------|------|------|------|------|------|
| GET `` | full | limit（默认50，1-200）/offset（≥0） | Conversation[] | 按 user 过滤、不含 deleted、pinned 优先 | conversations.py:90-107 |
| POST `` | full | title（**查询参数**，默认"新会话"） | Conversation | 新建会话 | conversations.py:110-128 |
| GET `/{conv_id}` | full | — | {conversation, messages} | 404 不存在/非本人 | conversations.py:131-157 |
| PATCH `/{conv_id}` | full | UpdateConversationRequest{title?(1-200),pinned?} | Conversation | 重命名/置顶 | conversations.py:189-223 |
| DELETE `/{conv_id}` | full | — | {conv_id, deleted:true} | 删会话+清消息 | conversations.py:226-253 |
| GET `/{conv_id}/messages` | full | —（无 limit 参数） | Message[] | 取历史 | conversations.py:160-186 |
| POST `/{conv_id}/rate` | full | RateRequest(msg_id, rating thumbs_up/down, comment?≤1000) | 反馈记录+flywheel | 消息不存在→404；thumbs 反馈驱动知识条目重要性 | conversations.py:256-327 |

**Conversation**（`ConversationMeta`）：conv_id/user_id/title/status/pinned/created_at/updated_at/message_count/total_input_tokens/total_output_tokens/total_cost_usd（backend/app/storage/base.py:33-52）。
**前端调用方**：`stores/chat.ts`、侧边栏/历史。

---

## 6. health / metrics / monitoring

| 端点 | 鉴权 | 说明 | 证据 |
|------|------|------|------|
| GET `/api/v1/health/` | open | 综合健康（LLM/工具/存储/graph） | health.py |
| GET `/api/v1/health/live` | open | 存活探针 | health.py |
| GET `/api/v1/health/ready` | open | 就绪探针 | health.py |
| GET `/metrics` | open | Prometheus 指标（23 个，见 09） | metrics.py |
| POST `/api/v1/monitoring/client-event` | open | 前端事件上报（独立限流） | monitoring.py |

---

## 7. knowledge（`/api/v1/knowledge`，路由级 require_full_access）

| 端点 | 鉴权 | 请求 | 响应 | 副作用/幂等 | 证据 |
|------|------|------|------|------------|------|
| GET `/categories` | full（路由级） | — | {tree, raw} 静态分类 | 只读（无用户绑定，但仍受路由级 full 依赖约束） | knowledge.py:55-58 |
| GET `/categories/stats` | full | — | {stats[], total} | 只读（异常吞掉返回 `{"stats":[]}`） | knowledge.py:61-103 |
| GET `` | full | source/category_l1/2/3/page(≥1)/page_size(默认20,1-100) | {entries(截200字), total, page, page_size} | 只读 | knowledge.py:106-151 |
| GET `/search` | full | q(必填,≥1)/top_k(默认10,1-50) | {entries(含similarity_score), total} | 只读 min_score=0.0 | knowledge.py:154-175 |
| DELETE `/{entry_id}` | full | — | {status,entry_id} | 删条目；不存在/非本人→404 | knowledge.py:178-208 |
| PATCH `/{entry_id}/category` | full | ReclassifyRequest{category_l1,category_l2,category_l3（均必填）} | {status,entry_id,category_l1/2/3,category_source:"manual"} | 覆盖元数据；非法分类→400 | knowledge.py:215-283 |

**前端调用方**：Knowledge 页、分享设置、Job 分析联动。

---

## 8. job（`/api/v1/job`，路由级 require_full_access）

> 所有写路径先 `_require_job_agent`（未启用→503）。缓存键均含 user_id 隔离。浏览器服务固定 `http://browser:1300`（browser_client.py:18）；设置环境变量 `BROWSER_INTERNAL_TOKEN` 后，后端对其所有请求带 `X-Internal-Token` 头（browser_client.py:20-31）。

| 端点 | 请求 | 说明 | 幂等 | 证据 |
|------|------|------|------|------|
| POST `/analyze` | {jd_text(必填), job_meta?} | 单 JD 深度分析；14 天缓存；自动存档 | 缓存命中跳过 | job.py:75-105 |
| POST `/fetch` | {keyword,city,min_salary_k(≥0),page(≥0),limit(默认20,1-40)} | 采集职位；14 天缓存 | 缓存命中跳过 | job.py:108-162 |
| POST `/boss/qr/start` | — | 转发浏览器服务扫码登录 | 每次新建会话 | job.py:169-177 |
| POST `/boss/qr/status` | {qr_id} | 轮询扫码状态 | — | job.py:180-190 |
| POST `/batch-analyze` | {keyword,city,force,jobs?,search_id,min_salary_k(≥0)} | 市场批量分析；14 天缓存；存档 | force 控制 | job.py:206-253 |
| DELETE `/batch-analyze` | ?search_id | 删批量报告（不传=删该用户全部） | 重删 false | job.py:256-269 |
| GET `/batch-analyze/cached` | ?search_id | 取缓存报告（14 天内），无则 report=None | 只读 | job.py:272-285 |
| GET `/searches` | — | 搜索历史（含 has_report/report_coverage/report_matched，过期惰性清理） | 只读 | job.py:293-319 |
| GET `/cache/search/{search_id}` | — | 取某次搜索的职位列表；不存在→404 | 只读 | job.py:352-361 |
| POST `/import` | multipart files | 解析 txt/md/pdf/docx 职位，无写库 | 幂等 | job.py:364-377 |
| POST `/refresh` | {job_url,source} | 重抓职位详情 | 每次重请求 | job.py:387-394 |
| POST `/cache/save` | {keyword,city,min_salary_k(≥0),jobs} | 覆盖职位缓存，返回 search_id | 覆盖 | job.py:406-421 |
| GET `/cache/latest` | — | 取最近缓存职位 | 只读 | job.py:424-441 |
| GET `/cache/list` | — | 全部未过期缓存职位集合（投递计划选填用） | 只读 | job.py:444-451 |
| GET `/reports` | — | 存档列表 | 只读 | job.py:454-460 |
| GET `/reports/{report_id}` | — | 报告全文；不存在→404 | 只读 | job.py:463-472 |
| DELETE `/reports/{report_id}` | — | 删报告（并同步清理对应缓存报告） | 重删 false | job.py:475-501 |
| GET `/apply-plan` | — | 投递计划列表 + 进度统计（含冷却倒计时） | 只读 | job.py:509-516 |
| POST `/apply-plan` | ApplyPlanReq{id?,company,title,tier,status,applied_at,result_at,cooldown_months(0-24),url,note}（**全字段可选，均有默认值**） | 新增/更新一条投递记录（带 id 则更新） | 带 id 幂等 | job.py:519-526 |
| DELETE `/apply-plan/{plan_id}` | — | 删一条投递记录 | 重删 false | job.py:529-536 |

**前端调用方**：Job 页（批量分析/单 JD/扫码采集）。

---

## 9. news（`/api/v1/news`，router 无路由级依赖）

> 只读端点仅需登录（preview 可读）；写端点（refresh/周期生成）要求 full。

| 端点 | 鉴权 | 请求 | 说明 | 幂等 | 证据 |
|------|------|------|------|------|------|
| POST `/refresh` | full | {force=true} | 提交日报生成（后台执行，立即返回 {accepted,kind}）；force=false 当日跳过；生成中→409 | 幂等(f=0) | news.py:48-64 |
| GET `/reports` | 登录 | — | 日报元信息 | 只读 | news.py:67-71 |
| GET `/report` | 登录 | date? | 日报 Markdown 全文；缺省最新，无→404 | 只读 | news.py:74-89 |
| GET `/status` | 登录 | — | 最近一次日报/周报/月报执行状态 | 只读 | news.py:92-100 |
| POST `/{report_type}` | full | weekly/monthly;{period,supplement?} | 提交周/月报生成（后台执行，立即返回）；生成中→409；非法类型→400 | — | news.py:103-126 |
| GET `/{report_type}` | 登录 | weekly/monthly;period? | 期全文或列表；非法类型→400 | 只读 | news.py:129-144 |

**注意**：`/reports`、`/report`、`/status` 为字面量路径，先于 `/{report_type}` 匹配（news.py:67/74/92 vs 103/129）。

---

## 10. profile（`/api/v1/profile`，登录）

| 端点 | 请求 | 响应 | 说明 | 证据 |
|------|------|------|------|------|
| GET `` | — | UserProfile | 无画像返回默认空 | profile.py:28-35 |
| PUT `` | ProfileUpdate(全可选) | UserProfile | upsert_update 深合并（只合并非 None 字段） | profile.py:40-53 |

**UserProfile**：user_id/bio/skills[]/career_goal/job_preferences{target_roles,target_cities,min_salary_k,company_types,keywords}/news_interests[]/updated_at（models/profile.py:35-44）。

---

## 11. share（`/api/v1/share`，路由级 require_full_access）

> 分享知识库问答：检索/对话只读所有者知识库。共享 LLM 角色 chat_simple、top_k=5、history=8（share.py:50-55）。

| 端点 | 请求 | 说明 | 证据 |
|------|------|------|------|
| POST `` | {title(≤100,默认""),category_l1/2/3(默认""),expires_days（**默认 7，范围 -1..30**）} | 创建分享；层级不完整/分类非法/范围为空→400 | share.py:126-197 |
| GET `` | — | 我的分享列表（含 expires_at/view_count/last_accessed_at/entries_count） | share.py:200-240 |
| GET `/{share_id}` | — | 分享详情；**访问即记录一次 view** | share.py:243-278 |
| DELETE `/{share_id}` | — | 撤销（仅所有者，否则 403） | share.py:281-296 |
| GET `/{share_id}/entries` | category_l1/2/3/page(≥1)/page_size(默认20,1-100) | 浏览条目（强制用分享限定分类） | share.py:303-365 |
| GET `/{share_id}/messages` | — | 访问者会话历史 | share.py:372-382 |
| POST `/{share_id}/chat/stream` | {message(必填,≥1)} | SSE 问答（写分享会话） | share.py:385-519 |

**越权防护**：分享校验 `_get_valid_share`（404 不存在/撤销、403 失效过期；share.py:92-98 定义，实现收敛至 `share_service.get_valid_share`）；entries 查询强制 owner 范围 + 分享限定分类覆盖请求参数（share.py:321-324）。

---

## 12. upload（`/api/v1/upload`，路由级 require_full_access）

> 文档/图片入库 ChromaDB；L3 未启用→503。单文件上限 50MB（`upload_service.py:36`，超限→413）；文档源 document / 图片源 image。

| 端点 | 请求 | 说明 | 幂等 | 证据 |
|------|------|------|------|------|
| POST `` | multipart file(form);async_process?/overwrite?（query，默认 false） | 解析→分块→入库；async 后台；overwrite 先删同名 | overwrite=true 近似 | upload.py:138-276 |
| GET `/status` | — | 知识库总量/状态（L3 未启用 total=0） | 只读 | upload.py:278-311 |
| DELETE `/entries/{entry_id}` | — | 删条目 | 重删 deleted=true | upload.py:313-371 |
| GET `/series` | — | 系列归组列表 | 只读 | upload.py:373-435 |
| GET `/files` | — | 文件列表（含 md5） | 只读 | upload.py:437-487 |
| POST `/reclassify` | — | 全量重跑 LLM 分类 | 覆盖 | upload.py:489-538 |
| POST `/re-series` | — | 重算系列 | 覆盖 | upload.py:540-601 |
| POST `/analyze` | — | 概览统计+LLM 概览 | 只读+重算 | upload.py:604-653 |

---

## 13. SSE 流式协议（chat & share）

- **端点**：`POST /api/v1/chat/stream`、`POST /api/v1/share/{share_id}/chat/stream`。
- **连接**：同 REST，`Authorization: Bearer <JWT>`；body `{message, conversation_id?}`。
- **媒体类型**：`text/event-stream`；nginx `X-Accel-Buffering: no` + `proxy_buffering off`（deploy/nginx.conf）。

### 13.1 事件帧格式
每事件为两行：`data: {json}\n\n`。无 `event:` 行（chat.py:525-527；分享问答 share.py:431-434/477）。

### 13.2 事件类型
| type | 载荷 | 含义 |
|------|------|------|
| `thinking` | `{content}` | 节点开始时推送的进度文案（正在理解/检索/规划/执行/反思/生成） |
| `token` | `{content}` | 答案逐字（真流式，来自 token sink） |
| `done` | `{meta}` | 结束；meta 含 conversation_id/title/intent/intent_confidence/metrics/trace_id/latency_ms/ingest_status/ingest_reason/degraded（chat.py:596-611）；分享流另有 latency_ms/retrieved_count/share_id（share.py:494-500） |
| `error` | `{detail}` | 出错结束（会话互斥、图异常等） |

### 13.3 语义与边界
- 无 `done` 即流中断：前端视为错误（services/chat.ts:106-110）。
- 同会话并发：`_conv_inflight` 集合 → 重复请求先回 `error`（chat.py:490-498）。
- 中断：客户端断开 → 后端 finally 清理 in-flight；run_task 继续至图结束但无人消费（进程内）。

---

## 14. CLI 命令参考

> 入口：`python -m app.cli.main`（backend/pyproject `sekb = app.cli.main:app`）。支持：`chat`（交互/单发）、`eval`、配置/导入导出等子命令。详细参数表见 03-MODULES §CLI 节与 `backend/app/cli/`（本版未逐条展开，标注为待补精确参数）。

---

## 15. 端点异常/漂移汇总（引自 T1 §14）

1. 无鉴权端点 7 个：health×3、`/metrics`、`/client-event`、register/login、reset-password（后三者为设计如此，reset-password 靠「原密码」而非登录态鉴权）；`/client-event` 与 `/metrics` 建议网络层保护。
2. ~~全局限流中间件未挂载~~ → **已挂载并生效**（`server.py:204-219`，`enabled: true`；见 §1.3）。
   注意：应用侧 429 与 nginx 429 是**两套独立计数**，排查时要先看是谁返回的
   （nginx 限流时上游响应时间为 `-`/`0.000` 且在 `docker logs sekb-frontend` 里有 `limiting requests`；
   应用限流则 `urt>0` 且响应体是 `{"code":3001,"message":"请求过于频繁，请稍后再试"}`）。
3. 多数写 POST 非幂等；upload overwrite、job 缓存、news force 控制近似幂等。
4. `_conv_inflight` 进程内集合；新会话锁键退化 `user|`。
5. create_jwt docstring 写「默认 90 天」（auth.py:81）、config.py 默认也仍是 2160h（config.py:312），但 `backend/config.yaml:302` 已收紧为 168h=**7 天**，运行时以配置为准（auth.py:83）——文档旧值「90 天」已失真。另：chat.py:320/357 注释写知识入库/L2「不阻塞」，实际均为同步 `await`（chat.py:323-344）。
6. `knowledge/categories` 不读取用户数据（静态目录），但仍受 knowledge router 级 `require_full_access` 依赖约束，需 full JWT（knowledge.py:28-30）。

---

## 相关文档

- [02-RUNTIME-FLOWS.md](./02-RUNTIME-FLOWS.md)（SSE 时序）
- [08-GLOSSARY.md](./08-GLOSSARY.md)
- 事实表：[.facts/T1-routes.md](./.facts/T1-routes.md)（逐端点字段全表）
- 部署/鉴权细节：[docs/ops/](../ops/)
