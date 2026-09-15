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
    # 第二个参数是可选的可操作提示（与 fail 一致）——早先只有 fail 支持，
    # 导致 warn 的提示被静默丢弃（调用方以为打印了）。
    if [ -n "${2:-}" ]; then
        echo -e "    ${YELLOW}→${NC} $2"
    fi
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

# ============================================================
# 部署互斥锁（多协作者必做）
# ============================================================
# 为什么需要：本项目存在多个协作者同时开发、都可能发生产。两个部署交错会
# 造成镜像构建互相打断、容器重启互相踩，最终停在**半部署状态**——比直接失败更难查。
# 这里用 flock 保证同一时刻只有一个部署；第二个会立刻失败并告诉你**谁在部署**。
#
# 注意：锁只保护「同一台机器上的 deploy.sh」。若确认对方已异常退出，
# 直接删除锁文件即可（flock 的锁随进程退出自动释放，文件残留不代表仍被占用）。
LOCK_FILE="${DEPLOY_LOCK_FILE:-/tmp/sekb-deploy.lock}"
if [ "$DRY_RUN" = false ] && command -v flock >/dev/null 2>&1; then
    exec 9>"$LOCK_FILE"
    if ! flock -n 9; then
        HOLDER="$(cat "$LOCK_FILE" 2>/dev/null | tail -1)"
        fail "另一个部署正在进行，已中止（避免两个部署交错）" \
"锁文件: $LOCK_FILE
持有者: ${HOLDER:-未知}
确认对方已退出后可删除锁文件重试：rm -f $LOCK_FILE"
    fi
    echo "pid=$$ user=$(whoami) since=$DEPLOY_DATE commit=$DEPLOY_SHA" >&9
elif [ "$DRY_RUN" = false ]; then
    warn "系统没有 flock，跳过部署互斥（多人同时部署可能交错）"
fi

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
# 阶段 0 之前：对齐内核子模块（必须在检查之前）
# ============================================================
# 为什么要有这一步：部署的职责就是「部署 SEKB 钉住的那份内核」，而 `git pull`
# **不会**自动更新子模块——所以「指针刚更新、子模块还停在旧 commit」是**预期状态**。
# 但阶段 0 的 pre-deploy-check 也会校验内核，于是部署被自己拦住；真正的对齐在阶段 2，
# 顺序反了。结果只能加 `--skip-check` 绕过，而那等于**连其他所有检查一起跳过**
# （2026-09-15 实际发生过：协作者部署时就是这么绕的）。
#
# 仅在子模块**工作区干净**时自动对齐：有本地改动就交给检查去报错，不悄悄覆盖。
if [ "$DRY_RUN" = false ] && [ -e "jobcopilot/.git" ] \
    && [ -z "$(git -C jobcopilot status --porcelain 2>/dev/null)" ]; then
    if ! git submodule update --init >/dev/null 2>&1; then
        warn "子模块对齐失败（继续走部署前检查，会给出具体原因）"
    fi
fi

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

    # ---------- 构建前腾磁盘：这一步不能省 ----------
    # 为什么放在**构建前**而不是只在阶段 7 收尾清：构建过程中 buildkit 缓存会
    # 持续增长（一次全量构建可增十几 G），而阶段 7 的清理发生在最后——
    # 磁盘不够时会在构建中途 `no space left on device`，留下半成品状态。
    # （2026-09-15 实际发生过一次：后端镜像构建失败，生产卡在旧版本。）
    #
    # 策略：余量够就保留缓存（构建快），不够才整体清掉（构建慢但一定成）。
    # 构建峰值实测约需 25G（旧镜像 + 新镜像 + 缓存增长）。
    NEED_MB=25000
    PRUNE_BELOW_MB=32000
    FREE_MB="$(df -Pm / | awk 'NR==2{print $4}')"
    if [ "$DRY_RUN" = false ]; then
        if [ "${FREE_MB:-0}" -lt "$PRUNE_BELOW_MB" ]; then
            info "磁盘可用 ${FREE_MB}MB < ${PRUNE_BELOW_MB}MB，构建前清理 Docker 构建缓存..."
            docker builder prune -af >/dev/null 2>&1 || true
            FREE_MB="$(df -Pm / | awk 'NR==2{print $4}')"
            success "清理后可用 ${FREE_MB}MB"
        else
            info "磁盘可用 ${FREE_MB}MB，保留构建缓存（构建更快）"
        fi
        if [ "${FREE_MB:-0}" -lt "$NEED_MB" ]; then
            fail "磁盘空间不足以完成构建（可用 ${FREE_MB}MB，峰值约需 ${NEED_MB}MB）" \
"先腾空间再重试：
    docker builder prune -af          # 清构建缓存
    docker image prune -a -f          # 清未被容器使用的镜像（旧版本镜像常有数 G）
    df -h /                           # 确认可用 ≥ 25G
