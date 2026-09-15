# 13 · 磁盘与内存巡检 / 清理手册

> 适用：腾讯云轻量服务器（3.6G 内存 / 59G 磁盘）。
> 触发场景：部署前检查报「磁盘不足」、`free` 看着很满、告警说内存高。
> 首次排查：2026-09-14（实测回收磁盘 19.5G）。

---

## 0. 一句话结论

**这台机器的瓶颈永远是磁盘，不是内存。**
内存长期有 1.6G available、PSI 压力≈0；磁盘则会被 **Docker 构建缓存**无声吃掉 20G。

排查时**先看 `docker system df`，不要先看 `free`**。

---

## 1. 先做体检（三条命令定性）

```bash
ssh -i ~/.ssh/sekb_tencent_key bo@49.232.42.91

df -h /                  # ① 磁盘：看 Use%
free -h                  # ② 内存：看 available（不是 used！）
docker system df         # ③ 谁在吃磁盘：重点看 Build Cache 的 RECLAIMABLE
```

判读要点：

| 指标 | 健康 | 告警 | 说明 |
|---|---|---|---|
| `df` Use% | < 70% | > 85% | 部署前检查要求 ≥20G 空闲 |
| `free` **available** | > 1G | < 300M | `used` 包含 buff/cache，**看它没意义** |
| PSI `memory some avg10` | < 1 | > 10 | 见下方第 3 节，这才是真实内存压力 |
| Build Cache RECLAIMABLE | < 3G | > 10G | 超了就跑第 2.1 节 |

---

## 2. 磁盘清理（按收益排序）

### 2.1 Docker 构建缓存 —— 唯一的大头

```bash
docker system df                        # 先确认可回收量
docker builder prune -af                # 回收（实测 20.2G → 1.2G，仅 16 秒）
```

- **原理**：`deploy.sh` 每次构建都把层堆进 buildkit 缓存，**从不自动过期**。
- **风险**：无功能风险，只影响下次构建速度（全量重建约多几分钟）。
- **2026-09-14 实测**：`Build Cache 20.2GB / RECLAIMABLE 19.05GB`，回收后磁盘 `39G→25G used`、`18G→33G avail`（69%→43%）。

### 2.2 journal 系统日志

```bash
journalctl --disk-usage
sudo journalctl --vacuum-size=100M
sudo mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=200M\nSystemKeepFree=1G\nMaxRetentionSec=2week\n' \
  | sudo tee /etc/systemd/journald.conf.d/limit.conf
sudo systemctl restart systemd-journald
sudo systemd-analyze cat-config systemd/journald.conf | tail -8   # 确认生效
```

> **踩坑**：`du -sh /var/log/journal` 曾经显示 322M，但 `journalctl --disk-usage` 只有 43M。
> 以 `journalctl --disk-usage` 为准（`du` 会把已删除但仍被进程持有的文件算进去）。

### 2.3 apt 缓存

```bash
sudo apt-get clean      # 实测 147M → 40K
```

### 2.4 备份文件（**谨慎，见第 5 节**）

```bash
ls -la /opt/self-evolving-kb/backups/
du -sh /opt/self-evolving-kb/backups
PRUNE_DRY_RUN=1 /opt/self-evolving-kb/backup_kb.sh --prune-only   # 预演：只列出将删哪些，一份都不删
/opt/self-evolving-kb/backup_kb.sh --prune-only                   # 真正清理（不做新备份、不停服务）
```

> `--prune-only` 跳过备份阶段：**不会**再打包一份 112MB，也**不会**停 backend/gitea。
> 不带它时脚本会先完整备份一次再清理（cron 与 `deploy.sh` 用的就是默认模式）。

保留策略（`scripts/backup_kb.sh`）是**两条规则叠加**：

1. 超过 `RETENTION_DAYS`（默认 14 天）→ 删；
2. **同一天只留最新一份** → 删（`RETENTION_MIN_KEEP` 默认 2 份保底不受此限）。

