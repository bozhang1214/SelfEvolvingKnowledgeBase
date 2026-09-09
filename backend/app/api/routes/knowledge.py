"""
知识库管理 API 路由（Phase 3）

提供知识库条目的查询、搜索、删除，以及多级分类管理：
- ``GET    /api/v1/knowledge``                    获取知识条目列表（支持分类过滤）
- ``GET    /api/v1/knowledge/search``             搜索知识条目
- ``DELETE /api/v1/knowledge/{entry_id}``          删除指定知识条目
- ``GET    /api/v1/knowledge/categories``         获取多级分类目录树
- ``GET    /api/v1/knowledge/categories/stats``   获取分类条目统计
- ``PATCH  /api/v1/knowledge/{entry_id}/category`` 手动修正条目分类
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.access import require_full_access
from app.core.auth import get_current_user
from app.core.bootstrap import get_app_context
from app.core.categories import DEFAULT_CATEGORY_TREE, to_tree_response
from app.core.logging import get_logger
from app.core.utils import fmt_dt as _fmt_dt

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"],
    dependencies=[Depends(require_full_access)],
)


def _entry_to_dict(e: Any, with_similarity: bool = False) -> dict[str, Any]:
    """将 KnowledgeEntry 转换为 API 响应字典（含分类字段）。"""
    item: dict[str, Any] = {
        "entry_id": e.entry_id,
        "content": e.content[:200] + "..." if len(e.content) > 200 else e.content,
        "source": e.source,
        "source_id": e.source_id,
        "importance_score": e.importance_score,
        "version": e.version,
        "created_at": _fmt_dt(e.created_at),
        "user_id": e.user_id,
        "category_l1": getattr(e, "category_l1", "其他"),
        "category_l2": getattr(e, "category_l2", "待分类"),
        "category_l3": getattr(e, "category_l3", "未分类"),
        "category_confidence": getattr(e, "category_confidence", 0.0),
        "category_source": getattr(e, "category_source", "auto"),
    }
    if with_similarity:
        item["similarity_score"] = getattr(e, "similarity_score", 0.0)
    return item


@router.get("/categories")
async def list_categories() -> dict[str, Any]:
    """获取多级分类目录树（前端用于渲染目录树与分类选择器）。"""
    return {"tree": to_tree_response(), "raw": DEFAULT_CATEGORY_TREE}


@router.get("/categories/stats")
async def category_stats(
    user_id: str = Depends(get_current_user),
) -> dict[str, Any]:
    """获取当前用户各分类的条目数量统计。"""
    ctx = get_app_context()
    kb = ctx.knowledge_base
    if kb is None:
        return {"stats": []}

    try:
        # 分页取全量：按批次拉取直到不足一页，避免硬编码上限导致统计失真
        batch = 500
        entries: list[Any] = []
        offset = 0
        while True:
            # 仅加载元数据（不加载全文），避免大库统计分类时把每篇文档文本都读出来，导致超时
            chunk = await kb.list_entries(
                user_id=user_id, limit=batch, offset=offset, include=["metadatas"]
            )
            entries.extend(chunk)
            if len(chunk) < batch:
                break
            offset += batch
    except Exception as e:
        logger.error("分类统计查询失败", error=str(e))
        return {"stats": []}

    # 按 l1/l2/l3 聚合计数
    counter: dict[tuple[str, str, str], int] = {}
    for e in entries:
        key = (
            getattr(e, "category_l1", "其他"),
            getattr(e, "category_l2", "待分类"),
            getattr(e, "category_l3", "未分类"),
        )
        counter[key] = counter.get(key, 0) + 1

    stats = [
        {"category_l1": k[0], "category_l2": k[1], "category_l3": k[2], "count": v}
        for k, v in sorted(counter.items())
    ]
    return {"stats": stats, "total": len(entries)}


@router.get("")
async def list_knowledge(
    user_id: str = Depends(get_current_user),
    source: str | None = Query(None, description="按来源筛选"),
    category_l1: str | None = Query(None, description="按一级分类筛选"),
    category_l2: str | None = Query(None, description="按二级分类筛选"),
    category_l3: str | None = Query(None, description="按三级分类筛选"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """获取知识条目列表（支持来源与多级分类过滤）。"""
    ctx = get_app_context()
    kb = ctx.knowledge_base
    if kb is None:
        return {"entries": [], "total": 0, "page": page, "page_size": page_size}

    try:
        # 用 offset/limit 正确分页，total 通过 count_entries 取真实总数
        offset = (page - 1) * page_size
        entries = await kb.list_entries(
            user_id=user_id,
            source=source,
            category_l1=category_l1,
            category_l2=category_l2,
            category_l3=category_l3,
            limit=page_size,
            offset=offset,
        )
        total = await kb.count_entries(
            user_id=user_id,
            source=source,
            category_l1=category_l1,
            category_l2=category_l2,
            category_l3=category_l3,
        )
    except Exception as e:
        logger.error("获取知识列表失败", error=str(e))
        entries = []
        total = 0

    return {
        "entries": [_entry_to_dict(e) for e in entries],
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
        logger.error("知识搜索失败", error=str(e))
        entries = []

    return {
        "entries": [_entry_to_dict(e, with_similarity=True) for e in entries],
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
        existing = await kb.get(entry_id)
    except Exception as e:
        logger.error("查询条目失败", entry_id=entry_id, error=str(e))
        raise HTTPException(500, f"查询条目失败: {e}")

    if existing is None:
        raise HTTPException(404, "条目不存在")

    # 权限校验：仅条目所有者可删除
    if getattr(existing, "user_id", None) != user_id:
        raise HTTPException(404, "条目不存在或无权操作")

    try:
        await kb.delete(entry_id)
        logger.info("知识条目已删除", entry_id=entry_id, user_id=user_id)
        return {"status": "ok", "entry_id": entry_id}
    except Exception as e:
        logger.error("删除知识条目失败", entry_id=entry_id, error=str(e))
        raise HTTPException(500, f"删除失败: {e}")


# ============================================================
# 手动重分类
# ============================================================

class ReclassifyRequest(BaseModel):
    """手动修正条目分类请求。"""

    category_l1: str = Field(..., description="一级分类大类")
    category_l2: str = Field(..., description="二级分类子类")
    category_l3: str = Field(..., description="三级分类细类")


@router.patch("/{entry_id}/category")
async def reclassify_entry(
    entry_id: str,
    request: ReclassifyRequest = Body(...),
    user_id: str = Depends(get_current_user),
):
    """手动修正知识条目的三级分类。"""
    from app.core.categories import validate_category

    ctx = get_app_context()
    kb = ctx.knowledge_base
    if kb is None:
        raise HTTPException(404, "知识库未启用")

    # 校验分类合法性
    if not validate_category(request.category_l1, request.category_l2, request.category_l3):
        raise HTTPException(400, "分类不在合法目录中")

    try:
        existing = await kb.get(entry_id)
    except Exception as e:
        logger.error("查询条目失败", entry_id=entry_id, error=str(e))
        raise HTTPException(500, f"查询条目失败: {e}")

    if existing is None:
        raise HTTPException(404, "条目不存在")

    if getattr(existing, "user_id", None) != user_id:
        raise HTTPException(404, "条目不存在或无权操作")

    # 直接调用抽象接口的 update（已声明于 KnowledgeBaseBackend）
    try:
        await kb.update(
            entry_id,
            metadata_updates={
                "category_l1": request.category_l1,
                "category_l2": request.category_l2,
                "category_l3": request.category_l3,
                "category_confidence": 1.0,
                "category_source": "manual",
            },
        )
        logger.info(
            "条目分类已手动修正",
            entry_id=entry_id,
            l1=request.category_l1,
            l2=request.category_l2,
            l3=request.category_l3,
        )
    except Exception as e:
        logger.error("更新条目分类失败", entry_id=entry_id, error=str(e))
        raise HTTPException(500, f"更新分类失败: {e}")

    return {
        "status": "ok",
        "entry_id": entry_id,
        "category_l1": request.category_l1,
        "category_l2": request.category_l2,
        "category_l3": request.category_l3,
        "category_source": "manual",
    }
