---
title: Agent 可观测性与评测体系（B1 设计 RFC）
layer: 宪法层
owner: SEKB Team
status: draft
version: v1.0.0
last-updated: 2026-09-24
based-on-commit: 6c69adb
related: [docs/tech/09-OBSERVABILITY, docs/tech/01-ARCHITECTURE, docs/tech/10-TESTING, docs/tech/.facts/T8-observability]
---

# RFC：Agent 可观测性与评测体系（B1）

> **本文档是交给实现者的方案设计，不含实现。**
> 交接方式：按 §7 的分期计划逐阶段实施，每阶段有独立验收标准（§8）。
> **动手前必读 §2（现状盘点）**——大部分基础设施已经存在，最容易犯的错是重复造轮子。

---

## 0. TL;DR

**这份 RFC 的核心结论与直觉相反：B1 的主要工作不是"接入 LangSmith/LangFuse"，而是"把已有的东西接上、修对、并让它成为门禁"。**

四个决定性事实（均已用代码核实，证据见 §2）：

| # | 事实 | 含义 |
|---|---|---|
| 1 | **LangSmith 代码已接好，但生产是"未启用"**（`{"event": "追踪未启用"}`） | 链路追踪不是"要不要做"，而是"**为什么没开**"——缺 API Key，且见 §3 的出境/合规权衡 |
| 2 | **Prometheus 已有 23 个自定义指标、Grafana 已有 3 个 LLM 面板** | 指标层不需要新建，只需要**补两个标签 + 修一个重复计数 bug** |
| 3 | **评测框架完整存在**（`app/eval/` 7 个模块 + 2 个 golden 数据集 + CLI + CI job） | 评测不需要开发，需要一个 `--fail-under` 参数把 CI 里的 `\|\| echo "eval non-blocking"` 换成真门禁 |
| 4 | **发现两个既有缺陷**：Token/成本**被重复计数约 2×**；`LLMStats.records` **无界增长** | 这两个必须在本 RFC 内修掉，否则新增的归因面板建在错误数据上 |

**推荐路线**：P0（零新增基础设施，约 5 人日）先做指标归因 + 修 bug + 评测门禁 + 看板补全；
P1（约 3 人日）再补跨容器链路；**P2 才决策**是否引入外部追踪后端（受 §3.1 的两个硬约束限制）。

---

## 1. 背景与目标

### 1.1 为什么做

FDE（Forward Deployed Engineer）岗位 JD 高频要求「**构建高性能的评估管道和可观测性框架**」
「**对交付效果负责**，构建评测方案并持续迭代直至业务指标达标」。
对照 SEKB 现状，"能跑"已经做到，"**能证明效果、能定位退化**"还差一段——这段就是 B1。

同时它也是自身工程需要：SEKB 有 5 个 Agent 协作 + RAG + MCP 工具调用，
**出问题时现在只能靠翻 structlog 日志**，缺"一个请求到底慢在哪一环、贵在哪个 Agent"的能力。

### 1.2 目标（可验收）

| 编号 | 目标 | 验收方式 |
|---|---|---|
| G1 | **按 Agent 归因**的 token / 成本 / 延迟 | Grafana 面板可按 `role`（supervisor/planner/executor/critic/scribe）下钻 |
| G2 | 单次调用粒度的 LLM 延迟分布 | `sekb_llm_call_latency_seconds` 有数据（现已接入，见 §2.1） |
| G3 | 一次请求的**完整链路**可追溯（含跨容器 MCP） | 给定 `trace_id`，能在 Grafana 看到各阶段 span 与耗时 |
| G4 | **评测成为 CI 门禁** | 通过率低于阈值时 CI 红，阻止合入 |
| G5 | 评测结果与**变更来源**可关联 | 报告里带被测 commit + 画像/提示词指纹 |
| G6 | 修掉 §2.3 的两个缺陷 | 重复计数消失；`records` 不再无界增长 |

### 1.3 非目标（Out of Scope，明确不做）

- ❌ **不做模型训练/微调相关的评测**（FDE 应用侧不需要）
- ❌ **不做在线 A/B 实验平台**（超出"对交付效果负责"的最小范围）
- ❌ **不替换现有 Prometheus/Grafana/Loki 栈**（它们是资产，不是负债）
- ❌ **不做用户级行为分析**（隐私红线，见 §5-D10）
- ❌ **不改 jobcopilot 内核的业务逻辑**（只在其边界注入追踪上下文）

---

## 2. 现状盘点（动手前必读）

### 2.1 已经有的（**不要重建**）

