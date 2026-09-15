#!/usr/bin/env bash
# ============================================================
# SEKB 一键部署脚本（线上生产环境）
# ============================================================
# 功能：执行完整的线上部署流程
# 用法：bash deploy/deploy.sh [选项]
#
# 选项：
#   --skip-build    跳过镜像构建（使用已构建的镜像）
#   --skip-check    跳过部署前检查
#   --skip-monitor  不启动监控栈
#   --dry-run       仅打印命令，不实际执行
#   --help          显示帮助
#
# 前置条件：
#   1. .env.prod 已配置（运行 pre-deploy-check.sh 验证）
#   2. Docker 服务运行中
#   3. 端口 80/443/8000 未被占用
# ============================================================

set -euo pipefail

# ============ 颜色定义 ============
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

# ============ 解析参数 ============
SKIP_BUILD=false
SKIP_CHECK=false
SKIP_MONITOR=false
DRY_RUN=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-build)
            SKIP_BUILD=true
            shift
            ;;
        --skip-check)
            SKIP_CHECK=true
            shift
            ;;
        --skip-monitor)
            SKIP_MONITOR=true
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --help|-h)
            echo "SEKB 一键部署脚本"
            echo ""
            echo "用法：bash deploy/deploy.sh [选项]"
            echo ""
            echo "选项："
            echo "  --skip-build    跳过镜像构建（使用已构建的镜像）"
            echo "  --skip-check    跳过部署前检查"
            echo "  --skip-monitor  不启动监控栈"
            echo "  --dry-run       仅打印命令，不实际执行"
            echo "  --help          显示帮助"
            exit 0
            ;;
        *)
            echo "未知选项: $1"
            echo "使用 --help 查看帮助"
            exit 1
            ;;
    esac
done

# ============ 辅助函数 ============
step() {
    echo ""
    echo -e "${BLUE}========== $1 ==========${NC}"
}

success() {
    echo -e "  ${GREEN}✓${NC} $1"
}

fail() {
    echo -e "  ${RED}✗${NC} $1"
    if [ -n "${2:-}" ]; then
        echo -e "    ${YELLOW}→${NC} $2"
    fi
    exit 1
}

warn() {
    echo -e "  ${YELLOW}⚠${NC} $1"
}

info() {
    echo -e "  ${BLUE}ℹ${NC} $1"
}

# dry-run 模式下的命令执行器
run() {
    if [ "$DRY_RUN" = true ]; then
        echo -e "  ${YELLOW}[DRY-RUN]${NC} $*"
    else
        eval "$@"
    fi
}

# ============ 获取项目根目录 ============
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

DEPLOY_DATE=$(date '+%Y-%m-%d %H:%M:%S')
DEPLOY_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")

echo ""
echo "============================================"
echo -e "  ${BOLD}SEKB 线上部署${NC}"
echo "============================================"
echo "  时间: $DEPLOY_DATE"
echo "  SHA:  $DEPLOY_SHA"
echo "  路径: $PROJECT_ROOT"
echo "  模式: $([ "$DRY_RUN" = true ] && echo 'DRY-RUN（仅预览）' || echo '实际执行')"
echo "============================================"

# ============================================================
# 阶段 0：部署前检查
# ============================================================
if [ "$SKIP_CHECK" = false ]; then
    step "阶段 0/7：部署前检查"
    if [ -f "deploy/pre-deploy-check.sh" ]; then
        if run "bash deploy/pre-deploy-check.sh"; then
            success "部署前检查通过"
        else
            fail "部署前检查未通过" "修复上述问题后重试，或使用 --skip-check 跳过"
        fi
    else
        warn "未找到 pre-deploy-check.sh，跳过检查"
    fi
else
    step "阶段 0/7：跳过部署前检查（--skip-check）"
fi

# ============================================================
# 阶段 1：部署前备份
# ============================================================
step "阶段 1/7：部署前备份"

# 备份配置文件
BACKUP_TAG="pre-deploy-$(date +%Y%m%d_%H%M%S)"
info "备份标签: $BACKUP_TAG"

