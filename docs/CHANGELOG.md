# SEKB 变更日志（CHANGELOG）

> 记录所有功能迭代与问题修复。按时间倒序，最新在前。
> 维护约定：**每次功能开发或问题修复完成后，必须同步在本文件追加一条记录**，并更新文档头部「最后更新」日期。
> 最后更新：2026-09-15

---

## 2026-09-15（JobCopilot P3：提示词分发端点 + 三级回退）

内核查升到 `a24c7af`。SEKB 侧新增 nginx `/prompts/` 静态分发端点。

**端点**：`https://bos-studio.tech/prompts/manifest.json` 可访问（证书正常）；
`autoindex off`（不列目录）、`access_log off`（少一份可用于画像的访问记录）、
一律按 `text/plain` 返回。内容由 `deploy.sh` 用**一次性容器**跑
`jobcopilot publish` 生成到 `prompts-dist/`，宿主机无需装 Python 依赖。

**消费端三级回退**：自建主源 → jsDelivr CDN / GitHub raw → 包内兜底。
实测国内到 `raw.githubusercontent.com` **间歇性读写超时**（同一小时内既有 0.59s 成功、
也有读超时），故把 jsDelivr 排在 raw 之前；代价是 jsDelivr 对 `@main` 有缓存
（可能滞后数小时），但 manifest 与文件来自同一份缓存，内部始终自洽。

**完整性校验**：manifest 带每个文件的 sha256，不一致即**中止同步**——
提示词会被注入 LLM，被篡改的后果比下载失败严重得多。

**发布闸门**：`jobcopilot publish` 会**拒绝**任何破坏 base JSON 输出骨架的 pack。
成因值得记：章节块的边界是「到下一个标题为止」，pack 若覆盖**最后一个标题**，
它后面的非标题内容（很可能正是 JSON 输出骨架）会被整块替换——组合结果看着正常，
实际已丢掉输出契约。发布是所有消费方上游，在这里拦住代价最小。
`version` 由内容决定，发布幂等。

**修掉的问题**
1. **中文文件名未做 URL 编码**：真实 HTTPS 下 urllib 抛
   `UnicodeEncodeError: 'ascii' codec can't encode`。`file://` 不走那条路径，
   本地单测**发现不了**——已补专门断言。
2. **发布脚本容器权限**：镜像默认以 appuser(uid 1000) 运行，挂载出的目录属部署用户
   → `PermissionError`。改为 `--user "$(id -u):$(id -g)"`。
3. macOS 常缺 CA 导致 `CERTIFICATE_VERIFY_FAILED` → 优先用 certifi 的 CA 包。
4. 重构时把「本地目录留 manifest」写丢了 → 补回（记录来源与版本，排障用）。

**验证**：jobcopilot 206 passed / ruff 全绿 / mypy strict 32 文件零错误；
SEKB 721 passed 无回归。DoD 三项：端点可访问 ✅、主源挂→备源 ✅、全挂→包内 ✅。

---

## 2026-09-15（JobCopilot P2：MCP Server + CLI run）

内核查升到 `3b441c3`（子模块指针同步）。**SEKB 侧未接入 MCP（P4 才切），行为完全不变。**

**MCP 工具集（6 个）**：`analyze_job` / `analyze_jobs_batch` / `get_profile` /
`save_profile` / `list_prompt_packs` / `sync_prompts`。双传输：
`jobcopilot-mcp`（stdio，给 DSH / Claude Desktop / Cursor）与
`jobcopilot-mcp --http`（streamable HTTP，给云端平台）。

**本轮最重要的发现：API 边界不能静默失败。** 内核「每步独立降级」在引擎里是对的，
但在 MCP 边界上，LLM 全挂时会返回 **7 个空段落**——调用方看起来像「调用成功但没内容」，
完全看不出是 Key 失效 / 余额不足 / 网络不通。已改为：全失败 → 结构化错误（含排查方向）；
部分失败 → 结果照常返回但带 `warnings` 标出不可信段落。这正是 P4 DoD 3 要防的模式。

**安全默认**：HTTP 形态下默认**禁用 `source_path`**。该参数让服务端读本地文件
（88 个职位当参数传会烧大量 token），但 HTTP 面向「别人的服务器 + 多用户」，
开放任意路径读取等于暴露宿主机文件系统。确需启用要显式开，并可用
`JOBCOPILOT_SOURCE_ROOT` 限定目录。

**报错可排障**：实测 OpenAI 不可达时错误信息是「OpenAI 调用失败: 」（冒号后空白，
因 httpx 网络异常的 `str()` 为空），完全无从下手。已带上异常类型与兜底说明。

**验证**：jobcopilot **177 passed**（+34）/ ruff 全绿 / mypy strict 30 文件零错误。
- **协议级**：官方 MCP 客户端真实拉起子进程，走完 initialize → list_tools（6 个工具）→
  call_tool；HTTP 形态同样端到端覆盖，并断言 `source_path` 被拒。
- **DoD 3 真 LLM 超时验证**（生产容器内、stdio 全链路）：50 职位 **12.5s** 返回
  （限值 180s，余量 14×），market 5 字段 / knowledge 6 字段全非空。
- **DoD 4 多 provider**：DeepSeek 真实调用成功；千问 / Kimi / 豆包 / 智谱**端点正确且返回干净 401**；
  OpenAI 国内网络不可达（ConnectTimeout，非代码问题）。

---

## 2026-09-14（运维：备份/恢复对称化 + 首次恢复演练通过 —— G1）

审查发现的 **G1（备份/恢复不对称）** 是本轮唯一评为「高」的问题，已修复并**实测验证**。

**问题**：`backup_kb.sh` 早已统一为「一个脚本备份双卷」，而 `restore_kb.sh`（53 行）按设计
**只恢复 `sekb_data`**（`VOLUME_NAME="sekb_data"`），**完全不认 `sekb_gitea_data_*.tar.gz`**——
Gitea 的恢复只有一段手工命令、**且从未演练**。备份已覆盖的权威源码，实际处于
「有备份、无法证明确实能恢复」的状态。

**改造 `scripts/restore_kb.sh`**（与备份脚本对称）：

- 支持**双卷**：`--sekb` / `--gitea` / `--both`，也可按文件名**自动识别**卷类型；
- `--both` **先做成对预检**（两个文件都存在且可解包）再动手，杜绝「恢复一半」的半成品状态；
- 解包前 `tar tzf` **完整性校验**，避免清空卷后才发现备份损坏（顺带落地 OPS-12 的诉求）；
- **目标卷必须已存在**才恢复——否则 `docker run -v` 会静默创建空卷，把失败伪装成「恢复成功」；
- `trap EXIT` 改为**只兜底重启本次真正停掉的服务**（旧版硬编码只认 backend）；
- 清卷补 `.[!.]*` / `/data/..?*`，不再漏隐藏文件；
- 新增 **`--drill` 旁路卷演练模式**：只往旁路卷回灌，**不停止/重启任何线上服务**，
  并带**安全联锁**——`--drill` 一旦发现目标是生产卷即拒绝退出。这让恢复演练可反复进行。

**首次双卷恢复演练（2026-09-14，备份时间戳 `20260914_182934`）——✅ 通过**：

- 旁路卷回灌 + 起临时 Gitea（仅绑 `127.0.0.1:3300`）→ `healthz` OK；
- 用**生产 token** 查到 **4 个仓库**、`private` 标记正确 ⇒ 凭据与仓库元数据完整恢复；
- Push Mirror 配置（`remote_address` + `interval: 8h0m0s`）同在 ⇒ 恢复后镜像可继续工作；
- 从旁路实例 `git ls-remote` 成功（`main` → `6f44ad0`，即 18:29 快照时点）⇒ git 对象完整；
- `sekb_data` 旁路卷 23 个顶层条目齐全（`chroma_db` 93.6M / `conversations` / `uploads` / `news` …）；
- **演练全程生产容器 uptime 未变**（backend / gitea 始终 healthy），旁路与生产 `gitea.db` md5 不同
  ⇒ 确实未触碰线上；演练资源（临时容器 + 两个旁路卷）已全部清理，无残留。

**文档**：`12-GITEA.md` §6.2 重写为统一恢复流程 + §6.3 新增演练手册与**演练记录表**；
`13-DISK-MEMORY.md` 关联段同步；`BACKLOG.md` G1 标记已解决。

> 其余发现（G2 镜像无告警 / G3 跟踪分支 / G4 cron 脚本漂移 / G5 端口绑定 / G6 错配 remote）
> 按用户选择**暂不动服务器**，保留在 BACKLOG 待排期。

---

## 2026-09-14（运维：Gitea 链路实现审查 + 文档纠偏）

对用户已实现的自建 Gitea 链路做代码/服务器实测审查。**结论：功能主体正常**——四仓库
Push Mirror 地址正确且最近同步全部成功、`sekb` 为 private、`backup_kb.sh` 已覆盖双卷、
公网端口实测不可达。以下为发现的**缺口与错配**（已登记 `BACKLOG.md` G1–G6）：

- **G1（高）备份/恢复不对称**：`restore_kb.sh`（53 行）按设计**只恢复 `sekb_data`**
  （`VOLUME_NAME="sekb_data"`），**不认 `sekb_gitea_data_*.tar.gz`**；Gitea 恢复只有
  `12-GITEA.md` §6.2 的手工步骤，且**无演练记录**。备份已统一为单脚本双卷，恢复却割裂
  ⇒ 灾难恢复易「恢复了知识库、丢了源码仓库」。
- **G2 镜像静默停摆无告警**：失败仅写 Gitea `last_error`，无指标/告警规则/定时巡检；
  最危险是 **GitHub PAT 过期**后永久失败而无人知。`gitea_mirror.py status` 本可用退出码判定，
  但无人定时执行。
  （**注**：审查中顺带**实测闭环成功**——本次 `push gitea` 后 Gitea 经 `sync_on_commit`
  **自动**镜像到 GitHub，三点 SHA 一致（`dee8b47`），`last_update` 由 18:55 自动推进到 19:27，
  无需人工 `sync`。故 G2 是**纯可观测性缺口**，镜像机制本身工作正常。）
