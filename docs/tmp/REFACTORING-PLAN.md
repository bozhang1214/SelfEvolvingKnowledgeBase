# SEKB 深度代码重构方案与实施计划（v1.0 已确认）

> 状态：**已确认（2026-09-09）**。范围=全量 M1–M4；D2=删除 skill 与硬编码分支；D3=P1-6 占位配置关闭并删配置。执行时拆解为 BACKLOG / 11-EVOLUTION 正式任务项，本文件为总纲（讨论区归档，迁移自 docs/ 根）。
> 依据：docs/BACKLOG.md、docs/tech/11-EVOLUTION.md（TD-01..14）、docs/tech/待确认项清单.md（Q-01..10）、docs/codeReview/未修复问题跟踪.md（P1/P2/Q/P2-P2）、docs/tech/漂移清单.md。
> 约定：全部结论基于 `file:line` 代码证据；改动遵循文档工程约定（活文档联动 + 每提交过门禁）。

---

## 1. 目标与范围

**目标**：在不改变对外行为的前提下，把 22.3k 行后端 + 7.7k 行前端的代码重构成——
清晰分层（api → 服务/用例 → 领域 → 存储）、模块边界可解释、命名统一、重复清零、
LLM 调用收敛到统一入口、死配置/死代码/中间脚本清理、Agent 实现符合开发规范。

**本次范围（重构）**：结构、边界、命名、重复、清理。**不含**（除非用户勾选）：
P1-4 多 worker 记忆（Redis）、P1-6 占位功能实现、RAG 入库闸门、Tracing 打通、SQLite 迁移等"功能补全"项——单列为可选项，见 §7。

**红线**：528 单测保持绿色、mypy 310 存量债不新增、ruff 阻塞、前端 tsc/eslint 0 错；
每工作包独立提交并过 `scripts/incremental_test.sh`；每里程碑后同步文档并部署一次。

---

## 2. 现状体检结论（证据）

### 2.1 分层边界问题：路由层充当"上帝服务"

| 文件 | 行数 | 职责混杂证据 |
|------|------|-------------|
| `backend/app/api/routes/upload.py` | 1006 | 上传 + MD5 去重 + 文档/图片入库流水线（`_process_and_ingest`:287、`_ingest_chunks`:216）+ 后台任务（`_background_ingest`:425）+ 文档分类（`_classify_document`:460）+ 直调向量库（`_require_vector_store`:198）+ 多个状态/删除端点——单文件 5 类职责 |
| `backend/app/api/routes/chat.py` | 847 | 聊天主流程（`_run_chat`:407）+ **画像偏好抽取子系统 5 个私有助手**（`_extract_and_update_profile`:269、`_extract_json_from_llm`:306、`_apply_pref_to_profile`:329、`_record_preferences_task`:355、`_schedule_preference_extraction`:393）+ 技能上下文构建（`_build_skill_context`:210）+ SSE 生成器——路由文件内含"画像服务 + 技能服务"两个领域 |
| `backend/app/api/routes/share.py` | 547 | 分享 CRUD + RAG 上下文构建（`_build_rag_context`:120）+ **裸 LLM astream**（:498-500，绕过统一入口）+ 历史消息转 messages（`_history_to_messages`:136） |
| `backend/app/api/routes/job.py` | 394 | 12 个端点 + **浏览器服务 HTTP 客户端 `_call_browser`:146 内联在路由文件** + 批量分析编排（`batch_analyze`:196） |
| `backend/app/api/routes/conversations.py` | 309 | 路由 + 错误翻译（`_handle_sekb_error`:66） |

> 已有 `app/services/` 层（classifier.py、series.py），但 upload/chat/share/job 的核心编排未下沉，服务层名存实亡。

### 2.2 重复实现（同一逻辑 ≥2 份）

| 逻辑 | 位置（file:line） | 跟踪项 |
|------|------------------|--------|
| `_extract_state_dict` | `api/routes/chat.py:128`、`cli/chat.py:569`（方法）、`eval/runner.py:373`（方法）— **3 份** | P2-14 / Q-4.5 |
| `_safe_float` | `agents/critic.py:182`、`agents/scribe.py:222` | Q-4.6 |
| `now_iso()` | `graph/state.py:245`、`storage/base.py`（推断） | Q-4.11 |
| `_fmt_dt` | `share.py:46`、`chat_share.py:34`、`knowledge.py:32` — **3 份**（本轮新发现） | — |
| `_require_share_storage`/`_owner_display_name` | `share.py:89/107` vs `chat_share.py:53/71` — 共享域拆成两个路由文件各自复制助手 | — |
| RAG 上下文格式化 | retriever/executor/`share.py:120` 三处 | P2-P2-03 |
| `except (MCPError, Exception)` | `tools/registry.py`（MCPError 是 Exception 子类） | NEW-E |