| 能力 | 位置 | 状态 |
|---|---|---|
| 23 个 Prometheus 自定义指标（含 LLM 延迟/调用/Token/成本/重试/降级） | `backend/app/core/metrics.py`（`llm_calls_total` :68、`llm_tokens_total` :75、`llm_cost_usd_total` :82、`llm_call_latency_seconds` :56） | ✅ 定义完整 |
| 单次调用粒度埋点函数 `record_llm_call()` | `backend/app/core/metrics.py:328` | ✅ **已接入** `backend/app/core/llm_factory.py:944`（`_record_call` 内） |
| 每次 LLM 调用的结构化日志（role/model/tokens/latency/cost/retry/degraded） | `backend/app/core/llm_factory.py:914` 起 | ✅ 现役，Loki 可查 |
| LangSmith 接入代码（设 env + LangChain 自动埋点） | `backend/app/core/tracing.py:21` `setup_tracing()` | ⚠️ 代码就绪，**生产未启用**（§2.2） |
| 业务 trace_id 注入 RunnableConfig | `backend/app/core/tracing.py:57` `get_trace_config()`；调用点 `llm_factory.py:397,567,609` | ✅ 现役 |
| 启动时初始化追踪 | `backend/app/core/bootstrap.py:181` | ✅ 现役 |
| 评测框架（数据集/指标/断言/报告/运行器） | `backend/app/eval/`：`runner.py`、`assertion.py`、`metrics.py`、`reporter.py`、`ragas_metrics.py`、`rag_runner.py` | ✅ 完整 |
| 两个 golden 数据集 | `backend/app/eval/datasets/golden_qa.json`、`rag_golden.json` | ✅ 存在 |
| 评测 CLI | `backend/app/cli/main.py:93`（`eval`）、`:138`（`rag-eval`）；实现 `backend/app/cli/eval.py:36` | ✅ 可用 |
| Grafana 看板（含 3 个 LLM 面板） | `deploy/grafana/provisioning/dashboards/sekb-overview.json`（「LLM 调用次数」「LLM Token 用量」「LLM 成本累计」） | ✅ 已 provision |
| 监控栈 | `docker-compose.monitoring.yml`（Prometheus / Grafana / Loki / Promtail / Alertmanager） | ✅ 已部署运行 |
| MCP 客户端（stdio + SSE 双传输） | `backend/app/tools/mcp/client.py:43` | ✅ 现役 |
| 内核独立容器 | `docker-compose.prod.yml:268`（`jobcopilot-mcp`） | ✅ 现役（**跨容器**，见 §5-D6） |

### 2.2 缺口清单（逐条带证据 —— 这就是要做的事）

| # | 缺口 | 证据 | 影响 |
|---|---|---|---|
| **K1** | **Token / 成本无法按 Agent 归因** | `llm_tokens_total` 标签为 `[model, direction]`（`metrics.py:75`）、`llm_cost_usd_total` 标签为 `[model]`（`metrics.py:82`）——**都没有 `role`**；而 `record_llm_call` 拿到的是带 `role` 的 `LLMCallRecord`（`llm_factory.py:68`） | G1 无法达成；"哪个 Agent 最烧钱"答不出来 |
| **K2** | **生产未启用任何链路追踪** | 容器内 `{"event": "追踪未启用"}`、`{"enabled": false, "provider": "langsmith"}`；`config.yaml:214` 的 `api_key: ${LANGSMITH_API_KEY}` 未注入 | G3 完全无数据；线上排障只能靠日志拼 |
| **K3** | **跨容器 MCP 调用无追踪** | 内核跑在独立容器 `jobcopilot-mcp`（`docker-compose.prod.yml:268`），经 `backend/app/tools/mcp/client.py` 的 stdio/SSE 通信；LangChain 自动埋点**不跨进程** | 内核里发生的 LLM 调用/工具调用在链路里是黑盒 |
| **K4** | **CI 评测是"假门禁"** | `.github/workflows/ci.yml:239`：`python -m app.cli.main eval --dataset ... \|\| echo "eval non-blocking"`——**无论通过率多少都 exit 0**；`app/cli/eval.py` 全文件 **0 处 `sys.exit`** | G4 无法达成；评测退化了也没人知道 |
| **K5** | **Grafana 缺延迟面板与按 Agent 分解** | 看板仅 3 个 LLM 面板，无 `sekb_llm_call_latency_seconds` 面板、无 `role` 维度 | 看板看不到"慢在哪" |
| **K6** | **三个指标定义后从未埋点** | `conversations_total`（`metrics.py:31`）、`reflection_pass_rate`（`metrics.py:113`）、`tool_call_latency_seconds`（`metrics.py:144`）——在 `metrics.py` 之外**零写入点** | 反思闭环与工具耗时不可观测；看板上若引用则恒为 0（**误导**） |
| **K7** | **事实表已过期** | `docs/tech/.facts/T8-observability.md` 记 `record_llm_call`「全仓库无调用方」——实际已在 `llm_factory.py:944` 接入 | 文档误导后来者；需同步（§2.4） |

### 2.3 ⚠️ 顺带发现的两个既有缺陷（**必须先修，否则新面板建在错数据上**）

#### 缺陷 A：Token 与成本被**重复计数约 2×**

`record_llm_call` 接入后，`record_chat_metrics` 里的**聚合写入没有删掉**，两者写**同一个 Prometheus 序列**：

