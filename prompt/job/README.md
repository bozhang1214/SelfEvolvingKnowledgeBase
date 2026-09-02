# 职位分析 Agent · 说明与用户画像

## 流程

```
职位采集 → 01 筛选 → 02 深度分析 → 03 知识点优先级 → 04 面试Q&A
                                    ↘
                   05 差距分析 → 06 简历建议 → 07 学习计划 → 08 项目迭代
                                    ↘
                         09 求职/投递策略（汇总）
```

各步骤由独立提示词驱动，输出 JSON，供程序串联。

## 用户画像（`{{user_profile}}` 注入内容，可在此维护）

> 本画像从 `resume/张博-技术经理.pdf` 提炼，后续有变化请同步更新这里。

```yaml
user_profile:
  name: 张博
  age: 41
  years_of_experience: 15
  current_role: 技术经理（爱奇艺）
  target_role: Agent 开发（应用侧）/ 大模型应用工程师
  city: 北京
  min_salary: "50K×14"          # 最低年薪约 70 万
  company_priority: [大厂, AI公司, 国央企, 创业公司]

  background:
    - 爱奇艺 10 年：播放器/Android 客户端、多屏互动(投屏)、技术管理、数据质量体系
    - 早期 5 年：Android ROM 开发（智能电视）
    - 技术管理经验丰富：带 20+ 团队、跨部门协调、项目全流程
    - 近 1 年：主导团队 AI 工具落地（Cursor），团队 AI 使用率 100%

  skill_stack:
    strong: [技术管理, 项目管理, 客户端/播放器架构, 数据指标与质量体系, Android/Java]
    medium: [AI 工具应用与推广, 需求分析, 跨团队协调]
    weak: [LangGraph/LangChain 深度开发, RAG/向量库工程化, LLM 应用后端, Python 深度开发, MCP/Agent 框架开发]

  gap_summary:
    - AI/Agent 经验主要在"应用/推广"层，深度开发经验偏弱
    - 需补齐：Agent 框架工程化、RAG 工程、LLM 应用后端（Python/FastAPI）
```

## 约定

- 所有提示词的 `{{user_profile}}` 由上面的画像（或程序读到的结构化画像）注入。
- 可调参数（薪资阈值、公司优先级、城市、目标岗位关键词）走配置，不写死在提示词里。
