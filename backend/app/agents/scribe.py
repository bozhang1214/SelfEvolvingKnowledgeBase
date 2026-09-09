"""
记录员 Agent 节点模块

ScribeAgent 作为工作流的末端节点，负责：
- 生成对话摘要（不超过 200 字）
- 评估对话重要性（0.0~1.0）
- 判断是否值得持久化到知识库
- 提取关键事实与话题
- 汇总生成 ConversationMetrics 写入 state.metrics

注意：Scribe 不调用外部工具，只做摘要和评估。
若 final_answer 为空，则使用 draft_answer 作为最终答案。

输出字段写入 GraphState：
    final_answer, summary, importance_score, should_persist, metrics
"""

from __future__ import annotations

from typing import Any

from app.agents.base import BaseAgent
from app.agents.prompts.templates import SCRIBE_PROMPT
from app.core.exceptions import AgentError
from app.core.utils import safe_float
from app.graph.state import GraphState


class ScribeAgent(BaseAgent):
    """
    记录员 Agent：摘要生成与重要性评估。

    使用 SCRIBE_PROMPT 与 "scribe" 角色的 LLM。
    不调用任何外部工具，仅基于 user_input 与 final_answer 生成摘要与评估。
    """

    async def __call__(self, state: GraphState) -> dict[str, Any]:
        """
        执行记录员逻辑，生成摘要与评估。

        Args:
            state: 当前 GraphState，需包含 user_input 与
                   final_answer 或 draft_answer

        Returns:
            state 更新字典，包含：
            - final_answer: 最终答案（若原为空则使用 draft_answer）
            - summary: 对话摘要
            - importance_score: 重要性评分
            - should_persist: 是否持久化
            - metrics: ConversationMetrics 量化指标
        """
        try:
            user_input = state.get("user_input", "")
            if not user_input:
                raise AgentError(
                    "user_input 为空，无法生成摘要",
                    agent_name="ScribeAgent",
                )

            # final_answer 优先，否则用 draft_answer
            final_answer = state.get("final_answer", "") or state.get(
                "draft_answer", ""
            )
            if not final_answer:
                raise AgentError(
                    "final_answer 与 draft_answer 均为空，无法生成摘要",
                    agent_name="ScribeAgent",
                )

            # 渲染 Prompt
            messages = SCRIBE_PROMPT.format_messages(
                user_input=user_input,
                final_answer=final_answer,
            )

            # 调用 LLM（带统计）
            response = await self.llm_factory.ainvoke_with_stats(
                "scribe", messages
            )

            # 解析 JSON
            data = await self._parse_json_response(response)

            summary = str(data.get("summary", "")).strip()
            importance_score = safe_float(
                data.get("importance_score"), 0.0, 1.0
            )
            should_persist = bool(data.get("should_persist", False))
            # 若重要性低于 0.3，强制不持久化（与 Prompt 中的标准保持一致）
            if importance_score < 0.3:
                should_persist = False

            key_facts = (
                data.get("key_facts", [])
                if isinstance(data.get("key_facts"), list)
                else []
            )
            topics = (
                data.get("topics", [])
                if isinstance(data.get("topics"), list)
                else []
            )

            # 生成 ConversationMetrics
            metrics = self._build_metrics(state, importance_score)

            self.logger.info(
                "摘要与评估完成",
                importance_score=importance_score,
                should_persist=should_persist,
                summary_len=len(summary),
                topic_count=len(topics),
                key_facts=key_facts,
                topics=topics,
            )

            return {
                "final_answer": final_answer,
                "summary": summary,
                "importance_score": importance_score,
                "should_persist": should_persist,
                "metrics": metrics,
            }

        except AgentError:
            raise
        except Exception as e:
            self.logger.error("Scribe 执行异常", error=str(e), exc_info=True)
            errors: list[str] = list(state.get("errors", []))
            errors.append(f"ScribeAgent: {e}")
            # 降级：直接使用 draft_answer 作为最终答案，空摘要
            final_answer_fallback = state.get("final_answer", "") or state.get(
                "draft_answer", ""
            )
            return {
                "final_answer": final_answer_fallback,
                "summary": "",
                "importance_score": 0.0,
                "should_persist": False,
                "metrics": self._build_metrics(state, 0.0),
                "errors": errors,
            }

    def _build_metrics(
        self, state: GraphState, importance_score: float
    ) -> dict[str, Any]:
        """
        汇总生成 ConversationMetrics。

        从 state 中提取量化指标，结合 LLMFactory 的统计信息生成完整 metrics。

        Args:
            state: 当前 GraphState
            importance_score: 本次对话的重要性评分

        Returns:
            ConversationMetrics 字典
        """
        evaluation = state.get("evaluation", {}) or {}
        tool_calls = state.get("tool_calls", []) or []

        # 工具调用成功率
        if tool_calls:
            success_count = sum(1 for tc in tool_calls if tc.get("success"))
            tool_success_rate = success_count / len(tool_calls)
        else:
            tool_success_rate = 1.0

        # P0-1 修复：使用 per-request 快照计算 delta，避免全局污染
        snapshot = state.get("llm_stats_snapshot")
        if snapshot is not None:
            llm_delta = self.llm_factory.delta_stats(snapshot)
            total_input_tokens = llm_delta["total_input_tokens"]
            total_output_tokens = llm_delta["total_output_tokens"]
            total_cost_usd = llm_delta["total_cost_usd"]
            model_used = llm_delta["model_used"]
            llm_retried = llm_delta["llm_retried"]
            llm_retry_count = llm_delta["llm_retry_count"]
            llm_degraded = llm_delta["llm_degraded"]
            llm_degradation_count = llm_delta["llm_degradation_count"]
        else:
            # 降级：快照缺失时使用全局统计（兼容评估/测试场景）
            stats = self.llm_factory.stats
            total_input_tokens = stats.total_input_tokens
            total_output_tokens = stats.total_output_tokens
            total_cost_usd = round(stats.total_cost_usd, 6)
            model_used = list({r.model for r in stats.records})
            llm_retried = stats.retry_count > 0
            llm_retry_count = stats.retry_count
            llm_degraded = stats.degradation_count > 0
            llm_degradation_count = stats.degradation_count

        metrics: dict[str, Any] = {
            "intent_confidence": float(state.get("intent_confidence", 0.0)),
            "intent_type": str(state.get("intent", "")),
            "plan_step_count": len(state.get("task_steps", [])),
            "replan_count": int(state.get("replan_count", 0)),
            "tool_success_rate": tool_success_rate,
            "answer_groundedness": float(
                evaluation.get("groundedness_score", 0.0)
            ),
            "critic_coherence_score": float(
                evaluation.get("coherence_score", 0.0)
            ),
            "answer_relevance": float(
                evaluation.get("relevance_score", 0.0)
            ),
            "e2e_latency_ms": 0,  # 由外层 wrapper 回填，此处占位
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_cost_usd": total_cost_usd,
            "model_used": model_used,
            # 质量追踪：重试/降级信息（便于监控功能质量）
            "llm_retried": llm_retried,
            "llm_retry_count": llm_retry_count,
            "llm_degraded": llm_degraded,
            "llm_degradation_count": llm_degradation_count,
        }
        return metrics
