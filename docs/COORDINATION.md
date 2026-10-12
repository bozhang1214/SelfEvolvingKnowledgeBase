# 协作登记表（开工前认领，收工后删除）

> 约定见 [AGENTS.md](../AGENTS.md)。**开工前加一行，收工后删掉**——表格越短越有用。
> 目的：改共享文件前能看出「有没有人正在改同一个文件」。

## 进行中

| 工作流 | 负责人 | 涉及文件（重点） | 开始时间 |
|---|---|---|---|
| **E′ · 鸿蒙 H3 网络 + H4 凭证**（✅ 已达成：`36e9ecf`，设备 `H3 PASS 3/3`×2 轮 + `CRED PASS 8/8`；仍未验：真机/HTTPS/真实 enroll-refresh）（`@ohos.net.http` 实现 `HttpTransport` + 流式；HUKS 实现 `CredentialStore`，纯 Kotlin/Native cinterop） | DSH Agent（子代理 E′） | `apps/harmony/spike/knspike/src/ohosMain/kotlin/**`（优先新建）、`hapshell/entry/src/main/ets/**`、`entry/src/main/cpp/napi_init.cpp`、`entry/src/main/module.json5`、`scripts/harmony.sh`、`apps/harmony/README.md` | 2026-10-26 |
| **F · 鸿蒙 H5 RAG + 检索同表**（进行中：RDB 探针 PASS，`OH_Rdb_Insert` 的 Kotlin/C++ 差异正在定位）（RDB NDK C API 实现 `VectorStore`/`DocumentRegistry`；接共享层 `KnowledgeIndex`/`Retriever`/评测） | DSH Agent（子代理 F） | 同上（**优先新建文件**；与 E′ 共用目录，改动只加自己的注册行） | 2026-10-26 |
| **A · 共享层公共设施**（契约路径 N 方守卫、facts/状态文档同步、四端同表汇总） | DSH Agent（主线） | `scripts/check_protocol_paths.py`、`docs/多端跨端-*.md`、`docs/RFC-多端跨端方案.md`、`apps/README.md`、`docs/changelog.d/**` | 2026-10-25 |
| **G · 鸿蒙 H8 四页 ArkUI UI**（聊天流式/徽章/来源 + 历史 + 文档 + 设置 + `SEKB_HARMONY_SELFTEST` 表） | DSH Agent（子代理 G） | `ets/pages/**`（新建页）、`ets/model/**`（自己的状态文件）、`ets/entryability/**`、`resources/base/profile/main_pages.json`、`scripts/harmony.sh`（只加自己的动作）、`apps/harmony/docs/screenshots/**`；**另加**（G 自己的挂载点，**已避开 `napi_init.cpp` 与 F 的 RDB 文件**）：`spike/knspike/src/ohosMain/kotlin/{ChatStreamPort,SekbAppPort}.kt`（新建）、`entry/src/main/cpp/sekb_chat_napi.cpp`（新建）+ `CMakeLists.txt`（加一行源文件）+ `h3h4_napi.cpp`（只加一行挂载 `RegisterSekbChat`，不改 E′ 的 `sekb_spike_h3_*`/`sekb_spike_cred_*` 任何行为） | 2026-10-26 |
| **H · 撤销 iOS 桥（逻辑一份）**（把 `SekbApiBridge.swift` 改回共享层 `SekbApi`，`@Throws` 已补齐） | DSH Agent（主线） | `apps/ios/App/**`、`scripts/ios_app.sh`、`apps/ios/README.md` | 2026-10-26 |
| ~~**D · `.ms` 掩码保真**~~ ✅ 已收工（`9ee1679` 等 5 个提交）：真因是**设备侧把 int32 token 输入按 int64 写、越界 2048B**（不是转换器丢掩码），设备 `EMBED PASS 10/10`、cos=0.2439 | — | — | — |
| ~~**B · iOS + Mac UI**~~ ✅ 已收工（`8f5ca75`，自检 42 PASS / 0 FAIL / 1 SKIP，截图已复核） | — | — | — |
| ~~**C · 鸿蒙 H1→H6 半程**~~ 已收工（`f993d38`/`7d38264`/`6b4dd5d`/`d367d02`）：H1/H2 ✅、H3 改判（`7a25d7e`）、H4–H8 待续 | — | — | — |

> **并行边界（防静默覆盖，开工前必读）**：
> **D 只碰上表列出的文件**；`apps/shared/src/commonMain/**`（共享层公共逻辑）与 `apps/ios/**`、`apps/mac/**` 归主线，
> D 若确需改动**先在报告里说明**，不要自行改动（共享层逻辑已有 295 用例与守卫兜着）。
> `docs/CHANGELOG.md` 一律走 `docs/changelog.d/` 碎片（每个工作流用**各自的**碎片文件，不共用）。
>
> ### ⚠️ 2026-10-26 事故后的**硬约定**：`napi_init.cpp` 归主线独占
>
> 事故经过与复盘见 `docs/changelog.d/20261012-122434-*.md`：两个工作流同时编辑
> `apps/harmony/spike/hapshell/entry/src/main/cpp/napi_init.cpp`，后保存者静默覆盖前一个 →
> 重复/截断的 `Ping`、helper 被整段删除、双方注册行各消失一次，**编译与链接都失败过**。
>
> 即日起：
> 1. **`napi_init.cpp` 由主线独占**，其他工作流**不编辑它**；要加 NAPI 入口就把**名字 + 参数**发给主线，
>    或者写成**独立 `.cpp` + 在 `CMakeLists.txt` 显式列出**（E′ 的 `h3h4_napi.cpp` 就是这个形态，效果好）；
> 2. **改完就提交**（本轮全部损失都来自"未提交的改动被覆盖后没有副本"）；
> 3. **构建前先体检**：`grep -c 'static napi_value Ping'`（应为 1）与 `{`/`}` 计数是否相等——
>    被截断的文件在这两项上立刻现形。

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
| 2026-10-12 | 协作者提交 `acd8b79`（"COORDINATION：D 收工…"）时，把我**尚未提交**的 `docs/COORDINATION.md` 改动（工作流 E 的登记行）一并卷进了他的提交 | **无内容损失**（行已入库，只是挂在别人的提交说明下）。同类情况若两边改的是**同一行**，就会静默覆盖 | 未改历史（main 禁强推）。教训：**自己没改完的文件不要留在工作区**——认领表这种"随时要改"的文件改完**立刻单独提交**；提交前继续按 §3 复核 `git diff`，看到不属于自己的改动就停下问清 |

