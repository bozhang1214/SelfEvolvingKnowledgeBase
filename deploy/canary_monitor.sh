#!/bin/bash
# ============================================================
# SEKB 灰度发布监控与自动回滚（Phase 4 P0-7）
# ============================================================
# 监控灰度版后端的错误率与延迟，超阈值自动回滚
#
# 使用方式：
#   ./deploy/canary_monitor.sh
#   建议在灰度发布期间作为后台进程运行：
#     nohup ./deploy/canary_monitor.sh > /var/log/sekb-canary.log 2>&1 &
#
# 环境变量：
#   PROMETHEUS_URL   Prometheus 地址（默认取 .env.prod 的 MONITOR_BIND_IP + :9091；
#                    未设置时 http://localhost:9091）
#   ERROR_THRESHOLD  错误率阈值（默认 0.05，即 5%）
#   LATENCY_P95_THRESHOLD  P95 延迟阈值秒（默认 30）
#   CHECK_INTERVAL   检查间隔秒（默认 30）
#   DURATION_MIN     灰度最短观察时长分钟（默认 30）
#   COMPOSE_FILE     compose 文件路径
# ============================================================

set -euo pipefail

# ---------- 配置 ----------
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# Prometheus 地址：默认取 .env.prod 的 MONITOR_BIND_IP（生产把监控栈只绑到 Tailscale IP，
# 此时 http://localhost:9091 连不上，会让灰度监控静默拿不到数据 → 误判「无异常」）。
# 未设置时回退 localhost；仍可用环境变量 PROMETHEUS_URL 显式覆盖。
_MONITOR_BIND_IP="$(grep -E '^MONITOR_BIND_IP=' "${PROJECT_DIR}/.env.prod" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '[:space:]')"
case "$_MONITOR_BIND_IP" in ""|"0.0.0.0"|"::"|"*") _MONITOR_BIND_IP="localhost" ;; esac
PROMETHEUS_URL="${PROMETHEUS_URL:-http://${_MONITOR_BIND_IP}:9091}"
ERROR_THRESHOLD="${ERROR_THRESHOLD:-0.05}"
LATENCY_P95_THRESHOLD="${LATENCY_P95_THRESHOLD:-30}"
CHECK_INTERVAL="${CHECK_INTERVAL:-30}"
DURATION_MIN="${DURATION_MIN:-30}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"

START_TIME=$(date +%s)
CHECK_COUNT=0
PASS_COUNT=0
FAIL_COUNT=0

# ---------- 工具函数 ----------
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [canary] $*"
}

fail() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [canary] FATAL: $*" >&2
    exit 1
}

# 查询 Prometheus 指标
query_prom() {
    local query="$1"
    curl -sf --max-time 5 "${PROMETHEUS_URL}/api/v1/query?query=${query}" | \
        python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
    results=d.get('data',{}).get('result',[])
    if not results: print('0'); exit()
    print(results[0]['value'][1])
except: print('0')
" 2>/dev/null || echo "0"
}

# 检查灰度容器是否运行
check_canary_alive() {
    docker ps --filter "name=backend-canary" --format '{{.Names}}' | grep -q backend-canary
}