- **G3 本地跟踪分支错**：Mac 上 `main` 跟踪 `origin/main`（**GitHub 归档镜像**）而非权威源
  `gitea/main` ⇒ 裸敲 `git push`/`pull` 会打到 GitHub；`git status` 的「领先 139」为
  `origin/main` 长期不 fetch 的**陈旧计数**（已在文档注明并给出 `git branch -u gitea/main`）。
  （本地 remotes 本身与 §4.1 文档**完全一致**，无误。）
- **G4 cron 脚本漂移**：cron 执行 `/opt/self-evolving-kb/backup_kb.sh`，仓库版在
  `.../SelfEvolvingKnowledgeBase/scripts/backup_kb.sh`——**是两个文件**且仓库内无任何同步逻辑
  （实测逐字节一致属手工巧合，`deploy.sh` 只做相对调用）。
- **G5 端口绑 `0.0.0.0` + 部分仓库 public**：当前公网不可达**依赖 `EnableUserlandProxy: true`**
  （使流量走 INPUT 链被 ufw `DROP`）；若改 `--userland-proxy=false` 则转 FORWARD 链被 Docker
  直插 `ACCEPT` **绕过 ufw** 即暴露。另 `jobcopilot*` 三仓为 public（`sekb` 为 private ✅）。
- **G6 服务器残留错配 remote `github`**：指向 `http://localhost:3000/bo/SelfEvolvingKnowledgeBase.git`，
  名为 `github` 实则指向本地 Gitea，且该仓库**不存在**（带 token 的认证 API 返回 `404`，
  真实名是 `sekb`）——正是文档「坑 2」描述的静默失效模式。

**文档调整**：

- **修正 ops 编号冲突**：`11-CHANGE-RELEASE-POLICY.md` 与 `11-MONITORING.md` **同时占用 11**
  （`00-README.md` 索引里两行都写 `| 11 |`）。已将较新建的变更发布规约移至
  **`14-CHANGE-RELEASE-POLICY.md`**（保留已建立的 `11-MONITORING`，只改 2 处引用，避免牵连
  `13-DISK-MEMORY.md`）。
- `12-GITEA.md`：补 §4.1 跟踪分支告警、§4.2 服务器 `github` remote 错配说明、§5「镜像静默
  停摆无告警」缺口、§6.1 cron 脚本漂移风险、§6.2 恢复清卷补 `.[!.]*`＋恢复不对称警告＋
  「尚无演练记录」、§7 排障表补「静默停摆」「推到错误 remote」两行。
- `13-DISK-MEMORY.md`：关联段标注备份脚本**双文件无同步**、恢复脚本**不含 Gitea 卷**。

> 审查方法：Gitea 认证 API（`/user/repos`、`/push_mirrors`）＋ `docker logs` ＋ 卷/权限实测；
> 公网可达性用 Mac 侧 `curl` 直连 `49.232.42.91:3000/2222/8000` 验证（均 `HTTP 000`）。

---

## 2026-09-15（JobCopilot P1：提示词职能分层 + Eval 骨架）

内核查升到 `340144a`（子模块指针同步更新）。**SEKB 侧行为不变**——SEKB 仍用自己的
`prompt/job`（本地目录优先级最高），且包内 base 提示词一字未改。

**提示词 pack（只写差异章节）**
- 新增章节级合并：pack 里写到的标题覆盖 base 同名标题，其余原样继承，
  **JSON 输出骨架永远来自 base**（防 schema 漂移）；匹配按标题序号，pack 可自由改写文案；
- 内置三个职能 pack：`presales`（客户/云厂商赛道 + 年包口径）、`product`（产品线赛道 +
  端云协同/评测体系 + 车载标注）、`engineering`（技术职能 + 框架源码深度）；
- `jobcopilot pull` 把**合并后的完整提示词**拉到本地目录（可直接改、立即生效）。

**Eval 骨架（三层）**
- L1 程序化断言（零 LLM）：结构 / job_count / **stats 精确比对** / 段落非空 /
  **输出与提示词声明的 JSON 骨架一致**；
- L2 基线回归（零 LLM）：**提示词指纹** + 指标不得低于基线；
- L3 LLM-as-Judge：覆盖度 / 如实性 / 可执行性 / 赛道 / 薪资依据；
- 黄金数据集：3 组批量（研发 8 / 售前 6 / 产品 6）+ 3 条单职位用例；
- **守门员方案 B**：CI 只跑 L1+L2（无需 Key、零成本）。L2 的提示词指纹把
  「改了提示词就必须重新基线化」变成零成本强制。

**CLI**：`jobcopilot pull / pack / eval / doctor`。

**可追溯性**：批量报告新增 `prompt_meta`（pack / 指纹 / 覆盖章节），
可从缓存或存档报告反查提示词版本。

**修掉两个真 bug**
1. **伪 JSON 骨架导致断言假绿**：提示词骨架用裸词占位（`"job_count": 招聘量`），
   严格 `json.loads` 必然失败 → 两条最重要的批量提示词**静默跳过校验**；
2. **`to_dict` 返回内部 dict 引用**：调用方一改就污染报告本身，使差异比对静默失效。

**验证**：jobcopilot 143 passed / ruff 全绿 / mypy strict 零错误（26 文件）；
SEKB 721 passed / ruff 全绿 / mypy 门禁 300 ≤ 310；**P0 逐字段回归比对仍全等**。

---

## 2026-09-14（运维：GitHub token 轮换 + 镜像同步跑通）

- **Token 轮换**：旧 PAT 实测已失效（`curl /user` 返回 `Bad credentials` ✅）；
  新 PAT 写入 `/home/bo/.github-token`（600）。
  **顺带发现旧 token 还残留在 `/home/bo/.git-credentials` 的 github.com 行**——已一并更新
  （只换这一行，Gitea 凭据不动）。
- **四个推送镜像全部重建并同步成功**：`sekb` / `jobcopilot` 与 Gitea 逐字节一致。
- **新增 `scripts/gitea_mirror.py`**（`status` / `sync` / `rebuild`），把本轮两个坑固化下来：
  1. **触发同步的端点不是 `mirror-sync`** —— 那是给拉取镜像用的，对推送镜像返回
     `400 Repository is not a mirror`（**既不代表成功也不代表失败**）。正确的是
     `push_mirrors-sync`。上一轮我据此误判过「已触发同步」，实际那次是 push 提交时
     `sync_on_commit` 生效的；
  2. **Gitea 仓库名 ≠ GitHub 仓库名** —— Gitea 侧 `sekb`、GitHub 侧 `SelfEvolvingKnowledgeBase`。
     我按 Gitea 名拼出了 `https://github.com/bozhang1214/sekb.git`（**不存在的仓库，且不报错**），
     差点让 SEKB 镜像静默失效；现已改成显式映射表。
- `12-GITEA.md` 补：仓库名映射表、token 轮换流程（含「旧 token 必须验证 Bad credentials」）、
  `scripts/gitea_mirror.py` 用法、凭据位置补 `.gitea-token`。

---

## 2026-09-14（工程：内核子模块状态在四个时机全部显性化）

**要解决的问题**：子模块最大的风险是**静默过期**——`git pull` 完 SEKB 子模块纹丝不动、
改完内核忘了回 SEKB 提交指针、新机器不知道还要拉子模块，**全都不报错**。

- **0）`.gitmodules` 改用公开 GitHub 地址**（原为 Tailscale 私网 `ssh://git@100.71.24.105:2222/...`）。
  这意味着**任何人从 GitHub clone SEKB 都拉不到子模块**——比「不知道是不是最新」更严重。
  本机/服务器用**全局 URL 重写**走自托管 Gitea，不动 `.gitmodules`（避免弄脏跟踪文件）。
- **1）首次接触**：新增 `scripts/check_kernel.sh`，一条命令看清
  是否初始化 / commit 是否与 SEKB 钉住的一致 / 工作区是否干净 / 提示词是否完整 /
  能否被 Python 导入，异常时给出**可直接复制**的修复命令。`--strict` 让警告也致命，`--quiet` 供 CI。
- **2）部署前硬闸门**：`deploy.sh` 从「目录存在」升级为 `check_kernel.sh --strict`——
  未初始化 / commit 不符 / 工作区脏 → **直接中止**，不再「静默部署非钉住版本的内核」；
  紧急绕过 `SKIP_KERNEL_CHECK=1`。`pre-deploy-check.sh` 接入同一脚本，标准一致。
- **3）运行时可见**：内核 commit 在构建期烧进镜像（build arg → ENV），
  容器启动日志打印，并在 `GET /api/v1/health/` 新增 `kernel` 字段
  （`version` / `commit` / `prompts` / `prompt_source` / `prompt_dir` / `healthy`）。
  线上一条命令即可核对：`git submodule status` 的 commit 与 `/health` 的 `kernel.commit` 一致 ⇒ 跑的就是钉住的那份。
- **4）CI**：给需要后端的 5 个 job 补 `submodules: true`
  （**此前我引入子模块后 CI 必挂**），`lint` job 前置 `check_kernel.sh --strict` 闸门。

**踩到的两个真坑**（都写进了代码注释）：
1. BSD（macOS）的 `tr -d '+-U'` 会把 `+-U` 当成 **ASCII 区间**（`+`=43 到 `U`=85，**含全部数字**），
   结果把 commit SHA 里的数字全删光（`a4b1885f...` → `abfcdfacdebaac`）→ 改用 `sed` 精确去首字符；
2. `pre-deploy-check.sh` 是 `set -euo pipefail`，写成 `VAR="$(失败命令)"` 再判 `$?` 会让脚本
   **直接退出**——门禁失败反而变成静默中断整个预检 → 必须写成 `if VAR="$(cmd)"; then`。

