# 需求跟踪 / Backlog

> 用途：跟踪暂缓需求点与当前推进中的功能，避免遗漏。
> 最后更新：2026-09-09

## 一、进行中（本轮）

| # | 需求 | 范围 | 状态 |
|---|------|------|------|
| — | 测试工具链整合（代码审查批次 1+2） | ruff/mypy/eslint/pytest/vitest/pre-commit/gitleaks/pip-audit/npm audit 等已接入 CI 与脚本，见下「五、测试工具链清单与状态」 | ✅ 已接入，存量债按基线控制（mypy 310 / eslint any 告警待逐轮收窄） |
| — | 文档工程剩余（C1/C3/C5/C9-C10） | 主 README 重构、子系统成篇、部署手册合并、文档守卫脚本化 | 暂缓（见 D15） |

## 二、暂缓 / 待办（Deferred）

| # | 需求点 | 原因 / 说明 | 优先级 |
|---|--------|-------------|--------|
| D1 | GitHub 远端 URL 内嵌明文 PAT（`ghp_...`）安全修复 | 已标记未修复，需改为 SSH 或凭据助手；当前 GitHub 访问不稳定，暂用 `git bundle` + scp 部署 | 中 |
| D2 | 鸿蒙大类时间窗口 | 已决定维持 24h 现状，小众类允许不足 10 条（不硬凑） | 低（已定案） |
| D3 | 资讯日报：公共 RSSHub 依赖自建实例的长期运维 | 自建 `sekb-rsshub` 容器已运行，需关注版本升级与路由可用性 | 低 |
| D4 | 招聘分析：真实职位采集（爬虫/平台 API） | 当前先以手动粘贴 JD 文本为主，自动采集后续接入 | 中 |
| D5 | 监控页（Prometheus/Grafana）公网暴露 + 鉴权 | 当前监控页仅 Tailscale 内网可访问（ufw 只放行 22/80/443/41641），手机点飞书卡片「查看告警/看板」打不开；已选「安全优先」暂缓，后续如需手机直看，走 nginx 反代 + 基础鉴权暴露到 bos-studio.tech 子路径 | 低 |
| D6 | 资讯「安全/评测/治理」大类信息源 | 当前无专门安全源，该大类条目数为 0；已明确暂缓，后续跟进（需先确定 AI 安全/红队/评测类的信息源） | 低 |
| D7 | 资讯分类：Agent(应用层) vs 大模型(算法层) 按内容语义区分 | ✅ 已解决：批量 LLM 语义分类（方案 A），大模型 7→20、安全 0→20 条 | 已定案 |
| D8 | 文件上传「完整断点续传」（IndexedDB） | 当前为轻量方案（localStorage 记文件名 + 重新选择）。完整方案需 IndexedDB 存文件内容 + 后端支持分片上传，刷新后自动续传、无需重新选文件；工作量较大，暂缓 | 低 |
| D9 | 知识库按顶级分类分组为多个知识库 | ✅ 分享部分已解决：分享支持按三级分类（大类/子类/细类）限定范围（`4463d6b`）。「浏览页按分类分组为多个独立知识库（如顶部 Tab）」的展示改造未实施——当前侧边栏已有分类树可筛选，用户未明确要求 Tab 式分库，暂缓 | 中 |
| D10 | embedding 模型缓存持久化 | ✅ 已解决：`HF_HOME=/app/data/hf_cache` 指向 `sekb_data` 数据卷（`b662f89`），旧缓存已迁移进卷，重建容器不再重下模型，且随每日备份自足。冷启动仍会做 etag 校验（~20s）但不下载；如需完全离线可加 `HF_HUB_OFFLINE=1` | 已定案 |
| D11 | ChromaDB bloat-guard bug 官方修复后升级 | 1.5.9 是当前最新版且含 HNSW 压缩 bug，已锁死 `chromadb==1.5.9`。官方出修复版后需升级并回归验证大数据量写入；升级前靠 L1/L2 兜底 | 中 |
| D12 | 健康自检（检索探针） | 定期跑一次检索探针，向量丢失/检索异常时飞书告警，避免「用户先发现搜索为空」；L2 备份 + 探针可形成完整「检测→恢复」闭环 | 低 |
| D14 | 多功能联动（skill 调度 + 反馈闭环） | ✅ 已闭环：用户画像地基（`5d9eefc`）+ skill 按钮与调度（应聘/资讯助手注入画像与模块知识）+ 方案 B 记录员 LLM 后台抽取偏好回流画像（`fd395f2`）+ 画像反向影响职位分析（批量/单职位注入实时画像）。**资讯部分定案：保持公共流，日报不做个性化**（`b8e964d`） | 已定案 |
| D15 | 文档守卫工具（lychee 死链检查、OpenAPI/清单校验、文档结构守卫脚本） | 属于「文档工程剩余」（C1/C3/C5/C9-C10），统一推迟到文档专项轮次；本次测试工具链批次只接代码质量类工具，避免范围膨胀 | 低 |

## 三、已完成（本轮及之前）