> **踩坑（2026-09-15）**：原来只有规则 1，**管不住同一天的多份**。而
> `deploy/deploy.sh` 每次部署前都会调 `backup_kb.sh` 备份一次 → 一天部署 N 次
> 就留 N 份近乎相同的数据。当天积了 **9 份 ×112M ≈ 1.0G**，把可用空间挤到
> 24394MB，低于构建门禁 25000MB，**部署直接卡住**。
> 加规则 2 后：18 份 → 9 份，释放 1003MB，且 `0908~0915` **每天仍各留一份**。

**磁盘不紧张时不要手工删** —— 这是目前**唯一的**数据副本（无异地备份，见第 5 节）。

---

## 3. 内存：怎么判断「真的紧张」

**不要看 `used`**。用 PSI（Pressure Stall Information，内核直接给出的真实争抢程度）：

```bash
cat /proc/pressure/memory      # 关键看 some/full 的 avg10
vmstat 5 3                     # 关键看 si/so（换入换出），0 = 没有换页活动
```

2026-09-14 实测（空闲态）：

```
some avg10=0.03  avg60=0.04   ← 争抢≈0
full avg10=0.01               ← 没有进程因缺内存停顿
vmstat si/so = 0 / 0          ← 完全没有换页
load average: 0.18 0.11 0.08
```

**结论：swap 用掉 649M ≠ 内存不足。** 那是 rsshub（89M）、playwright（85M）、dockerd（66M）
这些 10 天没动的冷页被换出去了，属于正常行为，不需要处理。

### 真正紧张时才做的动作

```bash
# ① 找出谁在占（RSS，注意容器内进程在宿主机也可见）
ps -eo pid,ppid,rss,pmem,comm,args --sort=-rss | head -16

# ② 容器视角（比 ps 准，含 limit）
docker stats --no-stream --format "table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}"

# ③ 降 swappiness（减少无谓换出；默认 60 偏激进）
sudo sysctl -w vm.swappiness=10
# 持久化：/etc/sysctl.d/99-swappiness.conf
```

### 云主机上的无用常驻服务（可关）

| 服务 | 内存 | 为什么可关 | 命令 |
|---|---|---|---|
| `fwupd` + `fwupd-refresh.timer` | ~30M | 云主机无固件可更新（`/sys/class/firmware` 为空） | 见下 |
| `multipathd` | ~27M | 实测 `/dev/mapper/` 只有 `control`，无多路径设备（单盘 vda） | 见下 |

```bash
# fwupd.service 是 static，disable 无效，要停它的 timer 并 mask
sudo systemctl disable --now fwupd-refresh.timer
sudo systemctl mask fwupd.service fwupd-refresh.service

# multipathd
sudo systemctl disable --now multipathd.service multipathd.socket
```

> 关之前**必须先验证**：`ls /dev/mapper/`（只有 control 才安全）。

---

## 4. 根因加固（已进 `deploy/deploy.sh`）

光清不加固会复发。部署脚本「阶段 7：清理与收尾」已加入构建缓存上限：

```bash
docker builder prune -f --max-used-space 14GB     # ✅ 正确
# docker builder prune -af --max-used-space 14GB  # ❌ 静默空操作！
```

### ⚠️ 两个实测踩到的坑

**坑 1：`--max-used-space` 不能和 `-a/--all` 一起用。**
实测 `docker builder prune -af --max-used-space 10GB` 返回 `Total: 0B`——**缓存一点不掉**，且不报错。
去掉 `-a` 后立刻正常：`19.54GB → 12.36GB`（LRU 淘汰）。这类「静默不生效」最危险，
加固命令必须实测过一次输出再上线。

**坑 2：上限设太小会让每次部署重装依赖。**
完整安装 `requirements.txt`（含 torch / transformers）需要保留约 **14G** 缓存层。
一开始按「能省则省」设成 2G，代价是每次部署都要重装依赖、构建从 1 分钟变成 **13 分钟**。
14G 上限下磁盘占用约 35G、可用约 24G，稳稳通过 20G 预检——**这是磁盘与部署速度的平衡点**。

### ⚠️ 构建过程本身会把磁盘吃满（2026-09-15 实际故障）

**症状**：`deploy.sh` 在后端镜像构建阶段失败：

