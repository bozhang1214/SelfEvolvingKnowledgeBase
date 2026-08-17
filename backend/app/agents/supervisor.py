"""
监督者 Agent 节点模块

SupervisorAgent 作为 LangGraph 的入口节点，负责：
- 识别用户意图（chitchat / kb_strict / kb_prefer / web_default / task_plan / clarify）
- 输出意图置信度
- 判断是否需要向用户澄清
- Phase 1 不执行真实预检索，预检索结果返回空列表

输出字段写入 GraphState：
    intent, intent_confidence, needs_clarification,
    clarification_question, pre_retrieval_results
"""

from __future__ import annotations

from typing import Any

from app.agents.base import BaseAgent
from app.agents.prompts.templates import SUPERVISOR_PROMPT
from app.core.exceptions import AgentError
from app.graph.state import GraphState


class SupervisorAgent(BaseAgent):
    """
    监督者 Agent：意图识别与澄清判断。

    使用 SUPERVISOR_PROMPT 与 "supervisor" 角色的 LLM。
    根据 config.intent_routing 配置填充 Prompt 变量；
    Phase 1 预检索结果固定为空列表（知识库未启用）。
    """

    async def __call__(self, state: GraphState) -> dict[str, Any]:
        """
        执行监督者逻辑，识别用户意图。

        Args:
            state: 当前 GraphState，需包含 user_input

        Returns:
            state 更新字典，包含：
            - intent: 识别到的意图
            - intent_confidence: 置信度
            - needs_clarification: 是否需要澄清
            - clarification_question: 澄清问题
            - pre_retrieval_results: 预检索结果（Phase 1 为空列表）
        """
        try:
            user_input = state.get("user_input", "")
            if not user_input:
                raise AgentError(
                    "user_input 为空，无法进行意图识别",
                    agent_name="SupervisorAgent",
                )

            intent_cfg = self.config.intent_routing

            # Phase 1：预检索结果固定为空列表
            pre_retrieval_results: list[dict[str, Any]] = []

            # 渲染 Prompt
            messages = SUPERVISOR_PROMPT.format_messages(
                kb_strict_keywords=", ".join(intent_cfg.kb_strict_keywords) or "（无）",
                realtime_keywords=", ".join(intent_cfg.realtime_keywords) or "（无）",
                clarify_threshold=intent_cfg.clarify_threshold,
                default_intent=intent_cfg.default_intent,
                pre_retrieval_top_k=intent_cfg.pre_retrieval_top_k,
                pre_retrieval_results="（Phase 1 未启用知识库，预检索结果为空）",
                kb_prefer_hit_threshold=intent_cfg.kb_prefer_hit_threshold,
                user_input=user_input,
            )

            # 调用 LLM（带统计）
            response = await self.llm_factory.ainvoke_with_stats(
                "supervisor", messages
            )

            # 解析 JSON
            data = await self._parse_json_response(response)

            intent = str(data.get("intent", intent_cfg.default_intent)).strip()
            # NEW-A 修复：LLM 可能返回非数值（如 "高"/null/dict），直接 float() 会抛
            # ValueError/TypeError 被外层 except 捕获降级；改用安全转换与 Critic/Scribe
            # 的 _safe_float 模式保持一致，提升鲁棒性。
            try:
                confidence = float(data.get("confidence", 0.0))
            except (TypeError, ValueError):
                confidence = 0.0
            # 归一化到 [0.0, 1.0]
            confidence = max(0.0, min(1.0, confidence))

            needs_clarification = bool(data.get("needs_clarification", False))

            # 如果置信度低于阈值，强制设置需要澄清
            if confidence < intent_cfg.clarify_threshold:
                needs_clarification = True

            clarification_question = ""
            if needs_clarification:
                clarification_question = str(
                    data.get("clarification_question", "")
                ).strip()
                if not clarification_question:
                    clarification_question = (
                        "请问您希望我如何处理这个问题？"
                        "（例如：基于知识库回答 / 联网搜索 / 直接回答）"
                    )
                # 需要澄清时，意图改写为 clarify
                intent = "clarify"

            self.logger.info(
                "意图识别完成",
                intent=intent,
                confidence=confidence,
                needs_clarification=needs_clarification,
            )

            return {
                "intent": intent,
                "intent_confidence": confidence,
                "needs_clarification": needs_clarification,
                "clarification_question": clarification_question,
                "pre_retrieval_results": pre_retrieval_results,
            }

        except AgentError:
            raise
        except Exception as e:
            self.logger.error("Supervisor 执行异常", error=str(e), exc_info=True)
            # 降级：返回默认意图，标记错误
            errors: list[str] = list(state.get("errors", []))
            errors.append(f"SupervisorAgent: {e}")
            return {
                "intent": self.config.intent_routing.default_intent,
                "intent_confidence": 0.0,
                "needs_clarification": False,
                "clarification_question": "",
                "pre_retrieval_results": [],
                "errors": errors,
            }
