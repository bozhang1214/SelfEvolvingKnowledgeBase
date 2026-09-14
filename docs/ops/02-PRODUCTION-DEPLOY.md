---
title: SEKB 线上部署手册
layer: 运维层
owner: SEKB Team
status: active
version: v1.0.0
last-updated: 2026-09-09
based-on-commit: f6eea56
related: [docs/tech/09-OBSERVABILITY]
---

# SEKB 线上部署手册

> 适用场景：将 SelfEvolvingKnowledgeBase 部署到公网服务器，对外提供生产服务
> 部署方式：Docker Compose 单机部署（应用栈 + 监控栈）
> 预计耗时：首次部署 30-60 分钟（含镜像构建），后续更新 5-10 分钟

---

## 目录

1. [部署架构总览](#1-部署架构总览)
2. [服务器准备](#2-服务器准备)
3. [代码与配置准备](#3-代码与配置准备)
4. [首次部署](#4-首次部署)
5. [HTTPS 配置（生产必做）](#5-https-配置生产必做)
6. [部署后验证](#6-部署后验证)
7. [CI/CD 自动部署配置](#7-cicd-自动部署配置)
8. [监控告警配置](#8-监控告警配置)
9. [备份策略配置](#9-备份策略配置)
10. [安全加固清单](#10-安全加固清单)
11. [常见部署问题排查](#11-常见部署问题排查)
12. [部署检查清单](#12-部署检查清单)

---

## 1. 部署架构总览

### 1.1 服务拓扑

```
                    Internet
                       │
                       ▼
              ┌─────────────────┐
              │   云服务器       │
              │  (公网 IP/域名)  │
              └────────┬────────┘
                       │ :80 / :443
                       ▼
         ┌──────────────────────────────┐
         │  frontend (nginx + SPA)     │   ← 静态资源 + 反向代理
         │  container: sekb-frontend   │
         └──────────────┬──────────────┘
                        │ /api/* 反代
                        ▼
         ┌──────────────────────────────┐
         │  backend (FastAPI)           │   ← 多 Agent 工作流
         │  container: sekb-backend     │
         └──────────────┬──────────────┘
                        │ data 卷持久化
                        ▼
              ┌─────────────────┐
              │  sekb_data 卷   │   ← 会话/索引/向量库/日志
              └─────────────────┘

   监控栈（独立 Compose，通过 external 网络连接）：
         ┌─────────────────────────────────────┐
         │  prometheus  grafana  loki          │
         │  promtail   alertmanager            │
         │  feishu-webhook                     │
         └─────────────────────────────────────┘
```

### 1.2 端口映射

| 服务 | 容器端口 | 宿主机端口 | 对外暴露 | 说明 |
|------|---------|-----------|---------|------|
| frontend | 80 | `${FRONTEND_PORT:-80}` | 是 | HTTP 入口 |
| backend | 8000 | 8000 | 否（仅调试） | 通过 frontend 反代 |
| prometheus | 9090 | 9091 | 可选 | 指标采集 |
| grafana | 3000 | 3001 | 可选 | 可视化看板 |
| alertmanager | 9093 | 9093 | 可选 | 告警路由 |
| loki | 3100 | 3101 | 否 | 日志聚合 |
| feishu-webhook | 5001 | 5001 | 否 | 飞书通知中转 |

### 1.3 资源要求

| 资源 | 最低 | 推荐 | 说明 |
|------|------|------|------|
| CPU | 2 核 | 4 核 | backend 限制 4 核 |
| 内存 | 4 GB | 8 GB | backend 限制 4G，监控栈约 2G |
| 磁盘 | 20 GB | 50 GB SSD | 含镜像、数据卷、日志 |
| 带宽 | 5 Mbps | 10 Mbps | LLM API 调用需要稳定带宽 |

---

## 2. 服务器准备

### 2.1 系统要求

- **操作系统**：Ubuntu 22.04 LTS 或 24.04 LTS（推荐）
- **Docker**：24.0+
- **Docker Compose**：v2.20+
- **Git**：2.34+

### 2.2 安装 Docker

```bash
# Ubuntu/Debian 一键安装
curl -fsSL https://get.docker.com | sh

# 将当前用户加入 docker 组（免 sudo）
sudo usermod -aG docker $USER

# 重新登录使 docker 组生效
exit
# 重新 SSH 登录后验证
docker --version
docker compose version
```

### 2.3 配置防火墙

```bash
# 仅开放必要端口
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp          # SSH
sudo ufw allow 80/tcp          # HTTP
sudo ufw allow 443/tcp         # HTTPS
sudo ufw enable

# 验证规则
sudo ufw status verbose
```

> **注意**：backend (8000)、grafana (3001)、prometheus (9091) 等端口**不要**对外开放，仅通过 SSH 隧道或 VPN 访问。

### 2.4 配置系统参数

```bash
# 增加文件描述符上限（Docker 容器默认继承）
echo "* soft nofile 65536" | sudo tee -a /etc/security/limits.conf
echo "* hard nofile 65536" | sudo tee -a /etc/security/limits.conf

# 配置 Docker 镜像加速（国内服务器推荐）
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json <<EOF
{
  "registry-mirrors": [
    "https://docker.mirrors.ustc.edu.cn",
    "https://hub-mirror.c.163.com"
  ],
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "100m",
    "max-file": "3"
  }
}
EOF
sudo systemctl daemon-reload
sudo systemctl restart docker
```

### 2.5 创建部署用户（可选，推荐）

```bash
# 创建专用部署用户
sudo useradd -m -s /bin/bash deploy
sudo usermod -aG docker deploy
sudo mkdir -p /opt/self-evolving-kb
sudo chown deploy:deploy /opt/self-evolving-kb
```

---

## 3. 代码与配置准备

### 3.1 克隆代码

```bash
# 切换到部署目录
cd /opt/self-evolving-kb

# 克隆仓库
git clone https://github.com/your-org/SelfEvolvingKnowledgeBase.git .
```

### 3.1.1 克隆内核查 `jobcopilot`（必需）

职位分析的内核已抽成**独立仓库** `jobcopilot`，后端镜像通过 Docker 的
**命名构建上下文**把它装进镜像（`docker-compose.prod.yml` 的 `additional_contexts`
＋ 后端 Dockerfile 的 `COPY --from=jobcopilot`）。

因此构建前，**本仓库根目录**必须有一份 jobcopilot 检出：

```bash
cd /opt/self-evolving-kb/SelfEvolvingKnowledgeBase
git clone ssh://git@<gitea-host>:2222/bo/jobcopilot.git jobcopilot
# 或走 HTTP（若服务器已配 .git-credentials）：
# git clone http://localhost:3000/bo/jobcopilot.git jobcopilot
```

要点：

- 该目录已在 `.gitignore` 里忽略，**不会**污染 SEKB 仓库，`git pull` 也不会动它；
- 内核有新版本时，要**单独** `cd jobcopilot && git pull`；
- `deploy.sh` 构建前会检查 `jobcopilot/pyproject.toml` 是否存在，缺失即报错并提示上面的命令；
- 发布顺序：先推 jobcopilot，再推 SEKB，最后服务器依次 pull 两个仓库。

### 3.2 配置环境变量

```bash
# 复制环境变量模板
cp deploy/.env.prod.example .env.prod

# 生成 JWT 密钥（强随机）
openssl rand -hex 32
# 输出示例：a3f4b8c9d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8
```

编辑 `.env.prod`，**必须修改以下项**：

```bash
# ============ 必填项 ============

# DeepSeek API Key（LLM 主力模型）
# 获取地址：https://platform.deepseek.com
DEEPSEEK_API_KEY=sk-your-real-deepseek-key

# 博查搜索 API Key（联网搜索工具）
# 获取地址：https://open.bochaai.com
BOCHA_API_KEY=sk-your-real-bocha-key

# JWT 签名密钥（用上面 openssl 生成的值替换）
JWT_SECRET=<替换为 openssl 生成的 64 位十六进制值>

# ============ 可选项 ============

# 前端对外端口（HTTP 模式默认 80，HTTPS 部署后改为 443）
FRONTEND_PORT=80

# 飞书告警通知（不配置则告警仅记录到 Alertmanager）
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/your-hook-id
FEISHU_SECRET=your-signing-secret

# LangSmith 链路追踪（可选）
LANGSMITH_API_KEY=
```

### 3.3 验证配置

```bash
# 检查必填项是否已替换占位符
grep -E "DEEPSEEK_API_KEY|BOCHA_API_KEY|JWT_SECRET" .env.prod | grep -v "change-me\|please-change"
# 若有输出，说明仍有未修改的占位符

# 校验 compose 配置文件语法
docker compose -f docker-compose.prod.yml --env-file .env.prod config --quiet && echo "✓ prod.yml OK"
docker compose -f docker-compose.monitoring.yml config --quiet && echo "✓ monitoring.yml OK"
```

---

## 4. 首次部署

### 4.1 构建并启动应用栈

```bash
# 构建镜像并启动后端 + 前端
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
```

**首次构建耗时**：
- 后端镜像：10-15 分钟（torch + sentence-transformers 模型下载，已配置 hf-mirror 加速）
- 前端镜像：2-3 分钟（npm install + vite build）

### 4.2 等待后端就绪

后端首次启动需要加载 embedding 模型，健康检查 `start_period: 60s`：

```bash
# 实时查看后端启动日志
docker compose -f docker-compose.prod.yml --env-file .env.prod logs -f backend

# 看到以下日志表示启动成功：
#   INFO: Uvicorn running on http://0.0.0.0:8000
#   INFO: Application startup complete.

# 另开终端检查健康状态
curl http://localhost:8000/api/v1/health/live
# 期望返回：{"status":"ok",...}
```

### 4.3 启动监控栈

```bash
# 方式一：一键脚本（推荐，自动带 --env-file .env.prod 与网络预检）
bash deploy/restart.sh monitoring

# 方式二：直接 docker compose
# 启动 Prometheus + Grafana + Loki + Alertmanager + feishu-webhook
docker compose -f docker-compose.monitoring.yml --env-file .env.prod up -d
```

### 4.4 查看服务状态

```bash
# 查看应用栈
docker compose -f docker-compose.prod.yml --env-file .env.prod ps

# 查看监控栈
docker compose -f docker-compose.monitoring.yml --env-file .env.prod ps

# 期望所有容器状态为 Up (healthy)
```

---

## 5. HTTPS 配置（生产必做）

HTTP 明文传输不安全，生产环境**必须**启用 HTTPS。

### 5.1 申请域名

确保已拥有域名并将 A 记录指向服务器公网 IP：

```bash
# 验证域名解析
dig +short bos-studio.tech
# 应返回服务器公网 IP
```

### 5.2 申请 SSL 证书

```bash
# 安装 certbot
sudo apt update && sudo apt install -y certbot

# 申请证书前先停止占用 80 端口的服务
docker compose -f docker-compose.prod.yml --env-file .env.prod stop frontend

# 申请 Let's Encrypt 证书
sudo certbot certonly --standalone -d bos-studio.tech

# 证书文件位置：
#   /etc/letsencrypt/live/bos-studio.tech/fullchain.pem
#   /etc/letsencrypt/live/bos-studio.tech/privkey.pem
```

### 5.3 切换 Nginx 配置为 HTTPS

```bash
# 备份当前 HTTP 配置
cp deploy/nginx.conf deploy/nginx.conf.http.bak

# 复制 HTTPS 配置模板
cp deploy/nginx-ssl.conf deploy/nginx.conf

# 编辑证书路径（将 bos-studio.tech 替换为真实域名）
sed -i 's/your-domain.com/bos-studio.tech/g' deploy/nginx.conf
```

### 5.4 挂载证书到前端容器

编辑 `docker-compose.prod.yml` 的 frontend 服务，添加证书挂载：

```yaml
  frontend:
    # ... 其他配置不变 ...
    ports:
      - "443:443"   # HTTPS
      - "80:80"     # HTTP（用于跳转和证书续期）
    volumes:
      - ./deploy/nginx.conf:/etc/nginx/conf.d/default.conf:ro
      - /etc/letsencrypt:/etc/letsencrypt:ro   # 新增：挂载证书
```

### 5.5 重启前端并验证

```bash
# 重启前端应用 HTTPS 配置
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d frontend

# 验证 HTTPS 访问
curl -I https://bos-studio.tech/
# 期望返回 HTTP/2 200

# 验证 HTTP 自动跳转
curl -I http://bos-studio.tech/
# 期望返回 301 Location: https://...
```

### 5.6 配置证书自动续期

```bash
# 添加 cron 定时任务（每月 1 号检查续期）
sudo crontab -e

# 添加以下行：
0 3 1 * * certbot renew --quiet && docker restart sekb-frontend
```

---

## 6. 部署后验证

### 6.1 健康检查

```bash
# 后端存活探针
curl -sf http://localhost:8000/api/v1/health/live && echo " ✓ backend live"

# 后端就绪探针
curl -sf http://localhost:8000/api/v1/health/ready && echo " ✓ backend ready"

# 后端综合健康
curl -s http://localhost:8000/api/v1/health/ | python3 -m json.tool

# 前端
curl -sf http://localhost/ -o /dev/null -w "frontend: %{http_code}\n"

# Prometheus 指标
curl -sf http://localhost:8000/metrics -o /dev/null -w "metrics: %{http_code}\n"
```

### 6.2 功能验证

```bash
# 1. 注册用户
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@sekb.local","password":"Test123456","name":"Admin"}'
# 期望返回 {"user":{...},"token":"..."}

# 2. 登录获取 token
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@sekb.local","password":"Test123456"}' | python3 -c "import json,sys;print(json.load(sys.stdin)['token'])")

# 3. 发送聊天消息（非流式）
curl -X POST http://localhost:8000/api/v1/chat/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"message":"你好"}'
# 期望返回 {"conversation_id":"...","message":"..."}

# 4. 验证前端页面
# 浏览器访问 http://bos-studio.tech/
# 应显示登录页面
```

### 6.3 监控栈验证

```bash
# Prometheus 抓取目标状态
curl -s http://localhost:9091/api/v1/targets | python3 -c "
import json, sys
d = json.load(sys.stdin)
for t in d['data']['activeTargets']:
    print(f\"  {t['labels']['job']:25s} {t['health']}\")
"

# Alertmanager 告警状态
curl -s http://localhost:9093/api/v2/alerts | python3 -m json.tool

# 访问 Grafana
# 浏览器访问 http://bos-studio.tech:3001（或通过 SSH 隧道）
# 默认账号：admin / admin（首次登录后立即修改密码）
```

---

## 7. CI/CD 自动部署配置

配置后，push 到 `main` 分支将自动触发部署。

### 7.1 配置 SSH 密钥

```bash
# 在本地机器生成 SSH 密钥对（若无）
ssh-keygen -t ed25519 -C "sekb-deploy" -f ~/.ssh/sekb_deploy_key -N ""

# 将公钥添加到服务器
cat ~/.ssh/sekb_deploy_key.pub | ssh deploy@your-server-ip "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"

# 测试免密登录
ssh -i ~/.ssh/sekb_deploy_key deploy@your-server-ip "docker --version"
```

### 7.2 配置 GitHub Secrets

在仓库 `Settings → Secrets and variables → Actions` 中添加以下 Secrets：

| Secret 名称 | 值 | 说明 |
|-------------|---|------|
| `DEEPSEEK_API_KEY` | `sk-xxx` | LLM API 密钥 |
| `BOCHA_API_KEY` | `sk-xxx` | 搜索 API 密钥 |
| `JWT_SECRET` | `a3f4b8c9...` | JWT 签名密钥 |
| `DEPLOY_HOST` | `1.2.3.4` | 服务器 IP |
| `DEPLOY_USER` | `deploy` | SSH 用户 |
| `DEPLOY_KEY` | `-----BEGIN OPENSSH PRIVATE KEY-----...` | SSH 私钥内容 |
| `DEPLOY_PATH` | `/opt/self-evolving-kb` | 部署路径 |

### 7.3 配置 GitHub Environment（推荐）

1. 仓库 `Settings → Environments → New environment` → 名称：`production`
2. 添加 reviewer（可选，需人工确认部署）
3. CI/CD 工作流已配置 `environment: production`

### 7.4 验证 CI/CD

```bash
# 在本地提交并推送
git add .
git commit -m "feat: 线上部署配置"
git push origin main

# 在 GitHub Actions 页面查看流水线：
#   lint → frontend-build → unit-test → integration-test → build → eval-test → deploy
# deploy job 会输出 8 个阶段的详细日志
```

### 7.5 CI/CD 部署日志（8 阶段）

部署 job 的输出包含以下阶段，便于排查失败原因：

```
[1/8] 部署前状态快照   - git HEAD、运行容器、磁盘空间
[2/8] 拉取最新代码     - git fetch + reset --hard
[3/8] 拉取最新镜像     - docker compose pull
[4/8] 部署前备份       - 配置备份 + 数据备份脚本
[5/8] 滚动更新应用栈   - backend 启动 + 健康检查 + frontend
[6/8] 更新监控栈       - monitoring compose up
[7/8] 部署后验证       - 容器状态 + 端点检查
[8/8] 清理与收尾       - 清理悬挂镜像 + 部署总结
```

---

## 8. 监控告警配置

### 8.1 访问监控面板

| 服务 | 访问方式 | 默认账号 |
|------|---------|---------|
| Grafana | `http://server-ip:3001` 或 SSH 隧道 | admin / admin |
| Prometheus | `http://server-ip:9091` | 无需认证 |
| Alertmanager | `http://server-ip:9093` | 无需认证 |

> **安全建议**：通过 SSH 隧道访问监控面板，不要直接暴露端口：
> ```bash
> ssh -L 3001:localhost:3001 -L 9091:localhost:9091 deploy@your-server-ip
> ```

### 8.2 修改 Grafana 密码

首次登录 Grafana 后**立即修改默认密码**：

```
Grafana → 下方齿轮图标 → Users → admin → Change password
```

### 8.3 配置飞书告警通知

1. **创建飞书机器人**：
   - 飞书群 → 群设置 → 群机器人 → 添加自定义机器人
   - 复制 webhook URL
   - （可选）启用安全设置 → 签名校验 → 复制签名密钥

2. **配置环境变量**：

```bash
# 编辑 .env.prod
cat >> .env.prod <<EOF
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/your-hook-id
FEISHU_SECRET=your-signing-secret
EOF

# 重启 feishu-webhook 服务
docker compose -f docker-compose.monitoring.yml up -d feishu-webhook
```

3. **测试告警链路**：

```bash
# 手动推送测试告警
curl -X POST http://localhost:5001/webhook \
  -H "Content-Type: application/json" \
  -d '{"alerts":[{"status":"firing","labels":{"alertname":"TestAlert","severity":"warning"},"annotations":{"summary":"部署测试告警","description":"验证飞书通知链路"},"startsAt":"2026-08-19T00:00:00Z"}]}'

# 预期：飞书群收到 "SEKB 告警通知（1 条）" 卡片消息
```

### 8.4 告警规则清单

| 告警名称 | 级别 | 触发条件 | 通知方式 |
|---------|------|---------|---------|
| BackendDown | critical | 后端不可达 1 分钟 | 飞书 + 邮件 |
| HighErrorRate | critical | 请求错误率 > 5% 持续 2 分钟 | 飞书 + 邮件 |
| ToolFailureHigh | critical | 工具失败率 > 10% 持续 2 分钟 | 飞书 + 邮件 |
| HighLatencyP95 | warning | P95 延迟 > 15 秒持续 3 分钟 | 飞书 |
| BackendDegraded | warning | 健康检查降级 2 分钟 | 飞书 |
| BudgetExceeded | warning | 日成本 > $5 持续 5 分钟 | 飞书 |
| LowGroundedness | warning | 答案锚定度 < 0.6 持续 15 分钟 | 飞书 |

> 告警故障排查请参考：[ALERTING-TROUBLESHOOTING.md](./07-ALERTING-TROUBLESHOOTING.md)

---

## 9. 备份策略配置

### 9.1 配置定时备份

```bash
# 添加 cron 定时任务（每日凌晨 3 点备份）
sudo crontab -e

# 添加以下行（DEPLOY_PATH 替换为实际路径）：
0 3 * * * cd /opt/self-evolving-kb && ./deploy/backup.sh >> /var/log/sekb-backup.log 2>&1
```

### 9.2 备份内容

`deploy/backup.sh` 会备份以下数据：

| 数据 | 备份方式 | 保留期 |
|------|---------|--------|
| 会话数据（sekb_data 卷） | docker cp | 本地 7 天 |
| ChromaDB 向量库 | docker cp | 本地 7 天 |
| 应用配置（.env.prod） | 文件复制 | 本地 7 天 |
| PostgreSQL（如启用） | pg_dump | 本地 7 天 |
| Redis（如启用） | RDB 快照 | 本地 7 天 |

### 9.3 配置 S3 远程备份（推荐）

```bash
# 编辑 .env.prod，添加 S3 配置
cat >> .env.prod <<EOF
S3_BUCKET=your-backup-bucket
S3_REGION=cn-north-1
AWS_ACCESS_KEY_ID=your-access-key
AWS_SECRET_ACCESS_KEY=your-secret-key
EOF

# 手动触发一次备份验证
cd /opt/self-evolving-kb
./deploy/backup.sh
ls -la /backup/

# 验证 S3 上传
aws s3 ls s3://your-backup-bucket/sekb-backups/
```

### 9.4 恢复演练

定期进行恢复演练，确保备份可用：

```bash
# 列出可用备份
./deploy/restore.sh --list

# 恢复最新备份
./deploy/restore.sh --latest

# 恢复指定日期的备份（含数据库）
./deploy/restore.sh --date 20260819_030000 --with-db
```

---

## 10. 安全加固清单

### 10.1 网络安全

- [x] 防火墙仅开放 22/80/443 端口
- [x] backend (8000) 不对外暴露（仅通过 nginx 反代）
- [x] 监控端口（3001/9091/9093）通过 SSH 隧道访问
- [x] Docker 容器间通过 sekb_network 网络隔离

### 10.2 应用安全

- [x] JWT 密钥使用强随机串（`openssl rand -hex 32`）
- [x] 密码使用 PBKDF2-SHA256 哈希存储
- [x] HTTPS 强制跳转（nginx-ssl.conf）
- [x] 安全响应头（CSP / HSTS / X-Frame-Options 等）
- [x] API 限流（nginx `limit_req` 10r/s）
- [x] 审计日志记录所有安全操作

### 10.3 容器安全

- [x] 容器以非 root 用户运行（appuser）
- [x] 容器资源限制（memory + cpus limits）
- [x] 日志大小限制（`max-size: 100m, max-file: 3`）
- [x] 健康检查配置（backend/frontend/grafana 等）

### 10.4 数据安全

- [x] `.env.prod` 已加入 `.gitignore`，不提交到版本控制
- [x] 定时备份（每日 3 点）
- [x] S3 远程备份（保留 30 天）
- [x] 部署前自动备份（CI/CD 第 4 阶段）

### 10.5 证书与密钥

- [x] SSL 证书使用 Let's Encrypt（免费 + 自动续期）
- [x] SSH 密钥使用 ed25519 算法
- [x] GitHub Secrets 存储 CI/CD 密钥

---

## 11. 常见部署问题排查

### 11.1 后端启动失败

```bash
# 查看后端日志
docker compose -f docker-compose.prod.yml --env-file .env.prod logs backend --tail 50

# 常见原因：
# 1. DEEPSEEK_API_KEY / BOCHA_API_KEY / JWT_SECRET 未设置或仍为占位符
grep "change-me\|please-change" .env.prod
# 2. embedding 模型下载失败（网络问题）
#    环境变量已配置 HF_ENDPOINT=https://hf-mirror.com 加速
# 3. 数据卷权限问题
docker exec sekb-backend ls -la /app/data
```

### 11.2 前端 502 Bad Gateway

```bash
# 检查后端是否健康
curl http://localhost:8000/api/v1/health/live

# 检查 nginx 配置语法
docker exec sekb-frontend nginx -t

# 检查 nginx 是否能连接到后端
docker exec sekb-frontend wget -qO- http://backend:8000/api/v1/health/live
```

### 11.3 镜像构建失败（网络超时）

```bash
# 后端镜像构建需要下载 torch 和 sentence-transformers
# 若网络超时，可配置 pip 镜像源：
docker compose -f docker-compose.prod.yml --env-file .env.prod build --no-cache backend \
  --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

# 或使用 HF 镜像加速模型下载（已在 compose 中配置）
# HF_ENDPOINT=https://hf-mirror.com
```

### 11.4 端口被占用

```bash
# 检查端口占用
sudo lsof -i :80
sudo lsof -i :443

# 若 80/443 被占用，修改 FRONTEND_PORT
# 编辑 .env.prod：
FRONTEND_PORT=8080
```

### 11.5 Alertmanager 启动失败

```bash
# 校验配置文件语法
docker exec sekb-alertmanager amtool check-config /etc/alertmanager/alertmanager.yml

# 常见原因：alertmanager.yml 不支持 ${VAR:default} 语法
# 确保 webhook URL 使用服务名：http://feishu-webhook:5001/webhook
```

> 更详细的告警模块排查请参考：[ALERTING-TROUBLESHOOTING.md](./07-ALERTING-TROUBLESHOOTING.md)

### 11.6 CI/CD 部署失败

```bash
# 在 GitHub Actions 页面查看 deploy job 日志
# 关注 [5/8] 滚动更新应用栈 阶段

# 若 backend 健康检查超时（90 秒未就绪）：
# 1. SSH 到服务器查看后端日志
ssh deploy@your-server-ip
cd /opt/self-evolving-kb
docker compose -f docker-compose.prod.yml --env-file .env.prod logs backend --tail 100

# 2. 若需手动回滚到上个版本
git log --oneline -5                        # 查看历史提交
git reset --hard HEAD~1                     # 回退到上个版本
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
```

---

## 12. 部署检查清单

首次部署完成后，逐项检查：

### 12.1 基础服务

- [ ] `docker compose -f docker-compose.prod.yml --env-file .env.prod ps` 所有容器 Up (healthy)
- [ ] `docker compose -f docker-compose.monitoring.yml ps` 所有容器 Up
- [ ] `curl http://localhost:8000/api/v1/health/live` 返回 200
- [ ] `curl http://localhost/` 返回 200（前端页面）

### 12.2 HTTPS

- [ ] `curl -I https://bos-studio.tech/` 返回 HTTP/2 200
- [ ] `curl -I http://bos-studio.tech/` 返回 301 跳转
- [ ] 证书自动续期 cron 已配置

### 12.3 功能验证

- [ ] 用户注册成功
- [ ] 用户登录获取 JWT token
- [ ] 非流式聊天返回正常
- [ ] SSE 流式聊天返回正常
- [ ] 文件上传成功
- [ ] 知识库搜索返回结果

### 12.4 监控告警

- [ ] Grafana 可访问且看板有数据
- [ ] Prometheus 抓取目标全部 up
- [ ] Alertmanager 配置校验通过
- [ ] 飞书测试告警已收到

### 12.5 CI/CD

- [ ] GitHub Secrets 已配置（7 项）
- [ ] push 到 main 触发流水线
- [ ] deploy job 8 阶段全部通过
- [ ] 自动部署后服务正常

### 12.6 备份与安全

- [ ] `.env.prod` 不含占位符
- [ ] 备份 cron 已配置
- [ ] S3 远程备份已配置（可选）
- [ ] 防火墙仅开放 22/80/443
- [ ] Grafana 默认密码已修改

---

## 附录：完整部署命令速查

> ⚠ 以下所有 docker compose 命令均需添加 --env-file .env.prod 参数，下文为简洁已省略

```bash
# ============ 首次部署 ============
git clone https://github.com/your-org/SelfEvolvingKnowledgeBase.git .
cp deploy/.env.prod.example .env.prod
# 编辑 .env.prod 填入真实密钥
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
docker compose -f docker-compose.monitoring.yml up -d

# ============ 日常更新 ============
git pull origin main
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
docker compose -f docker-compose.monitoring.yml up -d

# ============ 启用 HTTPS ============
docker compose -f docker-compose.prod.yml --env-file .env.prod stop frontend
sudo certbot certonly --standalone -d bos-studio.tech
cp deploy/nginx-ssl.conf deploy/nginx.conf
sed -i 's/your-domain.com/bos-studio.tech/g' deploy/nginx.conf
# 编辑 docker-compose.prod.yml 挂载证书 + 开放 443 端口
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d frontend

# ============ 停止服务 ============
docker compose -f docker-compose.prod.yml --env-file .env.prod down
docker compose -f docker-compose.monitoring.yml down

# ============ 查看日志 ============
docker compose -f docker-compose.prod.yml --env-file .env.prod logs -f backend
docker compose -f docker-compose.prod.yml --env-file .env.prod logs -f frontend
docker compose -f docker-compose.monitoring.yml logs -f prometheus
```
