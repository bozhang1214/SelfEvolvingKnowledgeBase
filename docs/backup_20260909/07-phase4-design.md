# Phase 4 详细设计：生产部署 + 监控 + CI/CD

> 文档版本：v1.0.0
> 最后更新：2026-08-12
> 状态：设计评审中
> 依赖文档：[01-architecture.md](./01-architecture.md)、[02-phase1-design.md](./02-phase1-design.md)、[05-phase2-design.md](./05-phase2-design.md)、[06-phase3-design.md](./06-phase3-design.md)

---

## 一、Phase 4 目标与范围

### 1.1 核心目标

Phase 4 将项目从"可用的应用"升级为**可稳定运行的线上服务**，建立完整的部署、监控、CI/CD 和应急响应体系。

### 1.2 必做（P0）

| 编号 | 范围 | 验收标准 |
|---|---|---|
| P0-1 | Docker 化 | 后端、前端、Redis、PostgreSQL 容器化 |
| P0-2 | docker-compose | 一键启动全栈开发/测试/生产环境 |
| P0-3 | CI/CD Pipeline | GitHub Actions 自动测试 → 构建 → 部署 |
| P0-4 | 监控系统 | Prometheus + Grafana 指标看板 |
| P0-5 | 日志系统 | ELK / Loki 日志聚合与检索 |
| P0-6 | 告警系统 | 异常告警通知（飞书/邮件） |
| P0-7 | 灰度发布 | 金丝雀发布 + 自动回滚 |
| P0-8 | 备份策略 | 数据库定时备份 + 恢复演练 |
| P0-9 | 安全加固 | HTTPS、WAF、限流、审计日志 |

### 1.3 不做

- Kubernetes（Phase 4 先用 docker-compose，后续可迁移 K8s）
- 移动端 App
- 多区域部署

---

## 二、部署架构

### 2.1 单节点部署（Phase 4 初始）

```
┌──────────────────────────────────────────────────────────────────────┐
│                        服务器（单节点）                              │
│                                                                      │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────┐    │
│  │  Nginx     │  │  FastAPI   │  │  React     │  │  Worker    │    │
│  │  (反向代理) │  │  (后端 API) │  │  (前端 SPA)│  │  (后台任务)│    │
│  └──────┬─────┘  └──────┬─────┘  └────────────┘  └──────┬─────┘    │
│         │               │                                 │          │
│         └───────┬───────┴─────────────────────────────────┘          │
│                 ▼                                                    │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────┐    │
│  │ PostgreSQL │  │   Redis    │  │  ChromaDB  │  │ 博查 MCP   │    │
│  │  (主数据)  │  │  (L2 记忆) │  │  (向量库)  │  │  (子进程)  │    │
│  └────────────┘  └────────────┘  └────────────┘  └────────────┘    │
│                                                                      │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐                     │
│  │ Prometheus │  │   Grafana  │  │   Loki     │                     │
│  │  (指标)    │  │  (看板)    │  │  (日志)    │                     │
│  └────────────┘  └────────────┘  └────────────┘                     │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
         │
         │ HTTPS
         ▼
┌──────────────────────────────────────────────────────────────────────┐
│                       用户端                                        │
│   浏览器 ←→ Nginx ←→ FastAPI ←→ Agent 工作流                        │
└──────────────────────────────────────────────────────────────────────┘
```

### 2.2 容器编排

