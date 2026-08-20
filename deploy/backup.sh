#!/bin/bash
# ============================================================
# SEKB 数据备份脚本（Phase 4 P0-8）
# ============================================================
# 备份内容：PostgreSQL（可选）/ Redis（可选）/ ChromaDB / 会话数据 / 配置
# 保留策略：本地保留 7 天，S3 保留 30 天
#
# 使用方式：
#   ./deploy/backup.sh
#
# 推荐配合 cron 定时执行（每日 03:00）：
#   0 3 * * * cd /opt/self-evolving-kb && ./deploy/backup.sh >> /var/log/sekb-backup.log 2>&1
#
# 环境变量：
#   BACKUP_DIR       备份根目录（默认 /backup）
#   RETENTION_DAYS   本地保留天数（默认 7）
#   S3_BUCKET        S3 存储桶（留空则跳过 S3 上传）
#   AWS_PROFILE      AWS CLI profile（可选）
#   COMPOSE_FILE     compose 文件路径（默认 docker-compose.prod.yml）
# ============================================================

set -euo pipefail

# ---------- 配置 ----------
BACKUP_DIR="${BACKUP_DIR:-/backup}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
S3_BUCKET="${S3_BUCKET:-}"
AWS_PROFILE="${AWS_PROFILE:-default}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_PATH="${BACKUP_DIR}/${DATE}"
LOG_PREFIX="[backup ${DATE}]"

# ---------- 工具函数 ----------
log() {
    echo "${LOG_PREFIX} $*"
}

fail() {
    echo "${LOG_PREFIX} FATAL: $*" >&2
    exit 1
}

# ---------- 前置检查 ----------
mkdir -p "${BACKUP_PATH}"
log "备份开始，目标目录: ${BACKUP_PATH}"
cd "${PROJECT_DIR}"

# ---------- 1. PostgreSQL 备份（仅在使用 postgres profile 时）----------
if docker compose -f "${COMPOSE_FILE}" ps postgres 2>/dev/null | grep -q "Up"; then
    log "备份 PostgreSQL..."
    docker compose -f "${COMPOSE_FILE}" exec -T postgres \
        pg_dump -U "${DB_USER:-sekb}" "${DB_NAME:-sekb}" \
        > "${BACKUP_PATH}/postgres.sql"
    gzip "${BACKUP_PATH}/postgres.sql"
    log "PostgreSQL 备份完成: postgres.sql.gz ($(du -h "${BACKUP_PATH}/postgres.sql.gz" | cut -f1))"
else
    log "PostgreSQL 未运行，跳过数据库备份"
fi

# ---------- 2. Redis 备份（仅在使用 with-db profile 时）----------
if docker compose -f "${COMPOSE_FILE}" ps redis 2>/dev/null | grep -q "Up"; then
    log "备份 Redis..."
    # 触发 RDB 快照
    docker compose -f "${COMPOSE_FILE}" exec -T redis redis-cli BGSAVE >/dev/null
    # 等待快照完成（最多 30 秒）
    for i in $(seq 1 30); do
        LAST_SAVE=$(docker compose -f "${COMPOSE_FILE}" exec -T redis redis-cli LASTSAVE)
        sleep 1
        NEW_SAVE=$(docker compose -f "${COMPOSE_FILE}" exec -T redis redis-cli LASTSAVE)
        if [ "${LAST_SAVE}" != "${NEW_SAVE}" ]; then
            break
        fi
    done
    # 复制 RDB 文件
    docker compose -f "${COMPOSE_FILE}" cp redis:/data/dump.rdb "${BACKUP_PATH}/redis.rdb" 2>/dev/null || \
        log "Redis RDB 复制失败（可能无数据）"
    log "Redis 备份完成"
else
    log "Redis 未运行，跳过 Redis 备份"
fi

