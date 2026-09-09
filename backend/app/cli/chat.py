"""
交互式聊天模块

实现 ChatSession 类，提供基于 rich 美化的交互式 REPL 聊天体验：
- 使用 rich Markdown 渲染助手回复
- 支持多轮对话，自动加载历史与压缩记忆
- 自动持久化消息到 JSON 存储
- 显示元信息（意图、模型、token、延迟、工具调用）
- 支持斜杠命令：/exit /quit /new /history /help /stats

聊天流程：
    1. 用户输入
    2. 创建 GraphState（从短期记忆加载历史）
    3. 运行 LangGraph 工作流
    4. 显示回复（Markdown 渲染）
    5. 保存消息到 JSON 存储
    6. 更新 L1 短期记忆（必要时触发压缩）

使用方式：
    from app.cli.chat import ChatSession

    session = ChatSession(config, llm_factory, tool_registry, memory,
                          storage, reflection_strategy)
    await session.initialize()
    await session.run_interactive()
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from app.agents.strategies.base import ReflectionStrategy
from app.core.config import AppConfig
from app.core.exceptions import SEKBError
from app.core.llm_factory import LLMFactory
from app.core.logging import bind_context, clear_context, get_logger
from app.core.utils import to_state_dict
from app.graph.state import create_initial_state
from app.memory.short_term import ShortTermMemory
from app.storage.json_storage import JSONStorage
from app.tools.registry import ToolRegistry

logger = get_logger(__name__)
console = Console()


class ChatSession:
    """
    交互式聊天会话。

    封装单次 CLI 聊天会话的全部状态与行为，包括：
    - LangGraph 工作流调用
    - 对话历史与短期记忆管理
    - 消息持久化
    - 元信息展示
    - 斜杠命令处理
    """

    # Phase 1 用户 ID 固定为 "default"
    USER_ID: str = "default"

    def __init__(
        self,
        config: AppConfig,
        llm_factory: LLMFactory,
        tool_registry: ToolRegistry,
        memory: ShortTermMemory,
        storage: JSONStorage,
        reflection_strategy: ReflectionStrategy,
        knowledge_ingester: Any = None,
        knowledge_base: Any = None,
    ) -> None:
        """
        初始化聊天会话。

        Args:
            config: 应用全局配置
            llm_factory: LLM 工厂实例
            tool_registry: 工具注册表实例（已初始化）
            memory: L1 短期记忆实例
            storage: JSON 存储后端实例
            reflection_strategy: 反思策略实例
            knowledge_ingester: 知识自迭代引擎实例（Phase 2，可选）
            knowledge_base: L3 知识库实例（Phase 2，可选）
        """
        self.config: AppConfig = config
        self.llm_factory: LLMFactory = llm_factory
        self.tool_registry: ToolRegistry = tool_registry
        self.memory: ShortTermMemory = memory
        self.storage: JSONStorage = storage
        self.reflection_strategy: ReflectionStrategy = reflection_strategy
        # Phase 2: 知识自迭代相关组件，默认 None（未启用时跳过入库）
        self.knowledge_ingester: Any = knowledge_ingester
        self.knowledge_base: Any = knowledge_base

        # 编译后的 LangGraph 工作流（在 initialize 中构建）
        self._graph: Any = None
        # 当前会话 ID
        self._conv_id: str | None = None
        # 当前会话已完成的轮次数
        self._turn_count: int = 0
        # 会话开始时间（用于统计）
        self._start_time: float = time.time()
        # 上一轮的元信息（用于 /stats 与显示）
        self._last_meta: dict[str, Any] = {}

    # ============================================================
    # 公共 API
    # ============================================================

    async def initialize(self) -> None:
        """
        初始化工具与 Graph 工作流。

        在 run_interactive 之前调用一次。若工具注册表尚未初始化，
        会自动触发初始化。然后构建 LangGraph 工作流并缓存。
        """
        # 确保工具已初始化（bootstrap 中已调用，但防御性检查）
        if not self.tool_registry._initialized:  # type: ignore[attr-defined]
            await self.tool_registry.initialize()

        # 构建 LangGraph 工作流
        from app.graph.builder import GraphBuilder

        builder = GraphBuilder(
            config=self.config,
            llm_factory=self.llm_factory,
            tool_registry=self.tool_registry,
            memory=self.memory,
            reflection_strategy=self.reflection_strategy,
        )
        self._graph = builder.build()
        logger.info("ChatSession 初始化完成")

    async def send(self, user_input: str) -> str:
        """
        发送一条消息并获取助手回复。

        流程：
            1. 确保当前会话存在（首次发送时自动创建）
            2. 从短期记忆加载对话历史
            3. 绑定 trace 上下文并创建初始 GraphState
            4. 运行 LangGraph 工作流
            5. 提取 final_answer 与 metrics
            6. 持久化用户与助手消息到 JSON 存储
            7. 更新 L1 短期记忆并按需触发压缩

        Args:
            user_input: 用户输入文本

        Returns:
            助手的最终回复文本
        """
        if self._graph is None:
            raise SEKBError("ChatSession 未初始化，请先调用 initialize()")

        # 1. 确保会话存在
        if not self._conv_id:
            title = user_input[:30] if user_input else "新会话"
            self._conv_id = await self.storage.create_conversation(
                self.USER_ID, title
            )
            logger.info("已创建新会话", conv_id=self._conv_id, title=title)

        # 2. 加载对话历史
        history = await self.memory.get_messages(self.USER_ID, self._conv_id)

        # 3. 创建初始 GraphState
        trace_id = str(uuid.uuid4())
        bind_context(
            conversation_id=self._conv_id,
            trace_id=trace_id,
            user_id=self.USER_ID,
        )
        state = create_initial_state(
            user_input=user_input,
            conversation_id=self._conv_id,
            trace_id=trace_id,
            user_id=self.USER_ID,
            history=history,
        )

        # P0-1 修复：在 ainvoke 前注入 LLM 统计快照，供 Scribe 计算 per-request delta
        state["llm_stats_snapshot"] = self.llm_factory.snapshot_stats()

        # 4. 运行 LangGraph 工作流（P0-2 修复：全局异常兜底）
        start_time = time.time()
        try:
            try:
                final_state = await self._graph.ainvoke(state)
            except Exception as e:
                # 工作流整体异常兜底：返回降级回复而非中断
                logger.error(
                    "LangGraph 工作流异常，触发降级",
                    error=str(e),
                    error_type=type(e).__name__,
                    exc_info=True,
                )
                final_state = {
                    "final_answer": (
                        "抱歉，处理您的请求时遇到了内部错误。"
                        "请稍后重试，或换一种方式提问。"
                    ),
                    "draft_answer": "",
                    "errors": [f"workflow_fallback: {e}"],
                    "metrics": {},
                    "intent": "",
                    "intent_confidence": 0.0,
                    "replan_count": 0,
                    "tool_calls": [],
                }
        finally:
            clear_context()

        latency_ms = int((time.time() - start_time) * 1000)

        # 5. 提取回复与指标
        final_state_dict = to_state_dict(final_state)
        answer = (
            final_state_dict.get("final_answer")
            or final_state_dict.get("draft_answer")
            or "（无回复）"
        )
        metrics = final_state_dict.get("metrics") or {}
        # P0-6 修复：回填 e2e_latency_ms（Scribe 占位 0，由外层回填真实值）
        metrics["e2e_latency_ms"] = latency_ms

        # 6. 持久化消息
        user_msg = {
            "role": "user",
            "content": user_input,
            "trace_id": trace_id,
        }
        await self.storage.append_message(self._conv_id, user_msg)

        assistant_msg = {
            "role": "assistant",
            "content": answer,
            "intent": final_state_dict.get("intent"),
            "tokens_input": metrics.get("total_input_tokens", 0),
            "tokens_output": metrics.get("total_output_tokens", 0),
            "latency_ms": latency_ms,
            "trace_id": trace_id,
            # NEW-F 修复：持久化重试/降级质量指标，提升可追溯性
            "llm_retried": metrics.get("llm_retried", False),
            "llm_retry_count": metrics.get("llm_retry_count", 0),
            "llm_degraded": metrics.get("llm_degraded", False),
            "llm_degradation_count": metrics.get("llm_degradation_count", 0),
        }
        await self.storage.append_message(self._conv_id, assistant_msg)

        # 7. 更新 L1 短期记忆
        await self.memory.add_message(
            self.USER_ID, self._conv_id, HumanMessage(content=user_input)
        )
        await self.memory.add_message(
            self.USER_ID, self._conv_id, AIMessage(content=answer)
        )
        try:
            await self.memory.compress_if_needed(self.USER_ID, self._conv_id)
        except Exception as e:
            logger.warning("短期记忆压缩失败", error=str(e))

        # Phase 2: 知识自迭代入库（不阻塞主回复，失败不影响用户收到答案）
        ingest_status = "disabled"
        if self.knowledge_ingester is not None and self.knowledge_base is not None:
            try:
                ingest_result = await self.knowledge_ingester.ingest_conversation(
                    user_input=user_input,
                    final_answer=answer,
                    summary=final_state_dict.get("summary", ""),
                    importance_score=final_state_dict.get("importance_score", 0.0),
                    conv_id=self._conv_id,
                    user_id=self.USER_ID,
                    knowledge_base=self.knowledge_base,
                )
                ingest_status = ingest_result.status
                logger.info(
                    "知识自迭代入库完成",
                    ingest_status=ingest_status,
                    entry_id=ingest_result.entry_id,
                    conv_id=self._conv_id,
                )
            except Exception as e:
                ingest_status = "error"
                logger.warning(
                    "知识自迭代入库失败", error=str(e), conv_id=self._conv_id
                )

        # 缓存元信息（包含重试/降级质量指标）
        self._last_meta = {
            "intent": final_state_dict.get("intent", ""),
            "intent_confidence": final_state_dict.get("intent_confidence", 0.0),
            "model_used": metrics.get("model_used", []),
            "tokens_input": metrics.get("total_input_tokens", 0),
            "tokens_output": metrics.get("total_output_tokens", 0),
            "total_cost_usd": metrics.get("total_cost_usd", 0.0),
            "latency_ms": latency_ms,
            "replan_count": final_state_dict.get("replan_count", 0),
            "tool_calls": final_state_dict.get("tool_calls", []),
            "errors": final_state_dict.get("errors", []),
            "llm_retried": metrics.get("llm_retried", False),
            "llm_retry_count": metrics.get("llm_retry_count", 0),
            "llm_degraded": metrics.get("llm_degraded", False),
            "llm_degradation_count": metrics.get("llm_degradation_count", 0),
            "ingest_status": ingest_status,
        }
        self._turn_count += 1
        return answer

    async def run_interactive(self, initial_conv_id: str | None = None) -> None:
        """
        运行交互式 REPL 主循环。

        持续读取用户输入，处理斜杠命令或发送消息给助手，
        直到用户输入 /exit /quit 或触发 EOF/Ctrl+C。

        Args:
            initial_conv_id: 初始会话 ID；若为 None 则在首次发送时自动创建
        """
        self._conv_id = initial_conv_id
        # 若指定了会话 ID，尝试从存储恢复历史到短期记忆
        if self._conv_id:
            await self._restore_history_to_memory(self._conv_id)

        self._print_banner()

        while True:
            try:
                user_input = Prompt.ask("[bold cyan]你[/bold cyan]")
            except (EOFError, KeyboardInterrupt):
                console.print("\n[yellow]再见！[/yellow]")
                break

            if not user_input.strip():
                continue

            # 斜杠命令
            stripped = user_input.strip()
            cmd = stripped.lower()
            if cmd in ("/exit", "/quit"):
                console.print("[yellow]再见！[/yellow]")
                break
            if cmd == "/new":
                await self._handle_new()
                continue
            if cmd == "/history":
                await self._handle_history()
                continue
            if cmd == "/help":
                self._handle_help()
                continue
            if cmd == "/stats":
                self._handle_stats()
                continue

            # 普通消息
            await self._handle_message(stripped)

    # ============================================================
    # 命令处理
    # ============================================================

    async def _handle_new(self) -> None:
        """处理 /new 命令：新建会话。"""
        if self._conv_id:
            try:
                await self.memory.clear(self.USER_ID, self._conv_id)
            except Exception as e:
                logger.warning("清空旧会话短期记忆失败", error=str(e))
        self._conv_id = None
        self._turn_count = 0
        self._last_meta = {}
        console.print("[green]已新建会话[/green]")

    async def _handle_history(self) -> None:
        """处理 /history 命令：显示当前会话历史消息。"""
        if not self._conv_id:
            console.print("[yellow]当前无活跃会话[/yellow]")
            return
        try:
            messages = await self.storage.get_messages(self._conv_id)
        except Exception as e:
            console.print(f"[red]读取历史失败: {e}[/red]")
            return

        if not messages:
            console.print("[yellow]暂无历史消息[/yellow]")
            return

        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if role == "user":
                console.print(
                    Panel(content, title="你", border_style="cyan", expand=False)
                )
            elif role == "assistant":
                console.print(
                    Panel(
                        Markdown(content),
                        title="助手",
                        border_style="green",
                        expand=False,
                    )
                )
            else:
                console.print(f"[dim]{role}: {content}[/dim]")

    def _handle_help(self) -> None:
        """处理 /help 命令：显示可用命令列表。"""
        help_text = (
            "[bold]可用命令：[/bold]\n\n"
            "  [cyan]/exit[/cyan] 或 [cyan]/quit[/cyan]  - 退出聊天\n"
            "  [cyan]/new[/cyan]                      - 新建会话\n"
            "  [cyan]/history[/cyan]                  - 显示当前会话历史\n"
            "  [cyan]/help[/cyan]                     - 显示本帮助\n"
            "  [cyan]/stats[/cyan]                    - 显示统计信息\n\n"
            "[dim]直接输入文本即可与助手对话。[/dim]"
        )
        console.print(Panel(help_text, title="帮助", border_style="blue"))

    def _handle_stats(self) -> None:
        """处理 /stats 命令：显示会话与 LLM 调用统计。"""
        stats = self.llm_factory.stats
        elapsed = time.time() - self._start_time

        table = Table(title="会话统计", show_lines=False)
        table.add_column("指标", style="cyan", no_wrap=True)
        table.add_column("值", style="green")

        table.add_row("当前会话 ID", self._conv_id or "(未创建)")
        table.add_row("当前会话轮次", str(self._turn_count))
        table.add_row("运行时长(秒)", f"{elapsed:.1f}")
        table.add_row("LLM 总调用次数", str(stats.total_calls))
        table.add_row("成功 / 失败", f"{stats.success_count} / {stats.failure_count}")
        table.add_row("总输入 token", str(stats.total_input_tokens))
        table.add_row("总输出 token", str(stats.total_output_tokens))
        table.add_row("总成本(USD)", f"{stats.total_cost_usd:.6f}")
        table.add_row("平均延迟(ms)", f"{stats.avg_latency_ms:.1f}")
        table.add_row("reasoner 占比", f"{stats.reasoner_ratio:.1%}")
        console.print(table)

    async def _handle_message(self, user_input: str) -> None:
        """处理普通聊天消息：发送、渲染回复、显示元信息。"""
        try:
            with console.status("[bold green]助手思考中...[/bold green]"):
                answer = await self.send(user_input)
        except SEKBError as e:
            console.print(f"[red]处理失败: {e.message}[/red]")
            logger.warning("聊天处理失败", error=str(e), exc_info=True)
            return
        except Exception as e:
            console.print(f"[red]意外错误: {e}[/red]")
            logger.error("聊天意外异常", error=str(e), exc_info=True)
            return

        console.print(
            Panel(
                Markdown(answer),
                title="助手",
                border_style="green",
                expand=True,
            )
        )
        self._print_meta()

    # ============================================================
    # 内部辅助方法
    # ============================================================

    def _print_banner(self) -> None:
        """打印欢迎横幅与帮助提示。"""
        banner = (
            f"[bold green]欢迎使用 {self.config.app.name}[/bold green]\n\n"
            f"版本: [cyan]{self.config.app.version}[/cyan]  "
            f"环境: [cyan]{self.config.app.environment}[/cyan]\n\n"
            "输入 [cyan]/help[/cyan] 查看命令列表，"
            "输入 [cyan]/exit[/cyan] 退出。"
        )
        console.print(Panel(banner, title="自迭代个人知识库 Agent", border_style="blue"))

    def _print_meta(self) -> None:
        """打印上一轮对话的元信息表格。"""
        meta = self._last_meta
        if not meta:
            return

        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column("key", style="dim", no_wrap=True)
        table.add_column("value", style="cyan")

        intent = meta.get("intent", "")
        if intent:
            confidence = meta.get("intent_confidence", 0.0)
            table.add_row("意图", f"{intent} (置信度 {confidence:.2f})")

        models = meta.get("model_used", [])
        if models:
            table.add_row("模型", ", ".join(models))

        tokens_in = meta.get("tokens_input", 0)
        tokens_out = meta.get("tokens_output", 0)
        if tokens_in or tokens_out:
            table.add_row("Token", f"输入 {tokens_in} / 输出 {tokens_out}")

        cost = meta.get("total_cost_usd", 0.0)
        if cost > 0:
            table.add_row("成本", f"${cost:.6f}")

        table.add_row("延迟", f"{meta.get('latency_ms', 0)} ms")

        replan = meta.get("replan_count", 0)
        if replan:
            table.add_row("重规划", str(replan))

        tool_calls = meta.get("tool_calls", [])
        if tool_calls:
            tool_names = [
                tc.get("tool_name", "?") for tc in tool_calls if isinstance(tc, dict)
            ]
            table.add_row("工具调用", ", ".join(tool_names))

        errors = meta.get("errors", [])
        if errors:
            table.add_row("错误", "[red]" + "; ".join(errors) + "[/red]")

        # 质量追踪：展示重试/降级信息
        if meta.get("llm_retried"):
            table.add_row("LLM 重试", f"[yellow]{meta.get('llm_retry_count', 0)} 次[/yellow]")
        if meta.get("llm_degraded"):
            table.add_row("模型降级", f"[yellow]{meta.get('llm_degradation_count', 0)} 次[/yellow]")

        console.print(table)
        console.print()

    async def _restore_history_to_memory(self, conv_id: str) -> None:
        """从存储加载历史消息到短期记忆，使跨进程的会话可继续。"""
        try:
            messages = await self.storage.get_messages(conv_id)
        except Exception as e:
            logger.warning("恢复会话历史失败", error=str(e), conv_id=conv_id)
            return

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content", "")
            if not content:
                continue
            if role == "user":
                await self.memory.add_message(
                    self.USER_ID, conv_id, HumanMessage(content=content)
                )
            elif role == "assistant":
                await self.memory.add_message(
                    self.USER_ID, conv_id, AIMessage(content=content)
                )
        # NEW-D：恢复完成后触发一次压缩，避免超大历史在内存中越积越大
        await self.memory.compress_if_needed(self.USER_ID, conv_id)
        logger.info("会话历史已恢复", conv_id=conv_id, count=len(messages))
