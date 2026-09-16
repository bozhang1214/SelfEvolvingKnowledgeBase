#!/usr/bin/env bash
# ============================================================
# SEKB 全功能重启脚本 - 拉代码 + 重新构建 + 重启前后端
# ============================================================
# 用法：
#   bash deploy/restart.sh [选项] [目标]
#
# 目标（互斥，默认 all）：
#   all           重新构建并重启前后端 + 启动监控栈（默认，全功能）
#   frontend      仅重新构建并重启前端
#   backend       仅重新构建并重启后端
#   monitoring    启动/重启监控栈（Prometheus + Grafana + Loki + Promtail + Alertmanager）
#   fe, be        简写
#   mon, monitor  简写（同 monitoring）
#
# 选项：
#   --no-pull         跳过 git pull（默认会先拉取最新代码）
#   --no-build        跳过镜像构建，仅 restart 容器（适用于纯配置/环境变量变更）
#   --no-monitoring   不启动监控栈（仅 all 目标有效，用于只想重启应用栈）
#   --dry-run         仅打印命令不实际执行
#   --help, -h        显示帮助
#
# 示例：
#   bash deploy/restart.sh                          # 默认：前后端 + 监控栈一起启动
#   bash deploy/restart.sh backend                  # 仅拉代码 + 构建后端 + 重启后端
#   bash deploy/restart.sh monitoring               # 仅启动监控栈
#   bash deploy/restart.sh all --no-monitoring      # 仅应用栈（不启动监控栈）
#   bash deploy/restart.sh fe --no-pull             # 已手动 git pull，仅构建+重启前端
#   bash deploy/restart.sh all --no-build           # 拉代码但跳过构建（仅配置变更）
# ============================================================

set -euo pipefail

# ============ 颜色 ============
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

# ============ 路径与默认值 ============
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
COMPOSE_FILE="${PROJECT_ROOT}/docker-compose.prod.yml"
MONITORING_COMPOSE_FILE="${PROJECT_ROOT}/docker-compose.monitoring.yml"
ENV_FILE="${PROJECT_ROOT}/.env.prod"

TARGET="all"
DO_PULL=true
DO_BUILD=true
NO_MONITORING=false
DRY_RUN=false

# ============ 参数解析 ============
usage() {
    sed -n '3,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0
}

while [[ $# -gt 0 ]]; do
    case $1 in
        all|frontend|backend|fe|be|monitoring|mon|monitor)
            TARGET=$1
            shift
            ;;
        --no-pull)
            DO_PULL=false
            shift
            ;;
        --no-build)
            DO_BUILD=false
            shift
            ;;
        --no-monitoring)
            NO_MONITORING=true
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --help|-h)
            usage
            ;;
        *)
            echo -e "${RED}未知参数：$1${NC}"
            echo "运行 bash deploy/restart.sh --help 查看用法"
            exit 1
            ;;
    esac
done

# 归一化目标
case $TARGET in
    fe) TARGET="frontend" ;;
    be) TARGET="backend" ;;
    mon|monitor) TARGET="monitoring" ;;
esac

# ============ 工具函数 ============
info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
fail()    { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }

run() {
    if $DRY_RUN; then
        echo -e "${YELLOW}[DRY]${NC} $*"
    else
        "$@"
    fi
}

# 切换到项目根目录
cd "$PROJECT_ROOT"

# ============ 前置检查 ============
info "工作目录: $PROJECT_ROOT"
[[ -f "$COMPOSE_FILE" ]] || fail "找不到 $COMPOSE_FILE"
[[ -f "$ENV_FILE" ]]     || fail "找不到 $ENV_FILE（请复制 .env.prod.example 并配置）"

# 拆解目标
RESTART_FE=false
RESTART_BE=false
RESTART_MON=false
case $TARGET in
    all)        RESTART_FE=true; RESTART_BE=true; RESTART_MON=true ;;
    frontend)   RESTART_FE=true ;;
    backend)    RESTART_BE=true ;;
    monitoring) RESTART_MON=true ;;
    *)          fail "未知目标：$TARGET" ;;
esac

# --no-monitoring：默认 all 会带监控栈，此选项用于仅重启应用栈
if $NO_MONITORING; then
    RESTART_MON=false
fi

# ============ 步骤 1：git pull ============
if $DO_PULL; then
    info "拉取最新代码..."
    if $DRY_RUN; then
        echo -e "${YELLOW}[DRY]${NC} git pull"
    else
        git pull || fail "git pull 失败，请手动处理后重试"
        success "代码已是最新"
    fi
fi

# ============ 步骤 2：镜像构建（前后端都构建，确保代码变更生效） ============
if $DO_BUILD; then
    if $RESTART_FE; then
        info "构建前端镜像..."
        if $DRY_RUN; then
            echo -e "${YELLOW}[DRY]${NC} docker compose -f docker-compose.prod.yml --env-file .env.prod build frontend"
        else
            docker compose -f docker-compose.prod.yml --env-file .env.prod build frontend \
                || fail "前端镜像构建失败"
            success "前端镜像构建完成"
        fi
    fi

    if $RESTART_BE; then
        info "构建后端镜像（后端代码变更需要重新构建才能生效）..."
        if $DRY_RUN; then
            echo -e "${YELLOW}[DRY]${NC} docker compose -f docker-compose.prod.yml --env-file .env.prod build backend"
        else
            docker compose -f docker-compose.prod.yml --env-file .env.prod build backend \
                || fail "后端镜像构建失败"
            success "后端镜像构建完成"
        fi
    fi
