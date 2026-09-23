"""采集源守卫：节流 + 熔断 + 冷却后探测。

**为什么需要它**（2026-09-23 的真实事故）：为 FDE 全量调研连续跑了多轮「22 关键词 × 翻页」
扫描，把猎聘的服务器 IP 打进了风控黑名单（搜索接口恒返回裸拒绝 `{"flag":0}`，
浏览器打开 302 到验证码页，`euuid` 解出为 `flag10001|ip_ua:<ip>`）。

复盘出的两个结构性原因：

1. **没有节流**：`fetch_all` 逐关键词串行，但**关键词之间没有延迟**，同一关键词内各源并发；
   更严重的是猎聘列表接口**不带 JD**，`fetcher._fetch_jds` 会为每条职位**并发抓详情页**
   （`_JD_CONCURRENCY=6`）——单关键词就是 `2 + 40` 次请求，一轮扫描上千次。
2. **被拒后还在继续打**：猎聘第一次返回 `flag:0` 之后，代码只是记个 warning 返回空列表，
   剩下 21 个关键词**照打不误**——这正是把"临时标记"升级成"持续封禁"的原因。

本模块提供三件事：

- **节流**（:class:`_IntervalGate`）：同一 key 的两次请求之间强制最小间隔。
  采用「**先占时隙再 sleep**」的预约式设计，因此 sync / async 两条路径可以共用同一份状态，
  并发调用者会自然排队，且 sleep 期间不持有锁。
- **熔断**（:class:`SourceGuard`）：某个源被明确拒绝达到阈值后，**打开熔断**，
  冷却期内所有调用**直接短路返回、不发任何网络请求**。
- **冷却后探测（半开）**：冷却期一过，放**一次**探测请求过去；成功则复位，失败则重新打开。
  这样风控解除后能自动恢复，而不是永久失效。

状态持久化在 ``data/source_guard.json``——**容器重启不会重置冷却**，
否则重启即等于立刻又去打一次，正是要避免的行为。

运维旋钮走环境变量（部署相关，不宜写死在 config.yaml）：

===========================  ==========================  ============
环境变量                      含义                        默认
===========================  ==========================  ============
``SEKB_JOB_BOARD_INTERVAL_S``  通用招聘平台最小请求间隔    4.0
``SEKB_JOB_OFFICIAL_INTERVAL_S``  大厂官方站最小请求间隔   0.8
``SEKB_JOB_DETAIL_INTERVAL_S``  详情页最小请求间隔         1.0
``SEKB_JOB_BREAKER_THRESHOLD``  连续被拒几次后熔断         2
``SEKB_JOB_BREAKER_COOLDOWN_S`` 熔断冷却时长（秒）         1800
===========================  ==========================  ============
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# 通用招聘平台：风控最严，间隔给最大（猎聘/BOSS/智联）
BOARD_SOURCES = frozenset({"猎聘", "BOSS直聘", "智联招聘"})

_BOARD_INTERVAL_S = float(os.getenv("SEKB_JOB_BOARD_INTERVAL_S", "4.0"))
_OFFICIAL_INTERVAL_S = float(os.getenv("SEKB_JOB_OFFICIAL_INTERVAL_S", "0.8"))
_DETAIL_INTERVAL_S = float(os.getenv("SEKB_JOB_DETAIL_INTERVAL_S", "1.0"))
_BREAKER_THRESHOLD = int(os.getenv("SEKB_JOB_BREAKER_THRESHOLD", "2"))
_BREAKER_COOLDOWN_S = float(os.getenv("SEKB_JOB_BREAKER_COOLDOWN_S", "1800"))

_STATE_FILE = Path("data/source_guard.json")


class SourceBlockedError(RuntimeError):
    """采集源**明确拒绝**了我们（风控 / 验证码），而不是"这次没有结果"。

    必须与"正常返回空列表"区分开：前者要触发熔断，后者是正常业务状态。
    各源在拿到判定性证据时抛这个异常（例如猎聘搜索接口 `flag != 1`）。
    """

    def __init__(self, source: str, reason: str = "") -> None:
        self.source = source
        self.reason = reason
        super().__init__(f"{source} 被风控拦截: {reason}" if reason else f"{source} 被风控拦截")


def interval_for(source: str) -> float:
    """该源的搜索请求最小间隔（秒）。"""
    return _BOARD_INTERVAL_S if source in BOARD_SOURCES else _OFFICIAL_INTERVAL_S


class _IntervalGate:
    """预约式最小间隔闸门（sync / async 共用一份状态）。

    ``reserve`` 会**立刻占用**下一个可用时隙并返回还需等待的秒数，
    调用方自己 sleep——因此多个调用者会各拿到不同的时隙而不会互相踩，
    且 sleep 期间不持锁。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_at: dict[str, float] = {}

    def reserve(self, key: str, min_interval: float) -> float:
        """占用下一次时隙，返回需要等待的秒数（>=0）。"""
        if min_interval <= 0:
            return 0.0
        now = time.monotonic()
        with self._lock:
            start = max(now, self._next_at.get(key, 0.0))
            self._next_at[key] = start + min_interval
            return max(0.0, start - now)

    async def wait_async(self, key: str, min_interval: float) -> float:
        delay = self.reserve(key, min_interval)
        if delay > 0:
            await asyncio.sleep(delay)
        return delay

    def wait_sync(self, key: str, min_interval: float) -> float:
        delay = self.reserve(key, min_interval)
        if delay > 0:
            time.sleep(delay)
        return delay

    def reset(self) -> None:
        with self._lock:
            self._next_at.clear()