run "cp docker-compose.prod.yml docker-compose.prod.yml.${BACKUP_TAG}.bak" 2>/dev/null || true
run "cp deploy/nginx.conf deploy/nginx.conf.${BACKUP_TAG}.bak" 2>/dev/null || true
success "配置文件已备份（.${BACKUP_TAG}.bak 后缀）"

# 触发数据备份（部署前的安全快照）
#
# 用维护中的 scripts/backup_kb.sh，**不用** deploy/backup.sh：
#   1) deploy/backup.sh 写死 /backup 目录，部署用户对其无权限 → 一直静默失败；
#   2) 它还会把 .env.prod 明文打进备份并 sync 到 S3（安全审查 SEC-09）；
#   3) 它只覆盖 ChromaDB/会话，**不覆盖 Gitea 数据卷**（D-02 起是版本管理权威源）。
# backup_kb.sh 覆盖 sekb_data + sekb_gitea_data，且带 EXIT trap 兜底重启服务。
if [ -x "scripts/backup_kb.sh" ] && [ "$DRY_RUN" = false ]; then
    info "触发部署前数据备份（停服务取一致快照，约 1-2 分钟）..."
    mkdir -p logs
    BACKUP_LOG="logs/deploy-backup.log"
    if ./scripts/backup_kb.sh >> "$BACKUP_LOG" 2>&1; then
        success "数据备份完成（日志：$BACKUP_LOG）"
    else
        warn "数据备份失败（非致命，继续部署；日志：$BACKUP_LOG）"
        [ -f "$BACKUP_LOG" ] && tail -5 "$BACKUP_LOG" | sed 's/^/    /'
    fi
elif [ "$DRY_RUN" = true ]; then
    echo -e "  ${YELLOW}[DRY-RUN]${NC} ./scripts/backup_kb.sh"
else
    warn "未找到 scripts/backup_kb.sh，跳过部署前数据备份"
fi

# ============================================================
# 阶段 2：构建镜像
# ============================================================
step "阶段 2/7：构建镜像"

if [ "$SKIP_BUILD" = true ]; then
    warn "跳过镜像构建（--skip-build）"
else
    # 内核包 jobcopilot 是 git 子模块（docker-compose.prod.yml 通过
    # additional_contexts 把它传进镜像）。**硬闸门**：未初始化 / commit 与 SEKB
    # 钉住的不一致 / 工作区脏，都直接中止——否则会「静默部署一个不是钉住版本的内核」。
    # 紧急情况可用 SKIP_KERNEL_CHECK=1 绕过（会打印警告）。
    if [ "${SKIP_KERNEL_CHECK:-0}" = "1" ]; then
        warn "已通过 SKIP_KERNEL_CHECK=1 跳过内核查校验（本次部署的内核版本未被验证）"
    elif [ -x "scripts/check_kernel.sh" ]; then
        info "校验内核查（git 子模块）状态..."
        if ! bash scripts/check_kernel.sh --strict; then
            fail "内核查状态异常，已中止部署" \
"按上面的提示对齐后重试。常用修复：
    git submodule update --init      # 未初始化
    git submodule update             # commit 与 SEKB 钉住的不一致
    git submodule update --force     # 工作区脏、要丢弃本地改动
