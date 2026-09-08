"""
用户画像 API 路由（多功能联动地基）。

- ``GET /api/v1/profile``          获取当前用户画像（无则返回默认空画像）
- ``PUT /api/v1/profile``          更新画像（沿字段合并，不覆盖未传字段）

画像供「应聘助手 / 科技资讯助手」skill 与招聘分析、资讯模块联动使用。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.core.auth import get_current_user
from app.core.logging import get_logger
from app.models.profile import ProfileUpdate, UserProfile
from app.storage.profile_storage import ProfileStorage

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/profile", tags=["profile"])

# 模块级存储：画像写入 data/profile/（在 sekb_data 卷内，持久化 + 随备份）
_profile_storage = ProfileStorage("data/profile")


@router.get("")
async def get_profile(
    user_id: str = Depends(get_current_user),
) -> UserProfile:
    """获取当前用户画像；无则返回默认空画像（便于前端回填）。"""
    storage = _profile_storage
    profile = await storage.get(user_id)
    if profile is None:
        profile = UserProfile(user_id=user_id)
    return profile


@router.put("")
async def update_profile(
    body: ProfileUpdate,
    user_id: str = Depends(get_current_user),
) -> UserProfile:
    """更新画像：只合并传入的字段（None 不覆盖），保存并返回。"""
    storage = _profile_storage
    patch: dict[str, Any] = {}
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            patch[field] = value.model_dump() if hasattr(value, "model_dump") else value
    profile = await storage.upsert_update(user_id, patch)
    logger.info("用户画像已更新", user_id=user_id, fields=list(patch.keys()))
    return profile
