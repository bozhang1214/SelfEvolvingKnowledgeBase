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
| Gitea API token（`scripts/gitea_mirror.py` 用）| `/home/bo/.gitea-token` 或环境变量 `GITEA_TOKEN` | `600` |
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

**首次接入一台新机器**（三步，缺前两步会直接失败）：

```bash
# 1) 本机公钥加到 Gitea：Gitea → Settings → SSH/GPG Keys（或用 POST /api/v1/user/keys）
#    验证：应回显 Hi there, bo! 之类欢迎语
ssh -p 2222 git@100.71.24.105

# 2) 信任 Gitea 的 SSH 主机公钥（否则报 Host key verification failed）
ssh-keyscan -p 2222 -t ed25519 100.71.24.105 >> ~/.ssh/known_hosts

# 3) 建议关掉主机密钥自动补全，否则每次推送会打印
#    "hostfile_replace_entries: mkstemp: Operation not permitted" 警告（无害但吵闹）
git config --local core.sshCommand "ssh -o UpdateHostKeys=no"
```

### 4.2 服务器拉取（新发布链路，**取代原 bundle+scp**）

```bash
cd /opt/self-evolving-kb/SelfEvolvingKnowledgeBase
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
- 运维工具：**`scripts/gitea_mirror.py`**（`status` / `sync` / `rebuild`），下面的坑都已处理。

```bash
python3 scripts/gitea_mirror.py status    # 查看四个仓库的镜像状态
python3 scripts/gitea_mirror.py sync      # 触发一次同步（正确端点）
python3 scripts/gitea_mirror.py rebuild   # token 轮换后用新 token 重建
```

### ⚠️ 两个实测踩过的坑

**坑 1：触发同步的端点不是 `mirror-sync`。**
`POST /repos/{o}/{r}/mirror-sync` 是给**拉取镜像**（pull mirror）用的，对推送镜像返回
`400 Repository is not a mirror`——**它 400 既不表示同步成功也不表示失败**。
推送镜像必须用 `POST /repos/{o}/{r}/push_mirrors-sync`。

> 曾因此误判：以为手动触发了同步，其实那次是 push 提交时 `sync_on_commit` 生效的。

**坑 2：Gitea 仓库名 ≠ GitHub 仓库名。**
Gitea 侧叫 `sekb`，GitHub 侧叫 `SelfEvolvingKnowledgeBase`。用 Gitea 名拼 GitHub URL
（`https://github.com/bozhang1214/sekb.git`）会**指向不存在的仓库，且不报错、静默失效**。
`scripts/gitea_mirror.py` 里用显式映射表，不靠同名巧合。

| Gitea | GitHub |
|---|---|
| `sekb` | `SelfEvolvingKnowledgeBase` |
| `jobcopilot` | `jobcopilot` |
| `jobcopilot-prompts` | `jobcopilot-prompts` |
| `jobcopilot-dsh-plugin` | `jobcopilot-dsh-plugin` |

### Token 轮换流程（GitHub PAT 换新时）

```bash
# 1) 更新落盘的 token（600）
umask 077; printf '%s' '<新 token>' > /home/bo/.github-token

# 2) 若 .git-credentials 里也有 github.com 行，一并更新（否则 git 仍用旧 token）
grep -n github.com /home/bo/.git-credentials

# 3) 用新 token 重建四个镜像（Gitea 不会自动感知 token 变更）
python3 scripts/gitea_mirror.py rebuild && python3 scripts/gitea_mirror.py sync
```

> **旧 token 一定要吊销并验证**：`curl -H "Authorization: token <旧>" https://api.github.com/user`
> 返回 `Bad credentials` 才算真失效。

⚠️ **为什么单向**：双向同步必然冲突（两边都能改，谁赢？）。D-02 已定 Gitea 为主、GitHub 为归档镜像，单向吻合。

### 当前状态（2026-09-14）

| 项 | 状态 |
|---|---|
| 镜像配置 | ✅ 四个仓库均已配置，地址正确 |
| 实际推送 | ✅ 正常（`sekb` / `jobcopilot` 与 Gitea 一致，两个空仓库待有内容后推送） |
| Token | ✅ 已轮换为新 PAT；旧 PAT 实测 `Bad credentials`（已失效） |

### ⚠️ 已知：GitHub 链路会间歇性不可用（非配置问题）

实测同一小时内：18:44 / 18:46 同步**成功**，18:50 失败并报
`Failed to connect to github.com:443 after 135273 ms`。
诊断特征：`api.github.com` 秒回（HTTP 200 / 0.4s）、`github.com:443` **TCP 可达**，
但 HTTPS **TLS 握手挂死**（`curl` 返回 `HTTP 000`、`time_connect=0`）——
典型的跨境链路抖动，**不是** token 或镜像配置问题。

**应对**：

- 失败**不用改配置**，重试即可（实测重试第一次就成功）。Gitea 会在
  `interval`（8h）与每次 push 时自动重试；
- 判断「是网络问题还是配置问题」：看 `last_error` 文案。
  网络类（`Failed to connect` / `Could not connect` / `SSL`）→ 重试；
  凭据类（`403` / `Authentication failed`）→ 走上面的 token 轮换流程；
- **不要**因为一次失败就去删改镜像配置——那才是真的会把地址搞坏
  （见坑 2：地址写错是静默失效，没有报错）。

> 这也正是 RFC D-02 把 Gitea 定为权威源、GitHub 降为**归档镜像**的原因：
> 归档允许最终一致，不要求强实时。


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
