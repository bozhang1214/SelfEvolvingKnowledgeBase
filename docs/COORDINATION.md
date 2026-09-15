# 协作登记表（开工前认领，收工后删除）

> 约定见 [AGENTS.md](../AGENTS.md)。**开工前加一行，收工后删掉**——表格越短越有用。
> 目的：改共享文件前能看出「有没有人正在改同一个文件」。

## 进行中

| 工作流 | 负责人 | 涉及文件（重点） | 开始时间 |
|---|---|---|---|
| JobCopilot P1–P7 | DSH Agent（本会话） | `jobcopilot/**`、`backend/app/agents/job/**`、`deploy/**`、`docs/ops/**` | 2026-09-15 06:00 |

> 注：2026-09-15「招聘分析：搜索历史 / 报告隔离 / 历史报告清理」轮次已收工
> （与 P1–P7 重叠过 `market.py`、`job.py`，未发生冲突）。

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

