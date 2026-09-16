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

完整链路（列工具 / 调工具 / BYOK）用官方客户端跑：`scripts/verify_public_mcp.py`
（需 `JOBCOPILOT_HTTP_TOKEN` 与 `DEEPSEEK_API_KEY` 环境变量）。

## 5. 安全边界（改之前先读）

- 端口只绑 `127.0.0.1`，**不新开公网端口**，对外只有 443 经 nginx；
- 容器 `read_only: true` + `no-new-privileges` + 内存 1G 上限：`save_profile`
  在这个端点上**用不了是刻意的**——共享端点上的画像会串到别人身上，
  调用方应改用 `user_profile` 参数按请求传；
- **绝不**给这个容器配 `JOBCOPILOT_ALLOW_SOURCE_PATH=1`（公网端点上的任意文件读取）；
- `JOBCOPILOT_HTTP_TOKEN` 未设时内核**拒绝启动**并打印可操作提示（这是安全闸，不是配置遗漏）；
- 与 SEKB **共用同一份提示词**（`./prompt/job` 只读挂载），所以提示词热改对两个入口同时生效。

## 5.5 响应「为空/被截断」时，怎么界定问题在谁

> **限长相关的功能已按 owner 决定暂缓**（不做事后裁剪、暂不做精简版）。这里给出的是
> **判定步骤**：先确认是不是我们的问题，是平台的就不管；但必须能分得清。

按下面顺序查，能把边界钉死：

| 步骤 | 命令 / 做法 | 结论 |
|---|---|---|
| 1. 链路通不通 | 调 `self_check` 工具（**固定 <1KB 响应**） | 连它也收不到 → **链路问题**（地址/令牌/Host/网络），不是体积 |
| 2. 是不是我们的输出 | 服务器上 `docker logs sekb-jobcopilot-mcp --tail 50` 看有没有 `工具 X 返回 N 字节（JSON 合法，可被解析）` | 有这条 → **我们确实发出去了完整合法 JSON**；没有 → 请求没到我们这儿或工具没执行 |
| 3. 是不是我们超限 | 日志/返回里出现 `内核侧错误：… 超过内核自设上限` | 是**我们的问题**（输出过大），错误信息带确切字节数 |
| 4. 反代有没有截断 | 从**本机**直连容器（绕开 nginx）：`curl -s -X POST http://127.0.0.1:8765/jobcopilot/mcp …` 与经 `https://域名/…` 各一次，比字节数 | 直连正常、经 nginx 异常 → **反代问题**（`proxy_buffering` / 超时） |
| 5. 以上都正常但仍拿到空/截断 | — | 落在**平台侧**（其节点对响应的处理），按 owner 决定**先不管** |

两个已内置的诊断点（都在内核里）：

- **出站体检**：每次工具调用都会记录返回字节数并校验 JSON 合法性；无法序列化或超过
  8MB 自设上限时**显式报错**，且错误信息**明确写着「不是平台限制，是我们的输出过大」**
  —— 避免把我们的问题误判成平台限制。
- **`self_check` 工具**：固定小响应（<1KB），只回版本/提示词来源/传输/前缀。
  它是「体积」变量的对照组：**它能通、`analyze_*` 不能通 → 差异在体积**。

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

## 6.5 逐平台配置与验证清单（2026-09-16 实测基线）

### 你手上要填的值（本项目生产，已实测）

| 项 | 值 |
|---|---|
| Streamable HTTP URL | `https://bos-studio.tech/jobcopilot/mcp` |
| SSE URL（**只有千帆用**） | `https://bos-studio.tech/jobcopilot/sse?token=<访问令牌>` |
| 访问令牌 | 服务器 `.env.prod` 的 `JOBCOPILOT_HTTP_TOKEN`（48 字符）。**不要贴到聊天里**，取法：<br>`ssh -i ~/.ssh/sekb_tencent_key bo@49.232.42.91 'grep ^JOBCOPILOT_HTTP_TOKEN= /opt/self-evolving-kb/SelfEvolvingKnowledgeBase/.env.prod'` |
| BYOK（调用方自己的 LLM Key） | 放 `X-JobCopilot-Api-Key` 头；**服务端刻意不配 Key** |
| 工具数 | **7 个**：`analyze_job`、`analyze_jobs_batch`、`self_check`、`get_profile`、`save_profile`、`list_prompt_packs`、`sync_prompts` |
| Host 白名单 | `JOBCOPILOT_ALLOWED_HOSTS=bos-studio.tech`（不一致会 421） |
| 运行内核 | `e7592f7f15`（`/api/v1/health/` 的 `kernel.commit`，`prompt_source: local`） |

### 服务端实测基线（2026-09-16，配置平台前先确认这 5 条还成立）

