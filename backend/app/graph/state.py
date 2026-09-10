"""
LangGraph 全局 State 定义

定义所有 Agent 节点共享的状态结构。
使用 TypedDict 确保 LangGraph 的类型推断正确。

State 流转：
    User Input → Supervisor → Planner → Executor → Critic → (通过/重规划)
                                    ↓                        ↓
                                  Tools                    Scribe
                                    ↓                        ↓
                                 Output ← ← ← ← ← ← ← ← Memory Update
"""

from __future__ import annotations

from enum import Enum
from typing import Any, TypedDict

from langchain_core.messages import BaseMessage

# ============================================================
# 枚举定义
# ============================================================

class IntentType(str, Enum):
    """意图类型枚举"""
    CHITCHAT = "chitchat"               # 闲聊
    KB_STRICT = "kb_strict"             # 严格基于知识库
    KB_PREFER = "kb_prefer"             # 优先知识库
    WEB_DEFAULT = "web_default"         # 默认联网增强
    TASK_PLAN = "task_plan"             # 复杂任务规划
    CLARIFY = "clarify"                 # 需要澄清


class TaskStatus(str, Enum):
    """任务步骤状态"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class ReflectionResult(str, Enum):
    """反思评估结果"""
    PASS = "pass"
    NEEDS_REPLAN = "needs_replan"
    NEEDS_REWRITE = "needs_rewrite"


# ============================================================
# 数据结构
# ============================================================

class TaskStep(TypedDict, total=False):
    """
    单个任务步骤（Planner 输出的最小执行单元）。

    用于多步任务的分解，每个步骤对应一个工具调用或 LLM 调用。
    """
    step_id: int                        # 步骤序号（从 1 开始）
    description: str                    # 步骤描述
    tool: str                           # 使用的工具名（如 "web_search", "rag_retrieve", "llm_generate"）
    tool_input: dict[str, Any]          # 工具输入参数
    depends_on: list[int]               # 依赖的前置步骤 ID
    status: str                         # TaskStatus 枚举值
    result: str | None                  # 执行结果
    error: str | None                   # 错误信息
    latency_ms: int                     # 执行耗时


class ToolCallRecord(TypedDict, total=False):
    """工具调用记录（用于评估和 tracing）"""
    tool_name: str                      # 工具名称
    input: dict[str, Any]               # 输入参数
    output: Any                         # 输出结果
    success: bool                       # 是否成功
    latency_ms: int                     # 调用耗时
    error: str | None                   # 错误信息


class CriticEvaluation(TypedDict, total=False):
    """审查者（Critic）的评估结果"""
    passed: bool                        # 是否通过
    result: str                         # ReflectionResult 枚举值
    groundedness_score: float           # 答案锚定度（0.0~1.0，防幻觉）
    coherence_score: float              # 逻辑自洽性（0.0~10.0）
    relevance_score: float              # 回答相关性（0.0~1.0）
    issues: list[str]                   # 发现的问题列表
    suggestions: list[str]              # 改进建议
    reasoning: str                      # 评估推理过程
    model_used: str                     # 使用的模型（chat/reasoner）


class ConversationMetrics(TypedDict, total=False):
    """单轮对话的量化指标（用于评估体系）"""
    intent_confidence: float            # 意图识别置信度
    intent_type: str                    # 识别到的意图
    plan_step_count: int                # 规划步数
    replan_count: int                   # 重规划次数
    tool_success_rate: float            # 工具调用成功率
    answer_groundedness: float          # 答案锚定度
    critic_coherence_score: float       # 逻辑自洽性
    answer_relevance: float             # 回答相关性
    e2e_latency_ms: int                 # 端到端延迟
    total_input_tokens: int             # 总输入 token
    total_output_tokens: int            # 总输出 token
    total_cost_usd: float               # 总成本
    model_used: list[str]               # 使用的模型列表
    # ============ 质量追踪：重试/降级（便于监控功能质量）============
    llm_retried: bool                   # 本次对话是否发生过 LLM 重试
    llm_retry_count: int                # 本次对话 LLM 重试次数
    llm_degraded: bool                  # 本次对话是否发生过模型降级
    llm_degradation_count: int          # 本次对话模型降级次数


# ============================================================
# 全局 Graph State
# ============================================================

class GraphState(TypedDict, total=False):
    """
    LangGraph 全局状态。

    所有节点共享此状态，通过读取和写入字段来传递信息。
    使用 total=False 表示所有字段都是可选的（LangGraph 增量更新）。

    状态生命周期：
        1. 初始化：user_input, conversation_id, trace_id
        2. Supervisor 写入：intent, intent_confidence, pre_retrieval_results
        3. Planner 写入：task_steps, task_complexity
        4. Executor 写入：tool_calls, draft_answer
        5. Critic 写入：evaluation
        6. 循环：如果 needs_replan 且未超 max_replan，回到 Planner
        7. Scribe 写入：final_answer, summary, metrics
    """
    # ============ 会话元信息 ============
    conversation_id: str                # 会话 ID
    trace_id: str                       # 追踪 ID（关联 LangSmith/local trace）
    user_id: str                        # 用户 ID（Phase 1 固定为 "default"）

    # ============ 用户输入 ============
    user_input: str                     # 当前用户输入
    conversation_history: list[BaseMessage]  # 完整对话历史（LangChain 消息格式）
    compressed_summaries: list[str]     # 压缩的历史摘要（L1 记忆溢出时）

    # ============ Supervisor 输出 ============
    intent: str                         # 识别到的意图（IntentType 枚举值）
    intent_confidence: float            # 意图置信度（0.0~1.0）
    needs_clarification: bool           # 是否需要澄清
    clarification_question: str         # 澄清问题（needs_clarification=True 时）
    pre_retrieval_results: list[dict[str, Any]]  # RAG 预检索结果（Phase 2 由 rag_retrieval 节点写入）
    rag_fallback_message: str                    # RAG 检索无结果时的降级提示文案

    # ============ Planner 输出 ============
    task_steps: list[TaskStep]          # 任务步骤列表
    task_complexity: float              # 任务复杂度（0.0~1.0，用于模型切换决策）
    plan_reasoning: str                 # 规划推理过程

    # ============ Executor 输出 ============
    tool_calls: list[ToolCallRecord]    # 工具调用记录
    draft_answer: str                   # 草稿答案
    execution_context: str              # 执行上下文（RAG/搜索结果拼接）

    # ============ Critic 输出 ============
    evaluation: CriticEvaluation        # 评估结果
    replan_count: int                   # 已重规划次数
    should_replan: bool                 # 是否需要重规划
    rewrite_count: int                  # 已重写答案次数
    should_rewrite: bool                # 是否需要重写答案（needs_rewrite）
    rewrite_feedback: str               # 重写反馈（issues+suggestions 拼接）

    # ============ Scribe 输出 ============
    final_answer: str                   # 最终答案
    summary: str                        # 对话摘要
    importance_score: float             # 重要性评分（0.0~1.0）
    should_persist: bool                # 是否值得持久化到知识库

    # ============ 量化指标 ============
    metrics: ConversationMetrics        # 量化评估指标
    errors: list[str]                   # 错误记录

    # ============ Per-request LLM 统计（P0-1 修复）============
    # 由外层 _run_chat 在 graph.ainvoke 前注入快照，
    # Scribe 读取此字段计算 per-request delta，避免全局污染
    llm_stats_snapshot: Any             # StatsSnapshot 对象（LLMFactory.snapshot_stats() 返回）


# ============================================================
# 工具函数
# ============================================================

def create_initial_state(
    user_input: str,
    conversation_id: str,
    trace_id: str | None = None,
    user_id: str = "default",
    history: list[BaseMessage] | None = None,
) -> GraphState:
    """
    创建初始 GraphState。

    Args:
        user_input: 用户输入文本
        conversation_id: 会话 ID
        trace_id: 追踪 ID（若为 None 则自动生成）
        user_id: 用户 ID（Phase 1 固定为 "default"）
        history: 之前的对话历史

    Returns:
        初始化的 GraphState
    """
    import uuid

    return GraphState(
        conversation_id=conversation_id,
        trace_id=trace_id or str(uuid.uuid4()),
        user_id=user_id,
        user_input=user_input,
        conversation_history=history or [],
        compressed_summaries=[],
        intent="",
        intent_confidence=0.0,
        needs_clarification=False,
        clarification_question="",
        pre_retrieval_results=[],
        rag_fallback_message="",
        task_steps=[],
        task_complexity=0.0,
        plan_reasoning="",
        tool_calls=[],
        draft_answer="",
        execution_context="",
        evaluation={},
        replan_count=0,
        should_replan=False,
        final_answer="",
        summary="",
        importance_score=0.0,
        should_persist=False,
        metrics={},
        errors=[],
    )
