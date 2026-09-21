---
title: T1-路由清单（REST API 端点事实表）
table: T1
source: backend/app/api/routes/*.py, backend/app/api/server.py
status: 端点清单为**生成物**（2026-09-20 起）；鉴权模型为人工维护
last-updated: 2026-09-20
based-on-commit: a3c5e28
---

# T1 路由清单

> **本表的端点清单是生成物**：`python3 scripts/gen_facts_routes.py --write`
> （`file:line` 天然准确，代码改动后重跑即可对齐）。
> 历史：2026-09-09 首版是手写草案，2026-09-16 复核发现**行号与计数已过期**
> （当年 65 条 vs 实测 75 个端点）——手抄的表必然再次漂移，所以改成生成。
> 设计意图、鉴权模型这类**需要理解**的内容仍在本文件上半部分人工维护。

## 0. 总览（人工维护部分）

### 0.1 路由注册（`backend/app/api/server.py:298-328`）

共 **15 个 router**（2026-09-20，含端云协同新增的 `device`、`edge`）：

| 模块 | 端点 | prefix | 说明 |
|---|---|---|---|
| auth | 8 | `/api/v1/auth` | 注册/登录/刷新/登出/改密/资料 |
| chat | 3 | `/api/v1/chat` | 聊天（非流式 + SSE 流式 + usage） |
| chat_share | 3 | `/api/v1/chat-share` | 会话分享 |
| conversations | 7 | `/api/v1/conversations` | 会话与消息读写 |
| **device** | 5 | `/api/v1/device` | **设备身份**（enroll/refresh/heartbeat/list/revoke，端云协同 S1） |
| **edge** | 3 | `/api/v1/edge` | **路由可观测**（stats/routes/route-events，S2） |
| health | 3 | `/api/v1/health` | 健康检查（含就绪探针） |
| job | 20 | `/api/v1/job` | 招聘分析（含搜索历史、投递计划） |
| knowledge | 6 | `/api/v1/knowledge` | 知识库读写（**设备 token 403**） |
| metrics | 1 | （无 prefix） | Prometheus 指标 |
| monitoring | 1 | `/api/v1/monitoring` | 监控辅助 |
| news | 6 | `/api/v1/news` | 科技资讯（日报/周报/月报/状态） |
| profile | 2 | `/api/v1/profile` | 用户画像 |
| share | 7 | `/api/v1/share` | 知识/会话分享（**设备 token 403**） |
| upload | 8 | `/api/v1/upload` | 上传与文档管理（**设备 token 403**） |

ASGI 入口：`backend/app/api/main.py:14`（`app = get_app()`，延迟构建）。

### 0.2 鉴权模型（三种凭证 + 五类依赖）

| 凭证 | 签发 | 载荷特征 | 有效期 |
|---|---|---|---|
| **用户 JWT** | `create_jwt()`（`app/core/auth.py:84`） | `sub`=user_id、`jti`（可吊销） | `api.auth.token_expire_hours`=**168h**（`config.yaml:302`，7 天） |
| **设备 JWT** | `create_device_jwt()`（`auth.py:99`） | 额外 `typ=device`、`device_id`、`scope=edge` | 代码默认 **720h**（`config.py:383`；`config.yaml` 未显式配置，30 天） |

依赖口径（端点表"端点级认证"列即取自这里）：

| 依赖 | 允许的凭证 | 备注 |
|---|---|---|
| `get_current_user` / `get_current_claims` | 用户 + 设备 | 二者共同入口 `_claims_checked()`（`auth.py:203`）：**设备 token 的吊销/轮换校验在这里**，所以任何受保护路由都自动生效 |
| `get_current_device` | **仅设备** | 设备专属动作（如上报），用户 token 会 403 |
| `require_full_access` | 用户 + 设备（白名单） | 白名单由环境变量 `ALLOWED_EMAILS` 决定；未配置 = 全员 full |
| `require_user_account` | **仅用户** | 写资产类路由（`upload` / `knowledge` / `share`）用它：设备 token 403 |

**为什么设备 token 要单独一套**：它长效（30 天）且存在客户端本地，一旦泄漏，
能做的应当尽可能少——只能"替用户用"（聊天、会话同步、路由上报），不能改用户资产。
这是端云协同里唯一的硬隐私边界在**鉴权层**的落地（RFC §5.2 / §8 S1）。

### 0.3 端云协同相关的三条约定

1. **执行位置可见**：`POST /api/v1/chat/` 与 `/chat/stream` 的响应带 `execution`
   （`primary_plane` / `reason` / `escalated` / `by_plane` / `versions`），用户有权知道数据出没出端。
2. **路由事件可上报**：`POST /api/v1/edge/route-events` 幂等（`event_id` 去重），
   端侧自己完成的推理靠它进入统计——否则"端侧完成率/升级率"只统计到一半。
3. **契约有守卫**：`scripts/check_protocol_paths.py` 检查
   协议文档 ↔ 服务端路由 ↔ 端侧客户端三方一致（接在 `doc_guard.sh` 的 5/5）。

## 1. 端点清单（生成区块）

<!-- BEGIN GENERATED: endpoints -->
> 本区块由 `python3 scripts/gen_facts_routes.py --write` **生成**（端点 84 个）。
> 行号来自当次代码，代码改动后请重跑脚本；**不要手工编辑本区块**。

**端点合计：84 个**（与 `docs/tech/05-API-REFERENCE.md` 的 `fact:api_endpoints` 对齐）。

### auth（8 个，prefix `/api/v1/auth`）

| 方法 | 路径 | 端点级认证 | 处理函数 | 证据 |
|---|---|---|---|---|
| POST | `/api/v1/auth/change-password` | 用户/设备 | `change_password` | `app/api/routes/auth.py:171` |
| POST | `/api/v1/auth/login` | （随路由级） | `login` | `app/api/routes/auth.py:96` |
| POST | `/api/v1/auth/logout` | 用户/设备 | `logout` | `app/api/routes/auth.py:156` |
| GET | `/api/v1/auth/me` | 用户/设备 | `get_me` | `app/api/routes/auth.py:233` |
| PATCH | `/api/v1/auth/me` | 用户/设备 | `update_me` | `app/api/routes/auth.py:246` |
| POST | `/api/v1/auth/refresh` | 用户/设备 | `refresh` | `app/api/routes/auth.py:216` |
| POST | `/api/v1/auth/register` | （随路由级） | `register` | `app/api/routes/auth.py:46` |
| POST | `/api/v1/auth/reset-password` | （随路由级） | `reset_password` | `app/api/routes/auth.py:194` |

### chat（3 个，prefix `/api/v1/chat`）

| 方法 | 路径 | 端点级认证 | 处理函数 | 证据 |
|---|---|---|---|---|
| POST | `/api/v1/chat` | 用户/设备 | `chat` | `app/api/routes/chat.py:450` |
| POST | `/api/v1/chat/stream` | 用户/设备 | `chat_stream` | `app/api/routes/chat.py:501` |
| GET | `/api/v1/chat/usage` | 用户/设备 | `get_usage` | `app/api/routes/chat.py:683` |

### chat_share（3 个，prefix `/api/v1/chat-share`）

| 方法 | 路径 | 端点级认证 | 处理函数 | 证据 |
|---|---|---|---|---|
| POST | `/api/v1/chat-share` | 用户/设备 | `create_chat_share` | `app/api/routes/chat_share.py:67` |
| DELETE | `/api/v1/chat-share/{share_id}` | 用户/设备 | `revoke_chat_share` | `app/api/routes/chat_share.py:143` |
| GET | `/api/v1/chat-share/{share_id}` | 用户/设备 | `get_shared_conversation` | `app/api/routes/chat_share.py:123` |

### conversations（7 个，prefix `/api/v1/conversations`）

| 方法 | 路径 | 端点级认证 | 处理函数 | 证据 |
|---|---|---|---|---|
| GET | `/api/v1/conversations` | 用户/设备 | `list_conversations` | `app/api/routes/conversations.py:90` |
| POST | `/api/v1/conversations` | 用户/设备 | `create_conversation` | `app/api/routes/conversations.py:110` |
| DELETE | `/api/v1/conversations/{conv_id}` | 用户/设备 | `delete_conversation` | `app/api/routes/conversations.py:226` |
| GET | `/api/v1/conversations/{conv_id}` | 用户/设备 | `get_conversation` | `app/api/routes/conversations.py:131` |
| PATCH | `/api/v1/conversations/{conv_id}` | 用户/设备 | `update_conversation` | `app/api/routes/conversations.py:189` |
| GET | `/api/v1/conversations/{conv_id}/messages` | 用户/设备 | `get_messages` | `app/api/routes/conversations.py:160` |
| POST | `/api/v1/conversations/{conv_id}/rate` | 用户/设备 | `rate_message` | `app/api/routes/conversations.py:256` |

### device（5 个，prefix `/api/v1/device`）

| 方法 | 路径 | 端点级认证 | 处理函数 | 证据 |
|---|---|---|---|---|
| POST | `/api/v1/device/enroll` | 用户/设备（白名单） | `enroll` | `app/api/routes/device.py:79` |
| POST | `/api/v1/device/heartbeat` | **仅设备** | `heartbeat` | `app/api/routes/device.py:129` |
| GET | `/api/v1/device/list` | 用户/设备,用户/设备（含设备生命周期校验） | `list_devices` | `app/api/routes/device.py:143` |
| POST | `/api/v1/device/refresh` | **仅设备** | `refresh` | `app/api/routes/device.py:107` |
| DELETE | `/api/v1/device/{device_id}` | 用户/设备,用户/设备（含设备生命周期校验） | `revoke_device` | `app/api/routes/device.py:155` |

### edge（4 个，prefix `/api/v1/edge`）

| 方法 | 路径 | 端点级认证 | 处理函数 | 证据 |
|---|---|---|---|---|
| GET | `/api/v1/edge/policy` | **仅设备** | `get_policy` | `app/api/routes/edge.py:102` |
| POST | `/api/v1/edge/route-events` | 用户/设备（白名单） | `report_route_event` | `app/api/routes/edge.py:85` |
| GET | `/api/v1/edge/routes` | 用户/设备（白名单） | `list_routes` | `app/api/routes/edge.py:75` |
| GET | `/api/v1/edge/routes/stats` | 用户/设备（白名单） | `route_stats` | `app/api/routes/edge.py:65` |

### health（3 个，prefix `/api/v1/health`）

| 方法 | 路径 | 端点级认证 | 处理函数 | 证据 |
|---|---|---|---|---|
| GET | `/api/v1/health` | （随路由级） | `health_check` | `app/api/routes/health.py:29` |
| GET | `/api/v1/health/live` | （随路由级） | `liveness` | `app/api/routes/health.py:103` |
| GET | `/api/v1/health/ready` | （随路由级） | `readiness` | `app/api/routes/health.py:116` |

### job（20 个，prefix `/api/v1/job`）

| 方法 | 路径 | 端点级认证 | 处理函数 | 证据 |
|---|---|---|---|---|
| POST | `/api/v1/job/analyze` | 用户/设备 | `analyze_job` | `app/api/routes/job.py:75` |
| GET | `/api/v1/job/apply-plan` | 用户/设备 | `list_apply_plan` | `app/api/routes/job.py:509` |
| POST | `/api/v1/job/apply-plan` | 用户/设备 | `save_apply_plan` | `app/api/routes/job.py:519` |
| DELETE | `/api/v1/job/apply-plan/{plan_id}` | 用户/设备 | `delete_apply_plan` | `app/api/routes/job.py:529` |
| DELETE | `/api/v1/job/batch-analyze` | 用户/设备 | `delete_batch_analysis` | `app/api/routes/job.py:256` |
| POST | `/api/v1/job/batch-analyze` | 用户/设备 | `batch_analyze` | `app/api/routes/job.py:206` |
| GET | `/api/v1/job/batch-analyze/cached` | 用户/设备 | `get_cached_batch_analysis` | `app/api/routes/job.py:272` |
| POST | `/api/v1/job/boss/qr/start` | 用户/设备 | `boss_qr_start` | `app/api/routes/job.py:169` |
| POST | `/api/v1/job/boss/qr/status` | 用户/设备 | `boss_qr_status` | `app/api/routes/job.py:180` |
| GET | `/api/v1/job/cache/latest` | 用户/设备 | `get_latest_job_cache` | `app/api/routes/job.py:424` |
| GET | `/api/v1/job/cache/list` | 用户/设备 | `list_job_caches` | `app/api/routes/job.py:444` |
| POST | `/api/v1/job/cache/save` | 用户/设备 | `save_job_cache` | `app/api/routes/job.py:406` |
| GET | `/api/v1/job/cache/search/{search_id}` | 用户/设备 | `get_search_jobs` | `app/api/routes/job.py:352` |
| POST | `/api/v1/job/fetch` | 用户/设备 | `fetch_jobs` | `app/api/routes/job.py:108` |
| POST | `/api/v1/job/import` | 用户/设备 | `import_jobs` | `app/api/routes/job.py:364` |
| POST | `/api/v1/job/refresh` | 用户/设备 | `refresh_job` | `app/api/routes/job.py:387` |
| GET | `/api/v1/job/reports` | 用户/设备 | `list_archived_reports` | `app/api/routes/job.py:454` |
| DELETE | `/api/v1/job/reports/{report_id}` | 用户/设备 | `delete_archived_report` | `app/api/routes/job.py:475` |
| GET | `/api/v1/job/reports/{report_id}` | 用户/设备 | `get_archived_report` | `app/api/routes/job.py:463` |
| GET | `/api/v1/job/searches` | 用户/设备 | `list_searches` | `app/api/routes/job.py:293` |

### knowledge（6 个，prefix `/api/v1/knowledge`）

| 方法 | 路径 | 端点级认证 | 处理函数 | 证据 |
|---|---|---|---|---|
| GET | `/api/v1/knowledge` | 用户/设备 | `list_knowledge` | `app/api/routes/knowledge.py:109` |
| GET | `/api/v1/knowledge/categories` | 用户/设备 | `list_categories` | `app/api/routes/knowledge.py:58` |
| GET | `/api/v1/knowledge/categories/stats` | 用户/设备 | `category_stats` | `app/api/routes/knowledge.py:64` |
| GET | `/api/v1/knowledge/search` | 用户/设备 | `search_knowledge` | `app/api/routes/knowledge.py:157` |
| DELETE | `/api/v1/knowledge/{entry_id}` | 用户/设备 | `delete_knowledge` | `app/api/routes/knowledge.py:181` |
| PATCH | `/api/v1/knowledge/{entry_id}/category` | 用户/设备 | `reclassify_entry` | `app/api/routes/knowledge.py:226` |

### metrics（1 个，prefix ``）

| 方法 | 路径 | 端点级认证 | 处理函数 | 证据 |
|---|---|---|---|---|
| GET | `/metrics` | （随路由级） | `metrics` | `app/api/routes/metrics.py:23` |

### monitoring（1 个，prefix `/api/v1/monitoring`）

| 方法 | 路径 | 端点级认证 | 处理函数 | 证据 |
|---|---|---|---|---|
| POST | `/api/v1/monitoring/client-event` | （随路由级） | `report_client_events` | `app/api/routes/monitoring.py:50` |

### news（6 个，prefix `/api/v1/news`）

| 方法 | 路径 | 端点级认证 | 处理函数 | 证据 |
|---|---|---|---|---|
| POST | `/api/v1/news/refresh` | 用户/设备（白名单） | `refresh_news` | `app/api/routes/news.py:48` |
| GET | `/api/v1/news/report` | 用户/设备 | `get_report` | `app/api/routes/news.py:83` |
| GET | `/api/v1/news/reports` | 用户/设备 | `list_reports` | `app/api/routes/news.py:76` |
| GET | `/api/v1/news/status` | 用户/设备 | `news_status` | `app/api/routes/news.py:101` |
| GET | `/api/v1/news/{report_type}` | 用户/设备 | `list_periodic` | `app/api/routes/news.py:138` |
| POST | `/api/v1/news/{report_type}` | 用户/设备（白名单） | `generate_periodic` | `app/api/routes/news.py:112` |

### profile（2 个，prefix `/api/v1/profile`）

| 方法 | 路径 | 端点级认证 | 处理函数 | 证据 |
|---|---|---|---|---|
| GET | `/api/v1/profile` | 用户/设备 | `get_profile` | `app/api/routes/profile.py:28` |
| PUT | `/api/v1/profile` | 用户/设备 | `update_profile` | `app/api/routes/profile.py:40` |

### share（7 个，prefix `/api/v1/share`）

| 方法 | 路径 | 端点级认证 | 处理函数 | 证据 |
|---|---|---|---|---|
| GET | `/api/v1/share` | 用户/设备 | `list_my_shares` | `app/api/routes/share.py:203` |
| POST | `/api/v1/share` | 用户/设备 | `create_share` | `app/api/routes/share.py:129` |
| DELETE | `/api/v1/share/{share_id}` | 用户/设备 | `revoke_share` | `app/api/routes/share.py:284` |
| GET | `/api/v1/share/{share_id}` | 用户/设备 | `get_share_info` | `app/api/routes/share.py:246` |
| POST | `/api/v1/share/{share_id}/chat/stream` | 用户/设备 | `shared_chat_stream` | `app/api/routes/share.py:388` |
| GET | `/api/v1/share/{share_id}/entries` | 用户/设备 | `list_shared_entries` | `app/api/routes/share.py:306` |
| GET | `/api/v1/share/{share_id}/messages` | 用户/设备 | `get_shared_messages` | `app/api/routes/share.py:375` |

### upload（8 个，prefix `/api/v1/upload`）

| 方法 | 路径 | 端点级认证 | 处理函数 | 证据 |
|---|---|---|---|---|
| POST | `/api/v1/upload` | 用户/设备 | `upload_file` | `app/api/routes/upload.py:141` |
| POST | `/api/v1/upload/analyze` | 用户/设备 | `analyze_knowledge_base` | `app/api/routes/upload.py:607` |
| DELETE | `/api/v1/upload/entries/{entry_id}` | 用户/设备 | `delete_entry` | `app/api/routes/upload.py:316` |
| GET | `/api/v1/upload/files` | 用户/设备 | `list_files` | `app/api/routes/upload.py:440` |
| POST | `/api/v1/upload/re-series` | 用户/设备 | `re_series` | `app/api/routes/upload.py:543` |
| POST | `/api/v1/upload/reclassify` | 用户/设备 | `reclassify_files` | `app/api/routes/upload.py:492` |
| GET | `/api/v1/upload/series` | 用户/设备 | `list_series` | `app/api/routes/upload.py:376` |
| GET | `/api/v1/upload/status` | 用户/设备 | `knowledge_base_status` | `app/api/routes/upload.py:281` |

<!-- END GENERATED: endpoints -->

---

## 相关文档

- [05-API-REFERENCE.md](../05-API-REFERENCE.md) —— 可读手册（端点的逐字段说明）
- [16-端云协同协议.md](../../ops/16-端云协同协议.md) —— 端侧宿主契约
- [README-工作手册.md](./README-工作手册.md) —— 事实表 T1–T8 的维护约定
