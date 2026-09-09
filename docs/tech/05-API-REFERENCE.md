---
title: 接口参考（API Reference）
layer: 参考层
owner: SEKB Team
status: active
version: v1.0.0
last-updated: 2026-09-09
based-on-commit: 42841ca
related: [02-RUNTIME-FLOWS, 08-GLOSSARY]
---

# 05 · 接口参考（API-REFERENCE）

> **本文回答什么问题**：对外有哪些接口？每个端点怎么调、返回什么、有什么副作用？
> **适合谁读**：前端开发者、接口调用方、后端新成员。
> **读完能做什么**：能对着任意端点写出 curl；理解 SSE 事件协议与 CLI 命令；定位错误处理。

> **完整证据**：全部端点的逐字段表见事实表 [.facts/T1-routes.md](./.facts/T1-routes.md)（65 端点，带 file:line）。本文为可读手册，正文引用 T1 深表。

---

## 1. 通用说明

### 1.1 基础
- Base URL：`/sekb/api/v1`（生产经 nginx 反代，nginx.conf `proxy_pass /api/`；本地 `http://localhost:8000/api/v1`）。
- 认证：`Authorization: Bearer <JWT>`。登录/注册开放；其余大多需登录；部分 router 需 full 访问（白名单）。
- 响应：统一 `{...}` JSON（成功无包层）；错误经全局异常处理器。

### 1.2 鉴权与访问级别
| 级别 | 含义 | 来源 |
|------|------|------|
| open | 无需登录（register/login/health/metrics/monitoring） | T1 §0 |
| 登录 | 任意有效 JWT | `get_current_user`（auth.py） |
| full | 白名单完整用户（`require_full_access` 路由级） | app/core/access.py |
| preview | 白名单外用户：仅功能说明+资讯只读 | 前端菜单/后端路由双收窄 |

- JWT：HS256，90 天有效期、滑动续租（`/auth/refresh`）；JWT 密钥取环境变量 `JWT_SECRET`（auth.py:39，启动强校验）。
- ALLOWED_EMAILS：逗号分隔白名单（`.env.prod`，脱敏）。

### 1.3 限流（现状：全局未生效）
- `RateLimitMiddleware` 已实现但被注释禁用（server.py:195-201）；`api.rate_limit.enabled: false`（config.yaml:271）。
- **当前所有端点均无生效限流**；nginx `limit_req zone=api_limit rate=10r/s` 是实际第一道闸（deploy/nginx.conf）。
- 客户端事件上报有独立限流 `client_event_limit`（nginx，2r/s+b10）。

### 1.4 错误映射（全局异常处理器）
| HTTP | 条件 | 证据 |
|------|------|------|
| 400 | 业务校验失败（如邮箱已注册） | server.py:75-106 |
| 401 | 未认证 / 密码错（防枚举统一文案） | auth.py |
| 403 | 无权限（preview 访问 full 资源）/ 分享过期 | access.py；T1 |
| 404 | 资源不存在 / 会话归属不符（不泄露存在性） | 多处 |
| 429 | LLM rate limit / budget 超限 | server.py:75-106 |
| 500 | 其它异常（响应只含脱敏 `error_id`） | server.py:226-250 |

---

## 2. auth（`/api/v1/auth`）

| 端点 | 鉴权 | 请求 | 响应 | 说明 | 证据 |
|------|------|------|------|------|------|
| POST `/register` | open | RegisterRequest(email, password≥8, name?) | LoginResponse(user, token) | 写 UserStorage+audit；邮箱已存在→400 | auth.py:41-88 |
| POST `/login` | open | LoginRequest(email, password) | LoginResponse | 只审计+指标，无写；错→401 统一文案 | auth.py:91-148 |
| POST `/logout` | 登录 | — | {status:ok} | 客户端自行清 token | auth.py:151-157 |
| POST `/refresh` | 登录 | — | LoginResponse | 滑动续租新 JWT | auth.py:160-174 |
| GET `/me` | 登录 | — | UserPublic(+access_level) | 只读 | auth.py:177-187 |
| PATCH `/me` | 登录 | 原始 body，白名单 {name,avatar_url,settings} | UserPublic | 写 UserStorage.update+audit | auth.py:190-205 |

**RegisterRequest 字段**：email（正则校验）、password（8-128）、name（默认""，≤50）。
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

