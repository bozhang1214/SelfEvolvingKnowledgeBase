"""
知识库管理 API 路由（Phase 3）
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth import get_current_user
from app.core.bootstrap import get_app_context

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])


@router.get("")
async def list_knowledge(
    user_id: str = Depends(get_current_user),
    source: str | None = Query(None, description="按来源筛选"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """获取知识条目列表"""
    ctx = get_app_context()
    kb = ctx.knowledge_base
    if kb is None:
        return {"entries": [], "total": 0, "page": page, "page_size": page_size}

    try:
        # 通过 list_entries 获取条目
        entries = await kb.list_entries(user_id=user_id, source=source)
    except Exception as e:
        logger.error("获取知识列表失败", extra={"error": str(e)})
        entries = []

    # 分页
    total = len(entries)
    start = (page - 1) * page_size
    end = start + page_size
    page_entries = entries[start:end]

    return {
        "entries": [
            {
                "entry_id": e.entry_id,
                "content": e.content[:200] + "..." if len(e.content) > 200 else e.content,
                "source": e.source,
                "source_id": e.source_id,
                "importance_score": e.importance_score,
                "version": e.version,
                "created_at": e.created_at.isoformat() if e.created_at else "",
                "user_id": e.user_id,
            }
            for e in page_entries
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/search")
async def search_knowledge(
    q: str = Query(..., min_length=1, description="搜索关键词"),
    user_id: str = Depends(get_current_user),
    top_k: int = Query(10, ge=1, le=50),
):
    """搜索知识条目"""
    ctx = get_app_context()
    kb = ctx.knowledge_base
    if kb is None:
        return {"entries": []}

    try:
        entries = await kb.retrieve(query=q, user_id=user_id, top_k=top_k, min_score=0.0)
    except Exception as e:
        logger.error("知识搜索失败", extra={"error": str(e)})
        entries = []

    return {
        "entries": [
            {
                "entry_id": e.entry_id,
                "content": e.content[:300] + "..." if len(e.content) > 300 else e.content,
                "source": e.source,
                "source_id": e.source_id,
                "importance_score": e.importance_score,
                "similarity_score": e.similarity_score,
                "version": e.version,
                "created_at": e.created_at.isoformat() if e.created_at else "",
                "user_id": e.user_id,
            }
            for e in entries
        ],
        "total": len(entries),
    }


@router.delete("/{entry_id}")
async def delete_knowledge(
    entry_id: str,
    user_id: str = Depends(get_current_user),
):
    """删除知识条目"""
    ctx = get_app_context()
    kb = ctx.knowledge_base
    if kb is None:
        raise HTTPException(404, "知识库未启用")

    try:
        await kb.delete(entry_id)
        logger.info("知识条目已删除", extra={"entry_id": entry_id, "user_id": user_id})
        return {"status": "ok", "entry_id": entry_id}
    except Exception as e:
        logger.error("删除知识条目失败", extra={"entry_id": entry_id, "error": str(e)})
        raise HTTPException(500, f"删除失败: {e}")
