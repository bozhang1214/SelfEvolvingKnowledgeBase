"""设备身份注册表（端云协同 S1，RFC §4.5-H / §7）。

为什么需要它
------------
端侧不是一个"用户"，而是一台**长期在线的设备**：它要能自己刷新凭证、要能被用户在
网页端单独吊销（手机丢了这是唯一止损手段）、要让服务端知道"这次请求来自哪台设备"
（路由统计要按 `(user, device)` 分片，见 §4.5-I）。

存储选型：为什么是**全量 JSON + 原子替换**而不是 JSONL
-----------------------------------------------------
与路由事件（只追加的流水）不同，设备注册表是**可变的小状态**：吊销要改记录、
心跳要更新 `last_seen`。设备数量在个位到几十，所以用「读-改-写 + 原子替换」最简单可靠；
路由流水那种只追加的场景才适合 JSONL（见 `edge_route_storage.py` 的对比说明）。

安全约定：**只存 jti，不存 token 本身**。吊销时把 jti 加进 auth 的吊销黑名单，
token 原文既不入库也不落日志。
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

#: 单用户最多注册多少台设备（防止注册接口被当存储滥用）
MAX_DEVICES_PER_USER = 20


@dataclass
class DeviceRecord:
    """一台已注册设备的状态。"""

    device_id: str
    user_id: str
    name: str = ""
    platform: str = ""                      # android | ios | macos | harmony | cli
    created_at: float = field(default_factory=time.time)
    last_seen_at: float = 0.0
    #: 当前有效 token 的 jti（轮换/吊销时更新）。只存 jti，绝不存 token 原文。
    token_jti: str = ""
    #: 已吊销的 jti（含轮换产生的旧 token），防止旧 token 在过期前仍可用
    revoked_jtis: list[str] = field(default_factory=list)
    revoked: bool = False
    #: 客户端上报的版本戳（§4.5-F）：排查"端侧版本不一致导致的诡异行为"
    app_version: str = ""
    embedding_space: str = ""

    def is_active(self) -> bool:
        return not self.revoked

    def to_public(self) -> dict[str, Any]:
        """给用户看的字段（**不含** jti 等凭据内部状态）。"""
        return {
            "device_id": self.device_id,
            "name": self.name,
            "platform": self.platform,
            "created_at": self.created_at,
            "last_seen_at": self.last_seen_at,
            "revoked": self.revoked,
            "app_version": self.app_version,
            "embedding_space": self.embedding_space,
        }


class DeviceStore:
    """设备注册表（进程内单例；多进程部署需换外部存储，同路由日志的取舍）。

    线程安全用 ``threading.Lock`` 而非 ``asyncio.Lock``：这里的方法是**同步**的
    （调用点在依赖注入里，不希望为此引入 await 传染），而锁只保护毫秒级的
    读-改-写，不会成为瓶颈。
    """

    def __init__(self, path: str | Path = "data/devices.json") -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._cache: dict[str, DeviceRecord] | None = None

    @property
    def path(self) -> Path:
        return self._path

    # ---------- 读写 ----------

    def _load(self) -> dict[str, DeviceRecord]:
        if self._cache is not None:
            return self._cache
        records: dict[str, DeviceRecord] = {}
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8") or "{}")
                for did, item in (raw.get("devices") or {}).items():
                    known = {f for f in DeviceRecord.__dataclass_fields__}
                    records[did] = DeviceRecord(**{k: v for k, v in item.items() if k in known})
            except Exception as e:  # noqa: BLE001 - 注册表损坏不该让服务起不来
                logger.warning("设备注册表读取失败，按空表启动", path=str(self._path),
                               error=str(e)[:160])
        self._cache = records
        return records

    def _save(self, records: dict[str, DeviceRecord]) -> None:
        """原子替换写盘（临时文件 + rename），避免半个文件导致注册表全丢。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "devices": {k: asdict(v) for k, v in records.items()}}
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except Exception:
            Path(tmp).unlink(missing_ok=True)
            raise
        self._cache = records

    # ---------- 业务操作 ----------

    def register(self, user_id: str, *, name: str = "", platform: str = "",
                 token_jti: str = "", app_version: str = "",
                 embedding_space: str = "") -> DeviceRecord:
        """注册一台新设备（超上限时抛 ``ValueError``）。"""
        with self._lock:
            records = self._load()
            mine = [r for r in records.values() if r.user_id == user_id and not r.revoked]
            if len(mine) >= MAX_DEVICES_PER_USER:
                raise ValueError(
                    f"设备数超上限（{MAX_DEVICES_PER_USER}），请先吊销不用的设备")
            rec = DeviceRecord(
                device_id=uuid.uuid4().hex[:16], user_id=user_id, name=name or "未命名设备",
                platform=platform or "unknown", token_jti=token_jti,
                last_seen_at=time.time(), app_version=app_version,
                embedding_space=embedding_space)
            records[rec.device_id] = rec
            self._save(records)
            logger.info("设备已注册", device_id=rec.device_id, platform=rec.platform,
                        user_id=user_id)
            return rec

    def get(self, device_id: str) -> DeviceRecord | None:
        return self._load().get(device_id)

    def list_for_user(self, user_id: str) -> list[DeviceRecord]:
        return sorted((r for r in self._load().values() if r.user_id == user_id),
                      key=lambda r: r.created_at, reverse=True)

    def rotate(self, device_id: str, new_jti: str) -> DeviceRecord | None:
        """把设备的当前 token 换成新的（旧 jti 记入吊销列表）。"""
        with self._lock:
            records = self._load()
            rec = records.get(device_id)
            if rec is None or rec.revoked:
                return None
            if rec.token_jti:
                rec.revoked_jtis.append(rec.token_jti)
            rec.token_jti = new_jti
            rec.last_seen_at = time.time()
            self._save(records)
            logger.info("设备 token 已轮换", device_id=device_id)
            return rec

    def revoke(self, device_id: str) -> DeviceRecord | None:
        """吊销设备（同时把当前 jti 记入吊销列表，供 auth 校验时快速判断）。"""
        with self._lock:
            records = self._load()
            rec = records.get(device_id)
            if rec is None:
                return None
            if rec.token_jti:
                rec.revoked_jtis.append(rec.token_jti)
            rec.token_jti = ""
            rec.revoked = True
            self._save(records)
            logger.warning("设备已吊销", device_id=device_id, platform=rec.platform)
            return rec

    def is_jti_revoked(self, device_id: str, jti: str) -> bool:
        """该 jti 是否已因轮换/吊销失效。"""
        rec = self.get(device_id)
        if rec is None:
            return True                      # 设备记录不存在 → 一律当失效处理
        if rec.revoked:
            return True
        return bool(jti) and (jti in rec.revoked_jtis or jti != rec.token_jti)

    def touch(self, device_id: str, *, app_version: str = "",
              embedding_space: str = "") -> None:
        """更新心跳（``last_seen`` + 可选版本戳）；设备不存在则忽略。"""
        with self._lock:
            records = self._load()
            rec = records.get(device_id)
            if rec is None or rec.revoked:
                return
            rec.last_seen_at = time.time()
            if app_version:
                rec.app_version = app_version
            if embedding_space:
                rec.embedding_space = embedding_space
            self._save(records)
