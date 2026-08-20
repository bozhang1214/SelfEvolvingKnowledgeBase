#!/bin/bash
# ============================================================
# SEKB 数据恢复脚本（Phase 4 P0-8）
# ============================================================
# 从指定备份恢复数据，支持选择性恢复（仅数据 / 仅数据库 / 全部）
#
# 使用方式：
#   # 列出可用备份
#   ./deploy/restore.sh --list
#
#   # 恢复最新备份（仅数据，不含数据库）
#   ./deploy/restore.sh --latest
#
#   # 恢复指定日期的备份
#   ./deploy/restore.sh --date 20240115_030000
#
#   # 恢复指定备份（含 PostgreSQL）
#   ./deploy/restore.sh --date 20240115_030000 --with-db
#
#   # 从 S3 下载并恢复
#   ./deploy/restore.sh --date 20240115_030000 --from-s3
#
# 危险：恢复会覆盖现有数据，请在低峰期执行
# ============================================================

set -euo pipefail

# ---------- 配置 ----------
BACKUP_DIR="${BACKUP_DIR:-/backup}"
S3_BUCKET="${S3_BUCKET:-}"
AWS_PROFILE="${AWS_PROFILE:-default}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# ---------- 参数解析 ----------
RESTORE_DATE=""
RESTORE_LATEST=false
WITH_DB=false
FROM_S3=false
LIST_ONLY=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --date)      RESTORE_DATE="$2"; shift 2 ;;
        --latest)    RESTORE_LATEST=true; shift ;;
        --with-db)   WITH_DB=true; shift ;;
        --from-s3)   FROM_S3=true; shift ;;
        --list)      LIST_ONLY=true; shift ;;
        -h|--help)
            grep '^#' "$0" | head -20
            exit 0
            ;;
        *) echo "未知参数: $1"; exit 1 ;;
    esac
done

# ---------- 工具函数 ----------
log() { echo "[restore] $*"; }
fail() { echo "[restore] FATAL: $*" >&2; exit 1; }

# ---------- 列出备份 ----------
if [ "${LIST_ONLY}" = true ]; then
    log "可用备份列表（${BACKUP_DIR}）："
    if [ -d "${BACKUP_DIR}" ]; then
        for d in "${BACKUP_DIR}"/*/; do
            [ -d "$d" ] || continue
            date=$(basename "$d")
            size=$(du -sh "$d" 2>/dev/null | cut -f1)
            manifest=""
            [ -f "$d/MANIFEST.md" ] && manifest="✓" || manifest="✗"
            echo "  ${date}  size=${size}  manifest=${manifest}"
        done
    else
        log "备份目录不存在: ${BACKUP_DIR}"
    fi
    exit 0
fi

# ---------- 确定恢复源 ----------
if [ "${RESTORE_LATEST}" = true ]; then
    RESTORE_PATH=$(ls -dt "${BACKUP_DIR}"/*/ 2>/dev/null | head -1)
    RESTORE_DATE=$(basename "${RESTORE_PATH%/}")
elif [ -n "${RESTORE_DATE}" ]; then
    RESTORE_PATH="${BACKUP_DIR}/${RESTORE_DATE}"
else
    fail "请指定 --date <备份日期> 或 --latest"
fi

# ---------- 从 S3 下载 ----------
if [ "${FROM_S3}" = true ]; then
    if [ -z "${S3_BUCKET}" ]; then fail "S3_BUCKET 未设置"; fi
    if [ ! -d "${RESTORE_PATH}" ]; then
        log "从 S3 下载: s3://${S3_BUCKET}/${RESTORE_DATE}/"
        mkdir -p "${RESTORE_PATH}"
        AWS_PROFILE="${AWS_PROFILE}" aws s3 sync \
            "s3://${S3_BUCKET}/${RESTORE_DATE}/" \
            "${RESTORE_PATH}" --no-progress
    else
        log "本地已存在 ${RESTORE_DATE}，跳过 S3 下载"
    fi
fi

# ---------- 前置检查 ----------
[ -d "${RESTORE_PATH}" ] || fail "备份目录不存在: ${RESTORE_PATH}"
log "恢复源: ${RESTORE_PATH}"

# ---------- 安全确认 ----------
echo ""
echo "============================================"
echo "  ⚠️  警告：恢复将覆盖现有数据！"
echo "  备份日期: ${RESTORE_DATE}"
echo "  含数据库: ${WITH_DB}"
echo "  建议先停止后端服务: docker compose -f ${COMPOSE_FILE} stop backend"
echo "============================================"
read -p "确认恢复？输入 yes 继续: " confirm
[ "${confirm}" = "yes" ] || fail "用户取消"

cd "${PROJECT_DIR}"

# ---------- 1. 恢复 PostgreSQL ----------
if [ "${WITH_DB}" = true ] && [ -f "${RESTORE_PATH}/postgres.sql.gz" ]; then
    log "恢复 PostgreSQL..."
    gunzip -c "${RESTORE_PATH}/postgres.sql.gz" | \
        docker compose -f "${COMPOSE_FILE}" exec -T postgres \
            psql -U "${DB_USER:-sekb}" "${DB_NAME:-sekb}"
    log "PostgreSQL 恢复完成"
fi

# ---------- 2. 恢复 Redis ----------
if [ "${WITH_DB}" = true ] && [ -f "${RESTORE_PATH}/redis.rdb" ]; then
    log "恢复 Redis..."
    log "注意：Redis RDB 恢复需要停止 Redis 容器后替换文件"
    docker compose -f "${COMPOSE_FILE}" stop redis
    docker compose -f "${COMPOSE_FILE}" cp "${RESTORE_PATH}/redis.rdb" redis:/data/dump.rdb
    docker compose -f "${COMPOSE_FILE}" start redis
    log "Redis 恢复完成"
fi

# ---------- 3. 恢复应用数据（ChromaDB + 会话 + 配置）----------
if [ -f "${RESTORE_PATH}/app_data.tar.gz" ]; then
    log "恢复应用数据..."
    # 解压到临时目录
    TMP_DIR=$(mktemp -d)
    tar xzf "${RESTORE_PATH}/app_data.tar.gz" -C "${TMP_DIR}"

    # 停止后端避免写入冲突
    log "停止后端服务..."
    docker compose -f "${COMPOSE_FILE}" stop backend

    # 复制数据到后端卷
    log "恢复数据到卷..."
    docker run --rm \
        -v "$(docker compose -f "${COMPOSE_FILE}" config --volumes 2>/dev/null | grep sekb_data | head -1):/dest" \
        -v "${TMP_DIR}/app_data:/src:ro" \
        alpine sh -c "rm -rf /dest/* && cp -a /src/. /dest/"

    # 清理临时目录
    rm -rf "${TMP_DIR}"

    log "重启后端服务..."
    docker compose -f "${COMPOSE_FILE}" start backend
    log "应用数据恢复完成"
else
    fail "未找到 app_data.tar.gz"
fi

# ---------- 4. 验证 ----------
log "等待后端启动..."
sleep 20

HEALTH=$(curl -sf http://localhost:8000/api/v1/health/live -o /dev/null -w "%{http_code}" 2>/dev/null || echo "fail")
if [ "${HEALTH}" = "200" ]; then
    log "恢复成功！后端健康检查通过"
else
    log "警告：后端健康检查未通过（${HEALTH}），请检查日志"
    log "查看日志: docker compose -f ${COMPOSE_FILE} logs backend --tail 50"
fi

log "恢复完成: ${RESTORE_DATE}"