| 路径 | 写入 | 位置 |
|---|---|---|
| **每次调用**（新） | `llm_tokens_total{model,direction}.inc(本次 tokens)` | `metrics.py:361-364` |
| **每次聊天请求**（旧） | `llm_tokens_total{model,direction}.inc(该请求聚合 tokens)` | `metrics.py:267-268` |
| **每次调用**（新） | `llm_cost_usd_total{model}.inc(本次成本)` | `metrics.py:365` |
| **每次聊天请求**（旧） | `llm_cost_usd_total{model}.inc(该请求聚合成本)` | `metrics.py:270` |

聊天路径下两者都会被触发（`record_llm_call` 来自 `LLMFactory._record_call`；`record_chat_metrics` 来自 `chat.py:674/802`），
**同一笔 token/成本被记两次**。因此现有 Grafana「LLM Token 用量」「LLM 成本累计」两个面板的数字**偏高约一倍**。

> 另一处口径不一致（非重复计数，但同样要处理）：`llm_calls_total` 被写了两种标签集合——
> `role="aggregate"`（`metrics.py:271`，且是 `.inc()` 即每模型 +1，**不是按调用次数**）与真实 `role`（`metrics.py:359`）。
> 混在同一指标里会让「按 role 求和」得到无意义的数。

**修法（P0 必做）**：删除 `record_chat_metrics` 中的 LLM 聚合写入口径（`metrics.py:266-271`），
保留 `record_llm_call` 的**单次粒度**作为唯一事实源；`role="aggregate"` 若要保留，
**新建独立指标**（如 `sekb_llm_calls_per_request`）而不是并入 `llm_calls_total`。

#### 缺陷 B：`LLMStats.records` **无界增长**

`backend/app/core/llm_factory.py:96` 定义 `records: list[LLMCallRecord] = field(default_factory=list)`，
`:893` 每次调用 `append`——**没有任何容量上限或清理**。
它被用作**差值快照**（`llm_factory.py:729` 记 `records_len`，`:744` 取 `records[snapshot.records_len:]` 算 delta）。

因此该列表在长期运行的服务里**只增不减**。后端已连续运行数天，属**内存泄漏**。
按一次调用一条记录估算，10 万次调用量级下会占用可观常驻内存，且 `_record_call` 每次持锁 append 会让列表越来越慢。

**修法（P0 必做）**：改为**环形容器 / 定长 deque**（如 `deque(maxlen=5000)`）
或改用"计数器 + 滚动窗口聚合"，并在 `snapshot` 语义上改为按**单调递增序号**取差值，
而不是按下标切片（下标切片本身就依赖"列表只增不减"，是设计债）。

> ⚠️ 这两处改动位于**共享热路径**（`metrics.py` / `llm_factory.py`），
> 动手前请按 `AGENTS.md` 在 `docs/COORDINATION.md` 认领。

### 2.4 T8 事实表已过期，需同步

`docs/tech/.facts/T8-observability.md` 是该主题的**事实表（SSOT 工作产物）**，
但它采集于 2026-09-09，且自述"可能已落后"。本 RFC 核实时发现：

- 它记 `record_llm_call`「**全仓库无调用方**」→ **已过期**（现有 `llm_factory.py:944` 调用）
- 它记 `sekb_llm_call_latency_seconds`「**实际未写入**」→ **已过期**（同上）

**要求**：本 RFC 实施完成后，**同步更新 T8 与 `docs/tech/09-OBSERVABILITY.md`**，
并把"每次改动都容易让行号漂移"这一点交给 `docs/tech/漂移清单.md` 已有的机制处理。

---

## 3. 方案选型：追踪后端

### 3.1 约束（这些约束决定选型，请勿忽略）

| 约束 | 实测值 | 对选型的影响 |
|---|---|---|
| **C1 磁盘紧张** | 服务器 `/` 共 59G，**已用 33G，仅剩 25G** | LangFuse **v3 需要 ClickHouse**（另加 Redis + S3/MinIO）→ 磁盘风险高，**不建议**在未扩容前自托管 v3 |
| **C2 境外 SaaS 出网不可靠** | 已实测：容器内 `openaipublic.blob.core.windows.net` **DNS 解析失败**（同容器 `pypi.org`、`mirrors.cloud.tencent.com` 正常） | LangSmith 端点是 `api.smith.langchain.com`（境外）。**出网可用性无法保证**，不能作为唯一追踪后端 |
| **C3 数据敏感性** | 追踪内容含用户对话、知识库检索片段、JobCopilot 客户 JD | 送第三方 SaaS 属**数据出境**，需明确脱敏与合规判断（见 §5-D10） |
| **C4 已有监控栈** | Prometheus + Grafana + **Loki + Promtail** 已运行数周 | 应优先复用，而非引入新组件 |

### 3.2 候选对比

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| **A. LangSmith（SaaS）** | 代码已就绪（`tracing.py:21`），LangChain/LangGraph 零改造自动埋点，UI 成熟 | 境外出网不稳（C2）；数据出境（C3）；需 API Key 与新账号 | **保留为可选 provider**，不设为默认 |
| **B. LangFuse 自托管 v3** | 数据不出服务器，UI 完整，OTel 原生 | **需 ClickHouse + Redis + S3**（C1 磁盘告急）；运维面变大 | **P2 再评估**；先扩容磁盘并确认运维承受力 |
| **C. 复用现有栈：span 落 Loki + 指标进 Prometheus + 看板在 Grafana** | **零新增容器**（C4）；数据不出境（C3）；出网无关（C2）；已有 `trace_id` 贯通（`tracing.py:57`） | 无现成 trace UI，需自己设计日志结构与 Grafana 面板；查询靠 LogQL 不如专业 trace UI 直观 | ✅ **P0/P1 的推荐路线** |

