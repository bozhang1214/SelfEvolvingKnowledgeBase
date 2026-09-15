# 三平台 MCP / 自定义工具接入调研（阿里云百炼 · 百度千帆 · 火山引擎 HiAgent）

> **调研时间**：2026-09-15
> **调研对象**：一个用 Python 官方 `mcp` SDK 写的 MCP Server，**streamable HTTP** 传输，
> 端点 `HTTP POST http://<host>:<port>/mcp`，工具如 `analyze_job`、`analyze_jobs_batch`。
> **目标**：确认三家平台**当前**接入方式与"可直接落地的配置要点"。
>
> **证据标记约定**（全文通用）：
> - ✅ **查证过**：有官方文档/官方 API 原文可引，附链接。
> - ⚠️ **推测**：基于官方文档的合理推断，文档没有明说。
> - ❓ **未查到**：翻了官方文档与多个第三方源仍未找到；列出搜索关键词。
>
> ⚠️ **一句话结论**：**同一份 MCP Server 想同时适配三家，必须同时暴露 SSE 与 Streamable HTTP 两个端点**。
> 因为**千帆 AppBuilder 的「MCP Server 节点」官方 UI 文案明确只写 SSE**（全站 494 篇文档里 "streamable" 零命中），
> 而百炼走 Streamable HTTP、火山 AgentKit 也要求 Streamable HTTP。
> ⚠️ **唯一的例外**：百度智能云另一个独立产品 **AI 原生网关（AIGW）** 的表单里确实有
> **「MCP 协议类型：Streamable HTTP」+「直接代理」**模式，可直连原生 MCP 服务 —— 但它是不是能被
> **千帆 AppBuilder 消费**，官方文档没写（❓未确认）。详见下文 **2.6 节**。
> 详见最后一节。

---

## 0. 结论速览

