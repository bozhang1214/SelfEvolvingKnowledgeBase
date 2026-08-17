# 配置参考

> 文档版本：v1.1.0
> 最后更新：2026-08-12
> 状态：设计评审中
> 依赖文档：[01-architecture.md](./01-architecture.md)、[02-phase1-design.md](./02-phase1-design.md)

---

## 一、配置设计原则

1. **配置驱动**：所有可调参数集中在 `backend/config.yaml`，业务代码零硬编码
2. **强类型校验**：用 Pydantic Settings 加载并校验，配置错误启动即失败
3. **环境变量优先**：敏感信息（API key）走环境变量，YAML 中用 `${VAR}` 语法引用
4. **可演进**：新增配置项不影响老配置加载（带默认值）
5. **可观测**：启动时打印生效配置（脱敏后）到日志

---

## 二、完整 config.yaml（带详细注释）

```yaml
# ============================================================
# 自迭代个人知识库 Agent 配置文件
# ============================================================
# 使用说明：
#   1. 修改配置后需重启服务生效（Phase 1 不支持热加载）
#   2. 敏感信息（API Key 等）请通过 ${ENV_VAR} 引用环境变量
#   3. 详细字段说明见"三、字段详细说明"章节
# ============================================================

# ============ 应用元信息 ============
app:
  name: self-evolving-kb                 # 应用名称，用于日志和标识
  version: 0.1.0                         # 应用版本号，变更时需同步更新
  environment: development                # 环境标识：development | staging | production
  debug: true                            # Debug 模式：输出详细调试日志

# ============ LLM 模型路由 ============
# 每个 Agent 角色独立配置模型与采样参数
llm:
  provider: deepseek                     # LLM 提供商，目前仅支持 deepseek
  api_key: ${DEEPSEEK_API_KEY}           # API Key，必须通过环境变量设置
  base_url: https://api.deepseek.com/v1  # API 基础 URL
  timeout_seconds: 60                    # 单次 LLM 调用超时时间（秒）
  max_retries: 2                         # LLM 调用最大重试次数
  retry_backoff_seconds: [1, 2]          # 重试退避间隔（秒），数组长度 = max_retries

  # 各角色模型配置
  # model: 使用的模型名称，可选：deepseek-chat, deepseek-reasoner
  # temperature: 采样温度 0.0~2.0，越低越确定，越高越发散
  # max_tokens: 单次响应最大 token 数
  # response_format: 输出格式，json 强制 JSON 输出（用于结构化解析）
  roles:
    supervisor:                          # 监督者：意图识别 + 路由
      model: deepseek-chat
      temperature: 0.1
      max_tokens: 500
      response_format: json
    planner:                             # 规划者：任务拆解（推荐用 reasoner）
      model: deepseek-reasoner
      temperature: 0.2
      max_tokens: 2000
      response_format: json
    executor:                            # 执行器：工具调用 + 草稿生成
      model: deepseek-chat
      temperature: 0.3
      max_tokens: 2000
    critic:                              # 审查者：反思评估（默认用 chat 省钱）
      model: deepseek-chat
      temperature: 0.0
      max_tokens: 1000
      response_format: json
    critic_complex:                      # 审查者：复杂任务用 reasoner
      model: deepseek-reasoner
      temperature: 0.0
      max_tokens: 2000
      response_format: json
    scribe:                              # 记录员：摘要 + 重要性评分
      model: deepseek-chat
      temperature: 0.2
      max_tokens: 800
      response_format: json
    chat_simple:                         # 闲聊直通（跳过规划/反思）
      model: deepseek-chat
      temperature: 0.7
      max_tokens: 1000

  # 降级链配置
  fallback:
    reasoner_to_chat: true               # reasoner 不可用时自动降级到 chat
    chat_to_error: true                  # chat 不可用时返回错误提示（而非死循环）

# ============ 意图路由 ============
# 控制 Super victor 的意图识别和路由行为
intent_routing:
  default_intent: web_default            # 默认意图：web_default（联网增强）| chitchat | kb_prefer
  clarify_threshold: 0.7                 # 意图置信度低于此值时，触发澄清提问
  kb_strict_keywords:                    # 触发 kb_strict 意图的关键词列表
    - "我的笔记"
    - "知识库"
    - "我之前说过"
    - "基于我的"
    - "根据我的文档"
  kb_prefer_hit_threshold: 0.85          # 预检索命中度高于此值时，优先走 RAG
  realtime_keywords:                     # 实时性关键词列表，触发强制联网
    - "最新"
    - "今天"
    - "昨天"
    - "今年"
    - "2026"
    - "当前"
  enable_pre_retrieval: true             # 是否启用预检索（Phase 1 启用但返回空结果）
  pre_retrieval_top_k: 3                 # 预检索返回的 Top-K 结果数

# ============ 三层记忆 ============
# L1 短期记忆（工作记忆）：Phase 1 实现
# L2 中期记忆（会话记忆）：Phase 2 实现
# L3 长期记忆（知识库）：Phase 2 实现
memory:
  l1_working:
    enabled: true                        # L1 是否启用（Phase 1 必须为 true）
    max_turns: 8                         # 滑动窗口保留的最大轮数
    max_tokens: 3000                     # 软上限：token 数超过此值触发压缩
    hard_token_limit: 4000               # 硬上限：token 数超过此值强制丢弃
    compress_strategy: hierarchical      # 压缩策略：hierarchical（分层）| simple（简单）
    max_compressed_summaries: 3          # 保留的压缩摘要条数（超过后丢弃最旧的）
  l2_session:
    enabled: false                       # L2 是否启用（Phase 1 为 false）
    max_items: 50                        # 单用户最大会话数
    eviction: lru                        # 淘汰策略：lru（最近最少使用）
  l3_knowledge:
    enabled: false                       # L3 是否启用（Phase 1 为 false）
    retrieval_top_k: 5                   # 检索返回的 Top-K 知识条目数
    importance_threshold: 0.3            # 重要性评分低于此值的知识不参与默认召回
    eviction:
      max_items: 10000                   # 知识库最大条目数
      stale_days: 7                      # 超过此天数未访问的知识视为陈旧
      similarity_merge_threshold: 0.95    # 语义相似度超过此值的条目自动合并

# ============ 反思策略 ============
reflection:
  policy: always                         # 反思策略：always | adaptive | sampling
  max_replan: 2                          # 最大重规划次数（Critic 不通过时回滚次数）
  model_switch_threshold: 0.7            # 任务复杂度超过此值时，Critic 切换为 reasoner 模型
  # Adaptive 策略参数（policy=adaptive 时生效）
  adaptive:
    skip_if_rag_hit_above: 0.95          # RAG 命中度超过此值时跳过反思
    skip_if_intent_in: [chitchat]        # 这些意图类型直接跳过反思
  # Sampling 策略参数（policy=sampling 时生效）
  sampling:
    rate: 0.5                            # 反思采样率（0.5 = 50% 的对话进行反思）

# ============ 评估指标阈值 ============
# 用于 Eval Mode 和生产环境的质量监控
evaluation:
  enabled: true                          # 是否启用评估日志
  log_to_file: true                      # 是否将评估日志写入文件
  log_path: data/eval_logs               # 评估日志存储路径
  metrics:
    # 意图识别置信度：越高越好，低于 warn 阈值需关注
    intent_confidence:
      good: 0.9                          # ≥ 此值为良好
      warn: 0.7                          # < 此值为警告
    # 规划步数：过多可能过度规划
    plan_step_count:
      good: 5                            # ≤ 此值为良好
      warn: 8                            # > 此值为警告
    # 重规划次数：越多说明质量问题越大
    replan_count:
      good: 0                            # = 0 为最佳
      warn: 2                            # ≥ 2 为警告
    # 工具调用成功率：低于 warn 阈值说明工具链异常
    tool_success_rate:
      good: 0.9                          # ≥ 此值为良好
      warn: 0.7                          # < 此值为警告
    # 答案锚定度（防幻觉核心）：越高说明答案越有据可依
    answer_groundedness:
      good: 0.8                          # ≥ 此值为良好
      warn: 0.6                          # < 此值为警告
    # 逻辑链自洽性：0-10 分，越高说明逻辑越通顺
    critic_coherence_score:
      good: 8.0                          # ≥ 此值为良好
      warn: 6.0                          # < 此值为警告
    # 回答相关性：越高说明答案越贴题
    answer_relevance:
      good: 0.8                          # ≥ 此值为良好
      warn: 0.6                          # < 此值为警告
    # 端到端延迟：毫秒，越低越好
    e2e_latency_ms:
      good: 5000                         # ≤ 此值为良好
      warn: 10000                        # > 此值为警告
    # cost_usd 无阈值，仅记录
  # 回归检测配置
  regression:
    pass_rate_drop_threshold: 0.05       # 通过率下降超过 5% 视为高危回归
    metric_drop_threshold: 0.05          # 单项指标下降超过 5% 视为指标回归

# ============ 成本控制 ============
cost_control:
  per_conversation_token_limit: 20000    # 单次对话的 token 上限（防止刷爆）
  daily_budget_usd: 1.0                  # 每日预算上限（美元），超出后自动降级
  reasoner_ratio_alert_above: 0.3        # reasoner 调用占比超过此值时告警
  auto_downgrade_on_budget: true         # 触发预算上限时自动降级为全 chat 模型
  # DeepSeek 定价表（用于成本精确计算）
  # 注意：此价格以官方实际定价为准，仅作为估算参考
  pricing:
    deepseek-chat:
      input: 0.00014                     # 输入价格：美元 / 1K tokens
      output: 0.00028                    # 输出价格：美元 / 1K tokens
    deepseek-reasoner:
      input: 0.00055                     # 输入价格：美元 / 1K tokens
      output: 0.00219                    # 输出价格：美元 / 1K tokens

# ============ Tracing（链路追踪） ============
tracing:
  provider: langsmith                    # 追踪服务：langsmith | local_json | langfuse
  fallback_to_local_on_failure: true     # LangSmith 连接失败时是否降级到本地 JSON
  sample_rate: 1.0                       # 采样率：1.0 = 100% 采样
  langsmith:
    api_key: ${LANGSMITH_API_KEY}        # LangSmith API Key
    project: self-evolving-kb            # LangSmith 项目名称
    endpoint: https://api.smith.langchain.com  # LangSmith 服务地址
  local_json:
    trace_dir: data/traces               # 本地 JSON trace 文件存储目录
    max_file_size_mb: 10                 # 单个 trace 文件最大体积（MB）

# ============ 工具层（MCP 协议） ============
tools:
  web_search:
    provider: bocha                      # 搜索服务提供商：bocha | tavily | none
    api_key: ${BOCHA_API_KEY}            # 搜索服务 API Key
    endpoint: https://api.bochaai.com/v1/web-search  # API 端点
    max_results: 5                       # 每次搜索返回的最大结果数
    timeout_seconds: 10                  # 搜索请求超时时间（秒）
    max_retries: 2                       # 搜索请求最大重试次数
  filesystem:
    enabled: false                       # 是否启用文件系统工具（Phase 1 为 false）
    allowed_paths: []                    # 允许访问的文件路径列表（空列表 = 禁止所有）
  # 以下为 Phase 2+ 启用的工具
  vector_store:
    enabled: false                       # 是否启用向量库工具
    provider: chroma                     # 向量库提供商：chroma | milvus
    persist_path: data/chroma_db         # 向量库持久化路径
  document_parser:
    enabled: false                       # 是否启用文档解析工具（PDF/Word）

# ============ 存储后端 ============
storage:
  backend: local_json                    # 存储后端：local_json | postgres
  conversations_dir: data/conversations  # 会话数据存储目录（local_json 模式）
  index_file: data/index.json            # 会话索引文件路径
  # 以下为 PostgreSQL 模式配置（Phase 2 启用）
  postgres:
    url: ${DATABASE_URL}                 # 数据库连接 URL
    pool_size: 10                        # 数据库连接池大小

# ============ 安全 ============
security:
  pii_masking: true                      # 是否对敏感个人信息（手机号、身份证等）脱敏
  prompt_injection_guard: true           # 是否启用 Prompt 注入防护
  max_input_length: 8000                 # 用户输入最大长度（字符），超过将被截断
  blocked_patterns:                      # 输入黑名单正则表达式列表
    - "ignore.*previous.*instruction"     # 典型 Prompt 注入：忽略之前指令
    - "disregard.*above"                 # 典型 Prompt 注入：无视上文

# ============ API 服务 ============
api:
  host: 0.0.0.0                         # API 服务监听地址
  port: 8000                             # API 服务监听端口
  cors_origins:                          # CORS 允许的来源列表
    - "http://localhost:3000"
    - "http://localhost:5173"
  rate_limit:
    enabled: false                       # 是否启用 API 限流（Phase 3 启用）
    requests_per_minute: 60              # 单 IP 每分钟最大请求数

# ============ 日志 ============
logging:
  level: INFO                            # 日志级别：DEBUG | INFO | WARNING | ERROR
  format: json                           # 日志格式：json | text
  log_dir: data/logs                     # 日志文件存储目录
  max_file_size_mb: 50                   # 单个日志文件最大体积（MB）
  backup_count: 7                        # 保留的历史日志文件数量
  redact_fields:                         # 日志中需要脱敏的字段名列表
    - api_key
    - authorization
    - token
```

