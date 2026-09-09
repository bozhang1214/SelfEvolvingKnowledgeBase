# 评估与测试体系

> 文档版本：v1.0.0
> 最后更新：2026-08-12
> 状态：设计评审中
> 依赖文档：[01-architecture.md](./01-architecture.md)、[02-phase1-design.md](./02-phase1-design.md)

---

## 一、设计目标

本项目最终要上线面向大量用户，因此**测试、评估与功能开发同等重要**。本体系解决四个核心问题：

1. **可量化**：每次对话输出 9 项指标的数值、含义、解读，杜绝"感觉还行"的主观判断
2. **可追溯**：通过 `trace_id` 串联指标 → trace → 日志，任一问题可反查根因
3. **可回归**：版本迭代后自动跑 eval 集，质量下降 > 5% 阻断发布
4. **可分析**：日/周聚合报告，识别趋势与异常

---

## 二、9 项量化评估指标

### 2.1 指标总览

| # | 指标名 | 类型 | 来源节点 | 单位 | 范围 |
|---|---|---|---|---|---|
| 1 | `intent_confidence` | 路由质量 | Supervisor | 比例 | 0.0~1.0 |
| 2 | `plan_step_count` | 规划质量 | Planner | 个数 | 整数 |
| 3 | `replan_count` | 反思质量 | Critic | 次数 | 整数 |
| 4 | `tool_success_rate` | 工具稳定性 | Executor | 比例 | 0.0~1.0 |
| 5 | `answer_groundedness` | **幻觉检测** | Critic | 比例 | 0.0~1.0 |
| 6 | `critic_coherence_score` | 逻辑自洽 | Critic | 分数 | 0.0~10.0 |
| 7 | `answer_relevance` | 回答相关性 | Critic + LLM-judge | 比例 | 0.0~1.0 |
| 8 | `e2e_latency_ms` | 性能 | 全链路 | 毫秒 | 整数 |
| 9 | `cost_usd` | 成本 | 全链路 | 美元 | 浮点 |

### 2.2 指标详细定义

#### 指标 1：`intent_confidence` — 意图识别置信度

- **含义**：Supervisor 输出的意图分类置信度
- **计算**：Supervisor LLM 直接输出（强制 JSON 字段）
- **工程化解读**：
  - `≥ 0.9`：高可信，正常路由
  - `0.7 ~ 0.9`：需关注，可能边界场景
  - `< 0.7`：触发澄清（Phase 1 简化为直通 chat_simple）
- **优化触发**：持续 < 0.7 → 优化 Supervisor Prompt 或增加 few-shot 示例

#### 指标 2：`plan_step_count` — 规划步数

- **含义**：Planner 拆解的 TaskStep 数量
- **计算**：`len(state.task_list)`
- **工程化解读**：
  - `1 ~ 5`：合理
  - `> 5`：过度规划，需约束 Prompt
  - `0`：异常（Planner 失败）
- **优化触发**：均值 > 5 → 加强 Prompt "步骤数 1~5 步" 约束

#### 指标 3：`replan_count` — 重规划次数

- **含义**：Critic 不通过触发的重规划次数
- **计算**：`state.replan_count`
- **工程化解读**：
  - `0`：最佳，一次通过
  - `1`：可接受
  - `≥ 2`：质量预警，触发了超限降级
- **优化触发**：连续多对话 replan_count ≥ 2 → 检查 Planner 与 Executor 协同

#### 指标 4：`tool_success_rate` — 工具调用成功率

- **含义**：成功工具调用次数 / 总工具调用次数
- **计算**：`sum(1 for c in tool_calls if c.success) / len(tool_calls)`
- **工程化解读**：
  - `≥ 0.9`：正常
  - `0.7 ~ 0.9`：需关注，工具链可能不稳
  - `< 0.7`：工具链异常，需增加重试或检查 MCP Server
- **优化触发**：< 0.9 → 检查博查 API 稳定性、增加熔断

#### 指标 5：`answer_groundedness` — 答案锚定度（防幻觉核心）

- **含义**：最终答案有多大比例锚定在执行结果（联网结果/知识库）中
- **计算**：Critic 评分维度 `scores.groundedness`（0.0~1.0）
- **工程化解读**：
  - `≥ 0.8`：良好，答案有据可依
  - `0.6 ~ 0.8`：一般，部分内容无依据
  - `< 0.6`：严重幻觉，需加强 Prompt 约束或提高重试阈值
