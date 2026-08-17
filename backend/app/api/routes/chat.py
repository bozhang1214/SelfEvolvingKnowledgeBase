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
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, status
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.api.server import get_app_context
from app.core.bootstrap import AppContext
from app.core.exceptions import SEKBError
from app.core.logging import bind_context, clear_context, get_logger
from app.graph.state import create_initial_state

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])

# Phase 1 用户 ID 固定为 "default"
_DEFAULT_USER_ID = "default"


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


class ChatResponse(BaseModel):
    """聊天响应（非流式）。"""

    conversation_id: str = Field(..., description="会话 ID")
    message: str = Field(..., description="助手回复文本")
    intent: str = Field("", description="识别到的意图")
    intent_confidence: float = Field(0.0, description="意图置信度")
    metrics: dict = Field(default_factory=dict, description="量化指标")
    trace_id: str = Field(..., description="本次调用的 trace ID")
    meta: dict = Field(default_factory=dict, description="扩展元信息（知识入库状态等）")


# ============================================================
# 内部辅助
# ============================================================

def _extract_state_dict(final_state: Any) -> dict[str, Any]:
    """
    从 LangGraph ainvoke 返回值中提取 GraphState 字典。

    LangGraph 不同版本可能返回 dict 或带 ``values()`` 方法的对象，
    统一转换为 dict 形式以便访问字段。
    """
    if isinstance(final_state, dict):
        return final_state
    if hasattr(final_state, "values"):
        try:
            return dict(final_state.values())
        except Exception:
            pass
    try:
        return dict(final_state)
    except Exception:
        return {}


async def _run_chat(ctx: AppContext, request: ChatRequest) -> dict[str, Any]:
    """
    执行一次完整的聊天流程并返回结构化结果。

    被非流式与流式端点共用。流程：
        1. 确保会话存在（必要时创建）
        2. 加载对话历史
        3. 创建初始 GraphState
        4. 运行 LangGraph
        5. 持久化用户与助手消息
        6. 更新 L1 短期记忆

    Returns:
        包含 ``conversation_id`` / ``answer`` / ``intent`` /
        ``intent_confidence`` / ``metrics`` / ``trace_id`` / ``latency_ms`` 的字典
    """
    user_input = request.message
    user_id = request.user_id or _DEFAULT_USER_ID

    # 1. 确保会话存在
    conv_id = request.conversation_id
    if not conv_id:
        title = user_input[:30] if user_input else "新会话"
        conv_id = await ctx.storage.create_conversation(user_id, title)
        logger.info("API 创建新会话", conv_id=conv_id, title=title)
    else:
        # 校验会话存在
        conv = await ctx.storage.get_conversation(conv_id)
        if conv is None:
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
        history = await ctx.memory.get_messages(user_id, conv_id)

    # 3. 创建初始 GraphState
    trace_id = str(uuid.uuid4())
    bind_context(conversation_id=conv_id, trace_id=trace_id, user_id=user_id)
    state = create_initial_state(
        user_input=user_input,
        conversation_id=conv_id,
        trace_id=trace_id,
        user_id=user_id,
        history=history,
    )

    # P0-1 修复：在 ainvoke 前注入 LLM 统计快照，供 Scribe 计算 per-request delta
    state["llm_stats_snapshot"] = ctx.llm_factory.snapshot_stats()

    # 4. 运行 LangGraph（P0-2 修复：全局异常兜底，避免工作流中断导致 500）
    start_time = time.time()
    try:
        try:
            final_state = await ctx.graph.ainvoke(state)
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
        clear_context()

    latency_ms = int((time.time() - start_time) * 1000)

    # 5. 提取回复与指标
    state_dict = _extract_state_dict(final_state)
    answer = (
        state_dict.get("final_answer")
        or state_dict.get("draft_answer")
        or "（无回复）"
    )
    metrics = state_dict.get("metrics") or {}
    # P0-6 修复：回填 e2e_latency_ms（Scribe 占位 0，由外层回填真实值）
    metrics["e2e_latency_ms"] = latency_ms
    intent = state_dict.get("intent", "")
    intent_confidence = state_dict.get("intent_confidence", 0.0)

    # 6. 持久化消息
    user_msg = {
        "role": "user",
        "content": user_input,
        "trace_id": trace_id,
    }
    await ctx.storage.append_message(conv_id, user_msg)

    assistant_msg = {
        "role": "assistant",
        "content": answer,
        "intent": intent,
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

    return {
        "conversation_id": conv_id,
        "answer": answer,
        "intent": intent,
        "intent_confidence": intent_confidence,
        "metrics": metrics,
        "trace_id": trace_id,
        "latency_ms": latency_ms,
        "ingest_status": ingest_status,
        "ingest_reason": ingest_reason,
    }


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
) -> ChatResponse:
    """
    发送消息（非流式）。

    运行 LangGraph 工作流并返回完整的助手回复与元信息。
    """
    try:
        result = await _run_chat(ctx, request)
    except HTTPException:
        raise
    except SEKBError as e:
        logger.warning("聊天处理失败", error=str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=e.message,
        ) from e
    except Exception as e:
        logger.error("聊天意外异常", error=str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"内部错误: {e}",
        ) from e

    return ChatResponse(
        conversation_id=result["conversation_id"],
        message=result["answer"],
        intent=result["intent"],
        intent_confidence=result["intent_confidence"],
        metrics=result["metrics"],
        trace_id=result["trace_id"],
        meta={
            "ingest_status": result.get("ingest_status", "disabled"),
            "ingest_reason": result.get("ingest_reason", ""),
        },
    )


@router.post("/stream")
async def chat_stream(
    request: ChatRequest,
    ctx: AppContext = Depends(get_app_context),
) -> EventSourceResponse:
    """
    发送消息（SSE 流式响应）。

    先运行 LangGraph 工作流得到完整回复，再按 token（字符）逐个推送：
        ``data: {"type": "token", "content": "你"}``
        ...
        ``data: {"type": "done", "meta": {"intent": "...", "metrics": {...}}}``

    出错时推送 ``error`` 事件并结束流。
    """
    async def event_generator() -> AsyncIterator[dict[str, str]]:
        try:
            result = await _run_chat(ctx, request)
        except HTTPException as e:
            yield {"event": "error", "data": json.dumps(
                {"type": "error", "detail": e.detail}, ensure_ascii=False
            )}
            return
        except SEKBError as e:
            logger.warning("流式聊天处理失败", error=str(e), exc_info=True)
            yield {"event": "error", "data": json.dumps(
                {"type": "error", "detail": e.message}, ensure_ascii=False
            )}
            return
        except Exception as e:
            logger.error("流式聊天意外异常", error=str(e), exc_info=True)
            yield {"event": "error", "data": json.dumps(
                {"type": "error", "detail": f"内部错误: {e}"}, ensure_ascii=False
            )}
            return

        # 逐 token 推送
        async for token_data in _stream_tokens(result["answer"]):
            yield {"event": "message", "data": token_data}

        # done 事件携带元信息
        meta = {
            "conversation_id": result["conversation_id"],
            "intent": result["intent"],
            "intent_confidence": result["intent_confidence"],
            "metrics": result["metrics"],
            "trace_id": result["trace_id"],
            "latency_ms": result["latency_ms"],
            "ingest_status": result.get("ingest_status", "disabled"),
            "ingest_reason": result.get("ingest_reason", ""),
        }
        yield {
            "event": "message",
            "data": json.dumps(
                {"type": "done", "meta": meta}, ensure_ascii=False
            ),
        }

    return EventSourceResponse(event_generator())
