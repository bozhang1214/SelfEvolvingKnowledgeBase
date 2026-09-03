"""
聊天会话分享存储层（JSON 文件实现）。

存储聊天会话分享记录：``data/chat_shares.json`` — { share_id: SharedConversation }。

与知识库分享（ShareStorage）的区别：
- 分享内容是创建时的消息快照（内嵌于记录，只读、不可变）
- 不维护访问者侧独立会话（纯只读浏览，不支持在分享内继续对话）

文件 IO 通过 asyncio.to_thread 包装为异步操作；写入采用临时文件 + 原子重命名。
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.models.chat_share import SharedConversation

logger = get_logger(__name__)


class ChatShareStorage:
    """JSON 文件存储的聊天会话分享仓库。"""

    def __init__(self, data_dir: str | Path) -> None:
        self._data_dir = Path(data_dir)
        self._shares_file = self._data_dir / "chat_shares.json"
        self._shares: dict[str, dict[str, Any]] = {}
        self._initialized = False

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        self._data_dir.mkdir(parents=True, exist_ok=True)
        if self._shares_file.exists():
            try:
                with open(self._shares_file, encoding="utf-8") as f:
                    self._shares = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("chat_shares.json 解析失败，重置为空", error=str(e))
                self._shares = {}
        self._initialized = True

    def _save_sync(self) -> None:
        tmp = self._shares_file.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._shares, f, indent=2, default=str, ensure_ascii=False)
        os.replace(tmp, self._shares_file)

    async def _save(self) -> None:
        await asyncio.to_thread(self._save_sync)

    # ============================================================
    # 分享记录 CRUD
    # ============================================================

    async def create_share(
        self,
        owner_user_id: str,
        conv_id: str,
        title: str = "",
        messages: list[dict[str, Any]] | None = None,
    ) -> SharedConversation:
        """创建一条聊天会话分享记录（含消息快照）。"""
        self._ensure_initialized()
        share = SharedConversation(
            owner_user_id=owner_user_id,
            conv_id=conv_id,
            title=title or "对话",
            messages=messages or [],
        )
        self._shares[share.share_id] = share.model_dump(mode="json")
        await self._save()
        logger.info("聊天会话分享已创建", share_id=share.share_id, owner=owner_user_id, conv_id=conv_id)
        return share

    async def get_share(self, share_id: str) -> SharedConversation | None:
        """按令牌获取分享记录。"""
        self._ensure_initialized()
        data = self._shares.get(share_id)
        if data is None:
            return None
        return SharedConversation(**data)

    async def list_by_owner(self, owner_user_id: str) -> list[SharedConversation]:
        """列出指定用户创建的聊天会话分享。"""
        self._ensure_initialized()
        result = [
            SharedConversation(**d)
            for d in self._shares.values()
            if d.get("owner_user_id") == owner_user_id
        ]
        result.sort(key=lambda s: s.created_at, reverse=True)
        return result

    async def delete_share(self, share_id: str) -> bool:
        """撤销分享。"""
        self._ensure_initialized()
        if share_id not in self._shares:
            return False
        del self._shares[share_id]
        await self._save()
        logger.info("聊天会话分享已删除", share_id=share_id)
        return True
