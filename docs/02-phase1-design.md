# Phase 1 详细设计

> 文档版本：v1.0.0
> 最后更新：2026-08-12
> 状态：设计评审中
> 依赖文档：[01-architecture.md](./01-architecture.md)

---

## 一、Phase 1 范围与验收标准

### 1.1 必做（P0）

| 编号 | 范围 | 验收标准 |
|---|---|---|
| P0-1 | LangGraph 多 Agent 工作流 | Supervisor → Router → Planner → Executor → Critic → Scribe 链路打通 |
| P0-2 | 五个 Agent 节点实现 | 各节点输入/输出/Prompt 完整，单元测试覆盖 |
| P0-3 | 混合 LLM 策略 | chat + reasoner 分级调用，由 config 驱动 |
| P0-4 | MCP 工具接入 | 博查搜索 MCP Server 实现 + MCP Client 封装 |
| P0-5 | L1 短期记忆 | 滑动窗口 + 触发压缩 + token 预算管理 |
| P0-6 | 本地 JSON 存储 | 多会话、多轮、摘要、索引完整 |
| P0-7 | 反思策略接口 | `AlwaysReflect` 默认实现，接口可扩展 |
| P0-8 | 量化评估日志 | 9 项指标每次对话输出，含 meaning/interpretation |
| P0-9 | CLI + FastAPI 双入口 | CLI 可交互聊天，FastAPI HTTP 接口可调用 |
| P0-10 | 单测 + 集成测 + eval 集 | 测试金字塔完整，eval 集可批量跑 |

### 1.2 接口预留（P1，不实现）

- L2 中期记忆、L3 长期知识库（接口签名定义，实现 `raise NotImplementedError`）
- 多用户、鉴权（`user_id` 字段预留，FastAPI dependency 占位）
- 知识库自迭代机制
- 向量库 / RAG（Phase 1 预检索命中度为 0，等价于全联网）

### 1.3 不做

- 前端页面
- Docker 配置（用户自维护）
- 生产部署相关（监控、CI/CD、灰度）

---

## 二、LangGraph 工作流详设

### 2.1 工作流图

```
                          START
                            │
                            ▼
                   ┌─────────────────┐
                   │   Supervisor    │  意图识别 + Top-3 预检索
                   │  (deepseek-chat)│
                   └────────┬────────┘
                            │
                  ┌─────────┴──────────┐
                  │  conditional_edges │
                  ▼                    ▼
        ┌──────────────────┐  ┌──────────────────┐
        │   chat_simple    │  │     Planner      │
        │  (deepseek-chat) │  │ (deepseek-reasoner)│
        │   闲聊直通        │  │   任务拆解        │
        └────────┬─────────┘  └────────┬─────────┘
                 │                      │
                 │                      ▼
                 │            ┌──────────────────┐
                 │            │    Executor      │
                 │            │ (deepseek-chat)  │
                 │            │  工具调用+草稿    │
                 │            └────────┬─────────┘
                 │                      │
                 │                      ▼
                 │            ┌──────────────────┐
                 │            │     Critic       │
                 │            │  反思评估         │
                 │            │ (策略选择模型)   │
                 │            └────────┬─────────┘
                 │                      │
                 │            ┌─────────┴──────────┐
                 │            │  conditional_edges │
                 │            ▼                    ▼
                 │    [pass] → Final          [fail] → Replan
                 │                                    │
                 │                                    ▼
                 │                          ┌──────────────────┐
                 │                          │  回滚到 Planner   │
                 │                          │  (max_replan=2)  │
                 │                          └────────┬─────────┘
                 │                                   │
                 │                          (超过上限 → 标记低置信度直通 Final)
                 │                                   │
                 └─────────────┬─────────────────────┘
                               ▼
                       ┌──────────────────┐
                       │     Scribe       │  异步：摘要+重要性评分
                       │ (deepseek-chat)  │  写入会话 JSON
                       └────────┬─────────┘
                                │
                                ▼
                          输出最终答案
                                │
                                ▼
                          生成 eval 日志
                                │
                                ▼
                              END
```

### 2.2 节点间通信

