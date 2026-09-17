"""端云路由事件存储（JSONL，append-only）。

为什么是 append-only 的 JSONL 而不是又一个 JSON 全量文件
--------------------------------------------------------
路由事件是**只追加、不修改**的流水（这正是 RFC §4.5-A 里"对话消息天然无冲突"的同一性质）。
全量 JSON 读写会在并发下互相覆盖（这个项目已经吃过三套缓存各自为政的亏），
所以这里用「一行一事件」+ 锁 + 按行数裁剪：

* 写入：``asyncio.Lock`` 串行 + 追加，不重写历史；
* 幂等：外部端（Android，M2）上报时可能重传，按 ``event_id`` 去重（§4.5-B）；
* 裁剪：超过 ``MAX_EVENTS`` 行时只保留最新的一段，避免无限增长。

统计口径（§4.4 的两个核心指标）
--------------------------------
* **端侧完成率** = 未被升级的端侧事件 / 需要端侧处理的事件（分母不含"一开始就判给云端"的）
* **升级率**     = 发生升级的事件 / 端侧处理过的事件
这两个数字是端云协同唯一的北极星指标：前者说明端侧够不够用，后者说明判得准不准。
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

#: 最多保留的事件行数（超过则裁剪，只留最新一段）
MAX_EVENTS = 5000


class EdgeRouteStore:
    """路由事件流水（进程内单例使用；多进程部署需换外部存储，见 RFC §11 风险）。"""

    def __init__(self, path: str | Path = "data/edge/routes.jsonl") -> None:
        self._path = Path(path)
        self._lock = asyncio.Lock()
        self._seen: set[str] = set()          # 幂等键（进程内；重启后靠文件里的 id 重建）
        self._seen_loaded = False

    @property
    def path(self) -> Path:
        return self._path

    # ---------- 写 ----------

    async def append(self, event: Any, event_id: str = "") -> bool:
        """追加一条事件。``event_id`` 重复时**不重复写入**（幂等），返回 False。"""
        payload = event.as_dict() if hasattr(event, "as_dict") else dict(event)
        eid = event_id or str(payload.get("event_id") or "")
        if eid:
            payload["event_id"] = eid
            await self._ensure_seen()
            if eid in self._seen:
                return False
        async with self._lock:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                line = json.dumps(payload, ensure_ascii=False)
                with self._path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
                if eid:
                    self._seen.add(eid)
            except OSError as e:
                # 记账失败不能影响主流程（但必须留痕）
                logger.warning("路由事件写入失败（忽略）", error=str(e)[:120])
                return False
        await self._maybe_trim()
        return True

    async def _ensure_seen(self) -> None:
        if self._seen_loaded:
            return
        self._seen_loaded = True
        for row in self._read_all():
            eid = row.get("event_id")
            if eid:
                self._seen.add(str(eid))

    async def _maybe_trim(self) -> None:
        """行数超限时裁剪（只在追加后触发，避免每次读都判）。"""
        try:
            if not self._path.exists():
                return
            with self._path.open("r", encoding="utf-8") as fh:
                lines = fh.readlines()
            if len(lines) <= MAX_EVENTS:
                return
            keep = lines[-MAX_EVENTS:]
            tmp = self._path.with_suffix(".jsonl.tmp")
            tmp.write_text("".join(keep), encoding="utf-8")
            os.replace(tmp, self._path)         # 原子替换，避免裁剪中途崩坏
        except OSError as e:
            logger.warning("路由事件裁剪失败（忽略）", error=str(e)[:120])

    # ---------- 读 ----------

    def _read_all(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        rows: list[dict[str, Any]] = []
        try:
            with self._path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue            # 半行/坏行直接跳过，不让它毒死统计
        except OSError:
            return []
        return rows

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """最近 ``limit`` 条（倒序：最新在前）。"""
        rows = self._read_all()
        return list(reversed(rows[-max(limit, 1):]))

    # ---------- 统计 ----------

    def stats(self) -> dict[str, Any]:
        """端侧完成率 / 升级率 / 分布。字段含义见模块 docstring。"""
        rows = self._read_all()
        by_plane = Counter(str(r.get("plane", "?")) for r in rows)
        by_role = Counter(str(r.get("role", "?")) for r in rows)
        by_reason = Counter(str(r.get("reason", "?")) for r in rows)
        esc_reasons = Counter(
            str(r.get("escalate_reason", "")) for r in rows if r.get("escalated")
        )
        # 端侧处理过的事件 = 决策落端侧的（含最终升级的）
        edge_decided = [r for r in rows if _decided_edge(r)]
        escalated = [r for r in edge_decided if r.get("escalated")]
        done_on_edge = [r for r in edge_decided if not r.get("escalated")]

        def _rate(num: int, den: int) -> float:
            return round(num / den, 4) if den else 0.0

        lat = [float(r.get("latency_ms") or 0) for r in rows if r.get("latency_ms")]
        return {
            "total": len(rows),
            "by_plane": dict(by_plane),
            "by_role": dict(by_role.most_common(20)),
            "by_reason": dict(by_reason.most_common(20)),
            "escalate_reasons": dict(esc_reasons.most_common(20)),
            "edge_decided": len(edge_decided),
            "edge_completed": len(done_on_edge),
            "escalated": len(escalated),
            # 两个北极星指标
            "edge_completion_rate": _rate(len(done_on_edge), len(edge_decided)),
            "escalation_rate": _rate(len(escalated), len(edge_decided)),
            "avg_latency_ms": round(sum(lat) / len(lat), 1) if lat else 0.0,
            "generated_at": time.time(),
        }


def _decided_edge(row: dict[str, Any]) -> bool:
    """判断这条事件"最初是被判给端侧的"。

    升级后 ``plane`` 会被改写成 cloud，所以不能只看 ``plane``——
    否则"升级了很多"会被统计成"端侧完成率很高"（口径反了）。
    约定：升级事件保留原始决策，见 ``RouteEvent.reason``。
    """
    if row.get("escalated"):
        return True
    return row.get("plane") == "edge"
