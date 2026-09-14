# SelfEvolvingKnowledgeBase

<div align="center">

**具备元认知能力的自迭代个人知识助手**

[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![LangGraph](https://img.shields.io/badge/langgraph-1.0+-green.svg)](https://langchain-ai.github.io/langgraph/)
[![FastAPI](https://img.shields.io/badge/fastapi-0.115+-teal.svg)](https://fastapi.tiangolo.com/)
[![License](https://img.shields.io/badge/license-MIT-yellow.svg)](#)

基于 **PDCA 循环**（Plan-Do-Check-Act）哲学，由五个智能体角色（Supervisor / Planner / Executor / Critic / Scribe）协作落地的多 Agent 知识助手。默认联网增强，内置反思机制与知识库自迭代闭环。

</div>

---

## 目录

- [项目简介](#项目简介)
- [Phase 功能特性](#phase-功能特性)
- [架构图](#架构图)
- [快速开始](#快速开始)
- [API 端点列表](#api-端点列表)
- [项目结构](#项目结构)
- [测试说明](#测试说明)
- [技术栈](#技术栈)

---

## 项目简介

**SelfEvolvingKnowledgeBase** 是一个基于 LangGraph 构建的多智能体知识助手，核心哲学是 **PDCA 循环**的工程化落地：

| 阶段 | 负责 Agent | 职责 |
|------|-----------|------|
| **Plan（计划）** | Supervisor → Planner | 意图识别 + 路由决策 → 任务拆解为 Task List |
| **Do（执行）** | Executor | 工具调用（联网搜索 / RAG 检索 / 文件解析）→ 生成草稿 |
| **Check（检查）** | Critic | 多维度反思评估，不通过则回滚重规划（最多 2 次） |
| **Act（处理）** | Scribe | 摘要 + 重要性评分 + 知识沉淀，异步写入知识库 |

**核心特性**：
- 🔀 **混合 LLM 策略**：`deepseek-chat`（快思考）+ `deepseek-reasoner`（慢思考）按角色分级
- 🌐 **默认联网增强**：除非用户明确要求"基于已有知识"，回答默认联网检索
- 🧠 **三层记忆架构**：L1 短期（工作记忆）→ L2 中期（会话偏好）→ L3 长期（知识库）
- 🔍 **RAG 检索增强**：向量库直连（<10ms），支持 `kb_strict` / `kb_prefer` / `web_default` 三种模式
- ♻️ **知识库自迭代**：高质量对话自动入库，冲突检测与版本管理
- 📊 **9 项量化评估**：每次对话输出可追溯的评估指标
- 🔌 **MCP 协议**：外部工具走 MCP 标准协议，本地高频组件直连

---

## Phase 功能特性

### Phase 1 — 多 Agent 聊天核心（已实现 ✅）

| 编号 | 功能 | 说明 |
|------|------|------|
| P0-1 | LangGraph 多 Agent 工作流 | Supervisor → Planner → Executor → Critic → Scribe 全链路打通 |
| P0-2 | 五个 Agent 节点 | 各节点输入/输出/Prompt 完整，含路由逻辑与错误处理 |
| P0-3 | 混合 LLM 策略 | chat + reasoner 分级调用，按角色配置 temperature/max_tokens |
| P0-4 | MCP 工具接入 | 博查搜索 MCP Server 自实现 + MCP Client 统一封装 |
| P0-5 | L1 短期记忆 | 滑动窗口 + 触发压缩（token 超阈值自动摘要） |
| P0-6 | 本地 JSON 存储 | 多会话/多轮/摘要/索引完整，append-only 写入 |
| P0-7 | 反思策略接口 | `AlwaysReflectStrategy` 默认实现，策略可扩展 |
| P0-8 | 量化评估日志 | 9 项指标每次对话输出，含 meaning/interpretation |
| P0-9 | CLI + FastAPI 双入口 | `sekb chat` 交互聊天 + REST API（含 SSE 流式） |
| P0-10 | 测试金字塔 | 单元测试 + 集成测试 + Eval 集，覆盖核心模块 |

### Phase 2 — 记忆系统 + RAG + 知识库（已实现 ✅）

| 编号 | 功能 | 状态 |
|------|------|------|
| P0-1 | L2 中期记忆（Redis） | 接口预留，待实现 |
| P0-2 | L3 长期知识库（ChromaDB） | ✅ 已实现：添加/检索/更新/删除/计数 |
| P0-3 | RAG 检索增强 | ✅ 已实现：`kb_strict` / `kb_prefer` / `web_default` 三种模式 |
| P0-4 | 知识库自迭代 | ✅ 已实现：Scribe 自动入库 + 重要性评分 |
| P0-5 | PostgreSQL 存储切换 | 设计完成，待实现迁移脚本 |
| P0-6 | 文件上传处理 | ✅ 已实现：PDF/Word/TXT 解析 → 分块 → 入库 |
| P0-7 | 向量检索直连 | ✅ 已实现：`DirectVectorStore` 绕过 MCP，<10ms |

### Phase 3 — 前端 + 多用户 + 鉴权（已实现 ✅）

| 编号 | 功能 | 说明 |
|------|------|------|
| P0-1 | React SPA 前端 | 聊天/文件上传/知识库管理/设置 4 个页面 |
| P0-2 | 用户注册/登录 | JWT 鉴权，PBKDF2-SHA256 密码哈希 |
| P0-3 | 会话管理 API | 列表/详情/重命名/删除/消息反馈 |
| P0-4 | 知识库管理 API | 搜索/列表/删除知识条目 |
| P0-5 | API 安全 | CORS + 速率限制 + 审计日志 |
| P0-6 | SSE 流式聊天 | 前端实时流式渲染助手回复 |

### Phase 4 — 生产部署 + 监控 + CI/CD（已实现 ✅）

| 编号 | 功能 | 说明 |
|------|------|------|
| P0-1 | Docker 容器化 | 后端 + 前端 + 数据库一键部署 |
| P0-2 | 监控栈 | Prometheus + Grafana + Loki + Alertmanager |
| P0-3 | CI/CD Pipeline | GitHub Actions：lint → 测试 → 构建 → 部署 |
| P0-4 | 告警通知 | Alertmanager + 飞书 Webhook 中转 |
| P0-5 | 灰度发布 | Nginx 流量分流 + 自动回滚监控 |
| P0-6 | 备份恢复 | PostgreSQL/Redis/ChromaDB/配置 定时备份 |
| P0-7 | 安全加固 | CSP 安全头 + SSL/TLS + 审计日志 |
| P0-8 | HTTPS 支持 | Let's Encrypt 证书 + Nginx SSL 配置 |

---

## 架构图

### 系统总体架构

```
┌──────────────────────────────────────────────────────────────────────┐
│                        用户接入层                                    │
│   ┌──────────────────┐         ┌──────────────────┐                 │
│   │   CLI 交互入口    │         │  FastAPI HTTP    │                 │
│   │  (typer/rich)    │         │  (支持 SSE 流式)  │                 │
│   └────────┬─────────┘         └────────┬─────────┘                 │
│            └────────────┬───────────────┘                            │
│                         ▼                                            │
├──────────────────────────────────────────────────────────────────────┤
│                      LangGraph 工作流引擎                            │
│                         ┌──────────────┐                             │
│                         │  Graph State │  (TypedDict)                │
│                         └──────┬───────┘                             │
│   ┌────────────────────────────┼────────────────────────────┐       │
│   │                            │                            │       │
│   ▼                            ▼                            ▼       │
│ [Supervisor] ──► [Router] ──► [Planner] ──► [Executor] ──► [Critic] │
│   意图识别        路由分发       任务拆解      工具调用       反思评估 │
│   + 预检索                       Task List    + 草稿生成              │
│        │                                                        │
│        │      ┌──────────────────────────────────────┐           │
│        └─────►│  简单闲聊直通 chat_simple，跳过 P/D  │           │
│               └──────────────────────────────────────┘           │
│                                                                    │
│                          Critic 不通过                             │
│                       ◄──────────────────── 回滚到 Planner         │
│                          (max_replan=2)                            │
│                                                                    │
│   最终答案 ◄──── [Scribe]（异步：摘要 + 重要性评分）                │
├──────────────────────────────────────────────────────────────────────┤
│                          工具层                                      │
│   ┌──────────────────────┐    ┌──────────────────────────────┐     │
│   │  MCP 标准协议层       │    │  本地直连层（绕过 MCP）       │     │
│   │  - 博查搜索 MCP       │    │  - ChromaDB 向量检索         │     │
│   │  - 文件解析 MCP       │    │  - 本地 JSON 存储             │     │
│   └──────────────────────┘    └──────────────────────────────┘     │
├──────────────────────────────────────────────────────────────────────┤
│                          LLM 工厂                                    │
│   DeepSeek API  ├─ deepseek-chat     （快思考：识别/执行/摘要）       │
│                 └─ deepseek-reasoner （慢思考：规划/复杂反思）        │
├──────────────────────────────────────────────────────────────────────┤
│                          记忆系统                                    │
│   ┌────────────┐  ┌────────────┐  ┌────────────────────┐           │
│   │ L1 短期    │  │ L2 中期    │  │ L3 长期知识库      │           │
│   │ 工作记忆   │  │ 会话偏好   │  │ ChromaDB 向量库    │           │
│   │ (已实现)   │  │ (预留)     │  │ (已实现)           │           │
│   └────────────┘  └────────────┘  └────────────────────┘           │
├──────────────────────────────────────────────────────────────────────┤
│                          存储层                                      │
│   ┌──────────────────────┐  ┌──────────────────────────────┐       │
│   │ 本地 JSON (已实现)   │  │ PostgreSQL (预留接口)         │       │
│   │ - conversations/     │  │ - 会话表                     │       │
│   │ - index.json         │  │ - 消息表                     │       │
│   │ - traces/            │  │ - 知识表                     │       │
│   └──────────────────────┘  └──────────────────────────────┘       │
├──────────────────────────────────────────────────────────────────────┤
│                          可观测性层                                  │
│   ┌────────────┐  ┌────────────┐  ┌────────────────────┐           │
│   │ 结构化日志 │  │ Eval 指标  │  │ Tracing            │           │
│   │ (JSON)     │  │ (9 项量化) │  │ LangSmith/本地JSON │           │
│   └────────────┘  └────────────┘  └────────────────────┘           │
└──────────────────────────────────────────────────────────────────────┘
```

### PDCA 循环映射

```
┌────────────────────────────────────────────────────────────────┐
│                      PDCA 循环映射                              │
├────────────────────────────────────────────────────────────────┤
│   Plan  ┌──► Supervisor（监督者）：意图识别 + 路由决策          │
│         │    ► Planner（规划者）：复杂任务拆解为 Task List       │
│         │                                                      │
│   Do    ├──► Executor（执行器）：工具调用 + 草稿生成            │
│         │                                                      │
│   Check └──► Critic（审查者）：多维度反思评估，不通过则回滚      │
│                                                                  │
│   Act   ────► Scribe（记录员）：摘要 + 重要性评分 + 知识沉淀    │
│                                                                  │
└────────────────────────────────────────────────────────────────┘
```

---

## 快速开始

### 环境要求

| 依赖 | 版本要求 | 说明 |
|------|---------|------|
| Python | ≥ 3.11 | 必须 |
| pip | 最新 | 用于安装依赖 |
| DeepSeek API Key | - | LLM 调用必需 |
| 博查搜索 API Key | - | 联网搜索必需 |
| LangSmith API Key | - | 可选，Tracing 可视化 |
| Redis | - | Phase 2 L2 记忆可选 |
| PostgreSQL | - | Phase 2 存储迁移可选 |

### 安装

> **⚠️ 本仓库含 git 子模块 `jobcopilot`（职位分析内核，独立仓库）。**
> 克隆时**必须**一并拉取，否则后端构建缺依赖、`import jobcopilot` 直接失败。
>
> ```bash
> # 方式一（推荐）：克隆时递归拉子模块
> git clone --recurse-submodules <repo-url> SelfEvolvingKnowledgeBase
>
> # 方式二：已经克隆过了，补初始化
> git submodule update --init
>
> # 不确定状态？一条命令看清（异常时给可复制的修复命令）
> bash scripts/check_kernel.sh
> ```
>
> 该脚本报告：子模块是否初始化 / commit 是否与 SEKB 钉住的版本一致 / 工作区是否干净 /
> 内置提示词是否完整。部署前检查与 CI 用的是**同一个脚本**，标准一致。
> 线上运行时也能查：`GET /api/v1/health/` 的 `kernel` 字段（含 commit 与提示词生效来源）。

```bash
# 1. 克隆项目
git clone --recurse-submodules <repo-url> SelfEvolvingKnowledgeBase
cd SelfEvolvingKnowledgeBase

# 2. 创建虚拟环境
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY 和 BOCHA_API_KEY

# 5. 检查 config.yaml 配置（可选，默认即可运行）
cat config.yaml
```

### CLI 示例

```bash
# 启动交互式聊天
sekb chat

# 指定已有会话 ID 继续对话
sekb chat --conv-id conv_xxx

# 运行评估测试（批量跑黄金数据集）
sekb eval --dataset app/eval/datasets/golden_qa.json --output tests/reports/eval_report.md

# 健康检查
sekb health
```

### API 示例

```bash
# 1. 启动后端服务
cd backend
uvicorn app.api.main:app --host 0.0.0.0 --port 8000 --reload

# 2. 发送聊天请求（curl）
curl -X POST http://localhost:8000/api/v1/chat/ \
  -H "Content-Type: application/json" \
  -d '{
    "message": "你好，介绍一下自己",
    "conversation_id": null
  }'

# 3. SSE 流式聊天
curl -N -X POST http://localhost:8000/api/v1/chat/stream \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Python 装饰器怎么用？",
    "conversation_id": null
  }'

# 4. 列出会话
curl http://localhost:8000/api/v1/conversations/

# 5. 上传文档到知识库
curl -X POST http://localhost:8000/api/v1/upload/ \
  -F "file=@/path/to/document.pdf"

# 6. 查看知识库状态
curl http://localhost:8000/api/v1/upload/status

# 7. 健康检查
curl http://localhost:8000/api/v1/health/
```

### Python SDK 示例

```python
import requests

BASE_URL = "http://localhost:8000/api/v1"

# 发送消息
response = requests.post(f"{BASE_URL}/chat/", json={
    "message": "2026 年诺贝尔物理学奖得主是谁？",
})
data = response.json()
print(f"助手回复: {data['message']}")
print(f"识别意图: {data['intent']}")
print(f"评估指标: {data['metrics']}")

# 流式聊天
with requests.post(f"{BASE_URL}/chat/stream", json={
    "message": "介绍一下 LangGraph",
}, stream=True) as resp:
    for line in resp.iter_lines(decode_unicode=True):
        if line:
            print(line)
```

---

## API 端点列表

### 认证（Auth）— Phase 3

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/v1/auth/register` | 用户注册 |
| `POST` | `/api/v1/auth/login` | 用户登录，返回 JWT |
| `POST` | `/api/v1/auth/logout` | 用户登出 |
| `GET` | `/api/v1/auth/me` | 获取当前用户信息 |
| `PATCH` | `/api/v1/auth/me` | 更新当前用户信息 |

### 聊天（Chat）

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/v1/chat/` | 发送消息（非流式），返回完整回复与评估指标 |
| `POST` | `/api/v1/chat/stream` | 发送消息（SSE 流式响应），逐 token 返回 |

### 会话管理（Conversations）

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/v1/conversations/` | 列出当前用户的会话（按更新时间倒序） |
| `GET` | `/api/v1/conversations/{conv_id}` | 获取会话详情（含消息历史） |
| `PATCH` | `/api/v1/conversations/{conv_id}` | 更新会话（重命名） |
| `DELETE` | `/api/v1/conversations/{conv_id}` | 删除会话 |
| `POST` | `/api/v1/conversations/{conv_id}/rate` | 消息反馈（thumbs up/down） |

### 文件上传（Upload）

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/v1/upload/` | 上传文件并入库到知识库（支持同步/异步处理） |
| `GET` | `/api/v1/upload/status` | 查询知识库状态（条目总数 + L3 启用状态） |
| `DELETE` | `/api/v1/upload/entries/{entry_id}` | 删除指定知识条目 |

### 知识库管理（Knowledge）— Phase 3

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/v1/knowledge/` | 知识条目列表（分页） |
| `GET` | `/api/v1/knowledge/search` | 知识库搜索（`?q=关键词`） |
| `DELETE` | `/api/v1/knowledge/{entry_id}` | 删除指定知识条目 |

### 监控指标（Metrics）— Phase 4

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/metrics` | Prometheus 指标端点 |

### 健康检查（Health）

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/v1/health/` | 综合健康检查（LLM / 工具 / 存储 / Graph） |
| `GET` | `/api/v1/health/live` | 存活探针（liveness） |
| `GET` | `/api/v1/health/ready` | 就绪探针（readiness，核心依赖检查） |

### 统一响应格式

**非流式聊天响应**：
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

---

## 项目结构

```
SelfEvolvingKnowledgeBase/
├── README.md                           # 项目根说明（本文件）
├── docs/                               # 设计文档与操作手册
│   ├── README.md                       # 文档工程导航
│   ├── 01-architecture.md              # 总体架构、技术栈、ADR
│   ├── 02-phase1-design.md             # Phase 1 详细设计
│   ├── 03-evaluation-and-testing.md    # 评估体系与测试
│   ├── 04-config-reference.md          # 配置参考
│   ├── 05-phase2-design.md             # Phase 2 详细设计
│   ├── 06-phase3-design.md             # Phase 3 详细设计
│   ├── 07-phase4-design.md             # Phase 4 详细设计
│   ├── DEPLOYMENT.md                   # 项目操作手册（从零到上线）
│   ├── ALERTING-TROUBLESHOOTING.md     # 告警模块故障排查
│   └── testCase/                       # 测试用例
│       └── TEST-CASES.md               # 测试用例汇总与自动化指南
│
├── backend/                            # Python 后端
│   ├── app/
│   │   ├── agents/                     # 五个 Agent 节点
│   │   │   ├── supervisor.py           #   监督者：意图识别 + 预检索
│   │   │   ├── planner.py              #   规划者：任务拆解
│   │   │   ├── executor.py             #   执行器：工具调用 + 草稿
│   │   │   ├── critic.py               #   审查者：反思评估
│   │   │   ├── scribe.py               #   记录员：摘要 + 重要性评分
│   │   │   ├── knowledge_ingestor.py   #   知识自迭代引擎
│   │   │   ├── prompts/                #   各 Agent Prompt 模板
│   │   │   └── strategies/             #   反思策略实现
│   │   │
│   │   ├── graph/                      # LangGraph 工作流
│   │   │   ├── state.py                #   State TypedDict 定义
│   │   │   └── builder.py              #   图构建（节点+边+条件路由）
│   │   │
│   │   ├── core/                       # 核心引擎
│   │   │   ├── config.py               #   配置加载
│   │   │   ├── llm_factory.py          #   LLM 工厂
│   │   │   ├── tracing.py              #   Tracing 初始化
│   │   │   ├── logging.py              #   结构化日志
│   │   │   ├── embedding.py            #   Embedding 函数
│   │   │   └── exceptions.py           #   自定义异常层级
│   │   │
│   │   ├── memory/                     # 记忆系统
│   │   │   ├── base.py                 #   MemoryBackend 抽象
│   │   │   ├── short_term.py           #   L1 短期记忆
│   │   │   ├── knowledge_base.py       #   L3 ChromaDB 知识库
│   │   │   └── knowledge_entry.py      #   知识条目数据模型
│   │   │
│   │   ├── tools/                      # 工具层
│   │   │   ├── mcp/                    #   MCP 标准工具
│   │   │   │   ├── client.py           #     MCP Client 封装
│   │   │   │   └── bocha_server.py     #     博查搜索 MCP Server
│   │   │   ├── direct/                 #   本地直连工具
│   │   │   │   └── vector_store.py     #     向量检索直连
│   │   │   ├── rag/                    #   RAG 检索
│   │   │   │   └── retriever.py        #     RAG 检索器
│   │   │   ├── file_processor.py       #   文件解析处理器
│   │   │   └── registry.py             #   工具注册表
│   │   │
│   │   ├── storage/                    # 存储后端
│   │   │   ├── base.py                 #   StorageBackend 抽象
│   │   │   └── json_storage.py         #   本地 JSON 实现
│   │   │
│   │   ├── eval/                       # 评估体系
│   │   │   ├── metrics.py              #   9 项指标计算
│   │   │   ├── assertion.py            #   断言引擎
│   │   │   ├── runner.py               #   Eval Mode 运行器
│   │   │   ├── reporter.py             #   回归报告生成
│   │   │   └── datasets/               #   黄金数据集
│   │   │
│   │   ├── api/                        # FastAPI 路由
│   │   │   ├── main.py                 #   ASGI 入口
│   │   │   ├── server.py               #   FastAPI app 工厂
│   │   │   └── routes/                 #   路由模块
│   │   │       ├── chat.py             #     聊天端点
│   │   │       ├── conversations.py    #     会话管理
│   │   │       ├── upload.py           #     文件上传
│   │   │       └── health.py           #     健康检查
│   │   │
│   │   └── cli/                        # CLI 入口
│   │       ├── main.py                 #   主命令（sekb）
│   │       ├── chat.py                 #   交互聊天会话
│   │       └── eval.py                 #   Eval Mode
│   │
│   ├── tests/                          # 测试
│   │   ├── unit/                       #   单元测试
│   │   ├── integration/                 #   集成测试
│   │   ├── fixtures/                   #   测试夹具
│   │   └── conftest.py                 #   pytest 配置
│   │
│   ├── config.yaml                     # 配置文件
│   ├── .env.example                    # 环境变量示例
│   ├── requirements.txt                # 依赖清单
│   └── pyproject.toml                  # 项目元数据
│
├── frontend/                           # 前端（Phase 3，React + Vite）
├── deploy/                             # 部署配置（Phase 4）
│   ├── nginx.conf                      #   Nginx 反向代理配置
│   ├── nginx-ssl.conf                  #   HTTPS SSL 配置
│   ├── nginx-canary.conf               #   灰度发布 Nginx 配置
│   ├── prometheus.yml                  #   Prometheus 采集配置
│   ├── alerts.yml                      #   告警规则
│   ├── alertmanager.yml                #   Alertmanager 路由配置
│   ├── loki-config.yml                 #   Loki 日志聚合配置
│   ├── promtail-config.yml             #   Promtail 日志采集配置
│   ├── backup.sh                       #   数据备份脚本
│   ├── restore.sh                      #   数据恢复脚本
│   ├── canary_monitor.sh               #   灰度监控与自动回滚
│   ├── pre-deploy-check.sh             #   部署前一键检查脚本
│   ├── deploy.sh                       #   一键部署脚本
│   ├── .env.prod.example               #   生产环境变量模板
│   ├── grafana/                        #   Grafana 看板 provisioning
│   └── feishu-webhook/                 #   飞书告警通知中转服务
│
├── .github/workflows/ci.yml            # CI/CD Pipeline（GitHub Actions）
├── docker-compose.prod.yml             # 生产部署 Compose 文件
├── docker-compose.monitoring.yml       # 监控栈 Compose 文件
├── start_backend.sh                    # 后端启动脚本
├── start_frontend.sh                   # 前端启动脚本
├── .gitignore
└── .env.project
```

---

## 测试说明

### 测试金字塔

```
                    ┌─────────────┐
                    │   E2E 测试   │  少量，接真实 API，跑 eval 集
                    └─────────────┘
                  ┌─────────────────┐
                  │  集成测试         │  图流转、存储读写、MCP 通信
                  └─────────────────┘
              ┌─────────────────────────┐
              │     单元测试              │  各 Agent 节点纯逻辑、记忆、工具
              └─────────────────────────┘
```

### 运行测试

```bash
cd backend

# 安装开发依赖
pip install -e ".[dev]"

# 运行单元测试 + 集成测试（不依赖外部 API）
pytest tests/unit tests/integration -v

# 运行指定测试文件
pytest tests/unit/test_state.py -v
pytest tests/unit/test_config.py -v

# 带覆盖率报告
pytest tests/unit tests/integration --cov=app --cov-report=term-missing

# 运行 Eval Mode（批量测试，需要真实 API）
sekb eval --dataset app/eval/datasets/golden_qa.json
```

### 测试用例集

| 类型 | 编号 | 说明 |
|------|------|------|
| 功能鲁棒性 | TC-01 ~ TC-06 | 意图识别、强制联网、多步规划、反思重试、长上下文压缩 |
| 量化指标 | TC-M01 ~ TC-M05 | 意图置信度、锚定度、逻辑连贯性、端到端延迟、工具成功率 |
| 边界异常 | TC-E01 ~ TC-E04 | LLM 超时、工具失败、预算超限、JSON 解析失败 |

### 评估指标（9 项）

| 指标 | 含义 | 来源节点 |
|------|------|---------|
| `intent_confidence` | 意图识别置信度 | Supervisor |
| `plan_step_count` | 规划步数 | Planner |
| `replan_count` | 重规划次数 | Critic |
| `tool_success_rate` | 工具调用成功率 | Executor |
| `answer_groundedness` | 答案锚定度（防幻觉核心） | Critic |
| `critic_coherence_score` | 逻辑链自洽性 | Critic |
| `answer_relevance` | 回答相关性 | Critic |
| `e2e_latency_ms` | 端到端延迟 | 全链路 |
| `cost_usd` | 单次对话成本 | 全链路 |

---

## 技术栈

### 后端

| 层 | 技术 | 版本 | 说明 |
|----|------|------|------|
| 语言 | Python | 3.11+ | LangGraph 原生生态 |
| Web 框架 | FastAPI | 0.115+ | 异步 + 自动 OpenAPI |
| Agent 框架 | LangGraph | 1.0+ | 多 Agent 工作流 |
| LLM 集成 | langchain-deepseek | 1.0+ | DeepSeek 官方集成 |
| MCP 协议 | mcp SDK | 1.1+ | 标准工具协议 |
| LLM | DeepSeek API | - | chat + reasoner 双模型分级 |
| 向量库 | ChromaDB | 0.5+ | L3 知识库存储 |
| Embedding | sentence-transformers | 3.0+ | 本地向量嵌入 |
| 配置 | Pydantic Settings + YAML | - | 强类型配置校验 |
| CLI | typer + rich | 0.12+ | 美观的命令行体验 |
| 日志 | structlog | 24.1+ | 结构化 JSON 日志 |
| HTTP | httpx | 0.27+ | 异步 HTTP 客户端 |
| Token 计数 | tiktoken | 0.8+ | 精确 token 计算 |
| 重试 | tenacity | 8.2+ | 指数退避重试 |

### 开发与测试

| 工具 | 版本 | 说明 |
|------|------|------|
| pytest | 8.0+ | 测试框架 |
| pytest-asyncio | 0.23+ | 异步测试支持 |
| pytest-cov | 4.1+ | 覆盖率报告 |
| respx | 0.21+ | httpx Mock |
| ruff | 0.5+ | 代码检查 |
| mypy | 1.10+ | 类型检查 |

### 外部服务

| 服务 | 用途 | 必需性 |
|------|------|--------|
| DeepSeek API | LLM 调用（chat + reasoner） | 必需 |
| 博查搜索 API | 联网搜索（国内直连） | 必需 |
| LangSmith | Tracing 可视化 | 可选（不可用自动降级本地） |
| Redis | L2 中期记忆 | Phase 2 可选 |
| PostgreSQL | 会话存储迁移 | Phase 2 可选 |

---

## 文档导航

| 文档 | 内容 |
|------|------|
| [docs/README.md](docs/README.md) | 文档工程导航（技术文档 / 运维手册 / 变更日志 / 备份） |
| [docs/tech/00-README.md](docs/tech/00-README.md) | **技术文档总览**：12 篇编号文档的文档地图与阅读路线 |
| [docs/tech/01-ARCHITECTURE.md](docs/tech/01-ARCHITECTURE.md) | 架构总览（C4 图 + ADR + 技术选型） |
| [docs/tech/02-RUNTIME-FLOWS.md](docs/tech/02-RUNTIME-FLOWS.md) | 运行时流程（主链路/SSE/状态机/TTFT） |
| [docs/tech/03-MODULES.md](docs/tech/03-MODULES.md) | 模块详解 |
| [docs/tech/04-DATA-MODEL.md](docs/tech/04-DATA-MODEL.md) | 数据模型（存储/隔离/生命周期） |
| [docs/tech/05-API-REFERENCE.md](docs/tech/05-API-REFERENCE.md) | 接口参考（65 端点 + SSE + CLI） |
| [docs/tech/06-CONFIG-REFERENCE.md](docs/tech/06-CONFIG-REFERENCE.md) | 配置参考（config.yaml + 死配置） |
| [docs/tech/07-DESIGN-PATTERNS.md](docs/tech/07-DESIGN-PATTERNS.md) | 设计模式与原则评价 |
| [docs/tech/08-GLOSSARY.md](docs/tech/08-GLOSSARY.md) | 术语表 |
| [docs/tech/09-OBSERVABILITY.md](docs/tech/09-OBSERVABILITY.md) | 可观测性（指标/日志/告警/排障） |
| [docs/tech/10-TESTING.md](docs/tech/10-TESTING.md) | 测试与质量 |
| [docs/tech/11-EVOLUTION.md](docs/tech/11-EVOLUTION.md) | 演进与技术债 |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | 变更日志（活文档） |
| [docs/BACKLOG.md](docs/BACKLOG.md) | 需求跟踪 / 审计（活文档） |
| [docs/ops/](docs/ops/) | 部署运维手册（生产部署 / 排障 / 采集渠道） |

## License

MIT