"""
知识库分享 API 路由。

支持将所有者知识库通过分享链接暴露给其他已登录用户，提供：
- 只读浏览：访问者可查看分享知识库的条目
- 基于分享知识库的问答对话：检索所有者知识库后由 LLM 作答

端点：
- ``POST   /api/v1/share``                    创建分享（所有者）
- ``GET    /api/v1/share``                     列出我的分享（所有者）
- ``GET    /api/v1/share/{share_id}``          获取分享信息（任意已登录用户）
- ``DELETE /api/v1/share/{share_id}``          撤销分享（所有者）
- ``GET    /api/v1/share/{share_id}/entries``  浏览分享知识库条目（只读）
- ``GET    /api/v1/share/{share_id}/messages`` 获取分享会话历史（访问者）
- ``POST   /api/v1/share/{share_id}/chat/stream`` 基于分享知识库问答（SSE 流式）

权限：
- 创建/列出/撤销：仅所有者
- 浏览/对话：任意已登录用户（需分享有效且未过期）
- 检索/对话仅读取所有者知识库，不写入，不修改。
"""
from __future__ import annotations

import json
import time
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.api.server import get_app_context
from app.core.access import require_full_access
from app.core.auth import get_current_user
from app.core.bootstrap import AppContext
from app.core.exceptions import SecurityError
from app.core.logging import get_logger
from app.core.utils import fmt_dt as _fmt_dt
from app.services.share_service import get_valid_share, owner_display_name
from app.tools.rag.format import format_rag_share_context as _build_rag_context

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/share", tags=["share"],
    dependencies=[Depends(require_full_access)],
)


# 分享对话使用的 LLM 角色（deepseek-chat，自然闲聊）
_CHAT_ROLE = "chat_simple"
# 检索条目数
_RETRIEVE_TOP_K = 5
# 历史消息窗口（最近 N 轮）
_HISTORY_WINDOW = 8


# ============================================================
# 请求 / 响应模型
# ============================================================

class CreateShareRequest(BaseModel):
    """创建分享请求。"""

    title: str = Field(default="", max_length=100, description="分享标题")
    # 分享范围：三级分类（空表示不限该级）；三个都为空 = 分享整个知识库
    category_l1: str = Field(default="", description="分享范围·一级分类")
    category_l2: str = Field(default="", description="分享范围·二级分类")
    category_l3: str = Field(default="", description="分享范围·三级分类")
    # 有效期天数：默认 7 天（2026-09-16 从 30 天收紧，降低链接泄露后的暴露窗口）；
    # 上限 30 天；<=0 表示永不过期（不推荐）
    expires_days: int = Field(default=7, ge=-1, le=30, description="分享有效期天数（默认 7，上限 30；<=0 永不过期）")


class SharedChatRequest(BaseModel):
    """分享知识库对话请求。"""

    message: str = Field(..., min_length=1, description="用户输入文本")


# ============================================================
# 辅助
# ============================================================

def _require_share_storage(ctx: AppContext) -> Any:
    """获取分享存储，未初始化则 500。"""
    if ctx.share_storage is None:
        raise HTTPException(500, "分享存储未初始化")
    return ctx.share_storage


async def _get_valid_share(ctx: AppContext, share_id: str):
    """获取并校验分享有效性（实现收敛至 share_service.get_valid_share）。"""
    storage = _require_share_storage(ctx)
    share = await get_valid_share(storage, share_id)
    # 记录一次访问：所有者能在列表里看到 view_count / last_accessed_at（此前完全无感知）
    await storage.record_view(share_id)
    return share


def _owner_display_name(ctx: AppContext, owner_user_id: str) -> str:
    """获取所有者展示名（实现收敛至 share_service.owner_display_name）。"""
    return owner_display_name(ctx, owner_user_id, "知识库所有者")


def _history_to_messages(history: list[dict[str, Any]]):
    """将会话历史转为 LangChain 消息列表（应用窗口截断）。"""
    recent = history[-_HISTORY_WINDOW * 2:]  # 每轮 user+assistant 两条
    msgs = []
    for m in recent:
        role = m.get("role")
        content = m.get("content", "")
        if not content:
            continue
        if role == "user":
            msgs.append(HumanMessage(content=content))
        elif role == "assistant":
            msgs.append(AIMessage(content=content))
    return msgs


# ============================================================
# 路由：分享管理
# ============================================================

