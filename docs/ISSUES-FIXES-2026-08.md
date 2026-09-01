# SEKB 问题修复记录 - 2026 年 8 月

> 文档版本：v1.0
> 最后更新：2026-08-20
> 维护者：SEKB Team
> 关联文档：[testCase/INCREMENTAL-TEST-CASES.md](./testCase/INCREMENTAL-TEST-CASES.md)、[DEPLOYMENT.md](./DEPLOYMENT.md)

---

## 目录

1. [概述](#1-概述)
2. [问题修复清单](#2-问题修复清单)
   - 2.1 [ISSUE-001] 后端 CPU 限制超出 2 核服务器物理核数
   - 2.2 [ISSUE-002] 未备案期间域名无法访问
   - 2.3 [ISSUE-003] 未注册用户登录无提示且无注册引导
   - 2.4 [ISSUE-004] 前端关键路径缺少日志和数据统计
   - 2.5 [ISSUE-005] Prometheus 缺少客户端事件指标
   - 2.6 [ISSUE-006] 客户端事件上报缺少安全与性能保护
   - 2.7 [ISSUE-007] 缺少一键重启脚本
   - 2.8 [ISSUE-008] 上传文件功能完全不可用
3. [变更文件清单](#3-变更文件清单)
4. [回归测试矩阵](#4-回归测试矩阵)
5. [部署生效指引](#5-部署生效指引)

---

## 1. 概述

本文档记录 2026 年 8 月在生产环境部署与日常使用过程中发现的问题及其修复方案。
每个问题项包含：

- **现象**：用户感知到的具体表现
- **根因**：技术层面的真实原因
- **修复**：具体改动文件与逻辑
- **影响范围**：哪些功能/模块受影响
- **回归测试**：对应的测试用例编号（详见 [INCREMENTAL-TEST-CASES.md](./testCase/INCREMENTAL-TEST-CASES.md)）

---

## 2. 问题修复清单

### 2.1 [ISSUE-001] 后端 CPU 限制超出 2 核服务器物理核数

| 字段 | 值 |
|------|---|
| 优先级 | P0 |
| 模块 | docker-compose.prod.yml |
| 发现场景 | 服务器（2 核）执行 `docker compose up -d` 时容器无法启动 |
| 关联测试 | TC-001、TC-002 |

**现象**

在 2 核腾讯云轻量服务器上执行 `docker compose -f docker-compose.prod.yml --env-file .env.prod up -d` 时，
backend 容器一直处于 `Created` 状态无法 `Up`，前端访问 502。

**根因**

[docker-compose.prod.yml](../docker-compose.prod.yml) 第 74 行 backend 容器配置：

```yaml
deploy:
  resources:
    limits:
      cpus: "4.0"   # 要求 4 核 CPU，但服务器只有 2 核
```

Docker 资源限制超出宿主机物理核数时拒绝创建容器。

**修复**

采用方案 B（保守限制）：

```yaml
deploy:
  resources:
    limits:
      memory: 4G
      cpus: "1.5"   # 给 frontend + 系统留 0.5 核，避免 SSH 卡顿
    reservations:
      memory: 1G
```

**影响范围**

- backend 容器 CPU 上限从 4 核 → 1.5 核
- 高峰期 backend 不会挤占 frontend 和系统资源
- 后续服务器升级到 4 核以上时，可调回 `"4.0"`

**注意事项**

- `deploy.resources.reservations` 在非 Swarm 模式下不生效，仅为元数据
- 真正生效的是 `limits.cpus`，会被 Docker 转为 CFS quota
- 完全移除 CPU 限制也是可行方案，但本次选择保守限制以保护 SSH 通道

---

### 2.2 [ISSUE-002] 未备案期间域名无法访问

| 字段 | 值 |
|------|---|
| 优先级 | P0 |
| 模块 | .env.prod / nginx.conf / 部署文档 |
| 发现场景 | 域名 `bos-studio.tech` 已解析但浏览器访问不通 |
| 关联测试 | TC-003 |

**现象**

域名 DNS 已解析到服务器 IP，但 `http://bos-studio.tech` 浏览器访问不通，
没有任何响应或重置连接。

**根因**

中国大陆云服务器（腾讯云轻量）对未完成 ICP 备案的域名，**对 80/443 端口流量做拦截**，
无论访问域名还是 IP 均不可用。备案检测主要针对 80/443 标准端口，非常规端口（如 8080）不受影响。

**修复**

临时方案：将前端对外端口从 80 改为 8080。

```bash
# .env.prod
sed -i 's/^FRONTEND_PORT=.*/FRONTEND_PORT=8080/' .env.prod

# 重启 frontend 容器使端口映射生效
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d frontend
```

腾讯云安全组同步放行 TCP 8080 + 8000 端口。

**切回标准端口的步骤（备案通过后）**

```bash
sed -i 's/^FRONTEND_PORT=.*/FRONTEND_PORT=80/' .env.prod
cp deploy/nginx-ssl.conf deploy/nginx.conf
sed -i 's/your-domain.com/bos-studio.tech/g' deploy/nginx.conf
sudo certbot certonly --standalone -d bos-studio.tech
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d frontend
```

**影响范围**

- 临时访问入口：`http://<服务器IP>:8080`
- 备案完成后切回 80/443 + HTTPS
- 文档同步：[DEPLOYMENT.md](./DEPLOYMENT.md) 已补充备案期临时方案

---

### 2.3 [ISSUE-003] 未注册用户登录无提示且无注册引导

| 字段 | 值 |
|------|---|
| 优先级 | P1 |
| 模块 | frontend/src/services/api.ts、frontend/src/pages/Login.tsx |
| 发现场景 | 未注册用户输入邮箱密码点登录，页面无任何反馈 |
| 关联测试 | TC-004、TC-005、TC-006 |

**现象**

未注册用户在登录页输入邮箱 + 密码点击"登录"按钮后：
- 页面无任何错误提示
- 没有引导到注册页面
- 进度条 / Loading 一闪而过

**根因**

**根因 1**（最严重）：[api.ts](../frontend/src/services/api.ts) 响应拦截器对**所有 401 一律跳转 /login**。
登录失败时后端返回 401，拦截器先执行 `window.location.href = '/login'`，页面刷新把
`message.error` 提示冲掉。

**根因 2**：[Login.tsx](../frontend/src/pages/Login.tsx) catch 块只弹消息，
没有针对"未注册"场景给出注册引导入口。

**修复**

1. **拦截器跳过 /auth/login 和 /auth/register 的 401**（[api.ts](../frontend/src/services/api.ts#L36-L42)）：

```ts
const isAuthEndpoint = url.includes('/auth/login') || url.includes('/auth/register');
if (error.response?.status === 401 && !isAuthEndpoint) {
  localStorage.removeItem('sekb_token');
  localStorage.removeItem('sekb_user');
  window.location.href = '/login';
}
```

2. **Login.tsx 增加 Alert 区域**（[Login.tsx](../frontend/src/pages/Login.tsx#L39-L52)）：
   失败时显示醒目错误信息 + "立即注册"链接

3. **Register.tsx 对称优化**：注册失败（如邮箱已存在）时引导到登录页

**影响范围**

- 登录失败时用户能立即看到错误原因
- 失败 Alert 提供"立即注册"快捷入口
- 鉴权接口的 401 不再被全局拦截器抢走

**安全考量**

后端登录失败仍统一返回"邮箱或密码错误"（401），**对外不暴露用户是否存在**，
防止用户枚举攻击。前端引导注册是基于 UX 考虑，与后端响应无关。

---

### 2.4 [ISSUE-004] 前端关键路径缺少日志和数据统计

| 字段 | 值 |
|------|---|
| 优先级 | P1 |
| 模块 | frontend/src/utils/logger.ts（新增）、api.ts、stores/user.ts、pages/Chat.tsx |
| 发现场景 | 用户上报"未注册点登录没提示"，前端 console 无任何日志可排查 |
| 关联测试 | TC-007、TC-008、TC-009 |

**现象**

前端关键操作（登录/注册/对话/API 调用/错误）完全没有日志输出，
线上问题排查只能靠用户描述，无法定位是网络问题、接口问题还是前端代码问题。

**根因**

前端没有任何统一的 logger 工具，所有 catch 块只 `message.error`，
console 也没有结构化输出。后端日志能看到请求到达与否，但前端发起方
和真实失败原因（如 XHR 网络错误 vs 401 vs 超时）无法分辨。

**修复**

1. **新建统一日志工具 [logger.ts](../frontend/src/utils/logger.ts)**：

```ts
logger.info('login_submit', { email: maskEmail(values.email) });
logger.error('api_error', { url, status, msg });
const done = logger.perf('api_request', { url: '/auth/login' });
done({ status: 200 });  // 自动计算耗时
```

- 结构化输出：`{ts, level, event, ...fields}`
- 控制台彩色样式：info 蓝 / warn 黄 / error 红
- 脱敏工具：`maskEmail()`、`maskToken()`
- 性能埋点：`perf()` 自动计算耗时
- 环境变量 `VITE_LOG_LEVEL` 控制日志级别

2. **[api.ts](../frontend/src/services/api.ts) 拦截器埋点**：
   - 请求拦截器：开始计时 + 记录 method/url
   - 响应拦截器：成功记录耗时；失败记录 `api_error` 事件

3. **[stores/user.ts](../frontend/src/stores/user.ts) 鉴权埋点**：
   `login_attempt` → `login_success` / `login_failed`

4. **[pages/Chat.tsx](../frontend/src/pages/Chat.tsx) 对话埋点**：
   `chat_send_message` + perf 耗时；`chat_send_message_failed`

**关键路径日志链路示例**

```
浏览器 console:
  [INFO]  login_submit          { email: "abc***" }
  [INFO]  api_request           { method: "POST", url: "/auth/login" }
  [ERROR] api_error             { url: "/auth/login", status: 401, ... }
  [WARN]  login_submit_failed   { email: "abc***", http_status: 401, ... }
```

**影响范围**

- 前端所有关键操作都有结构化日志
- 浏览器 F12 console 可立即看到事件流
- 性能数据（API 耗时）可量化用户体验

---

### 2.5 [ISSUE-005] Prometheus 缺少客户端事件指标

| 字段 | 值 |
|------|---|
| 优先级 | P1 |
| 模块 | backend/app/core/metrics.py、backend/app/api/routes/monitoring.py（新增） |
| 发现场景 | 前端有日志但无法被 Prometheus 采集聚合 |
| 关联测试 | TC-010、TC-011 |

**现象**

前端关键路径日志只在浏览器 console 可见，运维无法在 Grafana
统一查看登录失败率、API P95 延迟、客户端错误事件等指标。

**修复**

1. **[metrics.py](../backend/app/core/metrics.py#L173-L217) 新增 5 个指标**：

| 指标 | 类型 | 标签 | 用途 |
|------|------|------|------|
| `sekb_client_events_total` | Counter | event, level | 前端事件计数 |
| `sekb_login_attempts_total` | Counter | result | 登录漏斗 |
| `sekb_register_attempts_total` | Counter | result | 注册漏斗 |
| `sekb_client_api_duration_seconds` | Histogram | method, status | 前端 API 耗时分布 |
| `sekb_client_event_reports_total` | Counter | status | 上报链路健康度 |

2. **新建 [monitoring.py](../backend/app/api/routes/monitoring.py) 路由**：

`POST /api/v1/monitoring/client-event`，接收前端批量上报：
- 单批最多 50 条事件
- 字段校验失败的事件跳过
- 一律返回 202，避免前端重试风暴
- 事件白名单防高基数（`_CLIENT_EVENT_ALLOWLIST`）

3. **[auth.py](../backend/app/api/routes/auth.py) 调用 metrics**：
   `record_login_attempt("user_not_found" | "password_mismatch" | "success")`

4. **[logger.ts](../frontend/src/utils/logger.ts) 批量上报**：
   - 队列 + 5s 定时 flush
   - sendBeacon 优先，fetch keepalive 降级
   - 失败静默丢弃

**联合漏斗（覆盖所有失败场景）**

| 失败场景 | 后端记录 | 前端上报 | 最终指标 |
|---------|---------|---------|---------|
| 用户不存在 | `record_login_attempt("user_not_found")` | `login_submit_failed{http_status:401}` | `sekb_login_attempts_total{result="user_not_found"}` |
| 密码错误 | `record_login_attempt("password_mismatch")` | `login_submit_failed{http_status:401}` | `sekb_login_attempts_total{result="password_mismatch"}` |
| 网络中断 | （无请求到达） | `login_submit_failed{network_error:true}` | `sekb_login_attempts_total{result="network_error"}` |
| 登录成功 | `record_login_attempt("success")` | - | `sekb_login_attempts_total{result="success"}` |

**PromQL 验证示例**

```promql
# 登录失败漏斗
sum by (result) (sekb_login_attempts_total)

# 前端 API P95 延迟
histogram_quantile(0.95, sum by (le, method) (rate(sekb_client_api_duration_seconds[5m])))

# 前端关键错误事件 Top 5
topk(5, sum by (event) (rate(sekb_client_events_total{level="error"}[5m])))
```

---

### 2.6 [ISSUE-006] 客户端事件上报缺少安全与性能保护

| 字段 | 值 |
|------|---|
| 优先级 | P2 |
| 模块 | deploy/nginx.conf、frontend/src/utils/logger.ts |
| 关联测试 | TC-012、TC-013、TC-014 |

**现象**

P0-P1 完成后，客户端事件上报链路存在以下风险：
- 上报端点无独立限流，可能被恶意刷接口
- 高流量场景下上报量不可控
- 后端故障时前端无脑重试，加剧雪崩
- info 级别事件全量上报，浪费带宽

**修复**

| 优先级 | 注意事项 | 实现位置 |
|--------|---------|---------|
| P0 | nginx 端点限流 | [nginx.conf](../deploy/nginx.conf#L20-L22) 新增 `client_event_limit` zone |
| P1 | 上报开关 + 采样率 | [logger.ts](../frontend/src/utils/logger.ts#L86-L94) `VITE_LOG_REPORT_ENABLED` / `VITE_LOG_REPORT_RATE` |
| P2 | 连续失败退避 | [logger.ts](../frontend/src/utils/logger.ts#L100-L115) 5 次失败 → 60s 暂停 |
| P3 | 本地 console 可见 | 已支持 `VITE_LOG_LEVEL` 控制 |

**nginx 限流配置**

```nginx
limit_req_zone $binary_remote_addr zone=client_event_limit:10m rate=2r/s;

location = /api/v1/monitoring/client-event {
    limit_req zone=client_event_limit burst=10 nodelay;
    client_max_body_size 64k;
    proxy_pass http://sekb_backend;
    ...
}
```

**前端采样与退避**

```ts
// error 级别始终上报，其他按采样率
if (level !== 'error' && REPORT_RATE < 1) {
  if (Math.random() > REPORT_RATE) return;
}

// 5 次连续失败 → 暂停 60s
if (_consecutiveFailures >= REPORT_FAIL_THRESHOLD) {
  _pausedUntil = Date.now() + REPORT_PAUSE_MS;
}
```

**整体保护层次（从外到内）**

```
1. nginx 限流           ← P0：单客户端 2r/s + burst 10
2. 白名单防高基数        ← metrics.py：事件名必须在白名单内
3. 上报开关 + 采样率      ← P1：环境变量控制流量
4. 失败退避              ← P2：5 次失败暂停 60s
5. 静默丢弃              ← logger.ts：上报失败不影响主流程
6. console 兜底          ← 本地 console 始终可见
```

---

### 2.7 [ISSUE-007] 缺少一键重启脚本

| 字段 | 值 |
|------|---|
| 优先级 | P2 |
| 模块 | deploy/restart.sh（新增） |
| 关联测试 | TC-015 |

**现象**

每次代码更新后需要手动执行 git pull + docker compose build + up -d + 健康检查，
步骤繁琐易出错，且无法快速区分前端/后端单独重启。

**修复**

新增 [deploy/restart.sh](../deploy/restart.sh)：

```bash
bash deploy/restart.sh           # 默认：git pull + 构建前端 + 重启前后端
bash deploy/restart.sh backend   # 仅后端（最快，约 3s）
bash deploy/restart.sh fe         # 仅前端（含构建）
bash deploy/restart.sh --no-pull # 跳过 git pull
bash deploy/restart.sh --no-build # 跳过构建（仅 restart）
bash deploy/restart.sh --dry-run  # 预演
```

**设计要点**

- 路径自适应：通过 `BASH_SOURCE` 定位脚本目录
- 前置检查：缺 `docker-compose.prod.yml` 或 `.env.prod` 直接 fail
- 前端构建判断：Vite + nginx，代码改动必须 build；后端是 Python，restart 即可
- 健康检查重试：最多 10 次，每 2s 一次
- dry-run 不发 curl，纯本地预演

---

### 2.8 [ISSUE-008] 上传文件功能完全不可用

| 字段 | 值 |
|------|---|
| 优先级 | P0 |
| 模块 | frontend/src/services/file.ts、frontend/src/pages/Files.tsx、deploy/nginx.conf |
| 发现场景 | 用户拖文件到上传区，进度条闪一下就提示失败 |
| 关联测试 | TC-016、TC-017、TC-018 |

**现象**

上传任意文件，进度条闪一下就提示"上传失败"，没有任何有效日志可排查。

**根因（5 处不一致）**

| # | 问题 | 影响 |
|---|------|------|
| 1 | 前端调 `POST /api/v1/files/upload`，后端是 `POST /api/v1/upload` | 上传 404 |
| 2 | 前端调 `GET /api/v1/files`，后端无此路由 | 列表 404 |
| 3 | 前端调 `DELETE /api/v1/files/{id}`，后端是 `DELETE /api/v1/upload/entries/{id}` | 删除 404 |
| 4 | 前端期望 `{code, data, message}` 包装，后端直接返回裸对象 | 解析 undefined |
| 5 | nginx `client_max_body_size 10m`，后端允许 50MB | >10MB 文件被 nginx 挡掉（413） |

**修复**

1. **[services/file.ts](../frontend/src/services/file.ts) 重写**：
   - URL 改为后端实际路由
   - 数据模型从 `FileInfo` 改为 `UploadResult`（与后端 `UploadResponse` 对齐）
   - 不再期望 ApiResponse 包装，直接解析裸对象
   - 错误处理增强：识别 413 / 503 / 网络错误 / 超时
   - 加 logger 埋点：`upload_response` / `upload_failed` / `upload_network_error` / `upload_timeout`
   - XHR timeout 2 分钟（大文件保护）

2. **[pages/Files.tsx](../frontend/src/pages/Files.tsx) 重写**：
   - 上传成功展示分块/入库统计（`chunks_count`/`ingested_count`）
   - 区分 success / partial / error 三种入库状态
   - 上传历史展示（当前会话内有效）
   - 知识库状态卡片（条目数 + L3 启用状态）
   - L3 未启用时禁用上传 + 醒目 Alert 提示
   - 错误提示含 HTTP 状态码

3. **[nginx.conf](../deploy/nginx.conf#L161-L164) 上传体积提升**：
   ```nginx
   client_max_body_size 50m;   # 与后端 _MAX_FILE_SIZE_BYTES = 50MB 对齐
   ```

**上传流程**

```
用户拖文件 → Dragger.beforeUpload → uploadFile()
  ↓
XHR POST /api/v1/upload (FormData: file=...)
  ↓
nginx 50MB 限制通过 → backend upload_file
  ↓
1. 校验 L3 启用（否则 503）
2. 校验文件大小（>50MB 拒绝）
3. 落盘临时文件
4. FileProcessor 解析 + 分块
5. DirectVectorStore.add() 入库 ChromaDB
6. 返回 UploadResponse{chunks_count, ingested_count, status, error}
  ↓
前端 logger.info('upload_response', ...)
  ↓
成功：message.success("xxx.pdf 上传成功：12 分块，12 入库")
部分：message.warning("xxx.pdf 部分入库：9/12 成功，...")
失败：message.error("xxx.pdf 上传失败（HTTP 500）：内部错误")
  ↓
刷新知识库状态（条目数 +1）
```

---

### 2.9 [ISSUE-009] 知识库/分享条目 created_at 序列化 500

| 字段 | 值 |
|------|---|
| 优先级 | P1 |
| 模块 | backend/app/api/routes/knowledge.py、share.py |
| 发现场景 | 服务器端到端调试：访问知识库列表 / 搜索 / 分享条目浏览 |
| 关联测试 | 服务器端到端验证（上传 → 列表 → 搜索 → 分享条目） |

**现象**

以下 3 个接口返回 500 `'str' object has no attribute 'isoformat'`：

- `GET /api/v1/knowledge`（知识列表）
- `GET /api/v1/knowledge/search?q=...`（搜索命中时）
- `GET /api/v1/share/{id}/entries`（分享条目浏览）

**根因**

`KnowledgeEntry.created_at` 的类型是 **`str`**（`_now_iso()` 返回 ISO 格式字符串），但序列化代码对它调用了 `.isoformat()` —— 该方法只存在于 `datetime` 对象上。

**修复**

新增 `_fmt_dt()` 辅助函数，兼容 str 与 datetime 两种类型：

```python
def _fmt_dt(v):
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    return v.isoformat()
```

`knowledge.py` 的 `_entry_to_dict` 与 `share.py` 的 `list_shared_entries` 等所有 `created_at` 序列化点统一改为 `_fmt_dt(...)`。

**提交**：`bebf3be`、`757d2db`（后者为条目更新显式生成向量，避免 chromadb 懒加载默认模型下载 ONNX）。

---

### 2.10 [ISSUE-010] ChromaDB 向量检索索引损坏（哈希降级导致）

| 字段 | 值 |
|------|---|
| 优先级 | P0 |
| 模块 | backend/app/core/embedding.py、config.yaml、knowledge_base.py、bootstrap.py |
| 发现场景 | 知识搜索返回空；向量检索报 `Error finding id`；聊天 RAG / 分享问答检索同样受影响 |
| 关联测试 | ChromaDB 层检索验证 + 服务器端到端搜索验证 |

**现象**

- `collection.query`（向量检索）报 `Error executing plan: Internal error: Error finding id`
- `collection.get`（元数据查询）正常，能列出 27 条条目，但向量检索始终失败/返回空

**根因**

早期 embedding 模型（bge-small-zh-v1.5）加载失败时，代码静默降级为 **256 维哈希向量**；之后模型加载成功（**512 维**）。两种维度的向量混进同一个 ChromaDB collection，破坏了 HNSW 索引。

**修复（两步）**

1. **禁用哈希降级**：`LocalEmbeddingFunction` 新增 `allow_hash_fallback` 参数（默认 `false`），模型加载失败时抛异常而非静默降级；由 `config.yaml` 的 `l3_knowledge.allow_hash_fallback` 控制。提交 `65fc097`。
2. **重建 collection**：读取全部 27 条数据 → 备份 → 删除集合 → 用真实 bge 模型统一重灌（512 维），重建后向量检索恢复正常。

**预防**：生产环境务必保持 `allow_hash_fallback: false`；如需在开发环境无模型时启用哈希向量，需同时清空并重建 collection，避免维度混用。

---

## 3. 变更文件清单

### 后端（5 个文件）

| 文件 | 类型 | 关联问题 |
|------|------|---------|
| [backend/app/api/routes/auth.py](../backend/app/api/routes/auth.py) | 修改 | ISSUE-003、ISSUE-005 |
| [backend/app/api/routes/monitoring.py](../backend/app/api/routes/monitoring.py) | 新增 | ISSUE-005 |
| [backend/app/api/server.py](../backend/app/api/server.py) | 修改 | ISSUE-005 |
| [backend/app/core/metrics.py](../backend/app/core/metrics.py) | 修改 | ISSUE-005 |
| [docker-compose.prod.yml](../docker-compose.prod.yml) | 修改 | ISSUE-001 |

### 前端（6 个文件）

| 文件 | 类型 | 关联问题 |
|------|------|---------|
| [frontend/src/utils/logger.ts](../frontend/src/utils/logger.ts) | 新增 | ISSUE-004、ISSUE-005、ISSUE-006 |
| [frontend/src/services/api.ts](../frontend/src/services/api.ts) | 修改 | ISSUE-003、ISSUE-004 |
| [frontend/src/services/file.ts](../frontend/src/services/file.ts) | 重写 | ISSUE-008 |
| [frontend/src/stores/user.ts](../frontend/src/stores/user.ts) | 修改 | ISSUE-004 |
| [frontend/src/pages/Login.tsx](../frontend/src/pages/Login.tsx) | 修改 | ISSUE-003 |
| [frontend/src/pages/Register.tsx](../frontend/src/pages/Register.tsx) | 修改 | ISSUE-003 |
| [frontend/src/pages/Files.tsx](../frontend/src/pages/Files.tsx) | 重写 | ISSUE-008 |
| [frontend/src/pages/Chat.tsx](../frontend/src/pages/Chat.tsx) | 修改 | ISSUE-004 |

### 部署与运维（3 个文件）

| 文件 | 类型 | 关联问题 |
|------|------|---------|
| [deploy/nginx.conf](../deploy/nginx.conf) | 修改 | ISSUE-006、ISSUE-008 |
| [deploy/restart.sh](../deploy/restart.sh) | 新增 | ISSUE-007 |
| [.env.prod](../deploy/.env.prod.example) | 修改 | ISSUE-002 |

### 测试（4 个文件，新增）

| 文件 | 类型 | 关联问题 |
|------|------|---------|
| [backend/tests/unit/test_auth_metrics.py](../backend/tests/unit/test_auth_metrics.py) | 新增 | ISSUE-003、ISSUE-005 |
| [backend/tests/unit/test_monitoring_endpoint.py](../backend/tests/unit/test_monitoring_endpoint.py) | 新增 | ISSUE-005 |
| [frontend/src/utils/__tests__/logger.test.ts](../frontend/src/utils/__tests__/logger.test.ts) | 新增 | ISSUE-004、ISSUE-006 |
| [frontend/src/services/__tests__/file.test.ts](../frontend/src/services/__tests__/file.test.ts) | 新增 | ISSUE-008 |

### 文档（2 个文件，新增）

| 文件 | 类型 | 用途 |
|------|------|------|
| [docs/ISSUES-FIXES-2026-08.md](./ISSUES-FIXES-2026-08.md) | 新增 | 本文档 |
| [docs/testCase/INCREMENTAL-TEST-CASES.md](./testCase/INCREMENTAL-TEST-CASES.md) | 新增 | 增量测试用例 |

---

## 4. 回归测试矩阵

详见 [testCase/INCREMENTAL-TEST-CASES.md](./testCase/INCREMENTAL-TEST-CASES.md)。

| 问题编号 | 测试用例编号 | 测试文件 | 测试类型 |
|---------|------------|---------|---------|
| ISSUE-001 | TC-001, TC-002 | （手动） | 集成 |
| ISSUE-002 | TC-003 | （手动） | E2E |
| ISSUE-003 | TC-004, TC-005, TC-006 | test_auth_metrics.py | 单元 + 集成 |
| ISSUE-004 | TC-007, TC-008, TC-009 | logger.test.ts | 单元 |
| ISSUE-005 | TC-010, TC-011 | test_monitoring_endpoint.py | 单元 + 集成 |
| ISSUE-006 | TC-012, TC-013, TC-014 | logger.test.ts | 单元 |
| ISSUE-007 | TC-015 | （手动 + shellcheck） | E2E |
| ISSUE-008 | TC-016, TC-017, TC-018 | file.test.ts | 单元 + 集成 |

---

## 5. 部署生效指引

### 5.1 全量部署（推荐）

```bash
# 拉取最新代码
git pull

# 一键重启前后端（含 git pull + 前端构建 + 健康检查）
bash deploy/restart.sh
```

### 5.2 分步部署

```bash
# 仅后端（最快）
bash deploy/restart.sh backend

# 仅前端（含构建）
bash deploy/restart.sh fe

# 跳过 git pull（已手动 pull）
bash deploy/restart.sh --no-pull
```

### 5.3 验证清单

部署完成后按顺序验证：

```bash
# 1. 容器状态
docker compose -f docker-compose.prod.yml --env-file .env.prod ps

# 2. 健康检查
curl -s http://localhost:8000/api/v1/health/live | head
curl -s http://localhost/api/v1/health/live | head

# 3. 上传接口路由存在（应返回 401 或 503，不是 404）
curl -X POST http://localhost/api/v1/upload -w "%{http_code}\n" -o /dev/null

# 4. 监控端点路由存在（应返回 202）
curl -X POST http://localhost/api/v1/monitoring/client-event \
  -H "Content-Type: application/json" \
  -d '{"events":[]}' -w "\n%{http_code}\n"

# 5. Prometheus 指标包含客户端事件计数器
curl -s http://localhost:8000/metrics | grep -E "sekb_login|sekb_client"

# 6. nginx 限流生效（连续 30 次应看到 429）
for i in $(seq 1 30); do
  curl -o /dev/null -s -w "%{http_code}\n" \
    -X POST http://localhost/api/v1/monitoring/client-event \
    -H "Content-Type: application/json" \
    -d '{"events":[]}'
done
```

### 5.4 自动化测试运行

```bash
# 后端单元测试（含本次新增）
cd backend
pip install -e ".[dev]"
pytest tests/unit/test_auth_metrics.py tests/unit/test_monitoring_endpoint.py -v

# 前端单元测试（含本次新增）
cd ../frontend
npm install
npm test -- logger file
```
