# 15 · JobCopilot MCP 端点（云端平台接入）

> 面向运维：这个端点**是什么、怎么测、怎么换令牌、安全边界在哪**。
> 平台侧的具体配置见 `jobcopilot/docs/integrations/`（扣子 / 百炼 / 千帆 / HiAgent / Dify）。

---

## 1. 它是什么

SEKB 用的内核 MCP Server 是 **backend 容器内的 stdio 子进程**，外部访问不到。
云端平台只认 HTTP，所以额外起了一个**同一套内核的 HTTP 入口**：

| 项 | 值 |
|---|---|
| 容器 | `sekb-jobcopilot-mcp`（复用 `self-evolving-kb-backend` 镜像，**不新增镜像**） |
| 对外地址 | `https://bos-studio.tech/jobcopilot/mcp`（streamable HTTP）<br>`https://bos-studio.tech/jobcopilot/sse`（SSE，百度千帆只支持这条） |
| 容器内监听 | `0.0.0.0:8765`（仅容器网络） |
| 宿主机端口 | `127.0.0.1:8765`（**只绑回环**，对外一律经 nginx） |
| 由谁启动 | `deploy/deploy.sh` **阶段 3.5**（必须在 frontend 之前——nginx 要能解析该容器名） |

## 2. 鉴权

| 请求头 | 作用 |
|---|---|
| `Authorization: Bearer <访问令牌>` | **门禁**（`JOBCOPILOT_HTTP_TOKEN`），缺失/错误返回 401 |
| `X-JobCopilot-Api-Key: <LLM Key>` | **调用方自己的 LLM Key（BYOK）** |
| `?token=<访问令牌>` | 兜底（千帆的配置 JSON 没有 headers 字段，只能放查询串） |

⚠️ **容器内刻意不配任何 LLM Key**：这是公网端点，必须 BYOK，否则任何能访问到的人
都花这台服务器的额度。不带 Key 调 LLM 工具会返回可操作的配置错误（已实测）。

## 3. 令牌在哪、怎么换

- 存放：服务器 `SelfEvolvingKnowledgeBase/.env.prod` 的 `JOBCOPILOT_HTTP_TOKEN`（文件权限 `600`）；
- 同时还有 `JOBCOPILOT_ALLOWED_HOSTS=bos-studio.tech`（Host 白名单，**必须是对外域名**，
  否则内核返回 421——nginx 用 `proxy_set_header Host $host` 传的就是它）；
- 生成新令牌（不打印明文）：

  ```bash
  cd /opt/self-evolving-kb/SelfEvolvingKnowledgeBase
  umask 077
  sed -i '/^JOBCOPILOT_HTTP_TOKEN=/d' .env.prod
  printf 'JOBCOPILOT_HTTP_TOKEN=%s\n' "$(openssl rand -hex 24)" >> .env.prod
  docker compose -f docker-compose.prod.yml --env-file .env.prod up -d jobcopilot-mcp
  ```

> 换令牌后**平台侧要同步更新**，否则平台调用会 401。

## 4. 从外部自测（复制即用）

```bash
TOKEN=<访问令牌>

# 1) 无令牌 → 401（说明服务活着且鉴权生效）
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://bos-studio.tech/jobcopilot/mcp \
  -H 'content-type: application/json' -H 'accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'

# 2) 带令牌但无会话 → 400 Missing session ID（说明鉴权已过、进到协议层）
curl -s -X POST https://bos-studio.tech/jobcopilot/mcp \
  -H 'content-type: application/json' -H 'accept: application/json, text/event-stream' \
  -H "authorization: Bearer $TOKEN" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'

# 3) SSE 的 endpoint 事件必须带 /jobcopilot 前缀（否则客户端会请求错地址）
curl -s -N --max-time 8 https://bos-studio.tech/jobcopilot/sse \
  -H 'accept: text/event-stream' -H "authorization: Bearer $TOKEN" | head -2
# → event: endpoint
#   data: /jobcopilot/sse/messages/?session_id=...
```

完整链路（列工具 / 调工具 / BYOK）用官方客户端跑：`docs/tmp/verify_public_mcp.py`
（需 `JOBCOPILOT_HTTP_TOKEN` 与 `DEEPSEEK_API_KEY` 环境变量）。

## 5. 安全边界（改之前先读）

- 端口只绑 `127.0.0.1`，**不新开公网端口**，对外只有 443 经 nginx；
- 容器 `read_only: true` + `no-new-privileges` + 内存 1G 上限：`save_profile`
  在这个端点上**用不了是刻意的**——共享端点上的画像会串到别人身上，
  调用方应改用 `user_profile` 参数按请求传；
- **绝不**给这个容器配 `JOBCOPILOT_ALLOW_SOURCE_PATH=1`（公网端点上的任意文件读取）；
- `JOBCOPILOT_HTTP_TOKEN` 未设时内核**拒绝启动**并打印可操作提示（这是安全闸，不是配置遗漏）；
- 与 SEKB **共用同一份提示词**（`./prompt/job` 只读挂载），所以提示词热改对两个入口同时生效。

## 6. 平台接入模板（等 owner 处理时用）

**扣子 / 百炼（streamable HTTP）**：

```json
{
  "mcpServers": {
    "jobcopilot": {
      "url": "https://bos-studio.tech/jobcopilot/mcp",
      "headers": {
        "Authorization": "Bearer <访问令牌>",
        "X-JobCopilot-Api-Key": "<调用方自己的 LLM Key>"
      }
    }
  }
}
```

**百度千帆（只支持 SSE，且配置里没有 headers → 令牌放查询串）**：

```
https://bos-studio.tech/jobcopilot/sse?token=<访问令牌>
```

> 各平台的坑（扣子不接受 IP、百炼自签名证书报 `MCP_SSL_ERROR`、Dify 必须关 DCR
> 并调大超时、千帆只吃 SSE…）见 `jobcopilot/docs/integrations/`。

## 7. 相关

- 部署脚本：`deploy/deploy.sh` 阶段 3.5 + 阶段 6 端点检查
- nginx：`deploy/nginx.conf` 的 `upstream sekb_jobcopilot_mcp` 与 `location /jobcopilot/`
- 内核：`jobcopilot/src/jobcopilot/mcp/{server,config,request_keys}.py`
- 隐私与数据流向：`jobcopilot/docs/integrations/PRIVACY.md`