class SourceGuard:
    """按源维护节流闸门与熔断状态。"""

    def __init__(
        self,
        state_file: Path | None = None,
        threshold: int = _BREAKER_THRESHOLD,
        cooldown_s: float = _BREAKER_COOLDOWN_S,
        gate: _IntervalGate | None = None,
    ) -> None:
        self._file = state_file if state_file is not None else _STATE_FILE
        self._threshold = threshold
        self._cooldown_s = cooldown_s
        self.gate = gate or _IntervalGate()
        self._lock = threading.Lock()
        self._state: dict[str, dict[str, Any]] = self._load()

    # ---------- 持久化 ----------

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self._file.exists():
            return {}
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("采集源守卫状态读取失败，按空处理", error=str(e)[:120])
            return {}

    def _save(self) -> None:
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._file.with_name(self._file.name + ".tmp")
            tmp.write_text(
                json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            tmp.replace(self._file)
        except OSError as e:  # 落盘失败不应影响采集
            logger.warning("采集源守卫状态写入失败", error=str(e)[:120])

    # ---------- 熔断 ----------

    def open_remaining(self, source: str) -> float:
        """熔断剩余秒数；0 表示未熔断（允许调用，含冷却后的探测）。"""
        with self._lock:
            entry = self._state.get(source) or {}
            until = float(entry.get("open_until", 0.0) or 0.0)
        return max(0.0, until - time.time())

    def record_blocked(self, source: str, reason: str = "") -> None:
        """记录一次"被明确拒绝"，达到阈值就打开熔断（或续期）。"""
        with self._lock:
            entry = dict(self._state.get(source) or {})
            failures = int(entry.get("failures", 0)) + 1
            entry.update(
                failures=failures,
                reason=reason[:200],
                updated_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            )
            if failures >= self._threshold:
                entry["open_until"] = time.time() + self._cooldown_s
                logger.warning(
                    "采集源已熔断",
                    source=source,
                    failures=failures,
                    cooldown_s=self._cooldown_s,
                    reason=reason[:120],
                )
            self._state[source] = entry
        self._save()

    def record_success(self, source: str) -> None:
        """一次成功即复位熔断与计数（冷却后的探测成功 = 风控已解除）。"""
        with self._lock:
            entry = self._state.get(source) or {}
            if not entry:
                return
            was_open = float(entry.get("open_until", 0.0) or 0.0) > 0
            self._state.pop(source, None)
        if was_open:
            logger.info("采集源熔断已解除", source=source)
        self._save()

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """当前状态快照（供排查/展示）。"""
        with self._lock:
            return {k: dict(v) for k, v in self._state.items()}

    # ---------- 对外入口 ----------

    async def wait_source(self, source: str) -> None:
        """按源的档位节流（调用前 await）。"""
        await self.gate.wait_async(source, interval_for(source))

    def wait_detail(self, source: str) -> None:
        """详情页节流（同步路径调用，线程池里用）。"""
        self.gate.wait_sync(f"{source}:detail", _DETAIL_INTERVAL_S)

    def reset(self) -> None:
        with self._lock:
            self._state.clear()
        self.gate.reset()


_GUARD: SourceGuard | None = None
_GUARD_LOCK = threading.Lock()


def get_source_guard() -> SourceGuard:
    """进程内单例：**节流状态必须跨 collector 实例共享**，否则多实例各自计时等于没节流。"""
    global _GUARD
    if _GUARD is None:
        with _GUARD_LOCK:
            if _GUARD is None:
                _GUARD = SourceGuard()
    return _GUARD


def set_source_guard(guard: SourceGuard | None) -> None:
    """替换单例（测试用）。"""
    global _GUARD
    with _GUARD_LOCK:
        _GUARD = guard
