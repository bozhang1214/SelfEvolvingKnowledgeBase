#!/usr/bin/env bash
# ============================================================
# SEKB 线上部署前一键检查脚本
# ============================================================
# 功能：自动执行部署前所有检查项，输出彩色报告
# 用法：bash deploy/pre-deploy-check.sh
#
# 退出码：
#   0 - 所有必做项通过
#   1 - 存在未通过的必做项
# ============================================================

set -euo pipefail

# ============ 颜色定义 ============
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ============ 计数器 ============
PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0

# ============ 辅助函数 ============
pass() {
    echo -e "  ${GREEN}✓${NC} $1"
    PASS_COUNT=$((PASS_COUNT + 1))
}

fail() {
    echo -e "  ${RED}✗${NC} $1"
    if [ -n "${2:-}" ]; then
        echo -e "    ${YELLOW}→ 修复：${NC} $2"
    fi
    FAIL_COUNT=$((FAIL_COUNT + 1))
}

warn() {
    echo -e "  ${YELLOW}⚠${NC} $1"
    WARN_COUNT=$((WARN_COUNT + 1))
}

info() {
    echo -e "  ${BLUE}ℹ${NC} $1"
}

check_file_exists() {
    if [ -f "$1" ]; then
        pass "$1 存在"
        return 0
    else
        fail "$1 不存在" "创建文件或检查路径"
        return 1
    fi
}

# ============ 获取项目根目录 ============
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

echo ""
echo "============================================"
echo "  SEKB 线上部署前检查"
echo "  时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "  路径: $PROJECT_ROOT"
echo "============================================"
echo ""

# ============================================================
# 一、服务器环境检查
# ============================================================
echo -e "${BLUE}========== 1. 服务器环境 ==========${NC}"

# 1.1 操作系统
if [ -f /etc/os-release ]; then
    OS_ID=$(grep "^ID=" /etc/os-release | cut -d= -f2 | tr -d '"')
    OS_VERSION=$(grep "^VERSION_ID=" /etc/os-release | cut -d= -f2 | tr -d '"')
    case "$OS_ID" in
        ubuntu)
            if [ "${OS_VERSION%%.*}" -ge 22 ]; then
                pass "操作系统: Ubuntu $OS_VERSION"
            else
                fail "Ubuntu 版本过低: $OS_VERSION" "升级到 22.04+"
            fi
            ;;
        *)
            warn "操作系统: $OS_ID $OS_VERSION（建议 Ubuntu 22.04+）"
            ;;
    esac
else
    warn "无法检测操作系统版本"
fi

# 1.2 CPU 核数
CPU_COUNT=$(nproc 2>/dev/null || echo 0)
if [ "$CPU_COUNT" -ge 2 ]; then
    pass "CPU 核数: $CPU_COUNT"
else
    fail "CPU 核数不足: $CPU_COUNT" "至少需要 2 核"
fi

# 1.3 内存（阈值 3600MB，预留系统开销；优先读 /proc/meminfo 总内存）
MEM_TOTAL=""
if [ -r "/proc/meminfo" ]; then
    # Linux：从 /proc/meminfo 读取总内存（KB），转 MB
    MEM_KB=$(grep "^MemTotal:" /proc/meminfo 2>/dev/null | awk '{print $2}')
    if [ -n "$MEM_KB" ]; then
        MEM_TOTAL=$((MEM_KB / 1024))
    fi
fi
# 降级：用 free 命令
if [ -z "$MEM_TOTAL" ]; then
    MEM_TOTAL=$(free -m 2>/dev/null | awk '/^Mem:/ {print $2}')
fi
if [ -n "$MEM_TOTAL" ]; then
    if [ "$MEM_TOTAL" -ge 3600 ]; then
        pass "内存: ${MEM_TOTAL}MB"
    else
        fail "内存不足: ${MEM_TOTAL}MB" "至少需要 3600MB（推荐 4GB），当前可用内存偏低"
    fi
else
    warn "无法检测内存"
fi