| # | 检查 | 期望 |
|---|---|---|
| 1 | `POST /jobcopilot/mcp` 无令牌 | **401** |
| 2 | 带令牌、无会话 | **400** `Missing session ID` |
| 3 | `GET /jobcopilot/sse` + `Authorization` | `event: endpoint` → `data: /jobcopilot/sse/messages/?session_id=…`（**前缀 `/jobcopilot` 必须在**） |
| 4 | `GET /jobcopilot/sse?token=<正确令牌>`（千帆路径） | 同上；`?token=bogus` → **401** |
| 5 | 官方客户端全链路（列工具 / `list_prompt_packs` / 无 Key 报错 / 带 Key 出七段 / SSE 列工具） | 5 项全过：工具 7 个；`analyze_job` 带 BYOK **7/7 段** |

复现 1–4 见 §4；复现 5：`JOBCOPILOT_HTTP_TOKEN=… DEEPSEEK_API_KEY=… python scripts/verify_public_mcp.py`。

### 每个平台都先做这一步：调 `self_check`

它返回**固定 <1KB**，只回内核版本/传输/提示词来源。**它是「体积」变量的对照组**：

- `self_check` 也不通 → 问题在**链路/鉴权/Host/超时**（跟响应大小无关）；
- `self_check` 通、`analyze_*` 不通或返回空 → 差异在**体积**，按 §5.5 的四步区分是我们还是平台。

### 逐平台要点（细节见 `jobcopilot/docs/integrations/<平台>.md`）

| 平台 | 填什么 | 必须动的开关 | 绿了长什么样 | 红了先查 |
|---|---|---|---|---|
| **扣子 Coze** | `url=https://bos-studio.tech/jobcopilot/mcp`；`headers.Authorization=Bearer <令牌>`、`X-JobCopilot-Api-Key=<你的 LLM Key>` | Agent 设置 > 扩展能力 > MCP 里启用（**下一轮对话才生效**） | Agent 里能列出 7 个 `jobcopilot` 工具 | 用了 IP → 一定失败（**必须域名**）；工具没出现 → 是否只加了 MCP 没在 Agent 里启用 |
| **阿里百炼** | `type=streamableHttp`，`url=…/mcp`，自定义 header 同上 | 「MCP 管理 → 使用脚本部署 → **http**」（**别走插件页**） | MCP 服务状态可用 + 智能体里能选到工具 | `MCP_SSL_ERROR` = 证书链不受信；404/405 = 路径没写 `/mcp` |
| **百度千帆** | `url=https://bos-studio.tech/jobcopilot/sse?token=<令牌>` | 工作流 → 节点「工具引入 → **MCP Server**」 | SSE 连上、工具列表出现 | 千帆**没有 headers 字段**，令牌只能走查询串；CFC 节点 10 秒超时 → **批量基本不可用**，先验 `analyze_job` 单职位 |
| **火山 HiAgent / AgentKit** | `url=…/mcp`（域名，**不能私网 IP**）；`Headers` 放鉴权 | AgentKit 建 MCP 服务：`ProtocolType=MCP`、`BackendType=Domain`、`Path=/mcp`、出站 `ApiKeyLocation=HEADER` | 网关上 MCP 服务可用、工具可见 | AgentKit **不支持 SSE-only**（我们两条都有，无妨）；HiAgent 控制台文档缺失 → 先按 SDK 字段试 |
| **Dify** | 服务器 URL `…/mcp`；自定义请求头同上 | **关掉 DCR**；调大「请求超时 / SSE 读取超时」 | 工具 > MCP 里列出 7 个工具并能跑通一个 | 超时默认太小；返回超 ~68000 字符时 `tool_response` 变空（issue #18731）→ 减少单次职位数 |

### 会在验证时拦住你的三条硬限制（提前知情，别当成 bug）

1. **工作流节点 1 节点只能选 1 个工具**（百炼/千帆）→ 7 个工具要按需多节点，或改用智能体模式；
2. **批量分析耗时长**：千帆 CFC 节点约 10 秒超时基本不可用；Dify/扣子/BYOL 自建应用更合适。
   先验单职位 `analyze_job`，再验 `analyze_jobs_batch` 且**从小批量（3–5 个职位）开始**；
3. **返回体积**：Dify 社区实测 >~68000 字符会变空；千帆 API 节点硬限 1M。
   `analyze_jobs_batch` 的完整报告可能超 → 控制单次职位数。

## 7. 相关

- 部署脚本：`deploy/deploy.sh` 阶段 3.5 + 阶段 6 端点检查
- nginx：`deploy/nginx.conf` 的 `upstream sekb_jobcopilot_mcp` 与 `location /jobcopilot/`
- 内核：`jobcopilot/src/jobcopilot/mcp/{server,config,request_keys}.py`
- 隐私与数据流向：`jobcopilot/docs/integrations/PRIVACY.md`