```yaml
# docker-compose.prod.yml
version: "3.9"

services:
  # ============ 后端 ============
  backend:
    build:
      context: ./backend
      dockerfile: Dockerfile
    image: self-evolving-kb-backend:${TAG:-latest}
    container_name: sekb-backend
    restart: unless-stopped
    ports:
      - "8000:8000"
    environment:
      - DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY}
      - BOCHA_API_KEY=${BOCHA_API_KEY}
      - LANGSMITH_API_KEY=${LANGSMITH_API_KEY:-}
      - DATABASE_URL=postgresql://sekb:${DB_PASSWORD}@postgres:5432/sekb
      - REDIS_URL=redis://redis:6379/0
      - JWT_SECRET=${JWT_SECRET}
      - ENVIRONMENT=production
    volumes:
      - chroma_data:/app/data/chroma_db
      - backend_logs:/app/data/logs
    depends_on:
      - postgres
      - redis
      - bocha-mcp
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
    deploy:
      resources:
        limits:
          memory: 2G
          cpus: "2.0"
        reservations:
          memory: 512M
          cpus: "0.5"

  # ============ 博查搜索 MCP ============
  bocha-mcp:
    build:
      context: ./backend
      dockerfile: Dockerfile.bocha-mcp
    container_name: sekb-bocha-mcp
    restart: unless-stopped
    environment:
      - BOCHA_API_KEY=${BOCHA_API_KEY}

  # ============ 前端 ============
  frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile
    image: self-evolving-kb-frontend:${TAG:-latest}
    container_name: sekb-frontend
    restart: unless-stopped
    ports:
      - "80:80"
    depends_on:
      - backend

  # ============ 基础设施 ============
  postgres:
    image: postgres:16-alpine
    container_name: sekb-postgres
    restart: unless-stopped
    environment:
      - POSTGRES_USER=sekb
      - POSTGRES_PASSWORD=${DB_PASSWORD}
      - POSTGRES_DB=sekb
    volumes:
      - pg_data:/var/lib/postgresql/data
      - ./deploy/init-db.sql:/docker-entrypoint-initdb.d/init.sql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U sekb"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    container_name: sekb-redis
    restart: unless-stopped
    command: redis-server --maxmemory 256mb --maxmemory-policy allkeys-lru
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  # ============ 监控 ============
  prometheus:
    image: prom/prometheus:latest
    container_name: sekb-prometheus
    restart: unless-stopped
    ports:
      - "9090:9090"
    volumes:
      - ./deploy/prometheus.yml:/etc/prometheus/prometheus.yml
      - prometheus_data:/prometheus

  grafana:
    image: grafana/grafana:latest
    container_name: sekb-grafana
    restart: unless-stopped
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_PASSWORD}
    volumes:
      - grafana_data:/var/lib/grafana

  loki:
    image: grafana/loki:latest
    container_name: sekb-loki
    restart: unless-stopped
    ports:
      - "3100:3100"
    volumes:
      - loki_data:/loki

  # ============ 反向代理 ============
  nginx:
    image: nginx:alpine
    container_name: sekb-nginx
    restart: unless-stopped
    ports:
      - "443:443"
      - "80:80"
    volumes:
      - ./deploy/nginx.conf:/etc/nginx/nginx.conf
      - ./deploy/certs:/etc/nginx/certs
    depends_on:
      - backend
      - frontend

volumes:
  pg_data:
  redis_data:
  chroma_data:
  prometheus_data:
  grafana_data:
  loki_data:
  backend_logs:
```

---

## 三、Dockerfile 设计

### 3.1 后端 Dockerfile

```dockerfile
# backend/Dockerfile
FROM python:3.11-slim as builder

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY pyproject.toml .
RUN pip install --no-cache-dir uv && \
    uv pip install --system -r <(uv pip compile pyproject.toml)

# 复制代码
COPY app/ ./app/
COPY config.yaml .
COPY .env.example .

# 生产镜像
FROM python:3.11-slim

WORKDIR /app

# 从 builder 复制已安装的依赖
COPY --from=builder /usr/local/lib/python3.11/site-packages/ /usr/local/lib/python3.11/site-packages/
COPY --from=builder /usr/local/bin/ /usr/local/bin/

# 复制应用代码
COPY --from=builder /app/ ./

# 创建非 root 用户
RUN useradd -m -u 1000 appuser && \
    mkdir -p data/{conversations,traces,eval_logs,logs,chroma_db} && \
    chown -R appuser:appuser /app

USER appuser

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 8000

# 启动命令
CMD ["uvicorn", "app.api.server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

### 3.2 前端 Dockerfile（多阶段）

```dockerfile
# frontend/Dockerfile
FROM node:20-alpine as builder

WORKDIR /app

COPY package.json ./
RUN npm install

COPY . .
RUN npm run build

# 生产镜像
FROM nginx:alpine

# 复制构建产物
COPY --from=builder /app/dist /usr/share/nginx/html

# 复制 Nginx 配置
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
```

### 3.3 后端健康检查端点

```python
# api/routes/health.py
@router.get("/health")
async def health_check():
    """健康检查端点"""
    checks = {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "version": config.app.version,
        "services": {}
    }

    # 检查 PostgreSQL 连接
    try:
        await db.execute("SELECT 1")
        checks["services"]["postgres"] = "ok"
    except Exception as e:
        checks["services"]["postgres"] = f"error: {e}"
        checks["status"] = "degraded"

    # 检查 Redis 连接
    try:
        await redis.ping()
        checks["services"]["redis"] = "ok"
    except Exception as e:
        checks["services"]["redis"] = f"error: {e}"
        checks["status"] = "degraded"

    # 检查 LLM API
    try:
        await llm_factory.health_check()
        checks["services"]["llm"] = "ok"
    except Exception as e:
        checks["services"]["llm"] = f"error: {e}"
        checks["status"] = "degraded"

    if checks["status"] == "degraded":
        raise HTTPException(503, checks)

    return checks