**顺带发现（未修，另案）**：GitHub Actions **从 2026-08-20 起 0/30 全部失败，且每次运行
job 数都是 0**（工作流 active、YAML 合法、Actions 已启用），与本次改动无关，是又一个
「一直红着但没人追」的问题。

- 验证：SEKB 715 → **721 passed**；ruff 全绿；mypy 300 ≤ 基线 310；
  `deploy.sh` 完整跑通（EXIT=0，含新的内核硬闸门与部署前备份）；
  线上 `/health` 报 `commit=a4b1885...`，与 `git submodule status` 钉住的完全一致。

---

## 2026-09-14（修复：部署前数据备份从未真正执行 + 预检永久失败）

第一次跑完整 `deploy.sh` 时暴露两个「一直红着但没人追」的问题：

- **部署前数据备份从未真正执行**（日志只写「数据备份失败（非致命）」）：
  1. `deploy.sh` 把备份日志重定向到 `/var/log/sekb-deploy-backup.log`，而部署用户 `bo`
     对 `/var/log` **无写权限** → 重定向即失败，脚本根本没跑起来；
  2. `deploy/backup.sh` 写死 `/backup` 目录，`bo` 同样无权限
     （实测 `mkdir: cannot create directory '/backup/…': Permission denied`）；
  3. 该脚本还把 `.env.prod` **明文打进备份并 sync 到 S3**（安全审查 SEC-09），
     且**不覆盖 `sekb_gitea_data`**（RFC D-02 起是版本管理权威源）。
  - **修复**：改用维护中的 `scripts/backup_kb.sh`（覆盖 `sekb_data` + `sekb_gitea_data`，
    带 `EXIT trap` 兜底重启服务），日志落到 gitignore 的 `logs/deploy-backup.log`；
    `deploy/backup.sh` 标记废弃并写明三条原因。实测：`EXIT=0`，产出 112M + 2.2M 两份备份。
- **部署前检查第 6 节永久失败**：`prom/alertmanager` 镜像的 ENTRYPOINT 是 `/bin/alertmanager`，
  所以 `docker run prom/alertmanager:latest amtool check-config …` 会把 `amtool` 当成
  alertmanager 的参数，报 `unexpected amtool, try --help` → 整个预检不通过。
  - **修复**：加 `--entrypoint amtool`。实测 `SUCCESS`（global config / route / 1 inhibit rules / 3 receivers）。
- **结果**：`deploy.sh` 首次完整跑通（`EXIT=0`），七阶段全绿；构建阶段正确打印内核查 commit
  （`a4b1885 version = "0.0.1"`），阶段 7 的缓存上限也确认生效。

---

## 2026-09-14（工程：jobcopilot 转为 git 子模块 + GitHub 镜像打通）

- **P-1 阻塞全部解除**：
  - GitHub 邮箱验证完成后，SEKB 推送镜像一次补齐落后的 **119 个 commit**（GitHub 上已是 `db6f1f9`）；
  - JobCopilot 三个 GitHub 仓库创建成功（`HTTP 201`），四个仓库的 push mirror 全部 ✅ 正常。
  - **踩坑**：在邮箱验证**之前**创建的 mirror 配置会持续报 `OpenSSL SSL_read: unexpected eof`
    （网络实测正常、`git ls-remote` 也通）。**删除镜像配置后重建即恢复** —— 配置早于前置条件的残留，重建比排查快。
- **jobcopilot 从「gitignore 的独立检出」改为 git 子模块**：
  - 原状态别扭：物理上在仓库内却被 gitignore，还带自己的 `.git`；新机器 clone 完 SEKB 直接构建会失败，
    必须有人口头告诉你"还要再 clone 一个仓库到根目录"——**隐藏知识**。
  - 改为子模块后：SEKB 记录内核的**固定 commit**（此前服务器 `pull` 会拿到 main 上任意版本，部署不可复现）、
    新机器 `git clone --recurse-submodules` 一次到位、构建零改动（子模块就是普通目录）。
  - 服务器用**全局 URL 重写**走 HTTP（不写进 `.gitmodules`，避免弄脏跟踪文件）：
    `git config --global url."http://localhost:3000/".insteadOf "ssh://git@100.71.24.105:2222/"`
  - `deploy.sh` 缺内核时的提示改为 `git submodule update --init`，并在构建前打印子模块实际 commit 便于溯源。
- **代价（已知并接受）**：每次改内核后必须回 SEKB 提交一次子模块指针，忘了会部署到旧内核
  （deploy.sh 会打印实际 commit 便于发现）；P4 切 MCP 后本耦合消失，`git submodule deinit` 一行移除。
- 验证：SEKB 715 passed、jobcopilot 78 passed、`docker compose config` 正常解析命名上下文、服务器完整部署跑通。

---

## 2026-09-14（重构：招聘分析内核抽到 jobcopilot 独立包 · P0）

- **背景**：招聘助手要作为独立产品发布，先做「抽内核」——把分析能力从 SEKB 里剥出来，SEKB 改为**直接依赖**该包。
- **新仓库 `jobcopilot`**（Gitea 主 + GitHub 镜像）：零宿主耦合、**零第三方依赖**的 Python 包。
  - `analyzers/single`：单职位 7 步流水线（深度分析 → 知识优先级/差距分析 → 面试Q&A/简历建议/项目迭代/求职策略），每步独立降级；
  - `analyzers/batch`：批量分析（市场行情 + 职位知识迭代，两路并行）；
  - `analyzers/apply_plan`：投递计划 + 大厂冷冻期计算；
  - `stats`：程序化统计（公司/方向/热点关键词），Eval L1 层可零成本断言；
  - `prompts`：多级回退（请求级 override → 宿主本地目录 → packs/职能族 → base）；
  - `providers`：OpenAI 兼容客户端 + DeepSeek/千问/Kimi/豆包/智谱 预设，**BYOK**（Key 只走环境变量）。
- **SEKB 侧保留**：爬虫（collector/sources/fetcher）、缓存（job_cache/analysis_cache）、历史存档（archive）、用户画像与存储接线（profile）。
- **新增适配层** `app/agents/job/llm_adapter.py`：中性 Message ↔ LangChain 消息互转，SEKB 的模型路由/计费/重试/降级**完全不变**。
- **可观测性**：内核默认用标准库 logging 会绕过 SEKB 的脱敏处理器（日志含 LLM 原文片段），故新增 `set_logger_factory` 注入点，把内核日志接进 structlog 管道。
- **隐私**：`prompt/job/README.md` 含真实姓名/公司/年龄/薪资，**刻意不进开源包**；加了两道护栏（文件名黑名单 + 逐字节来源比对）。
- **构建**：`backend/Dockerfile` 通过命名构建上下文 `COPY --from=jobcopilot` 安装内核包（`docker-compose.prod.yml` 的 `additional_contexts`），`deploy.sh` 增加内核查检出前置检查。
- **验证**：SEKB **704 → 715 passed**（原 704 一条不差）；ruff 全绿；mypy 门禁 300 ≤ 基线 310（类型债降 10）；jobcopilot 自身 78 例 + ruff + mypy strict 全绿。
- **回归比对**：`docs/tmp/p0_regression_check.py` 用同一个假 LLM 驱动「git HEAD 旧实现」与「新实现」，比对发给 LLM 的调用序列（含消息类型与正文）、7 段结构化输出、批量报告、降级路径 —— **全部完全一致**。

---

## 2026-09-14（运维：服务器磁盘回收 19.5G + 部署脚本加固）

- **背景**：`deploy.sh` 反复报「磁盘需 ≥20G 空闲」预检不过，排查发现真凶是 Docker 构建缓存。
- **实测回收**：
  - `docker builder prune -af`：构建缓存 `20.2G → 1.16G`，**回收 19.05G**；磁盘 `39G/69% → 25G/43%`（可用 18G→33G）；
  - journal 限容（`SystemMaxUse=200M` / `SystemKeepFree=1G` / `MaxRetentionSec=2week`）+ `apt-get clean`：`/var/log` `378M → 146M`。
- **内存结论（反直觉，已固化为判读方法）**：内存**从未紧张** —— PSI `memory some avg10=0.03`、`vmstat si/so=0/0`、available 1.6G；swap 里 649M 是 rsshub / playwright / dockerd 的冷页，属正常。**今后先看 `docker system df`，不要先看 `free`**。
- **内存小优化**：停用云主机上的无用常驻服务 `fwupd`（+ `fwupd-refresh.timer`，static 需 mask）与 `multipathd`（实测 `/dev/mapper/` 仅 control，无多路径设备），释放约 57M，PSI 归零。
- **根因加固**：`deploy/deploy.sh` 阶段 7 新增 `docker builder prune -af --max-used-space 2GB`（保留 2G 热缓存，旧版 Docker 自动退化为整体清空），防止缓存再次无限增长。
- **新增文档**：`docs/ops/13-DISK-MEMORY.md`（体检三命令 / 清理清单 / PSI 判读 / 加固说明 / 一键巡检脚本），索引登记第 13 行。
- **遗留隐患（待确认）**：7 份备份仍在**同一块磁盘**上，`backup_kb.sh` 无任何 cos/oss/rclone/rsync 上传逻辑 —— 磁盘损坏即数据+备份同时丢失，建议接入腾讯云 COS 做异地副本。

---

## 2026-09-14（基础设施：自托管 Gitea 版本管理底座 + 发布链路切换）

