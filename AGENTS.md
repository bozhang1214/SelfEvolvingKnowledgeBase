# AGENTS.md · 协作约定（多协作者 / 多 Agent 同时开发）

> **本文件是硬约定，不是建议。** 任何在本仓库工作的协作者（人或 Agent）动手前先读完。
> 背景：本项目存在**多个协作者同时开发**的情况，且曾因此真实发生过冲突
> （CHANGELOG 条目标题被覆盖、同一文件两边并发修改）。

---

## 0. 一句话

**我们共用一个工作目录。** 因此「两个人同时改同一个文件」不是可能，而是**必然**——
`git` 不会报冲突，后保存的人会**静默覆盖**先保存的人。下面的规则就是为了把这类
静默覆盖变成「不可能」或「显式可见」。

---

## 1. 开工前：认领（必做）

在 `docs/COORDINATION.md` 的表格里**加一行**，写明：工作流 / 负责人 / 将改动的文件 /
开始时间。收工后删除该行（保持表格短小）。

改**共享文件**前，先看表格里有没有别人正在改同一个文件——有就等，或先沟通。

**共享文件（重点）**：

| 文件 | 为什么危险 |
|---|---|
| `backend/app/agents/job/market.py` | 分析与缓存编排的主战场 |
| `backend/app/api/routes/job.py` | 接口层，双方都会加端点 |
| `docker-compose*.yml` / `deploy/nginx.conf` / `deploy/deploy.sh` | 基础设施，改错影响全站 |
| `docs/CHANGELOG.md` | **历史冲突热点** → 见第 2 条，不要再直接改它 |
| `apps/*/` | 各端应用（2026-09-18 起 Android 在 `apps/android/`）：端侧改动与 SEKB 主代码同仓，**冲突窗口比过去大**，改前先看认领表 |
| `.tooling/` | 本机构建状态（Gradle 缓存等，不入库）。**不要提交**、也不要清理别人的缓存——清了就是几十分钟重新下载 |

## 2. 写变更记录：用**碎片文件**，不要改 CHANGELOG

> ❌ **不要**再往 `docs/CHANGELOG.md` 顶部插入条目 —— 所有人都这么做，必然冲突
> （历史上已因此吃掉过 3 个条目的标题）。

✅ 改为新建一个碎片文件：

```bash
python3 scripts/changelog_add.py --title "招聘分析：搜索历史 + 报告按搜索隔离"
# → 生成 docs/changelog.d/20260915-104500-招聘分析-搜索历史.md
# 然后编辑它，正文照原来 CHANGELOG 的写法（含 `## 日期（标题）` 这一行）
```

发版或需要时合并进 CHANGELOG：

```bash
python3 scripts/changelog_merge.py            # 合并 + 删除已合并的碎片
python3 scripts/changelog_merge.py --check    # 只检查碎片格式，CI 用
```

**碎片文件按「一次改动一个文件」命名，天然零冲突。**

## 3. 提交前：复核 diff（必做）

```bash
git status && git diff          # ⚠️ 看 diff 里有没有「不是我改的内容」
```

如果出现别人的改动，说明**共享索引**把对方的暂存/工作区内容卷进来了——
停下来，用 `git add <具体文件>` 逐个挑，不要 `git add -A`。

**提交要小、要勤**：一次改动一次提交。攒着不提交会显著放大第 1 条说的覆盖窗口。

## 4. 改内核（`jobcopilot` 子模块）前先确认

子模块指针是全局单点：谁改了指针，别人一 `git pull` 就跟着变。

- 改 `jobcopilot/**` → 必须同时更新 SEKB 的子模块指针并**在同一个提交里说明**；
- 开工前跑 `bash scripts/check_kernel.sh`，确认没人正在动它。

## 4.5 端侧构建（`apps/`）：统一走 `scripts/android.sh`

`apps/android` 的构建状态（Gradle 缓存、Android debug keystore）全部落在仓库内的
`.tooling/`，所以：

```bash
bash scripts/android.sh test | assemble | install   # 构建/装包
bash scripts/emulator.sh --background               # 起模拟器（同样状态全在仓库内）
```

不要手工 `export GRADLE_USER_HOME=~/.gradle` 再构建——那会把缓存写到仓库外，
在受限沙箱（如 DSH 的 workspace-write）里直接失败，而且缓存会分裂成两份。

两个已踩过的坑（写在脚本注释里，改脚本前先读）：

- **必须设 `ANDROID_USER_HOME`**，否则 `assembleDebug` 会因为写不了 `~/.android/debug.keystore` 失败；
- **不要再设 `ANDROID_PREFS_ROOT`**（哪怕指向同一路径）——AGP 9 会崩在
  `AndroidLocationsBuildService ... AndroidDirectoryCreator`。

模拟器同理（`scripts/emulator.sh` 已封装），它默认往外写四处，缺一处就崩或连不上：
`HOME`（jwk 目录，写不了会 **Abort trap**）、`TMPDIR`、`ANDROID_AVD_HOME`、`ANDROID_USER_HOME`；
另外 **adb 密钥必须与 AVD 里授权的那把一致**（AVD 里存的是创建它时的 `~/.android/adbkey`，
换了 HOME 会变成 `unauthorized`），以及被杀掉的模拟器会留下 `*.lock` 导致
"Running multiple emulators with the same AVD"。

## 5. 部署：脚本自带锁，别绕过

`deploy/deploy.sh` 已加 `flock` 互斥。**第二个部署会直接失败并提示谁在部署**，
不要手工绕过锁去 `docker compose up` —— 两个部署交错会把服务停在半成品状态。

部署前先 `git pull` + `git submodule update --init`，确保你部署的是最新代码。

## 6. 推送

- 目标分支是 `main`（已开启保护：**禁止强推与删除**）；
- 推送前先 `git pull --rebase`，避免制造无意义的合并提交；
- 推送失败（非快进）→ 说明有人先推了，**先 pull 再解决冲突**，不要强推。

## 7. 冲突万一发生了

1. **不要慌着"还原"**：先 `git log --oneline -10` + `git diff` 搞清楚谁覆盖了谁；
2. 内容还能找到：`git reflog`、`git fsck --lost-found`、编辑器本地历史；
3. 处理完在本文件或 `docs/COORDINATION.md` 记一笔：**发生了什么 / 怎么避免**。
   同类问题发生过两次却没记录，是它再次发生的直接原因。

---

## 附：为什么不上「工作区隔离」（git worktree）

隔离工作区是根治「同文件静默覆盖」的办法，但每个 worktree 是干净检出，
需要各自重建 `backend/.venv`（含 torch/transformers，约 10 分钟 + 数 GB）。
**当前本机与服务器资源都紧张，暂不采用**；改为用上面的认领 + 碎片 + 复核来压制风险。
若将来资源宽裕或并发人数增加，应重新评估。
