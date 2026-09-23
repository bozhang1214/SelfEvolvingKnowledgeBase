---
title: 招聘渠道采集状态跟踪
layer: 运维层
owner: SEKB Team
status: active
version: v1.0.0
last-updated: 2026-09-09
based-on-commit: f6eea56
related: [docs/tech/09-OBSERVABILITY]
---

# 招聘渠道采集状态跟踪

> 用途：记录各招聘渠道的采集接口可用性与登录需求，便于后续接入职位采集。
> 默认筛选条件：**Agent 相关职位、北京地区、月薪 ≥ 50K（即 50K×14）**。
> 已打通的猎聘渠道保留。
> 最后更新：2026-09-04（BOSS 扫码登录已打通；采集仍被反爬签名阻断，见下方跟踪项）

## 采集接口汇总

| # | 公司 | 页面 URL | JSON 接口 | 方法 | 需要登录？ | 备注 |
|---|------|----------|-----------|------|-----------|------|
| 0 | 猎聘 | liepin.com/zhaopin | `api-c.liepin.com/api/com.liepin.searchfront4c.pc-search-job` | POST | 否 | ✅ 已打通；北京码 `010`/全国 `410`；薪资需客户端过滤 |
| 1 | 字节 | jobs.bytedance.com/experienced/position | `jobs.bytedance.com/api/v1/search/job/posts` | POST | 否 | 社招 `recruitment_id_list:["101"]`；北京 `location_code_list:["CT_11"]`；实测 code:0/count:1059 |
| 2 | 阿里 | talent.alibaba.com | `talent.alibaba.com/position/search` | POST | 否 | 自签 XSRF（`X-XSRF-TOKEN`=Cookie `XSRF-TOKEN`）；`key` 关键词、`regions:"北京"` |
| 3 | 腾讯 | careers.tencent.com/search.html | `careers.tencent.com/tencentcareer/api/post/Query` | GET | 否 | `keyword/pageIndex/pageSize/language=zh-cn`；实测 Count:203 |
| 4 | 百度 | talent.baidu.com/jobs/social-list | `talent.baidu.com/httservice/getPostListNew` | POST | 否 | 表单编码 + 必须带 Referer；`workPlace=1100`(北京)、`recruitType=SOCIAL`、pageSize≤20 |
| 5 | 小米 | xiaomi.jobs.f.mioffice.cn | `xiaomi.jobs.f.mioffice.cn/api/v1/search/job/posts` | POST | 否 | 同字节 ATSX，社招省略 portal-channel |
| 6 | 小红书 | job.xiaohongshu.com/social/position | `job.xiaohongshu.com/websiterecruit/position/pageQueryPosition` | POST | 否 | `positionName` 关键词；城市参数被忽略→客户端过滤 |
| 7 | 大疆 | apply.careers.dji.com | `app.mokahr.com/api/outer/ats-apply/website/jobs/v2?orgId=dji` | POST | 否 | mokahr；响应 AES-128-CBC 密文（key=necromancer/iv=aesIv）；岗位多在深圳，北京过滤后偏少 |
| 8 | DeepSeek(高飞/深度求索) | app.mokahr.com/social-recruitment/high-flyer | `app.mokahr.com/api/outer/ats-apply/website/jobs/v2?orgId=high-flyer` | POST | 否 | mokahr；AES 解密；✅ 已打通（实测 7 个 Agent 职位）；岗位多在「浙江/北京」，城市前缀「北京」会漏掉，建议用「不限」 |
| 9 | 京东 | zhaopin.jd.com | （社招 HTML 壳 + 密文 XHR + 动态 sign） | — | 是（需逆向签名） | 暂不可免登录自动化 |
| 10 | BOSS直聘 | zhipin.com/web/geek/jobs | （HTML 壳） | — | 是（登录已打通） | 扫码登录✅；采集❌ 被 `code:37` 反爬签名阻断，见下方跟踪项 |

## 需要你提供登录信息的渠道

> 优先用免登录渠道。下表里京东/BOSS 若后续确需采集，需要你提供登录信息。

| 公司 | 是否需要登录 | 需要提供什么 | 你填写 |
|------|-------------|-------------|--------|
| BOSS直聘 | 是 | ~~登录 Cookie~~ 已通过「BOSS 扫码登录」按钮自动获取并持久化 | 无需手动提供 |
| 京东社招 | 是（需逆向 sign，较复杂） | Cookie + 逆向签名方案 | 待填 |

## 默认筛选条件