@router.post("")
async def create_share(
    request: CreateShareRequest,
    user_id: str = Depends(get_current_user),
):
    """创建知识库分享链接（可限定三级分类范围，空表示整个知识库）。"""
    ctx = get_app_context()
    share_storage = _require_share_storage(ctx)

    # 校验知识库已启用且有内容
    if ctx.knowledge_base is None or ctx.vector_store is None:
        raise HTTPException(503, "知识库未启用，无法分享")

    cat_l1 = (request.category_l1 or "").strip()
    cat_l2 = (request.category_l2 or "").strip()
    cat_l3 = (request.category_l3 or "").strip()

    # 分类范围合法性校验（选择下级必须带上级，且必须存在于默认目录）
    # 修复 R2-01：层级不完整（如只给 l2/l3 不给 l1）会导致 is_scoped() 误判，分享退化为全库
    if cat_l3 and not (cat_l1 and cat_l2):
        raise HTTPException(400, "分享范围分类层级不完整（三级需带上二级与一级）")
    if cat_l2 and not cat_l1:
        raise HTTPException(400, "分享范围分类层级不完整（二级需带上一级）")

    if cat_l1:
        from app.core.categories import DEFAULT_CATEGORY_TREE, validate_category

        if cat_l2 and cat_l3:
            valid = validate_category(cat_l1, cat_l2, cat_l3)
        elif cat_l2:
            valid = cat_l2 in (DEFAULT_CATEGORY_TREE.get(cat_l1) or {})
        else:
            valid = cat_l1 in DEFAULT_CATEGORY_TREE
        if not valid:
            raise HTTPException(400, "分享范围分类无效，请重新选择")

    try:
        total = await ctx.knowledge_base.count_entries(
            user_id=user_id,
            category_l1=cat_l1 or None,
            category_l2=cat_l2 or None,
            category_l3=cat_l3 or None,
        )
    except Exception as e:
        logger.error("查询知识库条目数失败", error=str(e))
        total = 0

    if total == 0:
        raise HTTPException(400, "所选分享范围内没有内容，无法分享")

    share = await share_storage.create_share(
        owner_user_id=user_id,
        title=request.title or "我的知识库",
        category_l1=cat_l1,
        category_l2=cat_l2,
        category_l3=cat_l3,
        expires_days=request.expires_days,
    )
    logger.info("分享已创建", share_id=share.share_id, owner=user_id, entries=total,
                category=share.category_label())
    return {
        "share_id": share.share_id,
        "title": share.title,
        "permission": share.permission,
        "category_l1": share.category_l1,
        "category_l2": share.category_l2,
        "category_l3": share.category_l3,
        "category_label": share.category_label(),
        "created_at": _fmt_dt(share.created_at),
        "share_url": f"/sekb/share/{share.share_id}",
        "entries_count": total,
    }


@router.get("")
async def list_my_shares(
    user_id: str = Depends(get_current_user),
):
    """列出当前用户的分享。"""
    ctx = get_app_context()
    share_storage = _require_share_storage(ctx)
    shares = await share_storage.list_by_owner(user_id)

    # 附带条目数（按分享范围统计）
    result = []
    for s in shares:
        entries_count = 0
        try:
            if ctx.knowledge_base is not None:
                entries_count = await ctx.knowledge_base.count_entries(
                    user_id=s.owner_user_id,
                    category_l1=s.category_l1 or None,
                    category_l2=s.category_l2 or None,
                    category_l3=s.category_l3 or None,
                )
        except Exception:
            pass
        result.append({
            "share_id": s.share_id,
            "title": s.title,
            "permission": s.permission,
            "category_l1": s.category_l1,
            "category_l2": s.category_l2,
            "category_l3": s.category_l3,
            "category_label": s.category_label(),
            "created_at": _fmt_dt(s.created_at),
            "expires_at": _fmt_dt(s.expires_at),
            "is_active": s.is_active,
            "has_expired": not s.is_valid(),
            "view_count": s.view_count,
            "last_accessed_at": _fmt_dt(s.last_accessed_at),
            "share_url": f"/sekb/share/{s.share_id}",
            "entries_count": entries_count,
        })
    return {"shares": result, "total": len(result)}