---

## 三、字段详细说明

### 3.1 LLM 角色配置（`llm.roles.*`）

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `model` | string | `deepseek-chat` | 使用的 LLM 模型，可选 `deepseek-chat`（快速/便宜）或 `deepseek-reasoner`（深度推理/贵） |
| `temperature` | float | 0.1 | 采样温度 0.0~2.0。**0.0** = 完全确定（适合分类/评估），**0.7** = 有创造性（适合闲聊），**2.0** = 非常发散 |
| `max_tokens` | int | 500 | 单次响应最大 token 数。过小会导致输出截断，过大浪费 token |
| `response_format` | string | - | 输出格式。设置为 `json` 时强制 JSON 输出，便于程序解析 |

**推荐配置**：
- `supervisor`：temperature=0.1（意图分类需要确定性）
- `planner`：temperature=0.2（任务拆解需要合理但不发散）
- `critic`：temperature=0.0（评估必须确定性）
- `chat_simple`：temperature=0.7（闲聊可以有创造性）

### 3.2 记忆系统配置（`memory.*`）

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `max_turns` | int | 8 | L1 滑动窗口保留的对话轮数。**建议 5~10**，过多会导致 token 超限 |
| `max_tokens` | int | 3000 | L1 token 软上限。超过此值触发压缩，将旧对话转为摘要 |
| `hard_token_limit` | int | 4000 | L1 token 硬上限。超过此值强制丢弃最早的摘要 |
| `compress_strategy` | string | `hierarchical` | 压缩策略。`hierarchical` = 分层压缩（摘要的摘要），`simple` = 直接丢弃 |
| `importance_threshold` | float | 0.3 | L3 重要性评分阈值。低于此值的知识不参与默认召回 |
| `max_items` | int | 10000 | L3 知识库最大条目数。超过后触发驱逐策略 |

