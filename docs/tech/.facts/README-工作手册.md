# 事实表抽取工作手册（阶段1 SSOT）

> ⚠️ **本表是 2026-09-09 逆向分析期的「编写用工作产物」，可能已落后。**
> 2026-09-16 复核确认：`T1-routes`（65 条 vs 实测 75 个端点）、`T2-config`、
> `T8-observability` 的**行号与计数已过期**。写文档时请以
> **代码本身 + `docs/tech/*.md`** 为准；本表仅作线索索引，不要把它的行号当断言。
> （不随技术文档一起维护的原因见 `docs/tech/漂移清单.md` DOC-11。）


> 用途：供并行子代理抽取 T1–T8 事实表，产出证据驱动、可直接被 12 篇文档引用的单一事实源。
> 存放：`docs/tech/.facts/T*.md`。仅作工作产物，不属于正式 12 篇文档。

## 硬性规则
1. **证据纪律**：每条记录必须带 `backend/…:行号` 或 `frontend/…:行号`；未读到的代码不许写。
2. **推断标注**：凡非直接从代码读出者，标注 `【推断·待验证】`。
3. **脱敏**：任何真实密钥/内网 IP/Token 一律写 `<REDACTED>`。
4. **客观**：只描述现状（as-is），不给改进建议（改进归 07/11 文档）。
5. **产出物**：Markdown 表，可被后续文档引用。文件头加三行 front-matter 占位后正文。

## 每张表的输出要求
- **T1 路由清单**：遍历 `backend/app/api/routes/*.py` 全部 `@router` 端点。字段：方法、路径、鉴权依赖、限流、请求模型（逐字段）、响应模型、错误处理、副作用（写入哪些存储/触发异步）、幂等性。
- **T2 配置项清单**：读 `backend/config.yaml` + `backend/app/core/config.py`。字段：键全路径、类型、默认值、是否被代码引用（可 grep 判定，未引用标「死配置」）、影响模块、生效时机、对应环境变量（含 `.env.prod` 可见项脱敏）。
- **T3 LLM 调用点清单**：grep `ainvoke_with_stats|astream_with_stats|llm_factory.get|\.ainvoke\(|\.astream\(` 于 `backend/app/`。字段：角色、模型、参数、超时/重试、文件:行号、是否走统一统计入口。
- **T4 AgentState 字段矩阵**：读 `backend/app/graph/state.py`。字段名/类型/写入节点/读取节点/默认值/生命周期。
- **T5 工具与集成清单**：读 `backend/app/tools/`（registry、direct、rag）、`backend/app/agents/executor.py` 工具调用。字段：名称/协议/入出参/降级行为/外部依赖。
- **T6 存储清单**：读 `backend/app/storage/`、`backend/app/memory/`（kb、short_term）、news storage、profile storage、job archive。字段：路径/结构/读写方/隔离/锁/清理/备份。
- **T7 异步与调度清单**：grep `asyncio.create_task|cron|schedule|BackgroundTasks|while True|sleep(` 于 backend/app（含 scheduler/）。字段：触发方式/并发/幂等/补偿/可观测。
- **T8 可观测性清单**：读 `backend/app/core/metrics.py`、`backend/app/core/logging.py`、monitoring 路由、prometheus 配置。字段：指标名/类型/标签/埋点位置；日志字段/注入点；trace 现状；告警规则（deploy/ 或 monitoring compose）。

## 验收
- 每张表与代码 grep 结果逐条可对上；无可对上证据的行必须删除或标推断。
- 完成后回报：本域文件数/端点/配置/调用点计数 + 发现的「异常/漂移」清单（如无鉴权路由、绕过统计入口的调用、死配置）。
