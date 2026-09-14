#!/usr/bin/env bash
#
# 知识库每日备份脚本（L2 兜底）。
#
# 做法：短暂停 backend / gitea 拿到一致快照 → 分别打包 sekb_data 与
#       sekb_gitea_data 数据卷 → 重启服务 → 按保留天数清理旧备份。
#
# 数据卷 sekb_data 内含：chroma_db（向量库）、uploads/documents（原始文件）、
# uploaded_md5.json、shares.json、conversations、news 等全部知识资产。
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

COMPOSE_DIR="/opt/self-evolving-kb/SelfEvolvingKnowledgeBase"
BACKUP_DIR="/opt/self-evolving-kb/backups"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
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
# ------------------------------------------------------------
echo "[${TS}] 清理 ${RETENTION_DAYS} 天前的旧备份 ..."
find "${BACKUP_DIR}" -name 'sekb_data_*.tar.gz' -mtime "+${RETENTION_DAYS}" -delete
find "${BACKUP_DIR}" -name 'sekb_gitea_data_*.tar.gz' -mtime "+${RETENTION_DAYS}" -delete

echo "[${TS}] 备份完成: ${BACKUP_FILE} ($(du -h "${BACKUP_FILE}" | cut -f1))"
if [ -f "${GITEA_BACKUP_FILE}" ]; then
  echo "[${TS}] Gitea 备份完成: ${GITEA_BACKUP_FILE} ($(du -h "${GITEA_BACKUP_FILE}" | cut -f1))"
fi
