---
title: 线上部署前最终检查清单
layer: 运维层
owner: SEKB Team
status: active
version: v1.0.0
last-updated: 2026-09-09
based-on-commit: f6eea56
related: [docs/tech/09-OBSERVABILITY]
---

# 线上部署前最终检查清单

> 适用场景：执行 `docker compose up -d` 之前的最终核对，确保所有前置条件已满足
> 使用方式：逐项打勾，所有"必做项"必须 ✓ 后才能执行部署

---

## 一、服务器环境检查（必做）

### 1.1 系统要求

- [ ] **操作系统**：Ubuntu 22.04+ 或 24.04 LTS
  ```bash
  cat /etc/os-release | grep VERSION_ID
  ```

- [ ] **CPU**：≥ 2 核（推荐 4 核）
  ```bash
  nproc
  ```

- [ ] **内存**：≥ 4 GB（推荐 8 GB）
  ```bash
  free -h
  ```

- [ ] **磁盘可用空间**：≥ 20 GB（推荐 50 GB SSD）
  ```bash
  df -h /
  ```

### 1.2 Docker 环境

- [ ] **Docker 已安装**：版本 24.0+
  ```bash
  docker --version
  ```

- [ ] **Docker Compose 已安装**：v2.20+
  ```bash
  docker compose version
  ```

- [ ] **当前用户在 docker 组**（免 sudo 执行 docker）
  ```bash
  groups | grep docker
  # 若无输出：sudo usermod -aG docker $USER && exit（重新登录）
  ```

- [ ] **Docker 服务运行中**
  ```bash
  docker info >/dev/null 2>&1 && echo "✓ Docker 运行中" || echo "✗ Docker 未运行"
  ```

### 1.3 网络

- [ ] **防火墙仅开放必要端口**（22/80/443）
  ```bash
  sudo ufw status
  # 确认 8000/3001/9091/9093 等端口未对外开放
  ```

- [ ] **域名已解析到服务器 IP**
  ```bash
  dig +short bos-studio.tech
  # 应返回服务器公网 IP
  ```

- [ ] **能访问外部 API**（DeepSeek/博查）
  ```bash
  curl -sf -o /dev/null -w "%{http_code}" https://platform.deepseek.com
  curl -sf -o /dev/null -w "%{http_code}" https://open.bochaai.com
  ```

---

## 二、代码与配置检查（必做）

### 2.1 代码

- [ ] **代码已克隆到部署目录**
  ```bash
  ls -la /opt/self-evolving-kb/docker-compose.prod.yml
  ```

- [ ] **当前分支为 main**
  ```bash
  cd /opt/self-evolving-kb && git branch --show-current
  ```

- [ ] **代码为最新版本**
  ```bash
  cd /opt/self-evolving-kb && git pull origin main --dry-run 2>&1
  ```

### 2.2 环境变量配置

- [ ] **.env.prod 文件已创建**
  ```bash
  ls -la /opt/self-evolving-kb/.env.prod
  ```

- [ ] **DEEPSEEK_API_KEY 已配置**（不含占位符）
  ```bash
  grep "^DEEPSEEK_API_KEY=" .env.prod | grep -v "change-me"
  # 应输出：DEEPSEEK_API_KEY=sk-xxx
  ```

- [ ] **BOCHA_API_KEY 已配置**（不含占位符）
  ```bash
  grep "^BOCHA_API_KEY=" .env.prod | grep -v "change-me"
  ```

- [ ] **JWT_SECRET 已配置**（强随机，非占位符）
  ```bash
  grep "^JWT_SECRET=" .env.prod | grep -v "please-change"
  # 验证长度（应为 64 字符的 hex）
  JWT=$(grep "^JWT_SECRET=" .env.prod | cut -d= -f2)
  [ ${#JWT} -ge 32 ] && echo "✓ 长度合格" || echo "✗ 长度不足"
  ```

- [ ] **.env.prod 无遗留占位符**
  ```bash
  grep -E "change-me|please-change|your-real" .env.prod
  # 应无输出（除非是注释行）
  ```

