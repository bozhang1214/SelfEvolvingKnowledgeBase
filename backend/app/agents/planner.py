"""
规划者 Agent 节点模块

PlannerAgent 负责将用户问题拆解为有序的执行步骤：
- 解析 Supervisor 输出的意图
- 调用 LLM 生成执行计划（steps + complexity + reasoning）
- 将 steps 转换为 TaskStep 列表，初始化 status 为 pending
- 输出复杂度评估，供后续 Critic 决定使用哪种模型

输出字段写入 GraphState：
    task_steps, task_complexity, plan_reasoning
"""

from __future__ import annotations

from typing import Any

from app.agents.base import BaseAgent
from app.agents.prompts.templates import PLANNER_PROMPT
from app.core.exceptions import AgentError
from app.graph.state import GraphState, TaskStatus, TaskStep


class PlannerAgent(BaseAgent):
    """
    规划者 Agent：任务拆解。

    使用 PLANNER_PROMPT 与 "planner" 角色的 LLM。
    从 state 读取 intent 和 user_input，生成执行计划。
    所有步骤初始状态为 pending。
    """

    async def __call__(self, state: GraphState) -> dict[str, Any]:
        """
        执行规划逻辑，生成任务步骤列表。

        Args:
            state: 当前 GraphState，需包含 user_input 与 intent

        Returns:
            state 更新字典，包含：
            - task_steps: TaskStep 列表
            - task_complexity: 任务复杂度（0.0~1.0）
            - plan_reasoning: 规划推理过程
        """
        try:
            user_input = state.get("user_input", "")
            if not user_input:
                raise AgentError(
                    "user_input 为空，无法进行任务规划",
                    agent_name="PlannerAgent",
                )

            intent = state.get("intent", "") or self.config.intent_routing.default_intent
            model_switch_threshold = self.config.reflection.model_switch_threshold

            # 渲染 Prompt
            messages = PLANNER_PROMPT.format_messages(
                intent=intent,
                user_input=user_input,
                model_switch_threshold=model_switch_threshold,
            )

            # 调用 LLM（带统计）
            response = await self.llm_factory.ainvoke_with_stats(
                "planner", messages
            )

            # 解析 JSON
            data = await self._parse_json_response(response)

            raw_steps = data.get("steps", [])
            if not isinstance(raw_steps, list):
                raw_steps = []

            # 转换为 TaskStep 列表
            task_steps: list[TaskStep] = []
            for idx, step in enumerate(raw_steps, start=1):
                if not isinstance(step, dict):
                    continue
                task_step: TaskStep = TaskStep(
                    step_id=int(step.get("step_id", idx)),
                    description=str(step.get("description", "")),
                    tool=str(step.get("tool", "llm_generate")),
                    tool_input=dict(step.get("tool_input", {}))
                    if isinstance(step.get("tool_input"), dict)
                    else {},
                    depends_on=list(step.get("depends_on", []))
                    if isinstance(step.get("depends_on"), list)
                    else [],
                    status=TaskStatus.PENDING.value,
                    result=None,
                    error=None,
                    latency_ms=0,
                )
                task_steps.append(task_step)

            # NEW-A 修复：LLM 可能返回非数值（如 "高"/null），直接 float() 会抛异常
            # 被外层 except 捕获降级；改用安全转换，与 Critic/Scribe 模式保持一致。
            try:
                complexity = float(data.get("complexity", 0.0))
            except (TypeError, ValueError):
                complexity = 0.0
            # 归一化到 [0.0, 1.0]
            complexity = max(0.0, min(1.0, complexity))

            reasoning = str(data.get("reasoning", ""))

            # 如果 LLM 未生成任何步骤，兜底为单步 llm_generate
            if not task_steps:
                task_steps.append(
                    TaskStep(
                        step_id=1,
                        description="直接基于用户输入生成回答",
                        tool="llm_generate",
                        tool_input={"query": user_input},
                        depends_on=[],
                        status=TaskStatus.PENDING.value,
                        result=None,
                        error=None,
                        latency_ms=0,
                    )
                )
                self.logger.warning(
                    "Planner 未生成有效步骤，使用兜底单步计划",
                    intent=intent,
                )

            self.logger.info(
                "任务规划完成",
                intent=intent,
                step_count=len(task_steps),
                complexity=complexity,
            )

            return {
                "task_steps": task_steps,
                "task_complexity": complexity,
                "plan_reasoning": reasoning,
            }

        except AgentError:
            raise
        except Exception as e:
            self.logger.error("Planner 执行异常", error=str(e), exc_info=True)
            errors: list[str] = list(state.get("errors", []))
            errors.append(f"PlannerAgent: {e}")
            # 降级：单步 llm_generate
            fallback_step: TaskStep = TaskStep(
                step_id=1,
                description="直接基于用户输入生成回答（降级计划）",
                tool="llm_generate",
                tool_input={"query": state.get("user_input", "")},
                depends_on=[],
                status=TaskStatus.PENDING.value,
                result=None,
                error=None,
                latency_ms=0,
            )
            return {
                "task_steps": [fallback_step],
                "task_complexity": 0.0,
                "plan_reasoning": f"降级计划（异常: {e}）",
                "errors": errors,
            }