- 资讯日报 → 科技资讯改名（日报/周报/月报标题统一为「AI 科技资讯」+ 副标题）
- 科技资讯：头条置顶 + 11 大类 + 每类总结预测 + 十分制打分 + 150~200 字原文摘要 + 关注建议 + 篇尾综合分析
- 周报/月报复用日报格式，按周/月时间跨度采集分析预测
- 鸿蒙专属源（自建 RSSHub 掘金 HarmonyOS 标签）
- 文件上传：单/多文件 + 文件夹选择 UI 修复
- 知识分享（share）+ 知识分类展示
- 飞书告警卡片（LowGroundedness 等说明 + 操作按钮）

## 四、代码审计跟踪（2026-09 全域评审）

> 来源：`docs/codeReview/2026-09-全域评审/`（01 工程评审、02 文档工程、03 路线图、04 第二轮复核、**Qoder 全项目深度审查**）。
> **编号冲突说明**：Qoder 报告用 SEC/CON/QLT/OPS/TST 编号，与 01/04 报告的 SEC/AGENT/RAG/OBS/PERF/DATA/FE/OPS/DOC/R2 **不同义**（如 Qoder 的 SEC-01=JWT 弱密钥，而 01 报告的 SEC-01=会话越权）。以下一律按「**主题**」合并登记，不再引用原始编号。

**已处理（截至 2026-09-08）**：
- **批次 A0**：chat 会话越权(IDOR)、异常脱敏(error_id)、资讯只读鉴权、分享分类限定+默认过期、备份 trap+restore、队列卡死、前端单测修复、RAG 检索失败标记
- **白名单**（ALLOWED_EMAILS 完整/预览隔离）+ **B1 思考过程流式** + **B2 发送快捷键**
- **3×P0**：CON-02 并发丢数据（JSON 存储 `asyncio.Lock` + uvicorn `--workers 1`）、SEC-01 JWT 弱密钥（fail-closed + 移除硬编码回退 + 轮换）、SEC-02 端口收敛（`127.0.0.1:8000`）
- **R2-06**：答案 token 真流式（完整：llm_factory astream + Executor 流式 + token_sink 回传）
- **文档工程第一刀（C2）**：`docs/README.md` 导航收敛 + Phase 5 能力速览 + 归档标注
- **测试工具链整合（代码审查批次 1+2，2026-09-09）**：见下「五、测试工具链清单与状态」

**未做（按优先级登记，含 Qoder 新增项）**：

| 优先级 | 主题 | 事项（摘要） |
|---|---|---|
| P0（本周） | JWT 收尾 | 密钥 fail-closed 已做；**未做**：jti 黑名单、刷新链限制、缩短有效期（90 天→短） |
| P0 | 限流重开 | 恢复 RateLimitMiddleware（auth/upload/job/news-refresh 分组；SSE 用并发数限制） |
| P0 | 降级可见化 | degraded 标记 → 响应/指标/前端（失败不再被记为 success） |
| P0 | 统一 LLM 入口 | news/job/classifier/image 走 `ainvoke_with_stats`（成本可观测、有重试） |
| P0 | RAG 入库闸门 | 评分公式修正 + 阈值入 config（低质对话不再全量入库） |
| P0 | Tracing 打通 | collector 持有 + 中间件注入 trace_id（当前零 trace） |
| P0 | 后端质量门禁 | **Qoder 动态实测 7 failed/530 passed**（`_classify→_classify_llm` 等漂移）→ **已修复（521→528 passed，2026-09-09）**；mypy 严格模式存量债 310 条 → **已转「回归门禁」**（超基线才失败，`backend/mypy-baseline.txt`）；eval 转阻断仍未做（eval 需真实 LLM key，保持非阻断） |
| P1（本月） | 注入防护 | SEC-05 声明未实现：输入长度限制 + blocked_patterns + 检索内容隔离 |
| P1 | 反思回路 | needs_rewrite 分支、replan 计数、should_reflect 接线、checkpointer/recursion_limit |
| P1 | Prompt 工程 | PromptRegistry（路径配置化+版本+热重载）+ 模板转义校验 |
| P1 | 步骤执行 | 并行调度（depends_on）、预检索回灌 supervisor、suggestions 回灌 planner |
| P1 | RAG 增强 | 驱逐/过期、混合检索+Rerank+Query Rewrite、分块策略、get/update/delete user_id 前置 |
| P1 | 指标补齐 | 埋点、trace 上下文、news/job 指标 |
| P1 | 存储收尾 | news index 原子写、缓存清理、archive 按用户淘汰（DATA-01 存储锁已做） |
| P1 | 前端健壮 | 错误可见、SSE 重连/controller 治理 |
| P1 | news/job 性能 | 并发/分批、周月报复用、采集限流、同步 IO 转 to_thread |
| P1 | 收尾项 | 会话锁 key、非流式 inflight、skill 注入面、画像锁/路径/缓存失效、队列上限/绑 conv、MD5 原子写、upload limit 5000、新功能测试 |
| P2（本季度） | 运维/架构/文档 | 灰度链路、编排层合并、SSRF/cookie/采集合规、暴露面收敛（监控栈/browser-service/webhook）、密钥长尾加固（argon2id/备份排除.env/分享过期统一）、**文档工程剩余（C1 主README/C3 子系统成篇/C5 部署手册合并/C9-C10 清单脚本化与守卫）** |

