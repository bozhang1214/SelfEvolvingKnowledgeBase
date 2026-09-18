---
title: 测试与质量（Testing）
layer: 评价层
owner: SEKB Team
status: active
version: v1.0.0
last-updated: 2026-09-17
based-on-commit: 44dfed1
related: [00-README, 07-DESIGN-PATTERNS, 11-EVOLUTION]
---

# 10 · 测试与质量（TESTING）

> **本文回答什么问题**：测试覆盖现状如何？CI 门禁有效吗？质量短板在哪？
> **适合谁读**：QA、开发者。
> **读完能做什么**：知道如何跑测试、测试在管什么、哪些没测到。

---

## 1. 测试金字塔现状

| 层 | 数量 | 技术栈 | 说明 |
|----|------|--------|------|
| 后端单元 | 949 用例（60 文件）<!-- fact:backend_unit_cases=949 --> | pytest + pytest-asyncio + respx | 覆盖 config/graph/agent/storage/memory/news/job/state/plane_router 等 |
| 后端集成 | 16 | pytest | storage+memory 集成、eval pipeline |
| 后端 API 路由级 | 7（**已含在上面的 949 内**，此处单列只为标注技术栈） | FastAPI TestClient | test_api_client.py（知识列表/搜索/401/403） |
| 前端单元 | 68<!-- fact:frontend_unit_cases=68 --> | vitest + @testing-library/react | frontend/tests/ 下 api/auth-service/chat-store/file/home/logger/news/series-tree/user-store |
| Eval | golden_qa.json | app.eval | 黄金问答评估 |

**全量口径**：`pytest tests/` = **965**（949 单元 + 16 集成；API 路由级 7 条在单元目录内，
不重复计数）。加用例时请一并更新上面的活数字 `backend_unit_cases`。

**运行方式**：
- 后端：`python -m pytest tests/unit -q` / `tests/integration`（容器镜像或 `sekb-toolbox` docker）。
- 前端：`npm test`（vitest run）。
- 增量/全量脚本：`scripts/incremental_test.sh`（每次提交前）、`scripts/full_test.sh`（双周/月度）。

---

## 2. 测试覆盖矩阵（按模块，抽样）

| 模块 | 测试文件 | 覆盖核心 |
|------|---------|---------|
| 状态/图 | test_state / test_graph_routing | GraphState 字段、路由条件 |
| Agent | test_p0_fixes（planner/supervisor 降级） | 意图/规划/反思异常路径 |
| 记忆 | test_short_term_memory | 压缩、窗口、摘要原子性 |
| 知识库 | test_knowledge_ingestor / test_knowledge_base_filter / test_vector_store | 入库、过滤、原生 offset |
| 存储 | test_storage / test_share_storage | JSON 会话、分享 |
| 认证 | test_auth_metrics | 登录/注册指标 |
| 新闻 | test_news | 分类（单归属）、生成 |
| 配置 | test_config | Pydantic 校验/校验器 |
| 监控 | test_monitoring_endpoint | client-event、指标写入 |
| 前端 | tests/ 下 api/chat-store/file/logger/user-store/news/series-tree | store 会话隔离、SSE（mock streamChat）、上传、日志脱敏 |

---

## 3. Eval 评估体系

| 项 | 现状 |
|----|------|
| 数据集 | `app/eval/datasets/golden_qa.json` |
| 指标 | 8 项阈值量化（intent_confidence/plan_step_count/replan_count/tool_success_rate/answer_groundedness/critic_coherence_score/answer_relevance/e2e_latency_ms）+ token/成本统计（config.yaml:161-185；metrics.py:62-74） |
| 判级 | eval/metrics.py 读 evaluation.metrics.*.{good,warn} |
| 运行 | `python -m app.cli.main eval --dataset …`；CI 中 eval job 仅 push 到 main（定时任务不触发，ci.yml:195），**非阻断**（`|| echo`） |
| 断言 | assertion.py（阈值/回归字段，部分回归阈值死配置见 T2） |

---

## 4. CI 门禁有效性（.github/workflows/ci.yml）

| Job | 检查 | 门禁 | 阻断 |
|-----|------|------|------|
| lint | ruff（app+tests）+ mypy 回归门禁（基线 310） | 阻断（ruff）；mypy 超基线才失败 | ✔ |
| frontend-build | tsc + eslint(0 error) + vitest + build + npm audit(prod) | 阻断 | ✔ |
| unit-test | pytest tests/unit --cov-fail-under=40 | 阻断 | ✔ |
| integration-test | pytest tests/integration | 阻断 | ✔ |
| security-scan | gitleaks（非阻断）+ pip-audit | 部分 | 部分 |
| eval-test | golden eval | **非阻断**（需真实 LLM key） | ✘ |

**质量现状**：CI 阻断项覆盖 lint/类型/单测/集成/构建/生产依赖 audit；覆盖率门禁 40%（**2026-09-16 实测 54%**：`pytest tests/unit --cov=app` → TOTAL 10345 语句 / 4729 未覆盖）。

---

## 5. 质量短板与补齐建议

| 短板 | 现状 | 建议 |
|------|------|------|
| 覆盖率 | **54%**（门禁 40%，2026-09-16 实测）<!-- fact:coverage_pct=54 --> | 补 upload/job/news 路由测试；逐轮上调 |
| 流式/SSE | 无端到端 SSE 测试（仅有 mock streamChat） | 加 SSE 帧级测试 |
| eval | 非阻断、需真实 key | 固定 CI key 后转阻断；扩充 golden |
| E2E | 无浏览器级 E2E | 可选 Playwright（成本高，暂缓） |
| mypy | **308 存量债（基线 310）**（2026-09-17 实测 `scripts/mypy_gate.sh`）<!-- fact:mypy_debt=308 --> | 逐文件清零，下调基线 |
| 前端 any | 114 warning | 逐步收窄 no-explicit-any |
| 数据层 | ChromaDB/存储层并发无测试 | 补原子性/隔离测试 |

---

## 6. 相关文档

- [07-DESIGN-PATTERNS.md](./07-DESIGN-PATTERNS.md)（可测试性评分）
- [11-EVOLUTION.md](./11-EVOLUTION.md)
- 测试用例历史素材已随旧 docs 归档移除（git 历史可查）；当前用例见 backend/tests 与 frontend/tests。