- 所有节点共享 `GraphState`（TypedDict），通过状态传递信息
- 每个节点必须更新 `state.trace` 中的对应字段，保证可追溯
- 节点失败不中断流程，写入 `state.errors` 后由 Critic 决定是否重试

---

## 三、Graph State Schema

```python
from typing import TypedDict, Literal, Optional, Any
from enum import Enum


class IntentType(str, Enum):
    CHITCHAT = "chitchat"                  # 闲聊，直通 chat_simple
    KB_STRICT = "kb_strict"                # 明确要求基于知识库（Phase 1 无 RAG，降级提示）
    KB_PREFER = "kb_prefer"                # 知识库高命中，优先 RAG（Phase 1 等价 web_default）
    WEB_DEFAULT = "web_default"            # 默认联网
    TASK_PLAN = "task_plan"                # 需多步规划的任务
    DOCUMENT_OP = "document_op"            # 文档操作（Phase 1 仅占位）


class TaskStep(TypedDict):
    step_id: int
    description: str                       # 本步要做什么
    tool_hint: Optional[str]               # 建议使用的工具（如 "web_search"）
    complexity: float                      # 0.0~1.0，影响 Critic 模型选择
    status: Literal["pending", "running", "done", "failed"]
    result: Optional[str]


class CriticResult(TypedDict):
    pass_judgement: bool                   # 是否通过
    scores: dict[str, float]               # 各维度评分
    reasoning: str                         # 评判理由
    suggested_fix: Optional[str]           # 若不通过，建议如何修正


class TraceEvent(TypedDict):
    node: str                              # 节点名
    model: str                             # 使用的模型
    input_tokens: int
    output_tokens: int
    latency_ms: int
    timestamp: str                         # ISO8601
    status: Literal["ok", "error", "degraded"]
    error: Optional[str]


class GraphState(TypedDict):
    # ===== 对话上下文 =====
    conv_id: str                           # 会话 ID
    user_input: str                        # 本轮用户输入
    messages: list[dict]                   # 完整对话历史（含本轮）
    working_memory: list[dict]             # L1 工作记忆（滑动窗口）
    history_summary: Optional[str]         # 历史压缩摘要

    # ===== Supervisor 输出 =====
    intent: IntentType
    intent_confidence: float               # 0.0~1.0
    pre_retrieval_topk: list[dict]         # Top-3 预检索结果（Phase 1 为空）
    pre_retrieval_scores: list[float]      # Top-3 命中度
    needs_clarification: bool              # 置信度过低时触发澄清

    # ===== Planner 输出 =====
    task_list: list[TaskStep]
    overall_complexity: float              # 任务整体复杂度

    # ===== Executor 输出 =====
    current_step_idx: int
    step_results: list[dict]               # 每步执行结果
    tool_calls: list[dict]                 # 工具调用记录
    draft_answer: str                      # 草稿答案

    # ===== Critic 输出 =====
    critic_result: Optional[CriticResult]
    replan_count: int                      # 已重规划次数

    # ===== 最终输出 =====
    final_answer: str
    low_confidence: bool                   # 是否低置信度（重试超限）

    # ===== Scribe 输出 =====
    summary: Optional[str]                 # 本轮摘要
    importance_score: Optional[float]      # 重要性评分 0.0~1.0
    extracted_facts: list[dict]            # 抽取的事实三元组（Phase 1 仅记录）

    # ===== 可观测性 =====
    trace_id: str                          # 贯穿全链路
    trace: list[TraceEvent]                # 各节点 trace 事件
    errors: list[dict]                     # 错误收集
    started_at: str
    finished_at: Optional[str]
```

**State 设计原则**：
- 每个节点的输出有独立字段，便于 trace 与回放
- `trace` 列表 append-only，记录所有节点执行情况
- `errors` 不中断流程，由 Critic 决定是否重试
- `conv_id` / `trace_id` 贯穿存储/日志/trace 三个可观测层

---

## 四、Agent 节点详设

### 4.1 Supervisor（监督者）

**职责**：意图识别 + Top-3 预检索 + 是否需要澄清

**输入**：`user_input`、`working_memory`、`history_summary`

