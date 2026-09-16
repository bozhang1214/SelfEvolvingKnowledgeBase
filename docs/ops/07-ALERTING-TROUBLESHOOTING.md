---
title: 告警模块故障排查指南
layer: 运维层
owner: SEKB Team
status: active
version: v1.0.0
last-updated: 2026-09-09
based-on-commit: f6eea56
related: [docs/tech/09-OBSERVABILITY]
---

# 告警模块故障排查指南

> 适用范围：Phase 4 告警通知链路（Prometheus 告警规则 → Alertmanager 路由 → feishu-webhook 中转 → 飞书/邮件）
> 相关文件：[deploy/alertmanager.yml](../../deploy/alertmanager.yml)、[deploy/alerts.yml](../../deploy/alerts.yml)、[deploy/feishu-webhook/](../../deploy/feishu-webhook/)、[docker-compose.monitoring.yml](../../docker-compose.monitoring.yml)

---

## 一、告警模块架构概览

```
Prometheus (9091) ──告警评估──▶ Alertmanager (9093) ──webhook──▶ feishu-webhook (5001) ──HTTP POST──▶ 飞书群
                                      │                                    ▲
                                      └──email──▶ SMTP 服务器 ──▶ 邮箱     │
                                                                           │
                                          backend（应用侧告警，如「科技资讯日报生成失败」）
```

**两条来源，同一个出口**：Prometheus 那条管的是「指标异常」；应用侧那条管的是
「用户能感知的功能失败」。后者由 `backend/app/core/alerts.py: send_alert()` 直接 POST 到
同一个网关（`http://sekb-feishu-webhook:5001/webhook`），**格式也用 Alertmanager 形状**，
所以网关无需区分来源。

**涉及服务**：

| 服务 | 容器名 | 端口 | 作用 |
|------|--------|------|------|
| prometheus | sekb-prometheus | 9091 | 评估告警规则，触发告警 |
| alertmanager | sekb-alertmanager | 9093 | 接收告警，去重、分组、路由 |
| feishu-webhook | sekb-feishu-webhook | 5001 | 将 Alertmanager **或应用侧**告警转为飞书卡片消息 |

**依赖关系**：alertmanager 依赖 feishu-webhook 健康后才会启动。

**应用侧告警的字段约定**（改一边要改另一边）：

| 字段 | 含义 | 卡片上的效果 |
|------|------|--------------|
| `labels.alertname` | 告警标题 | 卡片首行；网关按它查「这是什么/建议」 |
| `labels.severity` | `critical` / `warning` / `info` | `critical` → 红色标题，其余 → 橙色 |
| `labels.source` | 来源模块（`news` 等） | 展示「来源: news」，并给「查看资讯」按钮 |
| `labels.service` | 服务名 | 展示「服务: sekb」 |
| `annotations.summary` / `description` | 摘要 / 详情 | 正文两行 |

**目前接了哪些应用侧告警**：

| 触发点 | 标题 | 级别 | 代码 |
|--------|------|------|------|
| 日报/周报/月报生成失败（手动或定时，含重试用尽） | `科技资讯{日报\|周报\|月报}生成失败` | critical | `backend/app/agents/news/service.py: write_status()` |

> 同一故障 5 分钟内只推一次（`alerts.DEDUP_WINDOW_S`）：调度器失败会重试一次，
> 不去重就会为同一个错误推两张卡片。

---

## 二、快速诊断流程

发现告警未收到时，按以下顺序快速定位：