| 维度 | 阿里云百炼 | 百度千帆 | 火山引擎 HiAgent |
|---|---|---|---|
| 支持 MCP | ✅ 支持（4 条路径） | ✅ 支持，但只在**工作流 MCP Server 节点**里；另有独立产品 **AIGW** 支持 Streamable HTTP | ✅ **支持**（官方开源 Go SDK `hibot` 证明）；控制台文档 ❓ 未公开 |
| 传输 | stdio(npx/uvx) + **SSE** + **Streamable HTTP** | 工作流 MCP 节点：**仅 SSE**（❓无 streamable HTTP）；**AIGW：Streamable HTTP ✅** | HiAgent：**Streamable HTTP + Stdio**（SDK 原文）；AgentKit：**仅 Streamable HTTP（≥2025-03-26），明确不支持 stdio/SSE-only** ✅ |
| URL 路径 | ✅ **必须匹配**：`streamableHttp`↔`POST /mcp`，`sse`↔`GET /sse` | ❓ 文档只写 "your server url"，未强制 | AgentKit ✅ **路径可自定义**（示例值就是 `/mcp`） |
| 自定义 Header 鉴权 | ✅ 支持 `headers` 键值对（键名可自定义） | ⚠️ 无 headers 字段，官方示例**一律把凭据放 URL query** | HiAgent ✅ `Headers map[string]string`（任意名）；AgentKit ✅ Header 名可自定义；DataAgent ✅ 有「Header 鉴权」键值对 |
| 公网 HTTPS | ⚠️ 需公网可达 + 受信任证书（自签名报 `MCP_SSL_ERROR`） | ⚠️ 推测需公网可达（未查到明文） | ⚠️ 推测**不强制**（SDK 测试用 `http://mcp.local/mcp`）；AgentKit 后端 http 也行（`TlsMode=DISABLE`） |
| 非 MCP 方案 | 自定义插件（表单）/ AI 网关 HTTP 转 MCP | API 节点 / 函数计算 CFC 节点 | HiAgent 插件(Plugin)→工具(Tool) / Skill / 工作流；AgentKit HTTP 转 MCP / 部署 MCP 服务 |
| 数据合规一句话 | 「绝不会将您的数据用于模型训练」，AES-256，但**会存储调用数据** | 「不用于训练、不与第三方共享」，业务数据归你，**境内存储**；但**无保密义务** | HiAgent ❓无专属公开条款；同生态用[方舟专用条款](https://www.volcengine.com/docs/82379/1104498) 3.7.7「未经单独同意不用于训练」/ 3.7.9「数据将被发送至插件内处理」；火山 [DPA](https://www.volcengine.com/docs/6391/67493) 原则上中国大陆存储 |

---

## 1. 阿里云百炼（Model Studio / Bailian）

### 1.1 是否支持 MCP？支持哪种传输？入口在哪？

✅ **查证过**：支持，且是三条云厂商里文档化最完整的。官方把自定义 MCP 分成**三种部署方式**
（[自定义 MCP 服务](https://help.aliyun.com/zh/model-studio/custom-mcp)）：

| 方式 | 面向 | 说明 |
|---|---|---|
| **使用脚本部署** | 代码 | 你自己的 MCP 代码。stdio 走 **npx / uvx**（由百炼托管到函数计算 FC）；远程走 **http**（即 `sse` / `streamableHttp`） |
| **从 AI 网关导入** | 已有 REST API | 在阿里云 AI 网关把 RESTful API 升级为 MCP，再导入百炼 |
| **从阿里云 OpenAPI 导入** | 阿里云生态 | 把阿里云官方 OpenAPI 发布成 MCP 再导入 |

另有「官方 MCP 服务」（云部署，一键开通，见[官方 MCP 服务](https://help.aliyun.com/zh/model-studio/official-and-third-party-mcp)）。

**传输方式** ✅：`stdio`（npx/uvx）、`sse`、`streamableHttp`。
官方配置模板原文：

```json
{
  "mcpServers": {
    "本地 MCP 服务": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@your_acc_name/your_pkg_name"],
      "env": { "YOUR_ENV_KEY": "YOUR_ENV_VALUE" }
    },
    "远程 MCP 服务": {
      "type": "sse/streamableHttp",
      "url": "https://your-mcp-server/sse"
    }
  }
}
```

**控制台入口** ✅：
- **MCP 管理**（创建/管理自定义 MCP）：`https://bailian.console.aliyun.com/?tab=app#/mcp-manage`
  → 「创建 MCP 服务」→ 选「使用脚本部署 / AI 网关 / 阿里云 OpenAPI」
- **MCP 广场**（官方 MCP，一键开通）：`https://bailian.console.aliyun.com/?tab=mcp#/mcp-market`
- 在**智能体应用**编排页的 **MCP** 区块里 `+` 添加；在**工作流应用**里拖入 **MCP 节点**。

### 1.2 MCP 配置字段 / URL 路径 / Header / 网络

**（a）URL 要填到什么路径？→ 必须与 `type` 严格匹配，`/mcp` 是正确答案** ✅

[MCP 常见问题](https://help.aliyun.com/zh/model-studio/mcp-faq) 里两处错误码排查都写了同一句话（原文）：

> 确认配置中 `type` 与端点路径匹配：`"sse"` 对应 **GET `/sse`**，`"streamableHttp"` 对应 **POST `/mcp`**。

也就是说：你的服务是 `POST /mcp`，那么配置里 **`"type": "streamableHttp"` + `"url": "https://<host>/mcp"`**。
填错会分别报 `11200059 MCP_SERVER_HTTP_NOT_FOUND`（404）或 `11200058 MCP_SERVER_HTTP_METHOD_NOT_ALLOWED`（405）。
👉 **`/mcp` 不是可选的，它是百炼对 streamableHttp 的约定路径。**

**（b）自定义 HTTP Header 做鉴权？→ 支持，且键名可自定义** ✅（配置层面）+ ✅（实测）

官方配置模板里没有展开 `headers`，但：
- [MCP 常见问题](https://help.aliyun.com/zh/model-studio/mcp-faq) 的 `11200049 MCP_SERVER_HTTP_UNAUTHORIZED`（HTTP 401）排查里明确写：
  「在 MCP 服务中正确添加鉴权信息，例如 **Headers 中的 `Authorization` 信息**」→ 说明 header 是可配的。
- 第三方实测（CSDN，2026-08-07 发布）给出了能在百炼跑通的完整配置，**含 `headers`**：
  [阿里云百炼 MCP 部署](https://blog.csdn.net/scabbards_/article/details/159394955)

  ```json
  {
    "mcpServers": {
      "findata-mcp": {
        "url": "https://cloud-findxxxxxxx/mcp/",
        "type": "streamableHttp",
        "headers": { "Authorization": "Bearer 1pzxxxxxxx" }
      }
    }
  }
  ```
  作者备注：填进去后「平台可以自动检测你有的工具，无需手动设计参数」。

**（c）是否要求 header 里放平台自己的鉴权信息？→ 不需要（方向是反的）** ✅
百炼**调用**你的自定义 MCP 时，`headers` 里放的是**你自己服务**的凭据，百炼不会把它的 API Key 注入进来。
反过来，如果你用百炼**托管**的 MCP 供外部调用，才需要平台的 key：
外部调用端点是 `https://dashscope.aliyuncs.com/api/v1/mcps/<mcp-id>/mcp`，header 是
`Authorization: Bearer <DASHSCOPE_API_KEY>`（见[外部调用](https://help.aliyun.com/zh/model-studio/mcp-external-calls)）。

**（d）公网 HTTPS / 内网 VPC / 出网白名单**

| 问题 | 结论 |
|---|---|
| 必须公网 HTTPS？ | ⚠️ **推测必须**：远程 MCP 由百炼侧发起请求，URL 必须公网可达；有专门的 `11200048 MCP_SSL_ERROR`（TLS/证书校验失败，**含自签名证书不被信任**）→ 用受信任 CA 证书的 HTTPS 最稳 |
| 支持内网/VPC 地址？ | ❌ **不支持直接把内网地址填进来**。FC 托管的 MCP **无固定出口公网 IP**，要访问你的内网资源得自己配 **FC 的 IP 白名单 / VPC 打通**（[FAQ 第 6 条](https://help.aliyun.com/zh/model-studio/mcp-faq)）；「百炼 MCP 服务能访问本地数据库吗？**暂不支持**，目前不能访问用户本地资源」 |
| 出网白名单？ | 反向的：**百炼不提供固定出口 IP**，所以如果要在你这边做白名单，你得按 FC 的指引配 VPC/IP 白名单，而不是加一个百炼的固定 IP |

**（e）其它硬约束** ✅
- 「并非所有 MCP 服务都支持 npx/uvx/http 方式部署」，缺配置代码就部署不了。
- 部署完成后**只能改服务名称和描述**；要改部署方式/地域/安装方式/配置，必须**先停止再重新部署**。
- 一个智能体**最多同时添加 5 个** MCP 服务。
- **工作流里每个 MCP 节点只能用 1 个工具**（需手动指定输入参数）。
- MCP **不能**在直接调用千问 API 时接入，只能用于**智能体**或**工作流**应用。

### 1.3 不走 MCP 时的替代方式

✅ **查证过**：[自定义插件](https://help.aliyun.com/zh/model-studio/custom-plug-ins)（表单式，不是文件导入）：
1. 建插件：填**插件 URL**（只到域名）+ 是否鉴权 + **Header 列表**；
2. 建工具：**工具路径**（必须以 `/` 开头，拼在插件 URL 后）、**请求方法**（GET/POST）、
   **提交方式**（`application/json` 或 `application/x-www-form-urlencoded`）、入参/出参（出参全部必填）；
3. 在线调试 → **发布**（只有已发布且启用的工具能被调用）。
4. 也可把插件 **「发布为 MCP 服务」**，再在智能体 MCP 区块里添加。

鉴权 ✅：**位置**支持 Header 或 Query；放 Header 时**参数名默认是 `Authorization`**；
**Type** 支持 `basic`（不加前缀）/ `bearer`（加 `Bearer `）/ `appcode`（加 `APPCODE`）；
放 Query 时可自定义参数名（如 `api_key`）。

⚠️ **重要坑（第三方实测）**：**插件通道不能直接挂一个已写好的 MCP Server**。
[CSDN 实测](https://blog.csdn.net/scabbards_/article/details/159394955) 原文：
> 「**插件**不支持已经写好的 mcp，它是把你的 HTTP 服务包成 mcp，否则调用会出错，所以我们选择了**脚本部署**」。

👉 所以：**你要接自己的 MCP Server，必须走「MCP 管理 → 创建 MCP 服务 → 使用脚本部署 → http」**，不要走插件。

### 1.4 数据出境 / 隐私（可引用的官方原文）

✅ **查证过**：[合规资质与隐私说明](https://help.aliyun.com/zh/model-studio/privacy-notice)
- 「阿里云严格保护数据隐私，**绝不会将您的数据用于模型训练**。」
- 「您在构建应用或训练大模型过程中传输的数据都会经过 **AES-256** 加密。」
- 「根据相关法律法规要求，阿里云百炼**将存储模型与应用调用时产生的数据**。」
- 详细条款见[《阿里云百炼服务协议》](https://terms.alicdn.com/legal-agreement/terms/common_platform_service/20230728213935489/20230728213935489.html)
  的数据处理/隐私/安全条款；平台以无保留意见通过 SOC 2 审计。

**⚠️ 工具调用时 JD 数据经过哪些环节（据上述文档推断的链路）**：

```
你的用户 → 百炼智能体/工作流（阿里云境内外未知具体地域，控制台可选地域）
        → 通义千问大模型把 query 解析成工具入参（JD 数据进入模型上下文）
        → 百炼 MCP 网关 → 公网 → 你的 MCP Server (POST /mcp)
        → 你的后端 / 数据源
        → 工具返回结果 → 回到模型上下文（会增加 token）→ 生成回答
```

要点：**JD 数据会离开你的内网、经公网到达百炼侧，并作为模型上下文被处理；且百炼官方声明会存储调用数据**。
写合规提示时必须体现这两点。若要把暴露面压到最小，可考虑把 MCP Server 放到阿里云 FC + VPC 内连数据源。

### 1.5 OpenAPI 方式接入的具体限制

| 限制项 | 结论 |
|---|---|
| 控制台能否上传 OpenAPI/Swagger 文件（自定义插件） | ❌ **不能**（截至 2026-09 的文档）。[自定义插件](https://help.aliyun.com/zh/model-studio/custom-plug-ins) 全流程是表单配置 |
| OpenAPI 版本 / `operationId` / `oneOf` | ❓ **未查到**百炼侧的量化规定。搜索关键词：`dashscope api/v1/plugins open_api_schema operationId`、`阿里云百炼 插件 超时 限制 响应大小 OpenAPI 3.0 operationId`（曾存在的 `plugin-api` 文档页现在返回 404） |
| 请求体 Content-Type | ✅ 仅 `application/json` 或 `application/x-www-form-urlencoded`（提交方式二选一） |
| 已知参数限制 | ✅ 工具路径必须以 `/` 开头；**GET 下入参不支持 Object 类型**；Object 子属性不能为空；输入/输出参数**必须有描述**（否则报 `130040`）；工具名称超 20 字符会导致发布失败 |
| 响应大小 / 超时 | ❓ **未查到**量化数值（百炼 MCP 侧只有错误码 `11200046 MCP_REQUEST_TIMEOUT` / `11200045 MCP_CONNECTION_TIMEOUT` / `11200057 MCP_INIT_TIMEOUT`，均未给秒数） |
| **上传 OpenAPI 文件的地方在哪** | ✅ 在 **AI 网关**：[MCP 网关管理](https://help.aliyun.com/zh/api-gateway/ai-gateway/user-guide/mcp-gateway-management) 明确支持「**方式一：基于 Swagger 文件（推荐）**：从本地上传 OpenAPI 文件或粘贴 API 定义」+「方式二：自定义 YAML」，字段规范见 [HTTP to MCP 配置字段参考](https://help.aliyun.com/zh/api-gateway/ai-gateway/user-guide/http-to-mcp-field-configurations)。后端认证支持 Basic / Bearer / API Key(Header 或 Query) |

---

## 2. 百度智能云千帆（Qianfan / AppBuilder）

### 2.1 是否支持 MCP？支持哪种传输？入口在哪？

✅ **查证过**：支持，但**接入点是工作流里的一个节点**，不是应用级的 MCP 服务注册。

官方文档 [MCP Server节点](https://cloud.baidu.com/doc/qianfan/s/cmhj482ha)（工作流组件版）与
[MCP Server节点](https://cloud.baidu.com/doc/qianfan/s/Bmh4stwja)（工作流 Agent 版），
文档元数据 `date: 2025-12-30`，正文原文：

> 「工作流组件/工作流Agent支持通过工作流编排的形式还原业务流程……**MCP Server 节点** 支持用户在工作流内引入MCP，串联到业务流程中使用。」

**控制台入口（具体到节点）** ✅：
1. 页面左上方【创建 - 工作流Agent】/【创建 - 组件】
2. 左侧节点栏 **工具引入 → `MCP Server`**
3. 打开「MCP Server」节点 → 点【**配置 MCP Server**】
4. 填写 **JSON 地址及鉴权** → 点【**连接到 MCP Server**】→ 等校验通过
5. 校验通过后**自动显示请求地址并展示工具列表** → **选择工具（仅支持单选）**
6. 配置工具输入参数 + 输出格式 → 发送测试 → 点【**解析到输出参数**】

**另有一条「零配置」入口** ✅：[控制台使用说明](https://cloud.baidu.com/doc/qianfan/s/zmh4stqex) 原文
「**工作流使用 MCP-SSE 组件**」——在**自主规划Agent**的【能力扩展-组件】里筛选并添加 **MCP-SSE 组件**，
「前往 Server 提供方网站，获取鉴权参数并输入后，即可完成添加」。
⚠️ 注意：这条是**从 MCP 广场里挑平台内建的服务**，「前往 Server 提供方网站获取鉴权参数」指的也是广场服务的官方参数，
**不是"填任意自建 URL"**。

**「MCP 广场」能不能加任意自建 MCP URL？→ 判定为不能** ✅
同一文档 §3.3「**上架 MCP Server**」给出的两条路径是：
「将**组件**上架到 MCP 广场（流程同工作流组件上架）」「将**云函数 MCP Server** 上架到 MCP 广场」。
即广场是 **消费 + 上架（仅限平台内建的组件 / 云函数 MCP）**，**任意第三方 URL 只能填进工作流 MCP Server 节点**。

**传输方式：✅ 官方明确只写 SSE。** 节点提示气泡原文（我从官方截图里读出来的）：

> 「MCP Server — 支持接入 **SSE** 的 MCP Server，该服务能在云端平台中直接使用」

[控制台使用说明](https://cloud.baidu.com/doc/qianfan/s/zmh4stqex) 亦原文：「点击云部署，输入 API Key，**一键部署为 SSE 传输格式的 MCP 服务**」。

截图：[节点列表气泡](https://bce.bdstatic.com/doc/ai-cloud-share/AppBuilder/22_cd00118.png)、
[配置面板](https://bce.bdstatic.com/doc/ai-cloud-share/AppBuilder/23_d8b1b8d.png)、
[编辑 MCP Server 弹窗](https://bce.bdstatic.com/doc/ai-cloud-share/AppBuilder/24_4406d0e.png)。

- `stdio`：❌ 无（千帆不做 stdio 托管）。
- `streamable HTTP`：❌ **判定为"千帆 AppBuilder 侧没有"**。证据：
  1. 全站核查 —— 把千帆文档库 `/doc/qianfan/s/*` 的**全部 494 篇文档**抓下来 grep，
     **`streamable` 零命中**（大小写各种写法都试过）。
  2. 配置弹窗**没有传输方式下拉**，只有 `url` 一个字段。
  3. 控制台文档原文还有「一键部署为 **SSE 传输格式**的 MCP 服务」「工作流使用 **MCP-SSE 组件**」
     （见 [MCP 广场相关页](https://cloud.baidu.com/doc/qianfan/s/zmh4stqex)）。
  - 搜索关键词：`千帆 MCP Server节点 SSE streamable http`、`qianfan MCP streamable http`、`千帆 AppBuilder MCP 接入 自定义`

> ⚠️ **这对你的服务是最大的风险点**：你现在只有 `POST /mcp`（streamable HTTP），
> **按官方文档接不进千帆 AppBuilder 的工作流 MCP 节点**。
> 必须再暴露一个 SSE 端点（`GET /sse` + message 端点）。
> 👉 但请先看下文 **2.6 节**：百度另一个产品 AIGW 是支持 Streamable HTTP 的。

### 2.2 MCP 配置字段

✅ **查证过**：千帆不需要填表，而是**在一个 JSON 编辑器里填一段配置**。官方截图里的模板原文（可逐字复刻）：

```json
{
  "mcpServers": {
    "serverName": {
      "url": "your server url"
    }
  }
}
```

编辑器下方提示：「请编写合法的请求地址，连接成功后可获取到 MCP Server 提供的工具列表」，
按钮是【连接到 MCP Server】，面板上还有折叠区「选择工具」「配置参数」「响应结果」。

逐个回答：

| 问题 | 结论 |
|---|---|
| URL 填到什么路径？是否必须 `/mcp`？ | ✅ **要填"能完成 MCP 握手的完整端点路径"，但" `/mcp`"这个规定在千帆文档里不存在**。千帆官方示例全部把传输端点写全：百度地图 `https://mcp.map.baidu.com/sse?ak=您的AK`、百度AI搜索 `http://appbuilder.baidu.com/v2/ai_search/mcp/sse?api_key=Bearer+...`、自建组件 `http://appbuilder.baidu.com/v2/mcp/components/<id>/...`。→ 因为 UI 明确是 SSE，⚠️**应填 `/sse`**，不是 `/mcp` |
| 是否支持自定义 HTTP header 鉴权？ | ❌ **未查到支持**。JSON 模板里**只有 `url`，没有 `headers`/`type` 字段**，弹窗也没有 header 输入框 |
| 固定格式还是可自定义 header 名？ | ❌ 同上，**没有 header 这条路** |
| 那鉴权怎么写？ | ✅ **官方一致的做法是塞进 URL query**：`?ak=`（百度地图）、`?api_key=`（百度AI搜索）。⚠️**推测这就是千帆唯一可行方式**，对你的 token 就是 `?token=<TOKEN>` |
| 是否要求放平台自己的鉴权信息？ | ⚠️ **分两种情况**：① 填**自建** MCP 时，鉴权参数是你自己的（千帆自建组件弹出的配置框里写的是"**鉴权参数 = 点击获取 **Appbuilder API Key****" —— 见[截图](https://bce.bdstatic.com/doc/ai-cloud-share/AppBuilder/4_357836a.png)，即平台内建服务用平台凭据）；② 千帆自己的 OpenAPI 统一是 `Authorization: Bearer bce-v3/ALTAK-xxx/xxx`（[通用说明](https://cloud.baidu.com/doc/qianfan/s/fmh4sutq6)、[认证鉴权](https://cloud.baidu.com/doc/qianfan/s/Kmh4sutww)），那是**你调千帆**时的头，不是千帆调你时的头 |
| 是否强制公网 HTTPS？ | ❓ **未查到**明文要求（千帆 494 页无相关条款）。⚠️**推测平台在百度云侧出站调用，URL 必须可达**；千帆自有端点里出现过 `http://`，所以"强制 HTTPS"没有依据 —— 但**建议还是上 HTTPS，别赌** |
| 支持内网 / VPC？ | ❓ **未查到**（千帆 AppBuilder 侧无任何内网/VPC 说明）。✅ 但百度 **AIGW** 支持公网+VPC 双入口，见 2.6 |
| 出网白名单？ | ❓ **未查到**（没有找到千帆的出口 IP 清单或白名单机制）。✅ AIGW 有 IP 白名单，见 2.6 |

👉 **落地建议**：**鉴权就走 URL query（`?token=xxx`）** —— 这是千帆所有官方 MCP 示例的做法；
同时**也兼容 `Authorization: Bearer`**（万一以后支持 header，或你走 AIGW）。
在千帆控制台用【连接到 MCP Server】当场验证。

**⚠️ 补充：千帆代码态 SDK 也只实现到 SSE** ✅
[appbuilder-python 的 `mcp_server/client.py`](https://github.com/baidubce/app-builder/blob/master/python/mcp_server/client.py)
里的 `MCPClient` **只实现了 stdio + SSE 两种 client，没有 streamable HTTP client** —— 与上面"千帆侧 = SSE"的判断一致。

### 2.3 不走 MCP 时的替代方式

✅ **查证过**，千帆有两条成熟路径：

**（A）API 节点 —— 把已有 HTTP 服务注册为组件**
[MCP Server节点](https://cloud.baidu.com/doc/qianfan/s/cmhj482ha) 的兄弟页 [API 节点](https://cloud.baidu.com/doc/qianfan/s/Emh4su361) 原文：
- 「API节点是基础节点类型之一，您可以通过该节点，将已有服务注册为**组件**」
- 「平台支持接入遵守 RESTful 架构规范并采用**标准认证机制**的 API」
- 「可以**手动逐步配置 API 基本信息或通过 curl 命令一键导入**」

**（B）函数计算 CFC 节点 —— 跑你自己的代码**
[函数计算CFC节点](https://cloud.baidu.com/doc/qianfan/s/Hmh4su41w) 原文：
- 「该节点用于配置百度云函数计算（CFC）服务，以调用 CFC 中自定义的函数服务，从而支持集成更多算法库，并支持多种编程语言」
- **超时配置**：「该超时时间为连接 CFC 服务的超时时间，对于流式接口，该超时时间为接收完首帧数据的最长等待时间。**通常设置为 10 秒**」
- 支持流式输出

**（C）组件也可反向暴露为 MCP Server**（注意方向）
[组件兼容MCP协议](https://cloud.baidu.com/doc/qianfan-docs/s/Hm984ai00)（AppBuilder SDK）：
AB 提供 `MCPComponentServer`，可把 AB 组件**转成 MCP Server 给 Cursor/Cherry Studio 用**。
该文（2025-04-18）路线图里「使用**远程MCP SSE组件**」是当时的远程接入方式 —— 再次印证**千帆侧是 SSE**。

**需要什么格式的文件？** ✅ 千帆的控制台流程是**节点配置 + curl 一键导入**，
**没有找到"上传 OpenAPI/Swagger 文件"的入口**（与百炼 AI 网关不同）。❓ 搜索关键词：`千帆 自定义组件 OpenAPI 导入`、`千帆 AppBuilder 自定义插件 OpenAPI schema`。

**⛔ 重要：老的「插件编排（OpenAPI / ai-plugin.json）上传」这条路已经死了** ✅
[千帆 ModelBuilder 插件编排服务下线通告](https://ai.baidu.com/ai-doc/WENXINWORKSHOP/nlu9ur4e5)（更新于 2025-04-11）原文：
> 「我们决定将千帆 ModelBuilder **插件编排**能力迁移到千帆 AppBuilder 产品内……
> 请注意，插件编排应用将在 **7月31日正式停止服务**……在此之前，您的插件服务仍可使用，但**无法新建插件**。」

迁移说明见[千帆大模型平台插件编排&知识库迁移至AppBuilder指导说明](https://qianfan.cloud.baidu.com/qianfandev/topic/271014)（2024.06.05）。
👉 所以"用 OpenAPI 文件导入自定义插件"**不能作为千帆的落地方案**，现行官方方式只有
**工作流组件 → API 节点（手动填 / curl 导入）** 或 **函数计算 CFC 节点**。

### 2.4 数据出境 / 隐私（可引用的官方原文）

✅ **查证过**，两个可引用来源：

**（1）[百度千帆大模型服务及Agent开发平台用户协议](https://cloud.baidu.com/doc/qianfan/s/Cmh4stmpy)**（生效时间 **2026-01-09**）
第 5.1(6) 条原文（**这是最强的一条，直接写"不训练、不共享"**）：
> 「您在使用千帆平台进行模型推理服务时，其产生的**输入、输出数据**，以及上传到平台的各类知识文档、数据集，为您的**用户数据**，
> 仅限您账号下在本服务中使用，除执行您的服务要求外，我们不进行任何未获授权的使用及披露。
> 为免疑义，**我们不会使用您的输入和模型输出数据、上传的知识文档、数据集进行训练，也不会将您的数据集与任何第三方共享**。」

同口径另见[千帆 AppBuilder 专项约定](https://cloud.baidu.com/doc/qianfan/s/emh4stmvj) 2.4 / 2.5。

**⚠️ 但同一份协议里有三个必须写进合规提示的"但是"** ✅（原文，用户协议 12/13 章）：
1. **授权例外**：你同意授权平台将相关数据用于「**服务性能优化、系统故障排查及风险控制管理**」。
2. **无保密义务**：「**您不得向本服务提供保密信息**……我们对您的任何内容**没有任何保密义务**。」
   → **意味着不要把保密级 JD / 客户数据直接喂平台。**
3. **第三方组件按第三方协议**：使用第三方组件/第三方 API 时，**按其各自协议处理你的数据**，冲突时第三方协议优先。
   → 你的自建 MCP 就属于这一类：**数据一旦出站到你的服务器，你成为独立的数据处理者**。

**（2）[百度智能云隐私政策](https://cloud.baidu.com/doc/Agreements/s/Plr0fi68q)**（页面元数据 `date: 2026-04-23`）
- **第 8 条「您的业务数据」**：「您通过百度智能云提供的服务，加工、存储、上传、下载、分发以及通过其他方式处理的数据，均为您的用户业务数据，**您完全拥有您的业务数据**。」
  「百度智能云作为中立的技术服务提供者，会严格执行您的委托和指示，除……外，**我们不对您的业务数据进行任何非授权的访问、使用或披露**。」
  「除非法律法规另有规定或依据服务规则约定，**我们不会访问、存储您的业务数据**，亦不对您的数据存储工作或结果承担任何责任。」
- **4.2 保存地域**：「原则上，我们在**中华人民共和国境内**收集和产生的个人信息，将存储在中华人民共和国境内。」
- **跨境传输**：需取得**单独同意**，并按国家网信部门规定订立标准合同 / 组织安全自评估 / 通过安全评估，采取加密、去标识化等措施。
- **保存期限**：「将在提供服务所需的最短期限内保存您的个人信息」，超期删除或匿名化。

**（3）⚠️ 对你这个场景最关键的一条：平台把"最终用户同意"的责任全部压给你**
用户协议里明确：
> 「就本服务而言，我们与非注册于本平台的**您的最终用户没有直接关系。我们不对您如何处理最终用户的数据及个人信息负责**。」
> 「对于您使用本服务和对 AI 原生应用的任何操作行为，您应全权责任，并承担**通知并获得最终用户同意**的法律义务。在收集或使用任何最终用户信息之前，您必须提供明确的通知并获得必要的同意……必须制定并向最终用户披露您 AI 原生应用适用的**隐私政策**……并取得您最终用户的恰当同意。」
> 「您创建的 AI 原生应用**不应面向 14 岁以下的儿童**。」

👉 **JD（职位描述）+ 招聘场景**：JD 本身通常不含个人信息，但一旦带上**招聘方联系人、薪资、或候选人相关字段**，
就落入"最终用户个人信息"范畴；而 `analyze_jobs_batch` 的入参会把**整批 JD 数据**送进大模型上下文。
写合规提示时务必体现：**告知义务与同意取得在你（开发者）这一侧，平台明确不承担**。

**⚠️ 工具调用时 JD 数据经过哪些环节**：
按上述条款，**业务数据本身归你、平台声明不用于训练也不与第三方共享**（措辞比百炼更强），但链路上 JD 数据仍然：
```
你的用户 → 千帆工作流（云端）→ 大模型节点/前序节点产出工具入参 → 千帆出网
        → 你的 MCP Server / API → 你的后端 → 结果回到节点输出参数 → 大模型生成回答
```
注意千帆的 MCP 节点**入参需由前序节点（通常是大模型节点）显式构造**，所以 JD 数据会**先进大模型上下文**再出网。

### 2.5 OpenAPI 方式接入的具体限制

✅ **查证过**（以 [API 节点](https://cloud.baidu.com/doc/qianfan/s/Emh4su361) 为准）：

| 限制项 | 官方原文 / 结论 |
|---|---|
| 规范要求 | 「接口设计符合 **OpenAPI 规范**」「需遵循 RESTful 架构规范」「使用无状态的请求模型并通过 HTTP 标准方法（GET、POST、PUT、DELETE）」「使用标准的认证机制」 |
| **参数层级** | 「当前接口参数**层级不支持超过 10 层**」 |
| **响应大小** | 「**API 接口返回内容大小不能超过 1M**」 |
| 响应内容类型 | 支持 **JSON、XML、HTML、Plain Text、YAML、CSV**；其中 JSON/XML/YAML 可被解析为 JSON 输出，其余以整个 string 输出 |
| 请求体是否必须 `application/json` | ❓ 未查到明文要求（文档只说 RESTful/OpenAPI 规范，可用 curl 导入） |
| OpenAPI 版本（3.0 / 3.1） | ❓ 千帆侧**未查到**；✅ 但百度 **AIGW** 侧明说「**Swagger 文档建议使用 OpenAPI 3.0 版本**」（见 2.6） |
| `operationId` 命名规则 | ❓ **未查到** |
| 是否允许 `oneOf` / `anyOf` | ❓ **未查到**；但「参数层级 ≤ 10 层」意味着**深层嵌套组合类型要避免** |
| 超时 | ✅ **函数计算 CFC 节点**的文档给了量化值：「通常设置为 **10 秒**」（连接 CFC 的超时；流式接口为首帧最长等待）。API 节点本身的超时 ❓未查到 |
| 流式 | ✅ 支持，需在配置处显式勾选「流式返回」 |
| 响应参数 | ✅ 可**不配置**响应参数，直接「根据 API 请求结果的返回信息**自动解析**输出参数」 |
| 组件命名（影响大模型能否调用） | ✅ [创建组件](https://cloud.baidu.com/doc/qianfan/s/ymh4su330)：「**英文名称**将用于被大模型 function call 识别及调用……大模型会根据**英文名称及组件描述**来识别是否调用该组件」 |
| 文件导入 | ⛔ 老的"OpenAPI/ai-plugin.json 文件导入"随**插件编排下线**已不可用（见 2.3） |

### 2.6 🔴 重要补充：百度 AI 原生网关（AIGW）—— **官方支持 MCP over Streamable HTTP**

这是本次调研挖到的**最有价值的新线索**，主文档（千帆那节）里完全没有提到。

✅ **查证过**：[产品介绍 - AI原生网关 AIGW](https://cloud.baidu.com/doc/AIGW/s/Ymj2hu93w)
- 「AI 原生网关（AI Gateway，**AIGW**）是百度智能云专为云原生 Kubernetes 环境及大规模 AI 应用场景打造的新一代流量管理与调度基础设施」
- 「**北京时间 2026 年 1 月 1 日 00:00:00 正式开启公测**，公测期间免费」
- 核心概念「MCP 服务」原文：
  > 「支持两种接入模式：1. **HTTP 转 MCP 模式**：网关将后端现有的 HTTP 服务（基于 OpenAPI 定义）自动转换为 MCP 协议；
  > 2. **直接代理模式**：**直接代理后端已经实现了 MCP 标准的原生服务**。」

✅ **查证过**：[创建 MCP 服务 - AIGW](https://cloud.baidu.com/doc/AIGW/s/cmo2dtigr)
- 入口：左侧边栏【**AI 服务**】→【**MCP 服务**】→【创建 MCP 服务】（前置：先建【后端服务】）
- 「目前提供两种 MCP 服务类型：**HTTP 转 MCP** / **直接代理**」；可配置**访问路径**、选择**后端服务**；
  「如果对这个路由有**消费者认证**的需求可以开启」
- HTTP 转 MCP 的工具配置：「现在提供两种配置方式：**基于 Swagger 文档** / **自定义 YAML**。
  注意，**Swagger 文档建议使用 OpenAPI 3.0 版本的格式**」
- 安全能力（产品介绍）：「**API Key 消费者认证、IP 白名单、外部认证插件**」

✅ **查证过（控制台截图，我逐字读出的表单字段）**：
`MCP 服务名称` / `访问域名`（不配置 | 自定义域名）/ **`MCP 服务类型`（HTTP 转 MCP | 直接代理）** /
**`MCP 协议类型`（显示为 `Streamable HTTP`）** / `访问路径`（提示："对外暴露的路径。例如：/mcp-servers/服务名称"）/
`后端服务`（服务来源 / 服务名称 / 端口 / 服务协议）。
- 创建页：[image_1b9146e.png](https://bce.bdstatic.com/doc/bce-doc/CSM/image_1b9146e.png)
- 详情页：[image_7cfc268.png](https://bce.bdstatic.com/doc/bce-doc/CSM/image_7cfc268.png)（显示「MCP 协议类型：`Streamable HTTP`」）

✅ **AIGW 的鉴权可自定义 header 名（查证过）**：[HTTP 转 MCP 配置说明](https://cloud.baidu.com/doc/AIGW/s/Jmk6ihy1t) 的 YAML 原文片段：
```yaml
server:
  name: YOUR-MCP-SERVER-NAME
  type: rest
  securitySchemes:
  - id: ApiKeyAuth
    defaultCredential: '123456'
    type: apiKey
    in: header            # 或 query
    name: X-API-Key       # ← header 名可自定义
  - id: BasicAuth
    defaultCredential: admin:admin123
    type: http
    scheme: basic
  - id: BearerAuth
    defaultCredential: asdf
    type: http
    scheme: bearer
```
参数位置支持 `query / path / header / cookie / body`；类型支持 `string / number / integer / boolean / array / object`；
`body` / `argsToJsonBody` / `argsToUrlParam` / `argsToFormBody` 四者互斥；
另有 `defaultUpstreamSecurity` / `defaultDownstreamSecurity` 做服务器级默认认证。

✅ **AIGW 的网络与超时（查证过）**：
- **公网 + VPC 内网双入口**；`访问域名` 可选「不配置（默认匹配所有域名 `*`，通过网关 IP / 默认域名访问）」或「自定义域名」；
  自定义域名 **HTTP 与 HTTPS 同时支持**，HTTPS 需绑 SSL 证书
- **IP 白名单**：仅白名单（无黑名单）、**网关全局生效**（对实例下所有模型推理服务与 MCP 服务统一生效）、
  ⚠️ **仅对内网/VPC 入口生效，公网入口默认放行**（要让公网也生效需提工单），每实例仅 1 条规则
  —— [设置网关 IP 白名单](https://cloud.baidu.com/doc/AIGW/s/mmou0vvn0)
- **超时策略 1–3600 秒，默认 60 秒** —— 但该文档挂在「**模型推理服务**」下，❓**是否同样作用于 MCP 服务未查到**
  —— [配置超时策略](https://cloud.baidu.com/doc/AIGW/s/amots3gc9)
- AIGW 的 OpenAPI 侧限制：**Swagger 文档建议 OpenAPI 3.0**；粘贴 Swagger → 【解析 Swagger 并生成】→
  转成 YAML（只读解析结果 + 可编辑的最终 YAML，可能要手工微调）
- 相关页：[功能发布记录](https://cloud.baidu.com/doc/AIGW/s/jmih1kum8)、[自定义域名](https://cloud.baidu.com/doc/AIGW/s/Bmotznz9o)、[内网 DNS 解析](https://cloud.baidu.com/doc/AIGW/s/Emou0i2ky)

> ✅ **结论**：**在百度智能云体系内，你的 `POST /mcp` streamable-HTTP MCP Server 有官方落点** ——
> AIGW 的「**直接代理**」模式。
>
> ❗ **但必须注意三个未确认点（不要当成已解决）**：
> 1. ❓ **AIGW 托管的 MCP 能不能被千帆 AppBuilder 的工作流 MCP 节点消费？官方文档没写。**
>    我的证据倾向于：**千帆「MCP 广场」只能消费平台内建/已上架的 MCP，自建 URL 只能填进工作流 MCP 节点**（而那个节点只吃 SSE）。
>    → **这条链路必须实测或开工单确认。**
> 2. ❓ 表单里 `MCP 协议类型` 的下拉**没有在截图里展开**，是否**只能**选 Streamable HTTP、
>    是否还能选 SSE，**未查到**（不编）。
> 3. ⚠️ AIGW 是**独立网关产品**（2026-01 才公测），接进千帆还可能涉及额外配置/审批。
>    另：AIGW 文档**未对 MCP 服务承诺数据留存策略**（❓未查到）。

---

## 3. 火山引擎 HiAgent（含同生态 AgentKit / DataAgent）

### 3.1 结论先行：**控制台文档没有，但官方开源 SDK 证明 HiAgent 支持 MCP，且支持 Streamable HTTP + 自定义 header**

**（a）先确认一个事实：HiAgent 在火山引擎公开文档中心里没有自己的库** ✅
1. 拉取文档中心**完整库清单**（`GET https://www.volcengine.com/api/doc/getLibList`，共 **288 个库**），
   **没有任何 HiAgent 库**（连 `hiagent` / `hi-agent` 字符串的出现次数都是 0）。与智能体相关的库只有：
   `85637 数据智能体 DataAgent`、`86681 AgentKit`、`82379 火山方舟`、`86760 DataAgent（私有化）`、
   `87373 AI Agent工具`、`85508 联网问答Agent`、`85800 客服Agent`、`85883 安全运营智能体`。
2. `https://www.volcengine.com/product/hiagent` 产品页**没有任何指向 docs 的链接**（grep 过全部 href/src）。
3. 站内搜索 API（`GET /api/search/openSearchNew?Query=HiAgent`）命中的都是**别的产品提到 HiAgent**
   （例如企业知识引擎的「对接 HiAgent」），不是 HiAgent 自己的文档。
4. ❓ **未查到**的项：HiAgent 控制台里 MCP 的**具体菜单路径**、HiAgent 专属隐私政策/服务条款、
   HiAgent 插件的 OpenAPI 文件规范。搜索关键词：`HiAgent 帮助中心`、`HiAgent 用户手册`、
   `HiAgent MCP 服务 添加`、`HiAgent 控制台 MCP`、`HiAgent 插件 创建 OpenAPI`、
   `HiAgent 隐私政策`、`HiAgent 数据出境`。

**（b）🎯 但官方开源 SDK 给出了**一手权威证据**：HiAgent 支持 MCP** ✅
[`volcengine/hiagent-go-sdk`](https://github.com/volcengine/hiagent-go-sdk) 的 **`hibot`** 包
（[README 原文](https://github.com/volcengine/hiagent-go-sdk/blob/main/hibot/README.md)）：
> 「Hibot 平台的官方 Go SDK，封装了 **Hibot 私有化部署**下的 Agent / Session / Skill / **MCP** / Resource 等核心资源」

（Hibot 即 HiAgent 私有化版的产品代号；根 README 写明这是 "SDK of the HiAgent product from Volcano Engine"。）

关键证据（**我逐条 curl 原文核对过**）：

| 证据 | 原文 / 代码 | 结论 |
|---|---|---|
| MCP 是一等资源 | README 表格：`\| **MCP** \| 外部 **Streamable HTTP / Stdio** MCP Server \|` | ✅ **支持 MCP，传输 = Streamable HTTP + Stdio** |
| 传输常量 | README 示例：`Transport: hibot.V1MCPTransportStreamableHTTP`、`Endpoint: "https://api.githubcopilot.com/mcp/"` | ✅ 官方示例是 **Streamable HTTP**，URL 以 `/mcp` 结尾 |
| **自定义 header** | [`hibot/v1/mcps_types.go`](https://github.com/volcengine/hiagent-go-sdk/blob/main/hibot/v1/mcps_types.go)：`Endpoint string \`json:"URL"\``、**`Headers map[string]string \`json:"Headers"\``** | ✅ **支持任意 header 名**（map 键完全自定义） |
| 工具级权限 | 同文件：`ToolAllowlist []string` / `ToolDenylist []string` / `ToolPrefix` / `Timeout int64` | ✅ 可按工具白名单放行 `analyze_job` / `analyze_jobs_batch` |
| Agent 绑定 MCP | README：`{OfMCP: &hibot.V1ManagedAgentMCPToolParams{Type: ..., ID: mcp.ID}}` | ✅ MCP 挂到 Agent 的 Tools 上 |
| 服务端入口 | [`hibot/internal/version/version.go`](https://github.com/volcengine/hiagent-go-sdk/blob/main/hibot/internal/version/version.go)：`ServerService = "hibot-server"` | ✅ 走 TOP API（服务名 `hibot-server`），**不依赖控制台** |
| 是否支持 SSE | 代码里唯一出现的传输常量是 `V1MCPTransportStreamableHTTP` | ⚠️ **推测不支持 SSE**（我只验证了 Streamable HTTP 常量存在，没找到 SSE 常量） |

👉 **这意味着：接 HiAgent 可以完全绕开控制台，用 SDK / TOP API / CLI 注册 MCP**，
而这正好绕开了"控制台菜单路径未查到"这个问题。

**（c）关于"内网 / 是否强制公网 HTTPS"** ⚠️
- ❓ 官方无明文。
- ⚠️ **推测不强制公网 HTTPS**：Python SDK 的端到端测试用的是 `http://mcp.local/mcp`（明文 HTTP + 内网 `.local` 域名）。
- ⚠️ **推测支持内网**：HiAgent 本身是私有化部署，SDK 允许任意 `Endpoint`。
- ❓ **出网白名单**：未查到（关键词 `HiAgent MCP 出网白名单`、`HiAgent MCP 网络要求`）。

**（d）第三方背景（不可作为配置依据）** ⚠️
[FORCE 2026 HiAgent 3.0 完整解读](https://blog.csdn.net/lpfasd123/article/details/162229660) 称 HiAgent 3.0 有
「**MCP 3.0 标准化网关**全面重构」「零代码连接器扩容至 **300+**」「**MCP 安全沙箱隔离**」。这是营销解读，仅供参考。

### 3.2 火山引擎**有完整公开文档**的 MCP 接入路径

#### （A）AgentKit —— 公开文档最全，生产推荐 ✅

[接入已有 MCP Server 到 AgentKit 网关](https://www.volcengine.com/docs/86681/2607684) 原文要点：
- 「后端必须实现 MCP 协议的 **Streamable HTTP** 传输端点，能响应 `tools/list` 与 `tools/call` 请求」
- 示例后端地址：`https://your-mcp-server.internal:8080/mcp/v1`（**「路径可自定义」**）
- 「网关会将后端已有 MCP Server 的 `tools/list` 响应**透传给平台侧**」
- 「AgentKit 网关转发 MCP 请求，**不改变后端 MCP 协议行为**」
- 前提：「AgentKit 网关能访问已有 MCP Server 地址」

🔴 **重要「操作限制」原文（这条对你的方案影响最大）**：
> 「AgentKit 网关仅支持 MCP **2025-03-26 及之后**协议版本的 Streamable HTTP 传输，
> **不支持直接接入 stdio 传输 / SSE（Server-Sent Events）-only 的 MCP Server**。
> 如需接入，请先使用 **mcp-proxy** 或同类工具将 stdio 进程或 SSE-only 服务封装为 Streamable HTTP 端点，再按本文流程接入。」
> —— [86681/2607684](https://www.volcengine.com/docs/86681/2607684)

🔴 **另一条限制**：
> 「当前 AgentKit 网关**暂不支持私网 IP 地址作为后端**，建议您使用如下两种方式实现网络环境的可达性……
> **同 VPC**：火山引擎同 VPC（Virtual Private Cloud）下子网间网络默认互通，可内网直达。」

以及：「当前 MCP 工具集**暂不支持添加仅开启"私网访问"的 MCP 服务**，若您要使用私网访问的方式……
请**同时开启公网访问**（即配置为：公网访问、私网访问）」。

**控制台入口** ✅：[创建 MCP 服务](https://www.volcengine.com/docs/86681/1844857)
→ 登录 **AgentKit 控制台** → 左侧导航树「**网关 > MCP**」→「**MCP 服务**」页签 → 「创建 MCP 服务」。
三种创建方式：**部署 MCP 服务** / **导入 MCP 服务** / **HTTP 转 MCP**。

**字段级证据（最硬）** ✅：[CreateMCPService API](https://www.volcengine.com/docs/86681/1913810)

| 参数 | 取值/说明 | 对你意味着 |
|---|---|---|
| `ProtocolType` | `HTTP`（HTTP 转 MCP）/ `MCP`（直接访问 MCP） | 你的场景选 **`MCP`** |
| `Path` | 「MCP 服务的访问路径」，**示例值：`/mcp`** | ✅ **路径可自定义**，你填 `/mcp` 正好 |
| `BackendType` | `Function` / `Domain`(固定域名) / `ECS` / `VKE` / `CustomPublic`(NPX/UVX) / `CustomPrivate`(镜像) | 自建有公网域名选 `Domain` |
| `NetworkConfiguration.EnablePublicNetwork` | 默认 `true` | 公网可达即可 |
| `NetworkConfiguration.EnablePrivateNetwork` + `VpcId` + `SubnetIds` | 「`EnablePrivateNetwork` 和 `EnablePublicNetwork` 参数，**必须传入其中一个**」 | ✅ **支持私网/VPC** |
| `OutboundAuthorizerConfiguration.AuthorizerType=ApiKey` → `ApiKeyLocation` | 取值「**当前仅支持 `HEADER`**」 | 出站鉴权只能放 header |
| ↑ `Parameter` | 「请求头（Header）中携带的 API Key 对应的**参数名称**」，示例值 **`Authorization`** | ✅ **header 名可自定义** |
| `InboundAuthorizerConfiguration.AuthorizerType` | `ApiKey`（header 名可自定义）或 `CustomJWT`(OAuth，需 `DiscoveryUrl`) | 平台侧可对**入站**做鉴权，防止后端裸暴露 |
| `TlsSettings.TlsMode` | `DISABLE`（后端协议为 **http** 时传）/ `SIMPLE`（后端 **https** 时传） | ✅ **后端不强制 HTTPS**，http 也行 |

**私网/VPC 专门文档** ✅：[MCP 服务通过私网域名方式接入网关实例](https://www.volcengine.com/docs/86681/2667405)
- 适用：「MCP Server 部署在 **VPC、企业 IDC 或第三方云上私网环境**」「以私网域名对内提供访问」
- 四层配置：专属网关入口 → MCP 服务映射 → 私网网络连通 → 私网 DNS 解析
- 需打通火山 VPC 与后端 VPC（中转路由器 TR / 专线），并**放通业务端口与 DNS 的 UDP/TCP 53**
- 有「查看 MCP 服务网络信息」页（[2611395](https://www.volcengine.com/docs/86681/2611395)）

**OpenAPI 文件规范** ✅：[MCP服务API文件规范与示例](https://www.volcengine.com/docs/86681/2227893)

| 项 | 官方结论 |
|---|---|
| 版本 | **仅支持 Swagger 2.0 与 OpenAPI 3.0**（示例用 `"openapi": "3.0.1"`）；「当前仅支持 Swagger 2.0 与 OpenAPI 3.0 格式的 API 描述文件」 |
| 元数据 | 「代码中必须包含 Swagger/OpenAPI 3 元数据、**title 和 version**」 |
| `operationId` | 「**建议**为每个 Operation 设置 `operationId`」（建议，非强制） |
| 请求方法 | 「**仅支持 GET、POST、PUT、DELETE、PATCH**」；CONNECT/HEAD/OPTIONS/TRACE 被忽略 |
| Content-Type | 「支持 JSON（`application/json`）和 Form（`application/x-www-form-urlencoded`）」；**不支持** `application/xml`、`multipart/form-data`、`application/octet-stream` |
| 响应 | 「仅支持解析 **200、201 和 default**，且仅解析 1 个」，优先 200 > 201 > default |
| 数组参数 | path/query/header/cookie/formData 位置时序列化为 **CSV（逗号分隔）** |
| 参数类型 | 「**不支持 `file`**」 |
| 是否允许 `oneOf` | ❓ 未查到明文（文档只列了上述约束） |
| 响应大小/超时 | ❓ 未查到明文 |

#### （B）数据智能体 DataAgent —— 最完整的"MCP 服务管理"界面 ✅

[MCP 服务管理](https://docs.volcengine.com/docs/85637/2123119)（DataAgent，中文）原文：
- 入口：「登录并进入智能体的管理后台，在左侧导航栏单击 **MCP 服务管理**」→ 右上角「**添加 MCP 服务**」；
  或 Agent 级入口。区分**系统级**（仅系统管理员可加，所有智能体可用）与 **Agent 级**。
- 字段表（原文）：

  | 参数 | 配置说明 |
  |---|---|
  | MCP 服务 & 描述 | 自定义名称和描述 |
  | 分类 | 数据拓展 / 分析能力 |
  | 添加方式 | 「当前支持：**StreamableHTTP、SSE、OpenAPI**」 |
  | **URL** | 「配置 MCP 服务的连接 URL。**请确保 Data Agent 所处网络能访问该 URL**」 |
  | **Header 鉴权** | 「配置 MCP 服务连接时的鉴权信息。如果 MCP 服务无需鉴权，您可单击配置框后的删除按钮」→ ✅ **可自定义的 header 键值对** |

- 「仅通过 **OpenAPI** 方式添加 MCP 服务时需手动添加工具，通过 **StreamableHTTP、SSE** 方式添加时，
  系统会**自动**为您拉取 MCP 服务中已注册的工具」
- OpenAPI 方式手工加工具时：「**模型调用标识**……**不支持配置为中文字符**」；
  **Input Schema 必填（JSON Schema）**、Output Schema 可选；Request/Response Schema 用 **JSONata** 映射
- ⚠️ **硬性限制**：「当前添加的自定义 MCP 服务的 **MCP 协议版本需为 2025-06-18 及以后的版本**」；
  「当前**智能问数**功能不支持使用接入的 MCP 服务能力」，只能在**深度研究**和**洞察报告**里用
- 官方画了「调用 MCP 工具时的处理流程」：大模型按 Input Schema 做入参校验 → 按 Request Schema 转换 → 调用 →
  按 Response Schema 回转 → 按 Output Schema 校验

（私有化版本同名文档：[DataAgent（私有化）MCP 服务管理](https://www.volcengine.com/docs/86760/2116759)）

### 3.3 HiAgent 自身的其它自定义能力方式

✅ **查证过（官方 SDK）**，HiAgent（hibot）把"自定义能力"分成四类：
1. **MCP**（首选，见 3.1）—— Agent 通过 `Tools=[{OfMCP:{Type:"mcp", ID: mcp.ID}}]` 绑定。
2. **插件（Plugin）→ 工具（Tool）** —— 经典 API（Service=`app`，Version=`2023-08-01`，`open.volcengineapi.com`）
   只有 `GetArchivedTool` / `ExecArchivedTool`；**工具归属插件**（响应里带 `PluginID`），
   执行时传 `PluginID + ToolID + Config(插件临时授权) + InputData`。
   → [tool_types.py](https://github.com/volcengine/hiagent-python-sdk/blob/main/libs/api/hiagent_api/tool_types.py)、
   [tool.py](https://github.com/volcengine/hiagent-python-sdk/blob/main/libs/api/hiagent_api/tool.py)
3. **Skill**（zip 制品 + 版本）
4. **工作流 / 知识库**

❓ **HiAgent「插件」是否支持 OpenAPI/Swagger 文件导入、要什么格式 → 未查到**。
搜索关键词：`HiAgent 插件 创建 OpenAPI`、`HiAgent 插件 Swagger 导入`、`HiAgent 自定义插件 API 文档`。

#### 补充：火山方舟 Ark 是另一条独立产品线（别和 HiAgent 混）✅
[方舟 Tools 文档](https://www.volcengine.com/docs/82379/2553719)：Managed Agents 用
`mcp_servers[{ type: "url", name, url }]` + `tools[].type = mcp_toolset`（**必须一一对应**），
凭据在 **Session** 层通过 `vault_ids` → Vaults（`static_bearer` / `mcp_oauth`）注入。
方舟自己的「文档 MCP」是给编程助手用的**只读**远程 Server（Streamable HTTP、无鉴权）。

### 3.4 数据出境 / 隐私（可引用的官方原文）

⚠️ **HiAgent 专属的隐私政策 / 服务条款：❓ 未查到**（无公开文档库，产品页也无法律入口）。
搜索关键词：`HiAgent 隐私政策`、`HiAgent 数据安全`、`HiAgent 数据出境`、`HiAgent 服务协议`。
👉 引用前必须走商务/合同渠道；「私有化、数据不出域」目前**只有第三方描述**。

✅ **可替代引用的官方条款（同生态，注意适用范围）**：

**（1）[火山引擎数据保护协议（DPA）](https://www.volcengine.com/docs/6391/67493)**
- **3.5.1.1 一般情况**：「……客户应委托火山引擎将客户数据或个人信息传输到**中国大陆**并在这些地点存储和处理……
  **除非客户指定，否则火山引擎不会将代表客户处理的客户数据和个人信息传输到中国大陆之外地理位置**。」
- **3.5.1.2**：特定场景（国际/港澳台短信等）可能经委托传境外；
  「原则上**客户应当对可能的数据出境情况承担数据安全、个人信息及隐私保护等承担合规责任**。」
- **3.5.5 数据保留及删除**：「保存和处理期限**不得超过为实现约定数据处理目的所需的必要期限**」。

**（2）[火山方舟大模型服务平台专用条款](https://www.volcengine.com/docs/82379/1104498)（与"工具调用"最相关）**
- **3.7.7**：「**未经您的单独同意，火山引擎不会存储和使用您的数据来训练或优化模型**。」
  （例外：合规审查、你主动使用排障工具、模型异常调用告警数据存储审查等）
- 🔴 **3.7.9（直接对应 MCP/插件场景）**：「特别提示您，您试（使）用火山引擎为您提供的**官方插件服务**时，
  **您的数据将被发送至插件内进行处理**，处理完成后您的数据与处理结果共同返回至您订购的大模型服务……」
- **3.7.11**：使用缓存/日志/可观测性/**Managed Agents 托管**时数据会存在方舟存储空间，
  「**未经您授权，火山引擎不会访问或使用您的数据**」
- **3.7.10**：你通过 Managed Agents 开发的应用，**知识产权归你**，但发布前须自行做合规评估/备案

**⚠️ 工具调用时 JD 数据经过哪些环节**：
```
（SaaS / AgentKit / DataAgent / Ark）你的用户 → 火山侧 Agent 运行时（中国大陆）→ 大模型
  → 网关/MCP 客户端出站（AgentKit 可开私网出口）→ 你的 MCP Server（公网 或 VPC 私网域名）
  → 你的后端 → 结果回传为模型上下文
（HiAgent 私有化）你的用户 → 企业内网 HiAgent（hibot-server）→ 内网 MCP Server → 内网后端
```
依据 3.7.9 的口径可以明确写：**"数据会被发送到你的 MCP Server 内部处理"** —— 这是官方承认的环节。
❓ **跨境环节：未查到**任何官方说明。

### 3.5 OpenAPI 方式接入的具体限制

**HiAgent 插件 OpenAPI 规范：❓ 未查到。**（关键词：`HiAgent 插件 OpenAPI 限制`、`HiAgent oneOf operationId`）

✅ **AgentKit 的规范是火山公开文档里最接近的官方答案**（[MCP服务API文件规范与示例](https://www.volcengine.com/docs/86681/2227893)、[将现有 REST API / OpenAPI 接入为 MCP 工具](https://www.volcengine.com/docs/86681/2607685)）：

| 项 | 官方结论 |
|---|---|
| **版本** | **仅 Swagger 2.0 与 OpenAPI 3.0**（**无 3.1**）；「必须包含 Swagger/OpenAPI 3 元数据、title 和 version」 |
| 文件大小 | ✅ **≤ 5 MiB**（`.yaml` / `.json`） |
| **`operationId`** | ✅ **建议**为每个 Operation 设置（非强制）；它**会映射为 MCP 工具名**，建议小写下划线；**缺失会导致工具列表为空** |
| 请求方法 | **仅 GET / POST / PUT / DELETE / PATCH**；CONNECT/HEAD/OPTIONS/TRACE 被忽略 |
| 请求体 | **不要求必须 `application/json`**，但**只支持** `application/json` 与 `application/x-www-form-urlencoded`；**不支持** xml / multipart/form-data / octet-stream |
| 响应 | **仅解析 200 / 201 / default，且只取 1 个**，优先级 **200 > 201 > default** |
| 数组 / 参数类型 | path/query/header/cookie/formData 位置的 array **序列化为 CSV**；**不支持 `file` 类型** |
| `oneOf` | ❓ 文档无明文 → **建议避免** |
| 响应大小 / 超时上限 | ❓ **未查到**显式数值。已知相关限制是**网关 QPS**：共享网关有 QPS 限流；专属网关 small_x1 = 1,500 QPS … large_x4 = 192,000 QPS；且**工具集对 MCP 服务的定期连接计入计费**（有状态协议 60 次/时，无状态 MCP-0728 协议 24 次/时）→ [网关模式和 MCP 请求计数规则](https://www.volcengine.com/docs/86681/2678873) |

✅ **DataAgent 的 OpenAPI 方式**（见 3.2(B)）：控制台手工建 Tool —— `工具名称`、
`模型调用标识`（**不支持中文**）、`描述`、`Input Schema`（必填 JSON Schema）、`Output Schema`（可选）、
`URL & 请求方式`、`Request/Response Schema`（**JSONata** 映射）。

---

## 4. 三平台共同的最小可行配置

> 目标：**同一个 MCP Server 同时适配三家**。下面是硬性条件清单（每条注明依据）。

### 4.1 硬性条件（必须做）

| # | 条件 | 依据 | 状态 |
|---|---|---|---|
| **1** | **必须两个远程端点都提供：`POST /mcp`（Streamable HTTP）和 `GET /sse` + message 端点（SSE）** | 百炼：`streamableHttp`↔`POST /mcp`、`sse`↔`GET /sse` ✅；**千帆工作流 MCP 节点：只吃 SSE** ✅；**AgentKit：只吃 Streamable HTTP，且明文「不支持直接接入 stdio / SSE-only」** ✅；HiAgent：Streamable HTTP ✅ —— 两端点同时提供，各家各取所需 | 🔴 **最关键** |
| **2** | 路径就用标准 `/mcp` 与 `/sse` | 百炼**强制**匹配 ✅；AgentKit 可自定义但示例就是 `/mcp` ✅；千帆未强制 ✅ | 🟢 |
| **3** | 鉴权统一用 **`Authorization: Bearer <token>`**，服务端**自己校验** | 百炼 `headers` 支持该键 ✅；AgentKit 出站 ApiKey 的 `Parameter` 示例值就是 `Authorization` 且**仅支持放 HEADER** ✅；DataAgent 有 Header 鉴权 ✅；千帆 ❓ 未查到 | 🟡 |
| **4** | **同时支持"不带 header 也能鉴权"的降级通道**（例如 `?token=<token>` 或 path 里的 token） | 千帆的 JSON 模板里**没有 headers 字段**，且千帆**所有官方 MCP 示例都把凭据放 URL query**（`?ak=` / `?api_key=`）✅ → **query 传 token 是千帆的既定做法** | 🔴 **为千帆必备** |
| **5** | **公网可达 + 受信任 CA 的 HTTPS 证书（禁用自签名）** | 百炼 `11200048 MCP_SSL_ERROR`（自签名不被信任）✅；千帆/百炼均为云端发起 ⚠️ | 🔴 |
| **6** | 单次工具调用**快且无状态、可重试幂等**；不要依赖长连接会话状态 | 百炼有 `MCP_REQUEST_TIMEOUT` / `MCP_SESSION_NOT_FOUND`（服务端重启丢会话）✅；千帆 CFC 节点超时「通常 10 秒」✅；百炼基础模式**有冷启动延迟** ✅ | 🟡 |
| **7** | **工具返回体做截断/分页，控制在 1MB 以内** | 千帆 API 节点硬限「返回内容大小不能超过 **1M**」✅；三家 MCP 侧 ❓ 未给量化值 | 🟡 |
| **8** | 工具名用 `[a-z0-9_]`、**无中文**；每个工具的描述写清楚（中英都备）；入参用**标准 JSON Schema** | DataAgent「模型调用标识**不支持中文**」✅；千帆「大模型根据**英文名称及组件描述**判断是否调用」✅；百炼「参数必须有描述，否则 `130040`」✅ | 🟢 |
| **9** | **避免 `oneOf`/`anyOf` 与深层嵌套**（建议 ≤3 层） | 千帆「参数层级**不支持超过 10 层**」✅；`oneOf` 三家 ❓ 未查到 → 保守起见不用 | 🟡 |
| **10** | 用**新版 mcp SDK**，协议版本 ≥ **2025-06-18** | DataAgent 硬性要求「MCP 协议版本需为 **2025-06-18 及以后**」✅ | 🟢 |
| **11** | **每个工具可独立调用**（别做"必须先调 A 再调 B"的隐式依赖） | 百炼工作流 MCP 节点**1 节点 1 工具** ✅；千帆 MCP 节点**工具仅支持单选** ✅ | 🟢 |
| **12** | 别指望平台把它的鉴权注入到你的服务；也不要依赖固定出口 IP 做白名单 | 百炼「**无固定出口公网 IP**」✅；AgentKit 出站鉴权配置在平台侧 ✅ | 🟡 |

### 4.2 建议的对外形态（照抄即可）

```jsonc
// 端点
POST https://mcp.example.com/mcp      // Streamable HTTP（百炼 / AgentKit / DataAgent）
GET  https://mcp.example.com/sse      // SSE（千帆）；配套 message 端点
POST https://mcp.example.com/messages

// 鉴权（三选一，按平台能力降级）
Authorization: Bearer <TOKEN>          // 首选：百炼 headers / AgentKit 出站 ApiKey / DataAgent Header 鉴权
?token=<TOKEN>                         // 兜底：给"只能填一个 URL"的平台（千帆 ❓）
```

各平台"粘贴版"：

**阿里云百炼**（MCP 管理 → 创建 MCP 服务 → 使用脚本部署 → http）
```json
{
  "mcpServers": {
    "job-analyzer": {
      "type": "streamableHttp",
      "url": "https://mcp.example.com/mcp",
      "headers": { "Authorization": "Bearer <TOKEN>" }
    }
  }
}
```

**百度千帆**（创建 → 工作流Agent/组件 → 节点「MCP Server」→ 配置 MCP Server）
```json
{
  "mcpServers": {
    "job-analyzer": {
      "url": "https://mcp.example.com/sse?token=<TOKEN>"
    }
  }
}
```
> ⚠️ 这段是**推测写法**（官方模板只有 `"url": "your server url"`，且 UI 声明只支持 SSE）。
> 必须到控制台点【连接到 MCP Server】实测；若读不到工具列表，再试 `"headers": {...}` 等未文档化的键。
> 参考：[MCP Server节点](https://cloud.baidu.com/doc/qianfan/s/cmhj482ha)

**百度智能云 AIGW**（备选：唯一官方支持 Streamable HTTP 的百度落点，但能否被千帆消费 ❓未确认）
```
左侧边栏【AI 服务】→【MCP 服务】→【创建 MCP 服务】
MCP 服务名称   = job-analyzer
MCP 服务类型   = 直接代理            # 直接代理原生 MCP 服务；非「HTTP 转 MCP」
MCP 协议类型   = Streamable HTTP
访问路径       = /mcp-servers/job-analyzer
后端服务       = 指向你的 MCP Server（需先在【后端服务】里注册，含服务来源/端口/服务协议）
消费者认证     = 建议开启（API Key）
```
> 参考：[创建 MCP 服务 - AIGW](https://cloud.baidu.com/doc/AIGW/s/cmo2dtigr)、[产品介绍 - AIGW](https://cloud.baidu.com/doc/AIGW/s/Ymj2hu93w)。
> ❗ 前提是先确认**千帆 AppBuilder 能消费 AIGW 托管的 MCP**（官方未写，必须实测/开工单）。

**火山引擎 HiAgent**（官方 Go SDK `hibot`，无需控制台）✅
```go
mcp, err := client.V1.MCPs.New(ctx, hibot.V1MCPNewParams{
    Name:      "job-analyzer",
    Transport: hibot.V1MCPTransportStreamableHTTP,   // 常量字面量 "streamable_http"
    Endpoint:  "https://mcp.example.com/mcp",        // 完整路径，官方示例均以 /mcp 结尾
    Headers:   map[string]string{"Authorization": "Bearer <TOKEN>"},  // 任意 header 名
    ToolAllowlist: []string{"analyze_job", "analyze_jobs_batch"},
})
// 再把 mcp 挂到 Agent：Tools: []ToolParams{{OfMCP: &hibot.V1ManagedAgentMCPToolParams{ID: mcp.ID}}}
```
> 服务端走 TOP API（`ServerService = "hibot-server"`），SDK 内部路由。
> 出处：[hibot/README.md](https://github.com/volcengine/hiagent-go-sdk/blob/main/hibot/README.md)、
> [hibot/v1/mcps_types.go](https://github.com/volcengine/hiagent-go-sdk/blob/main/hibot/v1/mcps_types.go)。
> ⚠️ 代码里只出现 Streamable HTTP 常量，**推测不支持 SSE**。

**火山引擎 AgentKit**（生产推荐，控制台「网关 > MCP > MCP 服务 > 创建」或 `CreateMCPService`）
```
ProtocolType = MCP
BackendType  = Domain
Path         = /mcp
TlsMode      = SIMPLE                      # 后端 https（http 用 DISABLE）
EnablePublicNetwork = true                 # 或 EnablePrivateNetwork + VpcId/SubnetIds
OutboundAuthorizerConfiguration.AuthorizerType = ApiKey
OutboundAuthorizerConfiguration.Authorizer.KeyAuth.ApiKeyLocation = HEADER
OutboundAuthorizerConfiguration.Authorizer.KeyAuth.Parameter      = Authorization
OutboundAuthorizerConfiguration.Authorizer.KeyAuth.ApiKeys.N.Key  = <TOKEN>
```
> ⚠️ 三条硬限制：**仅支持 MCP ≥2025-03-26 的 Streamable HTTP**（否则要 mcp-proxy 包一层）、
> **不支持私网 IP 作为后端（必须用域名）**、**MCP 工具集不接受"仅私网访问"的服务**（要同时开公网）。
> 出处：[86681/2607684](https://www.volcengine.com/docs/86681/2607684)

### 4.3 上线前的 smoke test（三家平台通用，先本地自测）

⚠️ Streamable HTTP 客户端**必须同时接受 `application/json` 和 `text/event-stream`**，
`Accept` 头缺 `text/event-stream` 是最常见的"平台连不上"原因之一。

```bash
# streamable HTTP 端点自测：必须返回 result.tools 里含 analyze_job / analyze_jobs_batch
curl -i -N --max-time 20 -X POST 'http://<host>:<port>/mcp' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  --data-raw '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"1.0.0"}}}'

# SSE 端点自测（千帆用）：应先返回 event: endpoint，再保持长连接
curl -i -N --max-time 20 'http://<host>:<port>/sse?token=<TOKEN>'
```

### 4.4 一句话总结

> 三家**唯一真正冲突**的地方是传输层，而且这个冲突是**双向的**：
> **千帆工作流 MCP 节点只吃 SSE；火山 AgentKit 明说"不支持 SSE-only"，只吃 Streamable HTTP；百炼两者都吃。**
> 所以你这个 MCP Server 的改造量**不在业务逻辑，而在传输层**：
> 在官方 `mcp` SDK 上**同时挂载 SSE 与 Streamable HTTP 两套 transport，并把鉴权做成
> "header 优先、query 兜底"**。其余（工具命名、JSON Schema、1MB 返回、无状态、短耗时）
> 都是**通用工程约束**，改一次三家通用。
>
> 🔀 **两条可选路线**：
> - **路线 A（稳，推荐）**：**加一个 SSE 端点**，`/sse` 给千帆、`/mcp` 给百炼/AgentKit/HiAgent。
>   改动小、风险最低，**覆盖全部四家**。
> - **路线 B（省事但要验证）**：**只保留 Streamable HTTP**，千帆侧改走**百度 AIGW 直接代理**。
>   ⚠️ 这条路**官方文档没写通**（AIGW 是 2026-01 才公测的独立网关产品），**必须实测或开工单**，
>   不要把"文档上支持 Streamable HTTP"直接当成"千帆能用"。

---

## 附录 A：本次调研用到的"文档后端 API"技巧（下次查这几个平台能省大量时间）

这几个厂商的文档站都是 SPA，`web_fetch` 只能拿到导航壳。绕过方式：

| 平台 | 方法 |
|---|---|
| **火山引擎** | `https://www.volcengine.com/api/doc/getDocDetail?DocumentID=<docId>` → JSON，`Result.Content` 是完整正文（Quill delta 或 markdown）。库清单 `GET /api/doc/getLibList`；文档树 `GET /api/doc/getDocList?LibraryID=<libId>`；站内搜索 `GET /api/search/openSearchNew?Query=<kw>&Page=1&Size=30` → 返回 **`FullContent` 正文 + `BreadCrumbs`（能定位文档属于哪个库）+ `Url`**，连已下线/加密文档（`document is not in online`）也能拿到摘要。两个 host 都可用：`www.volcengine.com` 与 `docs.volcengine.com`（注意 `docs.volcengine.com` 的部分路径会 404，`www` 更稳） |
| **百度智能云** | **两个办法，第二个更省事**：① Gatsby 的 `page-data.json`：`https://bce.bdstatic.com/p3m/bce-doc/online/<product>/doc/<product>/s/page-data/<slug>/page-data.json` → `result.data.markdownRemark.html` 是正文 HTML。② **`cloud.baidu.com/doc/...` 的文档页其实是服务端直出（Gatsby SSR）**：`curl -A "Mozilla/5.0" <url>` 就能在 `<div class="post__body">` 里拿到**完整正文**（`web_fetch` 只是把长正文截断了）。**完整左侧导航也在这份 HTML 里**（`data-filepath="..."` + `href="/doc/<product>/s/<slug>"`），**可以直接枚举该产品全部文档**（千帆 = 494 篇）→ 想做"全站 grep 某关键词是否出现"就用这招（已验证：`streamable` 在千帆 494 页里 0 命中）。另有整本 PDF：`https://bce-cdn.bj.bcebos.com/p3m/pdf/bce-doc/online/<产品>/<产品>.pdf` |
| **火山引擎开源 SDK** | HiAgent 的控制台文档不存在，但 **GitHub 上的官方 SDK 是一手权威来源**：`raw.githubusercontent.com/volcengine/hiagent-go-sdk/main/<path>` 直接读源码，比任何第三方博客可靠 |
| **阿里云** | `help.aliyun.com` 的文档页 `web_fetch` **能直接拿到正文**，无需特殊处理 |

## 附录 B：明确「未查到」的清单（避免误用）

| 平台 | 未查到项 | 搜索关键词 |
|---|---|---|
| 百炼 | 自定义插件的超时秒数、响应大小上限；自定义插件的 OpenAPI 版本/`operationId`/`oneOf` 规则 | `阿里云百炼 插件 超时时间 限制 秒 响应`、`dashscope api/v1/plugins open_api_schema operationId` |
| 千帆 | 工作流 MCP Server 节点**是否支持 Streamable HTTP（判定为否）**；是否支持自定义 Header 及其字段名；是否支持内网/VPC；出口白名单；API 节点的 OpenAPI 版本号 / operationId 命名 / oneOf；**MCP 广场能否添加任意自建 URL**（证据倾向于只能上架平台内建 MCP）；**AIGW 托管的 MCP 能否被千帆 AppBuilder 消费**；AIGW 的 `MCP 协议类型` 下拉是否还有 SSE | `千帆 MCP Server节点 SSE streamable http`、`千帆 AppBuilder MCP 自定义 headers 鉴权 json`、`千帆 OpenAPI 导入 operationId`、`AIGW MCP 直接代理 千帆` |
| 火山/HiAgent | **HiAgent 控制台里 MCP 的具体菜单路径**（SDK/API 层已确认支持，只是 GUI 路径没有公开文档）；HiAgent 专属隐私政策/服务条款；HiAgent 插件的 OpenAPI 文件规范；HiAgent 是否支持 SSE（只找到 Streamable HTTP 常量 → 推测不支持）；HiAgent 内网/出网白名单；AgentKit 是否允许 `oneOf`、响应大小/超时上限 | `HiAgent MCP 服务 接入`、`HiAgent 帮助中心 用户手册`、`HiAgent 控制台 MCP`、`HiAgent 插件 创建 OpenAPI`、`HiAgent 隐私政策`、`HiAgent MCP 出网白名单` |

## 附录 C：主要出处索引

**阿里云百炼**
- [自定义 MCP 服务](https://help.aliyun.com/zh/model-studio/custom-mcp) ✅ 三种部署方式、配置模板
- [MCP 常见问题](https://help.aliyun.com/zh/model-studio/mcp-faq) ✅ **路径匹配规则、Headers 鉴权、无固定出口 IP、本地资源不可访问、全部错误码**
- [MCP 简介](https://help.aliyun.com/zh/model-studio/mcp-introduction) ✅ 计费（基础/极速模式）
- [官方 MCP 服务](https://help.aliyun.com/zh/model-studio/official-and-third-party-mcp) ✅ 最多 5 个 MCP、工作流 MCP 节点 1 工具
- [外部调用](https://help.aliyun.com/zh/model-studio/mcp-external-calls) ✅ `dashscope.../api/v1/mcps/<id>/mcp` + Bearer
- [自定义插件](https://help.aliyun.com/zh/model-studio/custom-plug-ins) ✅ 表单式配置、鉴权三 Type
- [合规资质与隐私说明](https://help.aliyun.com/zh/model-studio/privacy-notice) ✅ 隐私条款
- [MCP 网关管理（AI 网关）](https://help.aliyun.com/zh/api-gateway/ai-gateway/user-guide/mcp-gateway-management) ✅ **Swagger 文件导入**（HTTP 转 MCP）
- 第三方实测：[阿里云百炼 MCP 部署（CSDN）](https://blog.csdn.net/scabbards_/article/details/159394955) ✅ `headers` 可用、插件不能挂 MCP

**百度千帆**
- [MCP Server节点](https://cloud.baidu.com/doc/qianfan/s/cmhj482ha)（工作流组件）✅ 步骤 + **SSE 文案** + JSON 模板
- [MCP Server节点](https://cloud.baidu.com/doc/qianfan/s/Bmh4stwja)（工作流 Agent）✅ 同上
- [工具及MCP广场](https://cloud.baidu.com/doc/qianfan/s/1mh4stp3t) ✅
- [API 节点](https://cloud.baidu.com/doc/qianfan/s/Emh4su361) ✅ **层级 ≤10 层、返回 ≤1M、响应类型**
- [函数计算CFC节点](https://cloud.baidu.com/doc/qianfan/s/Hmh4su41w) ✅ 超时通常 10 秒
- [创建组件](https://cloud.baidu.com/doc/qianfan/s/ymh4su330) ✅ 英文名/描述决定 function call
- [组件兼容MCP协议](https://cloud.baidu.com/doc/qianfan-docs/s/Hm984ai00)（AppBuilder SDK）✅ 远程 MCP **SSE** 组件
- [百度智能云隐私政策](https://cloud.baidu.com/doc/Agreements/s/Plr0fi68q) ✅ 业务数据/保存地域/跨境/保留期
- [百度千帆大模型服务及Agent开发平台用户协议](https://cloud.baidu.com/doc/qianfan/s/Cmh4stmpy) ✅ **「不用于训练、不与第三方共享」+ 最终用户同意责任**（生效 2026-01-09）
- 🔴 [产品介绍 - AI原生网关 AIGW](https://cloud.baidu.com/doc/AIGW/s/Ymj2hu93w) + [创建 MCP 服务 - AIGW](https://cloud.baidu.com/doc/AIGW/s/cmo2dtigr) ✅ **MCP over Streamable HTTP + 直接代理模式**（2026-01-01 公测）
- [AIGW HTTP 转 MCP 配置说明](https://cloud.baidu.com/doc/AIGW/s/Jmk6ihy1t) ✅ **securitySchemes：header 名可自定义**；[AIGW IP 白名单](https://cloud.baidu.com/doc/AIGW/s/mmou0vvn0) ✅（仅内网入口生效）
- [控制台使用说明](https://cloud.baidu.com/doc/qianfan/s/zmh4stqex) ✅ **「SSE 传输格式」「MCP-SSE 组件」「上架 MCP Server」**
- [千帆 AppBuilder 专项约定](https://cloud.baidu.com/doc/qianfan/s/emh4stmvj) + [认证鉴权](https://cloud.baidu.com/doc/qianfan/s/Kmh4sutww) + [通用说明（公共请求头）](https://cloud.baidu.com/doc/qianfan/s/fmh4sutq6)
- [千帆 ModelBuilder 插件编排服务下线通告](https://ai.baidu.com/ai-doc/WENXINWORKSHOP/nlu9ur4e5) ✅ **OpenAPI 文件导入那条路已死**（[迁移说明](https://qianfan.cloud.baidu.com/qianfandev/topic/271014)）
- 佐证（代码态也只到 SSE）：[appbuilder `mcp_server/client.py`](https://github.com/baidubce/app-builder/blob/master/python/mcp_server/client.py)

**火山引擎 / HiAgent**
- 🎯 **HiAgent 官方 Go SDK（一手证据）**：[hibot/README.md](https://github.com/volcengine/hiagent-go-sdk/blob/main/hibot/README.md) ✅ **「MCP = 外部 Streamable HTTP / Stdio MCP Server」**、官方示例用 `V1MCPTransportStreamableHTTP`
- [hibot/v1/mcps_types.go](https://github.com/volcengine/hiagent-go-sdk/blob/main/hibot/v1/mcps_types.go) ✅ **`Endpoint(URL)` / `Headers map[string]string` / `ToolAllowlist` / `Timeout` 字段级证据**
- [hibot/internal/version/version.go](https://github.com/volcengine/hiagent-go-sdk/blob/main/hibot/internal/version/version.go) ✅ `ServerService = "hibot-server"`
- [hiagent-python-sdk tool_types.py](https://github.com/volcengine/hiagent-python-sdk/blob/main/libs/api/hiagent_api/tool_types.py) ✅ 插件(Plugin)→工具(Tool) 经典 API
- [接入已有 MCP Server 到 AgentKit 网关](https://www.volcengine.com/docs/86681/2607684) ✅ **要求 Streamable HTTP、路径可自定义；明文「不支持 stdio / SSE-only」；不支持私网 IP 后端**
- [AgentKit 创建 MCP 服务](https://www.volcengine.com/docs/86681/1844857) ✅ 三种创建方式 + 入口路径
- [CreateMCPService API](https://www.volcengine.com/docs/86681/1913810) ✅ **字段级参数（Path/Header 名/VPC/TLS）**
- [MCP服务API文件规范与示例](https://www.volcengine.com/docs/86681/2227893) ✅ OpenAPI 限制表（Swagger 2.0 / OpenAPI 3.0、≤5 MiB、operationId→工具名）
- [网关模式和 MCP 请求计数规则](https://www.volcengine.com/docs/86681/2678873) ✅ QPS 与计费口径
- [MCP 服务通过私网域名方式接入网关实例](https://www.volcengine.com/docs/86681/2667405) ✅ **VPC/IDC 私网接入**
- [DataAgent MCP 服务管理](https://docs.volcengine.com/docs/85637/2123119) ✅ **StreamableHTTP/SSE/OpenAPI + Header 鉴权 + 协议版本 ≥2025-06-18**
- [DataAgent（私有化）MCP 服务管理](https://www.volcengine.com/docs/86760/2116759) ✅
- [方舟 Tools 文档](https://www.volcengine.com/docs/82379/2553719) ✅ Ark Managed Agents 的 MCP 接法
- [火山引擎数据保护协议（DPA）](https://www.volcengine.com/docs/6391/67493) ✅ 数据位置/跨境/保留期
- [火山方舟大模型服务平台专用条款](https://www.volcengine.com/docs/82379/1104498) ✅ **3.7.7 不用于训练 / 3.7.9 数据发送至插件内处理 / 3.7.11 托管数据不授权不访问**
- [HiAgent 产品页](https://www.volcengine.com/product/hiagent) — ⚠️ **无文档链接**
- 第三方（非官方）：[FORCE 2026 HiAgent 3.0 完整解读](https://blog.csdn.net/lpfasd123/article/details/162229660) ⚠️ 仅作背景