**处理流程**：
1. 调用 LLM 做意图分类（强制 JSON 输出）
2. 异步触发 Top-3 向量预检索（Phase 1 返回空列表，命中度 0）
3. 若 `intent_confidence < clarify_threshold`，标记 `needs_clarification=True`
4. 检测 `kb_strict` 关键词（"我的笔记"、"知识库"、"我之前说过"等）

**Prompt 要点**：
```
你是一个意图识别器。根据用户输入和对话历史，输出 JSON：
{
  "intent": "chitchat | kb_strict | kb_prefer | web_default | task_plan | document_op",
  "confidence": 0.0~1.0,
  "reasoning": "判断理由"
}

规则：
- 默认 web_default（除非明确要求基于已有知识）
- 包含"我的笔记/知识库/我之前说过/基于我的"等关键词 → kb_strict
- 闲聊问候 → chitchat
- 需要多步推理或对比 → task_plan
```

**输出**：更新 `intent`、`intent_confidence`、`pre_retrieval_topk`、`pre_retrieval_scores`、`needs_clarification`

**错误处理**：LLM JSON 解析失败 → 默认 `web_default`，confidence=0.5，记 error

---

### 4.2 Planner（规划者）

**职责**：将复杂任务拆解为有序的 TaskStep 列表

**输入**：`user_input`、`intent`、`pre_retrieval_topk`、`history_summary`

**处理流程**：
1. 根据 `intent` 决定是否需要复杂规划
2. 调用 `deepseek-reasoner` 生成 TaskStep 列表（强制 JSON）
3. 计算每个 step 的 `complexity`（0.0~1.0）
4. 计算 `overall_complexity`（用于 Critic 模型选择）

**Prompt 要点**：
```
你是任务规划者。将用户任务拆解为可执行的步骤列表，每步包含：
- step_id: 序号
- description: 具体做什么
- tool_hint: 建议工具（web_search / file_read / direct_answer）
- complexity: 0.0~1.0

约束：
- 步骤数 1~5 步，超过 5 步需合并
- 简单问题只 1 步：直接回答
- 必须能被 Executor 执行，不要规划无法落地的步骤
```

**输出**：更新 `task_list`、`overall_complexity`

**降级**：reasoner 不可用 → 降级 chat + 告警

---

### 4.3 Executor（执行器）

**职责**：按 TaskStep 顺序执行，调用工具，生成草稿答案

**输入**：`task_list`、`working_memory`、`history_summary`、`pre_retrieval_topk`

**处理流程**：
1. 遍历 `task_list`，逐个执行
2. 根据 `tool_hint` 调用对应工具（通过 `ToolRegistry`）
3. 收集每步结果到 `step_results`
4. 所有步骤完成后，整合生成 `draft_answer`

**工具调用约束**：
- 联网搜索：调用博查 MCP，返回 top-k 摘要
- 文件读取：Phase 1 暂不实现，返回提示
- direct_answer：直接 LLM 生成

**Prompt 要点**（整合阶段）：
```
基于以下信息整合最终答案：
- 用户问题：{user_input}
- 各步执行结果：{step_results}
- 历史摘要：{history_summary}

要求：
- 答案必须锚定在执行结果中（避免幻觉）
- 引用信息来源（联网结果/知识库）
- 不确定的内容明确标注
```

**输出**：更新 `current_step_idx`、`step_results`、`tool_calls`、`draft_answer`

**错误处理**：工具调用失败 → 重试 2 次 → 跳过该步并标记 → 不中断流程

---

### 4.4 Critic（审查者）

**职责**：对草稿答案做多维度反思评估，不通过则触发重规划

**输入**：`draft_answer`、`user_input`、`step_results`、`overall_complexity`、`replan_count`

**处理流程**：
1. `ReflectionPolicy.should_reflect(state)` 判断是否反思（默认总是）
2. `ReflectionPolicy.select_model(state)` 选择模型（默认 chat，复杂任务 reasoner）
3. 调用 LLM 做多维度评分
4. 判定 `pass_judgement`
5. 不通过且 `replan_count < max_replan` → 标记 `needs_replan=True`

