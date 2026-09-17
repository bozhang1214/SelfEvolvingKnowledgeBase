#!/usr/bin/env bash
#
# 知识库每日备份脚本（L2 兜底）。
#
# 做法：短暂停 backend / gitea 拿到一致快照 → 分别打包 sekb_data 与
#       sekb_gitea_data 数据卷 → 重启服务 → 按保留天数清理旧备份。
#
# 数据卷 sekb_data 内含：chroma_db（向量库）、uploads/documents（原始文件）、
# uploaded_md5.json、shares.json、conversations、news 等全部知识资产；
# 以及 edge/routes.jsonl（端云路由日志 —— M1 起「端侧完成率/升级率」两个北极星指标的
# **唯一事实来源**，只追加、丢了就无法回溯历史决策，故随卷一起备份）。
#
# 数据卷 sekb_gitea_data（RFC D-02 起为版本管理权威源）：仓库、SQLite 库、
# Gitea 配置、附件等，必须纳入备份，否则 Gitea 故障即丢源码。
#
# 关键保障：`trap ... EXIT` 兜底——无论脚本成功或失败（如 tar 失败），
# 退出前都会确保 backend 与 gitea 已启动，避免凌晨无人值守时停服整夜。
#
# 安装（服务器上，root 或可执行 docker 的用户）：
#   cp scripts/backup_kb.sh /opt/self-evolving-kb/backup_kb.sh
#   chmod +x /opt/self-evolving-kb/backup_kb.sh
#   crontab -e   # 加一行，每天凌晨 3:30：
#   30 3 * * * /opt/self-evolving-kb/backup_kb.sh >> /opt/self-evolving-kb/backups/backup.log 2>&1
#
set -euo pipefail

COMPOSE_DIR="${COMPOSE_DIR:-/opt/self-evolving-kb/SelfEvolvingKnowledgeBase}"
BACKUP_DIR="${BACKUP_DIR:-/opt/self-evolving-kb/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
# 至少保留的最新份数（跨天兜底：历史很短时不要把备份删到只剩 1 份）
RETENTION_MIN_KEEP="${RETENTION_MIN_KEEP:-2}"
# PRUNE_DRY_RUN=1 → 只列出「将要删除哪些备份」，一个都不删（删生产备份前先预演）
PRUNE_DRY_RUN="${PRUNE_DRY_RUN:-0}"
# --prune-only → 只清理旧备份，不做备份
PRUNE_ONLY=false
for _arg in "$@"; do
    case "${_arg}" in
        --prune-only) PRUNE_ONLY=true ;;
        -h|--help)
            echo "用法: $0 [--prune-only]"
            echo "  默认        备份 sekb_data + sekb_gitea_data，然后按策略清理旧备份"
            echo "  --prune-only 跳过备份，只清理旧备份（磁盘紧张时用）"
            echo "环境变量: RETENTION_DAYS=14  RETENTION_MIN_KEEP=2  PRUNE_DRY_RUN=1"
            echo "          BACKUP_DIR=<备份目录>  COMPOSE_DIR=<仓库目录>"
            exit 0 ;;
        *) echo "未知参数: ${_arg}（试 $0 --help）" >&2; exit 2 ;;
    esac
done
VOLUME_NAME="sekb_data"
GITEA_VOLUME_NAME="sekb_gitea_data"
TS="$(date +%Y%m%d_%H%M%S)"
BACKUP_FILE="${BACKUP_DIR}/sekb_data_${TS}.tar.gz"
GITEA_BACKUP_FILE="${BACKUP_DIR}/sekb_gitea_data_${TS}.tar.gz"

mkdir -p "${BACKUP_DIR}"
cd "${COMPOSE_DIR}"

# 兜底：无论脚本成功/失败退出，都确保 backend 与 gitea 已启动（start 幂等）
trap 'echo "[$(date +%Y%m%d_%H%M%S)] 兜底：确保 backend / gitea 已启动"; \
       docker compose -f docker-compose.prod.yml --env-file .env.prod start backend 2>/dev/null || true; \
       docker compose -f docker-compose.monitoring.yml start gitea 2>/dev/null || true' EXIT