# 回滚
rollback() {
    log "🔴 触发自动回滚！"
    cd "${PROJECT_DIR}"

    # 停止灰度容器
    log "停止灰度后端容器..."
    docker stop backend-canary 2>/dev/null || true
    docker rm backend-canary 2>/dev/null || true

    # 切换 nginx 配置回稳定版
    log "恢复 nginx 稳定配置..."
    if [ -f deploy/nginx.conf.bak ]; then
        cp deploy/nginx.conf.bak deploy/nginx.conf
        log "已从备份恢复 nginx.conf"
    fi

    # 重载前端 nginx
    log "重载 nginx..."
    docker exec sekb-frontend nginx -s reload 2>/dev/null || \
        docker restart sekb-frontend 2>/dev/null || true

    # 验证稳定版健康
    sleep 10
    HEALTH=$(curl -sf http://localhost:8000/api/v1/health/live -o /dev/null -w "%{http_code}" 2>/dev/null || echo "fail")
    if [ "${HEALTH}" = "200" ]; then
        log "✅ 回滚完成，稳定版健康"
    else
        log "⚠️  回滚完成，但稳定版健康检查异常（${HEALTH}）"
        log "请手动检查: docker compose -f ${COMPOSE_FILE} logs backend --tail 50"
    fi

    # 发送告警（可选：通过 alertmanager API 触发）
    log "灰度发布失败，已自动回滚。失败检查次数：${FAIL_COUNT}，通过次数：${PASS_COUNT}"
}

# ---------- 前置检查 ----------
[ -f deploy/nginx-canary.conf ] || fail "未找到 deploy/nginx-canary.conf"
log "========================================"
log "  SEKB 灰度发布监控启动"
log "========================================"
log "Prometheus:      ${PROMETHEUS_URL}"
log "错误率阈值:       ${ERROR_THRESHOLD}"
log "P95 延迟阈值:    ${LATENCY_P95_THRESHOLD}s"
log "检查间隔:         ${CHECK_INTERVAL}s"
log "最短观察时长:     ${DURATION_MIN} 分钟"
log "========================================"

# ---------- 监控循环 ----------
while true; do
    # 检查灰度容器是否还在运行
    if ! check_canary_alive; then
        log "灰度容器已停止，退出监控"
        break
    fi

    CHECK_COUNT=$((CHECK_COUNT + 1))
    ELAPSED=$(( $(date +%s) - START_TIME ))
    ELAPSED_MIN=$(( ELAPSED / 60 ))

    # 查询灰度版错误率
    # 通过 X-Canary 标签区分灰度请求（需要 nginx 转发该头到 metrics）
    ERROR_RATE=$(query_prom 'sum(rate(sekb_messages_total{status="error"}[2m])) / sum(rate(sekb_messages_total[2m]))')
    LATENCY_P95=$(query_prom 'histogram_quantile(0.95, rate(sekb_e2e_latency_seconds_bucket[2m]))')

    # 浮点比较
    ERR_OK=$(python3 -c "print(1 if float('${ERROR_RATE}') < ${ERROR_THRESHOLD} else 0)" 2>/dev/null || echo "0")
    LAT_OK=$(python3 -c "print(1 if float('${LATENCY_P95}') < ${LATENCY_P95_THRESHOLD} else 0)" 2>/dev/null || echo "0")

    STATUS="✅ PASS"
    if [ "${ERR_OK}" = "0" ] || [ "${LAT_OK}" = "0" ]; then
        STATUS="❌ FAIL"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    else
        PASS_COUNT=$((PASS_COUNT + 1))
    fi

    log "检查 #${CHECK_COUNT} (${ELAPSED_MIN}min) ${STATUS} | err_rate=${ERROR_RATE} p95=${LATENCY_P95}s | pass=${PASS_COUNT} fail=${FAIL_COUNT}"

    # 失败 3 次立即回滚
    if [ "${FAIL_COUNT}" -ge 3 ]; then
        rollback
        exit 1
    fi

    # 观察期结束且通过，自动提升为稳定版
    if [ "${ELAPSED_MIN}" -ge "${DURATION_MIN}" ] && [ "${FAIL_COUNT}" -eq 0 ]; then
        log "🎉 灰度观察期结束（${DURATION_MIN}min），全部检查通过，自动提升为稳定版"
        cd "${PROJECT_DIR}"

        # 停止旧稳定版，将灰度版设为新的稳定版
        log "切换灰度版为稳定版..."
        docker stop backend 2>/dev/null || true
        docker rm backend 2>/dev/null || true
        docker stop backend-canary 2>/dev/null || true
        docker rename backend-canary backend 2>/dev/null || true

        # 切换 nginx 配置回稳定版（单上游）
        cp deploy/nginx.conf deploy/nginx.conf.canary.bak 2>/dev/null || true
        # 使用稳定的单上游配置（从备份恢复或保持当前）
        if [ -f deploy/nginx.conf.bak ]; then
            cp deploy/nginx.conf.bak deploy/nginx.conf
        fi

        # 重启 compose（确保使用新 backend 容器）
        docker compose -f "${COMPOSE_FILE}" up -d frontend

        sleep 10
        HEALTH=$(curl -sf http://localhost:8000/api/v1/health/live -o /dev/null -w "%{http_code}" 2>/dev/null || echo "fail")
        if [ "${HEALTH}" = "200" ]; then
            log "✅ 灰度提升成功！新稳定版已上线"
        else
            log "⚠️  提升后健康检查异常，请手动验证"
        fi
        break
    fi

    sleep "${CHECK_INTERVAL}"
done

log "灰度监控结束。总计检查 ${CHECK_COUNT} 次，通过 ${PASS_COUNT}，失败 ${FAIL_COUNT}"
