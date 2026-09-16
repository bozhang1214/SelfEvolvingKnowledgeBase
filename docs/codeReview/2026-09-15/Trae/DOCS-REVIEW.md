# 文档全量审查 · 2026-09-15

> 覆盖范围：`docs/` 下除 `docs/tmp/` 外全部 md（52 份）
> 审查维度：代码一致性 / 编目导航 / 内容逻辑 / 缺失章节 / 格式规范

---

## A. 与代码不一致（🔴 影响信任）— 16 条

| # | 文档位置 | 问题 | 建议 |
|---|---------|------|------|
| D-A1 | ops/02-PRODUCTION-DEPLOY §1.3 | 写 "backend 限制 **4G**"，但 `docker-compose.prod.yml` 已改成 **2.5G**，CHANGELOG 也记录过这次改动 | 改 `4G → 2.5G`，并把旁边的说明（"宿主机总共 3.6G"）补进去 |
| D-A2 | ops/01-DEPLOYMENT §13.2 端口表 | 列了 backend 宿主机端口 8000、backend-canary 8001、postgres 5432、redis 6379——**实际 compose 里根本没有**宿主机端口映射 | 删掉这几行；加注释"backend 仅在 sekb_network 内部访问，对外一律经 nginx" |
| D-A3 | ops/01-DEPLOYMENT §4 API 使用指南 | chat/* 和 upload/* 写"无需鉴权、用固定 user_id=\"default\""——但代码 chat.py 和 upload.py 都挂了 require_full_access 白名单 | 把 chat/upload 移到"需要 JWT"行，删除 `user_id="default"` 说法 |
| D-A4 | ops/02-PRODUCTION-DEPLOY §1.2 端口 | Grafana/Prometheus/Alertmanager/Loki/feishu-webhook 都写了"可选对外暴露"——CHANGELOG 修过 XFF 信任问题，监控端口应只走 SSH/Tailscale | 改成"**不对外暴露**，见 05-TAILSCALE-ACCESS"，跟 11-MONITORING 口径统一 |
| D-A5 | ops/01-DEPLOYMENT §10 安全加固 | 勾了"API 限流（nginx limit_req 10r/s）"——但实际是双闸：nginx（api_limit 10r/s + news_limit 30r/s + client_event_limit 2r/s） + backend ASGI RateLimitMiddleware（auth 5 / upload 30 / news_generate 6 / news 读 120 / 默认 60） | 更新描述为双闸机制，列清两侧参数 |
| D-A6 | ops/01-DEPLOYMENT §8 手动 docker compose | 写 `docker compose -f docker-compose.monitoring.yml up -d` **不带** `--env-file .env.prod`——CHANGELOG 明确修了这个漏传导致飞书 webhook URL 为空 | 每一条 docker compose 命令统一加 `--env-file .env.prod` |
| D-A7 | ops/01-DEPLOYMENT §7.3 启动监控栈 | 写监控栈是 prometheus/grafana/loki/alertmanager——实际 compose 还包含 feishu-webhook + canary | 列全：prometheus + grafana + loki + promtail + alertmanager + feishu-webhook |
| D-A8 | ops/01-DEPLOYMENT §4.3 聊天鉴权 | curl 示例 `POST /chat/` **没带** `Authorization: Bearer <token>`——读者复制粘贴直接 403 | 补上 `-H "Authorization: Bearer <token>"` |
| D-A9 | ops/01 §7.3 CI/CD vs ops/02 §7.5 | 两处部署阶段编号不一致（01 写 8 步骤，02 写 8 步骤但清单不同，deploy.sh 实际是 7 阶段） | 统一到 deploy.sh 的硬闸门检查→备份→构建→启动 backend(300s 等待)→启动 frontend→启动监控→验证→清理 |
| D-A10 | ops/01 §7.2 健康 curl | 写 `/api/v1/health/live` 返回 `{"status":"ok","services":{...}}`——但 `/live` 只返回 `{"status":"ok"}`，services 字段是 `/api/v1/health/` 综合健康才有的 | 修描述：live 仅返回 `{"status":"ok"}`；综合健康用 `/api/v1/health/` |
| D-A11 | ops/01 §9.1 备份说明 | 备份清单列了"PG/Redis/ChromaDB"——但 **PG 从未启用**（compose 没 postgres service），Redis 2026-09-10 才加 | 改成真实清单：sekb_data 卷 + Redis（可选已启用） + Gitea + 配置/ENV |
| D-A12 | ops/01 §4.2 SSE 事件 | SSE done 事件只有 meta 字段——但 chat.py 的 done payload 实际有 conversation_id、intent、metrics、trace_id、latency_ms 等 | 对照 chat.py:730-800 真实 payload 重写 |
| D-A13 | ops/02 §1.2 端口表 | 写 backend 容器端口 8000 宿主机 8000"可调试"——实际 backend 没有 ports 映射 | 改成"容器内 8000，不对外暴露；调试用 `docker exec sekb-backend curl localhost:8000/api/v1/health/`" |
| D-A14 | ops/01 §9.2 定时备份 | cron 写 `./deploy/backup.sh`——但 BACKLOG G4 明确记录 cron 实际跑的是**另一份** `/opt/self-evolving-kb/backup_kb.sh` | 要么让 backup.sh 和 backup_kb.sh 二选一为单一事实源，要么明确说明 |
| D-A15 | ops/11-MONITORING §2 Grafana 面板清单 | 列了 12 个面板但没标注哪些是空壳——Ops 2.1 承认"部分指标因埋点缺陷从未写入" | 加"实际有数据？"列，对照 tech/09-OBSERVABILITY §2.3 逐条打勾/叉 |
| D-A16 | ops/07-ALERTING-TROUBLESHOOTING | 只讲 Alertmanager 告警链路——2026-09-15 CHANGELOG 新增的**应用侧告警**（alerts.py + news 失败推飞书）完全没提 | 新增一章：news 失败→alerts.py→feishu-webhook；手动自测端点 `/test`；DEDUP_WINDOW=300s |

---

## B. 编目与导航（🟡 影响可发现性）— 10 条

| # | 文档位置 | 问题 | 建议 |
|---|---------|------|------|
| D-B1 | docs/README.md | 运维手册索引写"编号 **00-10**"，实际已有 **00-15** | 改成 00-15 |
| D-B2 | docs/README.md 开篇 | 写文档工程按旧 prompt 重构——引用的文件已移走 | 改成"按 docs/tech/.facts/T1-T8 事实表 + .validation 校验脚本维护" |
| D-B3 | ops/00-README.md | 运维手册和技术手册的分层声明写反了；ops/00 没把 tech/09-OBSERVABILITY 放进 related | 在 ops/00 的 related 里加上 tech/09；ops/00 应回链 tech/00 |
| D-B4 | docs/BACKLOG.md REFACTORING 段 | 回链 `docs/tmp/REFACTORING-PLAN.md`——**死链**，tmp 被排除在范围外 | 挪到 docs/codeReview/ 归档，或删除引用 |
| D-B5 | ops/14-CHANGE-RELEASE-POLICY | 引 `docs/tmp/自迭代闭环设计RFC.md`——死链 | 若 RFC 已消化进 CHANGELOG 和 codeReview，改为回链 |
| D-B6 | ops/15-MCP-ENDPOINT §72 | 引用 `docs/tmp/verify_public_mcp.py`——tmp 排除范围 | 挪到 scripts/ 或删除引用 |
| D-B7 | ops/12-GITEA §3 | 引用 RFC `docs/tmp/自迭代闭环设计RFC.md` | 同上统一处理 |
| D-B8 | 根目录 | 有 RAG常见问题汇总.md、README.md、.env.project——这三份**没有任何一份**在 docs/README.md 的地图里 | 在 docs/README.md 加"根目录散件"段 |
| D-B9 | tech/00-README §版本选择 | 写"完整版 12 篇"——实际 tech/ 有 11 篇编号（00-11） | 改成"11 篇编号主文档（00-11）" |
| D-B10 | ops/01 §1 架构简图 | 只有 frontend + backend + sekb_data + monitoring——**缺** RSSHub、browser-service、Redis、jobcopilot-mcp 四个独立容器 | 重绘 C4-L2 级别图：frontend→(nginx)→backend + RSSHub(1200) + browser-service(1300) → Redis(6379) → ChromaDB |

---

## C. 内容逻辑与可读性（🟢 影响理解效率）— 8 条

| # | 文档位置 | 问题 | 建议 |
|---|---------|------|------|
| D-C1 | 全 tech/ | 术语不统一：Chromadb/ChromaDB/ChromaDb 大小写混乱；"短期工作记忆"/"L1 短期记忆" 混用；KNode 在 04 出现但 08-GLOSSARY 没收录 | 08-GLOSSARY 加维护约定："新增术语必须先在 08 登记，带同义词/大小写/禁用写法" |
| D-C2 | ops/02 + ops/03 + ops/04 | 三篇部署手册**严重重复**：Docker 安装、firewall、HTTPS、备份、成本表几乎一字不差 | 02 抽成"通用生产部署"，03/04 只保留差异化（腾讯云特有/轻量镜像选法） |
| D-C3 | ops/01-DEPLOYMENT.md | **1400 行单文件**，混了本地开发 + 生产部署 + API 参考 + 灰度 SOP + 备份恢复——四类人群混读 | 把 §3-§5（本地+API+CLI）移到 ops/00b-QUICK-START.md；ops/01 只保留生产运维 |
| D-C4 | tech/01-11 全部 | 每篇头部有 `based-on-commit`，但这个 commit 是写文档时的 HEAD——从未有人验证/更新 | 去掉 based-on-commit，换为 `last-verified` 字段，CI 脚本对比代码是否漂移 |
| D-C5 | tech/05-API-REFERENCE §14 CLI | 写"未逐条展开，标注为待补精确参数"——但 CLI 入口真实存在，半年没补 | 1 周内补齐（从代码自动抽 click docstring），或给明确的"以 `sekb --help` 为准"标记 |
| D-C6 | 全 ops/ | 多数运维手册写的是相对当前版本的现状，但部分写死了 | 每篇末尾加"本手册适用 docker-compose.prod.yml 版本 + deploy.sh 版本 + config.yaml 版本"锁定行 |
| D-C7 | ops/05-TAILSCALE + ops/11-MONITORING | 两篇对 Tailscale 和监控访问的关系描述**分散**，读者可能看了监控文档直接去改 nginx 暴露 3001 | 统一：11 的访问章节**只回链 05**，SSH/Tailscale 两种私网访问在 05 集中讲 |
| D-C8 | tech/11-EVOLUTION | 路线图"短/中/长期"边界模糊；BACKLOG G 系列运维债完全没引用 | 在 EVOLUTION §4 债台账里直接列 BACKLOG G 条目的 id 和状态 |

---

## D. 缺失的重要章节（🔵 影响完整性）— 7 条

| # | 位置 | 缺失内容 | 建议 |
|---|------|---------|------|
| D-D1 | docs/ 根 | **没有 10 分钟快速上手**——ops/01 §3 还是要先装 torch+transformers（~3.2GB） | 新增 ops/00b-QUICK-START.md，用 CI 里的 SkeletonStubLLM 跑 L1 骨架评测（不需要真实 LLM） |
| D-D2 | ops/ 下 | **数据目录结构说明**缺失——data/ 下 news/chroma/profile/audit/hf_cache/rsshub/gitea 等子目录，哪个能删、哪个随备份——没人写 | 新增 ops/16-DATA-DIRECTORY.md：子目录总表 + 清理 SOP + 备份包含/不包含清单 |
| D-D3 | tech/09 或 ops/11 | **监控/告警决策树**缺失——触发 BackendDown 做什么？触发 HighLatencyP95 做什么？散在 07 和 CHANGELOG | 加"告警→处理步骤"表：BackEndDown→SSH→docker ps→docker logs→curl health |
| D-D4 | tech/ | **如何写新 news source**——backend 有 rss_fetcher.py、自建 RSSHub，但没人写怎么加新源 | 新开 tech/13-NEWS-SOURCE.md |
| D-D5 | tech/ | **如何接入新 LLM**——llm_factory.py + config.yaml roles，想换 provider 要翻代码 | 在 tech/06-CONFIG-REFERENCE 的 LLM 章节加 "Adding a new provider" 子节 |
| D-D6 | 根目录 | **CONTRIBUTING.md** 缺失——AGENTS.md 只覆盖多 Agent 协作，没有一般 PR/commit/测试规范 | 根目录新增 CONTRIBUTING.md，引用 AGENTS.md 为"多协作者专属" |
| D-D7 | docs/ | **安全注意事项集中文档**缺失——密钥轮换 SOP、白名单修改流程、已暴露端点列表 | 新增 ops/17-SECURITY.md |

---

## E. 格式与规范（⚪ 影响专业度）— 10 条

| # | 位置 | 问题 | 建议 |
|---|------|------|------|
| D-E1 | 全 tech/ vs ops/ | front-matter 不一致：tech/ 全部有，ops/ 只有 00-04 有 | 统一补齐或全部删除 |
| D-E2 | tech/00-README | 写"完整版 12 篇"但仓库现有 11 篇编号 | 改成"11 篇编号主文档" |
| D-E3 | tech/00-README §6 | 写 "ops/ 编号 00-10"（实际 00-15） | 改 00-15 |
| D-E4 | CHANGELOG.md | 3 万+字，所有条目**都是 2026-09-15**，严重膨胀 | 顶部加注释"本文件为 2026-09-15 合并生成；历史碎片见 git 历史 docs/changelog.d/" |
| D-E5 | codeReview/未修复问题跟踪.md | 唯一一个中文文件名 | 改成 OPEN-ISSUES.md 或确认有意为之 |
| D-E6 | 全仓 | 中英文标点混用偶发：ops/01 的全角破折号、tech/02 混合半角/全角数字 | 统一规则：中文正文全角标点、数字/单位/代码半角，写进 CONTRIBUTING.md |
| D-E7 | tech/05 §9 news | 写 `GET /{report_type}` 但没写是 weekly/monthly | 改成 `/{report_type} (weekly\|monthly)` |
| D-E8 | ops/01 | ```bash 标记偶尔省略（§3.2 标了、§3.3 开头没标） | 加检查脚本（tech/.validation 已有 check-doc-sync.sh，再加 fenced code block 语言标记检查） |
| D-E9 | tech/.facts/ | 事实表 README 没写"新事实表怎么加" | 加"新增事实表步骤"：命名规则、证据格式、消费方 |
| D-E10 | ops/01 §9 备份 | 写"本地保留 7 天"但没写 Gitea/ChromaDB/配置/Redis 各自保留策略 | 加一张"每类数据的保留期 + 备份方式 + 是否远端"表 |

---

## 最高优先处理建议

| 优先级 | 编号 | 理由 |
|--------|------|------|
| **🔴 P0** | D-A1 | 内存 2.5G 漂移——运维照着给机器内存会错配 |
| **🔴 P0** | D-A2 / D-A13 | backend 端口未对外——部署者以为 backend 暴露了 8000 |
| **🔴 P0** | D-A3 / D-A8 | chat 鉴权写反——读者 curl 直接 403 |
| **🔴 P0** | D-A16 | 应用侧告警完全没文档——部署者不知道资讯失败会推飞书 |
| **🟡 P1** | D-C3 | ops/01 1400 行单文件——新人找不到要的章节 |
| **🟡 P1** | D-B10 | 架构简图缺 4 个容器——运维看着图不知道还有什么要跑 |
| **🟡 P1** | D-C2 | 三篇部署手册一字不差重复——改一处要改三处 |

---

## 总计

| 分类 | 数量 |
|------|------|
| A 与代码不一致（🔴） | 16 |
| B 编目与导航（🟡） | 10 |
| C 内容逻辑（🟢） | 8 |
| D 缺失章节（🔵） | 7 |
| E 格式规范（⚪） | 10 |
| **合计** | **51** |