```

---

## 四、Nginx 配置

```nginx
# deploy/nginx.conf
server {
    listen 80;
    server_name your-domain.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name your-domain.com;

    # SSL 配置
    ssl_certificate /etc/nginx/certs/fullchain.pem;
    ssl_certificate_key /etc/nginx/certs/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    # 安全头
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    # 前端静态文件
    location / {
        root /usr/share/nginx/html;
        try_files $uri $uri/ /index.html;

        # 缓存策略
        location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2)$ {
            expires 1y;
            add_header Cache-Control "public, immutable";
        }
    }

    # 后端 API 代理
    location /api/ {
        proxy_pass http://backend:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE 流式支持
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;

        # 限流
        limit_req zone=api zone burst=20 nodelay;
    }

    # 健康检查
    location /health {
        proxy_pass http://backend:8000/health;
    }

    # WebSocket（预留）
    location /ws/ {
        proxy_pass http://backend:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}

# 限流 zone 定义
limit_req_zone $binary_remote_addr zone=api:10m rate=10r/s;
```

---

## 五、监控系统

### 5.1 Prometheus 配置

```yaml
# deploy/prometheus.yml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: "backend"
    static_configs:
      - targets: ["backend:8000"]
    metrics_path: "/metrics"

  - job_name: "node"
    static_configs:
      - targets: ["node-exporter:9100"]

  - job_name: "nginx"
    static_configs:
      - targets: ["nginx-exporter:9113"]
```

### 5.2 后端指标暴露

```python
# core/metrics.py
from prometheus_client import Counter, Histogram, Gauge, generate_latest

# 会话指标
conversations_total = Counter(
    "sekb_conversations_total",
    "Total conversations",
    ["user_id"]
)

messages_total = Counter(
    "sekb_messages_total",
    "Total messages",
    ["user_id", "intent"]
)

# 性能指标
e2e_latency_seconds = Histogram(
    "sekb_e2e_latency_seconds",
    "End-to-end latency",
    buckets=[0.5, 1, 2, 5, 10, 15, 30, 60]
)

llm_call_latency_seconds = Histogram(
    "sekb_llm_call_latency_seconds",
    "LLM call latency",
    ["model", "agent"],
    buckets=[0.1, 0.5, 1, 2, 5, 10, 30, 60]
)

# 成本指标
daily_cost_usd = Gauge(
    "sekb_daily_cost_usd",
    "Daily cost in USD"
)

# 质量指标
reflection_pass_rate = Gauge(
    "sekb_reflection_pass_rate",
    "Critic pass rate"
)

replan_count_total = Counter(
    "sekb_replan_count_total",
    "Total replan count"
)

# 工具指标
tool_call_total = Counter(
    "sekb_tool_call_total",
    "Total tool calls",
    ["tool", "status"]
)

tool_call_latency_seconds = Histogram(
    "sekb_tool_call_latency_seconds",
    "Tool call latency",
    ["tool"],
    buckets=[0.01, 0.1, 0.5, 1, 5, 10, 30]
)


def get_metrics():
    return generate_latest()
```

### 5.3 Grafana 看板

**核心看板（预置）**：

1. **服务概览看板**
   - 请求 QPS
   - 平均/95分位延迟
   - 错误率
   - 活跃用户数
   - 日成本

2. **Agent 链路看板**
   - 各 Agent 节点耗时
   - 意图分布
   - 反思通过率
   - 重规划次数分布

3. **LLM 成本看板**
   - 按模型的 token 消耗
   - 按 Agent 的 token 消耗
   - 日/周/月成本趋势
   - reasoner 占比

4. **工具稳定性看板**
   - 各工具调用成功率
   - 各工具调用延迟
   - MCP Server 健康状态

5. **系统资源看板**
   - CPU/内存使用率
   - 磁盘使用率
   - PostgreSQL 连接数
   - Redis 内存使用

### 5.4 告警规则

```yaml
# deploy/alerts.yml
groups:
  - name: service_alerts
    rules:
      - alert: HighErrorRate
        expr: rate(sekb_messages_total{status="error"}[5m]) / rate(sekb_messages_total[5m]) > 0.05
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "错误率超过 5%"
          description: "当前错误率 {{ $value | humanizePercentage }}"

      - alert: HighLatency
        expr: histogram_quantile(0.95, rate(sekb_e2e_latency_seconds_bucket[5m])) > 15
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "P95 延迟超过 15 秒"

      - alert: BudgetExceeded
        expr: sekb_daily_cost_usd > 5
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "日预算超过 $5"

      - alert: ReflectionFailureHigh
        expr: rate(sekb_replan_count_total[5m]) > 0.5
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "反思重试率过高"

      - alert: ToolFailure
        expr: rate(sekb_tool_call_total{status="failed"}[5m]) / rate(sekb_tool_call_total[5m]) > 0.1
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "工具调用失败率超过 10%"

      - alert: Down
        expr: up == 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "服务不可达"
```

---

## 六、CI/CD Pipeline

### 6.1 GitHub Actions

```yaml
# .github/workflows/ci.yml
name: CI/CD

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main, develop]
  schedule:
    - cron: "0 2 * * *"  # 每日凌晨 2 点定时跑