**Token 预算计算**：
- 1 个汉字 ≈ 1~2 个 token
- 1 轮对话（用户+助理）≈ 200~500 tokens
- 8 轮对话 ≈ 1600~4000 tokens

### 3.3 意图路由配置（`intent_routing.*`）

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `default_intent` | string | `web_default` | 默认意图。用户输入未命中任何规则时的路由目标 |
| `clarify_threshold` | float | 0.7 | 澄清阈值。意图置信度低于此值时，系统会追问用户以澄清意图 |
| `kb_strict_keywords` | list[str] | 见配置 | 触发 `kb_strict` 的关键词。命中后系统仅基于知识库回答，不联网 |
| `realtime_keywords` | list[str] | 见配置 | 实时性关键词。命中后系统强制联网搜索最新信息 |

**路由决策流程**：
1. 检查是否命中 `kb_strict_keywords` → `kb_strict`
2. 检查是否命中 `realtime_keywords` → `web_default`（强制联网）
3. 检查 `pre_retrieval` 命中度是否 > `kb_prefer_hit_threshold` → `kb_prefer`
4. 检查是否为闲聊问候 → `chitchat`
5. 其他 → `default_intent`（默认 `web_default`）

### 3.4 反思策略配置（`reflection.*`）

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `policy` | string | `always` | 反思策略。`always` = 每次都反思，`adaptive` = 条件跳过，`sampling` = 随机采样 |
| `max_replan` | int | 2 | 最大重规划次数。Critic 不通过时回滚到 Planner 的次数上限 |
| `model_switch_threshold` | float | 0.7 | 模型切换阈值。任务复杂度（Planner 评估）超过此值时，Critic 用 reasoner 替代 chat |
| `sampling.rate` | float | 0.5 | 采样率。`policy=sampling` 时生效，0.5 = 50% 的对话进行反思 |

