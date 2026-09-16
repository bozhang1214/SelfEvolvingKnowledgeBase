---
title: BOSS 直聘 / 京东社招 Cookie 获取手册
layer: 运维层
owner: SEKB Team
status: active
version: v1.0.0
last-updated: 2026-09-09
based-on-commit: f6eea56
related: [docs/tech/09-OBSERVABILITY]
---

# BOSS 直聘 / 京东社招 · Cookie 获取与操作手册

> 用途：给用户（张博）提供「如何提供登录信息」的逐步操作指引，便于我们接入 BOSS 直聘采集。
> 京东社招因需逆向签名，本手册建议放弃自动化、改手动粘贴 JD。
> 最后更新：2026-09-04
> **更新要点**：BOSS 扫码登录已跑通（前端「BOSS 扫码登录」按钮自动完成两步扫码 + 保存 Cookie），
> 无需再手动复制 Cookie；当前 BOSS 的卡点从「登录」变成了「搜索接口 sign 签名」（见 `docs/ops/10-JOB-SOURCES.md` 跟踪项）。

---

## 一、BOSS 直聘：获取登录 Cookie

### 先回答：手机端的 Cookie 能用吗？

| 场景 | 能否用 | 说明 |
|------|--------|------|
| BOSS **手机 App** | ❌ 不能 | App 用的是原生登录 token，不是网页 Cookie；采集用的是网页（Playwright 浏览器）方案，需要**网页 Cookie** |
| BOSS **手机浏览器**（网页版） | ⚠️ 能用但难提取 | 手机浏览器抓 Cookie 很麻烦，不推荐 |
| BOSS **电脑浏览器**（网页版） | ✅ 推荐 | 用开发者工具或 Cookie 扩展复制 Cookie（F12 被禁时的替代方案见下文） |

**结论：请用电脑浏览器登录 BOSS 网页版来拿 Cookie。**

### 电脑浏览器抓 Cookie 步骤（Chrome / Edge 均可）

1. 电脑打开浏览器，访问 https://www.zhipin.com/web/geek/jobs （BOSS 直聘网页版）。
2. **登录**：右上角登录，用 BOSS App **扫码登录**（或手机号+验证码）。
3. 登录成功后，按 **F12** 打开开发者工具，切到 **Network（网络）** 标签页。
4. 刷新页面（F5），Network 里会出现一堆请求。
5. 点任意一个 `zhipin.com` 的请求（例如 `jobs` 或 `wapi/zpgeek/...`），在右侧 **Headers（标头）** 里找到 **Request Headers（请求标头）** → **Cookie:** 那一行。
6. **全选复制** Cookie 那一整行的值（很长一串，形如 `__zp_stoken__=...; wpt=...; __c=...; ...`）。
7. 把复制的 Cookie 内容贴到本手册末尾的「我提供的 Cookie」栏（或直接发给我）。

> 注意：Cookie 里含 `__zp_stoken__` 这个登录态关键字段，请**整段完整复制**，不要只复制部分。

### BOSS 打不开 F12 怎么办？—— 三种替代方案（推荐方案一）

BOSS 网页版有反调试（禁用 F12 / 右键），直接用 F12 抓不到。改用下面任一方法：

**方案一（最省事，无需复制 Cookie）：用 MCP 自带扫码登录**

`boss-mcp-job-hunting` 这个 MCP 内置了扫码登录流程，你只需在手机 BOSS App 扫码，它会自动把登录态 Cookie 存到自己的浏览器 profile 里，**完全不用你手动抓 Cookie**：
1. 启动 MCP，调用 `start_boss_qr_login()` → 生成一张登录二维码；
2. 用手机 BOSS App 扫这张二维码确认登录；
3. 调用 `complete_boss_qr_login()` 等它把登录态保存下来；
4. 之后直接调 `search_boss_jobs(keyword="...", city="北京", ...)` 搜职位。

**方案二（手动抓 Cookie 的变通）：先开控制台再进 BOSS**

BOSS 的反调试只拦截「页面加载后」按 F12。换个顺序即可绕过：
1. 先开一个**空白新标签页**；
2. 在这个空白页上按 **F12**（或 Mac `Cmd+Option+I` / Windows `Ctrl+Shift+I`）打开开发者工具；
3. 保持控制台开着，在地址栏输入 `https://www.zhipin.com/web/geek/jobs` 回车进入 BOSS 并登录；
4. 之后照上面的「Network → 请求头 → Cookie」复制即可。

**方案三（用浏览器扩展导出 Cookie）：**

装一个 Cookie 管理扩展（如 **Cookie-Editor**，Chrome/Edge 商店免费），登录 BOSS 后点扩展图标 → 一键「Export」→ 选「Header String」格式，直接得到可粘贴的 Cookie 串。

### 我们拿到 Cookie 后会怎么用

- 现在服务器上已部署了一个**通用浏览器登录/采集服务（sekb-browser）**，它提供：
  - `POST /cookies`：导入某站点的 Cookie（持久化保存，也可手动导入备用）；
  - `POST /login/qr/start` + `POST /login/qr/status`：扫码登录（BOSS 两步扫码，**已跑通**，前端按钮直接调用）；
  - `POST /scrape`：用已登录态采集职位（BOSS 为首个站点，可插拔扩展其它站）。
- 你的 Cookie 只会放在**服务器持久化卷**里，不会硬编码进代码、不会提交到 Git。
- Cookie 会**过期**（一般几天到几周），失效后**重新点一次前端「BOSS 扫码登录」**即可，不用手动复制。

### 扫码登录（已跑通，主路径）

BOSS 的「APP扫码登录」已完整逆向并跑通，前端「BOSS 扫码登录」按钮会自动完成：
1. 生成标准二维码（内容 = `qrId`）→ 手机 BOSS App「扫一扫」；
2. 扫完第一张自动换第二张码（BOSS 双码验证）→ 再扫一次；
3. 在 App 上点「确认登录」→ 自动把 `wt2`/`zp_at`/`bst`/`wbg` 等 Cookie 持久化。

> **当前卡点已从「登录」变成「采集」**：搜索接口 `search/joblist.json` 返回 `code:37 环境异常`，
> 需要逆向 `sign` 签名（与登录 Cookie 无关）。详情见 `docs/ops/10-JOB-SOURCES.md` 的「BOSS 直聘采集跟踪项」。
> 手动复制 Cookie 仍是备用方案（下面方案二/三），但正常情况用不到。

---

## 二、京东社招：为什么建议放弃自动化

京东社招（zhaopin.jd.com）的职位列表数据是**前端加密 XHR + 动态签名（sign）**，每次请求的签名都变，需要逆向 JS 才能拿到真实数据，成本高、且随时可能失效。

**建议**：京东社招职位直接**手动复制 JD** 粘贴到「招聘分析」页，同样能跑全流程分析，不依赖采集。

如果你坚持要自动化京东，需要提供：
- 登录京东后的 Cookie（同上，F12 或 Cookie 扩展）；
- 以及愿意接受「签名算法随时失效、需要维护」的代价。

---

## 三、我提供的登录信息（已自动完成）

| 渠道 | 需要提供 | 状态 |
|------|----------|--------|
| BOSS直聘 | ~~登录 Cookie~~ | ✅ 已通过前端「BOSS 扫码登录」自动获取并持久化，无需手动提供 |
| 京东社招 | （建议放弃，手动粘贴 JD） | — |

---

## 附：猎聘/字节等 9 家免登录渠道（已自动采集，无需你操作）

这些渠道已打通、无需登录，采集时会自动覆盖：
猎聘、字节、腾讯、百度、小米、阿里、小红书、大疆、DeepSeek。
