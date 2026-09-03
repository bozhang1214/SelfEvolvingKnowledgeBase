# 招聘渠道采集状态跟踪

> 用途：记录各招聘渠道的采集接口可用性与登录需求，便于后续接入职位采集。
> 默认筛选条件：**Agent 相关职位、北京地区、月薪 ≥ 50K（即 50K×14）**。
> 已打通的猎聘渠道保留。
> 最后更新：2026-09-03（探测子代理逐家 curl 实测）

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
| 7 | 大疆 | apply.careers.dji.com | `app.mokahr.com/api/outer/ats-apply/website/jobs/v2?orgId=dji` | POST | 否 | mokahr；响应 AES-128-CBC 密文（key=necromancer/iv=aesIv） |
| 8 | DeepSeek(高飞) | app.mokahr.com/social-recruitment/high-flyer | `app.mokahr.com/api/outer/ats-apply/website/jobs/v2?orgId=high-flyer` | POST | 否 | mokahr；同上 AES 解密 |
| 9 | 京东 | zhaopin.jd.com | （社招 HTML 壳 + 密文 XHR + 动态 sign） | — | 是（需逆向签名） | 暂不可免登录自动化 |
| 10 | BOSS直聘 | zhipin.com/web/geek/jobs | （HTML 壳） | — | 是（登录墙 + `__zp_stoken__` 签名） | 暂不可免登录自动化 |

## 需要你提供登录信息的渠道

> 优先用免登录渠道。下表里京东/BOSS 若后续确需采集，需要你提供登录信息。

| 公司 | 是否需要登录 | 需要提供什么 | 你填写 |
|------|-------------|-------------|--------|
| BOSS直聘 | 是 | 登录后的 Cookie（`__zp_stoken__` 等） | 待填 |
| 京东社招 | 是（需逆向 sign，较复杂） | Cookie + 逆向签名方案 | 待填 |

## 默认筛选条件

- 关键词：`Agent`（`job.default_keyword`）
- 城市：北京（客户端按 `city` 前缀过滤；猎聘码 `010`）
- 薪资：月薪 ≥ 50K（客户端解析 `job.salary` 字符串过滤；猎聘服务端薪资字段不生效）

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

> 结论：BOSS/京东**没有免登录的公开 API 或 MCP**。要接入 BOSS，需你提供登录 Cookie（BOSS 的 `__zp_stoken__` 等），可用 `boss-mcp-job-hunting` 的 `import_boss_cookies` 方式导入；京东建议放弃自动化、改用手动粘贴 JD。

## 实现状态

- [x] 猎聘（已打通）
- [x] 字节 / 阿里 / 腾讯 / 百度 / 小米 / 小红书（6 家免登录明文 JSON，已接入）
- [ ] 大疆 / DeepSeek（mokahr AES 解密，待接入，需加 AES 依赖）
- [x] 京东 / BOSS（需登录/逆向，文档标注；BOSS 需你提供 Cookie 后可用 MCP 接入）