### 3.3 推荐：分层引入

```
P0（立即，零新增基础设施）
   指标归因补全 + 修两个缺陷 + 评测门禁化 + Grafana 面板补全
   → 解决 G1/G2/G4/G6：能按 Agent 看成本、能拦住退化

P1（随后）
   span 级链路：结构化 span 日志 → Loki → Grafana（trace_id 贯通，含跨容器 MCP）
   → 解决 G3/K3：能回答"一个请求慢在哪一环"

P2（按需，需先满足前置条件）
   评估外部追踪后端：若磁盘扩容 → LangFuse 自托管 v3；若出网稳定且有合规结论 → LangSmith
   保留 tracing.py 的 provider 抽象，切换只改配置
```

> **为什么这个顺序是对的**：P0 就能覆盖 FDE JD 最看重的"对交付效果负责 ／ 构建评估管道"；
> P2 的 SaaS 追踪是**锦上添花**，不该被它的前置条件（磁盘/出网/合规）卡住 P0 的交付。

---

## 4. 总体架构

### 4.1 三层观测模型

| 层 | 回答的问题 | 载体 | 现状 |
|---|---|---|---|
| **Metrics**（聚合） | 有没有变差？成本多少？ | Prometheus + Grafana | 基本就绪，缺 `role` 归因（K1） |
| **Traces**（单请求） | 这一次慢在哪、错在哪？ | 结构化 span 日志 → Loki → Grafana（P1） | 缺（K2/K3） |
| **Evals**（质量） | 效果好没好？这次改动有没有退化？ | `app/eval/` + CI 门禁 + 报告 | 框架就绪，门禁是假的（K4） |

### 4.2 数据流

```
                        ┌──────────────── 业务入口 ────────────────┐
                        │ POST /api/v1/chat[/stream]              │
                        │  bind_context(trace_id=…)  ← structlog  │
                        └───────────────┬─────────────────────────┘
                                        │
                        ┌───────────────▼─────────────────────────┐
                        │ LangGraph 五 Agent（builder.py）        │
                        │  Supervisor→Planner→Executor→Critic→Scribe│
                        └───────┬──────────────────────┬──────────┘
                                │                      │
                ┌───────────────▼────────┐   ┌─────────▼──────────────┐
                │ LLMFactory             │   │ MCP Client             │
                │  _record_call          │   │  stdio / SSE           │
                │   ├─→ record_llm_call  │   └─────────┬──────────────┘
                │   │    (Prometheus)    │             │ traceparent 注入（P1/D6）
                │   └─→ structlog 记录   │   ┌─────────▼──────────────┐
                └───────────┬────────────┘   │ jobcopilot-mcp 容器    │
                            │                │（独立进程，默认黑盒）  │
                            │                └─────────┬──────────────┘
                            │                          │ span 回传（P1）
        ┌───────────────────▼──────────────────────────▼───────────────────┐
        │ structlog → Promtail → Loki  ─┐                                  │
        │ prometheus_client /metrics  ──┼─→ Grafana（指标面板 + trace 面板）│
        │                               │                                  │
        │                     Prometheus┘                                  │
        └──────────────────────────────────────────────────────────────────┘
                            ▲
                            │ 评测结果（通过率/指标/耗时）
        ┌───────────────────┴──────────────────────────────────────────────┐
        │ app/eval（runner/reporter）+ golden 数据集 → CI 门禁（--fail-under）│
        └──────────────────────────────────────────────────────────────────┘
```

### 4.3 trace_id 贯通（复用既有机制，不新增概念）

- 现有：`bind_context(trace_id=...)` 写 structlog 上下文；`get_trace_config()`（`tracing.py:57`）把它注入 LangChain `RunnableConfig.metadata`
- **P0/P1 一律沿用这个 `trace_id` 作为唯一关联键**，不引入第二套 ID
- span 日志统一字段：`trace_id` / `span` / `parent_span` / `role` / `phase` / `duration_ms` / `status`

---

## 5. 详细设计

### D1 — 指标归因补全（解决 K1，P0）

**改动**：给两个指标加 `role` 标签，并在写入处传入。

```python
# backend/app/core/metrics.py
llm_tokens_total = Counter(
    "sekb_llm_tokens_total", "Total LLM tokens used",
    ["model", "direction", "role"],          # ← 新增 role
)
llm_cost_usd_total = Counter(
    "sekb_llm_cost_usd_total", "Total LLM cost in USD",
    ["model", "role"],                       # ← 新增 role
)
```

```python
# record_llm_call 内（metrics.py:361-365 一带）
llm_tokens_total.labels(model=model, direction="input",  role=role).inc(input_tokens)
llm_tokens_total.labels(model=model, direction="output", role=role).inc(output_tokens)
llm_cost_usd_total.labels(model=model, role=role).inc(cost_usd)
```

