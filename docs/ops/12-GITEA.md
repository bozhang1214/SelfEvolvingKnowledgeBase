# 12 · Gitea 自建版本管理

> 依据 RFC `docs/tmp/自迭代闭环设计RFC.md` 的 **D-02 / D-08 / D-09**。
> 定位：**版本管理权威源**（自建 Gitea 为主，GitHub 降为归档镜像）。
> 最后更新：2026-09-14

---

## 1. 拓扑与定位

```
开发者(Mac) ──(Tailscale/SSH)──► Gitea :2222/:3000  ◄──(git pull)── 服务器部署
                                    │
                                    │ push mirror（单向，8h + on-commit）
                                    ▼
                                 GitHub（只读归档镜像）
```

| 项 | 值 |
|---|---|
| 容器 | `sekb-gitea`（`docker-compose.monitoring.yml` 的 `gitea` 服务）|
| 镜像 | `gitea/gitea:1.27.3`（**已锁版本**）|
| 数据库 | SQLite（`/data/gitea/gitea.db`）|
| 网络 | `sekb_gitea_net`（**独立网络，与应用/监控隔离**）|
| 数据卷 | `sekb_gitea_data` |
| Web/HTTP git | `3000` |
| SSH git | `2222` |
| 访问地址 | http://100.71.24.105:3000 （Tailscale）|
| 资源限制 | 512M / 0.5 CPU（实测占用 ~100M）|

> **公网不可达**：`ufw` 仅放行 22/80/443/41641，3000/2222 只经 **Tailscale** 访问（RFC「先 C 后 A」）。

---

## 2. 仓库清单

| 仓库 | 可见性 | 说明 |
|---|---|---|
| `bo/sekb` | private | SEKB 主仓库（GitHub 为镜像）|
| `bo/jobcopilot` | public | JobCopilot 主工程 |
| `bo/jobcopilot-prompts` | public | 提示词包（base + 职能 pack）|
| `bo/jobcopilot-dsh-plugin` | public | DSH 插件 |

---

## 3. 凭据管理（**红线：不得写入 git / remote URL**）

| 用途 | 存放位置 | 权限 |
|---|---|---|
| 服务器 git 拉取（Gitea 访问令牌）| `/home/bo/.git-credentials` | `600` |
| GitHub PAT（推送镜像用）| `/home/bo/.github-token` | `600` |
| 开发者 Mac 推送（Gitea SSH key）| `~/.ssh/id_ed25519` → Gitea「mac-zhangbo」| — |
| Gitea 管理员密码 | 由 owner 保管（CLI 创建，可改）| — |

**约定**：
- 任何 token/PAT **不进版本库、不写进 remote URL、不写进本文档**；
- 远端 URL 一律不带凭据（`http://localhost:3000/bo/sekb.git`）；
- 轮换：泄露或怀疑泄露时立即重新生成，并更新上面的文件。

---

## 4. 日常操作

### 4.1 开发者本地（Mac）

```bash
# 推送（remote 走 SSH，无需 token）
git push gitea main

# 查看 remote
git remote -v
# gitea   ssh://git@100.71.24.105:2222/bo/sekb.git
# origin  git@github.com:bozhang1214/SelfEvolvingKnowledgeBase.git
```

> 首次使用需把本机公钥加到 Gitea：Gitea → Settings → SSH/GPG Keys（或用 `POST /api/v1/user/keys`）。

### 4.2 服务器拉取（新发布链路，**取代原 bundle+scp**）

```bash
cd /home/bo/self-evolving-kb/SelfEvolvingKnowledgeBase
git pull            # main 已 track gitea/main
```

> 原「本地 `git bundle` → `scp` → 服务器 `fetch`+`merge`」流程**已废弃**（那是为绕过 GitHub 不稳而设）。

### 4.3 建新仓库

```bash
curl -u bo:<密码> -X POST http://localhost:3000/api/v1/user/repos \
  -H "Content-Type: application/json" \
  -d '{"name":"<repo>","private":true,"default_branch":"main"}'
```

---

## 5. GitHub 镜像（Gitea → GitHub 单向）