- **背景**：GitHub 在国内访问不稳、且 JobCopilot 独立仓库需要权威源，按 RFC D-02/D-08/D-09 自建 Gitea 作为版本管理底座。
- **服务**（`docker-compose.monitoring.yml`）：新增 `gitea` 服务（`gitea/gitea:1.27.3`，SQLite 单机，512M/0.5cpu，健康检查 `/api/healthz`），端口 `3000:3000` / `2222:22`，独立网络 `sekb_gitea_net` 与数据卷 `sekb_gitea_data`；关闭注册（`DISABLE_REGISTRATION=true`）、锁安装（`INSTALL_LOCK=true`）。
- **仓库**：`bo/sekb`（私有，SEKB 权威源）、`bo/jobcopilot` / `bo/jobcopilot-prompts` / `bo/jobcopilot-dsh-plugin`（公开，JobCopilot 三仓）。
- **发布链路切换**：原「本地 `git bundle` → `scp` → 服务器 `fetch`+`merge`」**已废弃**，改为 `Mac: git push gitea main` → `服务器: git pull`（服务器 `main` 已 track `gitea/main`）；远端 URL 一律不带凭据。
- **备份**：`scripts/backup_kb.sh` 纳入 `sekb_gitea_data`（版本管理权威源必须备份），恢复时自动拉起 `backend` + `gitea`。
- **文档**：新增 `docs/ops/12-GITEA.md`（拓扑 / 仓库清单 / 凭据位置 / 日常运维 / 推镜像 / 备份恢复 / 排障），`00-README.md` 索引登记第 12 行。
- **凭据纪律**：token/PAT 只落盘到 `/home/bo/`（`600`），**不进版本库、不写进 remote URL、不写进文档**。
- **已知阻塞**：Gitea → GitHub 单向推送镜像已配置（`sync_on_commit`，8h 间隔），但 GitHub 账号邮箱未验证导致 `403 You must verify your email address`；验证后镜像即自动补齐，JobCopilot 三个 GitHub 仓库的创建同样等待该验证。
- 验证：Gitea 容器 healthy（约 101 MiB）、`/api/healthz` 200、Gitea 卷备份实测 1.9M 且恢复后服务正常、Mac `push gitea main` 与服务器 `git pull` 双向实测通过。

---

## 2026-09-14（新功能：职位列表联动三增强）

- **背景**：投递计划录入职位要手打、批量分析/历史报告看不到原始职位、赛道热力的「招聘 N 个」是死数字——三处都缺「职位列表」的联动入口。
- **① 投递计划可从缓存职位库选填**：
  - 后端新增 `GET /job/cache/list`（`job_cache.list_all_cached`）——返回某用户**全部未过期**的缓存职位集合（按 ts 倒序，含 count/jobs）；
  - 前端投递弹窗顶部加可搜索下拉（展平全部缓存职位，label 形如 `[关键词] 公司 · 岗位 · 薪资`），选中即自动填入公司 / 岗位 / 链接。
- **② 批量分析 + 历史批量报告增加「查看职位列表」入口**：
  - 批量分析页：Alert 增加 action 按钮，弹出该报告的职位列表；
  - 历史报告详情弹窗：footer 增加「查看职位列表（N）」按钮（仅批量类型且有 jobs 时显示）。
- **③ 赛道热力「招聘 N 个」标签可点击**：
  - `MarketSection` 新增可选 `onShowJobs` 回调，标签变可点击（带 `›` 提示）；
  - 点击后按**赛道名关键词粗筛**相关职位弹出（`filterJobsByTrack`，无匹配自动回退全部，避免空列表体验落差）。
- **共用组件**：`render.tsx` 新增 `JobListModal`（职位列表弹框）——支持关键词过滤，职位名复用 `JobTitle`（点击跳原文 + **悬浮看 JD 详情**）；批量分析 / 历史报告 / 赛道热力三处共用同一弹框。
- **测试**：`tests/unit/test_job_cache.py` 10 例（缓存往返 / 取最新 / 列表倒序 / TTL 过期 / 用户隔离 / 条数上限）。
- 验证：后端 pytest 704 passed、ruff 全绿、mypy 307≤310；前端 tsc 0 错、eslint 0 错误、vitest 66 passed；已部署并线上验证（`/job/cache/list` 已注册、容器 healthy、构建产物含新功能）。

---

## 2026-09-14（新功能：招聘分析「投递作战计划」）

- **背景**：大厂社招普遍有「冷冻期」——面试失败后 6~12 个月内无法再投同一公司/岗位；盲目海投会白白消耗机会，需要一个工具管理投递进度与冷却期。
- **后端**：新增 `app/agents/job/apply_plan.py`（按用户隔离的 JSON 存储）：
  - 记录字段：公司 / 岗位 / 分层（①主攻 ②过渡 ③保底）/ 状态（计划投 / 已投 / 面试中 / 已挂 / Offer）/ 投递日期 / 结果日期 / 冷却月数 / 链接 / 备注；
  - **冷却期自动计算**：状态=已挂且有结果日期+冷却月数时，算出 `cooldown_until` / `cooling` / `days_left`，前端直接展示倒计时（含月末溢出处理，如 1/31+1月→2/28）；
  - 字段白名单规范化：tier / status / cooldown_months 脏值一律回退默认，不因脏数据报错。
- **API**：`GET/POST /job/apply-plan`、`DELETE /job/apply-plan/{plan_id}`。
- **前端**：`Job.tsx` 新增「投递计划」tab（进度统计卡 6 项 + 投递表格 + 新增/编辑弹窗 + 挂面冷却提示 `Alert`）。
- **测试**：`tests/unit/test_apply_plan.py` 14 例（CRUD / 用户隔离 / 规范化 / 冷却期 / 月末溢出 / 统计）。
- 验证：后端 pytest 694 passed、ruff 全绿、mypy 306≤310；前端 tsc 0 错、eslint 0 错误、vitest 66 passed；已部署并线上验证（路由已注册、页面 healthy、构建产物含新功能）。

---

## 2026-09-11（Bug 修复：批量分析/历史报告关键词恒为 Agent）

- **根因**：前端 `handleBatchAnalyze` 调 `batchAnalyze({ jobs, force })` 未传 `keyword`，后端回退到 `config.job.default_keyword="Agent"`，导致报告关键词永远是 Agent（职位数正确、仅关键词错）。
- **修复**：批量分析时带上 `lastFetchKeyword`/`fetchCity`；`syncJobCache` 的硬编码 `'Agent'` 兜底改为 `lastFetchKeyword || fetchKeyword || '未指定'`。
- **存量数据修复**：按 `job_count` 映射回真实搜索关键词，修正 8 条历史报告标题 + `.json` keyword + `.md` 标题，并修正批量报告缓存（`Agent`→`技术型产品`）；原文件已备份 `*.bak`。

---

## 2026-09-10（对话费用展示 + 修改/忘记密码）

### 对话 token / 费用展示
- 新增 `services/usage_service.py`：按「对话」与「用户」两个维度用 Redis Hash（HINCRBY/HINCRBYFLOAT）累计 token 与费用；Redis 不可用静默降级。
- `bootstrap` 条件装配 `usage_service`；`chat.py` 每次回复后累加本轮 token/费用；新增 `GET /api/v1/chat/usage` 返回当前对话 + 用户累计（人民币）。
- 配置：`cost_control.usd_to_cny: 7.2`（固定汇率）+ `cost_control.usage`（Redis）。
- 前端：对话输入框下方显示「该对话用了 N tokens，费用约 ¥X 元」；会话列表底部显示「所有对话累计使用 N tokens，费用约 ¥X 元」。

### 修改密码 / 忘记密码（邮箱登录保持不变）
- `POST /auth/change-password`（已登录，校验原密码）→ 设置页新增「修改密码」卡片。
- `POST /auth/reset-password`（忘记密码，邮箱 + 新密码直接重置，无邮件验证；已限流 + 审计）→ 登录页新增「忘记密码？」入口。

> 注：预览模式**仍不开放 AI 聊天**（按确认调整），故不做预览限额。

---

## 2026-09-10（模型切换：全量迁移到 DeepSeek V4.1 Flash）

### 模型统一切换到 deepseek-flash
- V4.1 Flash 官方 API 模型名为 `deepseek-flash`（性能超 V4 Pro，聊天/推理均可）。
- `config.yaml` 所有角色（含 planner/critic_complex 原 reasoner）模型统一改为 `deepseek-flash`；定价改 `deepseek-flash` 单档（近似值，峰谷计价待精确）。
- `llm_factory` 降级链 fallback 目标改 `deepseek-flash`；`User.settings.model` 默认值改 `deepseek-flash`。

### 设置页隐藏模型设置
- `Settings.tsx` 隐藏「默认模型 / 温度 / 最大 Token」三项（模型统一由服务端配置指定），保留「发送快捷键」；卡片改名「偏好」。

---

## 2026-09-10（集群3.2/3.3 + 文档回填）

### JWT jti 黑名单（SEC-02）
- `create_jwt` 增加 `jti`；`revoke_token`/`revoke_jwt` 吊销；`verify_jwt` 校验黑名单；`POST /auth/logout` 吊销当前 token（登出即失效）。

### PromptRegistry
- 新增 `agents/prompts/registry.py`：路径配置化 + 缓存 + 热重载 + 版本哈希（sha256 短哈希）；`bootstrap` 装配 `prompt_registry`（`prompt/` 目录），供硬编码模板逐步迁移。

### 文档回填
- `docs/tmp/简历补强与SEKB增强待办.md`：标记 1/2/3a/3b 已完成、4/5 部分完成。
- `docs/BACKLOG.md`：顶部新增「合并集群索引」，对齐集群2/5/1/3 状态与提交。

---

## 2026-09-10（集群3.1：反思 needs_rewrite 分支 + RAG 评测基线）

### needs_rewrite 分支（关闭 11-EVOLUTION 缺口）
- `CriticAgent` 新增 `should_rewrite`/`rewrite_count`/`rewrite_feedback`（result=needs_rewrite 且未超 `max_rewrite` 时触发）。
- `ExecutorAgent.rewrite_answer`：据 Critic 反馈重写答案（不重跑工具，失败回退原草稿）。
- `graph/builder.py` 新增 `rewrite` 节点 + `critic→rewrite→critic` 循环边；`route_after_critic` 增加 rewrite 分支。
- 配置 `reflection.max_rewrite`（默认 1）。

### RAG 检索质量基线（纯向量，混合检索未启 rerank）
- 真机 `sekb rag-eval` 30 条黄金集基线：**context_recall 0.512 / context_precision 0.468 / faithfulness 0.890 / answer_relevance 0.762**。
- 修复 `RagEvalRunner` 用户隔离导致检索为空的 bug（默认 `user_id=None` 全库检索）。