> 两端点共用 `_run_chat`（chat.py:407-623）。请求体 `ChatRequest`：`message` 必填、`conversation_id`?（None=新建）、`user_id`（被忽略，真实取 JWT）、`skill`（空=普通）。

| 端点 | 请求/响应 | 说明 | 证据 |
|------|-----------|------|------|
| POST `/` | ChatRequest → ChatResponse | 非流式；跑完整 LangGraph 后一次返回 | chat.py:642-687 |
| POST `/stream` | ChatRequest → SSE | 真流式（thinking/token/done）；同会话并发互斥 | chat.py:690-847 |

**ChatResponse**：conversation_id/message/intent/intent_confidence/metrics/trace_id/meta{ingest_status,ingest_reason}。
**副作用**：写会话 JSON + L1 记忆 + await 知识入库（chat.py:560-600）。
**SSE 协议**详见 §9。
**前端调用方**：`frontend/src/services/chat.ts` streamChat；`stores/chat.ts`。

---

## 4. chat_share（`/api/v1/chat-share`）

| 端点 | 鉴权 | 请求 | 响应 | 说明 | 证据 |
|------|------|------|------|------|------|
| POST `` | 登录 | CreateChatShareRequest(conv_id) | share_id/title/share_url/message_count | 快照非空 user/assistant 消息；非本人会话也 404 | chat_share.py:88-141 |
| GET `/{share_id}` | 登录 | — | share 详情+messages | 分享有效未过期才可读 | chat_share.py:144-161 |
| DELETE `/{share_id}` | 登录 | — | 撤销结果 | 仅所有者 | chat_share.py:… |

---

## 5. conversations（`/api/v1/conversations`，router 级 require_full_access）

| 端点 | 鉴权 | 请求 | 响应 | 说明 | 证据 |
|------|------|------|------|------|------|
| GET `` | 登录 | limit/offset | Conversation[] | 按 user 过滤、不含 deleted、pinned 优先 | conversations.py:90-107 |
| POST `` | 登录 | title? | Conversation | 新建会话 | conversations.py:110-… |
| GET `/{conv_id}` | 登录 | — | Conversation | 404 不存在 | conversations.py:… |
| PATCH `/{conv_id}` | 登录 | {title?,pinned?} | Conversation | 重命名/置顶 | conversations.py:… |
| DELETE `/{conv_id}` | 登录 | — | 删除结果 | 软删+清消息 | conversations.py:… |
| GET `/{conv_id}/messages` | 登录 | limit | Message[] | 取历史 | conversations.py:… |
| POST `/{conv_id}/rate` | 登录 | RateRequest(msg_id,rating,comment?) | 反馈记录 | thumbs up/down | conversations.py:… |

**Conversation**：conv_id/user_id/title/status/pinned/created_at/updated_at/message_count/统计（base.py:38-55）。
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
| GET `/categories` | full | — | {tree, raw} 静态分类 | 只读 | knowledge.py:63-66 |
| GET `/categories/stats` | full+登录 | — | {stats[], total} | 只读（异常吞掉返回空） | knowledge.py:69-111 |
| GET `` | full+登录 | source/category_l1/2/3/page/page_size | {entries(截200字), total, page, page_size} | 只读 | knowledge.py:114-159 |
| GET `/search` | full+登录 | q/top_k(≤50) | {entries(含similarity)} | 只读 min_score=0.0 | knowledge.py:162-183 |
| DELETE `/{entry_id}` | full+登录 | — | {status,entry_id} | 删条目；重删404 | knowledge.py:186-216 |
| PATCH `/{entry_id}/category` | full+登录 | {l1,l2,l3} | {status,l1,l2,l3,source:manual} | 覆盖元数据；幂等 | knowledge.py:231-291 |

**前端调用方**：Knowledge 页、分享设置、Job 分析联动。

---

## 8. job（`/api/v1/job`，路由级 require_full_access）

> 所有写路径先 `_require_job_agent`（未启用→503）。缓存键均含 user_id 隔离。浏览器服务固定 `http://browser:1300`。

