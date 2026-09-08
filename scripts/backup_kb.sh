#!/usr/bin/env bash
#
# 知识库每日备份脚本（L2 兜底）。
#
# 做法：短暂停 backend 拿到一致快照 → 打包整个 sekb_data 数据卷 →
#       重启 backend → 按保留天数清理旧备份。
#
# 数据卷 sekb_data 内含：chroma_db（向量库）、uploads/documents（原始文件）、
# uploaded_md5.json、shares.json、conversations、news 等全部知识资产。
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
TS="$(date +%Y%m%d_%H%M%S)"
BACKUP_FILE="${BACKUP_DIR}/sekb_data_${TS}.tar.gz"

mkdir -p "${BACKUP_DIR}"
cd "${COMPOSE_DIR}"

echo "[${TS}] 停止 backend 以获取一致快照 ..."
docker compose -f docker-compose.prod.yml --env-file .env.prod stop backend

echo "[${TS}] 打包数据卷 ${VOLUME_NAME} ..."
docker run --rm \
  -v "${VOLUME_NAME}:/data:ro" \
  -v "${BACKUP_DIR}:/backup" \
  alpine tar czf "/backup/sekb_data_${TS}.tar.gz" -C /data .

echo "[${TS}] 重启 backend ..."
docker compose -f docker-compose.prod.yml --env-file .env.prod start backend

echo "[${TS}] 清理 ${RETENTION_DAYS} 天前的旧备份 ..."
find "${BACKUP_DIR}" -name 'sekb_data_*.tar.gz' -mtime "+${RETENTION_DAYS}" -delete

echo "[${TS}] 备份完成: ${BACKUP_FILE} ($(du -h "${BACKUP_FILE}" | cut -f1))"
