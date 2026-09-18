# 协作登记表（开工前认领，收工后删除）

> 约定见 [AGENTS.md](../AGENTS.md)。**开工前加一行，收工后删掉**——表格越短越有用。
> 目的：改共享文件前能看出「有没有人正在改同一个文件」。

## 进行中

| 工作流 | 负责人 | 涉及文件（重点） | 开始时间 |
|---|---|---|---|
| monorepo 重构（端侧并入 apps/）+ 构建摩擦消除 | DSH Agent | `apps/**`、`scripts/android.sh`、`.tooling/`、`README.md`、`AGENTS.md`、`docs/RFC-端云协同与端侧Agent.md` | 2026-09-18 15:10 |
| M2 端侧宿主（SEKB 侧 S1/S3 + 独立 repo + Android） | DSH Agent | `backend/app/{core,api,storage}`、`docs/RFC-端云协同与端侧Agent.md`、`docs/ops/16-端云协同协议.md`、`scripts/gitea_mirror.py` | 2026-09-17 16:40 |
| 资讯定时任务收尾（weekly cron 落到周一 + CLI 关闭期 MCP 泄漏） | DSH Agent（另一个会话） | `backend/app/core/config.py`、`backend/app/core/bootstrap.py`、`backend/config*.yaml`、`docs/tech/.facts/T{2,7}-*.md` | 2026-09-18 15:15 |

> ⚠️ 上面最后一行与 M2 行在 `backend/app/core/` **有文件重叠**（M2 行写的是 `{core,api,storage}`）。
> 我的改动是两处**追加式小改**：`config.py` 只改 `news.weekly_cron` 默认值（`0 8 * * 1`→`0 8 * * 0`），
> `bootstrap.py` 只在 `shutdown_app()` 末尾加一段内核 MCP 关闭——都在改完当次提交，不与 M2 的
> 端云路由改动争同一段代码。`scripts/gitea_mirror.py` 我只读不写（M2 正在改它）。

> 注：2026-09-15 的 JobCopilot P1–P7、招聘分析、科技资讯告警接线与限流读写分离
> 等轮次均已收工（详情见 `docs/CHANGELOG.md`）。

## 共享文件（改前先查上表）

- `backend/app/agents/job/market.py`、`backend/app/api/routes/job.py`
- `docker-compose*.yml`、`deploy/nginx.conf`、`deploy/deploy.sh`
- `docs/CHANGELOG.md` → **不要再直接改**，改用 `scripts/changelog_add.py` 写碎片

## 冲突记录（发生过就记一笔，避免重复踩）

| 时间 | 事件 | 影响 | 处理 |
|---|---|---|---|
| 2026-09-15 | 我（DSH Agent）在 CHANGELOG 顶部插入条目时，把下一个标题当成了替换目标 | 吃掉协作者 **3 个条目的标题**（正文成了孤儿段落） | 已修复；根因是「插入写成替换」，`changelog_merge.py` 已改成与条目标题无关的定位方式 |
| 2026-09-15 | 双方并发改 `market.py`（我 P4 重写、协作者加缓存改造） | **无损失**（协作者基于我的最新提交） | 侥幸；已加入认领表 |
| 2026-09-15 | Gitea 推送镜像 `PushRejected: cannot lock ref` | 镜像同步失败 | `gitea_mirror.py` 已加重试 |

