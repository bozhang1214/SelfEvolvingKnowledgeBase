# 12 · Gitea 自建版本管理

> 依据 RFC `docs/RFC-自迭代闭环设计.md` 的 **D-02 / D-08 / D-09**。
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

> ⚠️ **本地 `main` 的跟踪分支指向 `origin/main`（GitHub 归档镜像），而非权威源 `gitea/main`**（实测 2026-09-14）。
> 后果有二：裸敲 `git push` / `git pull` 会直接操作 **GitHub**，绕过 Gitea 权威源，可能造成两侧分叉；
> 且 `origin/main` 长期不 fetch，`git status` 会显示「领先 N 个提交」这类**陈旧计数**，具有误导性。
> 建议改为跟踪权威源（一次性）：
>
> ```bash
> git branch -u gitea/main main   # 之后裸 git push/pull 即走 Gitea
> ```

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

> ⚠️ **服务器上残留一个误导性的 `github` remote**（实测 2026-09-14）：
>
> ```
> gitea   http://localhost:3000/bo/sekb.git                      ← 正确（main 已 track gitea/main）
> github  http://localhost:3000/bo/SelfEvolvingKnowledgeBase.git ← 错配
> ```
>
> 名为 `github` 却指向**本地 Gitea**，且拼的是 GitHub 侧仓库名 `SelfEvolvingKnowledgeBase`——
> Gitea 上该仓库**并不存在**（带 token 的认证 API 返回 `404`，真实仓库名是 `sekb`）。
> 它既推不到 GitHub，也推不进 Gitea，属**残留错配**：Gitea→GitHub 的归档由 Push Mirror 负责，
> 服务器**不需要** `github` remote（且 `ENABLE_PUSH_CREATE_USER` 未开启，推错地址会直接失败而非静默建库）。
> 建议清理：`git remote remove github`（服务器只保留 `gitea`）。

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

> 🔴 **核对 GitHub 时不要用 `git ls-remote https://github.com/...`**（2026-09-18 踩到）：
> 本机全局 git 配置里有
> `url.ssh://git@100.71.24.105:2222/bo/.insteadOf = https://github.com/bozhang1214/`，
> 会把这类 URL **静默重写成 Gitea**——于是"核对 GitHub"实际核的是 Gitea，永远"通过"。
> 正确做法（二选一）：
> ```bash
> # A. 走 GitHub API（推荐，公开仓库无需鉴权）
> curl -s https://api.github.com/repos/bozhang1214/<repo>/commits/main | grep -m1 '"sha"'
> # B. 临时屏蔽 insteadOf
> git -c url.ssh://git@100.71.24.105:2222/bo/.insteadOf= ls-remote https://github.com/bozhang1214/<repo>.git
> ```

> ⚠️ **`sync_on_commit` 不保证"立刻"**（2026-09-18 实测）：一次推送后 3 分钟内 GitHub
> 仍是旧提交（`push_mirrors.last_update` 停在推送**之前**的时间且 `last_error` 为空），
> 显式 `POST /repos/bo/<repo>/push_mirrors-sync` 之后才同步到最新。
> **要立刻可用就显式触发**，不要假设提交即同步。

> ⚠️ **`last_update` 不能用来判断"提交即同步"是否生效**（2026-09-18 实测）：
> `sync_on_commit` 触发的自动推送**不会**刷新 `push_mirrors` 的 `last_update`
> （它停在最后一次手动同步的时间），让人误以为没同步。真正的判据是去 GitHub 侧核对：
> ```bash
> git ls-remote https://github.com/bozhang1214/sekb-ondevice-agent.git
> # 与本地 git rev-parse HEAD 比对——这才是"镜像真的推上去了"的证据
> ```
> 同理，`last_error` 为空也只说明"上次同步没报错"，不等于"这次已经同步"。
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

### 当前状态（2026-09-18 更新）

| 项 | 状态 |
|---|---|
| 镜像配置 | ✅ **四个**仓库均已配置，地址正确。（2026-09-18 曾新增 `sekb-ondevice-agent`；同日又删除——端侧代码并入主仓 `apps/android/`，独立仓不再需要，见 RFC §17） |
| 实际推送 | ✅ 正常（`sekb` / `jobcopilot` 与 Gitea 一致，两个空仓库待有内容后推送） |
| Token | ✅ 已轮换为新 PAT；旧 PAT 实测 `Bad credentials`（已失效） |
| **全链路自动镜像** | ✅ **已实测闭环**（2026-09-14 19:27）：本地 `push gitea main` → Gitea `sync_on_commit` **自动触发** → GitHub。三点 SHA 一致（`dee8b47`），`sekb` 的 `last_update` 由 18:55（手动）自动推进到 19:27，**无需人工 `sync`** |

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

