"""
LangGraph 工作流构建器

构建完整的多 Agent 工作流图，包含：
- 5 个 Agent 节点（Supervisor, Planner, Executor, Critic, Scribe）
- 条件路由（基于意图和反思结果）
- 重规划循环（Critic → Planner）
- 闲聊/澄清直通路径

工作流图：
                    START
                      │
                      ▼
                ┌───────────┐
                │ Supervisor │
                └─────┬─────┘
                      │
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
   [chitchat]    [clarify]    [其他意图]
        │             │             │
        ▼             ▼             ▼
   chat_simple    clarify_q    Planner
        │             │             │
        │             │             ▼
        │             │         Executor
        │             │             │
        │             │             ▼
        │             │          Critic
        │             │             │
        │             │      ┌──────┴──────┐
        │             │      ▼             ▼
        │             │  [通过]        [重规划]
        │             │      │             │
        │             │      │         Planner
        │             │      │        (循环)
        │             │      │
        ▼             ▼      ▼
                Scribe
                      │
                      ▼
                    END
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph

from app.agents.strategies.base import ReflectionStrategy
from app.core.config import AppConfig
from app.core.logging import get_logger
from app.graph.state import GraphState, IntentType

logger = get_logger(__name__)


# ============================================================
# 路由函数
# ============================================================

def route_after_supervisor(state: GraphState) -> str:
    """
    Supervisor 后的路由决策。

    根据意图决定下一步：
    - chitchat → chat_simple（闲聊直通）
    - clarify → clarify_question（澄清问题）
    - 其他 → planner（进入规划）
    """
    intent = state.get("intent", "")
    needs_clarification = state.get("needs_clarification", False)

    if needs_clarification or intent == IntentType.CLARIFY.value:
        return "clarify"
    elif intent == IntentType.CHITCHAT.value:
        return "chat_simple"
    else:
        return "planner"


def route_after_critic(
    state: GraphState,
    reflection_strategy: ReflectionStrategy,
) -> str:
    """
    Critic 后的路由决策。

    根据反思策略决定：
    - 通过 → scribe
    - 需要重规划 → planner（循环）
    - 需要重写答案 → rewrite（循环，needs_rewrite 分支）
    """
    if reflection_strategy.should_replan(state):
        return "planner"
    if state.get("should_rewrite"):
        return "rewrite"
    return "scribe"


# ============================================================
# 闲聊和澄清节点
# ============================================================

async def chat_simple_node(
    state: GraphState,
    config: AppConfig,
    llm_factory: Any,
) -> dict[str, Any]:
    """闲聊直通节点：跳过规划/执行/反思，直接生成回复。"""

    from app.agents.prompts.templates import CHAT_SIMPLE_PROMPT

    try:
        messages = CHAT_SIMPLE_PROMPT.format_messages(user_input=state["user_input"])
        response = await llm_factory.ainvoke_with_stats("chat_simple", messages)

        content = response.content if hasattr(response, "content") else str(response)

        # 闲聊直接作为最终答案
        return {
            "final_answer": content,
            "draft_answer": content,
            "task_steps": [],
            "tool_calls": [],
            "evaluation": {
                "passed": True,
                "result": "pass",
                "groundedness_score": 1.0,
                "coherence_score": 10.0,
                "relevance_score": 1.0,
                "issues": [],
                "suggestions": [],
                "reasoning": "闲聊直通，跳过反思",
                "model_used": "chat_simple",
            },
        }
    except Exception as e:
        logger.error("闲聊节点失败", error=str(e))
        return {
            "final_answer": "抱歉，我遇到了一些问题，请稍后再试。",
            "errors": [f"chat_simple: {e}"],
        }


async def clarify_node(
    state: GraphState,
    config: AppConfig,
    llm_factory: Any,
) -> dict[str, Any]:
    """澄清问题节点：向用户提问以澄清意图。"""
    from app.agents.prompts.templates import CLARIFY_PROMPT

    try:
        messages = CLARIFY_PROMPT.format_messages(
            user_input=state["user_input"],
            intent=state.get("intent", "unknown"),
            confidence=state.get("intent_confidence", 0.0),
        )
        response = await llm_factory.ainvoke_with_stats("chat_simple", messages)

        content = response.content if hasattr(response, "content") else str(response)

        return {
            "final_answer": content,
            "draft_answer": content,
            "task_steps": [],
            "tool_calls": [],
            "evaluation": {
                "passed": True,
                "result": "pass",
                "groundedness_score": 1.0,
                "coherence_score": 10.0,
                "relevance_score": 1.0,
                "issues": [],
                "suggestions": [],
                "reasoning": "澄清问题，跳过反思",
                "model_used": "chat_simple",
            },
        }
    except Exception as e:
        logger.error("澄清节点失败", error=str(e))
        # 降级：使用已有的澄清问题
        clarification = state.get("clarification_question", "请问您能详细描述一下您的需求吗？")
        return {
            "final_answer": clarification,
            "errors": [f"clarify: {e}"],
        }


# ============================================================
# RAG 检索节点（Phase 2）
# ============================================================

async def rag_retrieval_node(
    state: GraphState,
    vector_store: Any,
    config: AppConfig,
    llm_factory: Any = None,
) -> dict[str, Any]:
    """
    RAG 预检索节点：在 Supervisor 之后、Planner 之前执行知识库检索。

    根据意图从知识库中检索相关信息，注入到 state 的 pre_retrieval_results。
    当 vector_store 为 None（L3 未启用）时，静默跳过。
    当配置启用混合检索时，将 vector_store 包装为 HybridRetriever（向量+BM25+可选重排）。
    """
    if vector_store is None:
        # L3 未启用，直接跳过
        return {"pre_retrieval_results": []}

    user_input = state.get("user_input", "")
    user_id = state.get("user_id", "default")
    intent = state.get("intent", "")

    try:
        from app.tools.rag.retriever import RAGRetriever

        # 混合检索：向量 + BM25 多路召回 → RRF 融合 → 可选重排
        retrieval_cfg = config.memory.l3_knowledge.retrieval
        if retrieval_cfg.hybrid_enabled:
            from app.tools.rag.hybrid import HybridRetriever

            vector_store = HybridRetriever(
                vector_store,
                config={
                    "bm25_enabled": retrieval_cfg.bm25_enabled,
                    "bm25_cache_ttl": retrieval_cfg.bm25_cache_ttl,
                    "bm25_page_size": retrieval_cfg.bm25_page_size,
                    "rerank_enabled": retrieval_cfg.rerank_enabled,
                    "rerank_top_n": retrieval_cfg.rerank_top_n,
                    "rerank_role": retrieval_cfg.rerank_role,
                    "query_rewrite_enabled": retrieval_cfg.query_rewrite_enabled,
                    "query_rewrite_role": retrieval_cfg.query_rewrite_role,
                },
                llm_factory=llm_factory,
            )

        rag_config = {
            "retrieval_top_k": config.memory.l3_knowledge.retrieval_top_k,
            "importance_threshold": config.memory.l3_knowledge.importance_threshold,
        }
        retriever = RAGRetriever(vector_store, rag_config)
        result = await retriever.retrieve_for_query(user_input, user_id, intent)

        logger.info(
            "RAG 检索完成",
            mode=result.mode,
            retrieval_count=result.retrieval_count,
            latency_ms=result.latency_ms,
            intent=intent,
        )

        return {
            "pre_retrieval_results": result.context or [],
            "rag_fallback_message": result.fallback_message,
        }
    except Exception as e:
        logger.warning("RAG 检索失败，降级为空结果", error=str(e))
        # 区分「检索失败」与「无结果」：失败时给 rag_fallback_message 打标记（RAG-09）
        return {
            "pre_retrieval_results": [],
            "rag_fallback_message": f"检索失败: {type(e).__name__}",
        }


# ============================================================
# Graph 构建器
# ============================================================

class GraphBuilder:
    """
    LangGraph 工作流构建器。

    负责将所有 Agent 节点组装为完整的工作流图。
    """

    def __init__(
        self,
        config: AppConfig,
        llm_factory: Any,
        tool_registry: Any,
        memory: Any,
        reflection_strategy: ReflectionStrategy,
        vector_store: Any = None,
    ):
        self.config = config
        self.llm_factory = llm_factory
        self.tool_registry = tool_registry
        self.memory = memory
        self.reflection_strategy = reflection_strategy
        self.vector_store = vector_store  # Phase 2: DirectVectorStore | None

        # 创建 Agent 实例
        from app.agents.factory import AgentFactory
        self.agent_factory = AgentFactory(
            llm_factory=llm_factory,
            config=config,
            tool_registry=tool_registry,
            memory=memory,
        )
        self.agents = self.agent_factory.create_all()

    def build(self):
        """
        构建并编译 LangGraph 工作流。

        Returns:
            编译后的 LangGraph 可执行图
        """
        # 创建状态图
        workflow = StateGraph(GraphState)

        # ============ 添加节点 ============
        workflow.add_node("supervisor", self.agents["supervisor"])
        workflow.add_node("planner", self.agents["planner"])
        workflow.add_node("executor", self.agents["executor"])
        workflow.add_node("critic", self.agents["critic"])
        workflow.add_node("scribe", self.agents["scribe"])

        # 闲聊和澄清节点（使用闭包捕获依赖）
        async def _chat_simple(state: GraphState) -> dict[str, Any]:
            return await chat_simple_node(state, self.config, self.llm_factory)

        async def _clarify(state: GraphState) -> dict[str, Any]:
            return await clarify_node(state, self.config, self.llm_factory)

        # Phase 2: RAG 检索节点
        async def _rag_retrieval(state: GraphState) -> dict[str, Any]:
            return await rag_retrieval_node(
                state, self.vector_store, self.config, self.llm_factory
            )

        # needs_rewrite 分支：根据 Critic 反馈重写答案（不重跑工具）
        async def _rewrite(state: GraphState) -> dict[str, Any]:
            executor_agent = self.agents["executor"]
            try:
                from app.tools.rag.format import format_rag_executor_reference

                rag_context = format_rag_executor_reference(
                    state.get("pre_retrieval_results", [])
                )
                new_draft = await executor_agent.rewrite_answer(
                    user_input=state.get("user_input", ""),
                    draft_answer=state.get("draft_answer", ""),
                    feedback=state.get("rewrite_feedback", ""),
                    rag_context=rag_context,
                )
                return {"draft_answer": new_draft}
            except Exception as e:
                logger.warning("答案重写节点失败，保留原草稿", error=str(e))
                return {"draft_answer": state.get("draft_answer", "")}

        workflow.add_node("chat_simple", _chat_simple)
        workflow.add_node("clarify", _clarify)
        workflow.add_node("rag_retrieval", _rag_retrieval)
        workflow.add_node("rewrite", _rewrite)

        # ============ 添加边 ============

        # 入口 → Supervisor
        workflow.set_entry_point("supervisor")

        # Supervisor → 条件路由
        workflow.add_conditional_edges(
            "supervisor",
            route_after_supervisor,
            {
                "chat_simple": "chat_simple",
                "clarify": "clarify",
                "planner": "rag_retrieval",  # Phase 2: 非 chitchat/clarify → RAG → Planner
            },
        )

        # 闲聊/澄清 → Scribe（直接结束）
        workflow.add_edge("chat_simple", "scribe")
        workflow.add_edge("clarify", "scribe")

        # RAG 检索 → Planner
        workflow.add_edge("rag_retrieval", "planner")

        # Planner → Executor
        workflow.add_edge("planner", "executor")

        # Executor → Critic
        workflow.add_edge("executor", "critic")

        # Critic → 条件路由（通过→Scribe / 重规划→Planner）
        def _route_after_critic(state: GraphState) -> str:
            return route_after_critic(state, self.reflection_strategy)

        workflow.add_conditional_edges(
            "critic",
            _route_after_critic,
            {
                "scribe": "scribe",
                "planner": "planner",
                "rewrite": "rewrite",
            },
        )

        # 重写答案 → Critic（重新评估）
        workflow.add_edge("rewrite", "critic")

        # Scribe → END
        workflow.add_edge("scribe", END)

        # 编译图
        app = workflow.compile()

        logger.info("LangGraph 工作流构建完成")
        return app

    def get_graph_diagram(self) -> str:
        """返回工作流图的文字描述（用于调试）"""
        return """
        工作流图：

            START → Supervisor
                         │
            ┌────────────┼────────────┐
            ▼            ▼            ▼
        chitchat      clarify     planner(其他意图)
            │            │            │
            ▼            ▼            ▼
        chat_simple   clarify_q   executor
            │            │            │
            └────────────┴────────────┘
                         │
                         ▼
                      scribe ←──── critic(通过)
                         │            ↑
                         │            │
                         │       critic(重规划) → planner
                         │
                         ▼
                       END
        """
