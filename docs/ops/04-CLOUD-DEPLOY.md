---
title: 国内云平台低成本部署手册
layer: 运维层
owner: SEKB Team
status: active
version: v1.0.0
last-updated: 2026-09-09
based-on-commit: f6eea56
related: [docs/tech/09-OBSERVABILITY]
---

# 国内云平台低成本部署手册（轻量应用服务器）

> 适用场景：个人 / 小团队使用 SEKB，追求最低成本上线
> 部署方式：国内轻量应用服务器 + Docker Compose
> 预算：月费 50-150 元（含新人特价可低至 100-300 元/年）

---

## 目录

1. [云平台选型对比](#1-云平台选型对比)
2. [购买与初始化服务器](#2-购买与初始化服务器)
3. [服务器基础配置](#3-服务器基础配置)
4. [部署 SEKB 项目](#4-部署-sekb-项目)
5. [域名与 HTTPS（可选）](#5-域名与-https可选)
6. [日常运维](#6-日常运维)
7. [成本优化建议](#7-成本优化建议)
8. [从个人版升级到生产版](#8-从个人版升级到生产版)

---

## 1. 云平台选型对比

### 1.1 国内主流轻量应用服务器

| 平台 | 产品名称 | 入门配置 | 新人特价 | 续费价格 | 推荐指数 |
|------|---------|---------|---------|---------|---------|
| **腾讯云** | 轻量应用服务器 Lighthouse | 2核2G 3M带宽 | 99 元/年 | 240 元/年 | ★★★★★ |
| **阿里云** | 轻量应用服务器 | 2核2G 3M带宽 | 99 元/年 | 280 元/年 | ★★★★★ |
| **华为云** | 耀云服务器 L 实例 | 2核2G 3M带宽 | 88 元/年 | 260 元/年 | ★★★★ |
| **腾讯云** | 轻量应用服务器（4G 版） | 2核4G 4M带宽 | 268 元/年 | 480 元/年 | ★★★★ |
| **阿里云** | 轻量应用服务器（4G 版） | 2核4G 4M带宽 | 298 元/年 | 520 元/年 | ★★★★ |

> **新人特价**：通常指首次购买或新注册用户享有的优惠价，续费会恢复原价。

### 1.2 推荐方案

**个人使用首选：腾讯云轻量应用服务器 2核4G**

理由：
1. **SEKB 内存需求**：后端 Docker 限制 4G 内存 + ChromaDB 向量库，2G 内存会 OOM，**必须 4G 起步**
2. **腾讯云新人价**：2核4G 4M带宽 268 元/年（约 22 元/月），性价比最高
3. **带宽 4M**：足够个人使用和少量并发
4. **自带 Docker 镜像**：开机即用，无需手动安装

> 若预算极紧（< 100 元/年），可选 2核2G 版本，但需要**关闭监控栈**并限制后端内存为 1.5G，否则会频繁 OOM。

### 1.3 配置选择建议

| 使用场景 | 推荐配置 | 月成本 | 说明 |
|---------|---------|--------|------|
| 仅自己用（< 5 QPS） | 2核4G 4M | ~22 元/月 | 新人特价，含完整监控栈 |
| 几个朋友共用 | 2核4G 6M | ~40 元/月 | 带宽升级，并发更好 |
| 50+ 用户 | 4核8G 5M | ~80 元/月 | 需要考虑升级或独立数据库 |

---

## 2. 购买与初始化服务器

以腾讯云为例（阿里云流程类似）：

### 2.1 购买流程

1. **注册账号**：
   - 访问 https://cloud.tencent.com/
   - 完成实名认证（个人认证即可，需身份证）

2. **购买轻量应用服务器**：
   - 进入 https://console.cloud.tencent.com/lighthouse
   - 点击「新建实例」

3. **配置选择**：

   | 配置项 | 推荐选择 | 说明 |
   |--------|---------|------|
   | 地域 | 广州/上海/北京（就近选） | 国内访问快 |
   | 镜像 | **Docker**（应用镜像） | 开机即装好 Docker，免手动安装 |
   | 实例规格 | 2核4G 4M | 新人特价 268 元/年 |
   | 系统盘 | 60GB SSD | 默认即可 |
   | 购买时长 | 1 年（享最大折扣） | 续费会恢复原价 |

   > **镜像选择**：务必选「Docker」应用镜像，这样开机即有 Docker 环境，省去安装步骤。

4. **设置密码**：
   - 购买时设置 root 密码（或购买后在控制台重置）
   - 记住此密码，后续 SSH 登录用

5. **等待创建**：约 1-2 分钟，状态变为「运行中」即可使用

### 2.2 获取服务器信息

在控制台找到以下信息：

- **公网 IP**：例如 `43.136.xx.xx`
- **内网 IP**：例如 `10.0.0.5`（免费，内网通信用）
- **实例 ID**：例如 `lh-xxxxxxxx`

### 2.3 开放防火墙端口

腾讯云轻量服务器有「云防火墙」，需在控制台开放端口：

进入「实例详情 → 防火墙」，添加以下规则：

| 应用 | 协议 | 端口 | 说明 |
|------|------|------|------|
| SSH | TCP | 22 | 远程登录 |
| HTTP | TCP | 80 | 网站访问 |
| HTTPS | TCP | 443 | HTTPS（配置证书后） |
| 后端调试 | TCP | 8000 | 可选，调试用，建议不开 |

> **Grafana/Prometheus 等监控端口（3001/9091/9093/3101/5001）不要对外开**，通过 SSH 隧道访问。

### 2.4 连接服务器

```bash
# 本地终端 SSH 登录
ssh root@43.136.xx.xx
# 输入购买时设置的密码

# 或在腾讯云控制台点「登录」→ WebShell（浏览器直接登录）
```

---

## 3. 服务器基础配置

### 3.1 验证 Docker 环境（若选了 Docker 镜像）

```bash
docker --version          # 应显示 24.0+
docker compose version    # 应显示 v2.20+

# 若未安装（选了纯净系统镜像），手动安装：
# curl -fsSL https://get.docker.com | sh
```

### 3.2 配置 Docker 镜像加速（国内必做）

```bash
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json <<'EOF'
{
  "registry-mirrors": [
    "https://docker.mirrors.ustc.edu.cn",
    "https://hub-mirror.c.163.com",
    "https://mirror.ccs.tencentyun.com"
  ],
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "50m",
    "max-file": "3"
  }
}
EOF

sudo systemctl daemon-reload
sudo systemctl restart docker
```

> **为什么必做**：不配置加速，拉取 prom/grafana/loki 等镜像会超时失败。

### 3.3 创建部署用户（推荐）

```bash
# 创建普通用户，避免长期用 root
useradd -m -s /bin/bash deploy
usermod -aG docker deploy
echo "deploy ALL=(ALL) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/deploy

# 设置密码
passwd deploy

# 切换到 deploy 用户
su - deploy
```

### 3.4 配置 SSH 密钥登录（推荐）

在本地机器执行：

```bash
# 1. 生成密钥对（若已有可跳过）
ssh-keygen -t ed25519 -C "sekb-cloud" -f ~/.ssh/sekb_cloud_key -N ""

# 2. 上传公钥到服务器
ssh-copy-id -i ~/.ssh/sekb_cloud_key.pub deploy@43.136.xx.xx

# 3. 免密登录测试
ssh -i ~/.ssh/sekb_cloud_key deploy@43.136.xx.xx
```

### 3.5 配置系统防火墙（可选，云防火墙已覆盖）

```bash
# Ubuntu 系统防火墙（云防火墙 + 系统防火墙双重保护）
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

### 3.6 设置交换分区（2G 内存机型必做）

```bash
# 2G 内存机型容易 OOM，建议加 2G swap
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile

# 永久生效
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# 验证
free -h
# 应显示 Swap: 2.0Gi
```

---

## 4. 部署 SEKB 项目

### 4.1 克隆代码

```bash
cd /opt
sudo mkdir -p self-evolving-kb
sudo chown deploy:deploy self-evolving-kb
cd self-evolving-kb

git clone https://github.com/your-org/SelfEvolvingKnowledgeBase.git .
```

### 4.2 配置环境变量

```bash
cp deploy/.env.prod.example .env.prod

# 生成 JWT 密钥
openssl rand -hex 32
# 复制输出的 64 位字符串
```

编辑 `.env.prod`，**必须修改**以下项：

```bash
# 必填项
DEEPSEEK_API_KEY=sk-your-real-deepseek-key    # https://platform.deepseek.com 获取
BOCHA_API_KEY=sk-your-real-bocha-key          # https://open.bochaai.com 获取
JWT_SECRET=刚才生成的 64 位字符串

# 前端端口（轻量服务器默认 80）
FRONTEND_PORT=80
```

> **飞书告警**：个人使用可暂时不配置 `FEISHU_WEBHOOK_URL`，告警仅记录到 Alertmanager，不推送通知。

### 4.3 执行部署前检查

```bash
bash deploy/pre-deploy-check.sh
```

预期输出所有 ✓，若有 ✗ 按提示修复。

### 4.4 一键部署

```bash
# 预览流程（不实际执行）
bash deploy/deploy.sh --dry-run

# 正式部署（约 15-20 分钟，含镜像构建）
bash deploy/deploy.sh
```

部署脚本会自动执行 8 个阶段：检查 → 备份 → 构建镜像 → 启动后端 → 启动前端 → 启动监控 → 验证 → 清理。

### 4.5 验证部署

```bash
# 健康检查
curl http://localhost:8000/api/v1/health/live
# 期望返回 {"status":"ok",...}

# 前端访问（浏览器）
# http://43.136.xx.xx/
```

---

## 5. 域名与 HTTPS（可选）

### 5.1 域名注册（可选，个人用 IP 访问可跳过）

国内云平台均可注册域名：

| 平台 | 域名价格 | 说明 |
|------|---------|------|
| 腾讯云 | 35-75 元/年 | .com 域名首年优惠 |
| 阿里云 | 35-75 元/年 | .com 域名首年优惠 |

### 5.2 域名备案（国内服务器必须）

> **重要**：使用国内服务器 + 域名访问，**必须完成 ICP 备案**，否则域名无法解析到服务器。

备案流程（以腾讯云为例）：

1. 进入 https://console.cloud.tencent.com/beian
2. 提交备案信息（身份证 + 服务器实例 + 域名）
3. 等待审核（通常 7-20 个工作日）
4. 审核通过后获得备案号，显示在网站底部

> **备案期间**：可用服务器公网 IP 直接访问，不影响使用。

### 5.3 配置域名解析

备案通过后，在域名管理页添加 A 记录：

| 主机记录 | 记录类型 | 记录值 | TTL |
|---------|---------|--------|-----|
| @ | A | 43.136.xx.xx | 600 |
| www | A | 43.136.xx.xx | 600 |

验证解析：

```bash
dig +short bos-studio.tech
# 应返回 43.136.xx.xx
```

### 5.4 申请 SSL 证书（免费）

```bash
# 安装 certbot
sudo apt update && sudo apt install -y certbot

# 申请证书（需先停止占用 80 端口的服务）
docker compose -f docker-compose.prod.yml --env-file .env.prod stop frontend
sudo certbot certonly --standalone -d bos-studio.tech -d www.bos-studio.tech

# 证书路径：
#   /etc/letsencrypt/live/bos-studio.tech/fullchain.pem
#   /etc/letsencrypt/live/bos-studio.tech/privkey.pem
```

### 5.5 启用 HTTPS

```bash
# 切换 Nginx 配置为 HTTPS 版
cp deploy/nginx-ssl.conf deploy/nginx.conf
sed -i 's/your-domain.com/bos-studio.tech/g' deploy/nginx.conf

# 编辑 docker-compose.prod.yml，frontend 服务添加证书挂载
# volumes:
#   - ./deploy/nginx.conf:/etc/nginx/conf.d/default.conf:ro
#   - /etc/letsencrypt:/etc/letsencrypt:ro   # 新增
# ports:
#   - "443:443"   # 新增
#   - "80:80"

# 重启前端
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d frontend

# 验证 HTTPS
curl -I https://bos-studio.tech/
```

### 5.6 配置证书自动续期

```bash
# 每月 1 号自动续期
sudo crontab -e
# 添加：
0 3 1 * * certbot renew --quiet && docker restart sekb-frontend
```

---

## 6. 日常运维

### 6.1 常用命令

> ⚠ 以下所有 docker compose 命令均需添加 --env-file .env.prod 参数，下文为简洁已省略

```bash
cd /opt/self-evolving-kb

# 查看服务状态
docker compose -f docker-compose.prod.yml --env-file .env.prod ps
docker compose -f docker-compose.monitoring.yml ps

# 查看日志
docker compose -f docker-compose.prod.yml --env-file .env.prod logs -f backend
docker compose -f docker-compose.prod.yml --env-file .env.prod logs -f frontend

# 重启服务
docker compose -f docker-compose.prod.yml --env-file .env.prod restart backend

# 更新代码并重新部署
git pull origin main
bash deploy/deploy.sh --skip-check   # 跳过检查，直接部署

# 停止所有服务
docker compose -f docker-compose.prod.yml --env-file .env.prod down
docker compose -f docker-compose.monitoring.yml down
```

### 6.2 监控面板访问

监控端口不对外暴露，通过 SSH 隧道访问：

```bash
# 本地机器执行，建立 SSH 隧道
ssh -i ~/.ssh/sekb_cloud_key -L 3001:localhost:3001 -L 9091:localhost:9091 -L 9093:localhost:9093 deploy@43.136.xx.xx

# 保持此终端不关，浏览器访问：
# Grafana:    http://localhost:3001  (admin/admin，首次登录后改密码)
# Prometheus: http://localhost:9091
# Alertmanager: http://localhost:9093
```

### 6.3 数据备份

```bash
# 手动备份
./deploy/backup.sh

# 配置定时备份（每日凌晨 3 点）
crontab -e
# 添加：
0 3 * * * cd /opt/self-evolving-kb && ./deploy/backup.sh >> /var/log/sekb-backup.log 2>&1

# 查看备份
ls -la /backup/
./deploy/restore.sh --list
```

### 6.4 磁盘空间管理

轻量服务器磁盘有限（60GB），定期清理：

```bash
# 查看 Docker 磁盘占用
docker system df

# 清理悬挂镜像和停止的容器
docker system prune -f

# 清理旧备份（保留 7 天）
find /backup -mtime +7 -delete

# 查看日志占用
du -sh /var/lib/docker/containers/*/  # 容器日志
```

---

## 7. 成本优化建议

### 7.1 月度成本明细

| 项目 | 月成本 | 说明 |
|------|--------|------|
| 轻量服务器 2核4G | ~22 元（新人年付 268 元） | 续费约 40 元/月 |
| 域名（可选） | ~3 元/月 | 年付 35-75 元 |
| SSL 证书 | 0 元 | Let's Encrypt 免费 |
| 备案 | 0 元 | 免费 |
| DeepSeek API | 按量付费 | 个人用约 10-50 元/月 |
| 博查搜索 API | 按量付费 | 个人用约 5-20 元/月 |
| **合计** | **~35-75 元/月** | 含 API 调用费用 |

### 7.2 省钱技巧

1. **新人特价最大化**：
   - 腾讯云/阿里云新人首购年付最优惠
   - 可注册多个账号（家人/朋友的），分别享受新人价
   - 年付比月付便宜 30-50%

2. **续费策略**：
   - 新人价通常只享 1 年
   - 续费前关注「续费优惠活动」（双 11、618 等）
   - 可考虑到期后换平台重新购买（数据需迁移）

3. **降低 API 成本**：
   - DeepSeek 缓存命中价格更低，相似问题会复用缓存
   - 博查搜索可降级为「仅必要时联网」（config.yaml 调整 `intent_routing`）
   - 评估测试（`sekb eval`）少跑，每次会消耗较多 token

4. **降低监控成本**：
   - 个人使用可关闭监控栈：`docker compose -f docker-compose.monitoring.yml down`
   - 节省约 1G 内存，2G 机型必备

### 7.3 2G 内存机型的精简方案

若使用 2核2G 机型（最低成本），需精简部署：

```bash
# 1. 不启动监控栈（节省 1G+ 内存）
# 部署时加 --skip-monitor
bash deploy/deploy.sh --skip-monitor

# 2. 编辑 docker-compose.prod.yml，降低后端内存限制
# deploy:
#   resources:
#     limits:
#       memory: 1G     # 从 4G 降到 1G
#       cpus: "1.5"

# 3. 增加交换分区（见 3.6 节）

# 4. 关闭非必要功能
# 编辑 backend/config.yaml：
#   reflection:
#     strategy: sampling   # always 改为 sampling，减少 Critic 调用
```

---

## 8. 从个人版升级到生产版

当用户量增长或需要正式商用时，可按以下路径升级：

### 8.1 升级路径

```
阶段 1（个人）: 轻量 2核4G          ~22 元/月
    ↓ 用户增加
阶段 2（小团队）: 轻量 4核8G        ~80 元/月
    ↓ 或迁移到 ECS
阶段 3（生产）: ECS + RDS + SLB     ~300+ 元/月
    ↓ 流量增长
阶段 4（规模化）: ECS 集群 + ACK    ~1000+ 元/月
```

### 8.2 何时升级

| 信号 | 建议动作 |
|------|---------|
| 内存使用率 > 80% | 升级到 4核8G |
| CPU 使用率 > 70% | 升级到 4核 |
| 磁盘使用 > 80% | 扩容磁盘或清理日志 |
| 用户数 > 50 人 | 迁移到 ECS + 独立数据库 |
| 需要高可用 | 多实例 + 负载均衡 |

### 8.3 迁移到 ECS（数据无损）

```bash
# 1. 在新 ECS 服务器安装 Docker
# 2. 克隆代码
# 3. 备份轻量服务器数据
./deploy/backup.sh
# 4. 传输备份到新服务器
scp -r /backup/20260819_030000 deploy@new-ecs-ip:/tmp/
# 5. 在新服务器恢复
./deploy/restore.sh --date 20260819_030000
# 6. 修改 DNS 解析到新 IP
# 7. 验证无误后释放轻量服务器
```

---

## 附录 A：各平台快速入口

| 平台 | 轻量服务器控制台 | 备案入口 | 域名注册 |
|------|----------------|---------|---------|
| 腾讯云 | https://console.cloud.tencent.com/lighthouse | https://console.cloud.tencent.com/beian | https://dnspod.cloud.tencent.com |
| 阿里云 | https://swas.console.aliyun.com | https://beian.aliyun.com | https://wanwang.aliyun.com |
| 华为云 | https://console.huaweicloud.com/swr | https://beian.huaweicloud.com | https://www.huaweicloud.com/product/domain.html |

## 附录 B：一键部署命令速查

```bash
# ============ 服务器初始化（首次） ============
# SSH 登录
ssh -i ~/.ssh/sekb_cloud_key deploy@服务器IP

# 配置 Docker 镜像加速
sudo tee /etc/docker/daemon.json <<'EOF'
{"registry-mirrors":["https://docker.mirrors.ustc.edu.cn","https://mirror.ccs.tencentyun.com"]}
EOF
sudo systemctl restart docker

# ============ 部署 SEKB ============
cd /opt/self-evolving-kb
git clone https://github.com/your-org/SelfEvolvingKnowledgeBase.git .
cp deploy/.env.prod.example .env.prod
# 编辑 .env.prod 填入密钥
openssl rand -hex 32    # 生成 JWT_SECRET

# 检查 + 部署
bash deploy/pre-deploy-check.sh
bash deploy/deploy.sh

# ============ 日常更新 ============
git pull origin main
bash deploy/deploy.sh --skip-check --skip-build

# ============ 访问监控（SSH 隧道） ============
# 本地执行：
ssh -i ~/.ssh/sekb_cloud_key -L 3001:localhost:3001 deploy@服务器IP
# 浏览器：http://localhost:3001

# ============ 停止服务 ============
docker compose -f docker-compose.prod.yml --env-file .env.prod down
docker compose -f docker-compose.monitoring.yml down
```

## 附录 C：故障排查

### 拉取镜像超时

```bash
# 检查镜像加速是否生效
docker info | grep -A5 "Registry Mirrors"
# 应显示配置的加速地址

# 手动拉取测试
docker pull prom/prometheus
# 若失败，检查 /etc/docker/daemon.json
```

### 后端 OOM（内存不足）

```bash
# 查看内存使用
free -h
docker stats

# 2G 机型必做：加 swap
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile

# 降低后端内存限制
# 编辑 docker-compose.prod.yml，memory: 4G → 1G
```

### 域名无法访问

```bash
# 1. 检查备案状态（国内服务器必须备案）
# 2. 检查 DNS 解析
dig bos-studio.tech
# 3. 检查云防火墙端口是否开放
# 4. 检查域名是否被墙
```

### 更多问题

- [PRODUCTION-DEPLOY.md](./02-PRODUCTION-DEPLOY.md) - 完整线上部署手册
- [PRE-DEPLOY-CHECKLIST.md](./06-PRE-DEPLOY-CHECKLIST.md) - 部署前检查清单
- [ALERTING-TROUBLESHOOTING.md](./07-ALERTING-TROUBLESHOOTING.md) - 告警故障排查
