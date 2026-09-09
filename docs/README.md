# SEKB 文档工程

> 本文档工程按 `prompt/1-6-工程逆向分析提示词-合并版.md` 重构，基于代码证据（`file:line`）维护。

## 目录结构

| 路径 | 内容 | 维护者建议 |
|------|------|-----------|
| [`docs/tech/00-README.md`](tech/00-README.md) | **技术文档总览**：12 篇编号文档的文档地图与阅读路线（完整版） | 所有人 |
| `docs/tech/01-ARCHITECTURE.md` … `docs/tech/11-EVOLUTION.md` | 技术文档全集（架构/运行时/模块/数据/API/配置/设计模式/术语/可观测/测试/演进） | 开发者 |
| [`docs/ops/`](ops/00-README.md) | 运维与部署手册（编号 00-10：部署/排障/采集渠道，先读 00-README 索引） | 运维/后端 |
| [`docs/CHANGELOG.md`](CHANGELOG.md) | 项目变更日志（活文档，持续更新） | 所有人 |
| [`docs/codeReview/`](codeReview/) | 历史代码审查存档（Qoder/CodeBuddy/全域评审，仅追溯） | 架构师 |
| [`docs/BACKLOG.md`](BACKLOG.md) | 需求跟踪 / 待办 / 代码审计跟踪（活文档） | 所有人 |
| `docs/tech/.validation/` | 文档工程校验脚本（链接/孤儿/编号/新鲜度） | 维护者 |
| `docs/tech/.facts/` | 事实表 T1–T8（单一事实源 SSOT 工作产物） | 维护者 |

## 文档工程维护约定

1. **证据驱动**：所有结论带 `文件:行号`；无证据结论标 `【推断·待验证】`。
2. **活文档**：每次功能/修复落地后，同步更新 `docs/CHANGELOG.md`、相关 `docs/tech/*.md`。
3. **自动联动**：`git pre-commit` 会检查「本次改动的后端路由/配置/模块是否有对应文档章节」，未更新会提示（见 `docs/tech/.validation/`）。
4. **过期标记**：每篇文档头部含 `last-updated` / `based-on-commit`；超过阈值由校验脚本告警。

## 快速开始

- 新成员 / 决策者：从 [`docs/tech/00-README.md`](tech/00-README.md) 开始，按其「阅读路线」走。
- 后端开发者：读 `02-RUNTIME-FLOWS.md` → `03-MODULES.md` → `05-API-REFERENCE.md`。
- 运维：从 `docs/ops/00-README.md` 索引进入。