- **基数评估**：`role` 取值是**封闭集合共 11 个**（配置见 `backend/config.yaml` 的 `llm.roles`）——
  Agent 侧 6 个（supervisor / planner / executor / critic / critic_complex / scribe），
  非 Agent 侧 5 个（chat_simple / news_report / job_analysis / rerank / ragas）；
  `model` 数量个位数 → 序列增长可控（11 × 模型数，量级 ~10¹），**不构成高基数**，
  符合 `metrics.py:13` 的既有纪律。**若将来新增自由文本型 role 标签，则必须先收敛枚举。**
- **注意**：加标签是**破坏性变更**（Prometheus 会视为新序列，旧序列成为孤儿）。
  需在发布说明中提示：Grafana 面板同步改查询；旧序列可在 Prometheus 侧按保留策略自然淘汰
- **验收**：Grafana 能按 `role` 下钻 token/成本；数值与 `llm_factory.py:914` 的日志逐条核对**对得上**

### D2 — 修复 Token/成本重复计数（解决 §2.3-A，P0）

删除 `record_chat_metrics` 中 LLM 的聚合写入（`metrics.py:266-271`），
让 `record_llm_call` 成为 token/成本/调用数的**唯一写入方**。

- `record_chat_metrics` 仍保留：`e2e_latency_seconds`、`messages_total`、`answer_groundedness`、`replan_count_total`、`knowledge_ingest_total`、`daily_cost_usd`
- **`daily_cost_usd` 的语义需明确**：现为"只有 inc、无 reset"（`metrics.py:307-308`），
  且注释称"由外部任务定期重置"但无实现。**若名称是"当日"就必须有 reset**——
  要么补一个按天重置的定时任务，要么改名为 `cumulative_cost_usd`。**不要留一个名不副实的指标。**
- **验收**：同一请求前后对比，token/成本增量与该请求日志中的合计**相等**（不再 2×）

### D3 — `LLMStats.records` 无界增长（解决 §2.3-B，P0）

- 改为定长：`records: Deque[LLMCallRecord] = field(default_factory=lambda: deque(maxlen=N))`（N 建议 2000~5000）
- **同时改快照语义**：`snapshot.records_len`（`llm_factory.py:140`）改为**单调递增序号**，
  `delta_records` 的取法随之调整，避免依赖"下标即偏移"这一隐含假设
- **验收**：连续 1 万次调用后 `len(records)` 稳定在 N；delta 统计仍正确

### D4 — 追踪开关与 provider 抽象（解决 K2，P0 配置 / P2 扩展）

- `config.yaml:210-216` 的 `tracing:` 段扩展为：

```yaml
tracing:
  provider: none            # none | loki | langsmith | langfuse
  sample_rate: 1.0          # 采样率（P1 起生效，避免日志量爆炸）
  redact: true              # 强制脱敏（见 D10）
  loki:                     # P1
    enabled: true
    span_events: true
  langsmith:                # P2（保留现有结构）
    api_key: ${LANGSMITH_API_KEY}
    project: self-evolving-kb
    endpoint: https://api.smith.langchain.com
```

- `setup_tracing()`（`tracing.py:21`）改为 **provider 分派**，返回值语义不变（`bool`），
  未知 provider 记 warning 并降级为 `none`（**不抛异常**，与现有容错风格一致）
- **默认值必须是 `none`**：追踪默认开启会带来日志量/成本/合规三重风险；
  由部署侧显式打开（`deploy.sh` 阶段可加校验）

### D5 — LangGraph 节点级 span 与命名规范（P1）

- **埋点位置**：`backend/app/graph/builder.py` 的节点包装处（**单一包装函数**，不要在每个 Agent 里各写一遍）
- **span 命名规范（固定，便于 LogQL 聚合）**：

```
graph.<agent>           例：graph.supervisor / graph.planner / graph.executor / graph.critic / graph.scribe
llm.<role>              例：llm.executor       （由 LLMFactory 侧统一产出，不由 Agent 各自写）
tool.<tool_name>        例：tool.web_search / tool.mcp.<server>
retrieval.<stage>       例：retrieval.embed / retrieval.bm25 / retrieval.rerank
mcp.<server>.<method>   例：mcp.jobcopilot.analyze_jobs_batch
```

- **每个 span 必须带**：`trace_id`、`span`、`parent_span`、`duration_ms`、`status`、`role`（若属某个 Agent）
- **与 LangSmith 的关系**：LangSmith 自动埋点产出的是它自己的 trace 树；
  自建 span 日志是**独立数据源**，两者靠 `trace_id` 关联即可，**不要试图让它们共用同一套 span id**

### D6 — 跨容器 MCP 追踪（解决 K3，P1）

**问题**：`jobcopilot-mcp` 是独立容器（`docker-compose.prod.yml:268`），
LangChain 自动埋点不跨进程；现有调用点在 `backend/app/tools/mcp/client.py`。

**设计（按代价从低到高，建议先做 ①）**：