# 1.4 磁盘空间（**只告警，不判定失败**）
# ⚠️ 2026-09-15 修：这里原本在 <20GB 时直接 fail，但真正的磁盘门禁在
#    deploy.sh 阶段 2（NEED_MB=25000，且会**先** `docker builder prune -af` 再判定）。
#    两者阈值不一致，且本检查排在前面，造成一个自相矛盾的循环：
#      「部署成功后的稳态可用空间（约 17-18GB）< 这里要求的 20GB」
#      → 于是**每次成功部署都会把下一次部署拦住**，而阶段 2 本来能靠清缓存解决。
#    现在这里只提示，把判定权交给阶段 2 那唯一的门禁（它知道要先清缓存）。
DISK_AVAIL=$(df -m . 2>/dev/null | awk 'NR==2 {print $4}')
if [ -n "$DISK_AVAIL" ]; then
    if [ "$DISK_AVAIL" -ge 20480 ]; then
        pass "磁盘可用空间: ${DISK_AVAIL}MB"
    else
        warn "磁盘可用空间偏低: ${DISK_AVAIL}MB（构建峰值约需 17-18GB；阶段 2 会先清构建缓存再判定，若仍不足会明确报错）"
    fi
else
    warn "无法检测磁盘空间"
fi

# 1.5 Docker 已安装
if command -v docker &>/dev/null; then
    DOCKER_VERSION=$(docker --version | grep -oE '[0-9]+\.[0-9]+' | head -1)
    pass "Docker 已安装: $DOCKER_VERSION"
else
    fail "Docker 未安装" "curl -fsSL https://get.docker.com | sh"
fi

# 1.6 Docker Compose 已安装
if docker compose version &>/dev/null; then
    COMPOSE_VERSION=$(docker compose version | grep -oE '[0-9]+\.[0-9]+' | head -1)
    pass "Docker Compose 已安装: $COMPOSE_VERSION"
else
    fail "Docker Compose 未安装" "Docker 24.0+ 自带 compose 插件"
fi

# 1.7 当前用户在 docker 组
if groups | grep -qw docker; then
    pass "当前用户在 docker 组"
else
    fail "当前用户不在 docker 组" "sudo usermod -aG docker \$USER && exit（重新登录）"
fi

# 1.8 Docker 服务运行中
if docker info &>/dev/null; then
    pass "Docker 服务运行中"
else
    fail "Docker 服务未运行" "sudo systemctl start docker"
fi

# 1.9 防火墙状态
if command -v ufw &>/dev/null; then
    UFW_STATUS=$(sudo ufw status 2>/dev/null | head -1)
    if echo "$UFW_STATUS" | grep -q "active"; then
        pass "防火墙已启用"
        # 检查是否开放了 80 和 443
        if sudo ufw status | grep -qE "80/tcp.*ALLOW"; then
            pass "80 端口已开放"
        else
            warn "80 端口未开放（HTTP 部署需要）"
        fi
        if sudo ufw status | grep -qE "443/tcp.*ALLOW"; then
            pass "443 端口已开放"
        else
            warn "443 端口未开放（HTTPS 部署需要）"
        fi
        # 检查是否泄露了内部端口
        if sudo ufw status | grep -qE "8000|3001|9091|9093|3101|5001"; then
            fail "内部端口对外暴露" "仅开放 22/80/443，关闭 8000/3001/9091/9093/3101/5001"
        else
            pass "内部端口未对外暴露"
        fi
    else
        warn "防火墙未启用（生产环境建议启用）"
    fi
else
    warn "未安装 ufw 防火墙"
fi

echo ""

# ============================================================
# 二、代码与配置检查
# ============================================================
echo -e "${BLUE}========== 2. 代码与配置 ==========${NC}"

# 2.1 docker-compose.prod.yml 存在
check_file_exists "docker-compose.prod.yml"

# 2.1.1 内核（jobcopilot git 子模块）状态：
# 未初始化 / commit 与 SEKB 钉住的不一致 / 工作区脏，都会让构建产出
# 「不是钉住版本的内核」。这里提前暴露，别等到构建或运行才发现。
#
# ⚠️ 必须写成 `if VAR="$(cmd)"; then` 形式：本脚本是 set -euo pipefail，
#    若写成 `VAR="$(cmd)"` 再判 $?，命令返回非 0 时脚本会**直接退出**，
#    门禁失败反而变成静默中断整个预检。
if [ -x "scripts/check_kernel.sh" ]; then
    if KERNEL_OUT="$(bash scripts/check_kernel.sh --strict 2>&1)"; then
        KERNEL_SHA="$(git submodule status jobcopilot 2>/dev/null | cut -c2- | awk '{print $1}')"
        pass "内核 jobcopilot 子模块状态正常（钉住 commit ${KERNEL_SHA:0:7}）"
    else
        fail "内核 jobcopilot 子模块状态异常" "运行 bash scripts/check_kernel.sh 查看详情与修复命令"
        printf '%s\n' "$KERNEL_OUT" | sed 's/^/    /' | tail -20
    fi
else
    warn "未找到 scripts/check_kernel.sh，跳过内核查校验"
