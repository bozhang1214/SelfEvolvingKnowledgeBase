"""
知识库分享存储层（JSON 文件实现）。

存储两类数据：
1. 分享记录：``data/shares.json`` — { share_id: SharedKnowledge }
2. 分享会话：``data/shares/{share_id}/{viewer_user_id}.json`` — 消息列表

每个 (share_id, viewer) 维护一个独立会话，消息以追加方式写入。
文件 IO 通过 asyncio.to_thread 包装为异步操作；写入采用临时文件 + 原子重命名。
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.models.share import SharedKnowledge

logger = get_logger(__name__)


class ShareStorage:
    """JSON 文件存储的分享仓库。"""

    def __init__(self, data_dir: str | Path) -> None:
        self._data_dir = Path(data_dir)
        self._shares_file = self._data_dir / "shares.json"
        self._conv_dir = self._data_dir / "shares"
        self._shares: dict[str, dict[str, Any]] = {}
        self._initialized = False

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._conv_dir.mkdir(parents=True, exist_ok=True)
        if self._shares_file.exists():
            try:
                with open(self._shares_file) as f:
                    self._shares = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("shares.json 解析失败，重置为空", error=str(e))
                self._shares = {}
        self._initialized = True

    def _save_sync(self) -> None:
        tmp = self._shares_file.with_suffix(".tmp")
        with open(tmp, "w") as f:
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
        title: str = "",
        category_l1: str = "",
        category_l2: str = "",
        category_l3: str = "",
    ) -> SharedKnowledge:
        """创建一条分享记录（可限定三级分类范围，空表示分享整个知识库）。"""
        self._ensure_initialized()
        share = SharedKnowledge(
            owner_user_id=owner_user_id,
            title=title or "我的知识库",
            category_l1=category_l1 or "",
            category_l2=category_l2 or "",
            category_l3=category_l3 or "",
        )
        self._shares[share.share_id] = share.model_dump(mode="json")
        await self._save()
        logger.info(
            "分享已创建",
            share_id=share.share_id,
            owner=owner_user_id,
            category=share.category_label(),
        )
        return share

    async def get_share(self, share_id: str) -> SharedKnowledge | None:
        self._ensure_initialized()
        data = self._shares.get(share_id)
        if data is None:
            return None
        return SharedKnowledge(**data)

    async def list_by_owner(self, owner_user_id: str) -> list[SharedKnowledge]:
        self._ensure_initialized()
        result = [
            SharedKnowledge(**d)
            for d in self._shares.values()
            if d.get("owner_user_id") == owner_user_id
        ]
        result.sort(key=lambda s: s.created_at, reverse=True)
        return result

    async def delete_share(self, share_id: str) -> bool:
        self._ensure_initialized()
        if share_id not in self._shares:
            return False
        del self._shares[share_id]
        await self._save()
        # 清理会话文件
        conv_dir = self._conv_dir / share_id
        if conv_dir.exists():
            try:
                await asyncio.to_thread(_rmtree, conv_dir)
            except OSError as e:
                logger.warning("清理分享会话目录失败", path=str(conv_dir), error=str(e))
        logger.info("分享已删除", share_id=share_id)
        return True

    async def set_active(self, share_id: str, is_active: bool) -> bool:
        self._ensure_initialized()
        data = self._shares.get(share_id)
        if data is None:
            return False
        data["is_active"] = is_active
        await self._save()
        return True

    # ============================================================
    # 分享会话（每个 share_id + viewer 一条独立会话）
    # ============================================================

    def _conv_file(self, share_id: str, viewer_user_id: str) -> Path:
        sub = self._conv_dir / share_id
        sub.mkdir(parents=True, exist_ok=True)
        # 用 viewer_user_id 作为文件名（user_id 为系统生成，无特殊字符）
        safe = viewer_user_id.replace("/", "_").replace(":", "_")
        return sub / f"{safe}.json"

    async def get_messages(
        self,
        share_id: str,
        viewer_user_id: str,
    ) -> list[dict[str, Any]]:
        """获取分享会话的消息列表。"""
        self._ensure_initialized()
        path = self._conv_file(share_id, viewer_user_id)
        if not path.exists():
            return []
        try:
            data = await asyncio.to_thread(_load_json, path)
            return data.get("messages", []) if data else []
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("读取分享会话失败", path=str(path), error=str(e))
            return []

    async def append_message(
        self,
        share_id: str,
        viewer_user_id: str,
        message: dict[str, Any],
    ) -> None:
        """向分享会话追加一条消息。"""
        self._ensure_initialized()
        path = self._conv_file(share_id, viewer_user_id)
        messages = await self.get_messages(share_id, viewer_user_id)
        messages.append(message)
        payload = {
            "share_id": share_id,
            "viewer_user_id": viewer_user_id,
            "messages": messages,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        await asyncio.to_thread(_save_json_atomic, path, payload)


# ============================================================
# 同步辅助函数（供 to_thread 调用）
# ============================================================

def _load_json(path: Path) -> Any:
    with open(path) as f:
        return json.load(f)


def _save_json_atomic(path: Path, payload: Any) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2, default=str, ensure_ascii=False)
    os.replace(tmp, path)


def _rmtree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)