```bash
# 1. 检查所有告警相关容器状态
docker ps -a --filter "name=sekb-alertmanager" --filter "name=sekb-feishu-webhook" --filter "name=sekb-prometheus" \
  --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

# 2. 检查 Alertmanager 是否有告警待发送
curl -sf http://100.71.24.105:9093/api/v2/alerts | python3 -m json.tool

# 3. 检查 Prometheus 是否有触发的告警
curl -sf http://100.71.24.105:9091/api/v1/alerts | python3 -m json.tool

# 4. 手动测试 feishu-webhook 是否正常响应
curl -sf http://localhost:5001/health

# 5. 手动推送一条测试告警验证整条链路
curl -X POST http://localhost:5001/webhook \
  -H "Content-Type: application/json" \
  -d '{"alerts":[{"status":"firing","labels":{"alertname":"TestAlert","severity":"warning"},"annotations":{"summary":"测试告警","description":"链路测试"},"startsAt":"2026-08-19T00:00:00Z"}]}'

# 6. 应用侧告警链路（backend → 网关 → 飞书）：一条命令，群里应当收到卡片
docker exec sekb-backend python -c "import asyncio; from app.core.alerts import send_alert; print(asyncio.run(send_alert('告警链路测试', '收到即说明链路正常', source='news')))"
# 或只用网关自带的测试端点（不依赖 backend 代码）
curl -X POST "http://localhost:5001/test?source=news"
```

---

## 三、常见故障与解决步骤

### 故障 1：Alertmanager 容器启动失败

**现象**：`docker ps` 显示 sekb-alertmanager 状态为 `Exited` 或反复重启。

**可能原因与排查**：

#### 原因 1.1：alertmanager.yml 配置语法错误

Alertmanager 对配置文件语法要求严格，不支持环境变量默认值语法 `${VAR:default}`。

```bash
# 校验配置文件语法
docker exec sekb-alertmanager amtool check-config /etc/alertmanager/alertmanager.yml

# 若容器已退出，用临时容器校验
docker run --rm -v "$(pwd)/deploy/alertmanager.yml:/etc/alertmanager/alertmanager.yml:ro" \
  prom/alertmanager:latest amtool check-config /etc/alertmanager/alertmanager.yml
```

**常见语法错误**：

```yaml
# ❌ 错误：Alertmanager 不支持 ${VAR:default} 语法
smtp_smarthost: "${SMTP_HOST:localhost:25}"

# ✅ 正确：使用直接值
smtp_smarthost: "localhost:25"

# ❌ 错误：webhook URL 使用了未定义的环境变量
url: "${FEISHU_WEBHOOK_URL}"

# ✅ 正确：webhook 指向 feishu-webhook 容器（通过 docker 网络服务名访问）
url: "http://feishu-webhook:5001/webhook"
```

**修复后重启**：

```bash
docker compose -f docker-compose.monitoring.yml restart alertmanager
```

#### 原因 1.2：依赖的 feishu-webhook 未就绪

alertmanager 配置了 `depends_on: feishu-webhook`，若 feishu-webhook 健康检查未通过，alertmanager 不会启动。

```bash
# 检查 feishu-webhook 状态
docker ps -a --filter "name=sekb-feishu-webhook"

# 若 feishu-webhook 异常，先修复它（见故障 2），再重启 alertmanager
docker compose -f docker-compose.monitoring.yml restart alertmanager
```

#### 原因 1.3：端口 9093 被占用

```bash
# 检查端口占用
lsof -i :9093 || netstat -tlnp | grep 9093

# 若被占用，停止占用进程或修改 docker-compose.monitoring.yml 中的端口映射
# ALERTMANAGER_PORT=9094 docker compose -f docker-compose.monitoring.yml up -d alertmanager
```

#### 原因 1.4：数据卷权限问题

```bash
# 查看 alertmanager 日志
docker logs sekb-alertmanager --tail 30

# 若提示 "permission denied" 或 "cannot create directory"
# 清理数据卷后重启
docker compose -f docker-compose.monitoring.yml down alertmanager
docker volume rm sekb_alertmanager_data 2>/dev/null
docker compose -f docker-compose.monitoring.yml up -d alertmanager
```

---

### 故障 2：feishu-webhook 容器启动失败或健康检查不通过

**现象**：sekb-feishu-webhook 状态为 `unhealthy` 或 `Exited`。

