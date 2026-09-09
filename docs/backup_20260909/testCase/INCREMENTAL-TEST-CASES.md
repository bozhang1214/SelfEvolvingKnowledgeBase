# SEKB 增量测试用例 - 2026 年 8 月问题修复

> 文档版本：v1.0
> 最后更新：2026-08-20
> 维护者：SEKB Team
> 关联文档：[ISSUES-FIXES-2026-08.md](../ISSUES-FIXES-2026-08.md)、[TEST-CASES.md](./TEST-CASES.md)

---

## 目录

1. [概述](#1-概述)
2. [测试用例编号规则](#2-测试用例编号规则)
3. [测试用例清单](#3-测试用例清单)
   - 3.1 [ISSUE-001] CPU 限制适配
   - 3.2 [ISSUE-002] 备案期间端口访问
   - 3.3 [ISSUE-003] 登录失败提示与注册引导
   - 3.4 [ISSUE-004] 前端关键路径日志
   - 3.5 [ISSUE-005] Prometheus 客户端事件指标
   - 3.6 [ISSUE-006] 上报安全与性能保护
   - 3.7 [ISSUE-007] 一键重启脚本
   - 3.8 [ISSUE-008] 上传文件功能
4. [自动化测试集成指引](#4-自动化测试集成指引)
5. [全量回归测试矩阵](#5-全量回归测试矩阵)

---

## 1. 概述

本文档记录 2026 年 8 月问题修复对应的增量测试用例。
本批用例与 [TEST-CASES.md](./TEST-CASES.md) 中的原有用例并行存在，
后续维护者可选择合并入主测试用例文档。

### 覆盖范围

| 维度 | 新增用例数 |
|------|----------|
| 单元测试 | 9 |
| 集成测试 | 6 |
| 端到端（手动 / 自动） | 3 |
| **合计** | **18** |

---

## 2. 测试用例编号规则

- **TC-XXX**：测试用例编号，三位数字递增
- **U**：单元测试（pytest / vitest）
- **I**：集成测试
- **E**：端到端测试

例如 `TC-004-U` 表示第 4 号用例的单元测试部分。

---

## 3. 测试用例清单

### 3.1 [ISSUE-001] CPU 限制适配

#### TC-001-I：docker-compose CPU 限制配置校验

| 字段 | 值 |
|------|---|
| 类型 | 集成测试（配置校验） |
| 文件 | （待新增）`backend/tests/integration/test_compose_config.py` |
| 关联 | ISSUE-001 |

**前置条件**

- `docker-compose.prod.yml` 已应用本次修复（backend cpus = 1.5）

**测试步骤**

1. 读取 `docker-compose.prod.yml` 解析 YAML
2. 断言 `services.backend.deploy.resources.limits.cpus` 值为 `"1.5"`
3. 断言 `services.frontend.deploy.resources.limits.cpus` 值为 `"0.5"`
4. 断言 backend 与 frontend cpus 之和 ≤ 2.0（适配 2 核服务器）

**预期结果**

- 所有断言通过，配置与修复后的预期一致

#### TC-002-E：2 核服务器容器启动验证

| 字段 | 值 |
|------|---|
| 类型 | 端到端测试（手动执行） |
| 关联 | ISSUE-001 |

**前置条件**

- 服务器为 2 核腾讯云轻量服务器
- `.env.prod` 已配置

**测试步骤**

```bash
# 1. 拉取最新代码
git pull

# 2. 启动 backend
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d backend

# 3. 等待 30 秒后查看状态
sleep 30 && docker compose -f docker-compose.prod.yml --env-file .env.prod ps backend
```

**预期结果**

- backend 容器状态为 `Up`
- 没有 `ContainerConfig` 错误
- 健康检查通过：`curl http://localhost:8000/api/v1/health/live` 返回 200

---

### 3.2 [ISSUE-002] 备案期间端口访问

#### TC-003-E：8080 端口临时访问验证

| 字段 | 值 |
|------|---|
| 类型 | 端到端测试（手动执行） |
| 关联 | ISSUE-002 |

**前置条件**

- `.env.prod` 中 `FRONTEND_PORT=8080`
- 腾讯云安全组已放行 TCP 8080 + 8000

**测试步骤**

```bash
# 1. 服务器本地
curl -I http://localhost:8080/

# 2. 公网（外部浏览器）
# 访问 http://<服务器IP>:8080
```

**预期结果**

- 服务器本地 curl 返回 200 OK
- 外部浏览器访问能看到登录页
- 通过 8080 反代访问 `/api/v1/health/live` 返回 200

---

### 3.3 [ISSUE-003] 登录失败提示与注册引导

#### TC-004-U：后端登录失败原因记录

| 字段 | 值 |
|------|---|
| 类型 | 单元测试 |
| 文件 | `backend/tests/unit/test_auth_metrics.py` |
| 关联 | ISSUE-003、ISSUE-005 |

**测试步骤**

1. Mock `UserStore.get_by_email` 返回 `None`（用户不存在）
2. 调用 `auth_login` 路由
3. 断言响应状态码 401
4. 断言响应 JSON `detail` 字段为 "邮箱或密码错误"
5. 断言 `sekb_login_attempts_total{result="user_not_found"}` 指标 +1
6. 断言审计日志包含 `action=login_failed`、`detail.reason=user_not_found`

**预期结果**

- 后端正确区分失败原因并记录到 metrics 和 audit
- 对外响应不暴露用户是否存在

#### TC-005-U：后端密码错误场景

| 字段 | 值 |
|------|---|
| 类型 | 单元测试 |
| 文件 | `backend/tests/unit/test_auth_metrics.py` |
| 关联 | ISSUE-003、ISSUE-005 |

**测试步骤**

1. Mock `UserStore.get_by_email` 返回有效用户
2. Mock `verify_password` 返回 `False`
3. 调用 `auth_login`
4. 断言 401
5. 断言 `sekb_login_attempts_total{result="password_mismatch"}` +1

#### TC-006-I：前端登录失败 Alert 显示

| 字段 | 值 |
|------|---|
| 类型 | 集成测试（组件渲染） |
| 文件 | `frontend/src/pages/__tests__/Login.test.tsx` |
| 关联 | ISSUE-003 |

**测试步骤**

1. Mock `uploadFile` / `apiClient.post` 返回 401
2. 渲染 Login 组件
3. 输入邮箱 `notexist@example.com` 和密码
4. 点击登录按钮
5. 等待异步完成

**预期结果**

- 不再触发 `window.location.href` 跳转
- Alert 区域显示"邮箱或密码错误"
- Alert 描述包含"立即注册"链接
- 链接 `to="/register"` 路径正确

---

### 3.4 [ISSUE-004] 前端关键路径日志

#### TC-007-U：logger.info / warn / error 输出格式

| 字段 | 值 |
|------|---|
| 类型 | 单元测试 |
| 文件 | `frontend/src/utils/__tests__/logger.test.ts` |
| 关联 | ISSUE-004 |

**测试步骤**

1. Spy `console.info` / `console.warn` / `console.error`
2. 调用 `logger.info('test_event', { foo: 'bar' })`
3. 断言 `console.info` 被调用一次
4. 断言第一个参数包含 `[INFO]` 和 `test_event`
5. 断言字段对象包含 `foo: 'bar'` 和 `ts` 时间戳

**预期结果**

- 日志按级别路由到对应 console 方法
- 格式符合 `[LEVEL] event {fields}` 规范
- 字段对象结构化

#### TC-008-U：logger.perf 耗时计算

| 字段 | 值 |
|------|---|
| 类型 | 单元测试 |
| 文件 | `frontend/src/utils/__tests__/logger.test.ts` |
| 关联 | ISSUE-004 |

**测试步骤**

1. Mock `Date.now()` 返回 1000
2. 调用 `const done = logger.perf('api_request', { url: '/x' })`
3. Mock `Date.now()` 返回 1500
4. 调用 `done({ status: 200 })`
5. 断言 `console.info` 被调用
6. 断言日志字段包含 `duration_ms: 500`、`status: 200`、`url: '/x'`

#### TC-009-U：maskEmail / maskToken 脱敏

| 字段 | 值 |
|------|---|
| 类型 | 单元测试 |
| 文件 | `frontend/src/utils/__tests__/logger.test.ts` |
| 关联 | ISSUE-004 |

**测试步骤**

1. 调用 `maskEmail('abc@example.com')`
2. 断言返回 `abc***` 或 `ab***@example.com`（按实现而定）
3. 调用 `maskToken('eyJhbGc...')`
4. 断言返回前若干位 + `***`

**预期结果**

- 邮箱前缀保留前 3 位，剩余脱敏
- Token 只保留前 8 位，其余脱敏
- 短字符串（< 4 位）全脱敏为 `***`

---

### 3.5 [ISSUE-005] Prometheus 客户端事件指标

#### TC-010-U：monitoring 路由接收合法事件

| 字段 | 值 |
|------|---|
| 类型 | 单元测试 |
| 文件 | `backend/tests/unit/test_monitoring_endpoint.py` |
| 关联 | ISSUE-005 |

**测试步骤**

1. 用 FastAPI `TestClient` 调用 `POST /api/v1/monitoring/client-event`
2. 请求体包含 3 条合法事件（事件名在白名单内）
3. 断言响应状态码 202
4. 断言 `sekb_client_events_total{event="login_submit",level="info"}` +1
5. 断言 `sekb_client_event_reports_total{status="accepted"}` +1

**预期结果**

- 合法事件被接收并写入 Prometheus 指标
- 不在白名单的事件被静默跳过

#### TC-011-U：monitoring 路由拒绝非法事件

| 字段 | 值 |
|------|---|
| 类型 | 单元测试 |
| 文件 | `backend/tests/unit/test_monitoring_endpoint.py` |
| 关联 | ISSUE-005 |

**测试步骤**

1. 调用 `POST /api/v1/monitoring/client-event`
2. 请求体包含：
   - 1 条合法事件（白名单内）
   - 1 条非法事件（事件名不在白名单）
   - 1 条格式错误事件（缺 ts 字段）
3. 断言响应状态码 202（不报错）
4. 断言只有 1 条合法事件被记录到 metrics
5. 断言 `sekb_client_event_reports_total{status="rejected"}` +2

**预期结果**

- 单条事件错误不会影响其他事件
- 整批请求仍返回 202 避免前端重试
- 非白名单事件被拒绝并计入 rejected 指标

---

### 3.6 [ISSUE-006] 上报安全与性能保护

#### TC-012-U：nginx 限流配置校验

| 字段 | 值 |
|------|---|
| 类型 | 单元测试（配置文件解析） |
| 文件 | `backend/tests/integration/test_nginx_config.py`（待新增） |
| 关联 | ISSUE-006 |

**测试步骤**

1. 读取 `deploy/nginx.conf`
2. 断言存在 `limit_req_zone ... zone=client_event_limit ... rate=2r/s`
3. 断言存在 `location = /api/v1/monitoring/client-event`
4. 断言该 location 内有 `limit_req zone=client_event_limit burst=10 nodelay`
5. 断言该 location 内有 `client_max_body_size 64k`
6. 断言该 location 在 `location /api/` 之前（用文本位置校验）

#### TC-013-U：上报开关 VITE_LOG_REPORT_ENABLED

| 字段 | 值 |
|------|---|
| 类型 | 单元测试 |
| 文件 | `frontend/src/utils/__tests__/logger.test.ts` |
| 关联 | ISSUE-006 |

**测试步骤**

1. 设置 `import.meta.env.VITE_LOG_REPORT_ENABLED = 'false'`
2. 调用 `logger.warn('test_event')`
3. 断言 `console.warn` 被调用（本地仍输出）
4. 等待 6 秒（flush 周期 + 1s）
5. 断言没有发起 XHR / sendBeacon 请求

**预期结果**

- 开关关闭后只本地 console，不上报

#### TC-014-U：连续失败退避

| 字段 | 值 |
|------|---|
| 类型 | 单元测试 |
| 文件 | `frontend/src/utils/__tests__/logger.test.ts` |
| 关联 | ISSUE-006 |

**测试步骤**

1. Mock `navigator.sendBeacon` 返回 `false`
2. Mock `fetch` reject（网络错误）
3. 触发 5 次 flush（每次队列有 1 条事件）
4. 断言第 5 次后 console.warn 输出"暂停上报"提示
5. 触发第 6 次 flush
6. 断言没有再发起请求（处于暂停期）
7. Mock `Date.now` 推进 60 秒
8. 触发第 7 次 flush
9. 断言又发起了请求（探针恢复）

**预期结果**

- 连续失败 5 次后暂停 60s
- 暂停期不发请求
- 暂停结束后下次 flush 探针恢复

---

### 3.7 [ISSUE-007] 一键重启脚本

#### TC-015-E：restart.sh 参数解析与 dry-run

| 字段 | 值 |
|------|---|
| 类型 | 端到端（shell 测试） |
| 文件 | `deploy/restart.sh` |
| 关联 | ISSUE-007 |

**测试步骤**

```bash
# 1. 语法检查
bash -n deploy/restart.sh

# 2. --help 输出
bash deploy/restart.sh --help | grep -q "用法"

# 3. dry-run 不实际执行
bash deploy/restart.sh --dry-run | grep -q "DRY"

# 4. backend 简写
bash deploy/restart.sh be --dry-run --no-pull | grep -q "backend"

# 5. 未知参数报错
bash deploy/restart.sh unknown 2>&1 | grep -q "未知参数"
```

**预期结果**

- 语法检查通过
- `--help` 显示帮助文本
- `--dry-run` 不发 curl、不调用 docker
- 简写 `be` 等价于 `backend`
- 未知参数报错并退出 1

---

### 3.8 [ISSUE-008] 上传文件功能

#### TC-016-U：file.ts URL 路径对齐

| 字段 | 值 |
|------|---|
| 类型 | 单元测试 |
| 文件 | `frontend/src/services/__tests__/file.test.ts` |
| 关联 | ISSUE-008 |

**测试步骤**

1. Mock `XMLHttpRequest`
2. 调用 `uploadFile(file, ...)` 创建的 XHR
3. 断言 `xhr.open` 第一个参数为 `'POST'`
4. 断言 `xhr.open` 第二个参数包含 `/api/v1/upload`（不是 `/api/v1/files/upload`）

**预期结果**

- 上传 URL 与后端实际路由对齐

#### TC-017-U：file.ts 响应解析（裸对象，非 ApiResponse 包装）

| 字段 | 值 |
|------|---|
| 类型 | 单元测试 |
| 文件 | `frontend/src/services/__tests__/file.test.ts` |
| 关联 | ISSUE-008 |

**测试步骤**

1. Mock `XMLHttpRequest`
2. 设置 `xhr.status = 200`
3. 设置 `xhr.responseText = '{"file_name":"a.pdf","file_size":100,"chunks_count":3,"ingested_count":3,"status":"success","error":"","entry_ids":["e1","e2","e3"]}'`
4. 触发 `xhr.onload`
5. 断言 `onDone` 回调被调用，参数为 `UploadResult` 对象
6. 断言 `result.file_name === 'a.pdf'`
7. 断言 `result.chunks_count === 3`

**预期结果**

- 直接解析裸 JSON 对象，不需要 `data` 包装层

#### TC-018-U：file.ts 413 错误处理

| 字段 | 值 |
|------|---|
| 类型 | 单元测试 |
| 文件 | `frontend/src/services/__tests__/file.test.ts` |
| 关联 | ISSUE-008 |

**测试步骤**

1. Mock `XMLHttpRequest`
2. 设置 `xhr.status = 413`
3. 设置 `xhr.responseText = '<html>413 Request Entity Too Large</html>'`
4. 触发 `xhr.onload`
5. 断言 `onError` 被调用
6. 断言错误消息包含"文件过大"或"50MB"提示

**预期结果**

- 413 错误被识别为"文件过大"，给出明确提示
- 不再抛出"解析响应失败"误导信息

---

## 4. 自动化测试集成指引

### 4.1 后端 pytest 集成

**新增测试文件位置**

```
backend/tests/unit/
├── test_auth_metrics.py        # TC-004-U、TC-005-U
└── test_monitoring_endpoint.py # TC-010-U、TC-011-U
```

**运行命令**

```bash
cd backend
pip install -e ".[dev]"
pytest tests/unit/test_auth_metrics.py tests/unit/test_monitoring_endpoint.py -v
```

**pytest.ini 标记**

```ini
[pytest]
markers =
    unit: 单元测试
    integration: 集成测试
    e2e: 端到端测试（需要外部服务）
```

### 4.2 前端 vitest 集成

**前置：安装 vitest**

```bash
cd frontend
npm install -D vitest @vitest/coverage-vite jsdom @testing-library/react @testing-library/jest-dom
```

**vitest.config.ts**

```ts
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test-setup.ts'],
    coverage: { reporter: ['text', 'html'] },
  },
});
```

**新增测试文件位置**

```
frontend/src/
├── utils/__tests__/logger.test.ts   # TC-007-U、TC-008-U、TC-009-U、TC-013-U、TC-014-U
├── services/__tests__/file.test.ts  # TC-016-U、TC-017-U、TC-018-U
└── pages/__tests__/Login.test.tsx  # TC-006-I（可选，后续补充）
```

**运行命令**

```bash
cd frontend
npm test -- logger
npm test -- file
npm test                # 运行全部 vitest
```

### 4.3 CI 流水线集成

在 `.github/workflows/ci.yml` 的 test job 中增加：

```yaml
- name: Run backend tests (incremental)
  working-directory: backend
  run: |
    pip install -e ".[dev]"
    pytest tests/unit/test_auth_metrics.py tests/unit/test_monitoring_endpoint.py -v

- name: Run frontend tests
  working-directory: frontend
  run: |
    npm ci
    npm test
```

---

## 5. 全量回归测试矩阵

下表是本次增量用例 + 原有用例的全量回归矩阵，
后续每次发布前都应执行一次完整回归：

| 模块 | 用例编号 | 文件 | 类型 | 是否自动 |
|------|---------|------|------|---------|
| CPU 配置 | TC-001-I | test_compose_config.py | 集成 | ✅ |
| CPU 配置 | TC-002-E | （手动） | E2E | ❌ |
| 端口访问 | TC-003-E | （手动） | E2E | ❌ |
| 登录失败原因 | TC-004-U | test_auth_metrics.py | 单元 | ✅ |
| 登录密码错误 | TC-005-U | test_auth_metrics.py | 单元 | ✅ |
| 登录 Alert 显示 | TC-006-I | Login.test.tsx | 集成 | ✅ |
| logger 输出格式 | TC-007-U | logger.test.ts | 单元 | ✅ |
| logger perf | TC-008-U | logger.test.ts | 单元 | ✅ |
| logger 脱敏 | TC-009-U | logger.test.ts | 单元 | ✅ |
| monitoring 路由 | TC-010-U | test_monitoring_endpoint.py | 单元 | ✅ |
| monitoring 拒绝 | TC-011-U | test_monitoring_endpoint.py | 单元 | ✅ |
| nginx 限流 | TC-012-U | test_nginx_config.py | 集成 | ✅ |
| 上报开关 | TC-013-U | logger.test.ts | 单元 | ✅ |
| 上报退避 | TC-014-U | logger.test.ts | 单元 | ✅ |
| restart 脚本 | TC-015-E | restart.sh | E2E | ❌ |
| 上传 URL 对齐 | TC-016-U | file.test.ts | 单元 | ✅ |
| 上传响应解析 | TC-017-U | file.test.ts | 单元 | ✅ |
| 上传 413 处理 | TC-018-U | file.test.ts | 单元 | ✅ |

### 自动化覆盖率统计

| 维度 | 总数 | 自动化 | 覆盖率 |
|------|------|-------|--------|
| 单元测试 | 12 | 12 | 100% |
| 集成测试 | 3 | 2 | 67% |
| 端到端 | 3 | 0 | 0% |
| **合计** | **18** | **14** | **78%** |

**未自动化原因说明**

- TC-002-E：依赖真实 2 核服务器，无法在 CI 中复现
- TC-003-E：依赖腾讯云备案状态，无法在 CI 中模拟
- TC-006-I：前端组件测试需要 `@testing-library/react`，本次未完整安装
- TC-015-E：shell 脚本的 `--help` / `--dry-run` 可以加到 CI，但 docker 命令需要真实环境

### 后续改进计划

| 优先级 | 任务 | 目标 |
|--------|------|------|
| P1 | 安装 `@testing-library/react`，补 TC-006-I | 集成测试自动化 |
| P2 | 用 Docker Compose 起本地测试环境跑 TC-002-E | E2E 半自动化 |
| P2 | restart.sh 的 `--dry-run` 和 `--help` 加入 CI | E2E 自动化 |
| P3 | 用 Playwright 跑上传 + 登录的完整 E2E | E2E 全自动化 |