- **优化触发**：< 0.6 → 加强 Executor Prompt "答案必须锚定执行结果"

#### 指标 6：`critic_coherence_score` — 逻辑链自洽性

- **含义**：Critic 评估的答案逻辑链完整性与自洽性
- **计算**：Critic 评分维度 `scores.coherence`（0.0~10.0）
- **工程化解读**：
  - `≥ 8.0`：良好
  - `6.0 ~ 8.0`：可接受
  - `< 6.0`：逻辑断裂，需切换 R1 模型或增加 CoT 示例
- **优化触发**：连续 < 6.0 → Critic 切换 reasoner 模型

#### 指标 7：`answer_relevance` — 回答相关性

- **含义**：最终答案与用户问题的相关性
- **计算**：Critic 评分维度 `scores.relevance`（0.0~1.0）
- **工程化解读**：
  - `≥ 0.8`：良好
  - `0.6 ~ 0.8`：一般
  - `< 0.6`：跑题，需检查 Planner 拆解方向
- **优化触发**：< 0.6 → 检查 Planner 是否正确理解用户意图

#### 指标 8：`e2e_latency_ms` — 端到端延迟

- **含义**：从用户输入到最终答案返回的总耗时（含反思重试）
- **计算**：`finished_at - started_at`（毫秒）
- **工程化解读**：
  - `< 5000ms`：良好
  - `5000 ~ 10000ms`：可接受
  - `> 10000ms`：需优化（异步流式/缩减窗口/降级 reasoner）
- **优化触发**：P95 > 10s → 优化重点链路

#### 指标 9：`cost_usd` — 单次对话成本

- **含义**：本次对话所有 LLM 调用的总成本
- **计算**：`Σ (input_tokens × input_price + output_tokens × output_price)`
- **工程化解读**：
  - 由 DeepSeek 定价表计算
  - 用于成本归因与日预算告警
- **优化触发**：日累计 > `daily_budget_usd` → 触发自动降级到全 chat

### 2.3 指标日志格式

每次对话结束写入 `data/eval_logs/metrics_YYYY-MM-DD.jsonl`，每行一条：

```json
{
  "trace_id": "20260812-143022-abc123",
  "conv_id": "conv_xxx",
  "timestamp": "2026-08-12T14:30:22+08:00",
  "version": {
    "code": "0.1.0",
    "models": {
      "supervisor": "deepseek-chat",
      "planner": "deepseek-reasoner",
      "executor": "deepseek-chat",
      "critic": "deepseek-chat",
      "scribe": "deepseek-chat"
    },
    "config_hash": "a1b2c3"
  },
  "metrics": {
    "intent_confidence": {
      "value": 0.92,
      "meaning": "意图识别置信度",
      "interpretation": "≥0.9 高可信 | 0.7-0.9 需关注 | <0.7 触发澄清",
      "thresholds": { "good": 0.9, "warn": 0.7 },
      "status": "good"
    },
    "plan_step_count": {
      "value": 3,
      "meaning": "规划步数",
      "interpretation": "1-5 合理 | >5 过度规划 | 0 异常",
      "thresholds": { "good": 5, "warn": 8 },
      "status": "good"
    },
    "replan_count": {
      "value": 0,
      "meaning": "重规划次数",
      "interpretation": "0 最佳 | 1 可接受 | ≥2 质量预警",
      "thresholds": { "good": 0, "warn": 2 },
      "status": "good"
    },
    "tool_success_rate": {
      "value": 1.0,
      "meaning": "工具调用成功率",
      "interpretation": "≥0.9 正常 | 0.7-0.9 关注 | <0.7 异常",
      "thresholds": { "good": 0.9, "warn": 0.7 },
      "status": "good"
    },
    "answer_groundedness": {
      "value": 0.85,
      "meaning": "答案锚定度（防幻觉核心）",
      "interpretation": "≥0.8 良好 | 0.6-0.8 一般 | <0.6 严重幻觉",
      "thresholds": { "good": 0.8, "warn": 0.6 },
      "status": "good"
    },
    "critic_coherence_score": {
      "value": 8.5,
      "meaning": "逻辑链自洽性（0-10）",
      "interpretation": "≥8.0 良好 | 6.0-8.0 可接受 | <6.0 逻辑断裂",
      "thresholds": { "good": 8.0, "warn": 6.0 },
      "status": "good"
    },
    "answer_relevance": {
      "value": 0.88,
      "meaning": "回答相关性",
      "interpretation": "≥0.8 良好 | 0.6-0.8 一般 | <0.6 跑题",
      "thresholds": { "good": 0.8, "warn": 0.6 },
      "status": "good"
    },
    "e2e_latency_ms": {
      "value": 4200,
      "meaning": "端到端延迟(ms)",
      "interpretation": "<5000 良好 | 5000-10000 可接受 | >10000 需优化",
      "thresholds": { "good": 5000, "warn": 10000 },
      "status": "good"
    },
    "cost_usd": {
      "value": 0.0031,
      "meaning": "本次对话成本(美元)",
      "interpretation": "用于成本归因与预算告警",
      "thresholds": null,
      "status": "info"
    }
  },
  "overall_status": "good"
}
```