#### 原因 2.1：镜像内无 wget 导致健康检查失败

`python:3.11-slim` 镜像不包含 wget，使用 `wget` 作为健康检查命令会失败。

**诊断**：

```bash
docker inspect --format='{{range .State.Health.Log}}{{.Output}}{{end}}' sekb-feishu-webhook | tail -3
# 若显示 "wget: executable file not found in $PATH" 即为此问题
```

**修复**：[docker-compose.monitoring.yml](../../docker-compose.monitoring.yml) 中健康检查改用 python：

```yaml
healthcheck:
  test: ["CMD-SHELL", "python3 -c \"import urllib.request; urllib.request.urlopen('http://localhost:5001/health')\""]
  interval: 30s
  timeout: 5s
  retries: 3
```

```bash
# 应用配置后重启
docker compose -f docker-compose.monitoring.yml up -d feishu-webhook
```

#### 原因 2.2：镜像构建失败（pip 安装超时）

```bash
# 查看构建日志
docker compose -f docker-compose.monitoring.yml build feishu-webhook

# 常见错误：pip install 超时
# 修复：使用国内 pip 镜像源
# 编辑 deploy/feishu-webhook/Dockerfile，在 pip install 前添加：
#   RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

#### 原因 2.3：端口 5001 被占用

```bash
lsof -i :5001
# 修改端口：FEISHU_GATEWAY_PORT=5002 docker compose -f docker-compose.monitoring.yml up -d feishu-webhook
```

#### 原因 2.4：FastAPI 应用启动异常

```bash
docker logs sekb-feishu-webhook --tail 30
# 常见错误：
#   - "Address already in use"：端口冲突
#   - "ModuleNotFoundError"：镜像构建不完整，重新构建
#   - "ValidationError"：feishu_gateway.py 代码错误
```

---

### 故障 3：Alertmanager 无法连接 feishu-webhook

**现象**：告警触发但飞书未收到，Alertmanager 日志中出现连接错误。

```bash
docker logs sekb-alertmanager --tail 50 | grep -i "error\|failed\|webhook"
```

#### 原因 3.1：服务名解析失败（不在同一网络）

alertmanager.yml 中 webhook URL 使用 `http://feishu-webhook:5001/webhook`，依赖 docker 网络服务名解析。

```bash
# 验证两个容器是否在同一网络
docker inspect sekb-alertmanager --format='{{json .NetworkSettings.Networks}}' | python3 -m json.tool
docker inspect sekb-feishu-webhook --format='{{json .NetworkSettings.Networks}}' | python3 -m json.tool

# 两者都应包含 "sekb_network"

# 从 alertmanager 容器内测试连接
docker exec sekb-alertmanager wget -qO- http://feishu-webhook:5001/health
```

**修复**：若网络不一致，将两者加入同一网络：

```bash
docker network connect sekb_network sekb-alertmanager 2>/dev/null
docker network connect sekb_network sekb-feishu-webhook 2>/dev/null
```

#### 原因 3.2：feishu-webhook 未启动或已退出

```bash
docker ps -a --filter "name=sekb-feishu-webhook"
# 若状态非 Up，先启动 feishu-webhook
docker compose -f docker-compose.monitoring.yml up -d feishu-webhook
```

---

### 故障 4：告警触发但飞书未收到通知

**现象**：Prometheus/Alertmanager 中显示告警 firing，但飞书群无消息。

#### 原因 4.1：FEISHU_WEBHOOK_URL 未配置

```bash
# 检查 feishu-webhook 是否配置了飞书地址
curl -sf http://localhost:5001/health
# 若返回 "feishu_configured": false，说明未配置

# 检查环境变量
docker exec sekb-feishu-webhook env | grep FEISHU
```

**修复**：在 `.env.prod` 中配置飞书 webhook：

```bash
echo 'FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/your-hook-id' >> .env.prod
docker compose -f docker-compose.monitoring.yml --env-file .env.prod up -d feishu-webhook
```

