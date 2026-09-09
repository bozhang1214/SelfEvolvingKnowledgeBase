"""
审查者 Agent 节点模块

CriticAgent 负责对 Executor 生成的草稿答案进行多维度评估：
- 答案锚定度（groundedness_score，0.0~1.0）
- 逻辑自洽性（coherence_score，0.0~10.0）
- 回答相关性（relevance_score，0.0~1.0）
- 根据 task_complexity 决定使用 chat 或 reasoner 模型
- 判断是否需要重规划（result=needs_replan 且未超 max_replan）

输出字段写入 GraphState：
    evaluation, should_replan, replan_count
"""

from __future__ import annotations

from typing import Any

from app.agents.base import BaseAgent
from app.agents.prompts.templates import CRITIC_PROMPT
from app.core.exceptions import AgentError
from app.core.utils import safe_float
from app.graph.state import GraphState


class CriticAgent(BaseAgent):
    """
    审查者 Agent：反思与评估。

    使用 CRITIC_PROMPT 进行多维度质量评估。
    根据 task_complexity 与 config.reflection.model_switch_threshold
    决定使用 "critic"（chat）还是 "critic_complex"（reasoner）模型。
    """

    async def __call__(self, state: GraphState) -> dict[str, Any]:
        """
        执行审查逻辑，评估草稿答案质量。

        Args:
            state: 当前 GraphState，需包含 user_input, draft_answer,
                   task_complexity, replan_count

        Returns:
            state 更新字典，包含：
            - evaluation: CriticEvaluation 评估结果
            - should_replan: 是否需要重规划
            - replan_count: 更新后的重规划次数
        """
        try:
            user_input = state.get("user_input", "")
            draft_answer = state.get("draft_answer", "")
            if not draft_answer:
                raise AgentError(
                    "draft_answer 为空，无法进行评估",
                    agent_name="CriticAgent",
                )

            task_complexity = float(state.get("task_complexity", 0.0))
            replan_count = int(state.get("replan_count", 0))

            # 根据复杂度选择模型（P2-13 语义：task_complexity >= 阈值 → reasoner 强模型）
            model_switch_threshold = self.config.reflection.model_switch_threshold
            if task_complexity >= model_switch_threshold:
                critic_role = "critic_complex"
                model_used = "reasoner"
            else:
                critic_role = "critic"
                model_used = "chat"

            # 拼接工具调用结果用于评估
            tool_calls = state.get("tool_calls", [])
            tool_results_parts: list[str] = []
            for tc in tool_calls:
                tool_results_parts.append(
                    f"- 工具: {tc.get('tool_name', '?')}, "
                    f"成功: {tc.get('success', False)}, "
                    f"输出: {tc.get('output', '')}"
                )
            tool_results = "\n".join(tool_results_parts) or "（无工具调用）"

            # 渲染 Prompt
            messages = CRITIC_PROMPT.format_messages(
                user_input=user_input,
                draft_answer=draft_answer,
                tool_results=tool_results,
            )

            # 调用 LLM（带统计）
            response = await self.llm_factory.ainvoke_with_stats(
                critic_role, messages
            )

            # 解析 JSON
            data = await self._parse_json_response(response)

            # 提取评估字段
            passed = bool(data.get("passed", False))
            result_str = str(data.get("result", "")).strip()
            groundedness_score = safe_float(
                data.get("groundedness_score"), 0.0, 1.0
            )
            coherence_score = safe_float(
                data.get("coherence_score"), 0.0, 10.0
            )
            relevance_score = safe_float(
                data.get("relevance_score"), 0.0, 1.0
            )
            issues = (
                data.get("issues", [])
                if isinstance(data.get("issues"), list)
                else []
            )
            suggestions = (
                data.get("suggestions", [])
                if isinstance(data.get("suggestions"), list)
                else []
            )
            reasoning = str(data.get("reasoning", ""))

            # 判断是否需要重规划
            max_replan = self.config.reflection.max_replan
            should_replan = (
                result_str == "needs_replan" and replan_count < max_replan
            )

            new_replan_count = replan_count + (1 if should_replan else 0)

            evaluation = {
                "passed": passed,
                "result": result_str,
                "groundedness_score": groundedness_score,
                "coherence_score": coherence_score,
                "relevance_score": relevance_score,
                "issues": [str(i) for i in issues],
                "suggestions": [str(s) for s in suggestions],
                "reasoning": reasoning,
                "model_used": model_used,
            }

            self.logger.info(
                "审查评估完成",
                model_used=model_used,
                passed=passed,
                result=result_str,
                groundedness=groundedness_score,
                coherence=coherence_score,
                relevance=relevance_score,
                should_replan=should_replan,
                replan_count=new_replan_count,
            )

            return {
                "evaluation": evaluation,
                "should_replan": should_replan,
                "replan_count": new_replan_count,
            }

        except AgentError:
            raise
        except Exception as e:
            self.logger.error("Critic 执行异常", error=str(e), exc_info=True)
            errors: list[str] = list(state.get("errors", []))
            errors.append(f"CriticAgent: {e}")
            # 降级：默认通过，避免阻塞流程
            return {
                "evaluation": {
                    "passed": True,
                    "result": "pass",
                    "groundedness_score": 0.0,
                    "coherence_score": 0.0,
                    "relevance_score": 0.0,
                    "issues": [f"评估异常: {e}"],
                    "suggestions": [],
                    "reasoning": "评估异常，降级为通过",
                    "model_used": "unknown",
                },
                "should_replan": False,
                "replan_count": int(state.get("replan_count", 0)),
                "errors": errors,
            }