env:
  REGISTRY: ghcr.io
  IMAGE_NAME: ${{ github.repository }}

jobs:
  # ============ 代码质量 ============
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install ruff mypy
      - run: ruff check backend/app/
      - run: mypy backend/app/

  # ============ 单元测试 ============
  unit-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -e "backend/[dev]"
      - run: cd backend && pytest tests/unit -x --cov=app --cov-report=xml
      - uses: codecov/codecov-action@v3
        if: github.event_name == 'push' && github.ref == 'refs/heads/main'

  # ============ 集成测试 ============
  integration-test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_USER: test
          POSTGRES_PASSWORD: test
          POSTGRES_DB: test
        ports: ["5432:5432"]
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 3
      steps:
        - uses: actions/checkout@v4
        - uses: actions/setup-python@v5
          with:
            python-version: "3.11"
        - run: pip install -e "backend/[dev]"
        - run: cd backend && pytest tests/integration -x

  # ============ Eval 回归测试 ============
  eval-test:
    runs-on: ubuntu-latest
    needs: [unit-test, integration-test]
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -e "backend/[dev]"
      - name: Run eval
        run: cd backend && python -m app.cli.main eval --dataset tests/fixtures/golden_qa.json
        env:
          DEEPSEEK_API_KEY: ${{ secrets.DEEPSEEK_API_KEY }}
          BOCHA_API_KEY: ${{ secrets.BOCHA_API_KEY }}

  # ============ 构建 Docker 镜像 ============
  build:
    runs-on: ubuntu-latest
    needs: [lint, unit-test]
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - name: Build and push backend
        uses: docker/build-push-action@v5
        with:
          context: ./backend
          push: true
          tags: |
            ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}/backend:${{ github.sha }}
            ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}/backend:latest
          cache-from: type=gha
          cache-to: type=gha,mode=max
      - name: Build and push frontend
        uses: docker/build-push-action@v5
        with:
          context: ./frontend
          push: true
          tags: |
            ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}/frontend:${{ github.sha }}
            ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}/frontend:latest
          cache-from: type=gha
          cache-to: type=gha,mode=max

  # ============ 部署 ============
  deploy:
    runs-on: ubuntu-latest
    needs: [build, eval-test]
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    environment:
      name: production
      url: https://your-domain.com
    steps:
      - uses: actions/checkout@v4
      - name: Deploy via SSH
        uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.DEPLOY_HOST }}
          username: ${{ secrets.DEPLOY_USER }}
          key: ${{ secrets.DEPLOY_KEY }}
          script: |
            cd /opt/self-evolving-kb
            docker compose pull
            docker compose up -d
            docker compose prune -f
```

### 6.2 发布流程

```
开发者推送代码到 main
         │
         ▼
    ┌────────────┐
    │  CI 检查    │  lint + 单测 + 集成测
    └──────┬─────┘
           │ 通过
           ▼
    ┌────────────┐
    │ Eval 回归   │  黄金数据集 + 基线对比
    └──────┬─────┘
           │ 通过
           ▼
    ┌────────────┐
    │ 构建镜像    │  推送 Docker Hub / GHCR
    └──────┬─────┘
           │
           ▼
    ┌────────────┐
    │ 部署生产    │  docker compose pull + up
    └──────┬─────┘
           │
           ▼
    ┌────────────┐
    │ 健康检查    │  自动验证 /health
    └──────┬─────┘
           │ 通过
           ▼
       发布完成