### ⚠️ 已知缺口：镜像「静默停摆」没有任何告警

镜像失败**只写进 Gitea 的 `last_error` 字段**，没有任何主动通知：

- 没有 Prometheus 指标、没有 Alertmanager 规则、没有巡检任务；
- `gitea_mirror.py status` 是**人工**调用才有结论（它设计为可用退出码判定，但当前无人定时执行）；
- 最危险的失效是 **GitHub PAT 过期**：PAT 有有效期，过期后镜像从某天起**永久失败**，
  而 Gitea 界面不主动提示，等你某天去 GitHub 看才发现归档停在几周前。

**建议**（对齐 RFC 的「定期巡检 + 关键环节告警」）：把 `status` 的退出码接入现有巡检/告警链路，
或对 `last_update` 的**陈旧度**做判定——例如超过 `2×interval`（16h）未成功即告警。

```bash
# 可直接用于定时任务的判据（非 0 即需要关注）
python3 scripts/gitea_mirror.py status
```

> 现状小结：**Push Mirror 本身工作正常**（四仓库最近一次同步均成功、`last_error` 为空），
> 缺的是「失败时有人知道」。这属于**可观测性缺口，不是功能缺陷**。

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

> ⚠️ **路径漂移风险**：cron 执行的是 `/opt/self-evolving-kb/backup_kb.sh`（仓库**之外**的一份副本），
> 而版本库里的脚本在 `/opt/self-evolving-kb/SelfEvolvingKnowledgeBase/scripts/backup_kb.sh`——
> **两者是两个文件**，且仓库内**没有任何**把它们同步的机制（`deploy.sh` 只是 `./scripts/backup_kb.sh` 地调用）。
> 实测 2026-09-14 两者内容仍**逐字节一致**，但这是**手工维护的巧合**：
> 改仓库脚本不会影响 cron 实际执行的那份。
> 建议二选一：cron 直接指向仓库内路径（单一事实源），或在部署脚本里显式 `install -m 755` 同步过去。

### 6.2 恢复（统一走 `scripts/restore_kb.sh`）

恢复脚本已与备份脚本**对称**：一个脚本覆盖两个卷，不再是「备份自动、恢复手工」。

```bash
cd /opt/self-evolving-kb/SelfEvolvingKnowledgeBase

# 恢复两个卷（推荐：--both 自动配对同时间戳的另一半）
bash scripts/restore_kb.sh --both /opt/self-evolving-kb/backups/sekb_data_<TS>.tar.gz

# 只恢复其一
bash scripts/restore_kb.sh --sekb  /opt/self-evolving-kb/backups/sekb_data_<TS>.tar.gz
bash scripts/restore_kb.sh --gitea /opt/self-evolving-kb/backups/sekb_gitea_data_<TS>.tar.gz

# 也可不传卷参数：按文件名自动识别
bash scripts/restore_kb.sh /opt/self-evolving-kb/backups/sekb_gitea_data_<TS>.tar.gz
```

脚本内置的保障（每一条都对应一个真实踩坑）：

| 保障 | 说明 |
|---|---|
| 成对预检 | `--both` 先确认两个文件都存在且可解包，**否则直接退出**——不会恢复一半 |
| 完整性校验 | 解包前 `tar tzf` 验证归档可读，避免清空卷后才发现备份已损坏（审查项 OPS-12） |
| 目标卷存在检查 | 卷不存在即失败。否则 `docker run -v` 会**静默创建空卷**，把失败伪装成「恢复成功」 |
| 服务兜底 | `trap EXIT` 只重启**本次真正停掉**的服务（backend / gitea 分别跟踪；旧版只认 backend） |
| 清卷彻底 | `rm -rf /data/* /data/.[!.]* /data/..?*`，含隐藏文件（旧手工命令漏掉隐藏文件） |

### 6.3 恢复演练（`--drill`：**不碰线上数据**）

演练是 RFC 要求的例行项。脚本提供**旁路卷演练模式**：只往旁路卷回灌，**不停止/重启任何线上服务**；
并带安全联锁——`--drill` 一旦发现目标是生产卷（`sekb_data` / `sekb_gitea_data`）就**直接拒绝退出**，
避免在服务运行的不一致状态下误覆盖真实数据。