**评分维度**（写入 `critic_result.scores`）：
- `relevance`：答案与问题的相关性（0.0~1.0）
- `groundedness`：答案锚定在执行结果的比例（0.0~1.0，防幻觉核心）
- `coherence`：逻辑链自洽性（0.0~10.0，对应 reasoning_coherence）
- `completeness`：是否完整回答了问题（0.0~1.0）

**Prompt 要点**：
```
你是答案审查者。对以下草稿进行批判性评估：

用户问题：{user_input}
草稿答案：{draft_answer}
执行依据：{step_results}

输出 JSON：
{
  "pass_judgement": true/false,
  "scores": {
    "relevance": 0.0~1.0,
    "groundedness": 0.0~1.0,
    "coherence": 0.0~10.0,
    "completeness": 0.0~1.0
  },
  "reasoning": "评判理由",
  "suggested_fix": "不通过时的修正建议"
}

通过标准：relevance ≥ 0.7 AND groundedness ≥ 0.6 AND coherence ≥ 6.0 AND completeness ≥ 0.7
```

**输出**：更新 `critic_result`、`replan_count`（若重规划）

**超限处理**：`replan_count >= max_replan` 仍不通过 → `low_confidence=True`，直通 Final

---

### 4.5 Scribe（记录员）

**职责**：异步生成摘要 + 重要性评分 + 抽取事实，写入存储

**输入**：`final_answer`、`user_input`、`messages`

**处理流程**：
1. **异步触发**，不阻塞用户响应（先返回答案，后台运行 Scribe）
2. 生成本轮摘要（< 200 字）
3. 计算重要性评分（0.0~1.0，决定是否进入长期记忆）
4. 抽取事实三元组（Phase 1 仅记录，不入库）
5. 调用 `StorageBackend` 更新会话 JSON

**重要性评分依据**：
- 是否包含事实性信息（0.3）
- 是否涉及用户偏好（0.2）
- 是否是复杂任务结论（0.2）
- 用户后续是否引用（0.1，需 LLM 判断）
- 信息时效性（0.2）

**Prompt 要点**：
```
你是对话记录员。对本轮对话生成：
1. summary: < 200 字摘要
2. importance_score: 0.0~1.0（基于事实性/偏好/复杂度/时效性）
3. extracted_facts: 事实三元组列表 [{subject, predicate, object}]

输出 JSON。
```

**输出**：更新 `summary`、`importance_score`、`extracted_facts`，并触发存储更新

---

## 五、路由逻辑详设

### 5.1 Supervisor 后的条件路由

```python
def route_after_supervisor(state: GraphState) -> str:
    if state["needs_clarification"]:
        return "clarify"                    # Phase 1 简化为直通 chat_simple
    if state["intent"] == IntentType.CHITCHAT:
        return "chat_simple"
    if state["intent"] == IntentType.DOCUMENT_OP:
        return "unsupported"                # Phase 1 占位
    # kb_strict / kb_prefer / web_default / task_plan 都走完整链路
    return "planner"
```

### 5.2 Critic 后的条件路由

```python
def route_after_critic(state: GraphState) -> str:
    if state["critic_result"]["pass_judgement"]:
        return "scribe"
    if state["replan_count"] >= config.reflection.max_replan:
        state["low_confidence"] = True
        return "scribe"                     # 超限直通
    state["replan_count"] += 1
    return "planner"                        # 回滚重规划
```

### 5.3 意图与工具映射

| 意图 | Planner 是否调用 | Executor 工具 | 备注 |
|---|---|---|---|
| `chitchat` | 否（直通） | 无 | 直接 chat_simple 生成 |
| `kb_strict` | 是 | （Phase 1 无 RAG） | 提示用户"知识库建设中" |
| `kb_prefer` | 是 | web_search（Phase 1） | Phase 1 等价 web_default |
| `web_default` | 是 | web_search | 默认联网 |
| `task_plan` | 是 | 按步骤选工具 | 多步规划 |
| `document_op` | 否 | unsupported | Phase 1 占位 |

---

## 六、L1 短期记忆详设

### 6.1 数据结构