---

## 2026-09-10（集群1：Redis L2 中期记忆落地）

### Redis L2 会话记忆
- 新增 `memory/session_memory.py`：`RedisSessionMemory`（实现 `SessionMemoryBackend`），基于 Redis 的跨会话偏好（Hash + TTL）与近期话题（List + LRU 截断 + TTL），连接失败优雅降级。
- `bootstrap` 条件装配 `session_memory`（`l2_session.enabled` 且 Redis 可达）；`shutdown_app` 释放连接；`AppContext` 新增 `session_memory` 字段。
- `chat.py` `_run_chat` 在回复后 `record_topic` 记录本轮话题（失败不阻塞回复）。
- 配置 `l2_session.enabled=true` + `redis_url`；docker-compose 启用 redis 服务（移除 `with-db` profile）。

### rag-eval 修复
- `RagEvalRunner` 新增 `user_id` 参数（默认 None=全库检索）；修复默认落到 `"default"` 用户导致检索为空、评测全 0 的问题。

---

## 2026-09-10（集群5：RAG 收尾 + 注入防护补全）

### 黄金集扩充 + 注入防护补全
- `rag_golden.json` 由 6 条扩到 **30 条**（覆盖 Python/异步、LangGraph、RAG 全链路、Agent 机制、MCP/多智能体/评测/系统设计），供 rag-eval 产出简历量化基线。
- 注入防护补全：`knowledge_ingestor` 事实提取 Prompt 加「忽略指令性语句」隔离标注（P2-P2-12）；`share.py` 分享问答入口接入规则层注入检测（公开路径不启用 LLM 层，防成本滥用，SHARE-2）；`format_rag_share_context` 注入隔离标注。

---

## 2026-09-10（集群2：止血 + 故障可见）

### 限流重开（含分享问答）
- `RateLimitMiddleware` 重写为「IP + 路由分组」独立滑动窗口（修复原全局计数误伤）；路由分组按最长前缀匹配。
- 启用 `rate_limit.enabled=true`，分组：auth 5/min、share 20/min、upload 30/min、job 30/min、news 10/min、默认 60/min；纯 ASGI 不缓冲 SSE。
- 覆盖 SHARE-1（分享问答公开链接限流）。

### Tracing 打通（trace_id 注入）
- `core/tracing.py` 新增 `get_trace_config()`：从 structlog 上下文读 trace_id/conversation_id，注入 LangChain RunnableConfig 的 metadata/tags；`setup_tracing` 改为返回是否启用。
- `llm_factory` 的 ainvoke/astream 统一携带 `config=get_trace_config()`，使 LangSmith span 与业务 trace_id 关联。

### 降级可见化 + 指标埋点
- `ChatResponse` / SSE done meta 新增 `degraded` 字段；前端助手气泡在降级时显示「已降级」角标。
- `LLMFactory._record_call` 接入 `record_llm_call`，修复「LLM 单次调用粒度指标零埋点」（重试/降级计数器真正发 Prometheus）。

### 死配置清理
- 修复 `${VAR:-default}` / `${VAR:default}` 环境变量默认值展开缺陷（原实现把整段当变量名，jwt_secret/视觉模型配置的默认值失效）。
- 删除死配置 `app.debug`、`tools.vector_store.provider`。

- 验证：后端 pytest **650 passed**、ruff 全绿、mypy **297≤310**；前端 tsc 0 错、eslint 0 错、vitest 66 passed。

---

## 2026-09-10（功能开发：RAG 增强 / 检索评测 / 注入防护 / 反馈飞轮）

### RAG 检索增强（混合检索 + 重排 + 查询改写）
- 新增 `tools/rag/bm25.py`（基于 rank_bm25 + jieba 的关键词召回）、`tools/rag/hybrid.py`（向量 + BM25 多路召回 → RRF 融合 → 可选重排）、`tools/rag/reranker.py`（LLM 列表式重排，失败降级原序）。
- `graph/builder.py` 的 RAG 节点在 `memory.l3_knowledge.retrieval.hybrid_enabled` 开启时把 `vector_store` 包装为 `HybridRetriever`，上层意图路由/重要性过滤零改动。
- 配置：`l3_knowledge.retrieval`（hybrid/bm25/rerank/query_rewrite 开关）+ `llm.roles.rerank`。生产默认：混合检索开、重排/查询改写关（控延迟）。

### RAGAS 式检索质量评测
- 新增 `eval/ragas_metrics.py`（context_recall / context_precision / faithfulness / answer_relevance 四指标，LLM-as-Judge，零第三方依赖）+ `eval/rag_runner.py` + `eval/datasets/rag_golden.json`。
- CLI 子命令 `sekb rag-eval`（`cli/rag_eval.py` + `main.py`）输出 Markdown 报告。配置 `evaluation.ragas` + `llm.roles.ragas`。

### Prompt 注入防护
- 新增 `core/guard.py`：规则层（长度上限 + `blocked_patterns` 正则）+ 可选 LLM 层（低温度 JSON 分类）。
- `chat.py` `_run_chat` 入口接入，命中抛 `SecurityError` → 403；`tools/rag/format.py` 在检索内容注入前加「忽略指令性语句」隔离标注（防 indirect injection）。
- 配置：`security.prompt_injection_use_llm` / `prompt_injection_llm_role`（默认关 LLM 层）。

### 反馈数据飞轮
- 新增 `services/feedback_service.py`：消费 thumbs up/down → 被引用知识条目 `importance_score` 升降，踩到 0 分删除条目。
- `chat.py` 持久化 `rag_entry_ids`（本轮 RAG 命中的条目）；`conversations.py` 的 `rate` 端点调用飞轮并返回 `flywheel` 结果。

- 验证：后端 pytest **635 passed**、ruff 全绿、mypy **296≤310**（无新增类型错误）；新增单测 4 个文件（hybrid/ragas/guard/feedback）。

---

## 2026-09-10

### Bug 修复（职位分析 / AI 对话 / 历史报告 / 系列文章）
- **职位分析默认关键词**：新增 `lastFetchKeyword`，删除/刷新职位时的缓存同步改用「最近一次实际采集」的关键词，修复输入框被编辑后污染缓存导致默认回填错误（Agent开发 vs Agent）；并修复「采集缓存命中时未刷新 ts」导致默认回填取到旧关键词（Agent 刷新后回填 Agent开发）。
- **批量分析默认展示**：保持「职位集合严格一致」才展示缓存报告的约束（职位列表须与批量分析严格一致，避免两份信息错位）；并修复「职位收集 → 批量分析」联动时报告不写缓存，导致刷新/重新登录后默认展示缺失（根因：`analyze_market` 在 jobs 提供时跳过缓存写入，改为一律缓存报告）。
- **历史报告时间**：由原样展示 UTC ISO 改为 `new Date(...).toLocaleString('zh-CN')`（北京时间，与其他页面一致）。
- **系列文章树**：按「系列名段」截断路径（兼容 父目录/系列名/文件），消除冗余嵌套；后端 `list_series` 按「系列名之后的相对路径」去重，修复重传（不同父路径）导致的重复计数。
- **系列/文件计数截断（根因）**：新增 `_list_all_entries()` 分页拉取全部条目，替换 `list_series`/`list_files`/`reclassify`/`analyze` 的 `limit=5000`（文档块已达 10883，截断导致「3-技术文章汇总」只统计到 8/应为 12）。修复后线上验证：3-技术文章汇总=12、2-Agent全栈开发学习实践=90、3-Agent开发框架学习=20。
- **数据清理**：物理删除「3-技术文章汇总」旧 12 条（带 `2-Agent全栈开发学习实践/` 父前缀的 570 块），避免 RAG 检索重复内容；该系列现仅剩 12 条干净条目。
- **新会话标题**：后端按「首条提问 + 首条答复」用 LLM 提炼标题并回写会话，标题随 done meta 返回；前端流式完成后更新会话列表标题。
- 验证：后端 ruff/mypy 294≤310/pytest 586 passed；前端 tsc 0 错/eslint 0 错误/vitest 66 passed（+2 系列树单测）。

## 2026-09-09

### 深度代码重构（WP7：前端上帝组件拆分 Job/Chat/Files）
- **Job.tsx**（1635→907 行）：展示组件 + 纯函数下沉到 `features/job/render.tsx`（SectionRenderer/MarketSection/KnowledgeSection/JobTitle/JsonBlock + classifyRole/isEmptyValue/getMatchScore 等）。
- **Chat.tsx**（970→899 行）：CodeBlock 组件 + buildConversationMarkdown/joinSelectedMessages 下沉到 `features/chat/markdown.tsx`。
- **Files.tsx**（890→781 行）：SUPPORTED_EXTENSIONS/filterSupportedFiles/flattenItems/readEntry/buildSeriesTree 下沉到 `features/files/helpers.tsx`。
- 验证：tsc --noEmit 0 错、eslint 0 错误、vitest 64 passed。

### 深度代码重构（WP6：死配置/死代码/占位配置关删）
- **死代码清理**：删除 `scheduler.trigger_now`（无调用方）、`verify_phase3.py`（阶段性验证脚本）。
- **死配置删除**：`TracingConfig.sample_rate`（无消费，P2-12）、`VectorStoreConfig.enabled`（bootstrap 只查 l3_knowledge.enabled，P2-P2-05）。
- **D3 占位配置关删（P1-6）**：删除 adaptive/sampling 反思占位（factory 原降级为 always）及 config.yaml 段；删除 cost_control 预算占位字段（per_conversation_token_limit/daily_budget_usd/reasoner_ratio_alert_above/auto_downgrade_on_budget，均无消费），仅保留 pricing；实现项列入 11-EVOLUTION。
- 全量 586 passed；ruff 全绿、mypy 297≤310。

