"""
聊天路由模块

提供基于 LangGraph 工作流的聊天 HTTP 端点，支持非流式与 SSE 流式两种模式：

- ``POST /api/v1/chat/``        发送消息（非流式），返回完整回复
- ``POST /api/v1/chat/stream``  发送消息（SSE 流式响应），逐 token 返回

聊天流程：
    1. 若 ``conversation_id`` 为空，则创建新会话（标题取用户输入前 30 字符）
    2. 从 L1 短期记忆加载对话历史（内存缓存，缺失时回退到存储）
    3. 绑定 trace 上下文并创建初始 GraphState
    4. 运行 LangGraph 工作流，提取 final_answer 与 metrics
    5. 将用户消息与助手消息持久化到 JSON 存储
    6. 返回响应（非流式：JSON；流式：逐 token SSE + done 事件）
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, AsyncIterator, Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.api.server import get_app_context
from app.core.access import require_full_access
from app.core.auth import get_current_user
from app.core.bootstrap import AppContext
from app.core.exceptions import SecurityError, SEKBError
from app.core.logging import bind_context, clear_context, get_logger
from app.core.metrics import record_chat_error, record_chat_metrics
from app.core.plane_router import collect_route_events, summarize_route_events
from app.core.token_sink import reset_token_sink, set_token_sink
from app.core.utils import to_state_dict
from app.graph.state import create_initial_state
from app.services import profile_service

logger = get_logger(__name__)

router = APIRouter(
    prefix="/api/v1/chat",
    tags=["chat"],
    dependencies=[Depends(require_full_access)],
)

# Phase 1 用户 ID 固定为 "default"
_DEFAULT_USER_ID = "default"

# 同一 (user, conversation) 正在流式回复的会话集合：防止多标签页/并发请求误打断
_conv_inflight: set[str] = set()


# 各图节点的思考进度文案（按节点流式推送给前端，替代固定「正在思考」）
_NODE_PROGRESS = {
    "supervisor": "正在理解你的意图…",
    "rag_retrieval": "正在检索知识库…",
    "planner": "正在规划执行步骤…",
    "executor": "正在执行与调用工具…",
    "critic": "正在反思与校验…",
    "chat_simple": "正在组织回答…",
    "clarify": "正在思考如何澄清…",
    "scribe": "正在生成最终回答…",
}






# ============================================================
# 请求 / 响应模型
# ============================================================

class ChatRequest(BaseModel):
    """聊天请求。"""

    message: str = Field(..., min_length=1, description="用户输入文本")
    conversation_id: str | None = Field(
        None, description="会话 ID；为 None 表示新建会话"
    )
    user_id: str = Field(_DEFAULT_USER_ID, description="用户 ID")


class ExecutionInfo(BaseModel):
    """本次回答的**执行位置与理由**（RFC §8 的 S3，端云协同的可见性）。

    为什么要回给客户端：端侧宿主必须能显示"这次是在设备上算的还是上云了"，
    否则用户无法判断自己的数据有没有出端；这也是"可证明的隐私"（§4.5-G）的用户可见面。
    """

    primary_plane: str = Field("", description="产生最终答案的平面：edge | cloud")
    primary_role: str = Field("", description="产生最终答案的角色")
    model: str = Field("", description="实际使用的模型名")
    reason: str = Field("", description="路由决策理由（如 edge_preferred）")
    tier: str = Field("", description="端侧档位 short|default|quality")
    escalated: int = Field(0, description="本次请求中发生升级的角色数")
    by_plane: dict = Field(default_factory=dict, description="各平面参与的角色数")
    edge_decided: int = Field(0, description="判给端侧的角色数（含升级的）")
    edge_completed: int = Field(0, description="在端侧真正完成的角色数")
    latency_ms: float = Field(0.0, description="主角色耗时")
    versions: dict = Field(default_factory=dict, description="版本戳（§4.5-F）")
    roles: list = Field(default_factory=list, description="逐角色的落点明细")


class ChatResponse(BaseModel):
    """聊天响应（非流式）。"""

    conversation_id: str = Field(..., description="会话 ID")
    message: str = Field(..., description="助手回复文本")
    intent: str = Field("", description="识别到的意图")
    intent_confidence: float = Field(0.0, description="意图置信度")
    metrics: dict = Field(default_factory=dict, description="量化指标")
    trace_id: str = Field(..., description="本次调用的 trace ID")
    meta: dict = Field(default_factory=dict, description="扩展元信息（知识入库状态等）")
    degraded: bool = Field(False, description="本次是否发生 LLM 降级（reasoner→chat）")
    execution: ExecutionInfo = Field(default_factory=ExecutionInfo,
                                     description="本次执行位置/理由（端云协同 S3）")


# ============================================================
# 内部辅助
# ============================================================



async def _run_chat(
    ctx: AppContext,
    request: ChatRequest,
    user_id: str,
    on_progress: Callable[[str], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """
    执行一次完整的聊天流程并返回结构化结果。

    被非流式与流式端点共用。流程：
        1. 确保会话存在（必要时创建）
        2. 加载对话历史
        3. 创建初始 GraphState
        4. 运行 LangGraph
        5. 持久化用户与助手消息
        6. 更新 L1 短期记忆

    Args:
        user_id: 从 JWT 解析的真实用户 ID（不再使用 ChatRequest 默认值 "default"）

    Returns:
        包含 ``conversation_id`` / ``answer`` / ``intent`` /
        ``intent_confidence`` / ``metrics`` / ``trace_id`` / ``latency_ms`` 的字典
    """
    user_input = request.message

    # 0. Prompt 注入防护（规则层 + 可选 LLM 层）
    sec_cfg = ctx.config.security
    if sec_cfg.prompt_injection_guard is True:
        from app.core.guard import check_prompt_injection

        blocked, reason = await check_prompt_injection(
            user_input,
            blocked_patterns=sec_cfg.blocked_patterns,
            max_input_length=sec_cfg.max_input_length,
            llm_factory=ctx.llm_factory,
            use_llm_guard=sec_cfg.prompt_injection_use_llm,
            llm_role=sec_cfg.prompt_injection_llm_role,
        )
        if blocked:
            logger.warning("Prompt 注入拦截", user_id=user_id, reason=reason)
            raise SecurityError("输入被安全策略拦截", details={"reason": reason})

    graph_input = user_input

    # 1. 确保会话存在
    conv_id = request.conversation_id
    is_new_conv = not conv_id
    title = ""
    if not conv_id:
        title = user_input[:30] if user_input else "新会话"
        conv_id = await ctx.storage.create_conversation(user_id, title)
        logger.info("API 创建新会话", conv_id=conv_id, title=title)
    else:
        # 校验会话存在 + 归属（SEC-01 越权修复：非本用户会话一律 404，不泄露存在性）
        conv = await ctx.storage.get_conversation(conv_id)
        if conv is None or conv.get("user_id") != user_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"会话不存在: {conv_id}",
            )

    # 2. 加载对话历史（优先从短期记忆；若为空则从存储回填）
    history = await ctx.memory.get_messages(user_id, conv_id)
    if not history:
        # 从存储恢复历史到短期记忆
        stored = await ctx.storage.get_messages(conv_id)
        for msg in stored:
            role = msg.get("role")
            content = msg.get("content", "")
            if not content:
                continue
            if role == "user":
                await ctx.memory.add_message(user_id, conv_id, HumanMessage(content=content))
            elif role == "assistant":
                await ctx.memory.add_message(user_id, conv_id, AIMessage(content=content))
        # NEW-D：恢复完成后触发一次压缩，避免超大历史在内存中越积越大
        await ctx.memory.compress_if_needed(user_id, conv_id)
        history = await ctx.memory.get_messages(user_id, conv_id)

    # 3. 创建初始 GraphState
    trace_id = str(uuid.uuid4())
    bind_context(conversation_id=conv_id, trace_id=trace_id, user_id=user_id)
    state = create_initial_state(
        user_input=graph_input,
        conversation_id=conv_id,
        trace_id=trace_id,
        user_id=user_id,
        history=history,
    )

    # P0-1 修复：在 ainvoke 前注入 LLM 统计快照，供 Scribe 计算 per-request delta
    state["llm_stats_snapshot"] = ctx.llm_factory.snapshot_stats()

    # 4. 运行 LangGraph（P0-2 修复：全局异常兜底，避免工作流中断导致 500）
    #    用 contextvar 收集**本次请求**内各角色的路由事件 → 汇总成 execution（S3）
    start_time = time.time()
    # 手工 __enter__/__exit__ 而不是 with：下面这段已有 try/finally 降级兜底，
    # 用 with 会把整段再缩进一层——那种 diff 无法 review。
    _ev_cm = collect_route_events()
    route_events: dict[str, Any] = _ev_cm.__enter__()
    try:
        try:
            # 用 astream_events 流式执行：节点「开始」即推送思考进度（task 4 修复：
            # 此前仅在节点「完成」后推送，长耗时 LLM 节点（如 planner 15s）期间界面停更）
            acc: dict[str, Any] = {}
            async for ev in ctx.graph.astream_events(state, version="v2"):
                name = ev.get("name")
                event = ev.get("event")
                meta = ev.get("metadata", {})
                is_node_event = (
                    meta.get("langgraph_node") is not None
                    and name == meta.get("langgraph_node")
                )
                if is_node_event and event == "on_chain_start" and on_progress:
                    # 节点开始：立即推送「正在…」进度，让长耗时阶段可见
                    await on_progress(name)
                if is_node_event and event == "on_chain_end":
                    out = ev.get("data", {}).get("output")
                    if isinstance(out, dict):
                        acc.update(to_state_dict(out))
            final_state = {**dict(state), **acc}
        except Exception as e:
            # 工作流整体异常兜底：返回降级回复而非 500 中断
            logger.error(
                "LangGraph 工作流异常，触发降级",
                error=str(e),
                error_type=type(e).__name__,
                trace_id=trace_id,
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
        _ev_cm.__exit__(None, None, None)
        clear_context()

    latency_ms = int((time.time() - start_time) * 1000)

    # 5. 提取回复与指标
    state_dict = to_state_dict(final_state)
    answer = (
        state_dict.get("final_answer")
        or state_dict.get("draft_answer")
        or "（无回复）"
    )
    # 反馈闭环：提取回复中的 <PREF> 求职偏好并更新用户画像（方案 A；方案 B 待求职意图触发，见 11-EVOLUTION）
    answer = await profile_service.extract_and_update_profile(user_id, answer)
    metrics = state_dict.get("metrics") or {}
    # P0-6 修复：回填 e2e_latency_ms（Scribe 占位 0，由外层回填真实值）
    metrics["e2e_latency_ms"] = latency_ms
    intent = state_dict.get("intent", "")
    intent_confidence = state_dict.get("intent_confidence", 0.0)

    # 对话用量统计（Redis）：累计本轮 token 与费用，供前端展示（失败不阻塞回复）
    if ctx.usage_service is not None:
        try:
            await ctx.usage_service.record(
                conv_id=conv_id,
                user_id=user_id,
                input_tokens=metrics.get("total_input_tokens", 0),
                output_tokens=metrics.get("total_output_tokens", 0),
                cost_usd=metrics.get("total_cost_usd", 0.0),
            )
        except Exception as e:
            logger.warning("对话用量统计失败", error=str(e))

    # 6. 持久化消息
    user_msg = {
        "role": "user",
        "content": user_input,
        "trace_id": trace_id,
    }
    await ctx.storage.append_message(conv_id, user_msg)

    # 记录本轮 RAG 命中的知识条目 ID，供反馈飞轮（thumbs up/down）调整条目重要性
    rag_entry_ids = [
        r.get("entry_id")
        for r in (state_dict.get("pre_retrieval_results") or [])
        if r.get("entry_id")
    ]

    assistant_msg = {
        "role": "assistant",
        "content": answer,
        "intent": intent,
        "tokens_input": metrics.get("total_input_tokens", 0),
        "tokens_output": metrics.get("total_output_tokens", 0),
        "latency_ms": latency_ms,
        "trace_id": trace_id,
        "rag_entry_ids": rag_entry_ids,
        # NEW-F 修复：持久化重试/降级质量指标，提升可追溯性
        "llm_retried": metrics.get("llm_retried", False),
        "llm_retry_count": metrics.get("llm_retry_count", 0),
        "llm_degraded": metrics.get("llm_degraded", False),
        "llm_degradation_count": metrics.get("llm_degradation_count", 0),
    }
    await ctx.storage.append_message(conv_id, assistant_msg)

    # 7. 更新 L1 短期记忆
    await ctx.memory.add_message(user_id, conv_id, HumanMessage(content=user_input))
    await ctx.memory.add_message(user_id, conv_id, AIMessage(content=answer))
    try:
        await ctx.memory.compress_if_needed(user_id, conv_id)
    except Exception as e:
        logger.warning("短期记忆压缩失败", error=str(e))

    # Phase 2: 知识自迭代入库（不阻塞主回复，失败不影响用户收到答案）
    ingest_status = "disabled"
    ingest_reason = ""
    if ctx.knowledge_ingester is not None and ctx.knowledge_base is not None:
        try:
            ingest_result = await ctx.knowledge_ingester.ingest_conversation(
                user_input=user_input,
                final_answer=answer,
                summary=state_dict.get("summary", ""),
                importance_score=state_dict.get("importance_score", 0.0),
                conv_id=conv_id,
                user_id=user_id,
                knowledge_base=ctx.knowledge_base,
            )
            ingest_status = ingest_result.status
            ingest_reason = ingest_result.reason
            logger.info(
                "知识自迭代入库完成",
                ingest_status=ingest_status,
                entry_id=ingest_result.entry_id,
                conv_id=conv_id,
            )
        except Exception as e:
            ingest_status = "error"
            ingest_reason = str(e)
            logger.warning("知识自迭代入库失败", error=str(e), conv_id=conv_id)

    # 新会话：根据「首条提问 + 首条答复」用 LLM 提炼标题并回写（失败回退首条提问前 30 字）
    if is_new_conv:
        gen_title = await _generate_conversation_title(ctx, user_input, answer)
        if gen_title and gen_title != title:
            try:
                await ctx.storage.update_conversation(conv_id, {"title": gen_title})
                title = gen_title
            except Exception as e:
                logger.warning("回写会话标题失败", error=str(e), conv_id=conv_id)

    # L2 中期记忆（Redis）：记录本轮对话话题，供跨会话连续性使用（失败不阻塞回复）
    if ctx.session_memory is not None:
        try:
            await ctx.session_memory.record_topic(
                user_id, title or intent or user_input[:30], conv_id=conv_id
            )
        except Exception as e:
            logger.warning("L2 记录话题失败", error=str(e))

    return {
        "conversation_id": conv_id,
        "answer": answer,
        "title": title,
        "intent": intent,
        "intent_confidence": intent_confidence,
        "metrics": metrics,
        "trace_id": trace_id,
        "latency_ms": latency_ms,
        "ingest_status": ingest_status,
        "ingest_reason": ingest_reason,
        "execution": summarize_route_events(route_events),
    }


async def _generate_conversation_title(ctx: AppContext, user_input: str, answer: str) -> str:
    """根据用户首条提问 + 助手首条答复，用 LLM 提炼简短会话标题；失败回退首条提问前 30 字。"""
    fallback = (user_input or "").strip()[:30] or "新会话"
    try:
        prompt = (
            "你是对话标题生成器。请根据用户的问题与助手回答，输出一个不超过 16 个字的中文标题，"
            "准确概括本次对话主题。只输出标题本身：不要引号、不要书名号、不要标点结尾、不要任何解释。"
        )
        messages = [
            SystemMessage(content=prompt),
            HumanMessage(content=f"【用户】{user_input[:500]}\n\n【助手】{answer[:800]}"),
        ]
        resp = await ctx.llm_factory.ainvoke_with_stats("chat_simple", messages)
        text = resp.content if hasattr(resp, "content") else str(resp)
        title = (text or "").strip().strip('"“”\'《》')
        title = title.splitlines()[0].strip() if title else ""
        if title:
            return title[:30]
    except Exception as e:
        logger.warning("生成会话标题失败，回退首条提问", error=str(e))
    return fallback


async def _stream_tokens(text: str) -> AsyncIterator[str]:
    """
    逐字符流式产出 token 事件。

    将完整回复按字符切分，逐个 yield 为 SSE ``token`` 事件的 JSON 数据。
    每个字符之间让出一次事件循环，避免长文本阻塞。
    """
    for ch in text:
        yield json.dumps({"type": "token", "content": ch}, ensure_ascii=False)
        await asyncio.sleep(0)


# ============================================================
# 路由
# ============================================================

@router.post("/", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    ctx: AppContext = Depends(get_app_context),
    user_id: str = Depends(get_current_user),
) -> ChatResponse:
    """
    发送消息（非流式）。

    运行 LangGraph 工作流并返回完整的助手回复与元信息。
    """
    try:
        result = await _run_chat(ctx, request, user_id)
    except HTTPException:
        record_chat_error()
        raise
    except SEKBError as e:
        logger.warning("聊天处理失败", error=str(e), exc_info=True)
        record_chat_error()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=e.message,
        ) from e
    except Exception as e:
        logger.error("聊天意外异常", error=str(e), exc_info=True)
        record_chat_error()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"内部错误: {e}",
        ) from e

    # 记录 Prometheus 指标（埋点失败不影响主流程）
    record_chat_metrics(result)

    return ChatResponse(
        conversation_id=result["conversation_id"],
        message=result["answer"],
        intent=result["intent"],
        intent_confidence=result["intent_confidence"],
        metrics=result["metrics"],
        trace_id=result["trace_id"],
        degraded=bool(result.get("metrics", {}).get("llm_degraded", False)),
        execution=ExecutionInfo(**(result.get("execution") or {})),
        meta={
            "title": result.get("title", ""),
            "ingest_status": result.get("ingest_status", "disabled"),
            "ingest_reason": result.get("ingest_reason", ""),
        },
    )


@router.post("/stream")
async def chat_stream(
    request: ChatRequest,
    ctx: AppContext = Depends(get_app_context),
    user_id: str = Depends(get_current_user),
) -> StreamingResponse:
    """
    发送消息（SSE 流式响应）。

    先运行 LangGraph 工作流得到完整回复，再按 token（字符）逐个推送：
        ``data: {"type": "token", "content": "你"}``
        ...
        ``data: {"type": "done", "meta": {"intent": "...", "metrics": {...}}}``

    出错时推送 ``error`` 事件并结束流。

    使用 FastAPI 原生 ``StreamingResponse`` 手动格式化 SSE 事件，
    避免 sse-starlette 的缓冲行为导致浏览器端无法流式接收。
    """
    async def event_generator() -> AsyncIterator[bytes]:
        # 同一会话并发流式回复防护：配合前端「对话排队」，避免多标签页/重复请求误打断
        lock_key = f"{user_id}|{request.conversation_id or ''}"
        if lock_key in _conv_inflight:
            err = json.dumps(
                {"type": "error", "detail": "该会话正在回复中，请稍后再试（前端已自动排队）"},
                ensure_ascii=False,
            )
            yield f"data: {err}\n\n".encode("utf-8")
            return
        _conv_inflight.add(lock_key)
        try:
            # 先推送一个初始 thinking 事件，让前端立即显示「思考中」提示
            init_thinking = json.dumps(
                {"type": "thinking", "content": "正在思考..."},
                ensure_ascii=False,
            )
            yield f"data: {init_thinking}\n\n".encode("utf-8")

            # 每个图节点完成时，推送真实思考进度（B1）；答案 token 也通过 token sink 回传（R2-06）。
            # 用统一队列在「跑图任务」与「SSE 输出」之间传递事件，实现真流式。
            out_q: asyncio.Queue[tuple[str, str]] = asyncio.Queue()

            async def on_progress(node_name: str) -> None:
                msg = _NODE_PROGRESS.get(node_name)
                if msg:
                    await out_q.put(("thinking", msg))

            async def on_token(token: str) -> None:
                if token:
                    await out_q.put(("token", token))

            # 设置 token sink（contextvar），使 Executor 流式生成答案时逐 token 回传
            sink_token = set_token_sink(on_token)

            streamed_answer = False

            def _yield_event(kind: str, content: str) -> bytes:
                payload = json.dumps({"type": kind, "content": content}, ensure_ascii=False)
                return f"data: {payload}\n\n".encode("utf-8")

            try:
                run_task = asyncio.create_task(
                    _run_chat(ctx, request, user_id, on_progress=on_progress)
                )
                # 图运行期间，持续消费队列事件推给前端
                while not run_task.done():
                    try:
                        kind, content = await asyncio.wait_for(out_q.get(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    if kind == "token":
                        streamed_answer = True
                    yield _yield_event(kind, content)

                # 图运行结束，排空剩余事件（如最后的「正在生成最终回答」与尾部 token）
                while not out_q.empty():
                    kind, content = out_q.get_nowait()
                    if kind == "token":
                        streamed_answer = True
                    yield _yield_event(kind, content)
            finally:
                # 客户端断开时事件生成器被取消，但后台 _run_chat 任务仍在跑——
                # 继续烧 LLM token 并落库，必须显式取消（否则资源泄漏）。
                if not run_task.done():
                    run_task.cancel()
                reset_token_sink(sink_token)

            # 取结果（异常转为 error 事件）
            try:
                result = run_task.result()
            except HTTPException as e:
                record_chat_error()
                payload = json.dumps(
                    {"type": "error", "detail": e.detail}, ensure_ascii=False
                )
                yield f"data: {payload}\n\n".encode("utf-8")
                return
            except SEKBError as e:
                logger.warning("流式聊天处理失败", error=str(e), exc_info=True)
                record_chat_error()
                payload = json.dumps(
                    {"type": "error", "detail": e.message}, ensure_ascii=False
                )
                yield f"data: {payload}\n\n".encode("utf-8")
                return
            except Exception as e:
                logger.error("流式聊天意外异常", error=str(e), exc_info=True)
                record_chat_error()
                payload = json.dumps(
                    {"type": "error", "detail": f"内部错误: {e}"}, ensure_ascii=False
                )
                yield f"data: {payload}\n\n".encode("utf-8")
                return

            # 记录 Prometheus 指标（埋点失败不影响主流程）
            try:
                record_chat_metrics(result)
            except Exception as e:
                logger.warning("流式埋点失败", error=str(e))

            # 逐 token 推送 + done 事件（包裹 try/except 确保流正确终止）
            try:
                if not streamed_answer:
                    # 答案未走 token sink（chitchat/clarify/降级兜底），按旧逻辑逐字推送
                    async for token_data in _stream_tokens(result["answer"]):
                        yield f"data: {token_data}\n\n".encode("utf-8")

                meta = {
                    "conversation_id": result["conversation_id"],
                    "title": result.get("title", ""),
                    "intent": result["intent"],
                    "intent_confidence": result["intent_confidence"],
                    "metrics": result["metrics"],
                    "trace_id": result["trace_id"],
                    "latency_ms": result["latency_ms"],
                    "ingest_status": result.get("ingest_status", "disabled"),
                    "ingest_reason": result.get("ingest_reason", ""),
                    "degraded": bool(result.get("metrics", {}).get("llm_degraded", False)),
                    # 端云协同 S3：客户端据此显示"本次在设备上/云端完成"
                    "execution": result.get("execution", {}),
                }
                done_payload = json.dumps(
                    {"type": "done", "meta": meta}, ensure_ascii=False
                )
                yield f"data: {done_payload}\n\n".encode("utf-8")
            except Exception as e:
                logger.error("流式推送异常", error=str(e), exc_info=True)
                payload = json.dumps(
                    {"type": "error", "detail": f"流式推送异常: {e}"},
                    ensure_ascii=False,
                )
                yield f"data: {payload}\n\n".encode("utf-8")
        finally:
            _conv_inflight.discard(lock_key)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            # 禁用 nginx/uvicorn 等中间层缓冲
            "Transfer-Encoding": "chunked",
        },
    )


# ============================================================
# 对话用量统计（token / 费用展示）
# ============================================================

_EMPTY_USAGE: dict[str, Any] = {
    "input_tokens": 0,
    "output_tokens": 0,
    "tokens": 0,
    "calls": 0,
    "cost_usd": 0.0,
    "cost_cny": 0.0,
}


@router.get("/usage")
async def get_usage(
    conversation_id: str | None = None,
    ctx: AppContext = Depends(get_app_context),
    user_id: str = Depends(get_current_user),
) -> dict[str, Any]:
    """
    返回「当前对话」与「用户全部对话累计」的 token 与费用统计（费用为约合人民币）。

    - ``conversation_id`` 为空时只返回用户累计。
    - Redis 未启用时 ``enabled=False``，各项为零，前端静默不展示。
    """
    if ctx.usage_service is None:
        return {"enabled": False, "conversation": _EMPTY_USAGE, "total": _EMPTY_USAGE}

    conversation = dict(_EMPTY_USAGE)
    if conversation_id:
        conv = await ctx.storage.get_conversation(conversation_id)
        if conv is None or conv.get("user_id") != user_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"会话不存在: {conversation_id}",
            )
        conversation = await ctx.usage_service.get_conversation(conversation_id)

    total = await ctx.usage_service.get_user_total(user_id)
    return {"enabled": True, "conversation": conversation, "total": total}
