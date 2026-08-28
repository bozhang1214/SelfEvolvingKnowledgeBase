# 自迭代个人知识库 Agent — 文档工程

> 项目代号：SelfEvolvingKnowledgeBase
> 当前阶段：Phase 1~4 均已实现，生产部署就绪
> 文档版本：v1.1.0
> 最后更新：2026-08-19

---

## 一、项目定位

本项目是一个**具备元认知能力的个人知识助手**，核心哲学是 **PDCA 循环**（Plan-Do-Check-Act）的工程化落地：

- **Plan（计划）**：Supervisor 识别意图，Planner 拆解任务
- **Do（执行）**：Executor 调用工具（联网搜索 / 知识库检索 / 文件解析）生成草稿
- **Check（检查）**：Critic 进行多维度反思与评估，不通过则回滚重试
- **Act（处理）**：Scribe 将高质量对话蒸馏为长期记忆，实现知识库的自迭代

项目最终目标是**面向大量用户的线上生产系统**，因此测试、评估、可观测性、可演进性与功能开发同等重要。

---

## 二、文档导航

本目录是项目的"设计宪法"，所有后续编码实现必须严格遵循文档约定。文档之间互相引用，建议按顺序阅读。

| 文档 | 内容 | 适用读者 |
|---|---|---|
| [01-architecture.md](./01-architecture.md) | 总体架构、技术栈、目录结构、架构决策记录（ADR） | 所有成员必读 |
| [02-phase1-design.md](./02-phase1-design.md) | Phase 1 详细设计：LangGraph 工作流、State Schema、五 Agent 节点详设、工具层、L1 记忆 | Phase 1 开发者 |
| [03-evaluation-and-testing.md](./03-evaluation-and-testing.md) | 评估体系（9 项量化指标）、测试用例集（TC-01~TC-06 + TC-M01~TC-M05）、Eval Mode、回归报告 | 测试与质量负责人 |
| [04-config-reference.md](./04-config-reference.md) | 完整 `config.yaml` 配置参考与每项参数含义、常见问题 | 运维与开发 |
| [05-phase2-design.md](./05-phase2-design.md) | Phase 2 详细设计：L2/L3 记忆、ChromaDB、RAG 检索增强、知识库自迭代、文件上传处理、PostgreSQL 迁移 | Phase 2 开发者 |
| [06-phase3-design.md](./06-phase3-design.md) | Phase 3 详细设计：前端 UI（聊天/文件/知识库/设置）、多用户系统、JWT 鉴权、API 路由、安全设计 | Phase 3 开发者 |
| [07-phase4-design.md](./07-phase4-design.md) | Phase 4 详细设计：Docker 部署、Nginx 配置、Prometheus+Grafana 监控、CI/CD Pipeline、灰度发布、备份恢复、应急响应 | 运维与 DevOps |
| [DEPLOYMENT.md](./DEPLOYMENT.md) | **项目操作手册**：从零到上线的完整指南，含本地开发、API 使用、CLI 使用、前端使用、Docker 部署、运维 | 所有用户必读 |
| [PRODUCTION-DEPLOY.md](./PRODUCTION-DEPLOY.md) | **线上部署手册**：生产服务器部署实战指南，含 HTTPS、CI/CD、监控告警、备份、安全加固 | 运维与 DevOps |
| [CLOUD-DEPLOY.md](./CLOUD-DEPLOY.md) | **云平台部署手册**：国内轻量应用服务器低成本部署，含腾讯云/阿里云选型、备案、成本优化 | 个人 / 小团队 |
| [TENCENT-CLOUD-DEPLOY.md](./TENCENT-CLOUD-DEPLOY.md) | **腾讯云部署手册**：基于腾讯云轻量 2核4G 套餐的完整部署指南，含前置项清单、购买、初始化、域名备案、HTTPS | 个人 / 小团队 |
| [PRE-DEPLOY-CHECKLIST.md](./PRE-DEPLOY-CHECKLIST.md) | **部署前检查清单**：8 大类检查项 + 一键检查脚本，确保上线前所有前置条件已满足 | 运维与 DevOps |
| [ALERTING-TROUBLESHOOTING.md](./ALERTING-TROUBLESHOOTING.md) | 告警模块故障排查指南：Alertmanager + feishu-webhook 常见故障与解决步骤 | 运维与 DevOps |
| [TAILSCALE-ACCESS.md](./TAILSCALE-ACCESS.md) | **监控页面远程访问手册**：Tailscale 组网（云服务器 / MacBook Pro / 手机），免公网端口、免 SSH 隧道访问 Grafana/Prometheus | 个人 / 运维 |
| [testCase/TEST-CASES.md](./testCase/TEST-CASES.md) | 测试用例汇总：单元测试、集成测试、评估黄金数据集、测试数据、自动化测试指南 | 测试与开发 |
| [python311-upgrade-plan.md](./python311-upgrade-plan.md) | Python 3.11 升级计划 | 开发 |

---

## 三、核心设计原则

1. **配置驱动**：所有可调参数（模型选择、阈值、策略开关）都在 `config.yaml`，业务代码零硬编码。
2. **接口先行**：跨阶段能力（多用户、L2/L3 记忆、知识库自迭代）Phase 1 仅预留接口，不做实现。
3. **可观测优先**：每次对话产生结构化 trace 与 eval 指标日志，可追溯、可分析、可回归。
4. **国内可访问**：所有外部服务优先国内直连，避免依赖需要翻墙的服务。
5. **MCP 优先 + 直连例外**：外部工具走 MCP 标准协议；本地高频组件（向量检索）直接 Python 调用。
6. **默认联网增强**：除非用户明确要求基于已有知识，否则回答默认联网增强（RAG 作为辅助信号）。

---

## 四、阶段路线图

| 阶段 | 目标 | 状态 |
|---|---|---|
| **Phase 1** | 多 Agent 聊天核心 + L1 记忆 + 本地 JSON 存储 + 评估体系 + CLI/FastAPI 双入口 | ✅ 已实现 |
| **Phase 2** | L3 记忆（ChromaDB）+ RAG 检索增强 + 知识库自迭代 + 文件上传 | ✅ 已实现（L2 中期记忆与 PostgreSQL 迁移预留接口） |
| **Phase 3** | 前端 UI（聊天/文件/知识库/设置）+ 多用户 + JWT 鉴权 + API 安全 | ✅ 已实现 |
| **Phase 4** | 生产部署（Docker、监控、CI/CD、灰度发布、备份恢复、安全加固） | ✅ 已实现 |

---

## 五、关键外部依赖

| 服务 | 用途 | 申请地址 | 必需性 |
|---|---|---|---|
| DeepSeek API | LLM 调用（chat + reasoner 分级） | https://platform.deepseek.com | Phase 1 必需 |
| 博查搜索 API | 联网搜索（国内直连） | https://open.bochaai.com | Phase 1 必需 |
| LangSmith | Tracing 与评估（先试，不可用降级本地 JSON） | https://smith.langchain.com | Phase 1 可选 |

---

## 六、文档变更规范

- 任何设计变更必须先修改文档，再修改代码。
- 重大决策（如更换技术栈、调整架构）通过新增 ADR 记录，不删除历史 ADR。
- 文档版本号遵循 SemVer：架构级变更 major / 功能扩展 minor / 修订 patch。