### 深度代码重构（WP5：记忆压缩加锁 + 恢复压缩 + 配置语义）
- **P2-15**：`ShortTermMemory.compress_if_needed` 增加按 conv_id 粒度的 `asyncio.Lock`（抽 `_compress_impl`），并发压缩串行化。
- **NEW-D**：API `_run_chat` 与 CLI `_restore_history_to_memory` 在历史恢复后调用一次压缩，避免超大历史堆积。
- **P2-13**：`model_switch_threshold` 补 description + critic.py 语义注释（`task_complexity >= 阈值 → reasoner`）。
- **Q-4.3 复核**：JSONStorage 已有 `self._lock`（json_storage.py:77/356），跟踪项过期。
- 全量 587 passed；ruff 全绿、mypy 299≤310。

### 深度代码重构（WP4：LLM 统一入口收口 + 限流接线）
- **LLM 统一入口**：9 处绕过统一入口的裸调用（news×4 / job×2 / classifier / upload_service / share 流式）改为 `ainvoke_with_stats`/`astream_with_stats`（带统计/重试/降级记录）；删除 share.py 死代码 `_extract_stream_text`。
- **RateLimitMiddleware 重开（D4）**：由注释死代码改为 `config.api.rate_limit.enabled` 驱动的条件挂载，阈值取 config（`requests_per_minute` / `rate_limit_login_per_minute`）；默认仍关闭，开启前需验证 SSE。
- **Q-4.7 复核**：`_record_call` 已有 `async with self._lock`（llm_factory.py:150/538），跟踪项过期，无需改动。
- 全量 587 passed（-5 移除 `_extract_stream_text` 测试）；ruff 全绿、mypy 299≤310。

### 深度代码重构（WP2 收尾 + WP3：画像下沉 profile_service 并删除 skill）
- **新增 `services/profile_service.py`**：画像偏好抽取（方案 A `<PREF>` 提取 + 方案 B 记录员 LLM 后台抽取）从 chat.py 下沉；共享 `_build_pref_patch`/`_upsert_profile` 消除原 `_extract_and_update_profile` 与 `_apply_pref_to_profile` 的重复逻辑。
- **删除 skill（D2）**：移除 `ChatRequest.skill`、`_build_skill_context`、`_build_job_analysis_context` 及 `_run_chat` 的 skill 注入；反馈闭环 A 保留（无 `<PREF>` 时 no-op），闭环 B 随 skill 解耦（函数保留，待「求职意图」识别后按意图触发）。
- chat.py 828→538 行；非 skill 路径行为一致。新增 test_profile_service 7 例；全量 592 passed；ruff 全绿、mypy 300≤310。

### 深度代码重构（WP2：job 路由文件导入解析下沉）
- **新增 `services/job_service.py`**：`parse_job_files(files)` 提取 import_jobs 的 55 行文件解析循环（FileProcessor + 临时文件 + 编码回退），job.py 净减 ~50 行并移除不再使用的 os 导入。
- 新增 test_job_service 3 例；全量 585 passed；ruff 全绿、mypy 301≤310。

### 深度代码重构（WP2：share/chat_share 重复助手收敛）
- **新增 `services/share_service.py`**：`get_valid_share`（404/403 校验）与 `owner_display_name`（脱敏展示名，fallback 参数化）单一实现，消除 share.py 与 chat_share.py 各自复制的两份逻辑。
- 两路由保留各自 `_require_share_storage`（不同存储后端）与薄委托包装，调用点/行为不变。
- 新增 test_share_service 7 例；全量 582 passed；ruff 全绿、mypy 301≤310。

### 深度代码重构（WP2：upload 路由入库流水线下沉）
- **新增 `services/upload_service.py`**：从 `api/routes/upload.py`（1006→618 行）提取 MD5 去重索引、图片/文档原文件持久化、入库流水线（process_and_ingest/ingest_chunks/background_ingest/classify_document）、LLM 概览。
- **领域类型 IngestResult**：服务返回领域结果，路由转换为 UploadResponse，消除「服务→路由模型」反向依赖。
- 行为不变；test_image_processor 改从 upload_service 导入；新增 test_upload_service 6 例；全量 575 passed；ruff 全绿、mypy 301≤310。

### 深度代码重构（WP2：服务层下沉 · 首个子提取）
- **新增 `services/browser_client.py`**：从 `api/routes/job.py` 内联的 `_call_browser` + 硬编码 `_BROWSER_BASE` 提取为 `BrowserClient`（可配置 base_url、可注入/单测）。
- **job.py 路由瘦身**：BOSS 扫码两处调用点改走 `_browser.post(...)`，行为不变。
- 新增 test_browser_client.py 2 例；全量 569 passed；ruff 全绿、mypy 306≤310。

### 深度代码重构（WP1：公共工具收敛重复清零）
- **新增 `core/utils.py`**：收敛 `now_iso`（原 graph/state + storage/base 双份）、`fmt_dt`（原 share/knowledge/chat_share 三份）、`safe_float`（原 critic/scribe 双份）、`to_state_dict`（原 chat/cli/eval 三份）为单一实现。
- **新增 `tools/rag/format.py`**：三处 RAG 上下文格式化归一（retriever「知识库参考」/ executor「相关度」/ share「来源编号」三种风格集中维护）。
- **重复清零**：registry `except (MCPError, Exception)` 冗余简化为 `except Exception`。
- **测试**：新增 test_core_utils + test_rag_format 共 39 例；全量 567 passed（528+39）；ruff 全绿、mypy 307≤310。
- 覆盖跟踪项：P2-14 / Q-4.5 / Q-4.6 / Q-4.11 / P2-P2-03 / NEW-E。

### 文档工程重构（按 1-6 工程逆向分析提示词，完整版 12 篇）
- **备份**：原 `docs/` 历史内容清理：旧技术文档/问题记录/测试用例文档已删（git 历史可查），codeReview 迁移至 `docs/codeReview/`，运维手册至 `docs/ops/`，活文档至 docs 根。
- **保留**：运维/部署手册副本 → `docs/ops/`（10 篇，真实环境操作）；CHANGELOG/BACKLOG 活文档副本 → `docs/` 根继续维护。
- **新工程 `docs/tech/`**：完整版 12 篇证据驱动文档（00-README 地图 + 01-ARCHITECTURE C4/ADR + 02-RUNTIME-FLOWS + 03-MODULES + 04-DATA-MODEL + 05-API(65端点/SSE/CLI) + 06-CONFIG + 07-DESIGN-PATTERNS + 08-GLOSSARY + 09-OBSERVABILITY + 10-TESTING + 11-EVOLUTION），全部带 `file:line`、Mermaid、编号/术语一致。
- **事实表 SSOT**：`docs/tech/.facts/T1-T8`（路由 65/配置 178/LLM 23 调用/AgentState/工具/存储/异步/可观测），多子代理并行抽取。
- **发现与记录**：49 死配置、10 处绕过 LLM 统一入口、4 死指标、7 无消费 State 字段、`${VAR:-default}` 语法缺陷、多处注释-实现漂移（→ `docs/tech/漂移清单.md` + `待确认项清单.md`）。
- **代码-文档联动**：`.validation/check-doc-sync.sh`（pre-commit hook：改路由→提示 05、改 config→06、改 graph/agents→02/03、改存储→04、改前端→03、改测试→10）+ `check-freshness.sh`（last-updated/based-on-commit 过期标记）+ `check-links.sh`（链接/孤儿，本地与服务器均全绿）。
- 根 `README.md` 文档导航更新为新工程结构。

### AI 对话体验优化（技能按钮移除 + 消息操作 + 滚动跟随 + 真流式确认）
- **移除技能按钮（任务1）**：删除 AI 对话输入区「通用/应聘/科技资讯助手」技能按钮及 `skill` 参数链路（前端 service/store/Chat + 后端 ChatRequest/`_build_skill_context`/`_schedule_preference_extraction` 调用）。技能模式后续有更具体需求再开发。
- **消息操作按钮（任务2）**：AI 答复下新增 复制/重生成/转发；用户输入下新增 复制/编辑。store 新增 `regenerateAssistant`（截断到该用户消息后重发）+ `editUserMessage`（替换内容+删除其后消息重发），两者复用 `runStream` 走真流式。
- **滚动跟随（任务3）**：用户上滚（距底 >80px）即停止自动跟随，右下角出现悬浮「回到底部」按钮；点击恢复跟随。
- **真流式确认与思考进度修复（任务4）**：作答 token 已确认是**真流式**（`astream_with_stats` → token sink）。首字延迟主因是顺序多智能体链（supervisor → RAG → **deepseek-reasoner planner 15~35s** → executor 逐次 LLM 调用），非推送端问题。修复「思考过程停在旧文案」：`astream`（仅节点结束推送）→ `astream_events`（节点**开始**即推送「正在…」进度），长耗时 LLM 节点期间进度条实时可见。实测节点进度随执行实时推进（0s 理解意图→1s 检索/规划→3s 执行→6s 反思→7s 生成），简单问题首字 5.8s，复杂问题首字延迟取决于 reasoner 规划时长。


## 2026-09-09

