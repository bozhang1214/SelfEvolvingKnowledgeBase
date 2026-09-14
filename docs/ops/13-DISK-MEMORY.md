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
```

默认保留 14 天（`scripts/backup_kb.sh` 的 `RETENTION_DAYS`），约 7 份 × 112M ≈ 780M。
**磁盘不紧张时不要删** —— 这是目前**唯一的**数据副本（无异地备份）。

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

### 缓存与悬空镜像

每次部署出新镜像后，**上一个后端镜像会失去标签**（`image:` 名字被新镜像占用），
变成 10G 级别的可回收空间。部署脚本阶段 7 的 `docker image prune -f` 会顺手清掉，
所以**不要在部署前手动清**——那时旧镜像还被运行中的容器占着，清不掉。

> 旧版 Docker 不支持 `--max-used-space` 时自动退化为整体清空（安全，只是下次构建慢）。
> 本机实测 Docker 29.6.1 / buildx 0.35 支持。

---

## 5. ⚠️ 尚未解决的隐患：备份没有异地副本

**现状**：7 份备份全部位于 `/opt/self-evolving-kb/backups/`，**和源数据同一块磁盘**。
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
echo "--- docker:"; docker system df
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

---

## 关联

- 备份脚本：`scripts/backup_kb.sh`（cron 实际执行的是 `/opt/self-evolving-kb/backup_kb.sh`——**两者为不同文件、无自动同步机制**，改仓库版本不影响 cron；详见 `12-GITEA.md` §6.1）
- 恢复脚本：`scripts/restore_kb.sh`（**仅恢复 `sekb_data`，不含 `sekb_gitea_data`**；Gitea 恢复见 `12-GITEA.md` §6.2）
- 部署脚本：`deploy/deploy.sh`（阶段 7 清理）
- 监控看板：`docs/ops/11-MONITORING.md`
- 代码仓库底座：`docs/ops/12-GITEA.md`