### 2.3 聊天链路残留（前端已移除技能按钮，后端未同步）

- `ChatRequest.skill` 仍在：`chat.py:106`，`_run_chat` 仍按其拼装 `skill_ctx` 前缀（:435-439）。
- `_build_skill_context` 硬编码两支 `应聘助手`(:230)/`科技资讯助手`(:260)。
- 反馈闭环 A：**每条**回复无条件执行 `_extract_and_update_profile`（:544，通用助手也跑画像解析）；
- 反馈闭环 B：仅 `应聘助手` 再调度一次后台抽取（:546-547）→ A/B 双跑 + 与前端 UI 脱节。
- `_conv_inflight` 锁 + `_background_extract_tasks` 集合存在于聊天模块——画像异步管道整体应归属画像/档案领域。

### 2.4 LLM 调用未收敛到统一入口（23 处中 10 处裸调）

统一入口：`LLMFactory.ainvoke_with_stats`(:232) / `astream_with_stats`(:366)。绕过点（无统计/无降级记录）：

| 位置 | 方式 |
|------|------|
| `agents/news/generator.py:216/265/382/422`（4 处） | `get(role)` + 裸 `ainvoke` |
| `agents/job/generator.py:212`、`agents/job/market.py:113` | 同上 |
| `services/classifier.py:91` | 同上 |
| `api/routes/share.py:498`（流式分享） | `get` + 裸 `astream` |
| `api/routes/upload.py:1000` | `get` + 裸 `ainvoke` |
| `tools/image_processor.py:337` | 裸 `ainvoke` |

> 对应 BACKLOG「统一 LLM 入口（news/job/classifier/image → ainvoke_with_stats）」。

### 2.5 死代码 / 已实现未挂载

| 项 | 证据 | 建议 |
|----|------|------|
| `RateLimitMiddleware` 已实现未挂载 | `middleware.py:34`；`server.py:196-197` 整段注释 | 重开（可选项 D4） |
| `NewsScheduler.trigger_now()` 无调用方 | `scheduler/scheduler.py:69`（全仓仅定义处） | 删除或暴露为运维端点 |
| `LocalTraceCollector` 创建即丢弃 | `bootstrap.py:143` `setup_tracing(config)` 返回值未接收；`tracing.py:27` | 打通 or 裁剪（D6） |
| 画像 5 助手系统（§2.3） | 见上 | 收编到 profile 域（D2） |
| 主聊天真流式已落地 | `chat.py:407` 起 `astream_events(v2)` + token_sink | P1-7 应标记**已解决**（跟踪项复核） |

### 2.6 配置与指标

- T2 事实表：178 个叶子键中 **49 个死配置**；`tools.vector_store.enabled` 死键（P2-P2-05）。
- P1-6 "已配置未执行"：`kb_strict`/`kb_prefer` 意图、`adaptive`/`sampling` 反思策略、`cost_control` 预算检查。
- P2-12/P2-13：`model_switch_threshold` 方向语义模糊、`sample_rate` 未消费。
- 死指标（据 codeReview）：`conversations_total`/`reflection_pass_rate`/`tool_call_latency`/`llm_call_latency` 等无埋点（metrics.py 复核确认后删/补埋点）。
- `get_config` lru_cache + 路径参数（Q-4.8）、`requirements.txt` 未用依赖（Q-4.10）。

### 2.7 前端

| 文件 | 行数 | 问题 |
|------|------|------|
| `pages/Job.tsx` | 1635（29 hooks） | 上帝组件：JsonBlock/MarketSection 等展示子组件与页面逻辑混排一文件 |
| `pages/Chat.tsx` | 970 | 流式/队列/复制/编辑/转发的 UI 全部内联 |
| `pages/Files.tsx` | 890 | 同上 |
| `stores/chat.ts` | 476 | 状态模型正确（per-conv 流式）但页内逻辑未抽 hooks |
| 残留 | — | 技能相关 UI 已删，需确认无遗留文案/类型字段 |