### 3.5 评估指标阈值（`evaluation.metrics.*`）

| 指标 | good | warn | 说明 |
|---|---|---|---|
| `intent_confidence` | 0.9 | 0.7 | 越高越好。< 0.7 触发澄清 |
| `plan_step_count` | 5 | 8 | 越少越好。> 8 过度规划 |
| `replan_count` | 0 | 2 | 越少越好。≥ 2 质量预警 |
| `tool_success_rate` | 0.9 | 0.7 | 越高越好。< 0.7 工具链异常 |
| `answer_groundedness` | 0.8 | 0.6 | 越高越好。< 0.6 严重幻觉 |
| `critic_coherence_score` | 8.0 | 6.0 | 越高越好。< 6.0 逻辑断裂 |
| `answer_relevance` | 0.8 | 0.6 | 越高越好。< 0.6 跑题 |
| `e2e_latency_ms` | 5000 | 10000 | 越低越好。> 10s 需优化 |

### 3.6 成本控制配置（`cost_control.*`）

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `per_conversation_token_limit` | int | 20000 | 单次对话 token 上限。防止单次对话消耗过多预算 |
| `daily_budget_usd` | float | 1.0 | 每日预算上限（美元）。超出后触发降级策略 |
| `reasoner_ratio_alert_above` | float | 0.3 | reasoner 调用占比告警阈值。reasoner 占比过高说明成本控制失效 |
| `auto_downgrade_on_budget` | bool | true | 触发预算上限时是否自动降级为全 chat 模型 |