1. **客户端侧包裹（必做，成本最低）**：在 `client.py` 的 `call_tool` 外层统一记 span
   （`mcp.<server>.<method>`，含入参摘要、耗时、成功/失败）。
   **这已经能回答"内核调用慢不慢、错没错"**——即使内核内部仍是黑盒。
2. **上下文透传（增强）**：把 `trace_id` 放进 MCP 调用的 metadata/arguments 约定字段，
   内核侧若愿意埋点，即可把自己的 span 挂在同一 `trace_id` 下。
   ⚠️ 需要**内核侧配合**，属于跨仓协作，需与子模块负责人确认（见 `AGENTS.md` §4）。
3. **不入内核**：明确接受"内核内部不可观测"，在文档里写清楚这个边界，避免误判。

### D7 — 评测管道（解决 K4 的一半，P0）

**框架已存在，只需补三样**：

1. **阈值与基线**
   - 在 `app/eval/runner.py` 的 `EvalReport`（`:57`）基础上，定义**门禁判据**：
     整体通过率 + 关键指标（如 `answer_groundedness`、`intent_confidence`）的下限
   - 引入**基线文件**（新建于 backend/eval-baseline.json，提交入库；**本文档交付时尚不存在**），形态：
     `{"commit": "...", "pass_rate": 0.93, "metrics": {"answer_groundedness": 0.85}}`
   - 判据：**不允许比基线下降超过 epsilon**（防"绝对阈值"掩盖渐进退化）
2. **`--fail-under` / `--strict` CLI 参数**
   - `backend/app/cli/main.py:93` 的 `eval` 子命令加参数；`app/cli/eval.py:36` 的 `run_eval` 返回后
     **不满足判据则 `sys.exit(1)`**（该文件当前 **0 处 `sys.exit`**）
   - **保留"只报告不阻断"的开关**（`--no-fail`）供本地调试
3. **报告可追溯**
   - `reporter.py` 产出的报告头部增加：被测 `commit`、时间、**画像指纹**
     （`prompt/job/README.md` 的 md5）与**提示词指纹**（`prompt/**` 的 md5）
   - 理由：评测结果依赖画像/提示词；不带指纹的报告无法判断"退化是代码引起的还是提示词引起的"
     ——这正是 2026-09-23 画像滞后事故的同类教训

**数据集要求**（现有两个数据集需评估补充）：
- 覆盖五个 Agent 各自的关键路径（不只是端到端问答）
- 每个用例标注**期望行为**（不只是期望文本），便于 `assertion.py` 做结构化断言
- **新增回归用例的规则**：每修一个线上问题，补一条用例（把事故变成测试）

### D8 — CI 门禁化（解决 K4，P0）

```yaml
# .github/workflows/ci.yml（eval-test 作业，当前 :239）
- name: Run eval on golden dataset (blocking)
  env:
    JWT_SECRET: eval-secret
  run: |
    python -m app.cli.main eval \
      --dataset app/eval/datasets/golden_qa.json \
      --baseline backend/eval-baseline.json \
      --fail-under 0.90
```

- **删除 `|| echo "eval non-blocking"`** —— 这一句就是"假门禁"的全部原因
- **保留原有前置条件判断**（数据集不存在时给出明确提示，而不是静默跳过）
- `needs: [build, eval-test]`（`ci.yml:287`）保持不变——门禁一旦为真，发布链自然被卡住
- ⚠️ **注意**：`eval` 需要真实 LLM 调用 → 需要 CI secret（API Key）。
  若 CI 无法访问 LLM，则**退化为"离线断言子集"**（只跑不需要模型的结构化用例），
  并在 CI 日志里显著标注"离线子集，未覆盖端到端"——**不要装作跑全了**

### D9 — Grafana 看板补全（解决 K5，P0）

在 `deploy/grafana/provisioning/dashboards/sekb-overview.json` 增加/改造面板：

| 面板 | 查询要点 |
|---|---|
| **LLM 延迟分布（按 Agent）** | `histogram_quantile(0.95, sum by (role, le)(rate(sekb_llm_call_latency_seconds_bucket[5m])))` |
| **Token 用量（按 Agent × 方向）** | `sum by (role, direction)(rate(sekb_llm_tokens_total[5m]))` |
| **成本（按 Agent）** | `sum by (role)(rate(sekb_llm_cost_usd_total[5m]))` |
| **成本（按模型）** | 保留现有面板 |
| **LLM 降级 / 重试率** | 保留现有面板，补 `role` 维度 |
| **单请求成本 P50/P95**（需 D7 的 per-request 指标） | 依赖缺陷 A 的修法（新建独立指标） |
| **trace 检索入口**（P1） | Grafana Loki 数据源 + `{app="sekb-backend"} \|= "span="` 模板变量 |

- 看板**随仓库 provision**（已 provision，改动即生效），不要把面板建在 UI 里（会丢）

### D10 — 脱敏与数据边界（**红线，P0 起就必须做**）

- **默认脱敏**：span 日志与追踪 metadata **不得**包含
  - 用户对话原文、知识库文档正文、JobCopilot 客户 JD 原文
  - 密钥/令牌/内网 IP（沿用 `docs/tech/.facts` 的 `<REDACTED>` 纪律）