fi

# 2.2 docker-compose.monitoring.yml 存在
check_file_exists "docker-compose.monitoring.yml"

# 2.3 .env.prod 文件存在
if [ -f ".env.prod" ]; then
    pass ".env.prod 文件存在"
else
    fail ".env.prod 文件不存在" "cp deploy/.env.prod.example .env.prod"
fi

# 2.4 .env.prod 必填项检查（只检查对应密钥行，避免注释或其他行误判）
if [ -f ".env.prod" ]; then
    # DEEPSEEK_API_KEY（只取该行，检查是否以 sk- 开头且无占位符）
    DEEPSEEK_LINE=$(grep -E "^DEEPSEEK_API_KEY=" .env.prod | head -1)
    if [ -n "$DEEPSEEK_LINE" ] && echo "$DEEPSEEK_LINE" | grep -qE "^DEEPSEEK_API_KEY=sk-" \
        && ! echo "$DEEPSEEK_LINE" | grep -qE "change-me|please-change|your-real"; then
        pass "DEEPSEEK_API_KEY 已配置"
    else
        fail "DEEPSEEK_API_KEY 未配置或仍为占位符" "编辑 .env.prod 填入真实密钥（sk- 开头）"
    fi

    # BOCHA_API_KEY
    BOCHA_LINE=$(grep -E "^BOCHA_API_KEY=" .env.prod | head -1)
    if [ -n "$BOCHA_LINE" ] && echo "$BOCHA_LINE" | grep -qE "^BOCHA_API_KEY=sk-" \
        && ! echo "$BOCHA_LINE" | grep -qE "change-me|please-change|your-real"; then
        pass "BOCHA_API_KEY 已配置"
    else
        fail "BOCHA_API_KEY 未配置或仍为占位符" "编辑 .env.prod 填入真实密钥（sk- 开头）"
    fi

    # JWT_SECRET
    JWT_VALUE=$(grep "^JWT_SECRET=" .env.prod | cut -d= -f2-)
    if [ -n "$JWT_VALUE" ] && [ "$JWT_VALUE" != "please-change-me-to-a-strong-random-secret" ] && [ ${#JWT_VALUE} -ge 32 ]; then
        pass "JWT_SECRET 已配置（长度 ${#JWT_VALUE}）"
    else
        fail "JWT_SECRET 未配置或长度不足" "openssl rand -hex 32 生成密钥"
    fi

    # 无遗留占位符（只检查值行，跳过注释和空行）
    # 排除 DB_PASSWORD（可选配置，不强制要求修改）
    if grep -vE "^#|^$|^DB_PASSWORD=" .env.prod | grep -qE "=.*(change-me|please-change|your-real)"; then
        fail ".env.prod 含有占位符" "替换所有 change-me/please-change 占位符"
    else
        pass ".env.prod 无占位符"
    fi

    # .env.prod 已 gitignore
    if git check-ignore .env.prod &>/dev/null; then
        pass ".env.prod 已被 gitignore"
    else
        fail ".env.prod 未被 gitignore" "将 .env.prod 加入 .gitignore"
    fi

    # .env.prod 权限 600
    PERMS=$(stat -c "%a" .env.prod 2>/dev/null || stat -f "%Lp" .env.prod 2>/dev/null)
    if [ "$PERMS" = "600" ]; then
        pass ".env.prod 权限为 600"
    else
        warn ".env.prod 权限为 $PERMS（建议 chmod 600）"
    fi
fi

# 2.5 Compose 配置校验
if [ -f ".env.prod" ]; then
    if docker compose -f docker-compose.prod.yml --env-file .env.prod config --quiet 2>/dev/null; then
        pass "docker-compose.prod.yml 配置校验通过"
    else
        fail "docker-compose.prod.yml 配置校验失败" "检查 YAML 语法和环境变量"
    fi
else
    warn ".env.prod 不存在，跳过 prod.yml 配置校验"
fi

if docker compose -f docker-compose.monitoring.yml config --quiet 2>/dev/null; then
    pass "docker-compose.monitoring.yml 配置校验通过"
else
    fail "docker-compose.monitoring.yml 配置校验失败" "检查 YAML 语法"
fi

echo ""

# ============================================================
# 三、HTTPS 证书检查
# ============================================================
echo -e "${BLUE}========== 3. HTTPS 证书 ==========${NC}"

# 检查 nginx.conf 是否为 SSL 配置
if grep -q "ssl_certificate" deploy/nginx.conf 2>/dev/null; then
    pass "deploy/nginx.conf 已配置 SSL"
    # 检查是否替换了域名
    if grep -q "your-domain.com" deploy/nginx.conf; then
        fail "nginx.conf 仍含 your-domain.com 占位符" "替换为真实域名"
    else
        pass "nginx.conf 域名已替换"
    fi
else
    warn "deploy/nginx.conf 未配置 SSL（如仅 HTTP 部署可忽略）"
fi

# 检查 Let's Encrypt 证书（若存在）
if sudo test -d /etc/letsencrypt/live 2>/dev/null; then
    CERT_DOMAINS=$(sudo ls /etc/letsencrypt/live/ 2>/dev/null)
    if [ -n "$CERT_DOMAINS" ]; then
        for domain in $CERT_DOMAINS; do
            if sudo test -f "/etc/letsencrypt/live/$domain/fullchain.pem"; then
                EXPIRY=$(sudo openssl x509 -enddate -noout \
                    -in "/etc/letsencrypt/live/$domain/fullchain.pem" 2>/dev/null \
                    | cut -d= -f2)
                # 检查是否过期（简单日期比较）
                EXPIRY_EPOCH=$(date -d "$EXPIRY" +%s 2>/dev/null || echo 0)
                NOW_EPOCH=$(date +%s)
                if [ "$EXPIRY_EPOCH" -gt "$NOW_EPOCH" ]; then
                    pass "证书 $domain 未过期（到期日：$EXPIRY）"
                else
                    fail "证书 $domain 已过期" "sudo certbot renew"
                fi
            fi
        done
    else
        warn "未找到 Let's Encrypt 证书"
    fi
else
    warn "未安装 certbot 或无证书（如已配置商业证书可忽略）"
fi

echo ""

# ============================================================
# 四、端口冲突检查
# ============================================================
echo -e "${BLUE}========== 4. 端口冲突 ==========${NC}"

check_port() {
    local port=$1
    local name=$2
    if command -v lsof &>/dev/null; then
        if lsof -i :"$port" -sTCP:LISTEN -t &>/dev/null; then
            fail "端口 $port ($name) 被占用" "停止占用服务或修改端口配置"
        else
            pass "端口 $port ($name) 未被占用"
        fi
    else
        if ss -tlnp 2>/dev/null | grep -q ":$port "; then
            fail "端口 $port ($name) 被占用" "停止占用服务或修改端口配置"
        else
            pass "端口 $port ($name) 未被占用"
        fi
    fi
}

check_port 80 "HTTP"
check_port 443 "HTTPS"
check_port 8000 "backend"

echo ""

# ============================================================
# 五、遗留容器检查
# ============================================================
echo -e "${BLUE}========== 5. 遗留容器 ==========${NC}"

OLD_CONTAINERS=$(docker ps -a --filter "name=sekb-" --format "{{.Names}}" 2>/dev/null)
if [ -n "$OLD_CONTAINERS" ]; then
    warn "存在旧容器："
    echo "$OLD_CONTAINERS" | while read -r c; do
        echo "    - $c"
    done
    echo -e "    ${YELLOW}→ 如需清理：docker rm -f \$(docker ps -aq --filter name=sekb-)${NC}"
else
    pass "无遗留旧容器"
fi

echo ""

# ============================================================
# 六、监控告警检查
# ============================================================
echo -e "${BLUE}========== 6. 监控告警 ==========${NC}"

# Alertmanager 配置校验
if [ -f "deploy/alertmanager.yml" ]; then
    # 先检查 Docker 是否运行
    if ! docker info &>/dev/null; then
        warn "Docker 未运行，跳过 alertmanager.yml 校验" "启动 Docker 后重新执行检查"
    else
        # 捕获 amtool 输出，便于排查具体错误
        # ⚠️ 必须显式 --entrypoint amtool：prom/alertmanager 镜像的 ENTRYPOINT 是
        #    /bin/alertmanager，否则 `... prom/alertmanager:latest amtool check-config`
        #    会把 amtool 当成 alertmanager 的参数，报
        #    「unexpected amtool, try --help」并让预检永久失败。
        AMTOOL_OUTPUT=$(docker run --rm --entrypoint amtool \
            -v "$PROJECT_ROOT/deploy/alertmanager.yml:/etc/alertmanager/alertmanager.yml:ro" \
            prom/alertmanager:latest check-config /etc/alertmanager/alertmanager.yml 2>&1)
        AMTOOL_EXIT=$?
        if [ "$AMTOOL_EXIT" -eq 0 ]; then
            pass "alertmanager.yml 配置校验通过"
        else
            fail "alertmanager.yml 配置校验失败" "查看下方详细输出"
            echo -e "    ${YELLOW}--- amtool 输出 ---${NC}"
            echo "$AMTOOL_OUTPUT" | head -20 | sed 's/^/    /'
        fi
    fi
else
    warn "deploy/alertmanager.yml 不存在"
fi

# 飞书 webhook 配置
if [ -f ".env.prod" ]; then
    if grep -q "^FEISHU_WEBHOOK_URL=https://" .env.prod; then
        pass "飞书 webhook 已配置"
    else
        warn "飞书 webhook 未配置（告警将不推送飞书）"
    fi
fi

# 备份目录
if [ -d "/backup" ]; then
    pass "备份目录 /backup 存在"
else
    warn "备份目录 /backup 不存在" "sudo mkdir -p /backup"
fi

# 备份 cron
# ⚠️ 两个坑（2026-09-15 修）：
#   1) 只看 root 的 crontab —— 实际任务装在部署用户（bo）的 crontab 里；
#   2) grep 的模式是 "backup.sh"，而脚本名是 "backup_kb.sh"，中间的 _kb 让模式
#      永远匹配不上。结果部署日志里长期挂着「定时备份 cron 未配置」这条假警告，
#      而备份其实每天都在跑（假警告会让真警告被忽略）。
if { sudo crontab -l 2>/dev/null; crontab -l 2>/dev/null; } | grep -qE "backup_kb\.sh|backup\.sh"; then
    pass "定时备份 cron 已配置"
else
    warn "定时备份 cron 未配置（root 与当前用户的 crontab 都没找到）" "crontab -e 添加每日备份任务"
fi

echo ""

# ============================================================
# 七、CI/CD 检查（可选）
# ============================================================
echo -e "${BLUE}========== 7. CI/CD（可选） ==========${NC}"

# 检查是否在 git 仓库中
if git rev-parse --is-inside-work-tree &>/dev/null; then
    CURRENT_BRANCH=$(git branch --show-current 2>/dev/null)
    if [ "$CURRENT_BRANCH" = "main" ]; then
        pass "当前分支: main"
    else
        warn "当前分支: $CURRENT_BRANCH（CI/CD 部署需要 main 分支）"
    fi

    # 检查是否有 remote
    # ⚠️ 不能只认 origin：本仓库的 remote 叫 gitea / github（origin 指向 GitHub
    #    的 SSH 地址，中国网络经常连不上）。只认 origin 会长期报「Git remote 未配置」
    #    的假警告。这里改为「有任意 remote 即可」，并把名字打出来便于核对。
    REMOTES="$(git remote 2>/dev/null | tr '\n' ' ' | sed 's/ *$//')"
    if [ -n "$REMOTES" ]; then
        pass "Git remote 已配置（${REMOTES}）"
    else
        warn "Git remote 未配置"
    fi
else
    warn "非 Git 仓库（跳过 CI/CD 检查）"
fi

# SSH 密钥检查（检查 deploy key 是否存在）
if [ -f "$HOME/.ssh/sekb_deploy_key" ]; then
    pass "SSH 部署密钥存在"
else
    warn "未找到 ~/.ssh/sekb_deploy_key（CI/CD 部署需要）"
fi

echo ""

# ============================================================
# 汇总报告
# ============================================================
echo "============================================"
echo -e "  ${GREEN}检查完成${NC}"
echo "============================================"
echo ""
echo -e "  ${GREEN}通过：${PASS_COUNT} 项${NC}"
echo -e "  ${RED}失败：${FAIL_COUNT} 项${NC}"
echo -e "  ${YELLOW}警告：${WARN_COUNT} 项${NC}"
echo ""

if [ "$FAIL_COUNT" -gt 0 ]; then
    echo -e "${RED}========================================${NC}"
    echo -e "${RED}  ✗ 存在 $FAIL_COUNT 项未通过检查${NC}"
    echo -e "${RED}  请修复上述失败项后再执行部署${NC}"
    echo -e "${RED}========================================${NC}"
    exit 1
else
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  ✓ 所有必做项检查通过${NC}"
    if [ "$WARN_COUNT" -gt 0 ]; then
        echo -e "${YELLOW}  ⚠ 有 $WARN_COUNT 项警告（可选项）${NC}"
    fi
    echo -e "${GREEN}========================================${NC}"
    echo ""
    echo "  下一步：执行部署脚本"
    echo "    bash deploy/deploy.sh"
    echo ""
    exit 0
fi