```
write /tmp/.tmp-compose-build-metadataFile-*.json: no space left on device
```

**根因**：构建过程中 **buildkit 缓存会持续增长**（一次全量构建可增十几 G），
而缓存上限原先只在**阶段 7 收尾**执行——太晚。当时可用 17G，构建峰值约需 25G
（旧镜像 10.9G + 新镜像 ~11G + 缓存增长），中途就爆了。

**已加固**（`deploy.sh` 构建前）：可用 < 32G 先整体清缓存；清完仍 < 25G 直接失败
并给出腾空间的具体命令——**宁可明确失败，也不要构建到一半炸掉留半成品**。

**两个反直觉点**：

- `docker image prune -f` **清不掉旧版本镜像**：它们有标签、不是悬空镜像，
  必须 `-a`；而旧后端镜像常有 10G 量级，是最容易被忽略的一块。
- `docker builder prune -f --max-used-space N` 只统计**可回收**部分
  （那次只剩 447MB），"in use" 的 12G 一动不动 → 返回 `Total: 0B`。
  想真正腾空间只能 `-af`。

**闸门阈值参考**（59G 盘、后端镜像 ~11G）：

| 指标 | 经验值 |
|---|---|
| 构建峰值需求 | ~25G 可用 |
| 低于多少先清缓存 | 32G |
| 全量构建耗时（无缓存） | ~13 分钟 |

### ⚠️ 已解决的根因：后端镜像里装了用不上的 CUDA torch（2026-09-15）

后端镜像原本 **10.9GB**，其中 `site-packages/nvidia` 占 **3.2GB**（19 个 CUDA 包）——
因为 torch 是被 `sentence-transformers` 传递引入的，而 PyPI/国内镜像上的 Linux torch
默认是 **CUDA 构建**，可这台机器**没有 GPU**。

改用 CPU 版 torch（`backend/constraints-image.txt` 钉 `torch==2.14.0+cpu`，
Dockerfile 先用 `pip download --no-deps` 取轮子再用 `--find-links` 安装）后：

| 指标 | 改前 | 改后 |
|---|---|---|
| 后端镜像 | 10.9GB | **3.34GB** |
| `site-packages/nvidia` | 3.2GB | 不存在 |
| 可用磁盘 | 16–24GB | **32.7GB** |

**因此本节下面的「磁盘紧张」经验大多已成历史**，但仍值得保留（换机器/改依赖时可能复发）。
两条踩坑记录也留在 `backend/constraints-image.txt` 与 CHANGELOG 里：
`--prefix` 安装后再装 requirements 会重新解析并拉回 CUDA 版；给主安装步骤加
`--extra-index-url` 会让 pip 对所有包都查境外索引（networkx 掉到 36 kB/s）而构建超时。

### 缓存与镜像：`docker system df` 的「可回收」在本机**不可信**

> ⚠️ **2026-09-15 实测纠正**：本文档此前写「上一个后端镜像会失去标签，变成 10G 级别的
> 可回收空间，阶段 7 的 `docker image prune -f` 会顺手清掉」。**这个结论是错的。**

本机 Docker 已启用 **containerd 镜像存储**（`docker info` 显示
`Storage Driver: overlayfs` + `driver-type: io.containerd.snapshotter.v1`），
镜像实体在 `/var/lib/containerd` 而不是 `/var/lib/docker`（后者仅 941M）：

```bash
sudo du -xshm /var/lib/containerd/*          # 注：* 必须交给 root 展开
#   18922  .../io.containerd.snapshotter.v1.overlayfs   ← 解包后的镜像层
#    5059  .../io.containerd.content.v1.content         ← blob
```

此时 `docker system df` 会**误报**：它报 `Images 16.58GB / RECLAIMABLE 10.71GB (64%)`，
但 12 个镜像**全部被运行中的容器引用**，`docker image prune -a -f` 实测只回收 **1.013MB**。