- 配置位置：Gitea 仓库 → Settings → Mirror Settings → **Push Mirror**（或用 API `POST /repos/{owner}/{repo}/push_mirrors`）。
- 策略：`interval: 8h` + `sync_on_commit: true`。
- 手动触发：`POST /api/v1/repos/bo/sekb/push_mirrors-sync`。

⚠️ **为什么单向**：双向同步必然冲突（两边都能改，谁赢？）。D-02 已定 Gitea 为主、GitHub 为归档镜像，单向吻合。

### 当前状态（2026-09-14）

| 项 | 状态 |
|---|---|
| 镜像配置 | ✅ 已配置 |
| 实际推送 | ❌ **被 GitHub 阻塞**：`You must verify your email address` |

**待 owner 处理**：去 https://github.com/settings/emails 验证邮箱。验证后镜像即自动生效（Gitea 侧已有 119+ 提交待推送）。

> 同一原因也导致 **GitHub 仓库创建 API 返回 403**（`At least one email address must be verified`），故 JobCopilot 三个仓库暂只建在 Gitea，GitHub 侧待验证后补建。

---

## 6. 备份与恢复

### 6.1 备份（已纳入每日 cron）

`scripts/backup_kb.sh` 现同时备份两个卷：

| 卷 | 产物 | 说明 |
|---|---|---|
| `sekb_data` | `sekb_data_<TS>.tar.gz` | SEKB 知识资产 |
| `sekb_gitea_data` | `sekb_gitea_data_<TS>.tar.gz` | **版本管理权威源（丢了就丢源码）** |

- 打包前短暂停容器（拿到一致快照），`trap EXIT` 兜底确保服务重启；
- 卷不存在时静默跳过（未部署 Gitea 的环境不受影响）；
- 保留 14 天（`RETENTION_DAYS` 可覆盖）。

### 6.2 恢复 Gitea

```bash
cd /opt/self-evolving-kb/SelfEvolvingKnowledgeBase
docker compose -f docker-compose.monitoring.yml stop gitea
docker run --rm -v sekb_gitea_data:/data -v /opt/self-evolving-kb/backups:/backup \
  alpine sh -c "rm -rf /data/* && tar xzf /backup/sekb_gitea_data_<TS>.tar.gz -C /data"
docker compose -f docker-compose.monitoring.yml start gitea
curl -fsS http://localhost:3000/api/healthz   # 校验
```

> ⚠️ 恢复演练**至少做过一次**才可信（RFC 风险项）。

---

## 7. 排障

| 症状 | 排查 |
|---|---|
| 502/无法访问 | `docker ps --filter name=sekb-gitea`；`docker logs sekb-gitea --tail 50` |
| `healthz` 报 database 失败 | 检查 `sekb_gitea_data` 卷挂载与 `gitea.db` 权限 |
| push 被拒（auth）| 确认 SSH key 在 Gitea 已登记；或检查 `.git-credentials` |
| 镜像失败 | 查 Push Mirror 的 `last_error`；常见为 GitHub 侧凭据/邮箱问题 |
| 磁盘增长 | 仓库对象增长；必要时 `gitea admin` 清理或调大磁盘 |

---

## 8. 与 RFC 的关系

| RFC 决策 | 本文档覆盖 | 状态 |
|---|---|---|
| D-02 Git 平台 | Gitea 自建 + GitHub 镜像 | ✅ 已实施（镜像待邮箱验证）|
| D-08 域名/入口 | 先 Tailscale（方案 C）| ✅ 已实施；公网子域名 `git.bos-studio.tech` 后置 |
| D-09 Gitea 部署 | monitoring 栈 + 数据卷 | ✅ 已实施 |
| D-10 流水线载体 | 自建脚本流水线 | ⏳ 未实施（P-1 仅做「发布链路」这一小步）|
| D-15 提案状态机落 Gitea Issue | — | ⏳ 未实施 |

**不在 P-1 范围**：staging 环境（M0-2）、自迭代 Agent、蓝绿/快照发布（D-12）、Gitea Actions、完整 CI/CD。