- **只记可聚合的结构化字段**：长度、条数、模型名、耗时、token 数、状态、错误类型
- **正文一律以 hash + 长度代替**（既能比对"是不是同一段"，又不泄漏内容）
- **出境的额外门槛**：任何第三方 SaaS 追踪（LangSmith/LangFuse Cloud）
  必须**先有明确合规结论**，且默认关闭；开启时在 `deploy` 侧显式确认
- **验收**：随机抽 20 条 span 日志人工核对，**零原文泄漏**

---

## 6. 接口与契约（实现者直接照此编码）

### 6.1 新增/修改的环境变量与配置

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `tracing.provider` | string | `none` | `none` / `loki` / `langsmith` / `langfuse` |
| `tracing.sample_rate` | float | `1.0` | 采样率；生产建议 0.1~1.0 |
| `tracing.redact` | bool | `true` | 强制脱敏（**不允许配置为 false 用于生产**） |
| `tracing.loki.span_events` | bool | `true` | 是否输出 span 结构化日志 |
| `LANGSMITH_API_KEY` | env | 空 | 仅 `provider=langsmith` 时需要 |

### 6.2 新增函数/签名

```python
# backend/app/core/tracing.py
def setup_tracing(config: AppConfig | None = None) -> bool: ...        # 改为 provider 分派（签名不变）
def span(name: str, *, role: str | None = None,
         trace_id: str | None = None) -> AbstractContextManager[SpanHandle]: ...
    """统一 span 上下文管理器；内部按 provider 决定写 Loki / LangSmith。"""
def current_trace_id() -> str | None: ...                              # 从 structlog 上下文取

# backend/app/core/metrics.py
def record_llm_call(role: str, model: str, latency_ms: int, success: bool, ...) -> None: ...
    # 签名不变；行为新增：tokens/cost 带 role 标签

# backend/app/eval/runner.py
@dataclass
class EvalGate:                     # 新增
    pass_rate_min: float
    metric_mins: dict[str, float]
    baseline_path: str | None
    max_regression: float           # 允许的最大下降幅度
```

### 6.3 CLI 契约

```bash
# 现有
python -m app.cli.main eval --dataset app/eval/datasets/golden_qa.json --output tests/reports/eval_report.md
# 新增参数
  --fail-under 0.90          # 通过率下限，不满足 exit 1
  --baseline backend/eval-baseline.json
  --no-fail                  # 只报告不阻断（本地调试）
  --offline                  # 只跑不需模型的结构化断言子集
```

---

## 7. 实施计划

| 阶段 | 内容 | 产出 | 预估 |
|---|---|---|---|
| **P0-1** | D1 指标归因（加 `role`）+ D2 修重复计数 + D3 修无界增长 | 3 个改动 + 单测 | **2 人日** |
| **P0-2** | D7 评测判据与基线 + `--fail-under` + 报告指纹 | 新建基线文件 + CLI + 报告模板 | **2 人日** |
| **P0-3** | D8 CI 门禁化 + D9 看板补全 | CI 改动 + 看板 JSON | **1 人日** |
| **P0-4** | D4 追踪开关/provider 抽象（默认 none）+ D10 脱敏 | 配置 + `setup_tracing` 分派 + 脱敏工具 | **1 人日** |
| **P1-1** | D5 span 与命名规范（Graph 节点 + LLMFactory 统一产出） | `span()` + builder 包装 | **2 人日** |
| **P1-2** | D6 跨容器 MCP（先做客户端侧包裹） | `client.py` 包裹 + trace 面板 | **1 人日** |
| **P2** | 评估外部追踪后端（先决条件：磁盘扩容 / 出网验证 / 合规结论） | 选型结论 | **0.5 人日** |
| **收尾** | 同步 `T8-observability.md` 与 `09-OBSERVABILITY.md`；变更碎片 | 文档 | **0.5 人日** |

**合计 P0 ≈ 6 人日，P0+P1 ≈ 9 人日**（不含 P2 的运维改造）。

> **P0 可独立交付**：做完 P0 即满足 FDE JD 的"评估管道 + 对交付效果负责"，不必等 P1/P2。

---

## 8. 验收标准（逐条可验证）

| # | 验收项 | 怎么证明 |
|---|---|---|
| V1 | token/成本可按 Agent 下钻 | Grafana 面板选 `role=planner` 有数据；与 `llm_factory.py:914` 日志抽 5 条核对**数值一致** |
| V2 | 重复计数已修 | 同一请求前后，`llm_tokens_total` 增量 == 该请求日志的 token 合计（误差 0），不再是 2× |
| V3 | `records` 有界 | 压测 1 万次调用后 `len(records)` 恒定；delta 统计仍正确 |
| V4 | 评测是真门禁 | **故意让一条用例失败**，CI 必须**红**（这是唯一可信的证明方式） |
| V5 | 报告可追溯 | 报告头部含 commit + 画像 md5 + 提示词 md5；换一次提示词后指纹变化 |
| V6 | 延迟面板可用 | 面板显示 P50/P95，且能按 `role` 分层 |
| V7 | 追踪默认关闭 | 不配 Key、`provider=none` 时**零外部请求**（抓包/日志确认） |
| V8 | 脱敏有效 | 随机抽 20 条 span，人工确认**无对话原文/JD 原文/密钥** |
| V9 | 跨容器可观测 | 一次 `analyze_jobs_batch` 调用在 trace 里出现 `mcp.jobcopilot.*` span 且耗时可读 |
| V10 | 文档已同步 | `T8`/`09-OBSERVABILITY.md` 中"record_llm_call 无调用方"等过期表述已修正 |