- [ ] **.env.prod 未提交到 Git**（安全检查）
  ```bash
  git check-ignore .env.prod && echo "✓ 已被 gitignore" || echo "✗ 未被忽略！"
  ```

### 2.3 Compose 配置校验

- [ ] **docker-compose.prod.yml 语法正确**
  ```bash
  docker compose -f docker-compose.prod.yml --env-file .env.prod config --quiet \
    && echo "✓ prod.yml OK" || echo "✗ 配置错误"
  ```

- [ ] **docker-compose.monitoring.yml 语法正确**
  ```bash
  docker compose -f docker-compose.monitoring.yml config --quiet \
    && echo "✓ monitoring.yml OK" || echo "✗ 配置错误"
  ```

---

## 三、HTTPS 证书检查（生产必做）

- [ ] **SSL 证书已申请**
  ```bash
  sudo ls /etc/letsencrypt/live/bos-studio.tech/
  # 应包含 fullchain.pem 和 privkey.pem
  ```

- [ ] **证书未过期**
  ```bash
  sudo openssl x509 -enddate -noout \
    -in /etc/letsencrypt/live/bos-studio.tech/fullchain.pem
  # 检查日期是否在未来
  ```

- [ ] **nginx-ssl.conf 已替换为 nginx.conf**
  ```bash
  # 检查 nginx.conf 是否包含 ssl 配置
  grep -c "ssl_certificate" deploy/nginx.conf
  # 应 > 0
  ```

- [ ] **证书路径已替换为真实域名**
  ```bash
  grep "bos-studio.tech" deploy/nginx.conf
  # 应无输出（已替换为真实域名）
  grep "ssl_certificate" deploy/nginx.conf
  # 应显示真实域名路径
  ```

- [ ] **证书自动续期 cron 已配置**
  ```bash
  sudo crontab -l | grep certbot
  # 应输出续期任务
  ```

---

## 四、CI/CD 配置检查（如需自动部署）

- [ ] **SSH 密钥已配置**（本地 → 服务器免密登录）
  ```bash
  ssh -i ~/.ssh/sekb_deploy_key deploy@your-server-ip "echo OK"
  # 应输出 OK
  ```

- [ ] **GitHub Secrets 已配置**（7 项）
  ```
  在 GitHub 仓库 Settings → Secrets and variables → Actions 中检查：
  □ DEEPSEEK_API_KEY
  □ BOCHA_API_KEY
  □ JWT_SECRET
  □ DEPLOY_HOST
  □ DEPLOY_USER
  □ DEPLOY_KEY
  □ DEPLOY_PATH
  ```

- [ ] **GitHub Environment 已创建**（推荐）
  ```
  仓库 Settings → Environments → 存在 "production"
  ```

---

## 五、监控告警配置检查

### 5.1 告警通知

- [ ] **飞书 webhook 已配置**（或确认不使用飞书告警）
  ```bash
  grep "^FEISHU_WEBHOOK_URL=" .env.prod | grep -v "^$"
  ```

- [ ] **Alertmanager 配置校验通过**
  ```bash
  docker run --rm -v "$(pwd)/deploy/alertmanager.yml:/etc/alertmanager/alertmanager.yml:ro" \
    prom/alertmanager:latest amtool check-config /etc/alertmanager/alertmanager.yml 2>&1 | tail -3
  ```

### 5.2 备份策略

- [ ] **备份目录已创建**
  ```bash
  ls -ld /backup/ 2>/dev/null || sudo mkdir -p /backup && sudo chown $USER /backup
  ```

- [ ] **定时备份 cron 已配置**
  ```bash
  sudo crontab -l | grep backup.sh
  # 应输出每日备份任务
  ```

- [ ] **S3 远程备份已配置**（可选）
  ```bash
  grep "^S3_BUCKET=" .env.prod
  ```

---

## 六、安全加固检查

### 6.1 网络安全

- [ ] **backend 8000 端口未对外暴露**（仅通过 nginx 反代）
  ```bash
  sudo ufw status | grep 8000
  # 应无输出或为 DENY
  ```

