# 火山引擎 HiAgent / 方舟 Ark 接入自定义 MCP Server 调研

> **本文与接入指南的关系**：本文是「调研过程 + 完整证据 + 抓取方法」的长文档；
> 面向用户的**精简接入指南**在 jobcopilot 仓库
> [`jobcopilot/docs/integrations/hiagent.md`](../../jobcopilot/docs/integrations/hiagent.md)。
> 两者不重复：本文留证据与可复用的抓取脚本，那份留「怎么接」。

> 调研日期：2026-09-15。所有「查证过」结论均来自**实际抓取到的原文**，不是搜索摘要。
>
> **抓取方法（重要，可复用）**：`docs.volcengine.com` 页面是 JS 渲染的空壳，但文档中心有后端 JSON API，直接返回完整 Markdown：
>
> ```bash
> # 单篇文档（Result.Content / Result.MDContent 都是完整 markdown）
> curl 'https://docs.volcengine.com/api/doc/getDocDetail?DocumentID=2123119'
> curl 'https://www.volcengine.com/api/doc/getDocDetail?DocumentID=2123119'   # 两个 host 都可用
> # 全部文档库（288 个）
> curl 'https://docs.volcengine.com/api/doc/getLibList'
> # 某库文档树
> curl 'https://docs.volcengine.com/api/doc/getDocList?LibraryID=85637'
> # 全站搜索（返回 FullContent，连已下线文档也能拿到正文片段）
> curl 'https://docs.volcengine.com/api/search/openSearchNew?Query=MCP&Page=1&Size=30'
> ```

---

## 0. 先说三个关键纠正 / 结论