### 2.8 中间脚本/调试残留（需用户裁决，见 D7）

`backend/scripts/verify_phase3.py`（阶段性验证脚本）、`scripts/boss_qr_login.sh`（一次性采集辅助）、根目录 `start_backend.sh`/`start_frontend.sh`、`resume/`（疑似示例/临时目录）、根 `RAG常见问题汇总.md`。
保留：`backup_kb.sh`/`restore_kb.sh`（L2 备份）、`full_test.sh`/`incremental_test.sh`/`mypy_gate.sh`（质量门禁）、`rebuild_chroma_vectors.py`（灾难恢复）。

### 2.9 并发/一致性卫生（多为单 worker 下低风险，纳入收口）

P2-15 `ShortTermMemory.compress_if_needed` 无锁；NEW-D 恢复历史不触发压缩；Q-4.3 `JSONStorage` 读-改-写非原子；Q-4.7 `LLMFactory.stats.records` 无并发保护。

---

## 3. 目标架构与设计原则

### 3.1 分层与依赖方向（只允许上层依赖下层）

```
api/routes  （薄路由：参数校验 + 鉴权 + 调服务 + 组响应/SSE）
   │
services/   （用例编排：upload/ingest、chat/profile、share、job 的编排逻辑）
   │
agents/ memory/ tools/    （领域：Agent 图节点、记忆、工具——不感知 HTTP）
   │
core/       （横切：config/llm_factory/metrics/auth/token_sink/utils/errors）
storage/    （数据访问：JSONStorage + 各实体 Repository，单一写入点）
```

### 3.2 落地原则（映射到 §5 工作包）