**成本估算示例**：
- 单次对话：输入 3000 tokens + 输出 2000 tokens
- chat 模型成本：3000×0.00014/1000 + 2000×0.00028/1000 = $0.00098
- reasoner 模型成本：3000×0.00055/1000 + 2000×0.00219/1000 = $0.00603
- 日预算 $1.0 ≈ 1000 次 chat 对话 ≈ 165 次 reasoner 对话

---

## 四、环境变量

将以下变量配置到 `backend/.env`（gitignore，不提交）：

```bash
# ============ 必需 ============
# DeepSeek API Key：从 https://platform.deepseek.com 获取
DEEPSEEK_API_KEY=sk-your-deepseek-key-here

# ============ Phase 1 必需 ============
# 博查搜索 API Key：从 https://open.bochaai.com 获取
BOCHA_API_KEY=sk-your-bocha-key-here

# ============ 可选（Tracing） ============
# LangSmith API Key：从 https://smith.langchain.com 获取
# 不设置则自动降级到本地 JSON trace
LANGSMITH_API_KEY=lskv2-your-langsmith-key

# ============ Phase 2 启用 ============
# PostgreSQL 数据库连接 URL
# DATABASE_URL=postgresql://user:password@localhost:5432/self_evolving_kb
```

`.env.example` 提交到 git 作为模板，`.env` 本身 gitignore。

---

## 五、配置加载机制

### 5.1 加载流程

```python
# app/core/config.py（示意）
from pydantic_settings import BaseSettings
from pydantic import Field
import yaml
from pathlib import Path


def load_config(config_path: str = "config.yaml") -> AppConfig:
    """加载配置的主入口"""
    # 1. 读取 YAML 文件
    raw = yaml.safe_load(Path(config_path).read_text())

    # 2. 递归展开 ${ENV_VAR} 引用
    raw = _expand_env_vars(raw)

    # 3. Pydantic 强类型校验
    config = AppConfig(**raw)

    # 4. 启动时打印生效配置（脱敏后）
    _log_effective_config(config)

    return config


def _expand_env_vars(obj: dict) -> dict:
    """递归展开 ${ENV_VAR} 引用"""
    ...


class AppConfig(BaseSettings):
    app: AppConfigSection
    llm: LLMConfig
    intent_routing: IntentRoutingConfig
    memory: MemoryConfig
    reflection: ReflectionConfig
    evaluation: EvaluationConfig
    cost_control: CostControlConfig
    tracing: TracingConfig
    tools: ToolsConfig
    storage: StorageConfig
    security: SecurityConfig
    api: ApiConfig
    logging: LoggingConfig
```

### 5.2 配置校验规则

Pydantic 在加载时自动校验：
- **类型正确**：如 `temperature` 必须是 0.0~2.0 的浮点数
- **必填字段存在**：缺少必要字段立即报错
- **枚举值合法**：如 `policy` 只能是 `always`、`adaptive`、`sampling`
- **跨字段约束**：如 `max_tokens < hard_token_limit`

校验失败立即抛出明确错误，启动中止。

### 5.3 业务代码使用

```python
# 节点通过依赖注入获取配置
class Supervisor:
    def __init__(self, llm_factory: LLMFactory, config: AppConfig):
        self.llm = llm_factory.get("supervisor")
        self.config = config.intent_routing

    async def __call__(self, state: GraphState) -> GraphState:
        # 从配置读取参数
        keywords = self.config.kb_strict_keywords
        threshold = self.config.clarify_threshold
        ...
```

**禁止**：业务代码直接读环境变量、读 YAML 文件、硬编码任何可调参数。

---

## 六、各阶段配置演进