```

---

## 七、灰度发布策略

### 7.1 金丝雀发布

```
                    ┌──────────────────┐
                    │   Nginx 路由     │
                    └────────┬─────────┘
                             │
              ┌──────────────┴──────────────┐
              ▼                              ▼
    ┌──────────────────┐          ┌──────────────────┐
    │  稳定版本 (95%)  │          │  新版本 (5%)     │
    │  backend:v1.2.0  │          │  backend:v1.3.0  │
    └──────────────────┘          └──────────────────┘
```

**实现方式**：
- Nginx `split_clients` 模块按 cookie 分流
- 5% 流量到新版本
- 观察 30 分钟
- 指标正常 → 逐步切流到 100%
- 指标异常 → 自动回滚

```nginx
# split_clients 配置
split_clients "${cookie_release_version}" $backend_upstream {
    5%      backend_canary;
    *       backend_stable;
}

upstream backend_stable {
    server backend-v1:8000;
}

upstream backend_canary {
    server backend-v2:8000;
}
```

### 7.2 自动回滚

```python
# core/deployment.py
class CanaryMonitor:
    """金丝雀发布监控"""

    async def monitor(self, duration_minutes: int = 30):
        """监控新版本，如果指标异常则自动回滚"""
        start_time = datetime.now()
        check_interval = 30  # 30 秒检查一次

        while (datetime.now() - start_time).total_seconds() < duration_minutes * 60:
            # 检查关键指标
            error_rate = await self._get_error_rate("canary")
            p95_latency = await self._get_p95_latency("canary")

            if error_rate > 0.05 or p95_latency > 20:
                # 触发回滚
                await self._rollback()
                await self._notify("Canary failed, rolled back")
                return

            await asyncio.sleep(check_interval)

        # 指标正常，全量切换
        await self._promote_to_stable()
```

---

## 八、备份与恢复

### 8.1 备份策略

| 资源 | 备份频率 | 保留周期 | 备份方式 |
|---|---|---|---|
| PostgreSQL | 每日 03:00 | 30 天 | `pg_dump` + S3 |
| Redis | 每日 03:00 | 7 天 | RDB 快照 |
| ChromaDB | 每日 03:00 | 30 天 | 文件系统快照 |
| 应用日志 | 实时 | 90 天 | Loki 自动保留 |
| Eval 报告 | 生成时 | 永久 | 文件系统 |

### 8.2 备份脚本

```bash
#!/bin/bash
# deploy/backup.sh

BACKUP_DIR="/backup/$(date +%Y%m%d)"
RETENTION_DAYS=30

# PostgreSQL 备份
docker compose exec postgres \
    pg_dump -U sekb sekb > "$BACKUP_DIR/postgres.sql"

# Redis 备份
docker compose exec redis \
    redis-cli BGSAVE
cp redis_data/dump.rdb "$BACKUP_DIR/redis.rdb"

# ChromaDB 备份
cp -r chroma_data "$BACKUP_DIR/chroma"

# 上传到 S3
aws s3 sync "$BACKUP_DIR" "s3://sekb-backups/$(date +%Y%m%d)"

# 清理过期备份
find /backup -maxdepth 1 -type d -mtime +$RETENTION_DAYS -exec rm -rf {} \;
```

### 8.3 恢复演练

- 每季度进行一次恢复演练
- 验证备份完整性和恢复流程
- 记录恢复时间（RTO）和数据丢失量（RPO）

---

## 九、安全加固

### 9.1 生产安全清单

| 项目 | 状态 | 说明 |
|---|---|---|
| HTTPS | ✅ | Let's Encrypt 自动续签 |
| HSTS | ✅ | max-age=31536000 |
| CSP | ✅ | 严格内容安全策略 |
| 限流 | ✅ | Nginx + API 双重限流 |
| 鉴权 | ✅ | JWT + HttpOnly Cookie |
| 密码 | ✅ | bcrypt + 强度校验 |
| 审计日志 | ✅ | 登录/操作/异常记录 |
| WAF | ⏳ | Cloudflare / 阿里云 WAF |
| DDoS 防护 | ⏳ | 同上 |
| 渗透测试 | ⏳ | 每季度一次 |

### 9.2 审计日志

```python
# core/audit.py
class AuditLogger:
    """审计日志记录器"""

    async def log_auth(
        self,
        user_id: str,
        action: str,  # login | logout | register | password_change
        success: bool,
        ip_address: str,
        user_agent: str
    ):
        """记录认证相关审计事件"""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "category": "auth",
            "user_id": user_id,
            "action": action,
            "success": success,
            "ip_address": ip_address,
            "user_agent": user_agent
        }
        await self._write(entry)

    async def log_data_access(
        self,
        user_id: str,
        resource: str,
        action: str,  # read | create | update | delete
        resource_id: str
    ):
        """记录数据访问审计事件"""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "category": "data_access",
            "user_id": user_id,
            "resource": resource,
            "resource_id": resource_id,
            "action": action
        }
        await self._write(entry)
