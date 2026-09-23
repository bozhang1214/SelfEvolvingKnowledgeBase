"""采集源守卫单测：节流闸门 + 熔断/冷却后探测 + 与采集链路的集成。

背景：2026-09-23 猎聘把服务器 IP 封了——多轮「22 关键词 × 翻页」扫描（每关键词
2 次列表请求 + 最多 40 次详情页请求）触发风控；且被拒后剩余关键词仍在继续请求，
把临时标记升级成持续封禁。本文件的用例锁住这两条防线。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from app.agents.job import source_guard as sg
from app.agents.job.source_guard import (
    SourceBlockedError,
    SourceGuard,
    _IntervalGate,
)


@pytest.fixture(autouse=True)
def _fast_intervals(monkeypatch: pytest.MonkeyPatch):
    """把节流间隔压到 0，避免单测真的 sleep。"""
    monkeypatch.setattr(sg, "_BOARD_INTERVAL_S", 0.0)
    monkeypatch.setattr(sg, "_OFFICIAL_INTERVAL_S", 0.0)
    monkeypatch.setattr(sg, "_DETAIL_INTERVAL_S", 0.0)


@pytest.fixture
def guard(tmp_path: Path):
    """独立守卫（临时状态文件）并替换全局单例，用例后还原。"""
    g = SourceGuard(state_file=tmp_path / "guard.json", threshold=2, cooldown_s=60.0)
    sg.set_source_guard(g)
    yield g
    sg.set_source_guard(None)


# ---------------- _IntervalGate ----------------


def test_gate_reserves_distinct_slots():
    """预约式设计：并发调用者拿到**递增**的等待时长，而不是都拿到 0。"""
    gate = _IntervalGate()
    waits = [gate.reserve("s", 1.0) for _ in range(4)]
    assert waits[0] == pytest.approx(0, abs=0.05)
    assert waits[1] > waits[0]
    assert waits[2] > waits[1]
    assert waits[3] > waits[2]


def test_gate_zero_interval_is_noop():
    gate = _IntervalGate()
    assert gate.reserve("s", 0.0) == 0.0
    assert gate.reserve("s", 0.0) == 0.0


def test_gate_keys_are_independent():
    gate = _IntervalGate()
    gate.reserve("a", 5.0)
    assert gate.reserve("b", 5.0) == pytest.approx(0, abs=0.05)


def test_gate_wait_sync_actually_waits():
    gate = _IntervalGate()
    gate.reserve("s", 0.25)  # 占掉第一个时隙
    t0 = time.monotonic()
    gate.wait_sync("s", 0.25)
    assert time.monotonic() - t0 >= 0.2


@pytest.mark.asyncio
async def test_gate_wait_async_actually_waits():
    gate = _IntervalGate()
    gate.reserve("s", 0.25)
    t0 = time.monotonic()
    await gate.wait_async("s", 0.25)
    assert time.monotonic() - t0 >= 0.2


def test_gate_is_thread_safe():
    """多线程并发预约不应拿到重复时隙。"""
    gate = _IntervalGate()
    got: list[float] = []
    lock = threading.Lock()

    def worker() -> None:
        d = gate.reserve("s", 1.0)
        with lock:
            got.append(d)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(got)) == len(got), f"出现重复时隙: {sorted(got)}"


# ---------------- 熔断 ----------------


def test_breaker_closed_initially(guard: SourceGuard):
    assert guard.open_remaining("猎聘") == 0.0


def test_breaker_opens_after_threshold(guard: SourceGuard):
    guard.record_blocked("猎聘", "flag=0")
    assert guard.open_remaining("猎聘") == 0.0, "未达阈值不应熔断"
    guard.record_blocked("猎聘", "flag=0")
    assert guard.open_remaining("猎聘") > 0, "达到阈值应熔断"


def test_breaker_success_resets(guard: SourceGuard):
    guard.record_blocked("猎聘", "flag=0")
    guard.record_blocked("猎聘", "flag=0")
    assert guard.open_remaining("猎聘") > 0
    guard.record_success("猎聘")
    assert guard.open_remaining("猎聘") == 0.0
    assert guard.snapshot().get("猎聘") is None


def test_breaker_cooldown_expires(guard: SourceGuard, tmp_path: Path):
    """冷却期一过就允许**一次探测**（半开），而不是永久失效。"""
    short = SourceGuard(state_file=tmp_path / "g2.json", threshold=1, cooldown_s=0.2)
    short.record_blocked("猎聘", "flag=0")
    assert short.open_remaining("猎聘") > 0
    time.sleep(0.25)
    assert short.open_remaining("猎聘") == 0.0


def test_breaker_state_survives_restart(guard: SourceGuard, tmp_path: Path):
    """状态落盘：容器重启不应重置冷却（否则重启即等于立刻再打一次）。"""
    guard.record_blocked("猎聘", "flag=0")
    guard.record_blocked("猎聘", "flag=0")
    assert guard.open_remaining("猎聘") > 0

    reloaded = SourceGuard(state_file=tmp_path / "guard.json", threshold=2, cooldown_s=60.0)
    assert reloaded.open_remaining("猎聘") > 0

    data = json.loads((tmp_path / "guard.json").read_text(encoding="utf-8"))
    assert data["猎聘"]["failures"] >= 2
    assert "flag=0" in data["猎聘"]["reason"]


def test_breaker_state_file_corrupt_is_tolerated(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    g = SourceGuard(state_file=bad, threshold=1, cooldown_s=1.0)
    assert g.open_remaining("猎聘") == 0.0


def test_interval_for_gives_boards_a_longer_interval(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sg, "_BOARD_INTERVAL_S", 4.0)
    monkeypatch.setattr(sg, "_OFFICIAL_INTERVAL_S", 0.8)
    assert sg.interval_for("猎聘") == 4.0
    assert sg.interval_for("智联招聘") == 4.0
    assert sg.interval_for("BOSS直聘") == 4.0
    assert sg.interval_for("字节") == 0.8


# ---------------- 与采集链路集成 ----------------


class _CountingSource:
    name = "猎聘"

    def __init__(self, exc: Exception | None = None) -> None:
        self.calls = 0
        self._exc = exc

    async def fetch(self, keyword: str, page: int = 0, limit: int = 20) -> list[dict[str, Any]]:
        self.calls += 1
        if self._exc:
            raise self._exc
        return [{"title": f"{keyword}-ok", "job_url": f"u{self.calls}"}]


def _collector(sources: list[Any]):
    from app.agents.job.collector import JobCollector

    c = JobCollector(city="北京", min_salary_k=0)
    c._sources = sources
    return c


@pytest.mark.asyncio
async def test_blocked_source_short_circuits_without_network(guard: SourceGuard):
    """熔断冷却期内：**一次网络调用都不发**（这正是封禁事故的教训）。"""
    src = _CountingSource()
    c = _collector([src])

    guard.record_blocked("猎聘", "flag=0")
    guard.record_blocked("猎聘", "flag=0")
    assert guard.open_remaining("猎聘") > 0

    result = await c.fetch_all(keyword="FDE")
    assert result["count"] == 0
    assert src.calls == 0, "熔断期不应调用源"


@pytest.mark.asyncio
async def test_source_blocked_error_opens_breaker_and_stops_hammering(guard: SourceGuard):
    """源抛 SourceBlockedError → 计数；达阈值后后续关键词不再打它。

    这是把"临时标记"挡在"持续封禁"外面的关键：22 个关键词的扫描里，
    第 2 个关键词被拒之后就熔断，剩下 20 个不会再发请求。
    """
    src = _CountingSource(exc=SourceBlockedError("猎聘", "flag=0"))
    c = _collector([src])

    await c.fetch_all(keyword="k1")
    assert src.calls == 1
    await c.fetch_all(keyword="k2")
    assert src.calls == 2
    # 第 2 次已达阈值（threshold=2）→ 熔断
    assert guard.open_remaining("猎聘") > 0

    for kw in ("k3", "k4", "k5"):
        await c.fetch_all(keyword=kw)
    assert src.calls == 2, f"熔断后不应再打源，实际 {src.calls} 次"


@pytest.mark.asyncio
async def test_success_after_cooldown_recovers(guard: SourceGuard, tmp_path: Path):
    """冷却后的一次探测成功 → 熔断解除，采集恢复。"""
    src = _CountingSource()
    c = _collector([src])

    guard.record_blocked("猎聘", "flag=0")
    guard.record_blocked("猎聘", "flag=0")
    assert guard.open_remaining("猎聘") > 0

    # 让冷却过期
    guard._cooldown_s = 0.0
    guard._state["猎聘"]["open_until"] = time.time() - 1

    result = await c.fetch_all(keyword="FDE")
    assert src.calls == 1
    assert result["count"] == 1
    assert guard.open_remaining("猎聘") == 0.0
    assert guard.snapshot().get("猎聘") is None


@pytest.mark.asyncio
async def test_plain_exception_does_not_open_breaker(guard: SourceGuard):
    """普通异常（网络抖动等）不应触发熔断，否则会误伤正常源。"""
    src = _CountingSource(exc=RuntimeError("connection reset"))
    c = _collector([src])
    for kw in ("k1", "k2", "k3"):
        await c.fetch_all(keyword=kw)
    assert src.calls == 3
    assert guard.open_remaining("猎聘") == 0.0


@pytest.mark.asyncio
async def test_throttle_applied_per_source(guard: SourceGuard, monkeypatch: pytest.MonkeyPatch):
    """节流按源生效：同一源连续两轮之间会申请时隙。"""
    monkeypatch.setattr(sg, "_OFFICIAL_INTERVAL_S", 1.0)
    src = _CountingSource()
    src.name = "某官方站"
    c = _collector([src])

    reserved: list[float] = []
    orig = guard.gate.reserve

    def spy(key: str, min_interval: float) -> float:
        reserved.append(min_interval)
        return orig(key, 0.0)  # 不真的等

    monkeypatch.setattr(guard.gate, "reserve", spy)
    await c.fetch_all(keyword="k1")
    await c.fetch_all(keyword="k2")
    assert reserved == [1.0, 1.0]


# ---------------- fetcher 侧：区分"被拒"与"无结果" ----------------


class _FakeResp:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload

    @property
    def status_code(self) -> int:
        return 200

    @property
    def text(self) -> str:
        return ""


class _FakeCookies(dict):
    def get_dict(self) -> dict[str, str]:
        return dict(self)


class _FakeSession:
    """requests.Session 替身：记录 GET/POST，POST 返回预置 payload。"""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.cookies = _FakeCookies({"XSRF-TOKEN": "t", "acw_tc": "a"})
        self.gets: list[str] = []
        self.posts: list[str] = []

    def get(self, url: str, **kw: Any) -> _FakeResp:
        self.gets.append(url)
        return _FakeResp({})

    def post(self, url: str, **kw: Any) -> _FakeResp:
        self.posts.append(url)
        return _FakeResp(self._payload)


def test_fetch_sync_raises_blocked_on_flag_zero(monkeypatch: pytest.MonkeyPatch):
    """`flag != 1` 是风控裸拒绝，必须抛 SourceBlockedError（而非静默返回空列表）。"""
    from app.agents.job import fetcher

    monkeypatch.setattr(fetcher.requests, "Session", lambda: _FakeSession({"flag": 0}))
    with pytest.raises(SourceBlockedError) as ei:
        fetcher._fetch_sync("FDE", "410", 0, 40)
    assert "flag=0" in ei.value.reason
    assert ei.value.source == "猎聘"


@pytest.mark.asyncio
async def test_liepin_fetch_propagates_blocked(monkeypatch: pytest.MonkeyPatch):
    """LiepinJobFetcher.fetch 的兜底 except 不能把 SourceBlockedError 吞成空列表。"""
    from app.agents.job import fetcher

    def _boom(*a: Any, **k: Any) -> list[dict[str, Any]]:
        raise SourceBlockedError("猎聘", "flag=0")

    monkeypatch.setattr(fetcher, "_fetch_sync", _boom)
    with pytest.raises(SourceBlockedError):
        await fetcher.LiepinJobFetcher().fetch(keyword="FDE")


def test_fetch_jds_caps_detail_requests(guard: SourceGuard, monkeypatch: pytest.MonkeyPatch):
    """详情页是请求量放大器 → 单次调用封顶，超出部分留空按需补。"""
    from app.agents.job import fetcher

    monkeypatch.setattr(fetcher, "_JD_MAX_PER_CALL", 3)
    called: list[str] = []

    def _fake_jd(link: str, cookies: dict[str, str]) -> str:
        called.append(link)
        return f"jd:{link}"

    monkeypatch.setattr(fetcher, "_fetch_jd", _fake_jd)
    jobs = [{"job_url": f"u{i}"} for i in range(10)]
    out = fetcher._fetch_jds(jobs, {})

    assert called == ["u0", "u1", "u2"]
    assert out[0]["jd_text"] == "jd:u0"
    assert "jd_text" not in out[3], "超出封顶的职位不应被抓详情"


@pytest.mark.asyncio
async def test_refresh_job_jd_skipped_during_cooldown(guard: SourceGuard, monkeypatch: pytest.MonkeyPatch):
    """按需刷新 JD 也不能绕过熔断。"""
    from app.agents.job import fetcher

    guard.record_blocked("猎聘", "flag=0")
    guard.record_blocked("猎聘", "flag=0")
    assert guard.open_remaining("猎聘") > 0

    called: list[str] = []
    monkeypatch.setattr(fetcher, "_fetch_jd", lambda link, cookies: called.append(link) or "jd")

    assert await fetcher.refresh_job_jd("https://x/job/1", "猎聘") == ""
    assert called == [], "熔断期不应发起详情页请求"