---

## 9. 风险与回滚

| 风险 | 影响 | 缓解 |
|---|---|---|
| **加 `role` 标签是破坏性变更** | 旧序列成孤儿；看板查询失效 | 发布说明标注；同一次提交里改看板；旧序列按保留策略淘汰 |
| **修重复计数会让面板数字"变小"** | 有人误以为指标坏了 | 在变更记录里写清"此前偏高约 2×"，并给出核对方法（V2） |
| **评测门禁化后 CI 可能长期红** | 阻塞他人合入 | 先跑一轮建立真实基线（可能低于预期）；必要时先设 `--no-fail` 观察一周再切阻断 |
| **CI 无法访问 LLM** | 门禁跑不起来 | 用 `--offline` 子集 + 显著标注；端到端评测改为发布前人工/定时任务 |
| **span 日志量爆炸** | Loki 磁盘压力（本机仅剩 25G） | `sample_rate` + 只记结构化字段（不记正文）；设 Loki 保留期上限 |
| **追踪默认开启导致数据出境** | 合规问题 | 默认 `none`（D4）；开启需显式配置 + 合规结论 |
| **跨容器透传需改内核** | 涉及子模块协作（`AGENTS.md` §4） | P1 先只做客户端侧包裹，内核侧透传作为可选增强 |

**回滚**：所有改动均可通过配置回退——`tracing.provider=none`、`eval --no-fail`、
指标改动回退一次发布即可（Prometheus 旧序列会自然恢复上报）。

---

## 10. 附录：证据索引（实现者可逐个核对）

| 结论 | 证据位置 |
|---|---|
| 23 个指标定义完整、含 LLM 全套 | `backend/app/core/metrics.py:31-219` |
| `llm_tokens_total` 无 `role` 标签 | `backend/app/core/metrics.py:75` |
| `llm_cost_usd_total` 无 `role` 标签 | `backend/app/core/metrics.py:82` |
| Token/成本重复计数（两条写入路径） | `backend/app/core/metrics.py:267-270` 与 `:361-365` |
| `llm_calls_total` 混入 `role="aggregate"` | `backend/app/core/metrics.py:271` 与 `:359` |
| 三个指标从未埋点 | `conversations_total`:31、`reflection_pass_rate`:113、`tool_call_latency_seconds`:144（`metrics.py` 外零写入点） |
| `record_llm_call` 已接入 | `backend/app/core/llm_factory.py:46`（import）、`:944`（调用） |
| `records` 无界增长 | `backend/app/core/llm_factory.py:96`、`:893`；差值用法 `:729`、`:744` |
| LangSmith 代码就绪但生产未启用 | `backend/app/core/tracing.py:21`；`backend/app/core/bootstrap.py:181`；运行时日志 `{"enabled": false}` |
| trace_id 已注入 LangChain 配置 | `backend/app/core/tracing.py:57`；调用点 `llm_factory.py:397,567,609` |
| 评测框架完整 | `backend/app/eval/runner.py`（`EvalReport`:57、`run_dataset`:122、`pass_rate`:184）、`reporter.py`、`assertion.py`、`ragas_metrics.py` |
| CLI 无退出码 | `backend/app/cli/eval.py:36`（`run_eval`），全文件 0 处 `sys.exit` |
| CI 是假门禁 | `.github/workflows/ci.yml:239`（`\|\| echo "eval non-blocking"`）、`:287`（`needs: [build, eval-test]`） |
| Grafana 仅 3 个 LLM 面板 | `deploy/grafana/provisioning/dashboards/sekb-overview.json`（无延迟面板、无 role 维度） |
| 内核独立容器（跨容器边界） | `docker-compose.prod.yml:268`（`jobcopilot-mcp`）；`backend/app/tools/mcp/client.py:43` |
| T8 事实表已过期 | `docs/tech/.facts/T8-observability.md` 记 `record_llm_call`「全仓库无调用方」 |
| 磁盘约束 | 服务器实测：`/` 59G / 已用 33G / 剩 25G |
| 境外出网不可靠 | 服务器实测：容器内 `openaipublic.blob.core.windows.net` DNS 解析失败（`pypi.org` 正常） |

---

> **给实现者的最后一句话**：本 RFC 里"新增"的部分很少，"接上、修对、变成门禁"的部分很多。
> 如果发现某处基础设施比我描述的更好，**以代码为准，并回报修正本文档**——
> 本文档编写时已发现 `T8-observability.md` 因同一原因（文档滞后于代码）误导过读者，不要让它重演。