@router.get("/{share_id}")
async def get_share_info(
    share_id: str,
    user_id: str = Depends(get_current_user),
):
    """获取分享信息（任意已登录用户可访问，用于访问者确认分享详情）。"""
    ctx = get_app_context()
    share = await _get_valid_share(ctx, share_id)

    entries_count = 0
    try:
        if ctx.knowledge_base is not None:
            entries_count = await ctx.knowledge_base.count_entries(
                user_id=share.owner_user_id,
                category_l1=share.category_l1 or None,
                category_l2=share.category_l2 or None,
                category_l3=share.category_l3 or None,
            )
    except Exception:
        pass

    return {
        "share_id": share.share_id,
        "title": share.title,
        "permission": share.permission,
        "category_l1": share.category_l1,
        "category_l2": share.category_l2,
        "category_l3": share.category_l3,
        "category_label": share.category_label(),
        "owner_name": _owner_display_name(ctx, share.owner_user_id),
        "is_active": share.is_active,
        "has_expired": not share.is_valid(),
        "created_at": _fmt_dt(share.created_at),
        "entries_count": entries_count,
        "is_owner": share.owner_user_id == user_id,
    }


@router.delete("/{share_id}")
async def revoke_share(
    share_id: str,
    user_id: str = Depends(get_current_user),
):
    """撤销分享（仅所有者）。"""
    ctx = get_app_context()
    share_storage = _require_share_storage(ctx)
    share = await share_storage.get_share(share_id)
    if share is None:
        raise HTTPException(404, "分享不存在")
    if share.owner_user_id != user_id:
        raise HTTPException(403, "无权操作")

    deleted = await share_storage.delete_share(share_id)
    return {"share_id": share_id, "deleted": deleted}


# ============================================================
# 路由：分享知识库只读浏览
# ============================================================

