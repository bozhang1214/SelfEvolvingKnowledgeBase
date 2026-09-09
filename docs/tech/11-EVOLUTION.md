---
title: 演进与技术债（Evolution）
layer: 评价层
owner: SEKB Team
status: active
version: v1.0.0
last-updated: 2026-09-09
based-on-commit: 1ffb13c
related: [01-ARCHITECTURE, 06-CONFIG-REFERENCE, 07-DESIGN-PATTERNS, 09-OBSERVABILITY]
---

# 11 · 演进与技术债（EVOLUTION）

> **本文回答什么问题**：有哪些技术债？短期/中期/长期往哪走？能力缺口与依赖风险是什么？
> **适合谁读**：决策者、架构师。
> **读完能做什么**：按优先级排技术债、规划演进路线、识别依赖风险。

> 技术债证据来自各事实表（T2 死配置 / T3 绕过调用 / T6 存储 / T7 异步 / T8 观测）、BACKLOG 与代码审查记录（docs/codeReview/）。as-is 与 to-be 分离。

---

## 1. 技术债台账（抽样，编号 TD-xx）

| 编号 | 类型 | 描述 | 证据 | 影响 | 修复成本 | 优先级 |
|------|------|------|------|------|---------|--------|
| TD-01 | 架构 | `--workers 1` 单点：JSON/Chroma 单写前提限制扩展 | Dockerfile；ADR-03 | 吞吐上限 | 高 | P1 |
| TD-02 | 数据 | JSON 存储无 SQL/索引/事务，软删不 purge | json_storage.py:318-341 | 数据增长与查询 | 高 | P1 |
| TD-03 | 配置 | **49 个死配置**（预算/评估/限流/淘汰等定义未接线） | T2 §15 | 认知负担/假安全感 | 低-中 | P1 |
| TD-04 | 一致性 | 多存储无锁 RMW（profile/shares/caches/news storage） | T6 | 并发丢更新 | 中 | P1 |
| TD-05 | 观测 | 4 指标未埋点 + record_llm_call 无调用方 + user/tool 标签失真 | T8 D-T8-1..6 | 监控盲区 | 低 | P1 |
| TD-06 | 架构 | LLM 统一入口被绕过 10 处（news/job/classifier/image/share） | T3 | 成本/统计/重试缺失 | 中 | P1 |
| TD-07 | 架构 | RateLimitMiddleware 未挂载；`_conv_inflight` 仅进程内 | server.py:195-201；T1 | 限流/并发防护缺口 | 中 | P1 |
| TD-08 | 安全 | 无 refresh 黑名单/jti；分享过期无清扫；image 段配置静默丢 | T2/T6/BACKLOG | 安全纵深不足 | 中 | P1 |
| TD-09 | 代码 | 上帝路由 chat.py:407-623 | 03 §2 | 可维护性 | 中 | P2 |
| TD-10 | 数据 | ChromaDB eviction 未实现；News/Job 清理不彻底 | T6 | 磁盘增长 | 低 | P2 |
| TD-11 | 注释 | 「不阻塞」vs await、72h vs 90 天等漂移 | T1/T2/T7 | 误导 | 低 | P2 |
| TD-12 | 前端 | no-explicit-any 114 处 warning | 10 | 类型安全 | 低 | P2 |
| TD-13 | 质量 | mypy strict 310 存量债 | 10 | 类型保障 | 高 | P1(渐进) |
| TD-14 | 数据 | skill 应聘助手入口已移除，后端偏好抽取/画像分支存留 | chat.py:393-404 | 死代码 | 低 | P2 |

---

## 2. 架构演进路线

### 2.1 短期（1-2 月）——止血与收口
- 修 `${VAR:-default}` 展开缺陷（T2 §4），清理死配置（先删明显无用的，其余转真实配置或删）。
- 接通预算控制/限流：cost_control 预算接线或明确删除；挂载 RateLimitMiddleware（或网关统一）。
- 补齐观测：4 指标埋点、record_llm_call 接线或删除、标签失真修复。
- LLM 统一入口：把 10 个绕过点收敛到 factory（先 image_processor/share，后 news/job）。
- 知识入库改后台执行（对齐注释）或改注释。

### 2.2 中期（3-6 月）——数据与并发地基
- JSONStorage → SQLite/Postgres 迁移（storage 抽象收口；compose 已预留 postgres）。
- 存储层加锁/原子写统一（profile/shares/caches）。
- ChromaDB eviction 实现或明确删除死配置；News/Job 清理补全。
- chat.py 服务层抽取（职责拆分），为多 worker 做准备。

### 2.3 长期（6 月+）——多用户与扩展
- 多 worker：外置存储 + Chroma client-server + Redis 锁（解除 ADR-03 约束）。
- L2 会话记忆（Redis）真正落地。
- 多供应商 LLM 抽象 + 前缀缓存/结果缓存降本。
- 分享过期清扫 + refresh 黑名单 + 细粒度 RBAC。

---

## 3. 能力缺口清单（业务可能需要的功能）

| 缺口 | 说明 | 关联 |
|------|------|------|
| 求职意图识别（graph 层） | 画像偏好抽取（profile_service 方案 B）随 skill 删除而解耦，待新增「求职」意图识别后按意图触发；可复用原 `_build_job_analysis_context` 的职位分析上下文注入 | WP3 / 03 |
| 混合检索 + Rerank + Query Rewrite | 当前纯向量检索 | 03/04 |
| 反思 NEEDS_REWRITE 分支/checkpointer | 状态图部分能力未全用 | 02 |
| PromptRegistry（版本/热加载） | 提示词硬编码于模板 | 03 |
| 资讯个性化 | D14 定案保持公共流 | BACKLOG |
| 完整断点续传上传 | 现轻量方案 | BACKLOG D8 |
| 会话/分享的定期清扫任务 | 过期数据清理 | 04 |

---

## 4. 依赖风险

| 依赖 | 版本 | 风险 | 处置 |
|------|------|------|------|
| ChromaDB | 1.5.9 锁版 | 已知 HNSW bloat 段损坏 bug，官方无修复 | 锁版+L1/L2 备份兜底；官方修复后升级回归 |
| deepseek-reasoner | — | TTFT 高（15-35s） | 降级链；成本告警 |
| langchain/langgraph | 1.x | 版本 API 变动 | 锁范围 `>=1,<2`；升级回归 |
| react-router（前端） | 6.26 | npm audit 5 条 moderate | 随主版本升级（BACKLOG/audit 上报） |
| BAAI/bge-small-zh | — | 需联网拉模型（HF_HOME 卷缓存） | 已持久化；etag 冷校验 ~20s |
| MCP/博查/招聘接口/RSSHub | 外部 | 配额/可用性不可控 | 超时/重试/降级已部分覆盖 |

---

## 5. 相关文档

- [01-ARCHITECTURE.md](./01-ARCHITECTURE.md)（ADR-03/05）
- [06-CONFIG-REFERENCE.md](./06-CONFIG-REFERENCE.md)（死配置）
- [07-DESIGN-PATTERNS.md](./07-DESIGN-PATTERNS.md)
- [09-OBSERVABILITY.md](./09-OBSERVABILITY.md)
- [docs/BACKLOG.md](../BACKLOG.md)（活跃待办）
- 审查记录：docs/codeReview/