- 前端筛选框默认**全部「不限」**（工作地/关键字/薪资），用户自行选择。
- 关键词：空则用 `Agent`（`job.default_keyword`）；支持空格拼接多个关键词。
- 城市：客户端按 `city` 前缀过滤（「北京」前缀匹配不到「浙江/北京」这类多城市职位，注意）。
- 薪资：`salary` 为空（面议/多数源）时保留；猎聘/明文源有薪资时按 `min_salary_k` 过滤。

## BOSS 直聘关注重点（登录后接入时用）

> 大厂（字节/阿里/腾讯/百度/小米/京东/大疆/小红书/DeepSeek）已通过各自官网渠道覆盖，BOSS 上**重点抓非大厂的 AI/智能驾驶/具身智能创业公司**。

- **重点公司**：智谱、阶跃星辰、月之暗面（Kimi/Moonshot AI）、MiniMax、零一万物、百川智能、面壁智能、无问芯穹、昆仑万维、商汤、旷视、地平线、Momenta、小马智行、文远知行、图森未来、宇树科技、智元机器人、银河通用、星动纪元 等
- **关键词**：AI、大模型、Agent、智能驾驶、具身智能、机器人、自动驾驶
- **排除**：字节跳动、阿里巴巴、腾讯、百度、小米、京东、大疆、小红书、深度求索

## MCP / 其他免登录方式调研（需登录的京东/BOSS）