1. **薄路由**：路由文件只做"取参→调服务→返回"；现有路由内 ≥60 行的私有逻辑一律评估下沉。
2. **领域下沉**：画像偏好子系统 → `services/profile_service.py`（或并入既有 profile 域）；RAG 上下文 → `tools/rag/` 统一格式化；浏览器 HTTP 客户端 → `services/browser_client.py`。
3. **单一事实源**：公共工具收敛到 `core/utils.py`（或 `graph/state.py`）唯一实现；`_extract_state_dict` 收口 1 处。
4. **统一 LLM 入口**：全部业务 LLM 调用走 `ainvoke_with_stats`/`astream_with_stats`；重试/降级/成本/统计只在一处实现。
5. **配置即事实**：死配置删除或实现（二选一，不留"已配置未执行"）；键名与消费点一一对应。
6. **Agent 规范**：Agent 输入/输出经 GraphState（TypedDict）结构化；不得持有跨请求可变全局（快照差值模式）；prompt 集中 `agents/prompts/templates.py`；异常走 `SEKBError` 体系与降级兜底；不直接依赖三方 SDK（统一 mcp client）。
7. **命名统一**：对齐 08-GLOSSARY 禁混清单（反思 vs 重规划、intent vs RAG 模式、draft vs final）；清除 `conv`/`conversation` 混用、`_require_*` 助手命名。
8. **保留既有好模式**（07-DESIGN-PATTERNS 已验证）：AppContext DI（bootstrap）、LLM/Agent/Strategy 工厂、反思 Strategy、Repository（storage/*）。

---

## 4. 设计模式应用对照

| 模式 | 现状 | 本次动作 |
|------|------|----------|
| 依赖注入（AppContext + bootstrap 组装） | ✅ 已有 | 保持；把可选字段从 `Any` 收窄为具体类型（bootstrap.py:53 起 10+ 个 `Any = None`） |
| 工厂（LLMFactory / AgentFactory / StrategyFactory） | ✅ 已有 | 保持 |
| 策略（ReflectionStrategy：always/adaptive/sampling） | ✅ 已有 | P1-6 处置后保留已实现策略 |
| 仓库（JSONStorage 子类/实体存储） | 🟡 半成品 | 统一 `_load/_save` 原子写助手；实体方法单一写入点 |
| **服务层/用例对象（facade）** | ❌ 缺失 | 新增：UploadService / ChatProfileService / ShareService / JobService（吸收路由编排） |
| 模板方法（Agent 基类：前置→调用→后置→记录） | 🟡 部分 | `BaseAgent` 提供 `_safe_float` 等公共助手（Q-4.6） |
| 装饰器（鉴权/限流/审计横切） | 🟡 部分 | RateLimitMiddleware 重开（D4）；鉴权装饰器统一 |
| 门面（service 对路由屏蔽领域细节） | ❌ 缺失 | 见服务层 |
| 观察者/发布订阅（SSE token 流） | ✅ token_sink contextvar | 保持 |

---

## 5. 工作包（Work Packages）与实施顺序

> 每个 WP：改动范围 → 目标结构 → 测试 → 文档 → 估算。
> 提交策略：**每个 WP 独立 commit**，先跑 `scripts/incremental_test.sh` 再提交；行为不变类 WP 需补**特性测试**先行垫底（WP0）。

### WP0 前置：跟踪项复核 + 特性测试垫底（0.5–1 d）
- 对 §2/跟踪项清单逐条 `grep` 复核，产出"复核表"：哪些已过期/已解决（如 P1-7 主聊天真流式、P2-P2-04 已修复、P2-11 部分修复）、哪些仍成立 → 同步修正 `docs/codeReview/未修复问题跟踪.md` 与 BACKLOG。
- 为 WP1–WP2 涉及重构的模块补最小**特性测试**（防回归垫片，对齐 Q-4.13 的方向）。
- 输出：复核表（写入 11-EVOLUTION 附录或独立小节）。**不部署**（纯文档+测试）。

### WP1 公共工具收敛：重复清零（1–2 d）【行为不变】
- 新建 `core/utils.py`：`now_iso()`、`to_state_dict()`（收编 3 份 `_extract_state_dict`）、`safe_float()`、`fmt_dt()`、`safe_rel_path()` 等；`graph/state.py` 只保留状态定义与 `create_initial_state`。
- 3 处 `_extract_state_dict` → 统一引用；Critic/Scribe `_safe_float` → 基类/工具；3 处 `_fmt_dt`、`now_iso` 2 处、share/chat_share 重复助手合并。
- `registry.py` `except (MCPError, Exception)` → `except Exception`（NEW-E）。
- RAG 上下文格式化统一（P2-P2-03）：先定规范 → `tools/rag/format.py` 单实现，retriever/executor/share 复用（行为需快照测试确认三处格式差异处理策略）。
- 测试：重构模块既有单测全绿 + 新增工具单测 ≥10。文档：07-DESIGN-PATTERNS（模式新增）、03-MODULES（utils 条目）、08-GLOSSARY。里程碑 **M1**，部署一次验证无行为回归。

### WP2 API 服务层下沉：拆上帝路由（3–5 d，最大 WP）
- 新建服务层（`app/services/`）：
  - `upload_service.py`：吸收 `upload.py` 的 MD5 索引、`_process_and_ingest`、`_background_ingest`、`_classify_document`、向量库准入/删除/系列逻辑；路由只留 5 个端点壳 + 鉴权 + 分页。
  - `profile_service.py`（画像）：吸收 chat.py 画像 5 助手 + 现有 `routes/profile.py` 数据访问，供聊天闭环 A/B 与 profile 端点共用。
  - `share_service.py`：吸收 share.py/chat_share.py 的分享业务（含 RAG 上下文、`_history_to_messages`），两个路由文件合并去重。
  - `job_service.py` + `browser_client.py`：吸收 `job.py` 编排与 `_call_browser`（独立 HTTP 客户端类，可测试）。
- 依赖注入：以上服务经 bootstrap 装配进 `AppContext`（或 `get_xxx_service(ctx)` 门面）。
- 测试：每个服务对应新单测（mock storage/llm）；API 路由单测补齐关键路径。文档：05-API-REFERENCE（端点行为不变但内部引用更新）、03-MODULES、00-README。里程碑 **M2**，部署 + 冒烟（上传/聊天/分享/招聘 各走一遍）。

### WP3 聊天链路瘦身与画像解耦（1–2 d）
- 按 **D2** 决策：
  - 若删技能：移除 `ChatRequest.skill`、`_build_skill_context` 及硬编码分支；`chat.py` 净减 ~200 行；闭环 A/B 统一并入 profile_service（A 保留但仅当画像抽取器判断有求职信号时跑，避免全量解析；或改为只在 `intent`/关键词触发）。
  - SSE/`_run_chat` 保持 v2 astream_events；把"状态提取/消息持久化/指标回填"整理为 pipeline 步骤函数（职责命名化）。
- 测试：`test_p0_fixes.py` 等既有 mock 适配 + 聊天 e2e 冒烟（API 层）。文档：02-RUNTIME-FLOWS（若流式事件不变则仅标注）、03-MODULES。里程碑 **M2** 尾部署。

### WP4 LLM 统一入口收口 + 横切（2–3 d）
- §2.4 的 10 处裸调改走 `ainvoke_with_stats`/`astream_with_stats`（保留角色名与降级语义，校验 news/job/classifier/image 各调用点的 prompt 结构兼容）。
- `LLMFactory.stats` 加并发保护（Q-4.7，asyncio 下 `asyncio.Lock` 或临界区最小化）。
- RateLimitMiddleware 重开（**D4**：挂载 + 默认阈值 + 429 文案与 `X-RateLimit-Remaining` 已在 CORS 暴露头）；`server.py:196` 注释清除。
- 死指标复核：有埋点的留、无埋点的补埋点或删除（metrics.py + 09-OBSERVABILITY 同步）。
- 测试：每个迁移点的统计/重试单测（mock astream）；限流单测（新）。文档：09-OBSERVABILITY、06-CONFIG（限流配置）、05-API（429）。里程碑 **M3**，部署。

### WP5 Agent/Graph 规范与记忆并发（2–3 d）
- **D3**（P1-6）决策后执行：关闭 → 删配置/删分支；实现 → 本轮仅最小闭环（如 `cost_control` 熔断 + `rag_retrieve` 意图接真实检索），其余留 11-EVOLUTION。
- P2-12/13：统一 `model_switch_threshold` 语义与注释、删/实现 `sample_rate`；死键 `tools.vector_store.enabled` 删除（P2-P2-05）。
- Q-4.12：builder 条件路由签名按当前 LangGraph 版本固定并加版本校验注释；state 中 7 个"只写不读"字段清理或标注消费方（T4 事实表对照）。
- P2-15 + NEW-D：`ShortTermMemory` 压缩加 `asyncio.Lock`（conv 粒度，可复用 `_conv_inflight` 模式）；历史恢复后触发一次压缩。
- **D6**：tracing 打通（collector 进 AppContext + 提供读取/导出）或裁剪 local_json 分支。
- 测试：Agent/记忆新增单测 ≥15（含并发压缩、恢复触发压缩）。文档：03-MODULES、04-DATA-MODEL、06-CONFIG、09-OBSERVABILITY。里程碑 **M3** 尾部署。

### WP6 配置/死代码/脚本清理收口（1–2 d）
- T2 的 49 死键按"删除 or 补消费"逐类处置（输出处置表 → 06-CONFIG-REFERENCE 死配置表清空或降为 0）。
- `NewsScheduler.trigger_now` 删除或暴露（确认无调用后删）；`LocalTraceCollector` 随 D6；CLI/API/`eval/runner.py` 剩余重复收口复核。
- **D7** 脚本裁决执行：删 `verify_phase3.py`；`boss_qr_login.sh`/`start_*.sh` 处置；`resume/`、根 `RAG常见问题汇总.md` 归档或删。
- `requirements.txt` 未用依赖清理（Q-4.10，用 pipdeptree/ruff 复核后手工删，改动过镜像需重建验证）。
- 文档：06-CONFIG、11-EVOLUTION（TD 台账更新）。里程碑 **M4**。

### WP7 前端组件化重构（2–4 d）
- `pages/Job.tsx`（1635）拆为 `features/job/`：页面 + 分析报告组件 + 市场组件 + hooks（`useJobAnalysis`/`useBatch`）+ 纯函数（classifyRole/校验等移 `utils/`）。
- `pages/Chat.tsx`（970）拆：消息气泡/操作条/输入区/流式指示器组件 + `useChatStream` hook（消费现有 per-conv store）。
- `pages/Files.tsx`（890）同法拆分；`Knowledge.tsx`/`News.tsx` 顺带。
- 清理：技能残留类型/文案（确认无后端依赖后）；`types/` 与 service 返回值去重；utils/logger 是否对接 `/monitoring` 保留。
- 测试：vitest 补组件级 10–15 例（快照/事件），eslint/tsc 0 错。文档：03-MODULES（前端节）、10-TESTING。里程碑 **M4**。

### WP8 全量验证 + 文档收尾（0.5–1 d）
- `scripts/full_test.sh` 全绿；pre-commit 全钩子过；手动冒烟主链路（登录→聊天真流式→上传→分享→招聘→日报）。
- 文档同步：CHANGELOG、BACKLOG（勾选完成项）、11-EVOLUTION（TD 结项/新增）、00-README、07-DESIGN-PATTERNS、03/05/06/09 各受影响章。
- 打包部署（git bundle → 服务器 ff-only），验证 server HEAD 与监控告警无新增异常。

---

## 6. 里程碑总览

| 里程碑 | 内容 | 估算 | 部署点 | 交付物 |
|--------|------|------|--------|--------|
| M1 地基 | WP0 + WP1 工具收敛 | 1.5–3 d | M1 末 | 复核表、utils、重复清零、全绿 |
| M2 后端结构 | WP2 + WP3 服务化拆分 | 4–7 d | M2 末 | 服务层 4 个、路由瘦身 ≥1500 行下沉 |
| M3 LLM/Agent 规范 | WP4 + WP5 | 4–6 d | M3 末 | 统一入口 100%、限流、记忆锁、配置语义 |
| M4 收口 | WP6 + WP7 + WP8 | 3.5–7 d | M4 末 | 死键清零、前端拆分、全量绿、文档收尾 |

**总计约 13–23 个工作日**（若砍掉可选项 WP7 前端或 WP4 限流可再压缩；可按里程碑分批推进，每批独立交付）。

---

## 7. 决策记录（已确认 + 默认项）

> ✅=Owner 已拍板；默认=采用"我的建议"，执行中若与事实冲突会先停下回报再改。

| # | 决策 | 结论 |
|---|------|------|
| D1 | 跟踪项过期复核并入 WP0 | ✅ ①并入（已解决项如实标记，如 P1-7 主聊天真流式已落地） |
| D2 | 聊天 `skill`/技能上下文与画像闭环 A/B | ✅ **①删除** skill 字段、`_build_skill_context` 及硬编码分支；画像抽取仅当检测到求职意图时触发，逻辑重构入 profile 域 |
| D3 | P1-6 占位配置（kb_strict/adaptive/cost_control 等） | ✅ **②直接关闭删配置**；实现项列入 11-EVOLUTION 短期路线 |
| D4 | `RateLimitMiddleware` 本轮重开 | 默认①重开（挂载 + 默认阈值可配 + 429 与 X-RateLimit-Remaining） |
| D5 | JSONStorage 写一致性 | 默认①保持单写 + per-conv asyncio.Lock + 文档化约束（SQLite 迁移独立立项） |
| D6 | `LocalTraceCollector`/tracing | 默认②裁剪 local_json 分支仅留 LangSmith——**前提**：WP0 复核确认 collector 无任何消费方；若日常依赖本地 trace 请改①（collector 进 AppContext） |
| D7 | 脚本/残留目录处置 | 默认：`verify_phase3.py` 删；`boss_qr_login.sh` 留（运维工具，文档标注于 docs/ops）；`start_backend.sh`/`start_frontend.sh` 留（本地开发入口）；`resume/`、根 `RAG常见问题汇总.md` 用途不明 → 归档 docs/tmp 待 owner 说明后定 |
| D8 | Q-4.7 LLM stats 并发 | 默认①加 asyncio.Lock（成本低） |
| D9 | mypy 310 存量债 | 默认①维持回归门禁不追债（追债另立专项） |
| D10 | 范围 | ✅ **全量 M1–M4**，按里程碑分批交付 |

---

## 8. 风险与不做的事

- **风险**：WP2/WP3 改动面大 → 用 WP0 特性测试垫底 + 每 WP 独立提交 + M2 部署前手工冒烟清单对冲。
- **不做（本轮）**：Redis 多 worker（P1-4）、SQLite/Postgres 迁移、RAG 入库闸门、Tracing 全链路、Agent 单测全量补齐（Q-4.13 只做重构相关垫底）、mypy 债清零、LangGraph 升级——均保留在 11-EVOLUTION 路线并标注依赖本重构的结构前提。

---

*起草：2026-09 ｜ 依据提交 bf0f90e ｜ v1.0 已确认（2026-09-09），执行时逐 WP 拆解 BACKLOG*