```python
class ShortTermMemory:
    """L1 工作记忆：滑动窗口 + 触发压缩"""

    def __init__(self, config: MemoryConfig):
        self.max_turns = config.l1_working.max_turns          # 默认 8
        self.max_tokens = config.l1_working.max_tokens        # 默认 3000
        self.hard_token_limit = config.l1_working.hard_token_limit  # 默认 4000
        self.messages: list[dict] = []                        # 当前窗口内消息
        self.compressed_summaries: list[str] = []             # 已压缩的历史摘要

    def add_message(self, role: str, content: str) -> None: ...
    def get_context(self) -> list[dict]:
        """返回给 LLM 的上下文：compressed_summaries + messages"""
        ...
    def maybe_compress(self, scribe_fn) -> None:
        """token 超阈值时，将最旧的 N 轮压缩成摘要"""
        ...
```

### 6.2 压缩触发条件

满足任一即触发压缩：
- `len(messages) > max_turns`（轮数超限）
- `token_count(messages) > max_tokens`（软阈值）

**压缩策略**：
1. 取最旧的 N 轮（N = 当前轮数 - max_turns + 2，保留缓冲）
2. 调用 Scribe 生成 < 200 字摘要
3. 摘要追加到 `compressed_summaries`（最多保留最近 3 条）
4. 从 `messages` 移除已压缩的轮次

**硬上限保护**：
- 若压缩后 `token_count > hard_token_limit`，进一步丢弃最旧的 `compressed_summaries[0]`
- 保证 LLM 输入永不超硬上限

### 6.3 与 Graph State 的关系

- `state.working_memory` = `ShortTermMemory.messages`
- `state.history_summary` = `"\n".join(ShortTermMemory.compressed_summaries)`
- 每轮对话开始时从存储加载，结束时持久化

### 6.4 L2/L3 接口预留

```python
class SessionMemory(ABC):
    """L2 中期记忆接口（Phase 2 实现）"""
    @abstractmethod
    async def get_user_preferences(self, user_id: str) -> dict: ...
    @abstractmethod
    async def add_recent_topic(self, user_id: str, topic: str) -> None: ...


class KnowledgeBase(ABC):
    """L3 长期知识库接口（Phase 2 实现）"""
    @abstractmethod
    async def retrieve(self, query: str, top_k: int = 5) -> list[dict]: ...
    @abstractmethod
    async def add(self, content: str, metadata: dict) -> str: ...
    @abstractmethod
    async def evict(self, policy: EvictionPolicy) -> int: ...
```

---

## 七、工具层详设

### 7.1 工具注册表

```python
class ToolRegistry:
    """统一工具注册表，按意图暴露可用工具"""

    def __init__(self, mcp_client: MCPClient, config: ToolConfig):
        self.mcp_client = mcp_client
        self.config = config
        self._register_mcp_tools()
        self._register_direct_tools()

    def get_tools_for_intent(self, intent: IntentType) -> list[Tool]:
        """根据意图返回可用工具列表"""
        ...

    def _register_mcp_tools(self) -> None:
        """注册 MCP 标准工具"""
        # 博查搜索 MCP
        ...

    def _register_direct_tools(self) -> None:
        """注册本地直连工具（绕过 MCP）"""
        # JSON 存储、向量检索（Phase 2）等
        ...
```

### 7.2 博查搜索 MCP Server

**自实现**，约 50 行代码，使用 `mcp` Python SDK：

```python
# tools/mcp/bocha_server.py （示意，非最终代码）
from mcp.server import Server
from mcp.types import Tool, TextContent

server = Server("bocha-search")

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [Tool(
        name="web_search",
        description="联网搜索，返回与查询相关的网页摘要",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "default": 5}
            },
            "required": ["query"]
        }
    )]

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "web_search":
        results = await bocha_client.search(**arguments)
        return [TextContent(type="text", text=format_results(results))]
```

**启动方式**：作为子进程通过 stdio 与 MCP Client 通信。

### 7.3 MCP Client 封装

```python
class MCPClient:
    """MCP Client 统一封装，管理多个 MCP Server 连接"""

    async def connect(self, server_name: str) -> None: ...
    async def call(self, server_name: str, tool_name: str, args: dict) -> Any: ...
    async def close(self) -> None: ...
```

### 7.4 工具调用记录

每次工具调用都记录到 `state.tool_calls`：