# ---------- 3. ChromaDB + 会话数据备份（始终执行）----------
log "备份 ChromaDB + 会话数据..."
# 从后端容器复制整个 data 目录（含 chroma_db、conversations、traces 等）
docker compose -f "${COMPOSE_FILE}" cp backend:/app/data "${BACKUP_PATH}/app_data" 2>/dev/null || {
    log "容器数据复制失败，尝试从卷直接复制"
    # 回退：直接从命名卷复制
    VOLUME_NAME=$(docker compose -f "${COMPOSE_FILE}" config --volumes 2>/dev/null | grep sekb_data | head -1)
    if [ -n "${VOLUME_NAME}" ]; then
        docker run --rm -v "${VOLUME_NAME}:/source:ro" -v "${BACKUP_PATH}/app_data:/dest" \
            alpine sh -c "cp -a /source/. /dest/"
    fi
}

# 打包压缩数据目录
if [ -d "${BACKUP_PATH}/app_data" ]; then
    tar czf "${BACKUP_PATH}/app_data.tar.gz" -C "${BACKUP_PATH}" app_data
    rm -rf "${BACKUP_PATH}/app_data"
    log "数据备份完成: app_data.tar.gz ($(du -h "${BACKUP_PATH}/app_data.tar.gz" | cut -f1))"
fi

# ---------- 4. 配置文件备份 ----------
log "备份配置文件..."
mkdir -p "${BACKUP_PATH}/config"
cp -r deploy/ "${BACKUP_PATH}/config/deploy/" 2>/dev/null || true
cp config.yaml "${BACKUP_PATH}/config/" 2>/dev/null || true
cp docker-compose.prod.yml "${BACKUP_PATH}/config/" 2>/dev/null || true
cp .env.prod "${BACKUP_PATH}/config/" 2>/dev/null || log "无 .env.prod 文件"
log "配置备份完成"

# ---------- 5. 生成备份清单 ----------
{
    echo "# SEKB Backup Manifest"
    echo "date: ${DATE}"
    echo "host: $(hostname)"
    echo "git_sha: $(git rev-parse --short HEAD 2>/dev/null || echo 'unknown')"
    echo ""
    echo "## Files"
    find "${BACKUP_PATH}" -type f -exec ls -lh {} \; | awk '{print $5, $9}'
} > "${BACKUP_PATH}/MANIFEST.md"
log "备份清单已生成"

# ---------- 6. 上传到 S3（可选）----------
if [ -n "${S3_BUCKET}" ]; then
    log "上传到 S3: s3://${S3_BUCKET}/${DATE}/"
    if command -v aws >/dev/null 2>&1; then
        AWS_PROFILE="${AWS_PROFILE}" aws s3 sync \
            "${BACKUP_PATH}" \
            "s3://${S3_BUCKET}/${DATE}/" \
            --no-progress
        log "S3 上传完成"

        # 清理 S3 上 30 天前的备份
        log "清理 S3 上 30 天前的备份..."
        AWS_PROFILE="${AWS_PROFILE}" aws s3 ls "s3://${S3_BUCKET}/" | \
            awk '{print $2}' | while read -r prefix; do
            # 解析日期前缀（YYYYMMDD_HHMMSS/）
            backup_date=$(echo "${prefix}" | grep -oE '^[0-9]{8}' || true)
            if [ -n "${backup_date}" ]; then
                cutoff=$(date -d "-30 days" +%Y%m%d 2>/dev/null || date -v-30d +%Y%m%d)
                if [ "${backup_date}" -lt "${cutoff}" ]; then
                    AWS_PROFILE="${AWS_PROFILE}" aws s3 rm "s3://${S3_BUCKET}/${prefix}" --recursive
                    log "已删除 S3 过期备份: ${prefix}"
                fi
            fi
        done
    else
        log "aws CLI 未安装，跳过 S3 上传"
    fi
fi

# ---------- 7. 清理本地过期备份 ----------
log "清理本地 ${RETENTION_DAYS} 天前的备份..."
find "${BACKUP_DIR}" -maxdepth 1 -type d -name "20*" -mtime +${RETENTION_DAYS} -exec rm -rf {} \; 2>/dev/null || true
log "本地清理完成"

# ---------- 完成 ----------
BACKUP_SIZE=$(du -sh "${BACKUP_PATH}" | cut -f1)
log "备份完成！总大小: ${BACKUP_SIZE}"
log "备份路径: ${BACKUP_PATH}"
