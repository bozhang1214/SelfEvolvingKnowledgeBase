"""
本地 JSON 存储实现模块

基于本地 JSON 文件实现 StorageBackend 接口，作为 Phase 1 的默认存储后端。
存储布局：
    data/
    ├── index.json                  # 所有会话的元信息列表
    └── conversations/
        ├── {conv_id_1}.json        # 会话 1 的消息列表
        └── {conv_id_2}.json        # 会话 2 的消息列表

特性：
- 使用 asyncio.to_thread 包装文件 IO，避免阻塞事件循环
- 写入时采用临时文件 + os.replace 原子重命名，保证数据一致性
- 启动时自动创建所需目录
- 所有方法均有详细中文注释

使用方式：
    from app.storage.json_storage import JSONStorage
    storage = JSONStorage()  # 使用默认路径
    conv_id = await storage.create_conversation("default", "测试会话")
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Any

from app.core.exceptions import StorageError
from app.core.logging import get_logger
from app.storage.base import ConversationMeta, MessageRecord, StorageBackend, now_iso

logger = get_logger(__name__)


# ============================================================
# 本地 JSON 存储实现
# ============================================================

class JSONStorage(StorageBackend):
    """
    本地 JSON 文件存储后端。

    会话索引用一个 JSON 文件保存所有会话的元信息列表，
    每个会话的消息单独存储在 conversations 目录下，文件名为 {conv_id}.json。
    所有文件 IO 通过 asyncio.to_thread 包装为异步操作。
    """

    def __init__(
        self,
        index_file: str | Path | None = None,
        conversations_dir: str | Path | None = None,
    ) -> None:
        """
        初始化 JSON 存储。

        Args:
            index_file: 索引文件路径，默认为 data/index.json
            conversations_dir: 会话消息目录，默认为 data/conversations

        Raises:
            StorageError: 创建目录失败
        """
        # 默认路径与 config.yaml 中 storage 配置保持一致
        if index_file is None:
            index_file = "data/index.json"
        if conversations_dir is None:
            conversations_dir = "data/conversations"

        self.index_file: Path = Path(index_file)
        self.conversations_dir: Path = Path(conversations_dir)

        # 写操作互斥锁：保护 index.json 的读-改-写（RMW）临界区，避免并发丢更新（CON-02）
        self._lock = asyncio.Lock()

        # 启动时确保目录存在
        self._ensure_dirs()
        logger.info(
            "JSON 存储初始化完成",
            index_file=str(self.index_file),
            conversations_dir=str(self.conversations_dir),
        )

    # ============ 内部辅助：目录与文件 IO ============

    def _ensure_dirs(self) -> None:
        """创建必要的目录结构（索引父目录 + 会话消息目录）"""
        try:
            self.conversations_dir.mkdir(parents=True, exist_ok=True)
            self.index_file.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise StorageError(f"创建存储目录失败: {e}") from e

    def _read_index_sync(self) -> list[dict]:
        """
        同步读取索引文件。

        Returns:
            会话元信息字典列表；文件不存在时返回空列表

        Raises:
            StorageError: 文件解析失败或读取失败
        """
        if not self.index_file.exists():
            return []
        try:
            with self.index_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                raise StorageError(
                    f"索引文件格式错误：期望 list，得到 {type(data).__name__}"
                )
            return data
        except json.JSONDecodeError as e:
            raise StorageError(f"索引文件 JSON 解析失败: {e}") from e
        except OSError as e:
            raise StorageError(f"读取索引文件失败: {e}") from e

    def _write_index_sync(self, data: list[dict]) -> None:
        """
        同步写入索引文件（原子写入：临时文件 + 重命名）。

        Args:
            data: 会话元信息字典列表

        Raises:
            StorageError: 写入失败
        """
        try:
            # 确保父目录存在
            self.index_file.parent.mkdir(parents=True, exist_ok=True)
            # 写入临时文件后原子重命名
            tmp_path = self.index_file.with_suffix(self.index_file.suffix + ".tmp")
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.index_file)
        except OSError as e:
            raise StorageError(f"写入索引文件失败: {e}") from e

    def _read_messages_sync(self, conv_id: str) -> list[dict]:
        """
        同步读取指定会话的消息列表。

        Args:
            conv_id: 会话 ID

        Returns:
            消息字典列表；文件不存在时返回空列表

        Raises:
            StorageError: 文件解析失败或读取失败
        """
        path = self.conversations_dir / f"{conv_id}.json"
        if not path.exists():
            return []
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                raise StorageError(
                    f"消息文件格式错误：期望 list，得到 {type(data).__name__}"
                )
            return data
        except json.JSONDecodeError as e:
            raise StorageError(f"消息文件 JSON 解析失败: {e}") from e
        except OSError as e:
            raise StorageError(f"读取消息文件失败: {e}") from e

    def _write_messages_sync(self, conv_id: str, data: list[dict]) -> None:
        """
        同步写入指定会话的消息列表（原子写入）。

        Args:
            conv_id: 会话 ID
            data: 消息字典列表

        Raises:
            StorageError: 写入失败
        """
        path = self.conversations_dir / f"{conv_id}.json"
        try:
            self.conversations_dir.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_suffix(path.suffix + ".tmp")
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
        except OSError as e:
            raise StorageError(f"写入消息文件失败: {e}") from e

    def _delete_messages_sync(self, conv_id: str) -> None:
        """
        同步删除指定会话的消息文件。

        Args:
            conv_id: 会话 ID

        Raises:
            StorageError: 删除失败
        """
        path = self.conversations_dir / f"{conv_id}.json"
        if path.exists():
            try:
                path.unlink()
            except OSError as e:
                raise StorageError(f"删除消息文件失败: {e}") from e

    def _find_conv_in_index(self, index: list[dict], conv_id: str) -> dict | None:
        """在索引列表中查找指定会话，返回字典或 None"""
        for item in index:
            if item.get("conv_id") == conv_id:
                return item
        return None

    # ============ 公共 API 实现 ============

    async def create_conversation(self, user_id: str, title: str) -> str:
        """
        创建新会话。

        生成唯一 conv_id，写入索引文件，并初始化空消息文件。
        """
        conv_id = str(uuid.uuid4())
        now = now_iso()
        meta = ConversationMeta(
            conv_id=conv_id,
            user_id=user_id,
            title=title,
            status="active",
            created_at=now,
            updated_at=now,
        )
        try:
            # 读-改-写加锁，避免并发丢更新
            async with self._lock:
                index = await asyncio.to_thread(self._read_index_sync)
                index.append(meta.model_dump())
                await asyncio.to_thread(self._write_index_sync, index)
                # 初始化空消息文件
                await asyncio.to_thread(self._write_messages_sync, conv_id, [])
        except StorageError:
            raise
        except Exception as e:
            raise StorageError(f"创建会话失败: {e}") from e

        logger.info("会话已创建", conv_id=conv_id, user_id=user_id, title=title)
        return conv_id

    async def get_conversation(self, conv_id: str) -> dict | None:
        """
        获取会话详情。

        Returns:
            会话元信息字典；不存在返回 None
        """
        try:
            index = await asyncio.to_thread(self._read_index_sync)
        except StorageError:
            raise
        except Exception as e:
            raise StorageError(f"获取会话失败: {e}") from e

        return self._find_conv_in_index(index, conv_id)

    async def list_conversations(
        self,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """
        列出指定用户的会话（按 updated_at 倒序，分页返回）。
        """
        try:
            index = await asyncio.to_thread(self._read_index_sync)
        except StorageError:
            raise
        except Exception as e:
            raise StorageError(f"列出会话失败: {e}") from e

        # 过滤用户且未删除的会话
        user_convs = [
            item for item in index
            if item.get("user_id") == user_id and item.get("status") != "deleted"
        ]
        # 排序：置顶优先，其次按 updated_at 倒序
        user_convs.sort(
            key=lambda x: (x.get("pinned", False), x.get("updated_at", "")),
            reverse=True,
        )
        # 分页
        return user_convs[offset:offset + limit]

    async def update_conversation(self, conv_id: str, updates: dict) -> None:
        """
        更新会话元信息（合并 updates 字段）。
        """
        try:
            async with self._lock:
                index = await asyncio.to_thread(self._read_index_sync)
                found = False
                for i, item in enumerate(index):
                    if item.get("conv_id") == conv_id:
                        index[i].update(updates)
                        index[i]["updated_at"] = now_iso()
                        found = True
                        break
                if not found:
                    raise StorageError(f"会话不存在: {conv_id}")
                await asyncio.to_thread(self._write_index_sync, index)
        except StorageError:
            raise
        except Exception as e:
            raise StorageError(f"更新会话失败: {e}") from e

    async def delete_conversation(self, conv_id: str) -> None:
        """
        删除会话：索引中标记为 deleted，同时删除消息文件。
        """
        try:
            async with self._lock:
                # 软删除：在索引中标记状态
                index = await asyncio.to_thread(self._read_index_sync)
                found = False
                for i, item in enumerate(index):
                    if item.get("conv_id") == conv_id:
                        index[i]["status"] = "deleted"
                        index[i]["updated_at"] = now_iso()
                        found = True
                        break
                if not found:
                    raise StorageError(f"会话不存在: {conv_id}")
                await asyncio.to_thread(self._write_index_sync, index)
                # 同时物理删除消息文件
                await asyncio.to_thread(self._delete_messages_sync, conv_id)
        except StorageError:
            raise
        except Exception as e:
            raise StorageError(f"删除会话失败: {e}") from e

    async def append_message(self, conv_id: str, message: dict) -> str:
        """
        追加一条消息到会话。

        同时更新会话元信息中的 message_count、token 与成本累计。
        """
        # 补全 msg_id、conv_id、created_at 等字段
        msg_id = message.get("msg_id") or str(uuid.uuid4())
        message["msg_id"] = msg_id
        message.setdefault("conv_id", conv_id)
        message.setdefault("created_at", now_iso())

        try:
            async with self._lock:
                # 校验会话存在
                conv = await self.get_conversation(conv_id)
                if conv is None:
                    raise StorageError(f"会话不存在: {conv_id}")

                # 读取现有消息、追加、写回
                messages = await asyncio.to_thread(self._read_messages_sync, conv_id)
                messages.append(message)
                await asyncio.to_thread(self._write_messages_sync, conv_id, messages)

                # 更新会话元信息中的累计统计
                index = await asyncio.to_thread(self._read_index_sync)
                for i, item in enumerate(index):
                    if item.get("conv_id") == conv_id:
                        index[i]["message_count"] = len(messages)
                        index[i]["total_input_tokens"] = (
                            index[i].get("total_input_tokens", 0)
                            + message.get("tokens_input", 0)
                        )
                        index[i]["total_output_tokens"] = (
                            index[i].get("total_output_tokens", 0)
                            + message.get("tokens_output", 0)
                        )
                        index[i]["total_cost_usd"] = (
                            index[i].get("total_cost_usd", 0.0)
                            + message.get("cost_usd", 0.0)
                        )
                        index[i]["updated_at"] = now_iso()
                        break
                await asyncio.to_thread(self._write_index_sync, index)
        except StorageError:
            raise
        except Exception as e:
            raise StorageError(f"追加消息失败: {e}") from e

        logger.debug(
            "消息已追加",
            conv_id=conv_id,
            msg_id=msg_id,
            role=message.get("role"),
        )
        return msg_id

    async def get_messages(self, conv_id: str, limit: int = 100) -> list[dict]:
        """
        获取会话消息列表（按时间正序，最多返回最后 limit 条）。
        """
        try:
            messages = await asyncio.to_thread(self._read_messages_sync, conv_id)
        except StorageError:
            raise
        except Exception as e:
            raise StorageError(f"获取消息失败: {e}") from e

        # 取最后 limit 条，保持正序
        if limit > 0 and len(messages) > limit:
            messages = messages[-limit:]
        return messages

    async def update_conversation_stats(self, conv_id: str, stats: dict) -> None:
        """
        更新会话统计信息（token 数、成本等）。

        将 stats 中的字段合并到会话元信息中（直接覆盖，不累加）。
        如需累加，调用方应先读取当前值再传入新值。
        """
        try:
            async with self._lock:
                index = await asyncio.to_thread(self._read_index_sync)
                found = False
                for i, item in enumerate(index):
                    if item.get("conv_id") == conv_id:
                        index[i].update(stats)
                        index[i]["updated_at"] = now_iso()
                        found = True
                        break
                if not found:
                    raise StorageError(f"会话不存在: {conv_id}")
                await asyncio.to_thread(self._write_index_sync, index)
        except StorageError:
            raise
        except Exception as e:
            raise StorageError(f"更新统计失败: {e}") from e