```python
{
    "tool_name": "web_search",
    "server": "bocha",
    "args": {"query": "...", "max_results": 5},
    "result_summary": "...",               # 截断的结果摘要
    "latency_ms": 1234,
    "success": True,
    "error": None
}
```

---

## 八、存储层详设

### 8.1 StorageBackend 接口

```python
class StorageBackend(ABC):
    """存储后端抽象，Phase 1 用 JSON，Phase 2 切 PostgreSQL"""

    @abstractmethod
    async def create_conversation(self, conv_id: str, metadata: dict) -> None: ...

    @abstractmethod
    async def append_message(self, conv_id: str, message: dict) -> None: ...

    @abstractmethod
    async def update_summary(self, conv_id: str, summary: str, importance: float) -> None: ...

    @abstractmethod
    async def get_conversation(self, conv_id: str) -> dict: ...

    @abstractmethod
    async def list_conversations(self, limit: int = 50, offset: int = 0) -> list[dict]: ...

    @abstractmethod
    async def delete_conversation(self, conv_id: str) -> None: ...
```

### 8.2 JSON 存储实现

**目录结构**：
```
backend/data/
├── conversations/
│   └── {conv_id}/
│       ├── messages.jsonl       # 每行一条消息（append-only，便于增量写）
│       ├── summary.json         # 最新摘要 + 重要性评分
│       └── meta.json            # 创建时间、最后更新、消息数、token统计
└── index.json                   # 会话列表索引（侧边栏快速加载）
```

**`messages.jsonl` 格式**（每行一条）：
```json
{"msg_id":"m_001","role":"user","content":"...","timestamp":"...","trace_id":"..."}
{"msg_id":"m_002","role":"assistant","content":"...","timestamp":"...","trace_id":"...","meta":{"intent":"web_default","tokens":{"input":320,"output":580},"latency_ms":4200}}
```

**`summary.json` 格式**：
```json
{
  "current_summary": "本轮对话讨论了...",
  "importance_score": 0.72,
  "extracted_facts": [{"subject":"...","predicate":"...","object":"..."}],
  "updated_at": "2026-08-12T14:30:22+08:00"
}
```

**`meta.json` 格式**：
```json
{
  "conv_id": "conv_xxx",
  "created_at": "...",
  "updated_at": "...",
  "message_count": 12,
  "total_tokens": {"input": 3200, "output": 5800},
  "total_cost_usd": 0.031,
  "title": "关于装饰器的讨论"  // 由 Scribe 自动生成
}
```

**`index.json` 格式**：
```json
{
  "conversations": [
    {
      "conv_id": "conv_xxx",
      "title": "关于装饰器的讨论",
      "last_message_preview": "...",
      "updated_at": "...",
      "message_count": 12
    }
  ],
  "updated_at": "..."
}
```

### 8.3 写入策略

- `messages.jsonl`：append-only，每次只追加一行，性能高
- `summary.json` / `meta.json`：每次 Scribe 完成后整体覆盖
- `index.json`：每次会话更新后整体覆盖（Phase 1 会话数少，性能足够）
- 所有写入通过 `asyncio.Lock` 保证并发安全

---

## 九、CLI 与 FastAPI 双入口

### 9.1 CLI 入口

```bash
# 交互聊天
python -m app.cli.main chat

# 指定会话
python -m app.cli.main chat --conv-id conv_xxx

# 列出会话
python -m app.cli.main conversations list

# Eval Mode（批量跑黄金数据集）
python -m app.cli.main eval --dataset tests/fixtures/golden_qa.json

# 生成回归报告
python -m app.cli.main eval --report --compare-with last
```

**CLI 交互特性**：
- 用 `rich` 库渲染 Markdown 答案
- 支持 `/exit` `/new` `/list` `/switch <conv_id>` 等命令
- 显示当前意图、模型、token 等元信息（debug 模式）

### 9.2 FastAPI 入口

