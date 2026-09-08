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
import re
import time
import uuid
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.api.server import get_app_context
from app.core.access import require_full_access
from app.core.auth import get_current_user
from app.core.bootstrap import AppContext
from app.core.exceptions import SEKBError
from app.core.logging import bind_context, clear_context, get_logger
from app.core.metrics import record_chat_error, record_chat_metrics
from app.graph.state import create_initial_state

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

# 求职偏好结构化标签（应聘助手在回复末尾输出，供系统提取并更新用户画像）
_PREF_RE = re.compile(r"<PREF>(.*?)</PREF>", re.DOTALL)


# 记录员 LLM 提示词（方案 B）：不依赖主 LLM 输出标签，后台独立抽取求职偏好
_PREF_EXTRACT_PROMPT = """你是一个「用户求职偏好记录员」。请根据下面的用户与求职顾问的对话，
提取用户**明确表达或强烈暗示**的求职偏好。只输出一个 JSON 对象，不要输出任何其他文字，不要用代码围栏。
字段可省略：
{{
  "target_roles": ["目标岗位方向，如 AI 应用开发工程师"],
  "target_cities": ["意向城市"],
  "min_salary_k": 30,
  "keywords": ["关注的技术栈/技能/关键词"],
  "career_goal": "简短职业目标（一句）",
  "skills": ["已有技能"]
}}
如果用户没表达任何新偏好，输出空对象 {{}}。

【对话记录】
{context}"""


# 后台偏好抽取任务引用（防止被 GC，完成后自动移除）
_background_extract_tasks: set[Any] = set()


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
    skill: str = Field(
        default="",
        description="技能模式：通用助手 / 应聘助手 / 科技资讯助手；空/通用=普通聊天",
    )


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


async def _build_job_analysis_context(user_id: str) -> str:
    """
    读取用户近期的职位分析结果（存档报告 + 市场概况 + 最近采集职位），
    返回给「应聘助手」的简洁注入上下文，实现与职位分析模块的数据共享。

    任何读取/解析失败均静默跳过，返回可能为空串的摘要。
    """
    parts: list[str] = []
    try:
        from app.agents.job import archive, market

        # 1. 最近存档分析报告（batch 优先，取最近 2 份）
        reports = archive.list_reports(user_id)
        for rep in (reports or [])[:2]:
            full = archive.get_report(user_id, rep["id"])
            if not full:
                continue
            r = full.get("report") or {}
            lines = [f"### 职位分析：{full.get('title') or rep.get('title')}"]
            if r.get("keyword") or r.get("city"):
                lines.append(
                    f"- 关键词：{r.get('keyword','')} · 城市：{r.get('city','')} · 职位 {r.get('job_count','')} 个"
                )
            # 热门方向 Top3
            th = (r.get("market") or {}).get("track_heatmap") or []
            if th:
                lines.append(
                    "- 热门方向：" + "；".join(
                        f"{t.get('track','')}({t.get('job_count','')}岗/{t.get('salary','')})" for t in th[:3]
                    )
                )
            # 需补知识 Top3
            ki = (r.get("knowledge_iteration") or {}).get("foundation") or []
            if ki:
                lines.append(
                    "- 建议补强：" + "、".join(
                        f"{t.get('topic','')}" for t in ki[:3]
                    )
                )
            # 代表职位 Top4
            jobs = r.get("jobs") or []
            if jobs:
                lines.append(
                    "- 代表职位：" + "；".join(
                        f"{j.get('title','')}@{j.get('company','')} {j.get('salary','')}" for j in jobs[:4]
                    )
                )
            parts.append("\n".join(lines))

        # 2. 缓存的市场报告概况（「一键分析市场」结果）
        rep = market.get_cached_report(user_id)
        if rep:
            overview = rep.get("overview") or rep.get("summary") or rep.get("highlights")
            if isinstance(overview, str) and overview:
                parts.append("【近期职位市场概况】\n" + overview[:600])

    except Exception as e:
        logger.warning("读取职位分析结果失败", error=str(e))

    return "\n\n".join(parts)