**`status` 取值**：`good`（≥ good 阈值）/ `warn`（介于 good 与 warn 之间）/ `bad`（< warn 阈值）/ `info`（无阈值，仅记录）

**`overall_status`**：所有有阈值的指标中，任一为 `bad` → `bad`；任一为 `warn` → `warn`；否则 `good`

---

## 三、测试金字塔

```
                    ┌─────────────┐
                    │   E2E 测试   │  少量，接真实 API，跑 eval 集
                    └─────────────┘
                  ┌─────────────────┐
                  │  集成测试         │  图流转、存储读写、MCP 通信
                  └─────────────────┘
              ┌─────────────────────────┐
              │     单元测试              │  各 Agent 节点纯逻辑、记忆、工具
              └─────────────────────────┘
```

### 3.1 单元测试

**范围**：每个 Agent 节点的纯函数逻辑、记忆管理、工具封装

**Mock 策略**：
- LLM 调用：用 `respx` 或自定义 Mock 返回固定 JSON
- MCP 工具：Mock MCPClient.call 返回固定结果
- 存储：用临时目录的 JSONStorage 实例

**覆盖率目标**：核心模块 ≥ 80%

### 3.2 集成测试

**范围**：
- LangGraph 图流转（Supervisor → Planner → Executor → Critic → Scribe）
- 存储读写一致性
- MCP Client ↔ Server 通信
- L1 记忆压缩触发

**Mock 策略**：
- LLM 仍可 Mock（保证 CI 不依赖外部 API）
- MCP 起本地 Server 实例
- 存储用临时目录

### 3.3 E2E 测试（接真实 API）

**范围**：
- 调用真实 DeepSeek API 与博查 API
- 跑黄金数据集，验证端到端质量
- 仅在本地或特定 CI 环境跑（标记 `@pytest.mark.e2e`）

**频率**：
- 每次主分支合并前跑一次
- 每日定时跑一次（cron）

---

## 四、测试用例集

### 4.1 功能鲁棒性测试（黑盒，TC-01 ~ TC-06）

| 测试编号 | 测试模块 | 输入 | 预期结果（通过标准） |
|---|---|---|---|
| **TC-01** | 意图识别-闲聊 | "你好啊，今天天气真好" | Supervisor 分类为 `chitchat`，`intent_confidence ≥ 0.9`，**不触发** Planner/Executor，直通 chat_simple |
| **TC-02** | 意图识别-知识库严格 | "根据我上传的笔记，讲讲装饰器" | 识别为 `kb_strict`（含"我的笔记"关键词），Phase 1 返回"知识库建设中"提示 |
| **TC-03** | 强制联网 | "2026 年诺贝尔物理学奖得主是谁？" | 识别为 `web_default`（实时性问题），调用博查 MCP，答案包含当年信息，`tool_success_rate = 1.0` |
| **TC-04** | 多步规划 | "对比 React 和 Vue 在状态管理上的差异，并给出选型建议" | 识别为 `task_plan`，Planner 拆解为 ≥ 2 步，`plan_step_count` 在 2~5 之间，输出结构化对比 |
| **TC-05** | 反思重试 | 输入一个需要精确事实的问题（如"光速的精确值是多少？"） | Critic 评估 `answer_groundedness ≥ 0.8`，若首轮 < 0.6 触发 replan，最终通过或标记 low_confidence |
| **TC-06** | 长上下文压缩 | 连续输入 10 轮复杂对话 | 第 9 轮触发 L1 压缩，`history_summary` 非空，`token_count(working_memory) ≤ max_tokens`，不报错 |