else
    warn "跳过镜像构建（--no-build），仅 restart 容器"
fi

# ============ 步骤 3：重启容器（使用新构建的镜像） ============
if $RESTART_FE; then
    info "重启 frontend 容器..."
    run docker compose -f docker-compose.prod.yml --env-file .env.prod up -d frontend
    success "frontend 已重启"
fi

if $RESTART_BE; then
    info "重启 backend 容器..."
    run docker compose -f docker-compose.prod.yml --env-file .env.prod up -d backend
    success "backend 已重启"
fi

# ============ 步骤 3b：启动监控栈 ============
if $RESTART_MON; then
    [[ -f "$MONITORING_COMPOSE_FILE" ]] || fail "找不到 $MONITORING_COMPOSE_FILE"

    # 监控栈通过 external 网络 sekb_network 连接 backend，需应用栈先创建该网络
    if ! $DRY_RUN; then
        if ! docker network inspect sekb_network >/dev/null 2>&1; then
            warn "未检测到应用栈网络 sekb_network（监控栈需连接 backend 采集指标）"
            warn "请先启动应用栈：bash deploy/restart.sh backend  或  bash deploy/restart.sh all"
        fi
    fi

    info "启动监控栈（Prometheus/Grafana/Loki/Promtail/Alertmanager）..."
    run docker compose -f docker-compose.monitoring.yml --env-file .env.prod up -d
    success "监控栈已启动"
fi

# ============ 步骤 4：健康检查 ============
if $RESTART_FE || $RESTART_BE; then
    info "等待容器就绪并执行健康检查..."
    sleep 3
fi

check_health() {
    local name=$1
    local url=$2
    local max_tries=10
    local try=1
    local code="000"   # 初始化避免 set -u 触发 unbound variable
    if $DRY_RUN; then
        echo -e "${YELLOW}[DRY]${NC} 健康检查: $name ($url)"
        return 0
    fi
    while [[ $try -le $max_tries ]]; do
        code=$(curl -sf -o /dev/null -w "%{http_code}" --max-time 3 "$url" 2>/dev/null || echo "000")
        if [[ "$code" == "200" ]]; then
            success "$name 健康（${try}/${max_tries}） HTTP $code"
            return 0
        fi
        printf "  ${name} 等待中 %d/%d（HTTP %s）...\r" "$try" "$max_tries" "$code"
        sleep 2
        try=$((try + 1))
    done
    warn "$name 健康检查未通过（最后 HTTP $code）"
    return 1
}

# frontend：通过 nginx 反代访问 /api/v1/health/live
if $RESTART_FE; then
    check_health "frontend" "http://localhost/api/v1/health/live" || true
fi
# backend：直连 8000 端口
if $RESTART_BE; then
    check_health "backend"  "http://localhost:8000/api/v1/health/live" || true
fi

# ============ 步骤 5：状态展示 ============
echo ""
info "容器状态："
if $DRY_RUN; then
    echo -e "${YELLOW}[DRY]${NC} docker compose -f docker-compose.prod.yml --env-file .env.prod ps"
else
    docker compose -f docker-compose.prod.yml --env-file .env.prod ps
fi

if $RESTART_MON; then
    echo ""
    info "监控栈状态："
    if $DRY_RUN; then
        echo -e "${YELLOW}[DRY]${NC} docker compose -f docker-compose.monitoring.yml --env-file .env.prod ps"
    else
        docker compose -f docker-compose.monitoring.yml --env-file .env.prod ps
    fi
fi

echo ""
success "重启流程完成"
if $DRY_RUN; then
    echo -e "${YELLOW}（dry-run 模式，未实际执行）${NC}"
fi
echo ""
echo "提示："
echo "  - 查看 backend 日志:  docker compose -f docker-compose.prod.yml --env-file .env.prod logs -f backend"
echo "  - 查看 frontend 日志: docker compose -f docker-compose.prod.yml --env-file .env.prod logs -f frontend"
if $RESTART_MON; then
    echo "  - 查看监控栈日志:    docker compose -f docker-compose.monitoring.yml --env-file .env.prod logs -f prometheus"
    # 监控端口可能只绑 Tailscale（见 .env.prod 的 MONITOR_BIND_IP），写死 localhost 会给出打不开的地址。
    _MON="$(grep -E '^MONITOR_BIND_IP=' .env.prod 2>/dev/null | head -1 | cut -d= -f2- | tr -d '[:space:]')"
    case "$_MON" in ""|"0.0.0.0"|"::"|"*") _MON="localhost" ;; esac
    echo "  - Grafana 页面:      http://${_MON}:3001（口令见 .env.prod 的 GRAFANA_ADMIN_PASSWORD）"
    echo "  - Prometheus 页面:   http://${_MON}:9091"
fi