async def _build_skill_context(ctx: AppContext, user_id: str, skill: str) -> str:
    """
    构建技能内置上下文，注入到本次 LLM 输入（不写入会话历史、不污染用户消息）。

    - 应聘助手：注入「求职者画像」（目标岗位/城市/薪资/技术栈）+ 近期职位市场概况。
    - 科技资讯助手：注入「用户关注的资讯大类」。

    任何环节失败均静默跳过，返回可能为空串的上下文。
    """
    parts: list[str] = []

    # 用户画像（按 user_id 隔离）
    profile = None
    try:
        from app.storage.profile_storage import ProfileStorage

        profile = await ProfileStorage("data/profile").get(user_id)
    except Exception as e:
        logger.warning("skill 上下文：读取画像失败", error=str(e))

    if skill == "应聘助手":
        # 角色指令：不强依赖画像，主动向用户提问了解求职偏好；约定输出结构化偏好标签
        parts.append(
            "你是一名专业求职顾问。请结合用户的求职背景提供深入咨询"
            "（岗位匹配、虚拟面试、知识补充、简历建议等）。\n"
            "如果还不清楚用户的求职偏好，请主动、自然地向他提问以下关键信息："
            "目标岗位方向、意向城市、期望月薪、掌握的技能/技术栈、职业目标。"
            "用户给出信息后，请在回复最后单独输出一行 "
            "`<PREF>{\"target_roles\":[\"..\"],\"target_cities\":[\"..\"],"
            "\"min_salary_k\":30,\"keywords\":[\"..\"]}</PREF>` "
            "总结提取到的求职偏好（字段可省略，无则忽略），供系统更新画像。"
        )
        # 画像参考（如已有，简明给出）
        if profile and profile.job_preferences:
            jp = profile.job_preferences
            lines = ["【已知求职者画像（供参考，不全则补充提问）】"]
            if jp.target_roles:
                lines.append("- 目标岗位：" + "、".join(jp.target_roles))
            if jp.target_cities:
                lines.append("- 意向城市：" + "、".join(jp.target_cities))
            if jp.min_salary_k:
                lines.append(f"- 最低月薪：{jp.min_salary_k}k")
            if jp.keywords:
                lines.append("- 关注技术栈：" + "、".join(jp.keywords))
            parts.append("\n".join(lines))
        # 近期职位分析结果（与职位分析模块共享数据：存档报告 + 市场概况 + 最近采集职位）
        job_ctx = await _build_job_analysis_context(user_id)
        if job_ctx:
            parts.append("【近期职位分析结果】\n" + job_ctx)

    elif skill == "科技资讯助手":
        if profile and profile.news_interests:
            parts.append(
                "【用户关注的资讯大类】" + "、".join(profile.news_interests)
            )

    return "\n\n".join(parts)


async def _extract_and_update_profile(user_id: str, answer: str) -> str:
    """
    从应聘助手回复中提取 ``<PREF>...</PREF>`` 求职偏好，更新用户画像，并返回去掉该标签的干净回复。

    实现「聊天偏好 → 用户画像」的反馈闭环：LLM 在回复末尾输出结构化偏好，
    系统解析后写入画像（按 user_id 隔离），供后续 skill 与招聘/资讯模块使用。
    """
    m = _PREF_RE.search(answer)
    if not m:
        return answer
    try:
        import json

        data = json.loads(m.group(1).strip())
        patch: dict[str, Any] = {}
        job: dict[str, Any] = {}
        for key in ("target_roles", "target_cities", "min_salary_k", "keywords"):
            if key in data:
                job[key] = data[key]
        if job:
            patch["job_preferences"] = job  # upsert_update 深合并，不覆盖未传字段
        if data.get("skills"):
            patch["skills"] = data["skills"]
        if data.get("career_goal"):
            patch["career_goal"] = data["career_goal"]
        if patch:
            from app.storage.profile_storage import ProfileStorage

            await ProfileStorage("data/profile").upsert_update(user_id, patch)
            logger.info("已从聊天更新用户画像", user_id=user_id, fields=list(patch.keys()))
    except Exception as e:
        logger.warning("解析/更新求职偏好失败", user_id=user_id, error=str(e))

    # 去掉 <PREF> 标签，避免展示给用户
    return _PREF_RE.sub("", answer).strip()


def _extract_json_from_llm(text: str) -> dict[str, Any] | None:
    """从 LLM 输出中稳健地提取 JSON 对象（兼容 ```json 围栏与前后说明文字）。"""
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1))
        except Exception:
            pass
    try:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
    except Exception:
        pass
    try:
        return json.loads(text.strip())
    except Exception:
        return None


