"""
用户存储层（JSON 文件实现，Phase 3 暂用）
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.models.user import User, UserPublic


class UserStorage:
    """JSON 文件存储的用户仓库"""

    def __init__(self, data_dir: str | Path) -> None:
        self._data_dir = Path(data_dir)
        self._users_file = self._data_dir / "users.json"
        self._users: dict[str, dict[str, Any]] = {}
        self._initialized = False

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        self._data_dir.mkdir(parents=True, exist_ok=True)
        if self._users_file.exists():
            with open(self._users_file) as f:
                self._users = json.load(f)
        self._initialized = True

    def _save(self) -> None:
        # 原子写：先写临时文件再 os.replace，避免崩溃截断导致用户表损坏（全站无法登录）
        tmp = self._users_file.with_name(self._users_file.name + ".tmp")
        with open(tmp, "w") as f:
            json.dump(self._users, f, indent=2, default=str)
        tmp.replace(self._users_file)

    def find_by_email(self, email: str) -> User | None:
        self._ensure_initialized()
        for uid, data in self._users.items():
            if data.get("email") == email:
                return User(**data)
        return None

    def find_by_id(self, user_id: str) -> User | None:
        self._ensure_initialized()
        data = self._users.get(user_id)
        if data is None:
            return None
        return User(**data)

    def create(self, user: User) -> User:
        self._ensure_initialized()
        if user.user_id in self._users:
            raise ValueError(f"用户已存在: {user.user_id}")
        self._users[user.user_id] = user.model_dump(mode="json")
        self._save()
        return user

    def update(self, user_id: str, updates: dict[str, Any]) -> User | None:
        self._ensure_initialized()
        data = self._users.get(user_id)
        if data is None:
            return None
        data.update(updates)
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._users[user_id] = data
        self._save()
        return User(**data)

    def delete(self, user_id: str) -> bool:
        self._ensure_initialized()
        if user_id not in self._users:
            return False
        del self._users[user_id]
        self._save()
        return True

    def to_public(self, user: User) -> UserPublic:
        """转换为不含密码的公开信息。"""
        return UserPublic(
            user_id=user.user_id,
            email=user.email,
            name=user.name,
            avatar_url=user.avatar_url,
            created_at=user.created_at,
            is_active=user.is_active,
            settings=user.settings,
        )