```python
# POST /api/v1/chat
# 请求体
{
  "message": "用户输入",
  "conv_id": "可选，不传则新建会话"
}
# 响应
{
  "conv_id": "...",
  "answer": "...",
  "trace_id": "...",
  "meta": {
    "intent": "web_default",
    "model_used": {"planner": "deepseek-reasoner", ...},
    "tokens": {...},
    "latency_ms": 4200,
    "low_confidence": false
  }
}

# GET /api/v1/conversations
# GET /api/v1/conversations/{conv_id}
# DELETE /api/v1/conversations/{conv_id}
# POST /api/v1/eval  # 触发 eval mode
```

**FastAPI 特性**：
- 异步流式响应（SSE）支持答案逐字输出
- 自动 OpenAPI 文档 `/docs`
- Phase 1 单用户，无鉴权；Phase 3 加 JWT dependency

---

## 十、错误处理与降级

### 10.1 错误分级

| 级别 | 示例 | 处理 |
|---|---|---|
| Fatal | 配置缺失、API key 无效 | 启动失败，明确报错 |
| Error | LLM 调用超时、工具失败 | 重试 → 降级 → 记 error 继续 |
| Warn | token 接近上限、reasoner 降级 | 告警日志，不中断 |
| Info | 意图切换、压缩触发 | 记录到 trace |

### 10.2 降级链

```
deepseek-reasoner 不可用 → 降级 deepseek-chat + warn 日志
deepseek-chat 不可用     → 重试 3 次 → 返回降级提示 "服务暂时不可用"
博查搜索 MCP 失败        → 重试 2 次 → Executor 跳过联网，仅用 LLM 知识
LangSmith 连接失败       → 自动切本地 JSON trace + warn 日志
JSON 存储写入失败        → 重试 3 次 → 内存保留 + Fatal 告警
```

### 10.3 超时与重试

| 调用 | 超时 | 重试 | 退避 |
|---|---|---|---|
| LLM 调用 | 60s | 2 次 | 指数退避 1s/2s |
| 博查搜索 | 10s | 2 次 | 固定 1s |
| LangSmith 上报 | 5s | 0 次（失败即降级） | - |
| JSON 写入 | 5s | 3 次 | 指数退避 |

---

## 十一、启动流程

```python
# app/core/bootstrap.py （示意）
async def bootstrap() -> Application:
    # 1. 加载配置
    config = load_config("config.yaml")

    # 2. 初始化日志
    setup_logging(config)

    # 3. 初始化 tracing（LangSmith 或降级本地）
    tracing = setup_tracing(config)

    # 4. 初始化 LLM 工厂
    llm_factory = LLMFactory(config.llm)

    # 5. 初始化存储
    storage = JSONStorage(config.storage)  # Phase 1

    # 6. 初始化 MCP Client + 工具注册表
    mcp_client = MCPClient(config.tools)
    await mcp_client.connect("bocha")
    tool_registry = ToolRegistry(mcp_client, config.tools)

    # 7. 初始化记忆系统
    short_term_memory = ShortTermMemory(config.memory.l1_working)

    # 8. 初始化反思策略
    reflection_policy = AlwaysReflectPolicy(config.reflection)

    # 9. 构建 LangGraph
    graph = build_graph(
        llm_factory=llm_factory,
        tool_registry=tool_registry,
        storage=storage,
        reflection_policy=reflection_policy,
        config=config,
    )

    return Application(graph=graph, storage=storage, config=config)
```

---

## 十二、关键依赖清单

```toml
# pyproject.toml 核心依赖（示意）
[project]
dependencies = [
    "langgraph>=0.2",
    "langchain>=0.3",
    "langchain-core>=0.3",
    "langchain-mcp-adapters>=0.1",
    "langchain-deepseek>=0.1",        # DeepSeek 集成
    "mcp>=1.0",                       # MCP SDK
    "fastapi>=0.110",
    "uvicorn[standard]>=0.27",
    "typer>=0.12",
    "rich>=13",
    "pydantic>=2.5",
    "pydantic-settings>=2.1",
    "httpx>=0.27",                    # 异步 HTTP（博查调用）
    "structlog>=24.1",
    "tiktoken>=0.7",                  # token 计数
    "tenacity>=8.2",                  # 重试
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "pytest-cov>=4.1",
    "respx>=0.21",                    # HTTP mock
    "ruff>=0.5",
    "mypy>=1.10",
]
```
