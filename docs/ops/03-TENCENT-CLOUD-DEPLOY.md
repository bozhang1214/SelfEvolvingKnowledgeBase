---
title: 腾讯云轻量服务器部署手册
layer: 运维层
owner: SEKB Team
status: active
version: v1.0.0
last-updated: 2026-09-09
based-on-commit: f6eea56
related: [docs/tech/09-OBSERVABILITY]
---

# SEKB 腾讯云轻量服务器部署手册

> 平台：腾讯云轻量应用服务器 Lighthouse
> 套餐：2核4G 4M带宽 60GB SSD（新人价 268 元/年）
> 适用：个人 / 小团队使用 SEKB
> 预计耗时：购买 5 分钟 + 部署 30 分钟

---

## 目录

- [一、前置项清单（部署前必做）](#一前置项清单部署前必做)
- [二、购买腾讯云轻量服务器](#二购买腾讯云轻量服务器)
- [三、服务器初始化配置](#三服务器初始化配置)
- [四、部署 SEKB 项目](#四部署-sekb-项目)
- [五、域名与 HTTPS（可选）](#五域名与-https可选)
- [六、日常运维](#六日常运维)
- [七、成本与续费优化](#七成本与续费优化)
- [八、故障排查](#八故障排查)

---

## 一、前置项清单（部署前必做）

部署前请逐项准备，**所有必做项完成后才能开始部署**。

### 1.1 账号与实名认证（必做）

- [ ] **注册腾讯云账号**
  - 访问 https://cloud.tencent.com/
  - 点击「免费注册」，用手机号或邮箱注册

- [ ] **完成实名认证**（个人认证即可）
  - 进入 https://console.cloud.tencent.com/developer/auth
  - 选择「个人认证」
  - 上传身份证正反面 + 人脸识别
  - 等待审核（通常即时通过）

> **为什么必做**：购买国内服务器和域名备案都要求实名认证。

### 1.2 外部 API 密钥（必做）

SEKB 依赖两个外部 API，部署前必须准备好：

- [ ] **DeepSeek API Key**（LLM 主力模型）
  - 访问 https://platform.deepseek.com/
  - 注册账号 → 充值（最低 10 元即可）
  - 创建 API Key，格式如 `sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`
  - 记录此 Key，部署时填入 `.env.prod`

- [ ] **博查搜索 API Key**（联网搜索工具）
  - 访问 https://open.bochaai.com/
  - 注册账号 → 获取 API Key
  - 记录此 Key，格式如 `sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`

> **提示**：两个 API 都是按量付费，个人使用月成本约 15-70 元。

### 1.3 域名（可选，推荐）

- [ ] **注册域名**（若已有可跳过）
  - 腾讯云域名注册：https://dnspod.cloud.tencent.com/
  - 推荐 `.com` 域名，首年约 35-55 元
  - 或使用 `.top`/`.xyz` 等更便宜（首年 9-15 元）

- [ ] **域名实名认证**
  - 域名注册后需完成实名认证（身份证 + 手机号）
  - 等待认证通过（通常 1-3 工作日）

> **是否必须**：不是必须。可先用服务器公网 IP 访问（`http://43.136.xx.xx`），后续再配域名。但若要 HTTPS，必须有域名。

### 1.4 本地环境（必做）

在你的开发机（Mac/Windows）上准备：

- [ ] **SSH 客户端**
  - Mac/Linux：自带终端
  - Windows：Windows Terminal / PowerShell / WSL

- [ ] **Git**（用于克隆代码）
  - 验证：`git --version`

- [ ] **curl**（用于验证部署）
  - Mac/Linux 自带，Windows 10+ 自带

### 1.5 前置项检查清单

| # | 检查项 | 状态 | 说明 |
|---|--------|------|------|
| 1 | 腾讯云账号已注册 | ☐ | https://cloud.tencent.com/ |
| 2 | 实名认证已通过 | ☐ | 个人认证 |
| 3 | DeepSeek API Key 已获取 | ☐ | sk-xxx 格式 |
| 4 | 博查 API Key 已获取 | ☐ | sk-xxx 格式 |
| 5 | 本地 SSH 客户端就绪 | ☐ | 终端 / PowerShell |
| 6 | 本地 Git 已安装 | ☐ | git --version |
| 7 | 域名已注册（可选） | ☐ | 后续可补 |
| 8 | 域名已实名认证（可选） | ☐ | 后续可补 |

**所有必做项（1-6）✓ 后，继续下一步。**

---

## 二、购买腾讯云轻量服务器

### 2.1 进入购买页

1. 登录腾讯云控制台
2. 访问 https://console.cloud.tencent.com/lighthouse
3. 点击「新建实例」

### 2.2 选择配置

| 配置项 | 选择 | 说明 |
|--------|------|------|
| **地域** | 广州 / 上海 / 北京（就近选） | 国内访问快 |
| **镜像** | **Docker**（应用镜像） | ⚠️ 务必选 Docker 镜像，开机即装好 Docker |
| **实例规格** | 2核4G 4M带宽 | 新人价 268 元/年 |
| **系统盘** | 60GB SSD | 默认即可 |
| **购买时长** | 1 年 | 享新人最大折扣 |
| **流量包** | 600GB/月 | 个人使用足够 |

> **关键**：镜像务必选「Docker」应用镜像，否则需要手动安装 Docker，增加部署难度。

### 2.3 设置密码

- 购买时设置 root 密码（或购买后在控制台重置）
- **牢记此密码**，后续 SSH 登录用

### 2.4 完成购买

- 确认订单：268 元/年
- 支付（微信/支付宝）
- 等待 1-2 分钟，状态变为「运行中」

### 2.5 获取服务器信息

在控制台「实例列表」中找到：

- **公网 IP**：例如 `43.136.xx.xx`（后续访问和登录用）
- **实例 ID**：例如 `lh-xxxxxxxx`
- **内网 IP**：例如 `10.0.0.5`

### 2.6 开放防火墙端口

腾讯云轻量服务器有云防火墙，需在控制台开放端口：

进入「实例详情 → 防火墙」，添加规则：

| 应用 | 协议 | 端口 | 是否必开 |
|------|------|------|---------|
| SSH | TCP | 22 | ✅ 必开 |
| HTTP | TCP | 80 | ✅ 必开 |
| HTTPS | TCP | 443 | 配置证书后开 |

> **不要开放**的端口：8000（backend）、3001（Grafana）、9091（Prometheus）、9093（Alertmanager）、3101（Loki）、5001（feishu-webhook）。这些通过 SSH 隧道访问。

### 2.7 连接服务器测试

```bash
# 在本地终端执行
ssh root@43.136.xx.xx
# 输入购买时设置的密码

# 验证 Docker 环境（因为选了 Docker 镜像）
docker --version          # 应显示 24.0+
docker compose version    # 应显示 v2.20+

# 若显示正常，说明服务器准备就绪
exit
```

---

## 三、服务器初始化配置

### 3.1 SSH 登录服务器

```bash
ssh root@43.136.xx.xx
```

以下所有命令在服务器上执行。

### 3.2 配置 Docker 镜像加速（国内必做）

```bash
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json <<'EOF'
{
  "registry-mirrors": [
    "https://mirror.ccs.tencentyun.com",
    "https://docker.mirrors.ustc.edu.cn",
    "https://hub-mirror.c.163.com"
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

# 验证加速是否生效
docker info | grep -A5 "Registry Mirrors"
# 应显示配置的加速地址
```

> **为什么必做**：不配置加速，拉取 prometheus/grafana/loki 等镜像会超时失败。

### 3.3 创建部署用户（推荐）

避免长期用 root 操作：

```bash
# 创建普通用户
useradd -m -s /bin/bash deploy
usermod -aG docker deploy
echo "deploy ALL=(ALL) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/deploy

# 设置 deploy 用户密码
passwd deploy
# 输入两次新密码

# 切换到 deploy 用户
su - deploy
```

### 3.4 配置 SSH 密钥登录（推荐）

在**本地机器**执行：

```bash
# 1. 生成密钥对（若已有可跳过）
ssh-keygen -t ed25519 -C "sekb-tencent" -f ~/.ssh/sekb_tencent_key -N ""

# 2. 上传公钥到服务器
ssh-copy-id -i ~/.ssh/sekb_tencent_key.pub deploy@43.136.xx.xx
# 输入 deploy 用户密码

# 3. 测试免密登录
ssh -i ~/.ssh/sekb_tencent_key deploy@43.136.xx.xx
# 应直接登录，无需密码
```

后续所有 SSH 操作使用：
```bash
ssh -i ~/.ssh/sekb_tencent_key deploy@43.136.xx.xx
```

### 3.5 配置系统防火墙（可选，云防火墙已覆盖）

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable
sudo ufw status
```

---

## 四、部署 SEKB 项目

### 4.1 克隆代码

```bash
# 以 deploy 用户登录
ssh -i ~/.ssh/sekb_tencent_key deploy@43.136.xx.xx

# 创建部署目录
sudo mkdir -p /opt/self-evolving-kb
sudo chown deploy:deploy /opt/self-evolving-kb
cd /opt/self-evolving-kb

# 克隆代码
git clone https://github.com/your-org/SelfEvolvingKnowledgeBase.git .
```

> **替换仓库地址**：将 `your-org` 替换为你的 GitHub 用户名或组织名。

### 4.2 配置环境变量

```bash
cp deploy/.env.prod.example .env.prod

# 生成 JWT 密钥（复制输出的 64 位字符串）
openssl rand -hex 32
```

编辑 `.env.prod`：

```bash
nano .env.prod
# 或用 vim
vim .env.prod
```

**必须修改以下项**（将占位符替换为真实值）：

```bash
# ============ 必填项 ============

# DeepSeek API Key（前置项准备的）
DEEPSEEK_API_KEY=sk-你的真实DeepSeek密钥

# 博查搜索 API Key（前置项准备的）
BOCHA_API_KEY=sk-你的真实博查密钥

# JWT 签名密钥（用上面 openssl 生成的 64 位字符串替换）
JWT_SECRET=刚才生成的64位字符串

# ============ 可选项 ============

# 前端端口（默认 80）
FRONTEND_PORT=80

# 飞书告警通知（个人用可暂时不配）
# FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/your-hook-id
# FEISHU_SECRET=your-signing-secret
```

保存退出（nano：`Ctrl+O` → `Enter` → `Ctrl+X`）。

### 4.3 验证配置

```bash
# 检查必填项是否已替换
grep -E "DEEPSEEK_API_KEY|BOCHA_API_KEY|JWT_SECRET" .env.prod | grep -v "change-me\|please-change"
# 若有输出，说明仍有未修改的占位符

# 设置文件权限
chmod 600 .env.prod

# 校验 compose 配置
docker compose -f docker-compose.prod.yml --env-file .env.prod config --quiet \
  && echo "✓ 配置正确" || echo "✗ 配置错误"
```

### 4.4 执行部署前检查

```bash
bash deploy/pre-deploy-check.sh
```

预期输出所有 ✓。若有 ✗，按提示修复。

### 4.5 一键部署

```bash
# 预览部署流程（不实际执行）
bash deploy/deploy.sh --dry-run

# 正式部署（约 15-20 分钟，含镜像构建）
bash deploy/deploy.sh
```

部署脚本会自动执行 8 个阶段：
1. 部署前检查
2. 部署前备份
3. 构建镜像（backend + frontend）
4. 启动后端（等待健康检查 90 秒）
5. 启动前端
6. 启动监控栈
7. 部署后验证
8. 清理与收尾

### 4.6 验证部署

部署完成后，脚本会自动验证。也可手动验证：

```bash
# 健康检查
curl http://localhost:8000/api/v1/health/live
# 期望返回 {"status":"ok",...}

# 浏览器访问前端
# http://43.136.xx.xx/
```

### 4.7 首次使用

1. **浏览器访问** `http://43.136.xx.xx/`
2. **注册账号**（首个用户即为管理员）
3. **登录** → 开始使用 SEKB

---

## 五、域名与 HTTPS（可选）

> **前提**：已完成前置项 1.3（域名注册 + 实名认证）

### 5.1 域名备案（国内服务器必须）

> **重要**：使用国内服务器 + 域名访问，**必须完成 ICP 备案**，否则域名无法解析到服务器。

备案流程（腾讯云）：

1. 访问 https://cloud.tencent.com/product/ba
2. 点击「开始备案」
3. 提交备案信息：
   - 主体信息（身份证 + 手机号）
   - 域名信息
   - 服务器实例（选择你购买的轻量服务器）
4. 等待腾讯云初审（1-2 工作日）
5. 工信部短信核验（24 小时内）
6. 管局审核（7-20 工作日）
7. 审核通过 → 获得备案号

> **备案期间**：可用服务器公网 IP 直接访问，不影响使用。

### 5.2 配置域名解析

备案通过后，在腾讯云 DNSPod 添加 A 记录：

进入 https://console.dnspod.cn/dns/list：

| 主机记录 | 记录类型 | 记录值 | TTL |
|---------|---------|--------|-----|
| @ | A | 43.136.xx.xx | 600 |
| www | A | 43.136.xx.xx | 600 |

验证解析：

```bash
dig +short bos-studio.tech
# 应返回 43.136.xx.xx
```

### 5.3 申请 SSL 证书（免费）

在服务器上执行：

```bash
# 安装 certbot
sudo apt update && sudo apt install -y certbot

# 申请证书前先停止占用 80 端口的服务
docker compose -f docker-compose.prod.yml --env-file .env.prod stop frontend

# 申请 Let's Encrypt 证书
sudo certbot certonly --standalone -d bos-studio.tech -d www.bos-studio.tech
# 按提示输入邮箱、同意条款

# 证书文件位置：
#   /etc/letsencrypt/live/bos-studio.tech/fullchain.pem
#   /etc/letsencrypt/live/bos-studio.tech/privkey.pem
```

### 5.4 启用 HTTPS

```bash
# 1. 切换 Nginx 配置为 HTTPS 版
cp deploy/nginx-ssl.conf deploy/nginx.conf

# 2. 替换域名占位符
sed -i 's/your-domain.com/bos-studio.tech/g' deploy/nginx.conf

# 3. 编辑 docker-compose.prod.yml，frontend 服务添加证书挂载
#    找到 frontend 服务，修改为：
#    ports:
#      - "443:443"
#      - "80:80"
#    volumes:
#      - ./deploy/nginx.conf:/etc/nginx/conf.d/default.conf:ro
#      - /etc/letsencrypt:/etc/letsencrypt:ro   # 新增：挂载证书
nano docker-compose.prod.yml

# 4. 重启前端应用 HTTPS 配置
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d frontend

# 5. 验证 HTTPS
curl -I https://你的域名.com/
# 期望返回 HTTP/2 200

# 6. 验证 HTTP 自动跳转
curl -I http://你的域名.com/
# 期望返回 301 Location: https://...
```

### 5.5 配置证书自动续期

```bash
# 每月 1 号自动续期并重启前端
sudo crontab -e

# 添加以下行：
0 3 1 * * certbot renew --quiet && docker restart sekb-frontend
```

---

## 六、日常运维

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

# 重启单个服务
docker compose -f docker-compose.prod.yml --env-file .env.prod restart backend

# 更新代码并重新部署
git pull origin main
bash deploy/deploy.sh --skip-check

# 停止所有服务
docker compose -f docker-compose.prod.yml --env-file .env.prod down
docker compose -f docker-compose.monitoring.yml down

# 启动所有服务
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d
docker compose -f docker-compose.monitoring.yml up -d
```

### 6.2 访问监控面板

监控端口不对外暴露，通过 SSH 隧道访问。

在**本地机器**执行：

```bash
# 建立 SSH 隧道（保持终端不关）
ssh -i ~/.ssh/sekb_tencent_key \
  -L 3001:localhost:3001 \
  -L 9091:localhost:9091 \
  -L 9093:localhost:9093 \
  deploy@43.136.xx.xx
```

浏览器访问（保持 SSH 隧道不关）：

| 服务 | 地址 | 账号 |
|------|------|------|
| Grafana | http://localhost:3001 | admin / admin（首次登录后改密码） |
| Prometheus | http://localhost:9091 | 无需认证 |
| Alertmanager | http://localhost:9093 | 无需认证 |

### 6.3 数据备份

```bash
# 手动备份
./deploy/backup.sh

# 查看备份列表
ls -la /backup/
./deploy/restore.sh --list

# 配置定时备份（每日凌晨 3 点）
crontab -e
# 添加：
0 3 * * * cd /opt/self-evolving-kb && ./deploy/backup.sh >> /var/log/sekb-backup.log 2>&1
```

### 6.4 磁盘空间管理

轻量服务器磁盘 60GB，定期清理：

```bash
# 查看 Docker 磁盘占用
docker system df

# 清理悬挂镜像和停止的容器
docker system prune -f

# 清理旧备份（保留 7 天）
find /backup -mtime +7 -delete

# 查看磁盘使用
df -h
```

---

## 七、成本与续费优化

### 7.1 月度成本明细

| 项目 | 月成本 | 说明 |
|------|--------|------|
| 轻量服务器 2核4G | ~22 元（新人年付 268 元） | 续费约 40 元/月 |
| 域名（可选） | ~3 元 | 年付 35-55 元 |
| SSL 证书 | 0 元 | Let's Encrypt 免费 |
| 备案 | 0 元 | 免费 |
| DeepSeek API | 10-50 元 | 按量付费 |
| 博查搜索 API | 5-20 元 | 按量付费 |
| **合计** | **~35-75 元/月** | 含 API 调用 |

### 7.2 续费省钱技巧

1. **关注续费同价活动**：
   - 腾讯云部分轻量套餐标注「续费同价」
   - 购买时优先选择此类套餐

2. **续费优惠活动**：
   - 双 11、618 等大促期间有续费折扣
   - 关注腾讯云公众号或邮件通知

3. **延长购买时长**：
   - 3 年付通常比 1 年付便宜 20-30%
   - 适合确定长期使用的场景

4. **降低 API 成本**：
   - DeepSeek 缓存命中价格更低
   - 评估测试（`sekb eval`）少跑，每次消耗较多 token

### 7.3 2G 内存机型精简方案（预算极紧时）

若改用 2核2G 套餐（99 元/年），需精简部署：

```bash
# 1. 加 2G swap 防 OOM
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# 2. 不启动监控栈（节省 1G+ 内存）
bash deploy/deploy.sh --skip-monitor

# 3. 降低后端内存限制
# 编辑 docker-compose.prod.yml，backend 的 memory: 4G → 1G
```

---

## 八、故障排查

### 8.1 拉取镜像超时

```bash
# 检查镜像加速是否生效
docker info | grep -A5 "Registry Mirrors"

# 手动拉取测试
docker pull prom/prometheus
# 若失败，检查 /etc/docker/daemon.json 配置
```

### 8.2 后端 OOM（内存不足）

```bash
# 查看内存使用
free -h
docker stats

# 解决方案：
# 1. 加 swap（见 7.3 节）
# 2. 降低后端内存限制
# 3. 关闭监控栈（--skip-monitor）
```

### 8.3 前端 502 Bad Gateway

```bash
# 检查后端是否健康
curl http://localhost:8000/api/v1/health/live

# 检查 nginx 配置
docker exec sekb-frontend nginx -t

# 查看 nginx 日志
docker compose -f docker-compose.prod.yml --env-file .env.prod logs frontend --tail 20
```

### 8.4 域名无法访问

```bash
# 1. 检查备案状态（国内服务器必须备案）
# 2. 检查 DNS 解析
dig bos-studio.tech
# 3. 检查云防火墙是否开放 80/443 端口
# 4. 检查证书是否过期
sudo openssl x509 -enddate -noout \
  -in /etc/letsencrypt/live/bos-studio.tech/fullchain.pem
```

### 8.5 SSH 隧道访问监控失败

```bash
# 确认监控服务在运行
docker compose -f docker-compose.monitoring.yml ps

# 确认 SSH 隧道建立成功
# 本地执行后应显示 "Connected to 43.136.xx.xx"

# 确认本地端口未被占用
lsof -i :3001
# 若被占用，换一个本地端口：-L 4001:localhost:3001
```

### 8.6 更多问题

- [PRE-DEPLOY-CHECKLIST.md](./06-PRE-DEPLOY-CHECKLIST.md) - 部署前检查清单
- [ALERTING-TROUBLESHOOTING.md](./07-ALERTING-TROUBLESHOOTING.md) - 告警故障排查
- [PRODUCTION-DEPLOY.md](./02-PRODUCTION-DEPLOY.md) - 完整线上部署手册

---

## 附录 A：完整部署命令速查

```bash
# ============ 前置准备（本地） ============
# 1. 准备 DeepSeek + 博查 API Key
# 2. 注册腾讯云 + 实名认证
# 3. 购买轻量服务器（Docker 镜像，2核4G 4M）
# 4. 开放防火墙端口 22/80/443

# ============ 服务器初始化 ============
ssh root@43.136.xx.xx

# 配置 Docker 镜像加速
sudo tee /etc/docker/daemon.json <<'EOF'
{"registry-mirrors":["https://mirror.ccs.tencentyun.com","https://docker.mirrors.ustc.edu.cn"]}
EOF
sudo systemctl restart docker

# 创建部署用户
useradd -m -s /bin/bash deploy
usermod -aG docker deploy
echo "deploy ALL=(ALL) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/deploy
passwd deploy

# 退出，用 deploy 用户登录
exit
ssh deploy@43.136.xx.xx

# ============ 部署 SEKB ============
cd /opt
sudo mkdir -p self-evolving-kb && sudo chown deploy:deploy self-evolving-kb
cd self-evolving-kb
git clone https://github.com/your-org/SelfEvolvingKnowledgeBase.git .
cp deploy/.env.prod.example .env.prod
openssl rand -hex 32    # 生成 JWT_SECRET
nano .env.prod          # 填入真实密钥
chmod 600 .env.prod

# 检查 + 部署
bash deploy/pre-deploy-check.sh
bash deploy/deploy.sh

# ============ 日常更新 ============
cd /opt/self-evolving-kb
git pull origin main
bash deploy/deploy.sh --skip-check --skip-build

# ============ 访问监控（SSH 隧道） ============
# 本地执行：
ssh -i ~/.ssh/sekb_tencent_key \
  -L 3001:localhost:3001 -L 9091:localhost:9091 \
  deploy@43.136.xx.xx
# 浏览器：http://localhost:3001（Grafana）

# ============ 停止服务 ============
docker compose -f docker-compose.prod.yml --env-file .env.prod down
docker compose -f docker-compose.monitoring.yml down
```

## 附录 B：腾讯云控制台快速入口

| 功能 | 地址 |
|------|------|
| 轻量服务器控制台 | https://console.cloud.tencent.com/lighthouse |
| 防火墙配置 | 实例详情 → 防火墙 |
| 域名注册 | https://dnspod.cloud.tencent.com |
| DNS 解析 | https://console.dnspod.cn/dns/list |
| ICP 备案 | https://cloud.tencent.com/product/ba |
| 费用中心 | https://console.cloud.tencent.com/expense |