#### 原因 4.2：飞书 webhook URL 无效或被禁用

```bash
# 直接测试飞书 webhook 是否可用
curl -X POST "https://open.feishu.cn/open-apis/bot/v2/hook/your-hook-id" \
  -H "Content-Type: application/json" \
  -d '{"msg_type":"text","content":{"text":"测试消息"}}'

# 若返回 "iinvalid webhook url"：URL 已失效，需重新获取
#   飞书群 → 群设置 → 群机器人 → 添加自定义机器人 → 复制 webhook
```

#### 原因 4.3：飞书签名校验失败

若飞书机器人启用了安全设置（签名校验），但 `FEISHU_SECRET` 未配置或不匹配。

```bash
# 检查签名密钥是否配置
docker exec sekb-feishu-webhook env | grep FEISHU_SECRET

# 查看飞书推送结果
docker logs sekb-feishu-webhook --tail 20 | grep -i "feishu\|sign\|error"
```

**修复**：在 `.env.prod` 中配置正确的签名密钥：

```bash
FEISHU_SECRET=your-signing-secret-from-feishu
```

#### 原因 4.4：Alertmanager 告警被抑制或未到发送时机

Alertmanager 有分组聚合机制，告警不会立即发送：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| group_wait | 30s (critical) / 1m (warning) | 首次告警等待聚合时间 |
| group_interval | 5m | 同组告警发送间隔 |
| repeat_interval | 1h (critical) / 4h (warning) | 重复告警发送间隔 |

```bash
# 查看 Alertmanager 内部状态
curl -sf http://100.71.24.105:9093/api/v2/alerts | python3 -m json.tool
# 关注 "status.state" 字段：active / suppressed
```

#### 原因 4.5：feishu_gateway.py 推送异常被吞掉

```bash
# 手动推送测试告警，查看返回
curl -X POST http://localhost:5001/webhook \
  -H "Content-Type: application/json" \
  -d '{"alerts":[{"status":"firing","labels":{"alertname":"TestAlert","severity":"critical"},"annotations":{"summary":"测试","description":"详情"},"startsAt":"2026-08-19T00:00:00Z"}]}'

# 预期返回：
# {"status":"ok","alerts_count":1,"feishu":{"status":"sent","status_code":200,...}}

# 若 feishu.status 为 "failed" 或 "skipped"，根据 response 字段排查
```

---

### 故障 5：Prometheus 告警规则未生效

**现象**：Alertmanager 无告警接收，但实际指标已超阈值。

#### 原因 5.1：告警规则文件未加载

```bash
# 检查 Prometheus 是否加载了告警规则
curl -sf http://100.71.24.105:9091/api/v1/rules | python3 -m json.tool | head -30

# 若返回空 rules 列表，检查 prometheus.yml 配置
docker exec sekb-prometheus cat /etc/prometheus/prometheus.yml | grep rule_files

# 应包含：
# rule_files:
#   - /etc/prometheus/alerts.yml
```

#### 原因 5.2：告警规则语法错误

```bash
# 查看 Prometheus 日志中的规则加载错误
docker logs sekb-prometheus 2>&1 | grep -i "error\|rule\|fail"

# 常见错误：
#   - "could not parse expression"：PromQL 语法错误
#   - "unknown metric name"：指标名拼写错误
```

#### 原因 5.3：指标未采集到

```bash
# 检查 Prometheus 抓取目标状态
curl -sf http://100.71.24.105:9091/api/v1/targets | python3 -c "
import json, sys
d = json.load(sys.stdin)
for t in d.get('data',{}).get('activeTargets',[]):
    print(f\"  {t['labels'].get('job','?'):20s} {t['health']:8s} {t.get('lastError','')}\")
"

# 若 backend 目标为 down，检查后端 /metrics 端点
curl http://localhost:8000/metrics
```

