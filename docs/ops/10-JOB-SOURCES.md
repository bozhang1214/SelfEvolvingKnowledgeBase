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