紧急绕过（不推荐）：SKIP_KERNEL_CHECK=1 bash deploy/deploy.sh ..."
        fi
        KERNEL_SHA="$(git submodule status jobcopilot 2>/dev/null | cut -c2- | awk '{print $1}')"
        info "内核查 commit：${KERNEL_SHA:0:7}（已钉在 SEKB 中，构建时会烧进镜像）"
        # 传给 docker compose build（compose 里 args.JOBCOPILOT_COMMIT 插值），
        # 最终成为容器内的环境变量，供启动日志与 /health 报告。
        export JOBCOPILOT_COMMIT="$KERNEL_SHA"
    elif [ ! -f "jobcopilot/pyproject.toml" ]; then
        fail "缺少内核包子模块 jobcopilot/" "git submodule update --init"
    fi

    # 构建前网络预检测：测试镜像源连通性
    info "检测镜像源连通性..."
    if curl -sf --connect-timeout 5 -o /dev/null https://mirrors.cloud.tencent.com/ 2>/dev/null; then
        success "腾讯云镜像源可用（VPC 内自动走内网，构建最快）"
    elif curl -sf --connect-timeout 5 -o /dev/null https://mirrors.aliyun.com/ 2>/dev/null; then
        warn "腾讯云源不可达，将使用阿里云源（可能较慢）"
    else
        warn "镜像源连通性检测失败，构建可能较慢（请检查网络或配置 Docker 镜像加速器）"
        echo "        参考：sudo tee /etc/docker/daemon.json <<'EOF'"
        echo '        {"registry-mirrors":["https://mirror.ccs.tencentyun.com","https://docker.mirrors.ustc.edu.cn"]}'
        echo "        EOF && sudo systemctl restart docker"
    fi

    echo ""
    info "构建后端镜像（首次约 5-8 分钟，已配置腾讯云镜像源）..."
    echo "        如需查看实时进度：另开 SSH 会话执行 docker compose logs -f backend"
    echo "        如构建卡住超过 15 分钟：Ctrl+C 中止，检查网络和镜像源配置"
    # 使用 timeout 防止无限等待（20 分钟超时）
    if run "timeout 1200 docker compose -f docker-compose.prod.yml --env-file .env.prod build --progress=plain backend"; then
        success "后端镜像构建完成"
    else
        fail "后端镜像构建失败" "1. 检查网络: curl -I https://mirrors.cloud.tencent.com/
        2. 检查 Docker 加速: cat /etc/docker/daemon.json
        3. 查看构建日志: docker compose -f docker-compose.prod.yml logs backend
        4. 重试: docker compose -f docker-compose.prod.yml --env-file .env.prod build --no-cache backend"
    fi

    echo ""
    # 发布提示词到自建分发目录（nginx /prompts/，供 `jobcopilot pull --remote` 拉取）。
    # 用后端镜像跑一次性容器：只有它装了 jobcopilot，宿主机无需再装一套依赖。
    info "发布 JobCopilot 提示词到 ./prompts-dist（自建分发源）..."
    mkdir -p prompts-dist
    # ⚠️ 必须带 --user "$(id -u):$(id -g)"：镜像默认以 appuser(uid 1000) 运行，
    #    而挂载出来的 ./prompts-dist 属当前部署用户（未必是 1000）→ 写入会 PermissionError。
    if docker run --rm --user "$(id -u):$(id -g)" -v "$PWD/prompts-dist:/out" \
            --entrypoint jobcopilot "self-evolving-kb-backend:${TAG:-latest}" \
            publish --out /out >/dev/null 2>&1; then
        PROMPT_VER="$(python3 -c "import json;print(json.load(open('prompts-dist/manifest.json'))['version'])" 2>/dev/null || echo '?')"
        success "提示词已发布（version=$PROMPT_VER，nginx 路径 /prompts/）"
    else
        warn "提示词发布失败（非致命；/prompts/ 将不可用，pull 会回落到 GitHub 或包内）"
    fi

    info "构建前端镜像（约 2-3 分钟）..."
    if run "timeout 600 docker compose -f docker-compose.prod.yml --env-file .env.prod build --progress=plain frontend"; then
        success "前端镜像构建完成"
    else
        fail "前端镜像构建失败" "1. 检查 frontend/Dockerfile
        2. 查看日志: docker compose -f docker-compose.prod.yml logs frontend
        3. 重试: docker compose -f docker-compose.prod.yml --env-file .env.prod build --no-cache frontend"
    fi
fi

# ============================================================
# 阶段 3：启动应用栈 - 后端
# ============================================================
step "阶段 3/7：启动后端"

info "启动 backend 容器..."
run "docker compose -f docker-compose.prod.yml --env-file .env.prod up -d backend"