# ------------------------------------------------------------
# 清理逻辑（函数定义提前到此处，好让 --prune-only 能在备份之前直接调用）
# ------------------------------------------------------------

prune_backups() {
    local pattern="$1" label="$2"
    local f day kept=0 removed=0 freed_kb=0 size_kb
    local seen_days=","
    local -a doomed=()

    # ls 按文件名倒序 = 按时间戳倒序（文件名内嵌 %Y%m%d_%H%M%S），最新在前。
    # 注：只用 bash 3.2 就有的语法（macOS 自带 bash 3.2 无关联数组），
    #     便于在开发机上直接跑 docs/tmp/test_backup_retention.sh 验证。
    while IFS= read -r f; do
        [ -f "$f" ] || continue
        # 取文件名里的 8 位日期（必须锚定整串——不锚定只会替换第一处，
        # 结果 day 变成 "20260915_110825.tar.gz" 这种含时间的串，去重永远不生效）
        day="$(basename "$f" | sed -E 's/^.*_([0-9]{8})_[0-9]{6}\.tar\.gz$/\1/')"
        # 名字不符合约定时退化为「每个文件算独立一天」= 只受超龄规则约束，安全
        case "$day" in
            [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]) ;;
            *) day="$(basename "$f")" ;;
        esac

        # 规则 a：超龄
        if [ -n "$(find "$f" -maxdepth 0 -mtime "+${RETENTION_DAYS}" 2>/dev/null)" ]; then
            doomed+=("$f"); continue
        fi
        # 规则 b：同一天只留最新（保底 RETENTION_MIN_KEEP 份不受此限）
        case "$seen_days" in
            *",${day},"*)
                if [ "$kept" -ge "${RETENTION_MIN_KEEP}" ]; then
                    doomed+=("$f"); continue
                fi
                ;;
            *)
                seen_days="${seen_days}${day},"
                ;;
        esac
        kept=$(( kept + 1 ))
    done < <(ls -1 "${BACKUP_DIR}"/${pattern} 2>/dev/null | sort -r)

    for f in ${doomed[@]+"${doomed[@]}"}; do
        size_kb="$(du -k "$f" 2>/dev/null | cut -f1)" || size_kb=0
        if [ "${PRUNE_DRY_RUN}" = "1" ]; then
            echo "    [dry-run] 将删除 $(basename "$f")"
        else
            rm -f "$f"
        fi
        removed=$(( removed + 1 ))
        freed_kb=$(( freed_kb + ${size_kb:-0} ))
    done

    # 注意：这里必须用 if，不能用 `[ ... ] && verb=...`
    # —— set -e 下条件不成立会让整个脚本退出（经典坑）
    local verb="删除"
    if [ "${PRUNE_DRY_RUN}" = "1" ]; then
        verb="将删除（dry-run，实际未删）"
    fi
    if [ "$removed" -gt 0 ]; then
        if [ "$freed_kb" -ge 1024 ]; then
            echo "[$(date +%Y%m%d_%H%M%S)] 清理 ${label}: ${verb} ${removed} 份，可释放 $(( freed_kb / 1024 )) MB（保留 ${kept} 份）"
        else
            echo "[$(date +%Y%m%d_%H%M%S)] 清理 ${label}: ${verb} ${removed} 份，可释放 ${freed_kb} KB（保留 ${kept} 份）"
        fi
    else
        echo "[$(date +%Y%m%d_%H%M%S)] 清理 ${label}: 无需清理（保留 ${kept} 份）"
    fi
}

# ------------------------------------------------------------
# --prune-only：磁盘紧张时只清理旧备份，不做备份
#   （备份会停 backend + 打包 112MB，只为腾空间时纯属多余）
# ------------------------------------------------------------
if [ "${PRUNE_ONLY}" = true ]; then
    echo "[${TS}] --prune-only：跳过备份阶段，仅清理旧备份（干跑=${PRUNE_DRY_RUN}）"
    prune_backups 'sekb_data_*.tar.gz' 'SEKB 数据'
    prune_backups 'sekb_gitea_data_*.tar.gz' 'Gitea 数据'
    echo "[${TS}] 清理后备份目录占用: $(du -sh "${BACKUP_DIR}" | cut -f1)"
    exit 0
