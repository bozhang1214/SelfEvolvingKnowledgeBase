---
title: SEKB 文档工程
layer: 宪法层
owner: SEKB Team
status: active
version: v1.0.0
last-updated: 2026-09-20
based-on-commit: 965b246
related: [docs/tech/00-README, docs/ops/00-README, docs/codeReview/未修复问题跟踪]
---

# SEKB 文档工程

> 本文档工程按 `prompt/1-6-工程逆向分析提示词-合并版.md` 重构，基于代码证据（`file:line`）维护。
> 格式约束见 [`docs/tech/.validation/DOC-TEMPLATE.md`](tech/.validation/DOC-TEMPLATE.md)。

## 目录结构

| 路径 | 内容 | 维护者建议 |
|------|------|-----------|
| [`docs/tech/00-README.md`](tech/00-README.md) | **技术文档总览**：编号文档 00–11 共 12 篇（+ 调研/清单等附录）的文档地图与阅读路线 | 所有人 |
| `docs/tech/01-ARCHITECTURE.md` … `docs/tech/11-EVOLUTION.md` | 技术文档全集（架构/运行时/模块/数据/API/配置/设计模式/术语/可观测/测试/演进） | 开发者 |
| [`docs/tech/12-三平台MCP接入调研-百炼-千帆-HiAgent.md`](tech/12-三平台MCP接入调研-百炼-千帆-HiAgent.md) | 三平台 MCP 接入调研（附录，非编号正文） | 接入方 |
| [`docs/research/20260915-volcengine-hiagent-mcp.md`](research/20260915-volcengine-hiagent-mcp.md) | 火山引擎 HiAgent/Ark 接入调研**证据与抓取方法**（结论在 `tech/12`，本文留可复现的抓取脚本） | 接入方 |
| [`docs/ops/`](ops/00-README.md) | 运维与部署手册（编号 00-15：部署/排障/采集渠道/MCP 端点，先读 00-README 索引） | 运维/后端 |
| [`docs/CHANGELOG.md`](CHANGELOG.md) | 项目变更日志（活文档，持续更新；新条目走 `docs/changelog.d/` 碎片） | 所有人 |
| [`docs/codeReview/未修复问题跟踪.md`](codeReview/未修复问题跟踪.md) | 代码审查**总账**：已修/未修/排期（范围 3）与决策记录 | 架构师 |
| [`docs/codeReview/2026-09-15/`](codeReview/2026-09-15/) | 2026-09-15 四方审查**原始报告**（`DSH-Agent/` `Trae/` `Qoder/` `CodeBuddy/`）+ 合并结论（`DSH-Agent/07-全问题总表.md`、`09-修复状态确认清单.md`） | 架构师 |
| [`docs/BACKLOG.md`](BACKLOG.md) | 需求跟踪 / 待办 / 代码审计跟踪（活文档） | 所有人 |
| [`docs/COORDINATION.md`](COORDINATION.md) | 多协作者开工认领表（改共享文件前必看） | 所有人 |
| `docs/tech/.validation/` | 文档工程校验脚本（链接/孤儿/编号/新鲜度）+ 格式模板 | 维护者 |
| `docs/tech/.facts/` | 事实表 T1–T8（单一事实源 SSOT 工作产物） | 维护者 |
| `docs/RFC-自迭代闭环设计.md` | 自迭代研发-运维闭环平台**设计 RFC**（draft）：变更发布策略（`ops/14`）、Gitea 版本管理（`ops/12`）的决策依据 | 架构师 |
| [`docs/RFC-端云协同与端侧Agent.md`](RFC-端云协同与端侧Agent.md) | **端云协同与端侧 Agent 设计方案**（活文档）：决策表、路由矩阵与升级信号、状态一致性、里程碑与风险 | 架构师 |
| [`docs/RFC-端云协同-实施记录.md`](RFC-端云协同-实施记录.md) | **M0–M3 实施记录（已归档）**：每轮做了什么、实测数字、踩的坑；开头有**「阶段总结与恢复指引」**（暂停点状态、待办、恢复命令、三条不变量） | 架构师/接手人 |
| [`docs/RFC-多端跨端方案.md`](RFC-多端跨端方案.md) | **多端跨端方案（draft，待拍板）**：iOS / 鸿蒙 / 桌面的复用矩阵、四个关键决策（CMP 统一 UI、鸿蒙 KMP 路线 + spike、桌面两步走、推理运行时矩阵）、里程碑与风险 | owner / 架构师 |
| [`docs/ops/16-端云协同协议.md`](ops/16-端云协同协议.md) | 端侧宿主 ↔ SEKB **接口契约**（设备凭证、聊天 SSE、执行位置、路由事件、权限边界、错误码） | 写客户端的人 |
| [`apps/README.md`](../apps/README.md) | **多端目录**（Android 已可用 / iOS / 鸿蒙）+ 按需编译 + 跨端逻辑分层 | 所有人 |
| `docs/tmp/` | **本地讨论区**（gitignore，不进版本库）：个人资料、已完成方案的过程稿、一次性脚本。⚠️ **被跟踪文档不要链接到这里的文件**——干净克隆里没有它（`doc_guard` 会拦） | 本人 |
| 仓库根 [`README.md`](../README.md) / [`AGENTS.md`](../AGENTS.md) | 门面（能力速览）+ 协作硬约定；两者都不复制 docs 内容，只做导览 | 所有人 |

## 文档工程维护约定

1. **证据驱动**：所有结论带 `文件:行号`；无证据结论标 `【推断·待验证】`。
2. **活文档**：每次功能/修复落地后，同步更新 `docs/CHANGELOG.md`、相关 `docs/tech/*.md`。
3. **自动联动**：`git pre-commit` 会检查「本次改动的后端路由/配置/模块是否有对应文档章节」，未更新会提示（见 `docs/tech/.validation/`）。
4. **过期标记**：每篇文档头部含 `last-updated` / `based-on-commit`；超过阈值由校验脚本告警。
5. **不重复台账**：同一份结论只维护一处。原始审查报告只作**证据留存**（不再更新状态），
   状态一律以 `未修复问题跟踪.md` 与总表为准。
6. **单一日期目录**：`docs/codeReview/` 下的日期目录统一用 `YYYY-MM-DD`（2026-09-16 起，旧的 `20260915/` 已并入 `2026-09-15/`）。

## 快速开始

- 新成员 / 决策者：从 [`docs/tech/00-README.md`](tech/00-README.md) 开始，按其「阅读路线」走。
- 后端开发者：读 `02-RUNTIME-FLOWS.md` → `03-MODULES.md` → `05-API-REFERENCE.md`。
- 运维：从 `docs/ops/00-README.md` 索引进入。
- **端侧（Android/端云协同）**：先读 [`RFC-端云协同-实施记录.md`](RFC-端云协同-实施记录.md) 开头的
  「阶段总结与恢复指引」（当前进度 + 怎么接着干），再按需读设计 RFC 与接口契约。