### 测试工具链整合（代码审查批次 1+2，含精简去重）
- **约定确立**：每次提交前跑改动范围**增量测试**（`scripts/incremental_test.sh`，支持 `--staged` 提交前对比）；每两周/每月跑一次**全量测试**（`scripts/full_test.sh`）。
- **增量/全量脚本**：`scripts/incremental_test.sh`（改动映射：后端 ruff+mypy 回归门禁+相关 pytest，前端 tsc+eslint+vitest）+ `scripts/full_test.sh`（后端全量单测/集成/门禁 + 前端全量/构建）；后端测试跑在自动构建的 `sekb-toolbox` 镜像（backend 镜像 + ruff/mypy/pytest/feedparser）。
- **后端门禁**：ruff 覆盖面扩到 `tests/` 并清零存量 139 处违规（`86443f9`，行宽 100→120，适配存量中文/SSE 长行）；mypy 改从 backend 目录执行使 `[tool.mypy] strict` 真正生效，存量 310 条类型债转为**回归门禁**（`scripts/mypy_gate.sh` + `backend/mypy-baseline.txt`，超基线才失败，防新增不阻塞迭代）。
- **测试补强**：修复 2 个 API 聊天测试的 mock（`_run_chat` 已改 `astream` 流式执行，`ba79b75`），后端 521→528 passed；新增 TestClient 路由级 API 测试 7 例（知识列表/搜索参数透传、kb 未启用降级、401/403 门禁）；前端接入 @testing-library/react + jest-dom（组件测试 3 例，58 passed）。
- **前端门禁**：ESLint 9 flat config（typescript-eslint + react-hooks）接入 CI 并清零存量 error（`c431ada`），`no-explicit-any` 存量 114 条降 warning 逐轮收窄；tsc --noEmit 保留。
- **pre-commit**：`.pre-commit-config.yaml`（ruff + mypy 回归门禁走 docker，tsc/eslint 走本机 node）。
- **安全扫描**：CI 新增 security-scan job——gitleaks（密钥扫描）+ pip-audit（Python 依赖漏洞）；frontend-build 增加 `npm audit --omit=dev --audit-level=high` 阻断门禁（生产依赖当前仅 5 条 moderate，react-router 链）+ 全量 audit 上报（devDeps 漏洞非阻断）。
- **覆盖率门禁**：CI `--cov-fail-under=40`（当前 43%，逐轮上调）。
- **工具清单精简去重**：safety 并入 pip-audit、hadolint 并入 Trivy；文档守卫类（lychee/OpenAPI/清单）推迟到文档轮次（D15）；node_exporter/cAdvisor 属监控运维轮次。详见 `docs/BACKLOG.md`「五、测试工具链清单与状态」。



## 2026-09-08

### P0 止血（合并 Qoder 深度审查后）
- **修复（CON-02 并发丢数据，已动态复现）**：JSON 存储层引入 `asyncio.Lock` 保护 index 读-改-写临界区；uvicorn `--workers 2` → `--workers 1`（消除多进程共享 ChromaDB/SQLite 并发写、Prometheus 指标失真、scheduler 重复执行）。
- **修复（SEC-01 JWT 弱密钥）**：`get_jwt_secret` 改为 fail-closed（未配置/过短/含占位词即拒绝启动），移除硬编码回退；启动期校验；**已轮换生产密钥**（旧 token 全部失效，需重新登录）。
- **修复（SEC-02 端口暴露）**：backend `8000:8000` → `127.0.0.1:8000:8000`，仅经 nginx 反代对外。

### R2-06 答案 token 真流式
- **新增**：`llm_factory.astream_with_stats` 流式接口 + `core/token_sink.py`（contextvar token 回传）+ Executor 流式生成草稿答案逐 token 回传。答案在生成阶段即流式输出（实测 27.5s 开始出 token，618 token），替代「全量生成后逐字推」。

### 文档工程（第一刀）
- **导航收敛（C2）**：`docs/README.md` 补 8 篇孤儿（ISSUES-FIXES/boss-jd-cookie-manual/job-sources/INCREMENTAL-TEST-CASES/codeReview 等）+ 归档标注 + 新增「Phase 5 能力速览」与阶段路线图 Phase 5。

### 安全止血 + 访问控制 + 体验增强（批次 A0 + 白名单 + B1/B2）
- **安全修复（批次 A0）**：chat 路由补会话归属校验（SEC-01 越权 IDOR）；全局异常脱敏返回 `error_id`（SEC-04）；资讯只读接口补登录鉴权（SEC-05）；分享 `is_scoped` 改任意级非空 + 层级完整性校验（R2-01）+ 默认 30 天过期（R2-02）。
- **可靠性修复**：`backup_kb.sh` 加 `trap` 兜底启动 + 新增 `restore_kb.sh`（R2-03）；前端队列卡死补 `flushQueue` + 非流式清空队列入口（R2-05）；前端单测修复（streamChat 7 参 + user init 会话恢复）（R2-18）；RAG 检索失败打标记（RAG-09）。
- **新增（用户白名单）**：`ALLOWED_EMAILS` 环境变量（逗号分隔邮箱）——白名单内=完整功能，非白名单=预览（仅功能说明 + 科技资讯只读，不能重新生成日报）。`/me` 返回 `access_level`，前端按级别收窄菜单/路由，后端 router 级 `require_full_access` 依赖拦截。
- **新增（B1 思考过程流式）**：聊天由固定「正在思考…」改为按图节点流式推送真实进度（理解意图→检索知识库→规划→执行→反思→生成回答），用 `astream` + `asyncio.Queue` 并发消费。
- **新增（B2 发送快捷键）**：设置页增加「发送快捷键」选项（Enter 发送 / Cmd+Ctrl+Enter 发送），聊天输入框按设置生效。

### 知识库（关键故障修复 + 加固）
- **故障**：ChromaDB HNSW 段损坏导致 `/upload/status` 超时、`/upload/files` 500、上传 502（`chromadb.errors.InternalError: Failed to apply logs to the hnsw segment writer`，为 1.5.9 已知 HNSW bloat-guard bug，官方暂无修复版）。
- **纠正**：此前「删除 VECTOR 段从 WAL 重建」的修复**误删了向量**（WAL 早已合并进段，删除后重建出空段，导致检索返回 0）。真正修复为**重嵌入重建**：`scripts/rebuild_chroma_vectors.py` 从 SQLite 取出 10603 条文档 → 用同一 bge-small-zh 模型重嵌入 → 重建 collection，检索恢复（探针命中、RAG 查询分数 0.74）。
- **性能修复**：`count`/`list_entries` 不再加载 embedding（`include=[]` / `include=["documents","metadatas"]`），避免大库慢查询导致接口超时。

### 知识库加固（L0/L1/L2）
- **L0 锁版本**：`chromadb>=0.5.0` → `chromadb==1.5.9`（当前最新，含 bug 但无修复版，锁死防漂移；待官方修复版再升级）。
- **L1 原始文件落盘**：文档（md/pdf/docx/txt）上传时按相对路径保存到 `data/uploads/documents/`（此前只有图片存原图），作为 ChromaDB 全毁时的最终重灌源。
- **L2 每日备份**：`scripts/backup_kb.sh`（停 backend → 打包整个 `sekb_data` 卷 → 重启 → 保留 14 天），已装 crontab 每天 3:30 执行；已手动跑通首份备份（124M）。
- **L6 模型缓存持久化（D10）**：`HF_HOME=/app/data/hf_cache` 指向数据卷，embedding 模型缓存随 `sekb_data` 卷持久化——重建后端容器不再从 hf-mirror 重下 ~100MB 模型（已把旧缓存迁移进卷，冷启动后检索 0.08s，且每日备份一并覆盖模型缓存，恢复完全自足）。

### 账号隔离（审查 + 修复）
- **审查结论**：会话、职位缓存（3 个）、知识库 ChromaDB、分享均已按 user_id 隔离；**资讯 news 定性为「系统级公共资源」**（登录即可看），维持全局。
- **修复**：文件原始存储（L1）路径与 `uploaded_md5.json` 索引补上 user_id 隔离（`{user_id}/相对路径`、`{user_id}|文件名`），并迁移存量 133 条 MD5。

### 聊天增强
- **新增（对话排队自动发）**：回复进行中时新输入进入队列，当前回复结束后自动发送下一条，避免误打断。前端队列（`pendingQueue`+`flushQueue`）+ 后端按会话加并发锁（同一会话重复流式请求返回错误事件）。
- **新增（历史会话右侧快速导航）**：对话区顶栏加「历史会话」按钮，悬停弹出历史会话浮层（置顶/切换/删除），参考 deepseek 的深色浮层，方便快速查看与切换历史会话。

### 多功能联动地基（第 4 条前置）
- **新增（用户画像）**：`UserProfile` 模型 + `ProfileStorage`（按 user_id 隔离，`data/profile/{user_id}.json`）+ `GET/PUT /api/v1/profile`。含求职偏好（目标岗位/城市/薪资/公司类型/关键词）、技能、职业目标、资讯关注大类——供「应聘助手 / 科技资讯助手」skill 与招聘分析、资讯模块联动使用；聊天中的偏好回流将存入画像。

### 聊天增强 vol.2
- **性能修复**：知识库分类统计 `/knowledge/categories/stats` 由「载入全部文档全文」改为「仅载元数据」，耗时从 ~18.9s 降到秒级——修复分类目录下每个分类无条目数（前端拿不到 counts）。
- **修复（分类目录数量为 0）**：`list_entries` 用 `get(key, [])` 兜底，但当 ChromaDB `include` 不请求某字段（如 `documents`）时，该键存在但值为 `None`，导致 `len(None)` 抛错、统计返回空 `{"stats":[]}`。改用 `get(key) or []` 兜底，统计恢复正常（全部/各分类有真实条目数）。
- **改进（系列文章树对齐）**：系列/子目录/文件改用 antd `showIcon` + 统一图标（📖 读、📁 文件夹、📄 文件），替代 emoji 混排，避免 `[+]` 开关与标题图标错位。
- **改进（应聘助手主动提问 + 画像回流）**：应聘助手**不再强依赖用户画像**——画像不全时主动向用户提问（目标岗位/城市/薪资/技能/职业目标）；用户给出后，回复末尾输出 `<PREF>{...}</PREF>` 结构化标签，后端解析并写入用户画像（反馈闭环），并把标签从展示内容中去除，避免用户看到。
- **新增（应聘助手与职位分析共享数据）**：应聘助手注入近期的职位分析结果（最近 2 份存档报告的「关键词/城市/职位数 + 热门方向 Top3 + 建议补强 Top3 + 代表职位 Top4」+ 职位市场概况）——实现「先分析职位、再据此沟通应聘事宜」的工作流。
- **改进（方案 B：记录员 LLM 抽取偏好）**：不再只依赖主 LLM 输出 `<PREF>` 标签（方案 A 保留为轻量兜底）。新增后台「记录员 LLM」独立分析对话，提取求职偏好写入画像——**异步、不阻塞主回复**，更可靠。
- **新增（D14-职位：画像反向影响职位分析）**：批量分析「一键市场」与单职位分析均按 `user_id` 读取用户实时画像（聊天积累的求职偏好/技能/职业目标），作为「用户实时更新偏好」补充注入分析提示词——分析与用户真实诉求更贴合。