# 等待后端健康检查通过（最长 90 秒）
if [ "$DRY_RUN" = false ]; then
    info "等待 backend 就绪（最长 90 秒）..."
    HEALTHY=false
    for i in $(seq 1 18); do
        HEALTH=$(curl -sf http://localhost:8000/api/v1/health/live -o /dev/null -w "%{http_code}" 2>/dev/null || echo "000")
        if [ "$HEALTH" = "200" ]; then
            success "backend 就绪（第 ${i} 次检查，HTTP ${HEALTH}）"
            HEALTHY=true
            break
        fi
        echo -e "  ${YELLOW}等待中...${NC} (${i}/18, HTTP ${HEALTH})"
        sleep 5
    done

    if [ "$HEALTHY" = false ]; then
        fail "backend 健康检查超时" "查看日志：docker compose -f docker-compose.prod.yml logs backend --tail 50"
    fi
else
    echo -e "  ${YELLOW}[DRY-RUN]${NC} 等待 backend 健康检查..."
fi

# ============================================================
# 阶段 4：启动应用栈 - 前端
# ============================================================
step "阶段 4/7：启动前端"

info "启动 frontend 容器..."
run "docker compose -f docker-compose.prod.yml --env-file .env.prod up -d frontend"

if [ "$DRY_RUN" = false ]; then
    sleep 5
    FRONTEND_HEALTH=$(curl -sf http://localhost/ -o /dev/null -w "%{http_code}" 2>/dev/null || echo "000")
    if [ "$FRONTEND_HEALTH" = "200" ]; then
        success "frontend 就绪（HTTP ${FRONTEND_HEALTH}）"
    else
        warn "frontend 健康检查未通过（HTTP ${FRONTEND_HEALTH}）"
        info "查看日志：docker compose -f docker-compose.prod.yml logs frontend --tail 20"
    fi
fi

# ============================================================
# 阶段 5：启动监控栈
# ============================================================
if [ "$SKIP_MONITOR" = true ]; then
    step "阶段 5/7：跳过监控栈（--skip-monitor）"
else
    step "阶段 5/7：启动监控栈"

    info "启动监控服务（Prometheus + Grafana + Loki + Alertmanager + feishu-webhook）..."
    if run "docker compose -f docker-compose.monitoring.yml up -d"; then
        success "监控栈启动完成"
        if [ "$DRY_RUN" = false ]; then
            sleep 10
            # 检查监控服务状态
            for svc in sekb-prometheus sekb-grafana sekb-alertmanager sekb-feishu-webhook; do
                STATUS=$(docker inspect --format='{{.State.Status}}' "$svc" 2>/dev/null || echo "missing")
                if [ "$STATUS" = "running" ]; then
                    success "$svc 运行中"
                else
                    warn "$svc 状态异常: $STATUS"
                fi
            done
        fi
    else
        warn "监控栈启动失败（非致命，应用栈仍可运行）"
    fi
fi

# ============================================================
# 阶段 6：部署后验证
# ============================================================
step "阶段 6/7：部署后验证"

if [ "$DRY_RUN" = false ]; then
    echo "--- 容器状态 ---"
    docker compose -f docker-compose.prod.yml ps 2>/dev/null
    echo ""
    if [ "$SKIP_MONITOR" = false ]; then
        docker compose -f docker-compose.monitoring.yml ps 2>/dev/null
        echo ""
    fi

    echo "--- 端点检查 ---"
    echo -n "  backend /health/live: "
    curl -sf http://localhost:8000/api/v1/health/live -o /dev/null -w "%{http_code}\n" 2>/dev/null || echo "FAIL"
    echo -n "  backend /metrics: "
    curl -sf http://localhost:8000/metrics -o /dev/null -w "%{http_code}\n" 2>/dev/null || echo "FAIL"
    echo -n "  frontend /: "
    curl -sf http://localhost/ -o /dev/null -w "%{http_code}\n" 2>/dev/null || echo "FAIL"

    if [ "$SKIP_MONITOR" = false ]; then
        echo -n "  prometheus: "
        curl -sf http://localhost:9091/-/healthy -o /dev/null -w "%{http_code}\n" 2>/dev/null || echo "FAIL"
        echo -n "  grafana: "
        curl -sf http://localhost:3001/api/health -o /dev/null -w "%{http_code}\n" 2>/dev/null || echo "FAIL"
        echo -n "  alertmanager: "
        curl -sf http://localhost:9093/-/healthy -o /dev/null -w "%{http_code}\n" 2>/dev/null || echo "FAIL"
        echo -n "  feishu-webhook: "
        curl -sf http://localhost:5001/health -o /dev/null -w "%{http_code}\n" 2>/dev/null || echo "FAIL"
    fi
    echo ""
    success "部署后验证完成"
else
    echo -e "  ${YELLOW}[DRY-RUN]${NC} 跳过验证（dry-run 模式）"
fi

# ============================================================
# 阶段 7：清理与收尾
# ============================================================
step "阶段 7/7：清理与收尾"

# 清理悬挂镜像
info "清理悬挂镜像..."
run "docker image prune -f 2>/dev/null || true"
success "清理完成"

# 限制构建缓存上限（防止 buildkit 缓存无限增长撑爆磁盘）
# 背景：2026-09-14 排查发现构建缓存堆到 20.2G，占满 59G 磁盘的 1/3，
#       直接导致部署前检查「需要 ≥20G 空闲」反复告警。缓存只影响重建速度，不影响功能。
# 上限取 14G 的依据（实测）：
#   - 只有保留住 `pip install -r requirements.txt` 那一层（约 14G，含 torch/transformers），
#     依赖才不会每次重装；卡到 2G 会让每次部署多花 ~13 分钟重装依赖；
#   - 14G 上限下磁盘占用约 35G、可用约 24G，稳稳通过 20G 预检。
info "限制构建缓存上限（14GB）..."
if [ "$DRY_RUN" = false ]; then
    # ⚠️ 这里**不能加 -a/--all**：实测 `builder prune -af --max-used-space N`
    #    会静默变成空操作（返回 Total: 0B，缓存一点不掉）。
    #    正确用法是不带 -a，让它按上限做 LRU 淘汰。
    if docker builder prune -f --max-used-space 14GB >/dev/null 2>&1; then
        success "构建缓存已收敛到 14GB 以内"
    else
        # 旧版 Docker 不支持 --max-used-space，退化为整体清空（同样安全，只是下次构建慢）
        docker builder prune -af >/dev/null 2>&1 || true
        warn "Docker 版本不支持缓存上限参数，已改为整体清空构建缓存"
    fi
fi

# 清理超过 7 天的备份配置文件
info "清理旧备份配置文件（保留 7 天）..."
if [ "$DRY_RUN" = false ]; then
    find . -name "*.bak" -mtime +7 -delete 2>/dev/null || true
    success "旧备份已清理"
fi

# ============================================================
# 部署总结
# ============================================================
echo ""
echo "============================================"
echo -e "  ${GREEN}${BOLD}部署完成${NC}"
echo "============================================"
echo ""
echo "  部署时间: $DEPLOY_DATE"
echo "  代码版本: $DEPLOY_SHA"
echo ""
echo "  服务访问地址："
echo "    前端:       http://localhost/"
echo "    后端 API:   http://localhost:8000/api/v1/health/live"
if [ "$SKIP_MONITOR" = false ]; then
    echo "    Grafana:   http://localhost:3001（admin/admin，首次登录后改密码）"
    echo "    Prometheus: http://localhost:9091"
    echo "    Alertmgr:  http://localhost:9093"
fi
echo ""
echo "  常用命令："
echo "    查看日志:   docker compose -f docker-compose.prod.yml logs -f backend"
echo "    重启服务:   docker compose -f docker-compose.prod.yml restart backend"
echo "    停止服务:   docker compose -f docker-compose.prod.yml down"
echo ""
if [ "$DRY_RUN" = true ]; then
    echo -e "  ${YELLOW}以上为预览模式，未实际执行。去掉 --dry-run 参数执行真实部署。${NC}"
    echo ""
fi