| 配置项 | Phase 1 | Phase 2 | Phase 3 | Phase 4 |
|---|---|---|---|---|
| `memory.l1_working.enabled` | **true** | true | true | true |
| `memory.l2_session.enabled` | false | **true** | true | true |
| `memory.l3_knowledge.enabled` | false | **true** | true | true |
| `tools.vector_store.enabled` | false | **true** | true | true |
| `tools.document_parser.enabled` | false | **true** | true | true |
| `storage.backend` | local_json | **postgres** | postgres | postgres |
| `api.rate_limit.enabled` | false | false | **true** | true |
| `tracing.provider` | langsmith | langsmith | langsmith | **langfuse** |
| `app.environment` | development | development | **staging** | **production** |
| `app.debug` | true | true | false | **false** |

**关键约束**：
- Phase 2+ 才启用的配置项在 Phase 1 必须存在并默认 `false`/空
- 新增配置项必须有合理默认值，不破坏老配置加载
- 废弃配置项标记 `deprecated: true`，保留一个版本周期后再删

---

## 七、配置调优建议

### 7.1 Phase 1 推荐起点（低成本、易调试）

```yaml
# 给新手的稳妥起点
llm:
  roles:
    critic:
      model: deepseek-chat              # 先用 chat 省钱
memory:
  l1_working:
    max_turns: 8                        # 中等窗口
    max_tokens: 3000
reflection:
  policy: always                        # 每次反思
  max_replan: 2
cost_control:
  daily_budget_usd: 1.0                 # 日预算 1 美元
```

### 7.2 质量优先调优（提升回答质量）

```yaml
llm:
  roles:
    critic:
      model: deepseek-reasoner          # Critic 用 R1 提升幻觉检出
reflection:
  max_replan: 3                         # 多一次重试机会
evaluation:
  metrics:
    answer_groundedness:
      good: 0.85                        # 提高通过门槛
```

### 7.3 成本优先调优（降低 API 开销）

```yaml
llm:
  roles:
    planner:
      model: deepseek-chat              # 规划也用 chat（牺牲一些质量）
    critic:
      model: deepseek-chat
reflection:
  policy: adaptive                      # 简单任务跳过反思
  adaptive:
    skip_if_intent_in: [chitchat, web_default]
cost_control:
  per_conversation_token_limit: 10000
  daily_budget_usd: 0.5
```

### 7.4 性能优先调优（降低响应延迟）

```yaml
llm:
  roles:
    planner:
      model: deepseek-chat              # reasoner 慢，降级为 chat
memory:
  l1_working:
    max_turns: 5                        # 缩小窗口
    max_tokens: 2000
reflection:
  max_replan: 1                         # 减少重试
evaluation:
  metrics:
    e2e_latency_ms:
      good: 3000
      warn: 8000
```

---

## 八、常见配置问题

### Q1: 如何临时切换到 reasoner 模型做 A/B 测试？

修改 `llm.roles.critic.model` 为 `deepseek-reasoner`，重启服务即可。同时观察 `cost_usd` 指标，reasoner 成本约为 chat 的 5-10 倍。

### Q2: 预算超限后会发生什么？

触发 `daily_budget_usd` 后：
1. 所有 LLM 调用降级为 `deepseek-chat`
2. Critic 反思策略切换为 `adaptive`（跳过简单任务）
3. 日志中记录 WARN 级别告警
4. 次日自动恢复正常

### Q3: 如何添加新的意图类型？

1. 在 `app/graph/state.py` 的 `IntentType` 枚举中添加新值
2. 在 `app/agents/supervisor.py` 的 Prompt 中添加新意图的说明
3. 在 `app/graph/routing.py` 中添加新意图的路由逻辑
4. 在 `config.yaml` 的 `intent_routing` 中添加新意图的配置（如需）

### Q4: `max_tokens` 和 `hard_token_limit` 如何选择？

- `max_tokens`（软上限）：正常对话不应超过此值。超过则触发压缩，将旧对话转为摘要
- `hard_token_limit`（硬上限）：极端情况下的安全阈值。超过则丢弃摘要，仅保留最近对话
- 建议：`hard_token_limit` = `max_tokens` + 1000，预留压缩缓冲空间

### Q5: 为什么 `model_switch_threshold` 设为 0.7？

- 0.7 表示：任务复杂度超过 70%（如多步推理、逻辑分析）时，Critic 切换为 reasoner 进行深度评估
- 低于 0.7 的任务（如简单问答、闲聊），Critic 用 chat 即可，节省成本
- 此值越高，reasoner 调用越少，成本越低，但复杂任务的评估质量可能下降