#### 原因 5.4：告警条件未满足（for 持续时间未到）

alerts.yml 中告警通常配置 `for: 2m` 或 `for: 5m`，指标需持续超阈值一段时间才会触发。

```bash
# 查看告警的 pending/firing 状态
curl -sf http://100.71.24.105:9091/api/v1/alerts | python3 -c "
import json, sys
d = json.load(sys.stdin)
for a in d.get('data',{}).get('alerts',[]):
    print(f\"  [{a['state']}] {a['labels'].get('alertname')} activeAt={a.get('activeAt','')}\")
"
# state: pending（等待中）/ firing（已触发）
```

---

### 故障 6：邮件通知未收到

#### 原因 6.1：SMTP 配置错误

```bash
# 检查 alertmanager.yml 中的 SMTP 配置
docker exec sekb-alertmanager cat /etc/alertmanager/alertmanager.yml | grep -A5 smtp

# 当前默认配置为 localhost:25，生产环境需修改为真实 SMTP 服务器
# 编辑 deploy/alertmanager.yml：
#   smtp_smarthost: "smtp.gmail.com:587"
#   smtp_auth_username: "your-email@gmail.com"
#   smtp_auth_password: "your-app-password"
```

#### 原因 6.2：邮件被拦截或进入垃圾箱

检查邮件服务器日志和收件箱垃圾邮件文件夹。

---

### 故障 7：应用侧告警（如「科技资讯日报生成失败」）没收到卡片

> 这类告警**不经过 Prometheus/Alertmanager**：backend 直接 POST 到网关。
> 所以 `故障 4` 的排查（Alertmanager 抑制、Prometheus 规则）在这里**都不适用**，
> 先按下面顺序查。三个坑都是真实踩过的，且都不报错、只表现为「一条卡片都没有」。

#### 原因 7.1：`send_alert` 根本没被调用（静默死代码）

**症状**：任务确实失败了（日志有 `资讯定时任务失败`），但群里没有卡片。

```bash
# 确认失败路径真的调用了告警（应当能在 write_status 里看到 send_alert）
grep -n "send_alert" backend/app/agents/news/service.py

# 直接手动跑一发：为 True 说明函数本身没问题，问题在调用点
docker exec sekb-backend python -c "import asyncio; from app.core.alerts import send_alert; print(asyncio.run(send_alert('自测', 'ok', source='news')))"
```

历史上出现过「提交信息写着接了告警、实际只加了函数没加调用」——所以
`backend/tests/unit/test_alerts.py::test_write_status_failure_fires_alert` 专门盯着这条。

#### 原因 7.2：容器里 `FEISHU_WEBHOOK_URL` 是空的（网关收到了但发不出去）

`deploy.sh` 启动监控栈时必须带 `--env-file .env.prod`（已修）；漏传时容器内该变量为空。

```bash
# feishu_configured 必须是 true
curl -sf http://localhost:5001/health
# 再看一眼容器内实际值（长度应为几十个字符，不是 0）
docker exec sekb-feishu-webhook python3 -c "import os;u=os.getenv('FEISHU_WEBHOOK_URL','');print('已配置:',bool(u),'长度',len(u),'SECRET:',bool(os.getenv('FEISHU_SECRET')))"
```

#### 原因 7.3：改了网关代码但镜像没重建（改完部署完行为没变）

`feishu-webhook` 是**本地构建**的镜像（`image: sekb-feishu-webhook:latest`）。
`docker compose up -d` 看到同名 tag 存在就**不会重建** →
卡片字段/按钮/释义的改动不会生效。`deploy.sh` 已改为 `up -d --build`（已修）。

```bash
# 改完网关代码后：确认容器是刚构建的（Created 时间应是刚刚）
docker inspect sekb-feishu-webhook --format '{{.Created}} {{.Config.Image}}'
```

#### 原因 7.4：被主动关掉了 / 去重窗口