| 端点 | 请求 | 说明 | 幂等 | 证据 |
|------|------|------|------|------|
| POST `/analyze` | {jd_text, job_meta?} | 单 JD 深度分析；14 天缓存 | 缓存命中跳过 | job.py:54-84 |
| POST `/fetch` | {keyword,city,min_salary_k,page,limit} | 采集职位；14 天缓存 | 缓存命中跳过 | job.py:87-140 |
| POST `/boss/qr/start` | — | 转发浏览器服务扫码登录 | 每次新建会话 | job.py:160-168 |
| POST `/boss/qr/status` | {qr_id} | 轮询扫码状态 | — | job.py:171-181 |
| POST `/batch-analyze` | {keyword,city,force,jobs?} | 市场批量分析；7 天缓存；存档 | force 控制 | job.py:195-232 |
| DELETE `/batch-analyze` | — | 删批量报告 | 重删 false | job.py:235-242 |
| GET `/batch-analyze/cached` | — | 取缓存报告 | 只读 | job.py:245-252 |
| POST `/import` | multipart files | 解析 txt/md/pdf/docx 职位，无写库 | 幂等 | job.py:255-310 |
| POST `/refresh` | {job_url,source} | 重抓职位详情 | 每次重请求 | job.py:320-327 |
| POST `/cache/save` | {keyword,city,min_salary_k,jobs} | 覆盖职位缓存 | 覆盖 | job.py:339-351 |
| GET `/cache/latest` | — | 取最近缓存职位 | 只读 | job.py:354-363 |
| GET `/reports` | — | 存档列表 | 只读 | job.py:366-372 |
| GET `/reports/{report_id}` | — | 报告全文 | 只读 | job.py:375-384 |
| DELETE `/reports/{report_id}` | — | 删报告 | 重删 false | job.py:387-394 |

**前端调用方**：Job 页（批量分析/单 JD/扫码采集）。

---

## 9. news（`/api/v1/news`，router 无路由级依赖）

> 只读端点仅需登录（preview 可读）；写端点（refresh/周期生成）要求 full。

| 端点 | 鉴权 | 请求 | 说明 | 幂等 | 证据 |
|------|------|------|------|------|------|
| POST `/refresh` | full | {force=true} | 触发日报流水线；force=false 当日跳过 | 幂等(f=0) | news.py:34-47 |
| GET `/reports` | 登录 | — | 日报元信息 | 只读 | news.py:50-54 |
| GET `/report` | 登录 | date? | 日报 Markdown 全文 | 只读 | news.py:57-72 |
| POST `/{report_type}` | full | weekly/monthly;{period,supplement?} | 触发周/月报生成 | — | news.py:75-94 |
| GET `/{report_type}` | 登录 | weekly/monthly;period? | 期全文或列表 | 只读 | news.py:97-112 |

**注意**：`/reports`、`/report` 为字面量路径，先于 `/{report_type}` 匹配（news.py:50/57 vs 75/97）。

---

## 10. profile（`/api/v1/profile`，登录）

| 端点 | 请求 | 响应 | 说明 | 证据 |
|------|------|------|------|------|
| GET `` | — | UserProfile | 无画像返回默认空 | profile.py:28-37 |
| PUT `` | ProfileUpdate(全可选) | UserProfile | upsert_update 深合并（只合并非 None 字段） | profile.py:40-53 |

**UserProfile**：user_id/bio/skills[]/career_goal/job_preferences{target_roles,target_cities,min_salary_k,company_types,keywords}/news_interests[]/updated_at（models/profile.py:33-42）。

---

## 11. share（`/api/v1/share`，路由级 require_full_access）

> 分享知识库问答：检索/对话只读所有者知识库。共享 LLM 角色 chat_simple、top_k=5、history=8（share.py:56-60）。

| 端点 | 请求 | 说明 | 证据 |
|------|------|------|------|
| POST `` | {title,category_l1/2/3,expires_days=30} | 创建分享；校验范围非空 | share.py:171-242 |
| GET `` | — | 我的分享列表 | share.py:245-282 |
| GET `/{share_id}` | — | 分享详情 | share.py:285-320 |
| DELETE `/{share_id}` | — | 撤销（仅所有者） | share.py:323-338 |
| GET `/{share_id}/entries` | category_*/page | 浏览条目（强制用分享限定分类） | share.py:345-407 |
| GET `/{share_id}/messages` | — | 访问者会话历史 | share.py:414-424 |
| POST `/{share_id}/chat/stream` | {message} | SSE 问答（写分享会话） | share.py:427-547 |