**纠正 1：文档 id 85637 不是 HiAgent，是「数据智能体 DataAgent」。**
`getLibList` 显示 `85637 = Dataagent / 数据智能体`（[getLibList](https://docs.volcengine.com/api/doc/getLibList)）。`85637/2123119` 那篇「MCP 服务管理」是 **DataAgent** 的文档，不是 HiAgent 的。

**纠正 2：HiAgent 在火山引擎公开文档中心「没有」自己的文档库。**
完整拉取 288 个文档库，无 HiAgent；也没有 HiAgentPrivate 之类。HiAgent 只在别的产品文档里作为「对接对象」出现（如企业知识引擎的《对接 HiAgent》[doc 1852311](https://docs.volcengine.com/docs/86760/1852311)）。

**结论 1（本次最重要的发现）：HiAgent 支持 MCP，而且官方 SDK 明确写了 Streamable HTTP + Stdio，且 header 可自定义。**
证据是火山引擎官方开源 SDK `volcengine/hiagent-go-sdk` 的 `hibot` 包 —— 这是 HiAgent 私有化版本（内部代号 Hibot）的托管 Agent 平台 SDK：

- [`hibot/README.md`](https://github.com/volcengine/hiagent-go-sdk/blob/main/hibot/README.md) 核心概念表原文：
  `| **MCP** | 外部 Streamable HTTP / Stdio MCP Server | MCP Servers |`
- [`hibot/v1/types.go`](https://github.com/volcengine/hiagent-go-sdk/blob/main/hibot/v1/types.go) 原文：`V1MCPTransportStreamableHTTP = "streamable_http"`
- [`cmd/hibot/examples/README.md`](https://github.com/volcengine/hiagent-go-sdk/blob/main/cmd/hibot/examples/README.md) 原文：
  ```
  hibot mcps create --name=weather --transport=streamable-http \
      --endpoint=https://example.com/mcp \
      --header "Authorization=Bearer xyz" --header "X-Tenant=acme"
  ```

> ⚠️ 可信度说明：这是**官方仓库里的官方 SDK 代码 + 文档**（Apache-2.0，volcengine 组织），属于一手事实，但它描述的是 **SDK/API 层**，不是控制台 GUI。控制台菜单路径我没查到官方公开文档。

---

## 1. HiAgent 是否支持接入 MCP Server？传输？控制台入口？

| 子问题 | 结论 | 标注 |
|---|---|---|
| 是否支持 MCP | **支持**。官方 SDK 把 MCP 列为一等资源（`V1MCP`），有 Create/List/Get/Update/Delete/TestConnection 全套 Action | **查证过** — [hibot/README.md](https://github.com/volcengine/hiagent-go-sdk/blob/main/hibot/README.md)、[hibot/v1/mcps.go](https://github.com/volcengine/hiagent-go-sdk/blob/main/hibot/v1/mcps.go) |
| 支持哪些传输 | **Streamable HTTP + Stdio**。概念表原文 `外部 Streamable HTTP / Stdio MCP Server`；代码里只有一个传输常量 `"streamable_http"`，是 CLI 默认值；`V1MCP` 同时有 `URL`/`Headers`（HTTP）和 `Command`/`Args`/`Env`（stdio）。**未见 SSE 常量或 SSE 字样** | **查证过**（Streamable HTTP/Stdio）— [hibot/v1/types.go](https://github.com/volcengine/hiagent-go-sdk/blob/main/hibot/v1/types.go)、[hibot/v1/mcps_types.go](https://github.com/volcengine/hiagent-go-sdk/blob/main/hibot/v1/mcps_types.go)。**SSE 不支持 = 推测**（仅凭「代码里没有 SSE 常量」） |
| 控制台配置入口（具体菜单路径） | **未查到官方公开文档**。HiAgent 没有公开文档库，产品页 [volcengine.com/product/hiagent](https://www.volcengine.com/product/hiagent) 是 JS 空壳且不指向 docs。第三方（山东大学 HiAgent 使用指南）显示控制台结构为：`Agent 模块 → 创建智能体 → 编排页 → 技能面板 → 插件 / 工作流 / 知识库`（[SDU 4.2](http://aihub.sdu.edu.cn/aiic/syzn/djznt/jhscjznt.htm)、[SDU 4.5](http://aihub.sdu.edu.cn/aiic/syzn/djznt/tgcjcjznt.htm)）—— 但**那两页没有出现 MCP 入口** | 菜单路径 **未查到**（搜索关键词：`HiAgent 帮助中心`、`HiAgent 用户手册`、`HiAgent MCP 服务 添加`、`HiAgent 控制台 MCP`、`HiAgent 插件 创建`、`site:docs.volcengine.com HiAgent`） |
| 可替代的官方入口（**推荐**） | **TOP API / CLI**，无需控制台： Service=`hibot-server`，Version=`2026-04-23`，Region=`cn-north-1`，Actions=`CreateMCP / ListMCPs / BatchGetMCPs / GetMCP / UpdateMCP / DeleteMCP / TestMCPConnection`；CLI：`hibot mcps list\|get\|create\|delete\|test` | **查证过** — [hibot/internal/version/version.go](https://github.com/volcengine/hiagent-go-sdk/blob/main/hibot/internal/version/version.go)、[cmd/hibot/README.md](https://github.com/volcengine/hiagent-go-sdk/blob/main/cmd/hibot/README.md) |
| 鉴权方式（调 API） | TOP **AK/SK + WorkspaceID**（`TenantID` 由服务端从 AK/SK 解析），MCP 资源按工作空间隔离 | **查证过** — [hibot/README.md](https://github.com/volcengine/hiagent-go-sdk/blob/main/hibot/README.md) |

### 火山方舟 Ark 侧（对照，别和 HiAgent 混）

方舟是**另一条线**：它既有「客户端去连 MCP」也有「Agent 声明 MCP Server」，但没有 HiAgent 那种「MCP 服务管理」菜单。

- **Ark Managed Agents 声明 MCP Server**：在创建 Agent 时用 `mcp_servers` 数组（字段 `type`（固定 `url`）/`name`/`url`），再用 `tools[].type=mcp_toolset` + `mcp_server_name` 引用，二者必须一一对应；凭据**不在 Agent 定义里传**，而是在创建 Session 时用 `vault_ids` 引用 Vaults（`static_bearer` / `mcp_oauth`）注入。支持按工具白/黑名单开关（`default_config.enabled` + `configs`）。— **查证过** [doc 82379/2553718](https://docs.volcengine.com/docs/82379/2553718)、[Vaults 认证 82379/2553726](https://docs.volcengine.com/docs/82379/2553726)
- **Ark 云部署 MCP / Remote MCP**：在方舟上以 Remote MCP 方式调用外部 MCP 工具，鉴权一般走 `Authorization` 头；测试期需额外 header `ark-beta-mcp: true`；计费「消耗模型基础 Tokens，不收取 MCP 工具调用附加费」。— **查证过** [doc 82379/1827534](https://docs.volcengine.com/docs/82379/1827534)
- **Ark 官方「方舟文档 MCP」**：这是方舟**提供给 AI 编程助手**的只读远程 MCP Server，接入点 `https://mcp.ark-doc-resources.cn/mcp/`，协议 `MCP 2024-11-05`，传输 **Streamable HTTP**，无需 API Key。它证明方舟侧 `/mcp` 路径与 Streamable HTTP 的惯例，但**不是**「怎么把你的 MCP 接到方舟」。— **查证过** [doc 82379/2289964](https://docs.volcengine.com/docs/82379/2289964)

### 生态里最接近「企业级 MCP 网关」的其实是 AgentKit

如果你的目标是「把自建 Streamable HTTP MCP Server 接到火山的企业智能体平台」，**AgentKit 网关是目前公开文档最完整、最成熟的路径**（比 HiAgent 的公开资料完整得多）：

- 控制台入口：`AgentKit 控制台 → 左侧导航树「网关 > MCP」→「MCP 服务」页签 →「创建 MCP 服务」`；工具集：`「MCP 工具集」页签 →「创建 MCP 工具集」` — **查证过** [doc 86681/2607684](https://docs.volcengine.com/docs/86681/2607684)、[doc 86681/1844857](https://docs.volcengine.com/docs/86681/1844857)
- 三种接入方式：**部署 MCP 服务**（从零部署，支持 uvx/npx JSON 配置或 Docker 镜像，Local MCP 自动转 Remote MCP）、**导入 MCP 服务**（接入已有，后端可选 函数服务/容器服务/云服务器/固定域名）、**HTTP 转 MCP**（Swagger/OpenAPI → MCP） — **查证过** [doc 86681/1844857](https://docs.volcengine.com/docs/86681/1844857)
- **操作限制（原文）**：「AgentKit 网关仅支持 MCP 2025-03-26 及之后协议版本的 Streamable HTTP 传输，**不支持直接接入 stdio 传输 / SSE-only 的 MCP Server**。如需接入，请先使用 mcp-proxy 或同类工具将 stdio 进程或 SSE-only 服务封装为 Streamable HTTP 端点」 — **查证过** [doc 86681/2607684](https://docs.volcengine.com/docs/86681/2607684)
- AgentKit 区分「有状态 MCP 协议」与「无状态 MCP-0728 协议」，工具集对 MCP 服务做定期连接（分别 60 次/小时、24 次/小时） — **查证过** [doc 86681/2678873](https://docs.volcengine.com/docs/86681/2678873)

### 还有一条「历史存档」线：混合云智能体引擎 / 智能体套件（私有化）

火山引擎文档中心里存在一个**非公开库 85465「混合云智能体引擎 > 历史存档 > 智能体套件」**，里面有 MCP Server 章节（正文已下线，但搜索索引还能拿到摘要）。这是公开资料里**最像 HiAgent 私有化控制台**的东西：

- 菜单：`控制台 → 左侧目录树「智能体套件 > MCP Server」`；另有 `「智能广场 > MCP 广场」→「一键部署 MCP Server」`
- 自定义 MCP：支持「快速部署（粘贴配置代码，可选 uvx / npx / sse 安装方式）」与「代码构建（上传自定义 Docker 镜像）」；需部署到算力队列
- 服务详情「连接方式」页签可查看连接该服务的 **SSE URL**

— **查证过摘要**（正文 `document is not in online`，摘要来自搜索 API）[doc 85465/1802291](https://docs.volcengine.com/docs/85465/1802291)、[doc 85465/1802722](https://docs.volcengine.com/docs/85465/1802722)、[doc 85465/2023900](https://docs.volcengine.com/docs/85465/2023900)、[doc 85465/2023891](https://docs.volcengine.com/docs/85465/2023891)

> 注意：这个「智能体套件」**不等于** HiAgent 的证据不足（它归在「混合云智能体引擎」下，且已归档）。标为 **推测/待确认**。

---

## 2. 配置需要哪些字段？（URL 路径 / header / 公网 HTTPS / 内网 VPC / 白名单）

### 2.1 HiAgent（hibot）— **查证过**（SDK 字段级）

`V1MCP` / `V1MCPNewParams` 字段（[hibot/v1/mcps_types.go](https://github.com/volcengine/hiagent-go-sdk/blob/main/hibot/v1/mcps_types.go)、Python 侧 [types.py](https://github.com/volcengine/hiagent-python-sdk/blob/main/libs/hibot/hibot/v1/types.py)）：

| 字段 | 说明 |
|---|---|
| `Name` | MCP Server 名称 |
| `Description` | 描述（给模型看，影响是否选用） |
| `Transport` | 传输；streamable HTTP 取值为 **`streamable_http`** |
| `URL` | 端点（注意：服务端字段名是 `URL`，Python 属性叫 `endpoint`） |
| `Headers` | `map[string]string` / `Dict[str,str]` —— **任意 header 名**，不限 `Authorization` |
| `Env` | 环境变量（stdio 用） |
| `Command` / `Args` | 启动命令与参数（stdio 用） |
| `AuthType` | 鉴权类型 |
| `CredentialProviderID` / `CredentialConfig` | 凭据中心引用；`CredentialConfig{Name, ProviderType(如 basic), Secrets[{KeyName(默认 token), SecretType, SecretValue}]}` |
| `ToolAllowlist` / `ToolDenylist` / `ToolPrefix` | 工具白名单 / 黑名单 / 前缀 |
| `Timeout` | 超时（int64，单位文档未写明） |
| `Status` / `Source` / `WorkspaceID` | 状态 / 来源 / 工作空间 |
| 返回还有 `AgentIDs`、`CreatedAt`、`UpdatedAt` | 被哪些 Agent 引用等 |

- **URL 要不要带 `/mcp`？** 平台**没有强制后缀**，URL 就是你 Server 的完整端点。官方示例一致写成 `.../mcp`：`https://example.com/mcp`、`https://api.githubcopilot.com/mcp/`、测试里 `http://mcp.local/mcp`。→ **查证过**（示例）；「是否强制」→ **推测**（不强制，按你的 Server 来）
- **自定义 header 鉴权？** **支持，且 header 名完全自定义**：CLI `--header "Authorization=Bearer xyz" --header "X-Tenant=acme"`（可重复）。→ **查证过**
- **强制公网 HTTPS？** **未查到**官方明文要求。反证：SDK 的全链路 E2E 离线测试用的端点是 `http://mcp.local/mcp`（明文 HTTP + 内网 `.local` 主机名）→ 至少**私有化部署下不强制公网 HTTPS**。→ 反证 **查证过**；「是否强制公网」**推测：不强制**（`http://mcp.local/mcp` 见 [test_full_journey_offline.py](https://github.com/volcengine/hiagent-python-sdk/blob/main/libs/hibot/tests/test_full_journey_offline.py)）
- **支持内网/VPC？** **推测支持**（HiAgent 主打私有化「模型与数据不出域」，且 SDK 测试用内网域名）；**未查到**官方内网/VPC 配置说明。→ 内网明文 **未查到**（关键词：`HiAgent 内网 MCP`、`HiAgent VPC MCP`、`HiAgent 出网白名单`）
- **有出网白名单吗？** **未查到**（关键词：`HiAgent MCP 出网白名单`、`HiAgent MCP 网络要求`、`hibot MCP 网络`）

### 2.2 DataAgent（85637 / 私有化 86760）— **查证过**

控制台：`智能体的管理后台 → 左侧导航栏「MCP 服务管理」→ 右上角「添加 MCP 服务」`。分 **Agent 级**（仅当前智能体可用）与 **系统级**（所有智能体可用，仅系统管理员可加），两者配置项相同。

字段：`MCP 服务 & 描述`、`分类`（数据拓展 / 分析能力）、`添加方式`（**StreamableHTTP / SSE / OpenAPI**）、`URL`（原文「请确保 Data Agent 所处网络能访问该 URL」）、`Header 鉴权`（可整条删除，说明是 header 键值对）。

- URL 是否带 `/mcp`：文档未强制；OpenAPI 方式示例里 URL 形如 `http://*****/api/v0/web_search`（说明 URL 就是真实端点）→ **查证过**
- 自定义 header：`Header 鉴权` 是可增删的 header 配置 → **查证过**；**固定 `Authorization: Bearer` 还是可自定义 header 名**：文档未给出字段截图级细节 → **未查到**
- 是否强制公网 HTTPS：**未查到**；只有「确保 Data Agent 所处网络能访问该 URL」，**推测不强制公网**
- 内网/VPC：**未查到**
- **协议版本限制（原文）**：「当前添加的自定义 MCP 服务的 **MCP 协议版本需为 2025-06-18 及以后的版本**」；「当前**智能问数**功能不支持使用接入的 MCP 服务，可以在**深度研究**和**洞察报告**中使用」；私有化版自 **3.7.1** 起支持
- StreamableHTTP/SSE 方式**自动拉取**工具；**OpenAPI 方式需手工添加工具**

— **查证过** [doc 85637/2123119](https://docs.volcengine.com/docs/85637/2123119)、私有化版 [doc 86760/2116759](https://docs.volcengine.com/docs/86760/2116759)

DataAgent 的**营销互动助手**侧另有一套更细的 MCP 创建表单（`营销 Agent 控制台 → 营销互动 → MCP → 创建 MCP Server`）：`协议`（SSE 或 Streamable）、`服务来源`（注册 OpenAPI **或** 登记已有 MCP Server）、`展示名`、`描述`、`Endpoint`、**`Headers`（请求头参数配置）**、`开启鉴权`（开启则 API Key 鉴权，需在 header 中加参数）。之后在「Tools 管理」手工建 Tool（展示名 / 标识名 / 描述 / Input Schema / 请求类型仅 HTTP / 请求方式 GET·POST / 请求 URL / **请求头最多 20 个** / Request·Response Schema 用 **JSONata**）。— **查证过** [doc 85637/2477487](https://docs.volcengine.com/docs/85637/2477487)、[doc 86760/2479180](https://docs.volcengine.com/docs/86760/2479180)

### 2.3 AgentKit（作为「标准答案」对照）— **查证过**

| 问题 | 官方原文结论 |
|---|---|
| URL 格式 | 「**固定域名**：支持 HTTP / HTTPS 协议，需填写完整域名及端口号」；后端路径**可自定义**：`https://your-mcp-server.internal:8080/mcp/v1`（原文「路径可自定义」）。注意：`/mcp` 是 **AgentKit 网关侧「访问路径」默认值**，不是后端要求 |
| 自定义 header | **支持，header 名可自定义**。出站凭据 = `API Key` + `Location: Header` + `Parameter Name: Authorization`（Parameter Name 可换成别的名字）；原文亦写「例如 `Authorization: Bearer <token>`**或自定义 Header**」；CLI/API 侧 `KeyAuth.ApiKeyLocation=HEADER`、`ApiKeys[].Name` 就是 header 名 |
| 强制公网 HTTPS | **不强制**。`网络配置` 可选 **公网访问**（默认）/ **私网访问**（选 VPC + 子网） |
| 内网 / VPC | **支持**。专属网关 + 固定域名可选「网络类型：私网」，用私网出口经 TR/CEN + 专线 + Private DNS 入站 Endpoint 解析私网域名；**但「当前 AgentKit 网关暂不支持私网 IP 地址作为后端」**（要用域名）；后端为 函数服务/云服务器 时**必须开私网访问**且子网须在 AZ-a 或 AZ-b |
| 出网白名单 | 无「白名单」字样；等价能力是 安全组/出站策略 + `共享公网访问` 开关（开启=用平台公网出口；不开=用你自己 VPC 的公网出口）+ NAT 网关 SNAT/DNAT |

— **查证过** [doc 86681/1844857](https://docs.volcengine.com/docs/86681/1844857)（含 SpaceNetwork 网络配置）、[doc 86681/2607684](https://docs.volcengine.com/docs/86681/2607684)、[doc 86681/2667405](https://docs.volcengine.com/docs/86681/2667405)

---

## 3. 若不支持 MCP（或另有主推方式），自定义能力怎么接？

HiAgent **支持 MCP**（见 §1），所以这条主要是「HiAgent 同时还有哪些自定义能力」+「其他产品的替代路径」。

### 3.1 HiAgent 自身的自定义能力（**查证过**，来自官方 SDK）

1. **MCP**：`V1MCP` + Agent 绑定 `Tools: [{OfMCP:{Type:"mcp", ID: mcp.ID}}]` — [agents_types.go](https://github.com/volcengine/hiagent-go-sdk/blob/main/hibot/v1/agents_types.go)、[hibot/README.md](https://github.com/volcengine/hiagent-go-sdk/blob/main/hibot/README.md)
2. **插件（Plugin）+ 工具（Tool）**：HiAgent 经典 API（Service=`app`，Version=`2023-08-01`，`https://open.volcengineapi.com`）只有两个 Action：`GetArchivedTool` / `ExecArchivedTool`。工具**归属插件**——响应里有 `PluginID`（「工具所属插件 id」），执行时传 `PluginID`+`ToolID`+`Config`（「插件临时授权信息」）+`InputData`。→ **说明 HiAgent 的工具模型是「插件→工具」，与 MCP 并存** — [tool_types.py](https://github.com/volcengine/hiagent-python-sdk/blob/main/libs/api/hiagent_api/tool_types.py)、[tool.py](https://github.com/volcengine/hiagent-python-sdk/blob/main/libs/api/hiagent_api/tool.py)
3. **Skill**：上传 zip 制品 + 版本，绑到 Agent（`SkillVersionID`）— **查证过** [hibot/README.md](https://github.com/volcengine/hiagent-go-sdk/blob/main/hibot/README.md)
4. **工作流 / 知识库**：作为「技能」之一在编排页配置 — 第三方 [SDU 使用指南](http://aihub.sdu.edu.cn/aiic/syzn/djznt/jhscjznt.htm)
5. **「插件」在 SDK 组件里怎么调**：`Tool.init(svc, workspace_id, tool_id, credentials={...})`、`Tool.ainit(...)`，还有 SSE 类型工具的示例 — **查证过** [invoke_credentials_tool.py](https://github.com/volcengine/hiagent-python-sdk/blob/main/libs/components/examples/invoke_credentials_tool.py)、[invoke_sse_tool.py](https://github.com/volcengine/hiagent-python-sdk/blob/main/libs/components/examples/invoke_sse_tool.py)

> **HiAgent「插件」是否由 OpenAPI/Swagger 文件导入、需要什么格式的文件？** → **未查到**官方说明。
> 搜索关键词：`HiAgent 插件 创建 OpenAPI`、`HiAgent 插件 Swagger 导入`、`HiAgent 自定义插件 API 文档`、`HiAgent 插件 上传文件`、`HiAgent 工具 创建 格式`。
> HiAgent 公开材料里能确认的只有「技能 → 插件」这一层入口（第三方教程），**插件内部的文件格式规范没有公开文档**。

### 3.2 其他产品的替代路径（**全部查证过**）

| 路径 | 适合什么 | 需要什么文件/格式 | 出处 |
|---|---|---|---|
| **AgentKit「导入 MCP 服务」** | 已有自建 Streamable HTTP MCP Server（**你的场景**） | 只要一个 URL（固定域名）+ 出站凭据 | [doc 86681/2607684](https://docs.volcengine.com/docs/86681/2607684) |
| **AgentKit「HTTP 转 MCP」** | 只有 REST API，没有 MCP | **Swagger 2.0 或 OpenAPI 3.0** 文件（`.yaml`/`.json`，≤5 MiB） | [doc 86681/1844857](https://docs.volcengine.com/docs/86681/1844857)、[doc 86681/2227893](https://docs.volcengine.com/docs/86681/2227893) |
| **AgentKit「部署 MCP 服务」** | 没有现成服务，想托管 | JSON 配置（`uvx`/`npx` 的 `mcpServers` 片段）**或** Docker 镜像 + 启动命令 | [doc 86681/1844857](https://docs.volcengine.com/docs/86681/1844857) |
| **DataAgent「OpenAPI 方式」** | 在 DataAgent 深度研究/洞察报告里用 | 手工填 Tool：名称、模型调用标识（**不支持中文**）、Input Schema（必填, JSON Schema）、Output Schema、URL+请求方式、Request/Response Schema（**JSONata**） | [doc 85637/2123119](https://docs.volcengine.com/docs/85637/2123119) |
| **Ark Managed Agents** | 用方舟托管 Agent | 纯 JSON API（`mcp_servers` + `mcp_toolset` + Session `vault_ids`） | [doc 82379/2553718](https://docs.volcengine.com/docs/82379/2553718) |
| **API 网关 REST→MCP** | 把企业 HTTP 应用平滑转 MCP Server | MCP 配置文件规范 | [doc 1816086](https://docs.volcengine.com/docs/1816086)（搜索索引） |
| **函数服务（veFaaS）** | 作为 AgentKit MCP 后端 | 函数实例 | [doc 86681/1844857](https://docs.volcengine.com/docs/86681/1844857) |

---

## 4. 数据出境 / 隐私的官方说明

### 4.1 HiAgent

**未查到** HiAgent 专属的隐私政策 / 数据出境声明 —— HiAgent 没有公开文档库，产品页也没有法律条款入口。搜索关键词：`HiAgent 隐私政策`、`HiAgent 数据安全`、`HiAgent 数据出境`、`HiAgent 服务协议`、`HiAgent 专用条款`。
（HiAgent 的公开定位是**私有化部署、模型与数据不出域**，但这是第三方描述，非官方条款 → **推测**。）

### 4.2 火山方舟（Ark）— **查证过**，有明确条款

**《火山方舟大模型服务平台专用条款》**（[doc 82379/1104498](https://docs.volcengine.com/docs/82379/1104498)）关键条款原文：

- **3.7.7**：「未经您的单独同意，火山引擎**不会存储和使用您的数据来训练或优化模型**。」例外：排障（合理时间内存储，且仅在您申请排障时依委托处理）、异常调用告警数据（解除告警后不再存储审查）、聚合统计（不存储原始数据）。
- **3.7.9（与「工具/插件」直接相关）**：「您试（使）用火山引擎为您提供的**官方插件服务**时，**您的数据将被发送至插件内进行处理**，处理完成后您的数据与处理结果共同返回至您订购的大模型服务，火山引擎将按不低于行业标准的技术安全手段保护您的数据传输安全，您理解并同意，如因超出当前技术安全能力造成的您的数据安全风险，火山引擎免责。」
- **3.7.10**：您在方舟应用实验室 / Managed Agents 开发的应用，其数据知识产权归您；但发布应用前须自行完成合规评估、备案，并按《网络安全法》《个人信息保护法》《生成式人工智能管理暂行办法》等为用户提供用户协议与隐私政策。
- **3.7.11**：使用缓存存储、日志记录、AI 应用监控与调试、可观测性平台、加标识、**Managed Agents 托管服务**时，「您的数据将会存储在火山方舟平台为您提供的存储空间」；「**未经您授权，火山引擎不会访问或使用您的数据**」。
- **3.7.8**：方舟提供「高阶安全防护」（安全沙箱安全互信方案），保护您的数据不被提供给模型服务商；不使用高阶防护则遵循所接受的模型服务协议与规则。
- **3.6**：火山引擎有权对您传播的数据和内容做内容/合规/版权审查，不合规可停止接入传输、保存记录并向主管部门报告。

其他相关官方文件：
- 《火山方舟大模型服务安全白皮书》[doc 82379/2123283](https://docs.volcengine.com/docs/82379/2123283)
- 《火山引擎隐私政策》[doc 6256/64902](https://docs.volcengine.com/docs/6256/64902)
- 《MCP 云产品一键授权开通相关协议》[doc 82379/1786445](https://docs.volcengine.com/docs/82379/1786445)
- 《火山引擎数据授权使用协议》[doc 82379/1928265](https://docs.volcengine.com/docs/82379/1928265)

### 4.3 工具调用时数据经过哪些环节（**推测**，基于上面条款 + MCP 架构）

以「方舟 Managed Agents + 外部 MCP」为例：`用户输入 → 方舟 Agent 运行时/模型 → (Vaults 注入凭据) → 外部 MCP Server（数据被发送并在其中处理）→ 结果回流模型 → 结果返回用户`，中间可能落存储（缓存/日志/可观测性）。**明确被官方条款覆盖的一点是：数据会被发送到插件/工具内处理**（3.7.9）。跨境的环节**没有**找到任何官方说明（搜索 `火山引擎 数据出境` 只命中云服务器快照、私有化发版日志等无关内容）→ **未查到**。

---

## 5. OpenAPI 方式接入的具体限制

### 5.1 HiAgent 插件（OpenAPI 导入）— **未查到**

HiAgent 没有公开的插件 OpenAPI 规范文档。搜索关键词：`HiAgent 插件 OpenAPI 限制`、`HiAgent 插件 oneOf`、`HiAgent 插件 operationId`、`HiAgent 插件 请求体 application/json`、`HiAgent 插件 超时 响应大小`。

### 5.2 AgentKit（火山引擎公开文档里最接近的官方规范）— **查证过**

出自《MCP 服务 API 文件规范与示例》[doc 86681/2227893](https://docs.volcengine.com/docs/86681/2227893) + [doc 86681/2607685](https://docs.volcengine.com/docs/86681/2607685)：

| 项 | 官方原文结论 |
|---|---|
| **OpenAPI 版本** | **仅支持 Swagger 2.0 与 OpenAPI 3.0**（不支持 3.1）。示例用 `"openapi": "3.0.1"` / `"swagger": "2.0"`。文件必须含 Swagger/OpenAPI 3 元数据、`title`、`version`；**文件最大 5 MiB** |
| **operationId** | 「**建议**为每个 Operation 设置 operationId」；「`paths.<path>.<method>.operationId` 会被映射为 **MCP 工具名**，建议使用**小写下划线**风格，具备业务含义」（示例 `estimate_calories`）。→ **不是强制**，但缺 `operationId` 会导致**工具列表为空**（FAQ 原文） |
| **是否允许 oneOf** | **未查到**明确表述（文档只列了「不存在 Swagger/OpenAPI 3 语法错误」这类总要求）。→ 建议**避免** oneOf/anyOf，用扁平 object |
| **请求体是否必须 application/json** | **不是必须，但只有两种**：「支持 JSON（`application/json`）和 Form（`application/x-www-form-urlencoded`）；**不支持** `application/xml`、`multipart/form-data`、`application/octet-stream`」 |
| 请求方法 | 仅 GET / POST / PUT / DELETE / PATCH；**CONNECT / HEAD / OPTIONS / TRACE 会被忽略** |
| 参数 | array 类型在 path/query/header/cookie(formData) 中序列化为**逗号分隔值（CSV）**；参数类型**不支持 `file`** |
| **响应** | 「仅支持解析 **200、201 和 default**，且**仅解析 1 个**。同时存在时解析顺序为 **200 > 201 > default**」 |
| **响应大小 / 超时上限** | **未查到**显式数值上限。相关的确定性限制是 **网关 QPS**：共享网关有 QPS 限流（不适合生产）；专属网关可配规格，`small_x1`=1,500 QPS … `large_x4`=192,000 QPS，客户端连接数 20,000 … 2,560,000，可关联 MCP 服务+工具集总数 500 … 2,000。另有「MCP 工具集对 MCP 服务的定期连接」计入计费：有状态 MCP 协议 60 次/小时，无状态 MCP-0728 协议 24 次/小时 |

— **查证过** [doc 86681/2227893](https://docs.volcengine.com/docs/86681/2227893)、[doc 86681/2607685](https://docs.volcengine.com/docs/86681/2607685)、[doc 86681/2678873](https://docs.volcengine.com/docs/86681/2678873)

### 5.3 DataAgent OpenAPI 方式 — **查证过**

不是上传 OpenAPI 文件，而是**在控制台手工建 Tool**：`工具名称`、`模型调用标识`（**不支持中文字符**）、`描述`、`Input Schema`（**必填**，JSON Schema）、`Output Schema`（可选，JSON Schema）、`URL & 请求方式`、`Request Schema` / `Response Schema`（**JSONata** 格式，可空）。Request/Output 校验流程：Input Schema 校验 →（有 Request Schema 则）转成请求格式 → 调用 →（有 Response Schema 则）转 →（有 Output Schema 则）校验。

营销互动助手侧的 Tool 限制另有一组硬数字：**标识名**只能小写字母/数字/下划线/中划线/斜杠、必须小写字母开头、**≤100 字符**；**描述 ≤500 字符**；**请求类型仅支持 HTTP**；**请求方式支持 GET、POST**；**请求 URL ≤100 字符**；**请求头最多 20 个**。

— **查证过** [doc 85637/2123119](https://docs.volcengine.com/docs/85637/2123119)、[doc 85637/2477487](https://docs.volcengine.com/docs/85637/2477487)

---

## 6. 给「Python `mcp` SDK + streamable HTTP MCP Server」的 HiAgent 落地清单

你的 Server：`HTTP POST http://<host>:<port>/mcp`，工具 `analyze_job`、`analyze_jobs_batch`。

### 阶段 0 — 先做连通性自测（三家平台都认可这个 smoke test）

```bash
curl -i -N --max-time 20 \
  -X POST 'http://<host>:<port>/mcp' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  --data-raw '{
    "jsonrpc":"2.0","id":1,"method":"tools/list",
    "params":{"protocolVersion":"2025-06-18","capabilities":{},
              "clientInfo":{"name":"smoke","version":"1.0.0"}}}'
```

必须返回 `result.tools` 且包含 `analyze_job` / `analyze_jobs_batch`。这个 `Accept` 头是 Streamable HTTP 的硬要求（AgentKit 文档里就是这么验的：[doc 86681/2607684](https://docs.volcengine.com/docs/86681/2607684)）。

### 阶段 1 — 用官方 `mcp` SDK 的正确姿势

- `FastMCP(...).run(transport="streamable-http")`，挂载路径设成 `/mcp`（平台示例都用 `/mcp`，虽然不是强制后缀）。
- **必须实现 `tools/list` 和 `tools/call`**，并返回标准 `result.tools` / `result.content`。
- **协议版本对齐**：DataAgent 要求 **≥ 2025-06-18**（[85637/2123119](https://docs.volcengine.com/docs/85637/2123119)）；AgentKit 要求 **≥ 2025-03-26** 的 Streamable HTTP（[86681/2607684](https://docs.volcengine.com/docs/86681/2607684)）。→ **`mcp` SDK 用当前新版**，别锁老版本。
- **不要用 SSE-only，也不要只做 stdio**：AgentKit 明确不支持，要求用 `mcp-proxy` 包成 Streamable HTTP。
- **工具名与描述要自解释**：`analyze_job` / `analyze_jobs_batch` 名字 OK；`description` 要写清楚用途和适用场景（DataAgent 原文：「帮助模型理解 MCP 的能力，更好的选择 MCP 进行使用」）。
- **Input Schema 用扁平 JSON Schema**：避免 `oneOf`/`anyOf`（AgentKit 无明文支持，风险高）；array 参数会被序列化成 CSV，**别用 `file` 类型**。
- **响应体**：`tools/call` 结果尽量小、UTF-8 文本；返回结构在 200/201 语义上要稳定。**在没有查到响应大小上限前，别让 `analyze_jobs_batch` 返回几十 MB。**
- **无状态化更省成本**：AgentKit 区分有状态 MCP 与「无状态 MCP-0728」——无状态协议下工具集轮询只需 24 次/小时（有状态 60 次/小时），且无状态对网关更友好。你的 Python Server 若能做成无状态（不依赖 `Mcp-Session-Id`）更好。

### 阶段 2 — 鉴权设计（面向「可自定义 header」做最保守假设）

- 假定平台**可能**只给一个固定 header 字段名 + 一个值，也**可能**给任意键值对：
  - **推荐**：同时接受 `Authorization: Bearer <token>` **和** `X-API-Key: <token>`，任一有效即放行。
  - 这样无论对方给你 `Parameter Name: Authorization`（AgentKit 默认）还是让你自定义 `X-Tenant`（HiAgent 支持），都能接。
- HiAgent 侧还可以走**凭据中心**（`CredentialConfig{ProviderType:"basic", Secrets:[{KeyName:"token", SecretValue:...}]}`），此时**不要把 token 硬编码在 header 里**，让平台注入（[hiagent-go-sdk README](https://github.com/volcengine/hiagent-go-sdk/blob/main/hibot/README.md)）。
- **无鉴权也能跑通 smoke test**：Ark 的文档 MCP 就是「无鉴权、公开可用」；AgentKit 允许「出站凭据 = 无需凭证」。先无鉴权验证，再加鉴权。

### 阶段 3 — 按平台配置

**A. HiAgent（hibot）— 首选 SDK/CLI 路径**（因为控制台入口无公开文档）

```bash
# 一次性配置
hibot --endpoint=https://open.volcengineapi.com \
      --ak=$HIBOT_AK --sk=$HIBOT_SK \
      --workspace-id=$HIBOT_WORKSPACE_ID --region=cn-north-1 config init

# 先试连（不落库），看 ToolCount 是否 = 2
hibot mcps test --endpoint=http://<host>:<port>/mcp \
      --header "Authorization=Bearer <token>"

# 落库创建
hibot mcps create --name=job-analyzer \
      --transport=streamable-http \
      --endpoint=http://<host>:<port>/mcp \
      --header "Authorization=Bearer <token>"

# 再创建 Agent 并绑定（SDK: Tools=[{OfMCP:{Type:"mcp", ID: mcp.ID}}]）
```
- 注意 `--transport=streamable-http`（CLI 字面量）对应服务端值 **`streamable_http`**。
- URL **必须是你 Server 的完整路径**（含 `/mcp`）。
- 用 `ToolAllowlist`/`ToolDenylist` 精确放行 `analyze_job`、`analyze_jobs_batch`。
- AK/SK + WorkspaceID 是必需的；`TenantID` 服务端从 AK/SK 推。

**B. 若被要求走控制台**：目前公开资料只能确认 `Agent 模块 → 创建智能体 → 编排页 → 技能面板`（插件/工作流/知识库，[SDU](http://aihub.sdu.edu.cn/aiic/syzn/djznt/tgcjcjznt.htm)）。**MCP 的具体菜单项务必找火山售前/技术支持确认**（这是本调研唯一必须问人的点）。

**C. 备选：AgentKit 网关（公开文档最全，生产推荐）**
1. `AgentKit 控制台 → 网关 > MCP → MCP 服务 → 创建 MCP 服务`
2. `接入方式 = 导入 MCP 服务`；`协议支持 = Streamable HTTP`；`访问路径` 默认 `/mcp`
3. `所属网关`：生产选**专属网关**（共享模式有 QPS 限流）
4. `后端配置 → 服务类型 = 固定域名`，域名填 `http://<host>:<port>/mcp`（HTTP 可以，端口要写全）
   - 若你的 Server 在私网：专属网关 + 固定域名 `网络类型 = 私网`，先做 TR/CEN 打通 + Private DNS 入站 Endpoint，**并且必须用域名不能用裸 IP**
5. `出站凭据 = API Key`，`Location=Header`，`Parameter Name=Authorization`，值填你的 token
6. `入站身份认证 = API Key`（生产必须开；API Key 模式最多 1 个）
7. 若要给多个 Agent 复用 → 建 `MCP 工具集`，把两个工具勾进去；`调用模式` 工具少（≤20）选「全量返回」

**D. 备选：DataAgent**（仅在「深度研究 / 洞察报告」里用，智能问数不支持）
1. `智能体管理后台 → MCP 服务管理 → 添加 MCP 服务`
2. `添加方式 = StreamableHTTP`，填 URL `http://<host>:<port>/mcp`，配 `Header 鉴权`
3. 系统会**自动拉取**工具，无需手填 Input Schema
4. 确认你的 Server 协议版本 ≥ **2025-06-18**

**E. 备选：方舟 Managed Agents**
```json
{
  "mcp_servers": [{"type":"url","name":"job","url":"http://<host>:<port>/mcp"}],
  "tools": [{"type":"agent_toolset_20260701"},
            {"type":"mcp_toolset","mcp_server_name":"job",
             "default_config":{"enabled":false},
             "configs":[{"name":"analyze_job","enabled":true},
                        {"name":"analyze_jobs_batch","enabled":true}]}]
}
```
凭据在 **Session** 创建时用 `vault_ids` 注入（`static_bearer`），不要写进 Agent 定义。

### 阶段 4 — 上线前必查

- [ ] `tools/list` 返回 2 个工具，`analyze_job` / `analyze_jobs_batch` 名称与描述准确
- [ ] 用 `Accept: application/json, text/event-stream` 的 POST 能通（SSE 流式）与不通（纯 JSON）**两种**客户端都能工作
- [ ] 无状态：不依赖 `Mcp-Session-Id` 也能响应 `tools/call`
- [ ] `analyze_jobs_batch` 的返回体大小可控（未查到上限，先按「小」设计；必要时分批 + 分页参数）
- [ ] 工具失败时返回结构化错误文本，不抛栈
- [ ] Input Schema 无 `oneOf`/`anyOf`、无 `file` 类型
- [ ] 鉴权同时兼容 `Authorization: Bearer` 与 `X-API-Key`
- [ ] 若私网：域名可解析 + 端口放通 + **不能用裸 IP**
- [ ] 协议版本 ≥ 2025-06-18（DataAgent 硬要求）
- [ ] 数据合规：确认「工具调用会把数据发送到你的 MCP Server 内处理」已被你的 DPA/条款覆盖（Ark 条款 3.7.9 明确这一点）

---

## 附：本次查证过的全部文档 URL

**HiAgent（官方 SDK）**
- https://github.com/volcengine/hiagent-go-sdk/blob/main/hibot/README.md
- https://github.com/volcengine/hiagent-go-sdk/blob/main/hibot/v1/mcps_types.go
- https://github.com/volcengine/hiagent-go-sdk/blob/main/hibot/v1/mcps.go
- https://github.com/volcengine/hiagent-go-sdk/blob/main/hibot/v1/types.go
- https://github.com/volcengine/hiagent-go-sdk/blob/main/hibot/v1/agents_types.go
- https://github.com/volcengine/hiagent-go-sdk/blob/main/hibot/internal/version/version.go
- https://github.com/volcengine/hiagent-go-sdk/blob/main/cmd/hibot/README.md
- https://github.com/volcengine/hiagent-go-sdk/blob/main/cmd/hibot/examples/README.md
- https://github.com/volcengine/hiagent-python-sdk/blob/main/libs/hibot/README.md
- https://github.com/volcengine/hiagent-python-sdk/blob/main/libs/hibot/hibot/v1/types.py
- https://github.com/volcengine/hiagent-python-sdk/blob/main/libs/hibot/hibot/_version.py
- https://github.com/volcengine/hiagent-python-sdk/blob/main/libs/api/hiagent_api/tool_types.py
- 第三方控制台教程：http://aihub.sdu.edu.cn/aiic/syzn/djznt/jhscjznt.htm 、 http://aihub.sdu.edu.cn/aiic/syzn/djznt/tgcjcjznt.htm

**AgentKit（86681）**
- https://docs.volcengine.com/docs/86681/2607684 接入已有 MCP Server 到 AgentKit 网关
- https://docs.volcengine.com/docs/86681/1844857 创建 MCP 服务
- https://docs.volcengine.com/docs/86681/1844858 创建 MCP 工具集
- https://docs.volcengine.com/docs/86681/2607685 将现有 REST API / OpenAPI 接入为 MCP 工具
- https://docs.volcengine.com/docs/86681/2227893 MCP服务API文件规范与示例
- https://docs.volcengine.com/docs/86681/2667405 MCP 服务通过私网域名方式接入网关实例
- https://docs.volcengine.com/docs/86681/2678873 网关模式和 MCP 请求计数规则
- https://docs.volcengine.com/docs/86681/1913810 CreateMCPService
- https://docs.volcengine.com/docs/86681/2625448 管理 MCP（DeliveryAI）

**DataAgent（85637 / 私有化 86760）**
- https://docs.volcengine.com/docs/85637/2123119 MCP 服务管理
- https://docs.volcengine.com/docs/86760/2116759 MCP 服务管理（私有化）
- https://docs.volcengine.com/docs/85637/2477487 MCP（营销互动助手）
- https://docs.volcengine.com/docs/86760/2479180 MCP（营销互动助手，私有化）

**方舟 Ark（82379）**
- https://docs.volcengine.com/docs/82379/2553718 MCP（Managed Agents）
- https://docs.volcengine.com/docs/82379/1827534 云部署 MCP / Remote MCP
- https://docs.volcengine.com/docs/82379/1539085 MCP 简介
- https://docs.volcengine.com/docs/82379/2289964 方舟文档 MCP
- https://docs.volcengine.com/docs/82379/2553719 Tools
- https://docs.volcengine.com/docs/82379/2553726 使用 Vaults 认证

**混合云智能体引擎 / 智能体套件（85465，历史存档，正文下线）**
- https://docs.volcengine.com/docs/85465/1802291 MCP Server 概述
- https://docs.volcengine.com/docs/85465/1802722 创建并部署自定义 MCP Server
- https://docs.volcengine.com/docs/85465/2023900 管理 MCP Server
- https://docs.volcengine.com/docs/85465/2023891 一键部署 MCP Server

**法律/隐私**
- https://docs.volcengine.com/docs/82379/1104498 火山方舟大模型服务平台专用条款
- https://docs.volcengine.com/docs/82379/2123283 火山方舟大模型服务安全白皮书
- https://docs.volcengine.com/docs/6256/64902 火山引擎隐私政策
- https://docs.volcengine.com/docs/82379/1786445 MCP 云产品一键授权开通相关协议
- https://docs.volcengine.com/docs/82379/1928265 火山引擎数据授权使用协议
