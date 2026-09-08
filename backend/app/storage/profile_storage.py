"""
用户画像存储层（JSON，按 user_id 隔离）。

一个用户一个文件：``data/profile/{user_id}.json``。
写入用临时文件 + 原子重命名，避免并发写损坏。文件 IO 用 asyncio.to_thread 包装。
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.models.profile import UserProfile

logger = get_logger(__name__)


class ProfileStorage:
    """按 user_id 隔离的用户画像 JSON 仓库。"""

    def __init__(self, data_dir: str | Path = "data/profile") -> None:
        self._dir = Path(data_dir)

    def _path(self, user_id: str) -> Path:
        # user_id 为服务端生成的 user_xxx，不含路径分隔符；仍做一次安全兜底
        safe = user_id.replace("/", "_").replace("\\", "_")
        return self._dir / f"{safe}.json"

    def get_sync(self, user_id: str) -> UserProfile | None:
        path = self._path(user_id)
        if not path.exists():
            return None
        try:
            return UserProfile(**json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError, Exception) as e:
            logger.warning("读取用户画像失败", user_id=user_id, error=str(e))
            return None

    def save_sync(self, profile: UserProfile) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._path(profile.user_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(profile.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, path)

    async def get(self, user_id: str) -> UserProfile | None:
        return await asyncio.to_thread(self.get_sync, user_id)

    async def save(self, profile: UserProfile) -> None:
        await asyncio.to_thread(self.save_sync, profile)

    async def upsert_update(self, user_id: str, patch: dict[str, Any]) -> UserProfile:
        """读取现有画像，合并 patch（不覆盖未传字段），保存并返回。"""
        from datetime import datetime, timezone

        current = await self.get(user_id) or UserProfile(user_id=user_id)
        data = current.model_dump()
        # 深合并 job_preferences 等嵌套字段
        for key, value in patch.items():
            if value is None:
                continue
            if isinstance(value, dict) and isinstance(data.get(key), dict):
                data[key] = {**data[key], **value}
            else:
                data[key] = value
        data["updated_at"] = datetime.now(timezone.utc)
        merged = UserProfile(**data)
        await self.save(merged)
        return merged