详见 docs/ops/13-DISK-MEMORY.md"
        fi
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
# 阶段 3.5：启动 JobCopilot MCP（HTTP/SSE，供云端平台调用）
# ============================================================
# 必须排在 frontend **之前**：nginx 配置里有 `upstream ... server jobcopilot-mcp:8765`，
# nginx 启动时解析不到这个名字会直接启动失败。
#
# 它复用 backend 镜像，所以必须在 backend 镜像构建/存在之后启动。
step "阶段 3.5/7：启动 JobCopilot MCP（HTTP/SSE）"
if [ "$DRY_RUN" = false ]; then
    if grep -q "^  jobcopilot-mcp:" docker-compose.prod.yml 2>/dev/null; then
        if ! grep -qE "^JOBCOPILOT_HTTP_TOKEN=." .env.prod 2>/dev/null; then
            # 不静默跳过：这个变量缺失时内核会拒绝启动（安全闸），
            # 而 nginx 会因为解析不到 upstream 起不来 —— 必须让人知道原因。
            warn "未配置 JOBCOPILOT_HTTP_TOKEN，跳过 MCP HTTP 入口" \
                 "配置后重试：echo \"JOBCOPILOT_HTTP_TOKEN=\$(openssl rand -hex 24)\" >> .env.prod"
        else
            if run "docker compose -f docker-compose.prod.yml --env-file .env.prod up -d jobcopilot-mcp"; then
                sleep 3
                MCP_STATE="$(docker inspect -f '{{.State.Status}}' sekb-jobcopilot-mcp 2>/dev/null || echo unknown)"
                if [ "$MCP_STATE" = "running" ]; then
                    success "JobCopilot MCP 已启动（路径前缀 /jobcopilot，仅经 nginx 对外）"
                else
                    warn "JobCopilot MCP 启动后状态为 ${MCP_STATE}" \
                         "查看日志：docker logs sekb-jobcopilot-mcp --tail 30"
                fi
            else
                warn "JobCopilot MCP 启动失败（不阻塞主流程）" \
                     "查看日志：docker logs sekb-jobcopilot-mcp --tail 30"
            fi
        fi
    else
        info "compose 中没有 jobcopilot-mcp 服务，跳过"
    fi
else
    echo -e "  ${YELLOW}[DRY-RUN]${NC} 启动 JobCopilot MCP"
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

    # 带重试的端点探测。为什么必须重试：监控栈（尤其 Grafana）重启后要几十秒才就绪，
    # 一次性探测会在**服务其实健康**的情况下打 FAIL —— 2026-09-15 连续两次部署都出现
    # 「grafana: 000 FAIL」，而一分钟后它就是 healthy。这种假 FAIL 的代价是让人从此
    # 不再相信这份验证输出（进而忽略真正的 FAIL）。
    probe_endpoint() {
        local label="$1" url="$2" allowed="$3" tries="${4:-6}" delay="${5:-5}"
        local i code
        for i in $(seq 1 "$tries"); do
            code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$url" 2>/dev/null)" || code="000"
            case " $allowed " in
                *" $code "*) printf '  %-22s %s\n' "$label" "$code"; return 0 ;;
            esac
            # 差一点就绪时不必等满：继续重试
            [ "$i" -lt "$tries" ] && sleep "$delay"
        done
        printf '  %-22s %s（重试 %s 次后仍非 [%s]）\n' "$label" "$code" "$tries" "$allowed"
        return 1
    }

    echo "--- 端点检查（带重试，最多约 $((6 * 5)) 秒/端点）---"
    EP_FAIL=0
    probe_endpoint "backend /health/live" "http://localhost:8000/api/v1/health/live" "200" || EP_FAIL=$((EP_FAIL + 1))
    probe_endpoint "backend /metrics" "http://localhost:8000/metrics" "200" || EP_FAIL=$((EP_FAIL + 1))
    # 前端 / 是 301（nginx 重定向到 /sekb），也接受 200
    probe_endpoint "frontend /" "http://localhost/" "200 301" || EP_FAIL=$((EP_FAIL + 1))

    if [ "$SKIP_MONITOR" = false ]; then
        probe_endpoint "prometheus" "http://localhost:9091/-/healthy" "200" || EP_FAIL=$((EP_FAIL + 1))
        probe_endpoint "grafana" "http://localhost:3001/api/health" "200" || EP_FAIL=$((EP_FAIL + 1))
        probe_endpoint "alertmanager" "http://localhost:9093/-/healthy" "200" || EP_FAIL=$((EP_FAIL + 1))
        probe_endpoint "feishu-webhook" "http://localhost:5001/health" "200" || EP_FAIL=$((EP_FAIL + 1))
    fi

    # JobCopilot MCP（公网入口，经 nginx /jobcopilot/ 暴露给云端平台）
    # 无令牌访问返回 401 恰好说明「服务活着 + 鉴权生效」；200 也算通过
    if docker inspect -f '{{.State.Status}}' sekb-jobcopilot-mcp >/dev/null 2>&1; then
        probe_endpoint "jobcopilot-mcp" "http://localhost:8765/jobcopilot/sse" "401 200" || EP_FAIL=$((EP_FAIL + 1))
    fi
    echo ""
    if [ "$EP_FAIL" -eq 0 ]; then
        success "部署后验证完成（全部端点正常）"
    else
        warn "部署后验证完成：${EP_FAIL} 个端点在重试窗口内未就绪（可能仍在启动，请稍后复查：docker ps）"
    fi
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