- 关闭开关：`NEWS_ALERT_WEBHOOK_URL=off`（或 `none`/`0`/`disabled`）→ 有意不发。
  查当前生效地址：`docker exec sekb-backend python -c "from app.core.alerts import alert_webhook_url; print(alert_webhook_url())"`。
- 同一条故障（同来源+同标题+同原因）5 分钟内只推一次；调度器重试、用户连点
  都落在同一个窗口里，**只收到一张卡片是预期行为**。

#### 一键自测（推荐先跑这个）

```bash
# 走完整链路：backend 代码 → 网关 → 飞书群
docker exec sekb-backend python -c "import asyncio; from app.core.alerts import send_alert; print(asyncio.run(send_alert('告警链路测试', '忽略即可', source='news')))"
# 只看网关 → 飞书这一跳（不依赖 backend）
curl -X POST "http://localhost:5001/test?source=news"
```

两条都应返回 `True` / `{"status":"ok",...}` **并且飞书群里出现卡片**；
只返回成功但没卡片 → 看 `docker logs sekb-feishu-webhook` 里飞书的 `response`。

---

## 四、健康检查与自愈

### 4.1 各服务健康检查端点

| 服务 | 健康检查命令 | 说明 |
|------|-------------|------|
| prometheus | `curl http://100.71.24.105:9091/-/healthy` | 返回 200 即健康 |
| alertmanager | `curl http://100.71.24.105:9093/-/healthy` | 返回 200 即健康 |
| feishu-webhook | `curl http://localhost:5001/health` | 返回 JSON 含 feishu_configured 字段 |

### 4.2 自动重启策略

所有告警相关服务均配置了 `restart: unless-stopped`，容器异常退出会自动重启。但若健康检查持续失败，Docker 不会自动重启（需手动干预）。

```bash
# 监控并自动重启不健康的服务（可选 cron）
for svc in sekb-alertmanager sekb-feishu-webhook; do
  status=$(docker inspect --format='{{.State.Health.Status}}' $svc 2>/dev/null)
  if [ "$status" = "unhealthy" ]; then
    docker restart $svc
    echo "[$(date)] $svc unhealthy, restarted" >> /var/log/sekb-alert-watchdog.log
  fi
done
```

---

## 五、日志收集

### 5.1 查看各服务日志

```bash
# Alertmanager 日志（关注告警发送与路由）
docker logs sekb-alertmanager -f --tail 50

# feishu-webhook 日志（关注飞书推送结果）
docker logs sekb-feishu-webhook -f --tail 50

# Prometheus 日志（关注告警规则评估）
docker logs sekb-prometheus -f --tail 50 | grep -i "alert\|rule"
```

### 5.2 关键日志关键词

| 服务 | 关键词 | 含义 |
|------|--------|------|
| alertmanager | `NotifyError` | 通知发送失败 |
| alertmanager | `webhook` + `error` | webhook 调用失败 |
| alertmanager | `resolved` | 告警已恢复 |
| feishu-webhook | `status_code` + 非 200 | 飞书 API 返回错误 |
| feishu-webhook | `FEISHU_WEBHOOK_URL not configured` | 未配置飞书地址 |
| prometheus | `rule_files` + `error` | 告警规则加载失败 |

---

## 六、完整链路测试

### 6.1 端到端测试脚本

