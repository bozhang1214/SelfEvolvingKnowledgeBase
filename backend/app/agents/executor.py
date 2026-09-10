"""
执行器 Agent 节点模块

ExecutorAgent 负责按计划执行任务步骤并生成草稿答案：
- 遍历 task_steps，按依赖顺序执行
- 根据 step.tool 调用对应工具（web_search / rag_retrieve / llm_generate）
- 记录每个工具调用的 ToolCallRecord
- 将所有工具结果拼接为 execution_context
- 调用 EXECUTOR_PROMPT 生成草稿答案

输出字段写入 GraphState：
    tool_calls, draft_answer, execution_context
"""

from __future__ import annotations

import time
from typing import Any

from app.agents.base import BaseAgent
from app.agents.prompts.templates import EXECUTOR_PROMPT
from app.core.exceptions import AgentError, ToolError
from app.graph.state import GraphState, TaskStatus, ToolCallRecord
from app.memory.short_term import ShortTermMemory
from app.tools.rag.format import format_rag_executor_reference
from app.tools.registry import ToolRegistry


class ExecutorAgent(BaseAgent):
    """
    执行器 Agent：工具调用与草稿生成。

    依赖注入 ToolRegistry 和 ShortTermMemory：
    - ToolRegistry 提供 web_search 等工具
    - ShortTermMemory 提供对话上下文（用于草稿生成时的历史参考）

    Phase 1 中 rag_retrieve 工具返回空（知识库未启用）。
    """

    def __init__(
        self,
        llm_factory: Any,
        config: Any,
        tool_registry: ToolRegistry,
        memory: ShortTermMemory,
    ) -> None:
        """
        初始化执行器 Agent。

        Args:
            llm_factory: LLM 工厂实例
            config: 应用全局配置
            tool_registry: 工具注册表
            memory: 短期记忆实例
        """
        super().__init__(llm_factory, config)
        self.tool_registry: ToolRegistry = tool_registry
        self.memory: ShortTermMemory = memory

    async def __call__(self, state: GraphState) -> dict[str, Any]:
        """
        执行任务步骤，生成草稿答案。

        Args:
            state: 当前 GraphState，需包含 task_steps

        Returns:
            state 更新字典，包含：
            - tool_calls: ToolCallRecord 列表
            - draft_answer: 草稿答案
            - execution_context: 执行上下文（工具结果拼接）
        """
        try:
            task_steps = state.get("task_steps", [])
            if not task_steps:
                raise AgentError(
                    "task_steps 为空，无法执行任务",
                    agent_name="ExecutorAgent",
                )

            user_input = state.get("user_input", "")
            user_id = state.get("user_id", "default")
            conv_id = state.get("conversation_id", "")

            tool_calls: list[ToolCallRecord] = []
            step_results: list[str] = []

            # 顺序执行所有步骤（Phase 1：按列表顺序，依赖仅作记录）
            for step in task_steps:
                step_id = step.get("step_id", 0)
                tool_name = step.get("tool", "llm_generate")
                tool_input = step.get("tool_input", {}) or {}

                # 标记为进行中
                step["status"] = TaskStatus.IN_PROGRESS.value

                start_time = time.time()
                record: ToolCallRecord = ToolCallRecord(
                    tool_name=tool_name,
                    input=tool_input,
                    output=None,
                    success=False,
                    latency_ms=0,
                    error=None,
                )

                try:
                    output = await self._dispatch_tool(
                        tool_name, tool_input, user_input,
                        pre_retrieval_results=state.get("pre_retrieval_results", []),
                    )
                    output_str = (
                        output if isinstance(output, str) else str(output)
                    )
                    record["output"] = output_str
                    record["success"] = True
                    step["status"] = TaskStatus.DONE.value
                    step["result"] = output_str
                    step_results.append(
                        f"[步骤 {step_id} - {tool_name}] 输出：\n{output_str}"
                    )
                except Exception as e:
                    record["error"] = str(e)
                    record["success"] = False
                    step["status"] = TaskStatus.FAILED.value
                    step["error"] = str(e)
                    step_results.append(
                        f"[步骤 {step_id} - {tool_name}] 执行失败：{e}"
                    )
                    self.logger.warning(
                        "工具执行失败",
                        step_id=step_id,
                        tool=tool_name,
                        error=str(e),
                    )

                record["latency_ms"] = int((time.time() - start_time) * 1000)
                tool_calls.append(record)

            # 拼接执行上下文
            execution_context = "\n\n".join(step_results)

            # 获取对话上下文
            conversation_context = ""
            if conv_id and self.memory is not None:
                try:
                    conversation_context = await self.memory.get_context(
                        user_id, conv_id, self.config.memory.l1_working.max_tokens
                    )
                except Exception as e:
                    self.logger.warning(
                        "获取对话上下文失败，使用空上下文",
                        error=str(e),
                    )

            # 格式化 RAG 上下文（Phase 2：从 pre_retrieval_results 注入）
            rag_context = format_rag_executor_reference(
                state.get("pre_retrieval_results", [])
            )

            # 生成草稿答案
            draft_answer = await self._generate_draft(
                user_input=user_input,
                task_steps=task_steps,
                tool_results=execution_context,
                conversation_context=conversation_context,
                rag_context=rag_context,
            )

            self.logger.info(
                "执行完成",
                step_count=len(task_steps),
                tool_call_count=len(tool_calls),
                success_count=sum(1 for r in tool_calls if r.get("success")),
            )

            return {
                "tool_calls": tool_calls,
                "draft_answer": draft_answer,
                "execution_context": execution_context,
                "task_steps": task_steps,
            }

        except AgentError:
            raise
        except Exception as e:
            self.logger.error("Executor 执行异常", error=str(e), exc_info=True)
            errors: list[str] = list(state.get("errors", []))
            errors.append(f"ExecutorAgent: {e}")
            # 降级：直接以 user_input 作为草稿
            return {
                "tool_calls": [],
                "draft_answer": state.get("user_input", ""),
                "execution_context": "",
                "errors": errors,
            }

    async def _dispatch_tool(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        user_input: str,
        pre_retrieval_results: list[dict[str, Any]] | None = None,
    ) -> str:
        """
        根据工具名分派到对应工具执行。

        Args:
            tool_name: 工具名（web_search / rag_retrieve / llm_generate）
            tool_input: 工具输入参数
            user_input: 用户原始输入（兜底用）
            pre_retrieval_results: RAG 预检索结果（由 graph 的 rag_retrieval 节点注入）

        Returns:
            工具输出字符串

        Raises:
            ToolError: 工具调用失败
        """
        if tool_name == "web_search":
            query = str(tool_input.get("query", user_input))
            try:
                tool = await self.tool_registry.get_web_search_tool()
                result = await tool.ainvoke({"query": query})
                if hasattr(result, "content"):
                    return str(result.content)
                return str(result)
            except Exception as e:
                raise ToolError(
                    f"web_search 调用失败: {e}",
                    tool_name="web_search",
                ) from e

        elif tool_name == "rag_retrieve":
            # Phase 2：RAG 结果已通过 rag_retrieval 节点注入到 pre_retrieval_results，
            # 并由 _format_rag_context 格式化进 EXECUTOR_PROMPT 的 {rag_context} 段。
            # 此处不再重复格式化，避免同一知识在 Prompt 中出现两遍。
            pre_results = pre_retrieval_results or []
            if pre_results:
                return (
                    f"知识库已检索到 {len(pre_results)} 条相关结果，"
                    f"请参考系统提示中的「知识库参考」段落。"
                )
            self.logger.info("rag_retrieve 无预检索结果（知识库未启用或无匹配）")
            return "（知识库中暂无相关信息）"

        elif tool_name == "llm_generate":
            query = str(tool_input.get("query", user_input))
            return await self._llm_generate(query)

        else:
            # 未知工具，兜底为 LLM 生成
            self.logger.warning(
                "未知工具，兜底为 llm_generate",
                tool_name=tool_name,
            )
            query = str(tool_input.get("query", user_input))
            return await self._llm_generate(query)

    async def _llm_generate(self, query: str) -> str:
        """
        直接用 LLM 生成内容。

        Args:
            query: 生成请求

        Returns:
            LLM 生成的文本
        """
        from langchain_core.messages import HumanMessage

        try:
            response = await self.llm_factory.ainvoke_with_stats(
                "executor",
                [HumanMessage(content=query)],
            )
            if hasattr(response, "content"):
                return str(response.content)
            return str(response)
        except Exception as e:
            raise ToolError(
                f"llm_generate 调用失败: {e}",
                tool_name="llm_generate",
            ) from e

    async def _generate_draft(
        self,
        user_input: str,
        task_steps: list[Any],
        tool_results: str,
        conversation_context: str,
        rag_context: str = "",
    ) -> str:
        """
        用 EXECUTOR_PROMPT 生成草稿答案。

        Args:
            user_input: 用户输入
            task_steps: 任务步骤列表
            tool_results: 工具调用结果拼接
            conversation_context: 对话历史上下文
            rag_context: RAG 检索的知识库参考上下文

        Returns:
            草稿答案字符串
        """
        # 把 task_steps 格式化为简洁文本
        plan_lines: list[str] = []
        for step in task_steps:
            plan_lines.append(
                f"- 步骤{step.get('step_id', '?')}: "
                f"{step.get('description', '')} "
                f"[tool={step.get('tool', '')}, status={step.get('status', '')}]"
            )
        task_plan = "\n".join(plan_lines) if plan_lines else "（无）"

        try:
            messages = EXECUTOR_PROMPT.format_messages(
                tool_results=tool_results or "（无工具调用结果）",
                conversation_context=conversation_context or "（无历史上下文）",
                rag_context=rag_context or "（无知识库参考）",
                user_input=user_input,
                task_plan=task_plan,
            )

            # R2-06 真流式：若上层（SSE）设了 token sink，则流式生成并逐 token 回传
            from app.core.token_sink import get_token_sink

            sink = get_token_sink()
            if sink is not None:
                parts: list[str] = []
                try:
                    async for token in self.llm_factory.astream_with_stats(
                        "executor", messages
                    ):
                        parts.append(token)
                        await sink(token)
                    return "".join(parts)
                except Exception as e:
                    self.logger.warning(
                        "流式草稿生成失败，回退工具结果", error=str(e)
                    )
                    return tool_results or user_input

            response = await self.llm_factory.ainvoke_with_stats(
                "executor", messages
            )
            if hasattr(response, "content"):
                return str(response.content)
            return str(response)
        except Exception as e:
            self.logger.warning(
                "草稿答案生成失败，使用工具结果作为兜底",
                error=str(e),
            )
            # 兜底：把工具结果直接作为草稿
            return tool_results or user_input

    async def rewrite_answer(
        self,
        user_input: str,
        draft_answer: str,
        feedback: str,
        rag_context: str = "",
    ) -> str:
        """
        根据 Critic 反馈重写答案（needs_rewrite 分支）。

        不重新执行工具，仅用「原答案 + 反馈」让 LLM 改进草稿。
        失败时返回原草稿（降级不阻断流程）。
        """
        from langchain_core.messages import HumanMessage, SystemMessage

        system_prompt = (
            "你是答案优化器。请根据评审反馈改进下面的答案，"
            "使其更准确、完整、相关。保持原有事实依据，不要新增未经证实的内容。"
        )
        human = (
            f"用户问题：{user_input}\n\n"
            f"原答案：{draft_answer}\n\n"
            f"评审反馈：{feedback or '（无）'}\n\n"
            f"知识库参考：{rag_context or '（无）'}\n\n"
            "请输出改进后的完整答案："
        )
        try:
            response = await self.llm_factory.ainvoke_with_stats(
                "executor",
                [SystemMessage(content=system_prompt), HumanMessage(content=human)],
            )
            if hasattr(response, "content"):
                return str(response.content)
            return str(response)
        except Exception as e:
            self.logger.warning("答案重写失败，返回原草稿", error=str(e))
            return draft_answer