### 4.2 量化指标专项测试（白盒，TC-M01 ~ TC-M05）

| 测试编号 | 关联指标 | 测试方法 | 通过阈值 |
|---|---|---|---|
| **TC-M01** | `intent_confidence` | 预设 10 条明确意图的输入（5 闲聊 + 5 联网），检查日志输出的置信度均值 | 均值 **≥ 0.85** |
| **TC-M02** | `answer_groundedness` | 注入要求精确事实的问题，检查 Critic 评分 | 均值 **≥ 0.7** |
| **TC-M03** | `critic_coherence_score` | 输入 5 条需要逻辑推理的问题（如对比/因果分析） | 均值 **≥ 7.0** |
| **TC-M04** | `e2e_latency_ms` | 顺序压测 20 条请求，记录 P95 耗时 | P95 **≤ 15000ms**（含反思重试） |
| **TC-M05** | `tool_success_rate` | 连续调用博查搜索 20 次，统计成功率 | **≥ 0.95** |

### 4.3 边界与异常测试（TC-E01 ~ TC-E04）

| 测试编号 | 测试场景 | 输入 | 预期结果 |
|---|---|---|---|
| **TC-E01** | LLM 超时 | Mock LLM 超时 60s | 触发重试 2 次 → 降级提示，不崩溃，errors 记录 |
| **TC-E02** | 工具失败 | Mock 博查 API 返回 500 | 重试 2 次 → 跳过联网，Executor 用 LLM 知识生成，记 warn |
| **TC-E03** | 预算超限 | 配置 `daily_budget_usd = 0.001`，跑 2 条对话 | 第 2 条触发降级，全链路用 chat，记 warn |
| **TC-E04** | JSON 解析失败 | Mock LLM 返回非 JSON | Supervisor 默认 `web_default`，confidence=0.5，记 error 继续 |

---

## 五、Eval Mode 设计

### 5.1 工作模式

Eval Mode 是 CLI 的一个子命令，用于批量评估系统质量：

```bash
python -m app.cli.main eval \
  --dataset tests/fixtures/golden_qa.json \
  --output tests/reports/eval_20260812.json \
  [--compare-with tests/reports/eval_20260811.json]
```

**Eval Mode 行为**：
1. 禁用 CLI 交互输入
2. 批量消费 `golden_qa.json` 中的测试用例
3. 每条用例独立运行，生成完整 trace 与 eval 日志
4. 所有用例跑完后，聚合生成评估报告
5. 若指定 `--compare-with`，与历史报告对比，标记回归

### 5.2 黄金数据集格式

`tests/fixtures/golden_qa.json`：

```json
{
  "version": "1.0",
  "updated_at": "2026-08-12",
  "cases": [
    {
      "case_id": "TC-01",
      "category": "intent_chitchat",
      "input": "你好啊，今天天气真好",
      "expected_behavior": {
        "intent": "chitchat",
        "should_call_tools": false,
        "should_plan": false
      },
      "metric_thresholds": {
        "intent_confidence": { "min": 0.9 }
      }
    },
    {
      "case_id": "TC-03",
      "category": "web_search",
      "input": "2026 年诺贝尔物理学奖得主是谁？",
      "expected_behavior": {
        "intent": "web_default",
        "should_call_tools": true,
        "tool_name": "web_search"
      },
      "metric_thresholds": {
        "intent_confidence": { "min": 0.8 },
        "tool_success_rate": { "min": 1.0 },
        "answer_groundedness": { "min": 0.7 }
      }
    }
  ]
}
```

### 5.3 断言引擎

`app/eval/assertion.py` 负责读取 eval 日志，与 `metric_thresholds` 比对：

```python
class AssertionEngine:
    def assert_case(self, case: dict, metrics: dict) -> AssertionResult:
        """
        对每条用例断言：
        1. expected_behavior 检查（intent、工具调用等）
        2. metric_thresholds 检查（指标是否达标）
        """
        ...

    def assert_suite(self, suite_results: list) -> SuiteReport:
        """聚合所有用例结果，生成报告"""
        ...
```