```

---

## 十、应急响应

### 10.1 故障等级

| 等级 | 描述 | 响应时间 | 处理方式 |
|---|---|---|---|
| P0 | 服务完全不可用 | 立即 | 自动回滚 → 人工介入 |
| P1 | 核心功能异常（Agent 不工作） | 5 分钟 | 降级备用方案 → 排查 |
| P2 | 部分功能异常（文件上传） | 30 分钟 | 记录 → 排期修复 |
| P3 | 非核心问题（UI 小瑕疵） | 4 小时 | 记录 → 排期修复 |

### 10.2 应急预案

```markdown
# 应急预案

## 1. LLM API 不可用
- 现象：大量 5xx 错误，`llm_call_latency` 超 60s
- 排查：检查 DeepSeek API 状态 → 检查网络
- 处理：
  1. 确认是 API 端问题还是我方网络问题
  2. 若为 API 端问题，启用降级：返回"服务暂时不可用"
  3. 监控 API 恢复情况
  4. 恢复后清理降级状态

## 2. 数据库不可用
- 现象：`/health` 返回 503，PostgreSQL 连接超时
- 排查：检查 PostgreSQL 容器状态 → 磁盘空间 → 网络
- 处理：
  1. 重启 PostgreSQL 容器
  2. 若持续失败，从备份恢复
  3. 恢复后验证数据完整性

## 3. 磁盘空间不足
- 现象：写入失败，日志报 "No space left on device"
- 排查：`df -h` → 定位大文件
- 处理：
  1. 清理过期日志
  2. 清理临时文件
  3. 扩容磁盘

## 4. 安全事件
- 现象：异常登录、数据泄露可疑
- 处理：
  1. 立即封禁可疑账号
  2. 强制所有用户重置密码
  3. 检查审计日志
  4. 通知受影响用户
  5. 事后复盘
```

### 10.3 on-call 机制

- 工作日：9:00 ~ 21:00 在线响应
- 非工作时间：P0 故障需在 30 分钟内响应
- 告警通知：飞书机器人 + 短信 + 电话（逐级升级）

---

## 十一、Phase 4 验收标准

### 11.1 部署验收

| 验收项 | 通过标准 |
|---|---|
| 容器化 | docker-compose up 一键启动全栈 |
| 健康检查 | /health 返回 200，所有依赖正常 |
| HTTPS | 强制 HTTPS，HTTP 自动跳转 |
| Nginx 路由 | 前端/API/SSE/WebSocket 正确路由 |

### 11.2 监控验收

| 验收项 | 通过标准 |
|---|---|
| Prometheus | 指标采集正常，无断采 |
| Grafana | 5 个核心看板正常显示 |
| 告警规则 | 模拟故障触发告警 |
| 日志聚合 | Loki 可检索近 7 天日志 |

### 11.3 CI/CD 验收

| 验收项 | 通过标准 |
|---|---|
| Lint | 无错误 |
| 单测 | 通过率 100% |
| 集成测 | 通过率 100% |
| Eval 回归 | 高危回归 0 条 |
| 镜像构建 | 自动推送到仓库 |
| 自动部署 | main 分支变更自动部署 |

### 11.4 安全验收

| 验收项 | 通过标准 |
|---|---|
| HTTPS | SSL Labs A+ |
| OWASP ZAP | 无高危漏洞 |
| 渗透测试 | 无严重发现 |
| 审计日志 | 登录/操作/异常全覆盖 |

### 11.5 恢复验收

| 验收项 | 通过标准 |
|---|---|
| 备份完整性 | 30 天备份可恢复 |
| RTO | < 1 小时 |
| RPO | < 24 小时 |
| 恢复演练 | 每季度通过 |
