"""反馈数据飞轮：消费 thumbs up/down 反馈，调整知识条目重要性。

闭环：用户点「赞/踩」→ 找到该回答引用的知识条目（``rag_entry_ids``）→
按反馈方向调整 ``importance_score`` → 踩到 0 分则删除该条目。

与 ``profile_service`` 的 ``<PREF>`` 画像闭环区分：此处针对「知识条目质量」，
前者针对「用户画像偏好」。

使用方式：
    from app.services.feedback_service import apply_feedback

    result = await apply_feedback(knowledge_base, rating="thumbs_down",
                                  rag_entry_ids=["e1", "e2"])
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# 赞/踩的重要性调整幅度与删除阈值
_UP_DELTA = 0.1
_DOWN_DELTA = 0.2


async def apply_feedback(
    knowledge_base: Any,
    rating: str,
    rag_entry_ids: list[str],
    comment: str = "",
) -> dict[str, int]:
    """根据反馈调整知识条目重要性。

    Args:
        knowledge_base: L3 知识库（KnowledgeBaseBackend；None 时跳过）
        rating: 反馈类型 ``thumbs_up`` / ``thumbs_down``
        rag_entry_ids: 该回答引用的知识条目 ID 列表
        comment: 可选文字反馈（暂未消费，预留）

    Returns:
        ``{"adjusted": n, "deleted": m}`` 实际调整 / 删除的条目数。
    """
    if knowledge_base is None or not rag_entry_ids:
        return {"adjusted": 0, "deleted": 0}
    if rating not in ("thumbs_up", "thumbs_down"):
        return {"adjusted": 0, "deleted": 0}

    # update_metadata 非抽象接口方法，优先用元数据专用更新（避免重新嵌入向量）
    update_metadata = getattr(knowledge_base, "update_metadata", None)

    adjusted = 0
    deleted = 0
    for eid in rag_entry_ids:
        if not eid:
            continue
        try:
            entry = await knowledge_base.get(eid)
            if entry is None:
                continue
            current = float(getattr(entry, "importance_score", 0.5))
            if rating == "thumbs_up":
                new_score = min(1.0, current + _UP_DELTA)
            else:
                new_score = max(0.0, current - _DOWN_DELTA)

            if new_score <= 0.0:
                await knowledge_base.delete(eid)
                deleted += 1
                logger.info("反馈飞轮：删除低质知识条目", entry_id=eid, rating=rating)
            else:
                if update_metadata is not None:
                    await update_metadata(eid, {"importance_score": new_score})
                else:
                    await knowledge_base.update(eid, metadata_updates={"importance_score": new_score})
                adjusted += 1
        except Exception as e:  # noqa: BLE001 - 单条失败不影响整体反馈
            logger.warning("反馈飞轮：条目调整失败", entry_id=eid, error=str(e))

    return {"adjusted": adjusted, "deleted": deleted}