- **原因**：容器记录的 `.Image` 是**平台 manifest digest**（如 `233241502afc`），
  而 `docker images` 显示的是 **index digest**（如 `fdb9585a6c8d`）；两者不匹配，
  Docker 就把那个 10.9G 的后端镜像当成「无人使用」。同理 `docker builder prune`
  也可能报 `Reclaimable: 0B`（实测缓存 1.155GB 却清不掉）。
- **因此**：不要靠 `docker system df` 判断能不能腾出空间，**以 `df -Pm /` 为准**；
  镜像存储这块基本**没有**可回收量。

> 旧版 Docker 不支持 `--max-used-space` 时自动退化为整体清空（安全，只是下次构建慢）。
> 本机实测 Docker 29.6.1 / buildx 0.35 支持。

---

## 5. ⚠️ 尚未解决的隐患：备份没有异地副本

**现状**：全部备份（约 9 份 / 1.0G）都位于 `/opt/self-evolving-kb/backups/`，**和源数据同一块磁盘**。
磁盘损坏 / 误删 / 勒索 → 数据和备份一起没。

`backup_kb.sh` 里**没有任何** cos / oss / rclone / rsync / scp 上传逻辑。

**建议**（未实施，待确认）：

| 方案 | 成本 | 说明 |
|---|---|---|
| 腾讯云 COS + `coscmd`/`rclone` | 对象存储按量（本项目数据量约 120M/天，存 30 天≈3.6G，几毛钱/月） | 最省事，脚本里加一步上传即可 |
| 拉到 Mac 本地 | 0 | 需要 Mac 常开，手动或定时 `scp` |
| Gitea 之外的第二个云盘 | 低 | 挂载 CBS 后 `cp` |

---

## 6. 一键巡检脚本（复制即用）

```bash
echo "===== $(date +%F' '%T) ====="
df -h / | tail -1
free -h | head -2
echo "--- PSI:"; cat /proc/pressure/memory
echo "--- docker:"; docker system df   # ⚠️ 本机 RECLAIMABLE 不可信，以 df -Pm / 为准
echo "--- containerd:"; sudo du -xshm /var/lib/containerd/* | sort -hr | head -3
echo "--- 备份:"; du -sh /opt/self-evolving-kb/backups; ls /opt/self-evolving-kb/backups/*.tar.gz | wc -l
echo "--- 日志:"; journalctl --disk-usage
echo "--- 容器:"; docker ps --format '{{.Names}}\t{{.Status}}'
```

---

## 7. 变更记录

| 日期 | 动作 | 效果 |
|---|---|---|
| 2026-09-14 | `docker builder prune -af` | 磁盘 39G→25G used（69%→43%），回收 19.05G |
| 2026-09-14 | journal 限容 200M + `apt-get clean` | `/var/log` 378M→146M |
| 2026-09-14 | 停用 `fwupd` / `multipathd` | 释放约 57M 内存，PSI 归零 |
| 2026-09-14 | `deploy.sh` 加入 `--max-used-space 2GB` | 防复发（待下次部署验证） |
| 2026-09-15 | `backup_kb.sh` 保留策略加「同天去重」+ `RETENTION_MIN_KEEP=2` 保底 | 备份 18 份→9 份，释放 1003MB（`0908~0915` 每天仍各留一份） |
| 2026-09-15 | 纠正本文档「旧镜像可回收 10G」的错误结论 | 实测 `image prune -a` 仅回收 1.013MB；containerd 镜像存储无可回收量 |

---

## 关联

- 备份脚本：`scripts/backup_kb.sh`（cron 实际执行的是 `/opt/self-evolving-kb/backup_kb.sh`——**两者为不同文件、无自动同步机制**，改仓库版本不影响 cron；详见 `12-GITEA.md` §6.1）
- 恢复脚本：`scripts/restore_kb.sh`（已与备份**对称**：`--sekb` / `--gitea` / `--both` 双卷恢复，含 `--dry-run` 预检与 `--drill` 旁路卷演练；详见 `12-GITEA.md` §6.2–6.3）
- 部署脚本：`deploy/deploy.sh`（阶段 7 清理）
- 监控看板：`docs/ops/11-MONITORING.md`
- 代码仓库底座：`docs/ops/12-GITEA.md`