@router.get("/{share_id}/entries")
async def list_shared_entries(
    share_id: str,
    user_id: str = Depends(get_current_user),
    category_l1: str | None = Query(None, description="按一级分类筛选"),
    category_l2: str | None = Query(None, description="按二级分类筛选"),
    category_l3: str | None = Query(None, description="按三级分类筛选"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """浏览分享知识库条目（只读；分享限定了分类范围时，强制只看该范围内条目）。"""
    ctx = get_app_context()
    share = await _get_valid_share(ctx, share_id)

    if ctx.knowledge_base is None:
        return {"entries": [], "total": 0, "page": page, "page_size": page_size}

    # 分享若限定了分类范围，则以分享范围为准（忽略/覆盖查询参数，防止越权看其他分类）
    if share.is_scoped():
        category_l1 = share.category_l1
        category_l2 = share.category_l2 or None
        category_l3 = share.category_l3 or None

    try:
        offset = (page - 1) * page_size
        entries = await ctx.knowledge_base.list_entries(
            user_id=share.owner_user_id,
            category_l1=category_l1,
            category_l2=category_l2,
            category_l3=category_l3,
            limit=page_size,
            offset=offset,
        )
        total = await ctx.knowledge_base.count_entries(
            user_id=share.owner_user_id,
            category_l1=category_l1,
            category_l2=category_l2,
            category_l3=category_l3,
        )
    except Exception as e:
        logger.error("浏览分享知识库失败", share_id=share_id, error=str(e))
        entries = []
        total = 0

    items = []
    for e in entries:
        items.append({
            "entry_id": e.entry_id,
            "content": e.content[:300] + "..." if len(e.content) > 300 else e.content,
            "source": e.source,
            "source_id": e.source_id,
            "category_l1": getattr(e, "category_l1", "其他"),
            "category_l2": getattr(e, "category_l2", "待分类"),
            "category_l3": getattr(e, "category_l3", "未分类"),
            "created_at": _fmt_dt(e.created_at),
        })

    return {
        "entries": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# ============================================================
# 路由：分享知识库问答对话
# ============================================================

@router.get("/{share_id}/messages")
async def get_shared_messages(
    share_id: str,
    user_id: str = Depends(get_current_user),
):
    """获取访问者在当前分享下的会话历史。"""
    ctx = get_app_context()
    await _get_valid_share(ctx, share_id)
    share_storage = _require_share_storage(ctx)
    messages = await share_storage.get_messages(share_id, user_id)
    return {"messages": messages}


@router.post("/{share_id}/chat/stream")
async def shared_chat_stream(
    share_id: str,
    request: SharedChatRequest,
    user_id: str = Depends(get_current_user),
):
    """
    基于分享知识库的问答（SSE 流式）。

    流程：
        1. 校验分享有效性
        2. 从所有者知识库 RAG 检索相关条目
        3. 拼接上下文 + 历史消息，调用 LLM 生成回复
        4. 流式推送 token + done 事件
        5. 持久化 user/assistant 消息到分享会话

    仅读取所有者知识库，不写入，不修改。
    """
    ctx: AppContext = get_app_context()
    share = await _get_valid_share(ctx, share_id)
    owner_user_id = share.owner_user_id

    if ctx.vector_store is None or ctx.knowledge_base is None:
        raise HTTPException(503, "知识库未启用")

    share_storage = _require_share_storage(ctx)
    user_input = request.message

    # 分享问答为公开访问路径，应用规则层注入防护（长度+正则）；不启用 LLM 层（避免公开成本）
    sec_cfg = ctx.config.security
    if sec_cfg.prompt_injection_guard is True:
        from app.core.guard import check_prompt_injection

        blocked, reason = await check_prompt_injection(
            user_input,
            blocked_patterns=sec_cfg.blocked_patterns,
            max_input_length=sec_cfg.max_input_length,
            llm_factory=None,
            use_llm_guard=False,
        )
        if blocked:
            logger.warning("分享问答 Prompt 注入拦截", share_id=share_id, reason=reason)
            raise SecurityError("输入被安全策略拦截", details={"reason": reason})

    async def event_generator() -> AsyncIterator[bytes]:
        # thinking 提示
        thinking = json.dumps(
            {"type": "thinking", "content": "正在检索知识库..."}, ensure_ascii=False
        )
        yield f"data: {thinking}\n\n".encode("utf-8")

        try:
            # 1. RAG 检索（读取所有者知识库；分享限定分类范围时按范围检索）
            retrieved = await ctx.vector_store.search(
                query=user_input,
                user_id=owner_user_id,
                top_k=_RETRIEVE_TOP_K,
                min_score=0.3,
                category_l1=share.category_l1 or None,
                category_l2=share.category_l2 or None,
                category_l3=share.category_l3 or None,
            )
            context_text = _build_rag_context(retrieved)

            # 2. 加载历史
            history = await share_storage.get_messages(share_id, user_id)

            # 3. 构造消息并调用 LLM（真流式：边生成边推送）
            system_prompt = (
                "你是一个基于知识库的问答助手。请根据下方提供的知识库内容回答用户问题。"
                "如果知识库内容不足以回答，请如实说明，不要编造。"
                "回答应简洁、准确、有条理。\n\n"
                f"【知识库内容】\n{context_text}"
            )
            messages = [
                SystemMessage(content=system_prompt),
                *_history_to_messages(history),
                HumanMessage(content=user_input),
            ]

            # 检索完成后提示进入生成阶段
            generating = json.dumps(
                {"type": "thinking", "content": "正在生成回答..."}, ensure_ascii=False
            )
            yield f"data: {generating}\n\n".encode("utf-8")

            t0 = time.time()
            answer_parts: list[str] = []
            async for text in ctx.llm_factory.astream_with_stats(_CHAT_ROLE, messages):
                if not text:
                    continue
                answer_parts.append(text)
                yield f"data: {json.dumps({'type': 'token', 'content': text}, ensure_ascii=False)}\n\n".encode("utf-8")

            answer = "".join(answer_parts)
            latency_ms = int((time.time() - t0) * 1000)

            # 4. 持久化消息
            await share_storage.append_message(share_id, user_id, {
                "role": "user",
                "content": user_input,
            })
            await share_storage.append_message(share_id, user_id, {
                "role": "assistant",
                "content": answer,
                "latency_ms": latency_ms,
            })

            # 5. done 事件
            meta = {
                "latency_ms": latency_ms,
                "retrieved_count": len(retrieved),
                "share_id": share_id,
            }
            done = json.dumps({"type": "done", "meta": meta}, ensure_ascii=False)
            yield f"data: {done}\n\n".encode("utf-8")

        except HTTPException as e:
            err = json.dumps({"type": "error", "detail": e.detail}, ensure_ascii=False)
            yield f"data: {err}\n\n".encode("utf-8")
        except Exception as e:
            logger.error("分享对话失败", share_id=share_id, error=str(e), exc_info=True)
            inner_err = json.dumps({"type": "error", "detail": f"内部错误: {e}"}, ensure_ascii=False)
            yield f"data: {inner_err}\n\n".encode("utf-8")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Transfer-Encoding": "chunked",
        },
    )