async def _apply_pref_to_profile(user_id: str, data: dict[str, Any]) -> bool:
    """把提取到的偏好写入用户画像，返回是否写了。"""
    try:
        patch: dict[str, Any] = {}
        job: dict[str, Any] = {}
        for key in ("target_roles", "target_cities", "min_salary_k", "keywords"):
            if key in data:
                job[key] = data[key]
        if job:
            patch["job_preferences"] = job  # upsert_update 深合并，不覆盖未传字段
        if data.get("skills"):
            patch["skills"] = data["skills"]
        if data.get("career_goal"):
            patch["career_goal"] = data["career_goal"]
        if not patch:
            return False
        from app.storage.profile_storage import ProfileStorage

        await ProfileStorage("data/profile").upsert_update(user_id, patch)
        logger.info("已从聊天更新用户画像", user_id=user_id, fields=list(patch.keys()))
        return True
    except Exception as e:
        logger.warning("写用户画像失败", user_id=user_id, error=str(e))
        return False


async def _record_preferences_task(
    ctx: AppContext, conv_id: str, user_id: str, user_input: str, answer: str
) -> None:
    """记录员 LLM：后台分析对话，抽取求职偏好写入画像（方案 B，不依赖主 LLM 输出标签）。"""
    try:
        # 读取最近对话作为上下文（来自短期记忆）
        context_lines: list[str] = []
        try:
            history = await ctx.memory.get_messages(user_id, conv_id)
            for msg in (history or [])[-10:]:
                role = "用户" if getattr(msg, "type", "") == "human" else "助手"
                content = getattr(msg, "content", "") or ""
                if content:
                    context_lines.append(f"{role}: {str(content)[:300]}")
        except Exception:
            pass
        if user_input:
            context_lines.append(f"用户: {user_input[:300]}")
        if answer:
            context_lines.append(f"助手: {answer[:300]}")
        context_text = "\n".join(context_lines)

        messages = [
            SystemMessage(content="你是求职偏好记录员，只输出结构化 JSON。"),
            HumanMessage(content=_PREF_EXTRACT_PROMPT.format(context=context_text)),
        ]
        response = await ctx.llm_factory.ainvoke_with_stats("chat_simple", messages)
        text = response.content if hasattr(response, "content") else str(response)
        data = _extract_json_from_llm(text)
        if data is None:
            logger.info("记录员 LLM 未输出有效 JSON，跳过画像更新")
            return
        if await _apply_pref_to_profile(user_id, data):
            logger.info("记录员已提取偏好写入画像")
    except Exception as e:
        logger.warning("记录员抽取偏好失败", user_id=user_id, error=str(e), exc_info=True)


def _schedule_preference_extraction(
    ctx: AppContext, conv_id: str, user_id: str, user_input: str, answer: str
) -> None:
    """调度后台偏好抽取任务（不阻塞主回复）。"""
    try:
        task = asyncio.create_task(
            _record_preferences_task(ctx, conv_id, user_id, user_input, answer)
        )
        _background_extract_tasks.add(task)
        task.add_done_callback(_background_extract_tasks.discard)
    except Exception as e:
        logger.warning("调度偏好抽取任务失败", user_id=user_id, error=str(e))


async def _run_chat(ctx: AppContext, request: ChatRequest, user_id: str) -> dict[str, Any]:
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

    # 技能模式：为本次 LLM 输入注入内置上下文（不写入会话历史、不污染用户消息）
    graph_input = user_input
    skill = (request.skill or "").strip()
    if skill and skill != "通用助手":
        skill_ctx = await _build_skill_context(ctx, user_id, skill)
        if skill_ctx:
            graph_input = f"{skill_ctx}\n\n===== 用户问题 =====\n{user_input}"

    # 1. 确保会话存在
    conv_id = request.conversation_id
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
    # 反馈闭环 A：应聘助手可能输出 <PREF> 求职偏好，提取后更新用户画像，并去掉标签
    answer = await _extract_and_update_profile(user_id, answer)
    # 反馈闭环 B（更稳）：后台记录员 LLM 独立分析对话，抽取求职偏好写入画像（不阻塞主回复）
    if (request.skill or "").strip() == "应聘助手":
        _schedule_preference_extraction(ctx, conv_id, user_id, user_input, answer)
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
        meta={
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
            # 先推送 thinking 事件，让前端立即显示"思考中"提示
            thinking_payload = json.dumps(
                {"type": "thinking", "content": "正在思考..."},
                ensure_ascii=False,
            )
            yield f"data: {thinking_payload}\n\n".encode("utf-8")

            try:
                result = await _run_chat(ctx, request, user_id)
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
                async for token_data in _stream_tokens(result["answer"]):
                    yield f"data: {token_data}\n\n".encode("utf-8")

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