**断言结果**：
```json
{
  "case_id": "TC-03",
  "passed": true,
  "behavior_checks": [
    { "check": "intent == web_default", "passed": true },
    { "check": "tool_called == web_search", "passed": true }
  ],
  "metric_checks": [
    { "metric": "intent_confidence", "value": 0.92, "threshold": 0.8, "passed": true },
    { "metric": "tool_success_rate", "value": 1.0, "threshold": 1.0, "passed": true },
    { "metric": "answer_groundedness", "value": 0.85, "threshold": 0.7, "passed": true }
  ]
}
```

---

## 六、回归报告

### 6.1 报告格式

每次 Eval Mode 运行生成 Markdown 报告 `tests/reports/eval_YYYYMMDD_HHMMSS.md`：

```markdown
# 评估报告 - 2026-08-12 14:30

## 总体结果
- 通过率：18/20 (90%)
- 整体状态：warn
- 与上次对比：通过率下降 5%（高危回归）

## 指标聚合
| 指标 | 本次均值 | 上次均值 | 变化 | 状态 |
|---|---|---|---|---|
| intent_confidence | 0.88 | 0.91 | -3.3% | good |
| plan_step_count | 3.2 | 3.0 | +6.7% | good |
| replan_count | 0.4 | 0.2 | +100% | warn |
| tool_success_rate | 0.95 | 0.98 | -3.1% | good |
| answer_groundedness | 0.78 | 0.82 | -4.9% | good |
| critic_coherence_score | 7.8 | 8.1 | -3.7% | good |
| answer_relevance | 0.85 | 0.87 | -2.3% | good |
| e2e_latency_ms | 6200 | 5800 | +6.9% | warn |
| cost_usd | 0.0035 | 0.0031 | +12.9% | info |

## 高危回归
- replan_count 上升 100%（0.2 → 0.4），需检查 Planner 与 Critic 协同
- e2e_latency_ms P95 上升 6.9%，接近 warn 阈值

## 失败用例
- TC-05: answer_groundedness = 0.55 < 0.7（幻觉）
- TC-M04: P95 延迟 16200ms > 15000ms
```

### 6.2 回归判定规则

- **通过率下降 > 5%**：标记为"高危回归"，**阻断发布**
- **任一指标均值下降 > 5%**：标记为"指标回归"，**告警但不阻断**
- **新增 bad 状态用例**：标记为"用例回归"，**需人工确认**
- **首次出现的指标**：标记为"基线建立"，不参与回归对比

### 6.3 报告归档

- 每次报告存 `tests/reports/`（gitignore，仅本地保留）
- 关键里程碑（如版本发布）的报告手动复制到 `docs/reports/` 归档
- 基线报告 `tests/reports/baseline.json` 提交到 git，作为回归对比基准

---

## 七、日常评估流程

### 7.1 开发期（每次提交）

```bash
# 跑单测 + 集成测（不依赖外部 API）
pytest tests/unit tests/integration

# 跑小规模 eval（10 条用例，依赖 API）
python -m app.cli.main eval --dataset tests/fixtures/smoke_eval.json
```

### 7.2 发布前（每次版本发布）

```bash
# 跑完整 eval 集
python -m app.cli.main eval \
  --dataset tests/fixtures/golden_qa.json \
  --compare-with tests/reports/baseline.json

# 人工 review 报告
# 通过后更新 baseline
cp tests/reports/eval_latest.json tests/reports/baseline.json
```

### 7.3 线上（Phase 4）

- 每日定时跑 eval 集，监控指标趋势
- 在线指标看板（基于 `data/eval_logs/` 聚合）
- 异常告警（指标下降 > 5% 触发通知）

---

## 八、评估体系的可演进性

| 演进方向 | Phase 1 | Phase 2+ |
|---|---|---|
| 指标数量 | 9 项 | +RAG 专项（recall/precision）+ 用户反馈 |
| 数据集 | 合成 20 条 | +线上采样 + 用户标注 |
| 评估方法 | LLM-judge + 阈值 | +人工标注 + A/B 测试 |
| 报告 | Markdown | +在线看板（Grafana） |
| 触发 | 手动 CLI | +CI 自动 + 定时 cron |
| 回归 | 与 baseline 对比 | +多版本对比 + 灰度对比 |

**关键设计**：所有指标计算逻辑在 `app/eval/metrics.py`，新增指标只需扩展该模块，不影响业务代码。