**越权防护**：分享校验 `_get_valid_share`（404 不存在/撤销、403 失效过期）；entries 查询强制 owner 范围 + 分享限定分类覆盖请求参数（share.py:363-366）。

---

## 12. upload（`/api/v1/upload`，路由级 require_full_access）

> 文档/图片入库 ChromaDB；L3 未启用→503。单文件上限 50MB；文档源 document / 图片源 image。

| 端点 | 请求 | 说明 | 幂等 | 证据 |
|------|------|------|------|------|
| POST `` | multipart file;async_process?;overwrite? | 解析→分块→入库；async 后台；overwrite 先删同名 | overwrite=true 近似 | upload.py:494-612 |
| GET `/status` | — | 知识库总量/状态 | 只读 | upload.py:615-647 |
| DELETE `/entries/{entry_id}` | — | 删条目 | 重删 deleted=true | upload.py:650-707 |
| GET `/series` | — | 系列归组列表 | 只读 | upload.py:710-763 |
| GET `/files` | — | 文件列表（含 md5） | 只读 | upload.py:766-815 |
| POST `/reclassify` | — | 全量重跑 LLM 分类 | 覆盖 | upload.py:818-868 |
| POST `/re-series` | — | 重算系列 | 覆盖 | upload.py:871-932 |
| POST `/analyze` | — | 概览统计+LLM 概览（role job_analysis） | 只读+重算 | upload.py:935-1006 |

---

## 13. SSE 流式协议（chat & share）

- **端点**：`POST /api/v1/chat/stream`、`POST /api/v1/share/{share_id}/chat/stream`。
- **连接**：同 REST，`Authorization: Bearer <JWT>`；body `{message, conversation_id?}`。
- **媒体类型**：`text/event-stream`；nginx `X-Accel-Buffering: no` + `proxy_buffering off`（deploy/nginx.conf）。

### 13.1 事件帧格式
每事件为两行：`data: {json}\n\n`。无 `event:` 行（chat.py:736-737）。

### 13.2 事件类型
| type | 载荷 | 含义 |
|------|------|------|
| `thinking` | `{content}` | 节点开始时推送的进度文案（正在理解/检索/规划/执行/反思/生成） |
| `token` | `{content}` | 答案逐字（真流式，来自 token sink） |
| `done` | `{meta}` | 结束；meta 含 conversation_id/intent/intent_confidence/metrics/trace_id/latency_ms/ingest_* |
| `error` | `{detail}` | 出错结束（会话互斥、图异常等） |

### 13.3 语义与边界
- 无 `done` 即流中断：前端视为错误（services/chat.ts:106-110）。
- 同会话并发：`_conv_inflight` 集合 → 重复请求先回 `error`（chat.py:700-719）。
- 中断：客户端断开 → 后端 finally 清理 in-flight；run_task 继续至图结束但无人消费（进程内）。

---

## 14. CLI 命令参考

> 入口：`python -m app.cli.main`（backend/pyproject `sekb = app.cli.main:app`）。支持：`chat`（交互/单发）、`eval`、配置/导入导出等子命令。详细参数表见 03-MODULES §CLI 节与 `backend/app/cli/`（本版未逐条展开，标注为待补精确参数）。

---

## 15. 端点异常/漂移汇总（引自 T1 §14）

1. 无鉴权端点 6 个：health×3、`/metrics`、`/client-event`、register/login（后两者设计如此）；`/client-event` 与 `/metrics` 建议网络层保护。
2. 全局限流中间件未挂载（server.py:195-201 注释禁用）；当前实际限流只有 nginx。
3. 多数写 POST 非幂等；upload overwrite、job 缓存、news force 控制近似幂等。
4. `_conv_inflight` 进程内集合；新会话锁键退化 `user|`。
5. create_jwt docstring「72h」与实际 90 天漂移（auth.py:68 vs 71）；chat.py:586「不阻塞」注释与同步 await 不符。
6. 无鉴权 `knowledge/categories`（不绑定用户，属静态目录）。

---

## 相关文档

- [02-RUNTIME-FLOWS.md](./02-RUNTIME-FLOWS.md)（SSE 时序）
- [08-GLOSSARY.md](./08-GLOSSARY.md)
- 事实表：[.facts/T1-routes.md](./.facts/T1-routes.md)（逐端点字段全表）
- 部署/鉴权细节：[docs/ops/](../ops/)