```bash
cd /opt/self-evolving-kb/SelfEvolvingKnowledgeBase
docker volume create sekb_drill_data && docker volume create sekb_drill_gitea

RESTORE_SEKB_VOLUME=sekb_drill_data RESTORE_GITEA_VOLUME=sekb_drill_gitea \
  bash scripts/restore_kb.sh --drill --both \
  /opt/self-evolving-kb/backups/sekb_data_<TS>.tar.gz

# 校验：起临时 Gitea 指向旁路卷（仅绑 127.0.0.1:3300，不撞生产的 3000）
docker run -d --name sekb-gitea-drill -p 127.0.0.1:3300:3000 \
  -v sekb_drill_gitea:/data -e USER_UID=1000 -e USER_GID=1000 \
  -e GITEA__database__DB_TYPE=sqlite3 -e GITEA__database__PATH=/data/gitea/gitea.db \
  -e GITEA__server__ROOT_URL=http://localhost:3300/ -e GITEA__security__INSTALL_LOCK=true \
  gitea/gitea:1.27.3
sleep 10 && curl -fsS http://127.0.0.1:3300/api/healthz
curl -s -H "Authorization: token $(cat ~/.gitea-token)" \
  "http://127.0.0.1:3300/api/v1/user/repos?limit=50" | python3 -m json.tool | head

# 清理（务必删干净，别把旁路卷留成垃圾）
docker rm -f sekb-gitea-drill
docker volume rm sekb_drill_data sekb_drill_gitea
```

#### 演练记录

| 日期 | 备份时间戳 | 方式 | 结果 |
|---|---|---|---|
| 2026-09-14 | `20260914_182934` | `--drill --both` → 旁路卷 + 临时 Gitea | ✅ **通过**（首次演练） |

2026-09-14 首次双卷演练的实测结论：

- `gitea.db` 2.77 MB、文件头为 `SQLite format 3`；`bo/` 下 4 个仓库目录俱在
  （`sekb` 2.9M、`jobcopilot` 632K、两个空仓库各 156K）；
- 旁路卷起临时 Gitea → `healthz` OK（第 2 次探测）；用**生产 token** 查到 **4 个仓库**且
  `private` 标记正确 ⇒ **凭据与仓库元数据随备份完整恢复**；
- Push Mirror 配置（`remote_address` + `interval: 8h0m0s`）同样在 ⇒ 恢复后镜像可继续工作；
- 从旁路实例 `git ls-remote` 成功（`refs/heads/main` → `6f44ad0`，即 18:29 快照时点）⇒ **git 对象完整**；
- `sekb_data` 旁路卷：`chroma_db` 93.6M、`conversations`、`uploads`、`news`、`profile`、`index.json`
  等 23 个顶层条目齐全；
- **演练全程生产容器 uptime 未变**（backend / gitea 始终 healthy），且旁路卷与生产 `gitea.db`
  md5 不同 ⇒ 确实没碰线上。

> 恢复**会覆盖**目标卷，执行前请确认卷内没有未备份的新数据。
> 极端情况下若仓库目录不可用（无法执行脚本），可退回手工步骤：
>
> ```bash
> cd /opt/self-evolving-kb/SelfEvolvingKnowledgeBase
> docker compose -f docker-compose.monitoring.yml stop gitea
> docker run --rm -v sekb_gitea_data:/data -v /opt/self-evolving-kb/backups:/backup \
>   alpine sh -c "rm -rf /data/* /data/.[!.]* /data/..?* 2>/dev/null; tar xzf /backup/sekb_gitea_data_<TS>.tar.gz -C /data"
> docker compose -f docker-compose.monitoring.yml start gitea
> curl -fsS http://localhost:3000/api/healthz   # 校验
> ```

---

## 7. 排障

| 症状 | 排查 |
|---|---|
| 502/无法访问 | `docker ps --filter name=sekb-gitea`；`docker logs sekb-gitea --tail 50` |
| `healthz` 报 database 失败 | 检查 `sekb_gitea_data` 卷挂载与 `gitea.db` 权限 |
| push 被拒（auth）| 确认 SSH key 在 Gitea 已登记；或检查 `.git-credentials` |
| 镜像失败 | 查 Push Mirror 的 `last_error`；网络类（`Failed to connect`）重试即可，凭据类（`403`/`Authentication failed`）走 token 轮换 |
| 镜像**长期没更新但没人报错** | 典型的**静默停摆**：多半是 GitHub PAT 过期。跑 `python3 scripts/gitea_mirror.py status`，看 `last_update` 是否陈旧（>16h）与 `last_error`；重轮换 token 后 `rebuild` + `sync` |
| 推送报 404 / 推到奇怪地址 | 检查是否推了残留的 `github` remote（指向不存在的 Gitea 仓库 `bo/SelfEvolvingKnowledgeBase`）；用 `git remote -v` 核对，只保留 `gitea` |
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
