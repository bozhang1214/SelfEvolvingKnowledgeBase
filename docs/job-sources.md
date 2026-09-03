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

## 实现状态

- [x] 猎聘（已打通）
- [ ] 字节 / 阿里 / 腾讯 / 百度 / 小米 / 小红书（6 家免登录明文 JSON，待接入）
- [ ] 大疆 / DeepSeek（mokahr AES 解密，待接入，需加 AES 依赖）
- [x] 京东 / BOSS（文档标注需登录/逆向，不接入）
