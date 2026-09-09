---
title: 运维手册总览（索引）
layer: 运维层
owner: SEKB Team
status: active
version: v1.0.0
last-updated: 2026-09-09
based-on-commit: f6eea56
related: [docs/tech/00-README]
---

# 运维手册总览（00-README）

> 运维手册区存放**真实环境操作**文档（部署/排障/采集渠道），与技术文档（docs/tech/，讲代码结构与原理）分层。技术口径见 docs/tech/09-OBSERVABILITY。

## 编号约定

- 前缀 `NN-`：序号即推荐阅读/执行顺序；部署主流程在前，专项在后。
- 每篇带 front-matter（title/layer/owner/status/version/last-updated/based-on-commit）。
- 活更新：手册描述「真实环境现状」，若部署方式/服务器变化请同步更新本文档区。

## 手册索引

| 编号 | 文档 | 内容 | 何时用 |
|------|------|------|--------|
| 00 | 本文档 | 运维手册总览 | 进入运维区前 |
| 01 | [01-DEPLOYMENT.md](./01-DEPLOYMENT.md) | SEKB 从零到上线总操作手册 | 首次部署/完整了解 |
| 02 | [02-PRODUCTION-DEPLOY.md](./02-PRODUCTION-DEPLOY.md) | 线上（公网）生产部署 | 部署到公网服务器 |
| 03 | [03-TENCENT-CLOUD-DEPLOY.md](./03-TENCENT-CLOUD-DEPLOY.md) | 腾讯云轻量服务器部署 | 腾讯云场景 |
| 04 | [04-CLOUD-DEPLOY.md](./04-CLOUD-DEPLOY.md) | 国内云平台低成本部署 | 低成本方案对比 |
| 05 | [05-TAILSCALE-ACCESS.md](./05-TAILSCALE-ACCESS.md) | Tailscale 内网访问监控页 | 私网访问 Grafana/Prometheus |
| 06 | [06-PRE-DEPLOY-CHECKLIST.md](./06-PRE-DEPLOY-CHECKLIST.md) | 部署前最终检查清单 | 每次上线前逐项打勾 |
| 07 | [07-ALERTING-TROUBLESHOOTING.md](./07-ALERTING-TROUBLESHOOTING.md) | 告警链路排障指南 | 飞书告警异常时 |
| 08 | [08-PYTHON311-UPGRADE.md](./08-PYTHON311-UPGRADE.md) | Python 3.11 升级与依赖安装计划 | 环境重建/升级 |
| 09 | [09-BOSS-JD-COOKIE.md](./09-BOSS-JD-COOKIE.md) | BOSS 直聘/京东社招 Cookie 获取 | 职位采集渠道失效时 |
| 10 | [10-JOB-SOURCES.md](./10-JOB-SOURCES.md) | 招聘渠道采集状态跟踪 | 采集源维护 |
| 11 | [11-CHANGE-RELEASE-POLICY.md](./11-CHANGE-RELEASE-POLICY.md) | 变更与发布操作规约（HIL 闸门/回滚/审计） | 任何生产变更前 |
| 11 | [11-MONITORING.md](./11-MONITORING.md) | Prometheus/Grafana 监控配置与使用手册（看报表/改报表/查数） | 首次看监控、自己改报表时 |

## 关联

- 技术文档：`docs/tech/`（00-README 地图）；可观测技术口径：`docs/tech/09-OBSERVABILITY.md`。
- 活文档：`docs/CHANGELOG.md`（变更记录）、`docs/BACKLOG.md`（待办）。
- 审查存档：`docs/codeReview/`（历史审查报告，仅追溯）。
