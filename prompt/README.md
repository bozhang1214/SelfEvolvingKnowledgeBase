# 提示词模板目录说明

本目录存放各 Agent 的**提示词模板**，供你审阅和修改后再接入代码。

## 目录结构

```
prompt/
├── README.md                          # 本文件
├── 1-6-工程逆向分析提示词-合并版.md      # 工程逆向分析（合并版）
├── news/                              # 科技资讯 Agent
│   ├── daily_report.md                #   日报生成主提示词
│   ├── headline.md                    #   头条分析
│   └── comprehensive.md               #   综合分析（周报/月报按自然周期取数同走此提示词）
└── job/                               # 职位分析 Agent
    ├── README.md                      #   流程说明 + 用户画像
    ├── 01_job_filter.md               #   职位筛选
    ├── 02_job_analysis.md             #   职位深度分析
    ├── 03_knowledge_priority.md       #   知识点汇总与优先级
    ├── 04_interview_qa.md             #   面试问题与回答要点
    ├── 05_gap_analysis.md             #   能力差距分析
    ├── 06_resume_advice.md            #   简历建议 + 简历变体
    ├── 07_learning_plan.md            #   学习计划
    ├── 08_project_iteration.md        #   项目迭代建议
    ├── 09_job_strategy.md             #   求职建议与投递策略
    ├── 单职位探查.md                   #   单职位探查（中文可读版）
    ├── 批量职位分析.md                 #   批量职位分析（中文可读版）
    └── 职位知识迭代.md                 #   职位知识迭代（中文可读版）
```

## 约定

1. **占位符**：提示词中用 `{{变量名}}` 表示运行时动态注入的数据，例如：
   - `{{news_items}}` — 已筛选的资讯条目
   - `{{user_profile}}` — 用户画像
   - `{{jd_text}}` — 职位描述原文
   - `{{jd_requirements}}` — 结构化 JD 要求
2. **可调参数不写死在提示词里**：关键词、来源、阈值、画像等一律走配置文件（`config.yaml` 或独立配置），运行时拼入或作为变量注入。
3. **输出一律 JSON**：方便程序解析与后续 Agent 串联（结构化输出 + `response_format=json`）。
4. 每个模板都可以独立评审、修改，互不影响。