fi

# ------------------------------------------------------------
# 1) SEKB 应用数据卷（sekb_data）
# ------------------------------------------------------------
echo "[${TS}] 停止 backend 以获取一致快照 ..."
docker compose -f docker-compose.prod.yml --env-file .env.prod stop backend

echo "[${TS}] 打包数据卷 ${VOLUME_NAME} ..."
docker run --rm \
  -v "${VOLUME_NAME}:/data:ro" \
  -v "${BACKUP_DIR}:/backup" \
  alpine tar czf "/backup/sekb_data_${TS}.tar.gz" -C /data .

echo "[${TS}] 重启 backend ..."
docker compose -f docker-compose.prod.yml --env-file .env.prod start backend

# ------------------------------------------------------------
# 2) Gitea 数据卷（sekb_gitea_data）—— 版本管理权威源，必须备份
#    Gitea 未部署时静默跳过（卷不存在则 docker run 会失败，故先探测）
# ------------------------------------------------------------
if docker volume inspect "${GITEA_VOLUME_NAME}" >/dev/null 2>&1; then
  echo "[${TS}] 停止 gitea 以获取一致快照 ..."
  docker compose -f docker-compose.monitoring.yml stop gitea

  echo "[${TS}] 打包数据卷 ${GITEA_VOLUME_NAME} ..."
  docker run --rm \
    -v "${GITEA_VOLUME_NAME}:/data:ro" \
    -v "${BACKUP_DIR}:/backup" \
    alpine tar czf "/backup/sekb_gitea_data_${TS}.tar.gz" -C /data .

  echo "[${TS}] 重启 gitea ..."
  docker compose -f docker-compose.monitoring.yml start gitea
else
  echo "[${TS}] 跳过 Gitea 备份（卷 ${GITEA_VOLUME_NAME} 不存在）"
fi

# ------------------------------------------------------------
# 3) 清理旧备份
#
# 两条规则叠加：
#   a) 超过 RETENTION_DAYS 天            → 删（原有规则）
#   b) 同一天只留最新一份                 → 删（去重，本条是后加的）
#
# 为什么必须要 b)：deploy.sh 每次部署前都会调用本脚本做一次备份，**一天部署 N 次
# 就留 N 份近乎完全相同的数据**；而天数规则永远管不住同一天的多份。
# 2026-09-15 就是实例：当天积了 9 份 sekb_data（每份 112MB、合计约 1.0G），
# 把 59G 磁盘挤到 24G 可用，低于 deploy.sh 的 25G 构建门禁，部署直接卡住。
#
# 保底：无论规则 b) 怎么去重，最新 RETENTION_MIN_KEEP 份永远保留——
# 防止「同一天做了很多次备份」时被削到只剩 1 份（单份损坏即无备份）。
# ------------------------------------------------------------

echo "[${TS}] 清理旧备份（>${RETENTION_DAYS} 天 + 同天去重，最少保留 ${RETENTION_MIN_KEEP} 份）..."
prune_backups 'sekb_data_*.tar.gz' 'SEKB 数据'
prune_backups 'sekb_gitea_data_*.tar.gz' 'Gitea 数据'
echo "[${TS}] 清理后备份目录占用: $(du -sh "${BACKUP_DIR}" | cut -f1)"

echo "[${TS}] 备份完成: ${BACKUP_FILE} ($(du -h "${BACKUP_FILE}" | cut -f1))"
if [ -f "${GITEA_BACKUP_FILE}" ]; then
  echo "[${TS}] Gitea 备份完成: ${GITEA_BACKUP_FILE} ($(du -h "${GITEA_BACKUP_FILE}" | cut -f1))"
fi