| 渠道 | 是否有 MCP/公开接口 | 结论 |
|------|-------------------|------|
| BOSS直聘 | 有 MCP（[boss-mcp-job-hunting](https://github.com/lemonskiller/boss-mcp-job-hunting)、[mcp-bosszp](https://github.com/mucsbr/mcp-bosszp)） | 但都基于 **Playwright + 需你登录后的 Cookie**（`import_boss_cookies`）或扫码；**无真正免登录方式** |
| BOSS直聘/智联/51job 聚合 | 有 [mergedao/mcp-jobs](https://github.com/mergedao/mcp-jobs)（猎聘/Boss/智联/51job） | 仍是聚合 MCP，BOSS/智联/51job 各自受登录/签名/WAF 限制，**不解决免登录** |
| 京东社招 | 未找到公开 MCP | 需逆向动态 sign，无免登录方式 |

> 结论：BOSS/京东**没有免登录的公开 API 或 MCP**。BOSS 登录本身已通过扫码解决（见下），卡点是搜索接口签名。

## BOSS 直聘采集跟踪项（进行中）

> 登录 ✅ 已打通；采集 ❌ 卡在反爬签名。这是当前唯一未跑通的渠道。

**已完成：**
- 扫码登录全链路（逆向自 BOSS 登录页 `user-login` chunk 的 `BossAppScan` 类）：
  `randkey → 二维码(内容=qrId) → scan → getSecondKey(换第二张码) → scanSecond → scanLogin → dispatcher`
- 二维码是标准 QR（内容为 `bosszp-xxx` 字符串），BOSS App 能扫；两步扫码（双码验证）在前端自动切换。
- 登录成功后拿到 4 个登录 Cookie（`wt2`/`wbg`/`zp_at`/`bst`）并持久化到浏览器服务 `/data/cookies.json`。
- Cookie 有效：登录态接口 `resume/restrict/list.json` 返回 `code:0`。

**卡点（待解决）：**
- 搜索接口 `https://www.zhipin.com/wapi/zpgeek/search/joblist.json` 返回
  `{"code":37,"message":"您的环境存在异常.","zpData":{"name":"2330c665","seed":"..."}}`
  —— 需要按 BOSS 混淆 JS 计算 `sign` 参数（请求参数 + seed + 设备指纹 的哈希），与登录 Cookie 无关。
- 无头 Chromium 打开搜索页被反爬清空成 `about:blank`（`verify-sdk` + 窗口关闭检测）。

**下一步选项（未定）：**
1. 逆向 `sign`/`__zp_stoken__` 签名算法（工作量大、可能被风控升级打断）；
2. 用真实浏览器（含指纹）跑登录页拿 `fp`，再配合签名；
3. 你本地浏览器 F12 抓一次搜索请求，把带 `sign` 的完整 URL 发来，据此判断签名结构。

## 实现状态

- [x] 猎聘（已打通）
- [x] 字节 / 阿里 / 腾讯 / 百度 / 小米 / 小红书（6 家免登录明文 JSON，已接入）
- [x] 大疆 / DeepSeek（mokahr AES 解密，已接入）
- [x] BOSS 直聘：登录（扫码）已打通
- [ ] BOSS 直聘：采集（搜索接口 sign 签名逆向）—— 跟踪项，见上
- [ ] 京东社招：需逆向 sign（暂缓，建议手动粘贴 JD）
- [x] **BOSS / 智联：浏览器采集插件（路线 C，2026-09-22 新增）** —— 半自动、人工触发，见下

---

## 路线 C：浏览器采集插件（BOSS / 智联）

> **背景**：BOSS 的 `sign` 逆向工作量大且会被风控升级打断；实测也确认 **BOSS / 智联 / 前程无忧
> 都没有可用的免登录 JSON 接口**（与开源项目 [ai-job-search-cn](https://github.com/rockbenben/ai-job-search-cn)
> 的实测结论一致）。
> 所以改为**读「你自己浏览器里已经渲染好的页面」**——不碰接口签名，也不新增风控暴露。

### 脚本与位置

`scripts/sekb-job-collector.user.js`（油猴脚本，Tampermonkey / Violentmonkey 均可）。

装法：浏览器装 Tampermonkey → 新建脚本 → 粘贴该文件内容 → 保存。

### 为什么它比逆向签名安全

| | 逆向 `sign` | 本插件 |
|---|---|---|
| 发出的请求 | 新增大量接口请求 | **0**（只读 DOM） |
| 风控暴露 | 高（code:37 / `_security_check`） | 与人打开页面等价（`read` 类操作，间隔为 0） |
| BOSS 改版 | 签名算法失效 → 整条链路挂 | 选择器失效 → 改几行选择器 |
| 维护成本 | 高（跟混淆 JS 斗） | 低 |

参考实测：`rockbenben/ai-job-search-cn` 的 `workflows/reference/cdp-portals.md` 里，
`read`（纯读 DOM）的间隔要求是 **0 秒**，而 `navigate` ≥8 秒、`fetch` ≥4 秒。

### 用法（三步）

1. 在 BOSS / 智联**手动搜索、翻页**（跟平时找工作一样）；
2. 每看完一页，点右下角面板的「**采集当前页**」（把这一页已渲染的职位收进本机）；
3. 采够了点「**导出 JSON**」（或「复制 JSON」）→ 到 SEKB「职位收集」页导入该 JSON。

采集记录存在浏览器 `localStorage`，跨页累积，可随时「清空」。

### 支持站点与选择器（实测来源：cdp-portals.md，2026-08）

| 站点 | 入口 | 卡片选择器 | 字段选择器 |
|---|---|---|---|
| **BOSS 直聘** | `zhipin.com/web/geek/jobs` | `.job-card-wrap`（**不是** `.job-card-wrapper`） | `.job-name` / `.company-name` / `.salary` / `.job-area`；链接 `a[href*="/job_detail/"]` |
| **智联招聘** | `zhaopin.com/recommend`、`zhaopin.com/sou/*` | `.joblist-box__item.clearfix` | `.jname` / `.cname` / `.sal` / `.shrink-0`（城市·区）/ `.dc`（行业）/ `.tag`；链接 `a[href*="jobdetail"]` |

> 站点改版时**先失效的是精确类名**，脚本对每个字段都配了退化选择器；若整体失效，
> 按上表更新选择器即可（脚本里 `ADAPTERS` 一处定义）。

### 已知边界

- **列表页拿不到 JD 全文**（`jd_text` 为空）——导入后可在 SEKB 侧用「刷新 JD」补，或做批量分析时按需抓取；
- **半自动**：需要人手点「采集当前页」，不翻页、不轮询（这是设计选择，不是缺陷）；
- **撞到验证码/风控提示就停手**，别硬闯（账号安全铁律见 cdp-portals.md）。

### 配套的 SEKB 侧改动

`backend/app/services/job_service.py` 的 `parse_job_files` 现在**支持结构化 JSON**：
`.json` 文件走 `_parse_json_jobs`（兼容 `{"jobs":[...]}` 与裸数组，字段名宽容：
`title`/`position`、`job_url`/`url`、`jd_text`/`jd`），**保留公司/薪资/城市/链接**；
坏 JSON 退回文本解析（文件名为标题），不会整条丢掉。

---

## 渠道调研结论：脉脉

**结论：不接入**（无免登录数据源）。

实测 `maimai.cn` 首页：web 端是**B 端企业服务**（人才银行 / 人才智库 / 数字营销 / 拓客通 /
专家网络 / 企业号）+ 职场社区；面向求职者的职位搜索在 **App / 登录态**内，**web 端没有
公开的职位列表页**。另外：

- 脉脉自己的招聘用的是飞书 ATS（`maimai.jobs.feishu.cn`）——那是**脉脉在招人**，不是职位库；
- 开源项目 ai-job-search-cn 的渠道实测表里**没有脉脉**（它覆盖 BOSS / 智联 / 前程无忧 / 猎聘）。

若将来你愿意在**登录后**提供脉脉职位页的具体 URL 与 DOM 结构，可以照 BOSS/智联 同样的方式
在 `ADAPTERS` 里加一个适配器（脚本已按「一站一适配器」组织，加站点不用改主流程）。

## 渠道调研结论：智联招聘

**结论：可用，走浏览器**（已在本轮接入插件）。

- **`/recommend` 需要登录**：未登录会 302 到 `passport.zhaopin.com/login`（实测确认）。
- **免登录搜索页可用（2026-09-23 服务器实测复核）**：关键是用对 URL 形式 ——
  **城市码放 `jl` 段、裸关键词放 `kw` query**：

  ```
  https://www.zhaopin.com/sou/jl530/?kw=<URL编码的关键词>
    → 302 到 /sou/jl530/kw<站点自编码>/p1
  ```

  站点**自己完成关键词编码**（`FDE`→`kw01300H008K`、`前沿部署`→`kwA96MPFSGT1VN4`），
  **调用方不需要知道编码规则**。实测 `jl530`（北京）+ `前沿部署` → 20 张卡片，
  城市字段全部为「北京·xx」，标题为「「前沿部署招聘 2026年**北京**前沿部署招聘信息」」。
  ⚠️ 早前记录的 `?kw=…&city=530`（城市放 query）是**退化形式**，会掉到通用页并混入外地岗 ——
  那个「2/20 命中率」就是它造成的误判，**不是智联不支持中文/免登录**。

- **卡片选择器（2026-09-23 复核，早前记录已过期）**：

  | 用途 | 现在有效的选择器 | 早前记录的（已失效） |
  |---|---|---|
  | 卡片容器 | `.joblist-box__item.clearfix` ✅ | 同（仍有效） |
  | 职位标题 | **`.jobinfo__name`** | ~~`.jname`~~（0 命中） |
  | 公司 | **`.companyinfo__name`** | ~~`.cname`~~（0 命中） |
  | 薪资 | **`.jobinfo__salary`** | ~~`.sal`~~ |
  | 城市·区 | **`.jobinfo__other-info-item span`**（每卡 3 个：地点/经验/学历） | ~~`.shrink-0`~~ |
  | 标签 | `.joblist-box__item-tag` | ~~`.tag`~~ |
  | 链接 | `a[href*="jobdetail"]` ✅（形如 `jobs.zhaopin.com/CC…J….htm`） | 同 |

- **另有明文 JSON 接口层**（新发现）：新版页面会 `POST https://fe-api.zhaopin.com/c/i/search/positions`，
  **响应是明文 JSON**（`{code,data:{count:100,list:[…]}}`，字段含 `companyName`/`salary60`/
  `workCity`/`positionUrl`/`jobId`/`education`）。请求体是签名 blob，所以不能直接构造；
  可行姿势是「让浏览器自己发请求、拦截响应」。但它走的是新版 `/jobs` 页，
  **实测按 URL 直达不会触发**，且搜索框流程会丢城市过滤（落到 `jl489`、混入外地岗）。
  结论：**当前实现仍以经典页 DOM 为主**，API 拦截作为备选（抗改版更强，但触发路径待解）。
- robots：只禁 `/user/*` `/source/*` `/install/*` `/data/*` 与带 `utm_*` 的 URL；
  `/sou/…` 未被禁。
- **已实现为自动采集源**（2026-09-23）：`browser-service/app.py` 注册 `@_register_scraper("zhaopin")`，
  `backend/app/agents/job/collector.py` 新增 `ZhaopinBrowserSource`（与 `BossBrowserSource` 共用
  `_BrowserSource` 基类）。**免登录即可采**，无需导入 Cookie。
- **城市码只收录北京**：`jl` 段用的码**不是**通用城市码。实测 `538/763/765/653/801/635/736/854/639/531/551`
  全部返回 0 张卡片；城市落地页（`/shanghai/` 等）里出现的 `jl` 码是**共享导航链接**（538 同时出现在
  上海页和深圳页），不能用作城市码。因此 `ZHAOPIN_CITY_CODES` 只保留已实测的 `北京=530`，
  **未收录城市直接跳过并记日志**（避免静默采到外地岗）。改这个表前请按文件内注释的方法复验。
  > ⚠️ 另有一个未完全确认的点：服务器 IP 在北京，站点**可能按 IP 定位**，`jl=530` 未必真的在做过滤。
  > 但无论机制如何，**从本服务器采到的结果标题与卡片城市都是北京**，对北京求职目标可用。
- **未解**：分页。把重定向后的 `/p1` 直接改成 `/p2` 实测返回 0 张卡片（会被打回通用页），
  需要点击分页控件或找到正确的分页参数。**当前上限 20 条/关键词**。
- **未解**：JD 正文。经典页卡片不含职位描述，`jd_text` 为空，需要再进详情页抓取（会显著变慢）。
