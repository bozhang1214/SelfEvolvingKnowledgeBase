## 2026-09-16（部署：密钥自举（BROWSER_INTERNAL_TOKEN / Grafana 口令缺失即生成））

- **背景**：S12 给 browser-service 加了内部鉴权 token，但它的实现是
  `if _INTERNAL_TOKEN:` —— **token 为空时直接放行**。也就是说「忘了在 .env.prod 里配」
  等于「没有防护」，而同 Docker 网络内任意容器都能读写 BOSS 登录 Cookie。
  Grafana 同理：`GRAFANA_ADMIN_PASSWORD` 不配就是默认 `admin/admin`。
  靠「记得配」来保证安全是不可靠的，改为让安全成为默认值。

- **改动**：
  - `deploy/deploy.sh`：新增**阶段 0.5/7：密钥自举**（在构建/启动之前）。
    `bootstrap_secret()` 对 `BROWSER_INTERNAL_TOKEN`（`openssl rand -hex 24`）与
    `GRAFANA_ADMIN_PASSWORD`（`openssl rand -base64 24`）：
    已配置且非空 → 保持不变；存在但为空 → 原地 `sed` 替换；完全缺失 → 追加到 `.env.prod`。
    幂等，重复部署不会换值；DRY-RUN 下只打印不写文件。
  - `deploy/.env.prod.example`：两处注释更正为「留空 = 不鉴权」的红字警告 + 自动生成说明。

- **验证**：
  - `bash -n deploy/deploy.sh` 通过；
  - 隔离目录内模拟两次调用：第 1 次生成并原地替换空值（不破坏其它行顺序），
    第 2 次输出「已配置，保持不变」→ 幂等成立；
  - 后端 841 passed / ruff clean；前端 68 passed / tsc clean。
