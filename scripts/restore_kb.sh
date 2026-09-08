#!/usr/bin/env bash
#
# 知识库数据卷恢复脚本（配合 backup_kb.sh）。
#
# 用法：restore_kb.sh <backup_file.tar.gz>
#   1. 停止 backend
#   2. 清空 sekb_data 数据卷并解包备份
#   3. 重启 backend（trap 兜底确保即使恢复中途失败也拉起服务）
#
# 注意：
#   - 恢复会**覆盖**当前数据卷内容，请确认已无未备份的新数据；
#   - 备份文件通常位于 /opt/self-evolving-kb/backups/sekb_data_YYYYMMDD_HHMMSS.tar.gz
#
set -euo pipefail

COMPOSE_DIR="/opt/self-evolving-kb/SelfEvolvingKnowledgeBase"
VOLUME_NAME="sekb_data"

if [ $# -lt 1 ]; then
  echo "用法: $0 <backup_file.tar.gz>"
  exit 1
fi

BACKUP_FILE="$1"
if [ ! -f "${BACKUP_FILE}" ]; then
  echo "备份文件不存在: ${BACKUP_FILE}"
  exit 1
fi
BACKUP_ABS="$(cd "$(dirname "${BACKUP_FILE}")" && pwd)/$(basename "${BACKUP_FILE}")"
BACKUP_DIR_ABS="$(dirname "${BACKUP_ABS}")"

cd "${COMPOSE_DIR}"

# 兜底：无论恢复成功/失败，退出前都确保 backend 已启动
trap 'echo "[$(date +%Y%m%d_%H%M%S)] 兜底：确保 backend 已启动"; \
       docker compose -f docker-compose.prod.yml --env-file .env.prod start backend 2>/dev/null || true' EXIT

echo "停止 backend ..."
docker compose -f docker-compose.prod.yml --env-file .env.prod stop backend

echo "清空数据卷 ${VOLUME_NAME} ..."
docker run --rm -v "${VOLUME_NAME}:/data" alpine sh -c 'rm -rf /data/* /data/.[!.]* /data/..?* 2>/dev/null || true'

echo "解包备份 ${BACKUP_ABS} ..."
docker run --rm \
  -v "${VOLUME_NAME}:/data" \
  -v "${BACKUP_DIR_ABS}:/backup:ro" \
  alpine sh -c "tar xzf \"/backup/$(basename "${BACKUP_ABS}")\" -C /data"

echo "重启 backend ..."
docker compose -f docker-compose.prod.yml --env-file .env.prod start backend

echo "恢复完成: ${BACKUP_ABS}"
