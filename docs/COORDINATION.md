# 协作登记表（开工前认领，收工后删除）

> 约定见 [AGENTS.md](../AGENTS.md)。**开工前加一行，收工后删掉**——表格越短越有用。
> 目的：改共享文件前能看出「有没有人正在改同一个文件」。

## 进行中

| 工作流 | 负责人 | 涉及文件（重点） | 开始时间 |
|---|---|---|---|
| 多端跨端设计（KMP / CMP 评估 / 桌面审计）+ T1 事实表生成物 | DSH Agent | `docs/多端跨端-*.md`、`docs/RFC-多端跨端方案.md`、`apps/*/README.md`、`docs/tech/.facts/T1-routes.md`、`scripts/gen_facts_routes.py` | 2026-09-21 16:20 |

> 端云协同 M0–M3（`docs/RFC-端云协同与端侧Agent.md` / 实施记录）已于 2026-09-20 暂停存档，
> 真机到货后从实施记录 §0 的恢复指引继续。

> 注：2026-09-15 的 JobCopilot P1–P7、招聘分析、科技资讯告警接线与限流读写分离
> 等轮次均已收工（详情见 `docs/CHANGELOG.md`）。
>
> **2026-09-18 镜像排查结论（供镜像负责人参考，本轮未改 `gitea_mirror.py`）**：
> Gitea 自己的日志给出自动同步失效的根因——每次推送触发入队时都报
> `services/mirror/queue.go:66 [E] Unable to push sync request ... already in queue`
> （72 小时 88 次，含 15:12:57、15:19:18 两次实测推送）。即队列里有一条**卡死的条目**，
> 自动路径（`sync_on_commit` 与 8h 定时）**都不入队**，所以 GitHub 静默落后；
> 而手动 `push_mirrors-sync`（`gitea_mirror.py sync`）走的是另一条路径，**有效**
> （实测 GitHub 从 42cdcab → d0f14c3）。重启 Gitea 清不掉（队列是持久化的，
> `/data/gitea/queues/common`）。核对时不要用 `git ls-remote https://github.com/...`：
> 本机 `insteadOf` 会把它重写成 Gitea（SSH 形式 `git@github.com:...` 不受影响）。

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
| 2026-09-18 | 我用 `pkill -f "bash deploy/deploy.sh"` 清理一次误启动的部署，**该模式把执行命令的 ssh shell 自己也匹配杀了**；部署进程在「停服务取快照」阶段被杀 | **生产 backend 被留在 Exited 状态**（约 6 分钟不可用），并残留 `/tmp/sekb-deploy.lock` 使后续部署直接「已中止」 | 已用 `docker start sekb-backend` 恢复（health 200）；确认无持有者后 `rm -f /tmp/sekb-deploy.lock` 重新部署成功。教训：**不要用 pkill/pgrep 的 `-f` 模式去杀与自己命令行同名的进程**（改用显式 PID）；部署被中断后务必检查 ① 容器是否停在半成品 ② `/tmp/sekb-deploy.lock` 是否残留 |