- [ ] **监控端口不对外暴露**（3001/9091/9093/3101/5001）
  ```bash
  sudo ufw status | grep -E "3001|9091|9093|3101|5001"
  # 应无输出
  ```

### 6.2 应用安全

- [ ] **Grafana 默认密码已修改**（若已部署过）
  ```bash
  # 首次部署可跳过，已部署需确认
  curl -sf http://localhost:3001/api/health
  ```

- [ ] **.env.prod 权限为 600**
  ```bash
  stat -c "%a" .env.prod
  # 应为 600
  ```

### 6.3 容器资源限制

- [ ] **backend 容器内存限制已配置**
  ```bash
  grep -A5 "backend:" docker-compose.prod.yml | grep "memory"
  # 应显示 limits: memory: 4G
  ```

---

## 七、端口冲突检查（必做）

- [ ] **80 端口未被占用**
  ```bash
  sudo lsof -i :80
  # 若被占用：停止占用服务或修改 FRONTEND_PORT
  ```

- [ ] **443 端口未被占用**（HTTPS 部署）
  ```bash
  sudo lsof -i :443
  ```

- [ ] **8000 端口未被占用**
  ```bash
  sudo lsof -i :8000
  ```

- [ ] **无遗留旧容器运行**
  ```bash
  docker ps -a --filter "name=sekb" --format "{{.Names}}\t{{.Status}}"
  # 若有旧容器：docker rm -f sekb-backend sekb-frontend 2>/dev/null
  ```

---

## 八、最终确认

### 必做项汇总

| # | 检查项 | 状态 |
|---|--------|------|
| 1 | Docker 24.0+ + Compose v2.20+ 已安装 | ☐ |
| 2 | 服务器 ≥ 2核/4GB/20GB | ☐ |
| 3 | 防火墙仅开放 22/80/443 | ☐ |
| 4 | 域名已解析到服务器 IP | ☐ |
| 5 | 代码已克隆且为 main 分支最新 | ☐ |
| 6 | .env.prod 已配置 3 项必填密钥 | ☐ |
| 7 | .env.prod 无占位符 | ☐ |
| 8 | .env.prod 权限 600 且已 gitignore | ☐ |
| 9 | docker-compose 配置校验通过 | ☐ |
| 10 | SSL 证书已申请且未过期 | ☐ |
| 11 | nginx-ssl.conf 已替换为 nginx.conf | ☐ |
| 12 | 80/443/8000 端口未被占用 | ☐ |
| 13 | 无遗留旧容器运行 | ☐ |
| 14 | Alertmanager 配置校验通过 | ☐ |

### 可选项确认

| # | 检查项 | 状态 |
|---|--------|------|
| 15 | CI/CD SSH 密钥已配置 | ☐ |
| 16 | GitHub Secrets 已配置（7 项） | ☐ |
| 17 | 飞书告警 webhook 已配置 | ☐ |
| 18 | 定时备份 cron 已配置 | ☐ |
| 19 | S3 远程备份已配置 | ☐ |
| 20 | Grafana 默认密码已修改 | ☐ |

---

## 九、一键检查脚本

将上述检查自动化，运行以下命令一键完成全部检查：

```bash
bash deploy/pre-deploy-check.sh
```

脚本会自动执行所有检查项并输出彩色报告，详见：[deploy/pre-deploy-check.sh](../deploy/pre-deploy-check.sh)

---

## 十、检查通过后执行部署

所有必做项 ✓ 后，执行一键部署脚本：

```bash
bash deploy/deploy.sh
```

或手动分步执行（详见 [PRODUCTION-DEPLOY.md](./02-PRODUCTION-DEPLOY.md)）：

```bash
# 1. 构建并启动应用栈
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build

# 2. 等待后端就绪（最长 90 秒）
for i in $(seq 1 18); do
  curl -sf http://localhost:8000/api/v1/health/live && break
  sleep 5
done

# 3. 启动监控栈
docker compose -f docker-compose.monitoring.yml up -d

# 4. 验证
curl http://localhost:8000/api/v1/health/live
curl http://localhost/
```