> 注：`--workers 1` 已一并缓解 Qoder 报告的 CON-01（多进程共享 Chroma）、CON-03（scheduler 重复）、OPS-01（Prometheus 多进程指标失真）等「多 worker 架构」类问题；若未来恢复多 worker，需先外置存储/Redis + Chroma client-server。
> 详细条目、证据与验收标准见 `docs/codeReview/2026-09-全域评审/` 各报告；批次顺序以 03 号路线图为准。

---

## 五、测试工具链清单与状态（2026-09-09 精简去重后）

> 目标（用户约定）：**每次提交前至少跑改动范围的增量测试**（`scripts/incremental_test.sh`），
> **每两周或每月跑一次全量测试**（`scripts/full_test.sh`）。工具清单由多份审查报告
> （01/04/Qoder/Batch）合并去重而来，同类合并、文档守卫类推迟到文档轮次（D15）。

### 已启用（Batch 1+2）

| 工具 | 用途 | 接入点 | 状态/门禁 |
|---|---|---|---|
| **ruff** | Python lint（E/F/I/N/W） | CI lint + pre-commit + 增量脚本 | ✅ 阻断；覆盖面 `app/` + `tests/`（`86443f9` 清零存量）；行宽统一 120 |
| **mypy --strict** | Python 类型检查 | CI lint（backend 目录生效）+ pre-commit + 增量脚本 | 🟡 回归门禁：存量债 310 条（`backend/mypy-baseline.txt`），超基线才失败；逐轮下调 |
| **pytest** | 后端单元/集成测试 | CI unit-test + integration-test | ✅ 阻断；528 passed（2026-09-09），覆盖单测+集成 |
| **pytest-cov** | 覆盖率 | CI unit-test | ✅ `--cov-fail-under=40`（当前 43%，逐轮上调） |
| **TestClient** | 后端 API 路由测试 | `tests/unit/test_api_client.py` | ✅ 7 例（知识列表/搜索透传、401、403 门禁、降级） |
| **vitest** | 前端单测 | CI frontend-build `npm test` | ✅ 阻断；58 passed |
| **@testing-library/react** | 前端组件测试 | `tests/home.test.tsx` | ✅ 3 例（渲染 + 路由跳转）；jsdom + jest-dom |
| **ESLint 9 flat + typescript-eslint + react-hooks** | 前端 lint | CI frontend-build + pre-commit + 增量脚本 | ✅ error 阻断（存量清零）；`no-explicit-any` 存量 114 条为 warning，逐轮收窄 |
| **tsc --noEmit** | 前端类型检查 | CI frontend-build + pre-commit | ✅ 阻断 |
| **pre-commit** | 提交前钩子 | `.pre-commit-config.yaml` | ✅ ruff + mypy 回归 + tsc + eslint（docker/本地） |
| **npm audit** | 前端依赖漏洞 | CI frontend-build | ✅ `--omit=dev --audit-level=high` 阻断（生产依赖现仅 5 条 moderate）；全量 audit 仅上报 |
| **gitleaks** | 密钥泄漏扫描 | CI security-scan | ✅（PR/提交级扫描，全历史仅供参考） |
| **pip-audit** | Python 依赖漏洞 | CI security-scan（backend requirements） | ✅ |
| **Dependabot / CodeQL** | 依赖更新 / 语义分析 | GitHub 原生 | 待仓库配置（GH Actions 侧） |
| **Trivy**（已并 hadolint） | 镜像漏洞扫描 | 部署前镜像扫描 | 待接入部署流水线（运维侧，见 D 系列） |
| **coverage 阈值 + codecov** | 覆盖率上报 | CI | fail-under 已开；codecov 上报保留 |

### 明确不做 / 推迟（精简去重结论）

- **safety 合并进 pip-audit**；**hadolint 合并进 Trivy**；文档守卫（lychee/OpenAPI/清单/结构脚本）推迟到文档轮次（D15）；node_exporter/cAdvisor 属监控运维（监控轮次），不进本清单。
- 新增代码时「同前缀单测」由 `scripts/incremental_test.sh` 提示补齐。

### 存量债（有意保留的基线，非漏检）

| 项 | 量 | 策略 |
|---|---|---|
| mypy strict 类型错误 | 310 | 回归门禁防新增；随重构逐文件清零，写回更低的 `mypy-baseline.txt` |
| ESLint `no-explicit-any` | 114 warning | 不阻断；新代码避免 any，存量随重构收窄 |
| npm audit devDeps 漏洞 | 多（eslint/vite/vitest 链） | 仅上报不阻断（dev 工具非生产面）；react-router 生产链 5 moderate 待随主版本升级 |
| 覆盖率 | 43% | 门禁 40%；新功能补测后逐步上调 |

---

### 约定

- 状态字段：`开发中 / 待开始 / 已完成 / 暂缓 / 已定案`。
- 每次功能完成或需求变更后更新本表，并补充「完成日期」。