```bash
#!/bin/bash
# 告警链路完整性测试
echo "=== 1. 检查服务状态 ==="
for svc in sekb-prometheus sekb-alertmanager sekb-feishu-webhook; do
  status=$(docker inspect --format='{{.State.Status}}' $svc 2>/dev/null)
  echo "  $svc: $status"
done

echo ""
echo "=== 2. 测试 Prometheus → Alertmanager 链路 ==="
# 通过 Prometheus API 手动触发告警评估
curl -sf http://100.71.24.105:9091/api/v1/alerts | python3 -c "
import json, sys
d = json.load(sys.stdin)
alerts = d.get('data',{}).get('alerts',[])
print(f'  当前告警数: {len(alerts)}')
for a in alerts:
    print(f'  [{a[\"state\"]}] {a[\"labels\"].get(\"alertname\")}')
"

echo ""
echo "=== 3. 测试 Alertmanager → feishu-webhook 链路 ==="
# 模拟 Alertmanager 推送告警
result=$(curl -s -X POST http://localhost:5001/webhook \
  -H "Content-Type: application/json" \
  -d '{"alerts":[{"status":"firing","labels":{"alertname":"TestAlert","severity":"critical"},"annotations":{"summary":"链路测试","description":"端到端验证"},"startsAt":"2026-08-19T00:00:00Z"}]}')
echo "  feishu-webhook 响应: $result"

echo ""
echo "=== 4. 检查飞书是否收到（需人工确认）==="
echo "  请检查飞书群是否收到 'SEKB 告警通知（1 条）' 卡片消息"
```

### 6.2 静默告警测试

```bash
# 创建静默规则（模拟维护期）
SILENCE_ID=$(curl -s -X POST http://100.71.24.105:9093/api/v2/silences \
  -H "Content-Type: application/json" \
  -d '{"matchers":[{"name":"alertname","value":"TestAlert","isRegex":false}],"startsAt":"2026-08-19T00:00:00Z","endsAt":"2026-08-19T23:59:59Z","createdBy":"ops","comment":"测试静默"}' \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['silenceID'])")
echo "静默规则已创建: $SILENCE_ID"

# 查看静默规则
curl -sf http://100.71.24.105:9093/api/v2/silences | python3 -m json.tool

# 删除静默规则
curl -X DELETE http://100.71.24.105:9093/api/v2/silence/$SILENCE_ID
```

---

## 七、故障排查速查表

| 现象 | 首先检查 | 可能原因 |
|------|---------|---------|
| alertmanager 容器 Exited | `docker logs sekb-alertmanager` | 配置语法错误 / 端口占用 / 卷权限 |
| alertmanager 反复重启 | feishu-webhook 是否健康 | 依赖未就绪 |
| feishu-webhook unhealthy | 健康检查命令是否可用 | 镜像无 wget，改用 python |
| 告警触发但飞书无消息 | `curl localhost:5001/health` 的 `feishu_configured` | FEISHU_WEBHOOK_URL 未配置 |
| 任务失败但飞书无消息（应用侧） | `grep send_alert backend/app/agents/news/service.py` | 调用点缺失 / 被 `off` 关掉 / 5 分钟去重 |
| 改了网关代码部署后行为没变 | `docker inspect sekb-feishu-webhook --format '{{.Created}}'` | compose 少了 `--build`，镜像没重建 |
| 飞书返回签名错误 | `FEISHU_SECRET` 是否正确 | 签名密钥不匹配 |
| Prometheus 无告警 | `curl 100.71.24.105:9091/api/v1/rules` | 规则未加载 / 指标未采集 |
| 告警一直 pending | alerts.yml 的 `for` 持续时间 | 时间未到，属正常 |
| 邮件未收到 | alertmanager.yml 的 SMTP 配置 | SMTP 未配置或配置错误 |

---

## 八、附录：告警规则清单

当前配置的告警规则（[deploy/alerts.yml](../../deploy/alerts.yml)）：

| 告警名称 | 级别 | 触发条件 | 持续时间 |
|---------|------|---------|---------|
| BackendDown | critical | 后端不可达 | 1m |
| BackendDegraded | warning | 健康检查降级 | 2m |
| HighLatencyP95 | warning | P95 延迟 > 15s | 3m |
| BudgetExceeded | warning | 日成本 > $5 | 5m |
| LowGroundedness | warning | 答案锚定度 < 0.6 | 15m |
| ToolFailureHigh | critical | 工具失败率 > 10% | 2m |
| HighErrorRate | critical | 请求错误率 > 5% | 2m |
