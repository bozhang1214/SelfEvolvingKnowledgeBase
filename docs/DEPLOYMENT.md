# SEKB 操作手册

> SelfEvolvingKnowledgeBase（SEKB）——从零到上线完整操作手册。
> 本文档覆盖项目简介、本地开发、API/CLI/前端使用、Docker 生产部署、运维、备份、灰度、监控与故障排查。

## 目录

1. [项目简介](#1-项目简介)
2. [环境要求与准备](#2-环境要求与准备)
3. [快速开始（本地开发模式）](#3-快速开始本地开发模式)
4. [API 使用指南](#4-api-使用指南)
5. [CLI 使用指南](#5-cli-使用指南)
6. [前端使用指南](#6-前端使用指南)
7. [Docker 生产部署](#7-docker-生产部署)
8. [日常运维](#8-日常运维)
9. [备份与恢复](#9-备份与恢复)
10. [灰度发布与回滚](#10-灰度发布与回滚)
11. [监控与告警](#11-监控与告警)
12. [故障排查](#12-故障排查)
13. [附录：服务端口映射](#13-附录服务端口映射)

---

## 1. 项目简介

**SelfEvolvingKnowledgeBase（SEKB）** 是一个基于 LangGraph 构建的多智能体知识助手，核心哲学是 **PDCA 循环**（Plan-Do-Check-Act）的工程化落地，由五个智能体角色协作完成"意图识别 → 任务拆解 → 工具调用 → 反思评估 → 知识沉淀"的完整闭环。

### 1.1 核心功能

| 模块 | 说明 |
|------|------|
| **5 个 Agent** | Supervisor（意图识别）/ Planner（任务拆解）/ Executor（工具调用）/ Critic（反思评估）/ Scribe（知识沉淀） |
| **三层记忆** | L1 短期（工作记忆，已实现）/ L2 中期（会话偏好，预留）/ L3 长期（ChromaDB 知识库，已实现） |
| **工具层** | 博查搜索 MCP（联网搜索）/ 向量检索直连（<10ms）/ 文件解析（PDF/Word/TXT） |
| **评估体系** | 9 项量化指标（意图置信度、规划步数、重规划次数、工具成功率、答案锚定度等） |
| **三个入口** | CLI（`sekb chat/eval/health`）/ REST API（FastAPI + SSE 流式）/ 前端（React SPA） |
| **用户系统** | 注册 / 登录 / JWT 鉴权（Phase 3） |
| **生产部署** | Docker Compose + 监控（Prometheus/Grafana/Loki）+ CI/CD + 灰度发布 + 备份恢复（Phase 4） |

### 1.2 PDCA 循环映射

| 阶段 | 负责 Agent | 职责 |
|------|-----------|------|
| **Plan（计划）** | Supervisor → Planner | 意图识别 + 路由决策 → 任务拆解为 Task List |
| **Do（执行）** | Executor | 工具调用（联网搜索 / RAG 检索 / 文件解析）→ 生成草稿 |
| **Check（检查）** | Critic | 多维度反思评估，不通过则回滚重规划（最多 2 次） |
| **Act（处理）** | Scribe | 摘要 + 重要性评分 + 知识沉淀，异步写入知识库 |

### 1.3 架构简图

```
┌──────────────────────────────────────────────────────────────────────┐
│                        用户接入层                                    │
│   ┌──────────────────┐         ┌──────────────────┐                 │
│   │   CLI 交互入口    │         │  FastAPI HTTP    │  ← 前端 React   │
│   │  (typer/rich)    │         │  (支持 SSE 流式)  │     SPA        │
│   └────────┬─────────┘         └────────┬─────────┘                 │
│            └────────────┬───────────────┘                            │
│                         ▼                                            │
├──────────────────────────────────────────────────────────────────────┤
│                      LangGraph 工作流引擎                            │
│   [Supervisor] ──► [Planner] ──► [Executor] ──► [Critic] ──► [Scribe]│
│        │                                              │              │
│        └─► 简单闲聊直通 chat_simple                    │              │
│                          Critic 不通过 ◄── 回滚到 Planner (max=2)     │
│                                                                      │
├──────────────────────────────────────────────────────────────────────┤
│   工具层：MCP（博查搜索）│ 本地直连（ChromaDB 向量检索 / JSON 存储）  │
├──────────────────────────────────────────────────────────────────────┤
│   记忆：L1 短期 │ L2 中期（预留）│ L3 长期（ChromaDB）                │
├──────────────────────────────────────────────────────────────────────┤
│   可观测性：结构化日志 │ 9 项 Eval 指标 │ Tracing（LangSmith/本地）   │
└──────────────────────────────────────────────────────────────────────┘
```

### 1.4 三个入口

| 入口 | 启动方式 | 默认端口 | 适用场景 |
|------|---------|---------|---------|
| **CLI** | `sekb chat` | - | 终端交互、评估测试、健康检查 |
| **REST API** | `uvicorn app.api.main:app` | 8000 | 程序集成、二次开发 |
| **前端 SPA** | `npm run dev` | 3000 | 图形化使用、文件上传、知识库管理 |

---

## 2. 环境要求与准备

### 2.1 本地开发环境

| 依赖 | 版本要求 | 说明 |
|------|---------|------|
| Python | ≥ 3.11 | 必须（LangGraph 原生生态） |
| pip | 最新 | 用于安装 Python 依赖 |
| Node.js | ≥ 18 | 前端构建必需 |
| npm | ≥ 9 | 前端依赖管理 |
| Git | 最新 | 克隆代码 |

**Python 虚拟环境**（推荐）：

```bash
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

### 2.2 外部服务依赖

| 服务 | 用途 | 必需性 | 获取地址 |
|------|------|--------|---------|
| **DeepSeek API Key** | LLM 调用（chat + reasoner 双模型） | ✅ 必需 | https://platform.deepseek.com |
| **博查搜索 API Key** | 联网搜索（国内直连） | ✅ 必需 | https://open.bochaai.com |
| LangSmith API Key | Tracing 可视化 | ⚪ 可选（不设置自动降级本地 JSON） | https://smith.langchain.com |
| Redis | L2 中期记忆 | ⚪ Phase 2 可选 | - |
| PostgreSQL | 会话存储迁移 | ⚪ Phase 2 可选 | - |

> **说明**：sentence-transformers 的 embedding 模型在首次启动时自动下载（约 400MB）。国内网络建议设置 HuggingFace 镜像：`export HF_ENDPOINT=https://hf-mirror.com`（`start_backend.sh` 已默认设置）。

### 2.3 生产部署环境

| 组件 | 最低要求 | 推荐 |
|------|---------|------|
| OS | Linux (Ubuntu 22.04+) | Ubuntu 24.04 LTS |
| CPU | 2 核 | 4 核 |
| 内存 | 4 GB | 8 GB |
| 磁盘 | 20 GB | 50 GB SSD |
| Docker | 24.0+ | 26.0+ |
| Docker Compose | v2.20+ | v2.30+ |

**安装 Docker**：

```bash
# Ubuntu/Debian
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# 重新登录使 docker 组生效
```

---

## 3. 快速开始（本地开发模式）

### 3.1 克隆与安装

```bash
# 1. 克隆项目
git clone https://github.com/your-org/SelfEvolvingKnowledgeBase.git
cd SelfEvolvingKnowledgeBase

# 2. 创建并激活 Python 虚拟环境
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. 安装后端依赖
pip install -r requirements.txt
# 安装可执行命令 sekb（提供 chat/eval/health 子命令）
pip install -e .

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env，填入真实密钥：
#   DEEPSEEK_API_KEY=sk-your-real-key
#   BOCHA_API_KEY=sk-your-real-key
#   JWT_SECRET=dev-secret-key-change-in-production   # 本地开发可用默认值

# 5. 检查 config.yaml（可选，默认即可运行）
cat config.yaml
```

### 3.2 健康检查

安装完成后，先验证 LLM、工具、Graph 是否就绪：

```bash
# 在 backend/ 目录下（需激活虚拟环境）
sekb health
```

输出示例（系统就绪）：

```
╭──────────────────── 健康检查 ────────────────────╮
│ 应用: SelfEvolvingKnowledgeBase v0.1.0           │
│ 环境: development                                │
│ LLM Provider: deepseek                           │
╰──────────────────────────────────────────────────╯
                检查项
  组件    状态          详情
  LLM     ✓ 健康
  工具    ✓ 健康        bocha_search=ok
  Graph   ✓ 已编译
系统就绪
```

> 若 LLM 异常，请检查 `.env` 中的 `DEEPSEEK_API_KEY` 与网络连通性。

### 3.3 CLI 聊天

```bash
# 启动交互式聊天（自动创建新会话）
sekb chat

# 指定已有会话 ID 继续对话
sekb chat --conv-id conv_xxx
```

### 3.4 启动 API 服务

```bash
# 方式一：直接 uvicorn（开发热重载）
cd backend
uvicorn app.api.main:app --host 0.0.0.0 --port 8000 --reload

# 方式二：使用项目根目录启动脚本（自动激活虚拟环境 + 安装依赖 + 设置 HF 镜像）
cd ..
./start_backend.sh
```

启动后访问：

- API 服务：http://localhost:8000
- OpenAPI 文档（Swagger UI）：http://localhost:8000/docs
- ReDoc 文档：http://localhost:8000/redoc

验证：

```bash
curl http://localhost:8000/api/v1/health/live
# 期望返回：{"status":"ok"}
```

### 3.5 启动前端

```bash
# 进入前端目录
cd frontend

# 安装依赖
npm install

# 启动开发服务器（默认端口 3000，已配置 /api 代理到后端 8000）
npm run dev
```

或使用项目根目录启动脚本（自动设置 VITE_BACKEND_PORT / VITE_FRONTEND_PORT）：

```bash
cd ..
./start_frontend.sh
```

启动后访问前端：http://localhost:3000

> **端口说明**：前端开发端口默认 3000，可通过环境变量 `VITE_FRONTEND_PORT` 覆盖；后端代理目标默认 8000，可通过 `VITE_BACKEND_PORT` 覆盖。Vite 已为 `/api` 路径配置代理并禁用 SSE 缓冲，流式聊天可直接使用。

---

## 4. API 使用指南

所有 API 均以 `/api/v1` 为前缀。**鉴权规则**：

- ✅ **需要 JWT**：`/api/v1/auth/me`、`/api/v1/auth/logout`、`/api/v1/conversations/*`、`/api/v1/knowledge/*`
- ⚪ **无需鉴权**：`/api/v1/auth/register`、`/api/v1/auth/login`、`/api/v1/chat/*`、`/api/v1/upload/*`、`/api/v1/health/*`、`/metrics`

> 聊天与文件上传接口当前使用固定 `user_id="default"`（与 CLI 保持一致），无需 JWT 即可调用。会话管理与知识库管理接口需在请求头携带 `Authorization: Bearer <token>`。

### 4.1 认证（注册 / 登录 / 获取 JWT）

#### 4.1.1 注册

```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "alice@example.com",
    "password": "P@ssw0rd123",
    "name": "Alice"
  }'
```

响应：

```json
{
  "user": {
    "user_id": "usr_xxx",
    "email": "alice@example.com",
    "name": "Alice",
    "avatar_url": null,
    "settings": {},
    "created_at": "2026-08-19T10:00:00"
  },
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

#### 4.1.2 登录

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "email": "alice@example.com",
    "password": "P@ssw0rd123"
  }'
```

响应同注册，返回 `user` + `token`。后续需鉴权的请求请将 `token` 放入 `Authorization` 头。

#### 4.1.3 获取当前用户

```bash
curl http://localhost:8000/api/v1/auth/me \
  -H "Authorization: Bearer <token>"
```

#### 4.1.4 更新用户信息

仅允许更新 `name` / `avatar_url` / `settings` 字段。

```bash
curl -X PATCH http://localhost:8000/api/v1/auth/me \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Alice 2.0",
    "settings": {"theme": "dark"}
  }'
```

#### 4.1.5 登出

```bash
curl -X POST http://localhost:8000/api/v1/auth/logout \
  -H "Authorization: Bearer <token>"
```

> 登出为客户端语义：服务端仅记录审计日志，客户端需自行清除 token。

### 4.2 聊天 API

#### 4.2.1 非流式聊天

```bash
curl -X POST http://localhost:8000/api/v1/chat/ \
  -H "Content-Type: application/json" \
  -d '{
    "message": "2026 年诺贝尔物理学奖得主是谁？",
    "conversation_id": null,
    "user_id": "default"
  }'
```

**请求体字段**：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `message` | string | 是 | 用户输入文本（min_length=1） |
| `conversation_id` | string\|null | 否 | 会话 ID；为 `null` 表示新建会话 |
| `user_id` | string | 否 | 用户 ID，默认 `"default"` |

#### 4.2.2 SSE 流式聊天

```bash
curl -N -X POST http://localhost:8000/api/v1/chat/stream \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Python 装饰器怎么用？",
    "conversation_id": null
  }'
```

流式响应（SSE 事件）：

```
data: {"type":"token","content":"装"}
data: {"type":"token","content":"饰"}
...
data: {"type":"done","meta":{"conversation_id":"conv_xxx","intent":"web_default","intent_confidence":0.92,"metrics":{...},"trace_id":"uuid","latency_ms":4200,"ingest_status":"success"}}
```

- `token` 事件：逐字符推送回复内容
- `done` 事件：携带完整元信息（会话 ID、意图、指标、trace 等）
- `error` 事件：出错时推送并结束流

#### 4.2.3 继续已有会话

```bash
curl -X POST http://localhost:8000/api/v1/chat/ \
  -H "Content-Type: application/json" \
  -d '{
    "message": "再深入讲讲",
    "conversation_id": "conv_xxx"
  }'
```

### 4.3 会话管理 API

> 以下接口均需 `Authorization: Bearer <token>`。

#### 4.3.1 列出会话

```bash
curl "http://localhost:8000/api/v1/conversations/?limit=50&offset=0" \
  -H "Authorization: Bearer <token>"
```

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `limit` | int | 50 | 1-200 |
| `offset` | int | 0 | 分页偏移 |

#### 4.3.2 创建新会话

```bash
curl -X POST "http://localhost:8000/api/v1/conversations/?title=我的新会话" \
  -H "Authorization: Bearer <token>"
```

#### 4.3.3 获取会话详情（含消息历史）

```bash
curl http://localhost:8000/api/v1/conversations/conv_xxx \
  -H "Authorization: Bearer <token>"
```

响应：

```json
{
  "conversation": {"conv_id": "conv_xxx", "user_id": "usr_xxx", "title": "...", ...},
  "messages": [{"msg_id": "...", "role": "user", "content": "..."}, ...]
}
```

#### 4.3.4 获取会话消息列表

```bash
curl http://localhost:8000/api/v1/conversations/conv_xxx/messages \
  -H "Authorization: Bearer <token>"
```

#### 4.3.5 重命名会话

```bash
curl -X PATCH http://localhost:8000/api/v1/conversations/conv_xxx \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"title": "新标题"}'
```

#### 4.3.6 删除会话

```bash
curl -X DELETE http://localhost:8000/api/v1/conversations/conv_xxx \
  -H "Authorization: Bearer <token>"
```

#### 4.3.7 消息反馈（thumbs up / down）

```bash
curl -X POST http://localhost:8000/api/v1/conversations/conv_xxx/rate \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "msg_id": "msg_yyy",
    "rating": "thumbs_up",
    "comment": "回答很准确"
  }'
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `msg_id` | string | 是 | 被反馈的消息 ID |
| `rating` | string | 是 | `thumbs_up` 或 `thumbs_down` |
| `comment` | string\|null | 否 | 文字反馈（max 1000 字） |

### 4.4 文件上传 API

> 当前使用固定 `user_id="default"`，无需 JWT。单文件上限 50MB。

#### 4.4.1 上传文件

支持 PDF / Word / TXT 等格式，文件经解析 → 分块 → 入库到 L3 知识库。

```bash
# 同步处理（默认）
curl -X POST http://localhost:8000/api/v1/upload \
  -F "file=@/path/to/document.pdf"

# 异步处理（大文件建议开启，立即返回占位响应，后台入库）
curl -X POST "http://localhost:8000/api/v1/upload?async_process=true" \
  -F "file=@/path/to/large_document.pdf"
```

响应：

```json
{
  "file_name": "document.pdf",
  "file_size": 102400,
  "chunks_count": 12,
  "ingested_count": 12,
  "status": "success",
  "error": "",
  "entry_ids": ["entry_1", "entry_2", "..."]
}
```

`status` 取值：`success`（全部成功）/ `partial`（部分成功）/ `error`（全部失败）。

#### 4.4.2 查询知识库状态

```bash
curl http://localhost:8000/api/v1/upload/status
```

响应：

```json
{
  "total_entries": 128,
  "l3_enabled": true,
  "user_id": "default"
}
```

> 若 L3 知识库未启用（`l3_enabled=false`），上传与删除接口返回 503。

#### 4.4.3 删除知识条目

```bash
curl -X DELETE http://localhost:8000/api/v1/upload/entries/entry_xxx
```

响应（幂等语义，条目不存在也返回 `deleted=true`）：

```json
{
  "entry_id": "entry_xxx",
  "deleted": true
}
```

### 4.5 知识库管理 API

> 以下接口均需 `Authorization: Bearer <token>`。

#### 4.5.1 知识列表（分页）

```bash
curl "http://localhost:8000/api/v1/knowledge/?source=document&page=1&page_size=20" \
  -H "Authorization: Bearer <token>"
```

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `source` | string | null | 按来源筛选（如 `document` / `conversation`） |
| `page` | int | 1 | 页码（≥1） |
| `page_size` | int | 20 | 每页条数（1-100） |

响应：

```json
{
  "entries": [
    {
      "entry_id": "entry_xxx",
      "content": "前 200 字预览...",
      "source": "document",
      "source_id": "document.pdf",
      "importance_score": 0.5,
      "version": 1,
      "created_at": "2026-08-19T10:00:00",
      "user_id": "usr_xxx"
    }
  ],
  "total": 128,
  "page": 1,
  "page_size": 20
}
```

#### 4.5.2 知识搜索（向量检索）

```bash
curl "http://localhost:8000/api/v1/knowledge/search?q=PDCA%20%E5%BE%AA%E7%8E%AF&top_k=10" \
  -H "Authorization: Bearer <token>"
```

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `q` | string | - | 搜索关键词（必填，min_length=1） |
| `top_k` | int | 10 | 返回条数（1-50） |

响应包含 `similarity_score` 相似度得分。

#### 4.5.3 删除知识条目

```bash
curl -X DELETE http://localhost:8000/api/v1/knowledge/entry_xxx \
  -H "Authorization: Bearer <token>"
```

### 4.6 健康检查 API

#### 4.6.1 综合健康检查

```bash
curl http://localhost:8000/api/v1/health/
```

响应（聚合 LLM / 工具 / 存储 / Graph 状态）：

```json
{
  "status": "ok",
  "services": {
    "llm": {"status": "ok"},
    "tools": {"status": "ok", "details": {"bocha_search": true}},
    "storage": {"status": "ok"},
    "graph": {"status": "ok"}
  },
  "app": {"name": "SelfEvolvingKnowledgeBase", "version": "0.1.0", "environment": "development"}
}
```

`status` 取值：`ok`（核心依赖就绪）/ `degraded`（核心依赖异常）。

#### 4.6.2 存活探针（liveness）

```bash
curl http://localhost:8000/api/v1/health/live
# {"status":"ok"}
```

进程能响应即返回 ok，不检查外部依赖，适用于 K8s livenessProbe。

#### 4.6.3 就绪探针（readiness）

```bash
curl http://localhost:8000/api/v1/health/ready
```

检查 LLM 可达 + Graph 已编译。任一不可用返回 HTTP 503：

```json
{"status": "not_ready", "checks": {"llm": false, "graph": true}}
```

#### 4.6.4 Prometheus 指标

```bash
curl http://localhost:8000/metrics
```

返回 Prometheus 文本格式指标，供 Prometheus 抓取（无需鉴权）。

### 4.7 统一响应格式

**非流式聊天响应**（`POST /api/v1/chat/`）：

```json
{
  "conversation_id": "conv_xxx",
  "message": "助手回复文本...",
  "intent": "web_default",
  "intent_confidence": 0.92,
  "metrics": {
    "intent_confidence": {"value": 0.92, "status": "good"},
    "plan_step_count": {"value": 2, "status": "good"},
    "replan_count": {"value": 0, "status": "good"},
    "tool_success_rate": {"value": 1.0, "status": "good"},
    "answer_groundedness": {"value": 0.85, "status": "good"},
    "critic_coherence_score": {"value": 8.5, "status": "good"},
    "answer_relevance": {"value": 0.88, "status": "good"},
    "e2e_latency_ms": {"value": 4200, "status": "good"},
    "cost_usd": {"value": 0.0031, "status": "info"}
  },
  "trace_id": "uuid-xxx",
  "meta": {
    "ingest_status": "success",
    "ingest_reason": ""
  }
}
```

| 字段 | 说明 |
|------|------|
| `conversation_id` | 会话 ID（新建或续接） |
| `message` | 助手回复文本 |
| `intent` | Supervisor 识别的意图（`web_default` / `kb_strict` / `kb_prefer` / `chat_simple` 等） |
| `intent_confidence` | 意图置信度（0-1） |
| `metrics` | 9 项量化评估指标，每项含 `value` 与 `status`（good/warn/info） |
| `trace_id` | 本次调用 trace ID，用于链路追踪 |
| `meta.ingest_status` | 知识自迭代入库状态（success/skipped/disabled/error） |

**9 项评估指标说明**：

| 指标 | 含义 | 来源节点 |
|------|------|---------|
| `intent_confidence` | 意图识别置信度 | Supervisor |
| `plan_step_count` | 规划步数 | Planner |
| `replan_count` | 重规划次数 | Critic |
| `tool_success_rate` | 工具调用成功率 | Executor |
| `answer_groundedness` | 答案锚定度（防幻觉核心） | Critic |
| `critic_coherence_score` | 逻辑链自洽性 | Critic |
| `answer_relevance` | 回答相关性 | Critic |
| `e2e_latency_ms` | 端到端延迟（毫秒） | 全链路 |
| `cost_usd` | 单次对话成本（美元） | 全链路 |

---

## 5. CLI 使用指南

SEKB 提供 `sekb` 命令行工具（由 `pyproject.toml` 的 `[project.scripts]` 注册，`pip install -e .` 后可用）。所有子命令均支持 `--config` 指定配置文件路径（默认 `config.yaml`）。

### 5.1 sekb chat — 交互式聊天

```bash
# 启动新会话聊天
sekb chat

# 指定已有会话 ID 继续对话
sekb chat --conv-id conv_xxx

# 指定配置文件
sekb chat --config config.yaml
```

| 参数 | 简写 | 默认 | 说明 |
|------|------|------|------|
| `--conv-id` | `-c` | None | 会话 ID（不指定则自动创建新会话） |
| `--config` | - | `config.yaml` | 配置文件路径 |

交互式会话中输入消息回车发送，`Ctrl+C` 或输入 `exit` 退出。

### 5.2 sekb eval — 运行评估测试

批量运行黄金数据集，输出 Markdown 评估报告（需要真实 API Key）。

```bash
# 使用默认数据集与输出路径
sekb eval

# 指定数据集与报告输出路径
sekb eval \
  --dataset app/eval/datasets/golden_qa.json \
  --output tests/reports/eval_report.md

# 指定配置文件
sekb eval --config config.yaml
```

| 参数 | 简写 | 默认 | 说明 |
|------|------|------|------|
| `--dataset` | `-d` | `app/eval/datasets/golden_qa.json` | 测试数据集 JSON 文件路径 |
| `--output` | `-o` | `tests/reports/eval_report.md` | 评估报告输出路径（Markdown） |
| `--config` | - | `config.yaml` | 配置文件路径 |

### 5.3 sekb health — 健康检查

验证 LLM API 可达性、工具注册表健康状态、Graph 工作流是否已编译。

```bash
sekb health

# 指定配置文件
sekb health --config config.yaml
```

| 参数 | 默认 | 说明 |
|------|------|------|
| `--config` | `config.yaml` | 配置文件路径 |

检查项：LLM（API 可达）/ 工具（MCP ping 或直连模式）/ Graph（已编译）。任一异常退出码为 1，可用于 CI/CD 健康门禁。

---

## 6. 前端使用指南

### 6.1 访问前端

| 场景 | 访问地址 | 启动方式 |
|------|---------|---------|
| 本地开发 | http://localhost:3000 | `npm run dev`（Vite 开发服务器） |
| 生产部署 | http://localhost（默认 80 端口） | Docker Compose（Nginx 托管 SPA + 反代 `/api`） |

> 首次访问需注册账号或登录（前端路由对未登录用户重定向到 `/login`）。

### 6.2 页面功能

前端基于 React 18 + Ant Design 5 + Zustand 构建，左侧导航栏提供四个主功能页面：

| 路由 | 菜单 | 功能说明 |
|------|------|---------|
| `/` | 聊天 | 多会话聊天界面，支持 SSE 流式输出、Markdown 渲染、代码高亮、会话切换 |
| `/files` | 文件管理 | 上传 PDF/Word/TXT 文件到知识库，查看上传状态与已入库条目 |
| `/knowledge` | 知识库 | 浏览/搜索/删除知识条目，查看来源与重要性评分 |
| `/settings` | 设置 | 用户信息与偏好设置 |
| `/login` | - | 登录页（未登录可访问） |
| `/register` | - | 注册页（未登录可访问） |

**聊天页特性**：
- 流式逐字输出（SSE）
- 自动创建/切换会话
- 显示意图识别结果与评估指标
- 消息反馈（thumbs up/down）

### 6.3 前端开发

```bash
cd frontend

# 安装依赖
npm install

# 开发模式（热重载，默认端口 3000）
npm run dev

# 生产构建（输出到 dist/）
npm run build

# 预览生产构建
npm run preview

# 代码检查
npm run lint
```

**环境变量**（通过 `start_frontend.sh` 自动设置，或手动 export）：

| 变量 | 默认 | 说明 |
|------|------|------|
| `VITE_FRONTEND_PORT` | 3000 | 开发服务器端口 |
| `VITE_BACKEND_PORT` | 8000 | 后端 API 端口（用于 `/api` 代理） |
| `VITE_API_BASE` | `http://localhost:8000` | 后端 API 基地址 |

> Vite 已配置 `/api` 路径代理到后端，并针对 `text/event-stream` 响应禁用缓冲，确保 SSE 流式聊天可用。

---

## 7. Docker 生产部署

### 7.1 启动应用栈

```bash
# 配置生产环境变量
cp deploy/.env.prod.example .env.prod
```

编辑 `.env.prod`，**必须修改**以下项：

```bash
# LLM API 密钥
DEEPSEEK_API_KEY=sk-your-real-key
BOCHA_API_KEY=sk-your-real-key

# JWT 密钥（生成强随机串）
JWT_SECRET=$(openssl rand -hex 32)

# 可选：飞书告警通知
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/your-hook-id
```

启动应用栈：

```bash
# 构建并启动后端 + 前端
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
```

**首次构建耗时**：后端镜像约 10-15 分钟（torch + sentence-transformers 下载），前端约 2 分钟。

### 7.2 验证应用栈

```bash
# 检查容器状态
docker compose -f docker-compose.prod.yml --env-file .env.prod ps

# 验证后端健康
curl http://localhost:8000/api/v1/health/live
# 期望返回：{"status":"ok","services":{...}}

# 验证前端
curl -o /dev/null -w "%{http_code}" http://localhost/
# 期望返回：200
```

### 7.3 启动监控栈

```bash
docker compose -f docker-compose.monitoring.yml up -d
```

### 7.4 验证监控栈

| 服务 | URL | 账号 |
|------|-----|------|
| Grafana | http://localhost:3001 | admin / admin |
| Prometheus | http://localhost:9091 | 无需认证 |
| Alertmanager | http://localhost:9093 | 无需认证 |
| Loki | http://localhost:3101 | 无需认证 |

Grafana 首次登录后请立即修改密码。

### 7.5 启用 HTTPS（生产推荐）

```bash
# 1. 申请 Let's Encrypt 证书
sudo certbot certonly --standalone -d bos-studio.tech

# 2. 复制 SSL 配置
cp deploy/nginx-ssl.conf deploy/nginx.conf

# 3. 修改证书路径（编辑 deploy/nginx.conf）
#    ssl_certificate /etc/letsencrypt/live/bos-studio.tech/fullchain.pem;

# 4. 在 docker-compose.prod.yml 中挂载证书目录
#    volumes:
#      - ./deploy/nginx.conf:/etc/nginx/conf.d/default.conf:ro
#      - /etc/letsencrypt:/etc/letsencrypt:ro

# 5. 重启前端
docker compose -f docker-compose.prod.yml --env-file .env.prod restart frontend
```

---

## 8. 日常运维

### 8.1 启动 / 停止 / 重启

> ⚠ 以下所有 docker compose 命令均需添加 --env-file .env.prod 参数，下文为简洁已省略

```bash
# 启动全部
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d
docker compose -f docker-compose.monitoring.yml up -d

# 停止全部
docker compose -f docker-compose.prod.yml down
docker compose -f docker-compose.monitoring.yml down

# 重启单个服务
docker compose -f docker-compose.prod.yml restart backend
docker compose -f docker-compose.monitoring.yml restart prometheus

# 查看日志
docker compose -f docker-compose.prod.yml logs -f backend
docker compose -f docker-compose.prod.yml logs --tail 100 frontend

# 查看监控栈日志
docker compose -f docker-compose.monitoring.yml logs -f prometheus
```

### 8.2 更新部署

```bash
# 拉取最新代码
git pull origin main

# 重新构建并启动
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build

# 等待健康检查
sleep 30
curl http://localhost:8000/api/v1/health/live
```

### 8.3 CI/CD 自动部署

配置 GitHub Secrets 后，push 到 `main` 分支会自动触发部署：

```bash
# 需要配置的 Secrets：
# DEEPSEEK_API_KEY     - LLM 密钥
# BOCHA_API_KEY        - 搜索密钥
# JWT_SECRET           - JWT 签名密钥
# DEPLOY_HOST          - 部署服务器 IP
# DEPLOY_USER          - SSH 用户
# DEPLOY_KEY           - SSH 私钥
# DEPLOY_PATH          - 部署路径（默认 /opt/self-evolving-kb）
```

CI/CD 部署日志包含 8 个步骤的详细输出，便于排查失败原因：
1. 部署前状态快照
2. 拉取最新代码
3. 拉取最新镜像
4. 部署前备份
5. 滚动更新应用栈
6. 更新监控栈
7. 部署后验证
8. 清理与收尾

---

## 9. 备份与恢复

### 9.1 手动备份

```bash
# 执行备份（备份 PG/Redis/ChromaDB/会话数据/配置）
./deploy/backup.sh

# 查看备份列表
ls -la /backup/

# 备份到 S3（需配置 S3_BUCKET 环境变量）
S3_BUCKET=your-bucket ./deploy/backup.sh
```

### 9.2 定时备份

```bash
# 添加 cron 任务（每日凌晨 3 点备份）
crontab -e

# 添加以下行：
0 3 * * * cd /opt/self-evolving-kb && ./deploy/backup.sh >> /var/log/sekb-backup.log 2>&1
```

**保留策略**：本地保留 7 天，S3 保留 30 天。

### 9.3 恢复数据

```bash
# 列出可用备份
./deploy/restore.sh --list

# 恢复最新备份（仅数据）
./deploy/restore.sh --latest

# 恢复指定日期的备份
./deploy/restore.sh --date 20240115_030000

# 恢复含 PostgreSQL 数据库
./deploy/restore.sh --date 20240115_030000 --with-db

# 从 S3 下载并恢复
./deploy/restore.sh --date 20240115_030000 --from-s3
```

**⚠️ 恢复前注意事项**：
- 恢复会覆盖现有数据
- 建议在低峰期执行
- 恢复前先停止后端：`docker compose -f docker-compose.prod.yml --env-file .env.prod stop backend`
- 恢复脚本会自动停止/启动后端

---

## 10. 灰度发布与回滚

### 10.1 灰度发布流程

```bash
# 步骤 1：备份当前 nginx 配置
cp deploy/nginx.conf deploy/nginx.conf.bak

# 步骤 2：启动灰度版后端容器（新版本镜像）
docker run -d --rm \
  --name backend-canary \
  --network sekb_network \
  -e DEEPSEEK_API_KEY=$(grep DEEPSEEK .env.prod | cut -d= -f2) \
  -e BOCHA_API_KEY=$(grep BOCHA .env.prod | cut -d= -f2) \
  -e JWT_SECRET=$(grep JWT_SECRET .env.prod | cut -d= -f2) \
  -v sekb_data:/app/data \
  self-evolving-kb-backend:canary

# 步骤 3：切换 nginx 到灰度配置（10% 流量到灰度版）
cp deploy/nginx-canary.conf deploy/nginx.conf
docker exec sekb-frontend nginx -s reload

# 步骤 4：启动灰度监控（后台运行，自动回滚/提升）
nohup ./deploy/canary_monitor.sh > /var/log/sekb-canary.log 2>&1 &
```

### 10.2 灰度监控参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `ERROR_THRESHOLD` | 0.05 | 错误率阈值（5%） |
| `LATENCY_P95_THRESHOLD` | 30 | P95 延迟阈值（秒） |
| `CHECK_INTERVAL` | 30 | 检查间隔（秒） |
| `DURATION_MIN` | 30 | 最短观察时长（分钟） |

```bash
# 自定义参数
ERROR_THRESHOLD=0.03 LATENCY_P95_THRESHOLD=20 DURATION_MIN=60 ./deploy/canary_monitor.sh
```

### 10.3 灰度结果

- **自动提升**：观察期 30 分钟内全部检查通过 → 灰度版自动提升为稳定版
- **自动回滚**：失败检查累计 3 次 → 自动停止灰度容器，恢复 nginx 稳定配置

### 10.4 手动回滚

```bash
# 紧急手动回滚
docker stop backend-canary
docker rm backend-canary

# 恢复 nginx 稳定配置
cp deploy/nginx.conf.bak deploy/nginx.conf
docker exec sekb-frontend nginx -s reload

# 验证
curl http://localhost:8000/api/v1/health/live
```

### 10.5 调整灰度比例

编辑 `deploy/nginx-canary.conf` 中的 `split_clients` 块：

```nginx
split_clients "${remote_addr}${http_user_agent}" $backend_upstream {
    10%  backend_canary;   # 修改此数值调整灰度比例
    *   backend_stable;
}
```

修改后重载 nginx：

```bash
docker exec sekb-frontend nginx -s reload
```

---

## 11. 监控与告警

### 11.1 访问监控面板

- **Grafana 看板**：http://localhost:3001 → "SEKB" 文件夹 → "SEKB 系统总览"
- **Prometheus**：http://localhost:9091 → Alerts 页面查看告警状态
- **Alertmanager**：http://localhost:9093 → 查看告警通知

### 11.2 告警规则概览

| 告警 | 级别 | 触发条件 |
|------|------|---------|
| BackendDown | critical | 后端不可达 1 分钟 |
| BackendDegraded | warning | 健康检查降级 2 分钟 |
| HighLatencyP95 | warning | P95 延迟 > 15 秒 |
| BudgetExceeded | warning | 日成本 > $5 |
| LowGroundedness | warning | 答案锚定度 < 0.6 持续 15 分钟 |
| ToolFailureHigh | critical | 工具失败率 > 10% |
| HighErrorRate | critical | 请求错误率 > 5% |

### 11.3 配置飞书告警

1. 在飞书群中添加自定义机器人，获取 webhook URL
2. 编辑 `.env.prod`：

```bash
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/your-hook-id
FEISHU_SECRET=your-signing-secret  # 可选
```

3. 重启 feishu-webhook 服务：

```bash
docker compose -f docker-compose.monitoring.yml restart feishu-webhook
```

### 11.4 查看审计日志

```bash
# 审计日志文件路径
docker exec sekb-backend cat /app/data/audit/audit.log | tail -20

# 或从挂载卷查看
docker run --rm -v sekb_data:/data alpine cat /data/audit/audit.log | tail -20
```

审计日志记录所有安全相关操作：登录/注册/登出/知识库 CRUD/用户管理等。

---

## 12. 故障排查

### 12.1 常见问题

#### 后端无法启动

```bash
# 查看后端日志
docker compose -f docker-compose.prod.yml --env-file .env.prod logs backend --tail 50

# 常见原因：
# 1. DEEPSEEK_API_KEY / BOCHA_API_KEY / JWT_SECRET 未设置
# 2. 数据卷权限问题（确保 appuser 用户可写 /app/data）
# 3. embedding 模型下载失败（网络问题）
```

#### 前端 502 Bad Gateway

```bash
# 检查后端是否健康
curl http://localhost:8000/api/v1/health/live

# 检查 nginx 配置
docker exec sekb-frontend nginx -t

# 检查 nginx 是否能连接到后端
docker exec sekb-frontend wget -qO- http://backend:8000/api/v1/health/live
```

#### SSE 流式响应不工作

```bash
# 确认 nginx 关闭了缓冲（deploy/nginx.conf 中应包含）
#   proxy_buffering off;
#   proxy_cache off;
#   proxy_read_timeout 300s;

# 检查响应头
curl -v -X POST http://localhost/api/v1/chat/stream \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"message":"hi"}' 2>&1 | grep -i "transfer-encoding\|content-type"
```

#### Prometheus 抓取失败

```bash
# 检查抓取目标状态
curl http://localhost:9091/api/v1/targets | python3 -m json.tool

# 常见原因：
# 1. backend 容器未在 sekb_network 网络中
# 2. backend /metrics 端点未返回 200
# 3. scrape_timeout 太短（默认 10s）
```

#### Alertmanager 不发送通知

```bash
# 检查 Alertmanager 配置
docker exec sekb-alertmanager amtool check-config /etc/alertmanager/alertmanager.yml

# 检查 Alertmanager 日志
docker logs sekb-alertmanager --tail 20

# 手动测试飞书 webhook
curl -X POST http://localhost:5001/webhook \
  -H "Content-Type: application/json" \
  -d '{"alerts":[{"status":"firing","labels":{"alertname":"TestAlert","severity":"warning"},"annotations":{"summary":"测试告警","description":"这是一条测试告警"}}]}'
```

#### 本地开发：sekb 命令未找到

```bash
# 原因：未执行 pip install -e . 安装 CLI 入口
# 解决：在 backend/ 目录下激活虚拟环境后安装
cd backend
source .venv/bin/activate
pip install -e .

# 验证
which sekb
sekb --help
```

#### 本地开发：embedding 模型下载失败

```bash
# 原因：国内网络访问 HuggingFace 受限
# 解决：设置 HF 镜像后重试
export HF_ENDPOINT=https://hf-mirror.com

# 或直接使用启动脚本（已内置镜像设置）
./start_backend.sh
```

#### 本地开发：前端无法连接后端 / SSE 不流式

```bash
# 原因 1：后端未启动或端口不对
curl http://localhost:8000/api/v1/health/live

# 原因 2：Vite 代理端口配置错误
# 检查 vite.config.ts 中 proxy.target 是否指向后端端口
# 或通过环境变量覆盖：
export VITE_BACKEND_PORT=8000
npm run dev

# 原因 3：浏览器缓存，强制刷新（Ctrl+Shift+R）
```

#### 本地开发：会话/知识库数据丢失

```bash
# 原因：本地 JSON 存储默认在 backend/data/ 目录
# 检查数据目录
ls -la backend/data/

# 若误删，可重新创建空目录恢复运行
mkdir -p backend/data/conversations
```

### 12.2 日志位置

| 服务 | 日志位置 |
|------|---------|
| 后端应用日志 | `docker compose logs backend` |
| 后端审计日志 | `data/audit/audit.log`（容器内 `/app/data/audit/`） |
| nginx access log | `docker exec sekb-frontend cat /var/log/nginx/access.log` |
| Prometheus | `docker logs sekb-prometheus` |
| Grafana | `docker logs sekb-grafana` |
| Loki | `docker logs sekb-loki` |
| Promtail | `docker logs sekb-promtail` |
| Alertmanager | `docker logs sekb-alertmanager` |
| 飞书 webhook | `docker logs sekb-feishu-webhook` |
| 部署日志 | GitHub Actions → 对应 workflow run |
| 备份日志 | `/var/log/sekb-backup.log` |
| 灰度监控日志 | `/var/log/sekb-canary.log` |
| 本地后端日志 | 终端 stdout（uvicorn 前台运行） |
| 本地 CLI 日志 | 终端 stdout |

### 12.3 清理重置

```bash
# 停止并删除所有容器（保留数据卷）
docker compose -f docker-compose.prod.yml --env-file .env.prod down
docker compose -f docker-compose.monitoring.yml down

# 彻底清理（包括数据卷，谨慎操作！）
docker compose -f docker-compose.prod.yml --env-file .env.prod down -v
docker compose -f docker-compose.monitoring.yml down -v
docker volume rm sekb_data sekb_prometheus_data sekb_grafana_data sekb_loki_data

# 清理悬挂镜像
docker image prune -f
```

---

## 13. 附录：服务端口映射

### 13.1 本地开发端口

| 服务 | 端口 | 说明 |
|------|------|------|
| 后端 API | 8000 | FastAPI（uvicorn），可经 `start_backend.sh` 中 `PROJECT_ID` 偏移 |
| 前端开发服务器 | 3000 | Vite dev server（可经 `VITE_FRONTEND_PORT` 覆盖） |
| OpenAPI 文档 | 8000 | http://localhost:8000/docs |

### 13.2 生产部署端口

| 服务 | 容器内端口 | 宿主机端口 | 说明 |
|------|-----------|-----------|------|
| backend | 8000 | 8000 | FastAPI 后端 |
| frontend | 80 | 80 | Nginx 前端 + 反代（可经 `FRONTEND_PORT` 覆盖） |
| prometheus | 9090 | 9091 | 指标采集 |
| grafana | 3000 | 3001 | 可视化看板 |
| loki | 3100 | 3101 | 日志聚合 |
| alertmanager | 9093 | 9093 | 告警路由 |
| feishu-webhook | 5001 | 5001 | 飞书通知中转 |
| backend-canary | 8001 | - | 灰度版后端（不对外暴露） |
| postgres（可选） | 5432 | 5432 | Phase 2 存储迁移（profile=with-db） |
| redis（可选） | 6379 | 6379 | Phase 2 L2 记忆（profile=with-db） |