### D14-资讯（定案：保持公共流，不个性化）
- **决策**：资讯为「系统级公共资源」，日报**不做按用户个性化注入**（方案①）。用户画像中的 `news_interests` 不参与日报生成；保持公共流干净自洽。若将来需要「为你推荐」类个人化，放到前端做，不改公共日报生成。
- **已知现象（非本次引入）**：聊天工作流首 token 延迟较高（supervisor→RAG→planner→executor 多步 LLM 调用），通用助手与应聘助手一致；SSE 渐进流出不影响接收。
- **改进（右侧导航改为「对话内历史提问」）**：右侧边缘触发条 + 右侧 Drawer 改为**列出当前对话内的用户历史提问**（用户侧输入，非会话列表），点击某条提问即**滚动定位到消息流中该提问位置并短暂高亮**——方便回看当时上下文。
- **新增（skill 技能按钮 + 调度）**：聊天框下技能按钮（通用助手 / 应聘助手 / 科技资讯助手，参考豆包）。切换技能 → 聊天请求带 `skill` 参数 → 后端构建技能上下文（应聘助手注入求职画像 + 职位市场概况；资讯助手注入关注大类）注入本次 LLM 输入，且**不写入会话历史**。用户画像地基 + skill 注入为「深入沟通」核心；「聊天偏好→画像→反向影响招聘/资讯」的反馈闭环记入 D14。

### 系列识别
- **新增**：`detect_series` 支持「dN 第N天」编号（如 `2-Agent全栈开发学习实践/2-s1-w1/d1-xxx.md`），系列名取**顶级目录名**；`N-` 数字前缀的根目录文件（总纲/学习计划/补充资料）保持独立、不误入系列。
- **性能修复**：`/upload/re-series` 由逐条 `update_metadata` 改为**批量元数据更新**（1 次 get + 1 次 update），并把单次 `limit=5000` 改为分页拉全量，避免大库下超时/漏文件。耗时从 >280s 降到约 15s。
- **修复**：系列树**默认折叠**（移除 `defaultExpandedKeys`），刷新页面即自动加载系列列表（此前因 ChromaDB 500 导致加载为空）。

### 文件上传
- **修复（去重不生效）**：同名去重/跳过此前用 `file.name`（basename）对比后端入库的 `file_name`（完整相对路径），导致文件夹重传时永远匹配不上、不弹「同名同内容跳过」框。现统一改用相对路径标识 `fileKey = webkitRelativePath || name`。
- **改进（续传弹窗）**：检测到未完成上传时，弹窗改为**每个文件前加复选框**，并**只列出尚未入库的文件**（过滤掉已成功入库的残留项）；确认后按勾选的文件续传（文件夹场景自动用文件夹选择器以保留相对路径）。

### 知识库分享（按分类限定范围）
- **新增**：分享不再只支持整个知识库，现在可在创建分享时用**分类级联选择**（大类 → 子类 → 细类）限定范围，选到哪一级就只分享到哪一级；不选则分享整个知识库。
- **后端**：`SharedKnowledge` 增加 `category_l1/l2/l3`；创建/列出/详情均返回 `category_label`；`list_shared_entries` 与分享问答的 RAG 检索都**按分享范围强制过滤**（防止越权看其他分类）；`retrieve`/`search` 增加三级分类过滤参数。
- **前端**：分享弹窗加 `Cascader` 分类选择、分享列表与结果页显示分享范围；分享访问页头部显示分类范围标签。

---

## 2026-09-07

### 文件上传
- **改进**：上传循环迁移到全局 store（zustand），切换左侧导航（组件卸载/重挂）不中断上传，重挂后从 store 读回进度。
- **新增（轻量刷新续传）**：上传时把未完成文件名写入 localStorage，刷新后检测到未完成列表时弹窗列出，让用户确认后重新选择这些文件继续上传（不存文件内容，File 对象跨刷新无法序列化）。

### 资讯日报（分类升级 + 信息源扩充）
- **新增**：分类从「关键词匹配」升级为「批量 LLM 语义分类」（单归属），能按内容区分「Agent(应用层) vs 大模型(算法层)」，失败回退关键词。效果：大模型 7→20 条、安全 0→20 条。
- **新增**：`WebFetcher` 网页采集器（CSDN 热榜 + 魔搭模型库，公开 JSON 接口无需 Token），接入资讯采集流水线。

### 文件上传 / 系列识别
- **新增**：系列文章改用 Tree 多级展示（系列 → 子目录 → 文件），默认展开第一级、子级折叠。
- **修复**：系列文章识别不准确——`detect_series` 支持「第N天/课/讲/期」等常见单位，文件名本身无系列名时用**文件夹名**兜底（利用「同一文件夹下多为同一系列」规则）。
- **新增**：`POST /upload/re-series` 重新识别存量文件系列（不重新分类，轻量）；前端「系列文章」卡片加「重新识别系列」按钮。

### 文件上传
- **修复**：上传失败 502/503 给出明确提示（后端重启/初始化中），并支持「一键重试失败的文件」。
- **新增**：同名文件按内容 SHA-256 对比——同名且内容一致时可「跳过」（含复选框批量跳过），同名但内容不同才询问覆盖。
  - 后端上传时计算并记录文件哈希（`data/uploaded_md5.json`），`/upload/files` 返回 `md5`。

### 资讯日报
- **回退**：取消「有限多归属」，恢复**严格单归属**（一条只归一个类）；不足条目的类通过「扩大信息源」解决，而非分类层凑数。
- **新增**：信息源扩充掘金分类/标签（前端/后端/Android/iOS/LLM/Agent/Flutter），filtered 从 95 → 162 条。
- **改进**：归类改用「标题 + 摘要 + 正文前 500 字符」匹配，提高与大类的相关性。
- **改进**：单类输出加 20 条硬上限，防止某类过载。

### 批量分析
- **回退**：恢复「职位列表严格一致才默认展示最后一次报告」，避免展示过时报告引起误解。

---

## 2026-09-07（更早）

### 资讯日报
- **新增**：`_generate_category` 对 LLM 输出 items 按 title 去重（消除重复条目）。
- **调整**：大类顺序让热门具体类（Agent/RAG/鸿蒙/跨端）优先于宽泛类，避免被「大模型」等抢光。
- **新增**：历史报告存档额外落结构化 JSON，前端用与批量/单职位一致的语义化布局渲染。
- **新增**：批量分析 tab 默认展示最后一次缓存报告（后按用户要求回退为严格一致）。

### 文件上传
- **新增**：支持文件 + 文件夹混合拖入上传，文件夹递归到最深层（`webkitGetAsEntry` 递归 + `webkitdirectory` 选择文件夹按钮）。

### 职位分析
- **新增**：缓存职位默认展示 + 筛选选项回填（`/job/cache/latest`）。
- **新增**：历史报告分「批量分析 / 单职位分析」两个子 tab。
- **新增**：批量分析默认展示最后一次缓存报告（`/job/batch-analyze/cached`）。
- **新增**：资讯日报单归属（一条只归一个类）+ 摘要关键词加粗高亮。

### 资讯日报（归类修复）
- **修复**：去掉「大模型发布/更新」的裸「模型」关键词、「Agent」类的裸「框架」关键词，避免误匹配数据模型/车型/前端框架等。
- **修复**：exclude「早报」，排除 IT早报 类每日汇总栏目。

---

## 2026-09-04 ~ 09-05

### 职位分析（重构）
- **新增**：批量分析接入求职者视角提示词（`批量职位分析.md` + `职位知识迭代.md`），并行两次 LLM 产出「市场行情」+「知识迭代」，喂 JD 摘要。
- **新增**：单职位分析融入「JD 潜台词翻译」，简历建议强化「diff 微调」（不重写全文），移除 per-JD 学习计划。
- **新增**：批量/单职位报告改为中文语义化布局（替代生硬 JSON 表格）。

### 职位收集
- **新增**：跨页全选（「全选全部 N 条」按钮 + `preserveSelectedRowKeys`），解决分页 100 条上限无法全选全部职位。

### 监控与告警
- **新增**：飞书告警卡片按钮拆分为「查看告警(Prometheus) / 查看看板(Grafana) / 查看对话记录」，与告警源一致。
- **修复**：前端健康检查误报 unhealthy（改用 HTTPS + `--no-check-certificate`）。
- **新增**：监控页公网暴露需求记录到 BACKLOG D5（暂缓）。

### 职位筛选
- **修复**：城市筛选从「前缀匹配」改为「包含匹配」，识别「浙江 / 北京市」这类多地职位。
- **修复**：mokahr 城市归一化优先取 cityName（省名兜底），避免「浙江」顶替「杭州」。

### 飞书告警深链
- **新增**：飞书告警「查看对话记录」深链定位到具体会话：
  - 后端 `answer_groundedness` 增加 `conversation_id` 标签；
  - `alerts.yml` 透传 `conversation_id`；
  - feishu_gateway 按钮跳 `FRONTEND_URL/chat?conversation_id=xxx`；
  - 前端 Chat 支持 `?conversation_id=` 深链，未登录时保留目标登录后回跳。

---

## 附：文档维护约定

1. 每次功能迭代或问题修复完成后，**必须**在本文件追加记录，并更新头部日期。
2. 涉及 `config.yaml` 参数变更的，同步更新 [04-config-reference.md](./04-config-reference.md)。
3. 新增架构级决策的，在 [01-architecture.md](./01-architecture.md) 追加 ADR。
4. 需求暂缓/推进的，同步更新 [BACKLOG.md](./BACKLOG.md)。
