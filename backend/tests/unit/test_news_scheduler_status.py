"""定时任务**不再静默失败**：失败重试 + 状态落盘 + 接口可见。

背景（owner 报的 bug）：9/15 的日报任务在 08:00 确实触发了，但抛异常中断，
只在服务端日志里留了一行 APScheduler 的 "raised an exception"，**用户完全看不到**，
只能靠「咦今天怎么没有日报」发现。另外三个任务都没设 `misfire_grace_time`
（默认 1 秒）——容器恰好在触发时刻前后重启，这次运行会被**静默跳过**。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.scheduler.scheduler import NewsScheduler


def _config(tmp_path) -> SimpleNamespace:
    return SimpleNamespace(
        enabled=True,
        report_dir=str(tmp_path / "news"),
        daily_cron="0 8 * * *",
        weekly_cron="0 8 * * 1",
        monthly_cron="0 8 1 * *",
        timezone="Asia/Shanghai",
    )


def _scheduler(tmp_path) -> NewsScheduler:
    s = NewsScheduler(_config(tmp_path), news_agent=SimpleNamespace())
    s.RETRY_DELAY_S = 0  # 测试里不等 60 秒
    return s


# ---------- 状态落盘 ----------


def test_record_status_writes_file(tmp_path) -> None:
    s = _scheduler(tmp_path)
    s._record_status("daily", ok=True, period="2026-09-15", started_at="t0", duration_s=12.3)
    data = json.loads((tmp_path / "news" / "last_status.json").read_text(encoding="utf-8"))
    assert data["kind"] == "daily"
    assert data["ok"] is True
    assert data["period"] == "2026-09-15"
    assert data["duration_s"] == 12.3
    assert data["finished_at"]


def test_record_status_failure_has_error(tmp_path) -> None:
    s = _scheduler(tmp_path)
    s._record_status("weekly", ok=False, error="SSLError: 连接被重置")
    data = json.loads((tmp_path / "news" / "last_status.json").read_text(encoding="utf-8"))
    assert data["ok"] is False
    assert "SSLError" in data["error"]


def test_record_status_survives_unwritable_dir(tmp_path) -> None:
    """落盘失败不能把任务本身搞挂（例如目录权限异常）。"""
    cfg = _config(tmp_path)
    cfg.report_dir = "/proc/不可写/目录"
    s = NewsScheduler(cfg, news_agent=SimpleNamespace())
    s._record_status("daily", ok=True)  # 不应抛异常


# ---------- 失败重试 ----------


@pytest.mark.asyncio
async def test_run_guarded_retries_then_succeeds(tmp_path) -> None:
    """第一次失败、第二次成功 → 最终记为成功（网络抖动场景）。"""
    s = _scheduler(tmp_path)
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("TLS 读超时")
        return {"period": "2026-09-15"}

    await s._run_guarded("daily", flaky)
    data = json.loads((tmp_path / "news" / "last_status.json").read_text(encoding="utf-8"))
    assert calls["n"] == 2, "应当重试一次"
    assert data["ok"] is True
    assert data["period"] == "2026-09-15"


@pytest.mark.asyncio
async def test_run_guarded_records_failure_after_retries(tmp_path) -> None:
    """重试用尽仍失败 → 状态记为失败并带原因（这就是「今天没日报」的可见化）。"""
    s = _scheduler(tmp_path)

    async def always_fail():
        raise RuntimeError("LLM 调用失败: Request timed out.")

    await s._run_guarded("weekly", always_fail)
    data = json.loads((tmp_path / "news" / "last_status.json").read_text(encoding="utf-8"))
    assert data["ok"] is False
    assert "timed out" in data["error"]


# ---------- misfire 宽限 ----------


def test_job_kwargs_relax_misfire_grace(tmp_path) -> None:
    """默认 misfire_grace_time 只有 1 秒：容器在触发时刻重启就会静默跳过本次运行。"""
    kw = _scheduler(tmp_path)._job_kwargs()
    assert kw["misfire_grace_time"] == NewsScheduler.MISFIRE_GRACE_S
    assert kw["misfire_grace_time"] >= 600
    assert kw["coalesce"] is True
    assert kw["max_instances"] == 1


# ---------- 注册接线（回归：lambda 包协程 → 任务空转） ----------


@pytest.mark.asyncio
async def test_registered_jobs_are_coroutine_functions(tmp_path) -> None:
    """回归：`add_job(lambda: self._run_guarded(...))` 只会**创建**协程而不 await。

    症状极隐蔽：APScheduler 照报 "executed successfully"，任务体却从不执行，服务端
    日志里只有一行 `RuntimeWarning: coroutine ... was never awaited`。2026-09-15 那次
    「重试 + 状态落盘 + /status」改造正是这么写的 —— 结果 09-17、09-18 连续两天没有
    日报，而写在 `_run_guarded` 里的状态落盘与飞书告警也一并被绕过（所以「不再静默
    失败」的机制自己静默失败了）。

    这里断言注册进调度器的可调用对象**本身是协程函数**：复核上面那条改动的修复，
    一旦有人换回同步 lambda 包装，本用例立刻失败。
    """
    import inspect

    class _FakeAgent:
        """真实 NewsAgent 的两个入口（注册时 partial 会立即取属性，故必须存在）。"""

        async def refresh(self, force: bool = False) -> dict:
            return {}

        async def generate_periodic(self, kind: str) -> dict:
            return {}

    s = NewsScheduler(_config(tmp_path), _FakeAgent())
    s.start()
    try:
        jobs = {job.id: job for job in s._scheduler.get_jobs()}
        assert set(jobs) == {"news_daily", "news_weekly", "news_monthly"}
        for job in jobs.values():
            assert inspect.iscoroutinefunction(job.func), (
                f"{job.id} 注册的不是协程函数（{job.func!r}）："
                "APScheduler 不会 await 它，任务会空转"
            )
    finally:
        s.shutdown()


# ---------- 接口不被 catch-all 吃掉 ----------


def test_status_route_declared_before_periodic_catch_all() -> None:
    """`/status` 必须在 `/{report_type}` **之前**声明，否则会被后者匹配成 report_type。"""
    from app.api.routes.news import router

    get_paths = [
        getattr(r, "path", "") for r in router.routes if "GET" in getattr(r, "methods", set())
    ]
    assert "/api/v1/news/status" in get_paths
    assert get_paths.index("/api/v1/news/status") < get_paths.index("/api/v1/news/{report_type}")


def test_agent_read_status_missing_file(tmp_path) -> None:
    """没有状态文件时返回 None（首次运行/旧版本），不能报错。"""
    from app.agents.news.service import NewsAgent

    agent = NewsAgent.__new__(NewsAgent)  # 不跑 __init__（避免拉起 fetcher）
    agent._config = _config(tmp_path)
    assert agent.read_status() is None


def test_agent_read_status_parses(tmp_path) -> None:
    from app.agents.news.service import NewsAgent

    d = tmp_path / "news"
    d.mkdir(parents=True)
    (d / "last_status.json").write_text('{"ok": false, "error": "boom"}', encoding="utf-8")
    agent = NewsAgent.__new__(NewsAgent)
    agent._config = _config(tmp_path)
    assert agent.read_status() == {"ok": False, "error": "boom"}


def test_agent_read_status_corrupt_file(tmp_path) -> None:
    from app.agents.news.service import NewsAgent

    d = tmp_path / "news"
    d.mkdir(parents=True)
    (d / "last_status.json").write_text("{坏 JSON", encoding="utf-8")
    agent = NewsAgent.__new__(NewsAgent)
    agent._config = _config(tmp_path)
    assert agent.read_status() is None


# ---------- 启动补跑：错过的计划不再永久丢失 ----------


class _FakeAgent:
    """记录调用、可控「哪份报告已存在」的假 Agent。"""

    def __init__(self, *, daily_exists: bool = False, missing: set[str] | None = None) -> None:
        self.daily_exists = daily_exists
        self.missing = missing if missing is not None else {"weekly", "monthly"}
        self.calls: list[str] = []
        self.labels = {"daily": "2026-09-15", "weekly": "2026-09-07", "monthly": "2026-08"}

    def expected_period(self, kind: str) -> str:
        return self.labels[kind]

    def read_report(self, day: str):  # noqa: ANN201
        return {"md": "x"} if self.daily_exists else None

    def read_periodic(self, kind: str, period: str):  # noqa: ANN201
        return None if kind in self.missing else {"md": "x"}

    async def refresh(self, force: bool = False):  # noqa: ANN201
        self.calls.append("daily")
        return {"period": self.labels["daily"]}

    async def generate_periodic(self, kind: str, period: str | None = None):  # noqa: ANN201
        self.calls.append(kind)
        return {"period": self.labels[kind]}


def _scheduler_with(tmp_path, agent, cron_daily: str = "0 8 * * *") -> NewsScheduler:
    cfg = _config(tmp_path)
    cfg.daily_cron = cron_daily
    s = NewsScheduler(cfg, news_agent=agent)
    s.RETRY_DELAY_S = 0
    return s


def test_daily_time_passed(tmp_path) -> None:
    """日报必须过了当天计划时间才补：否则凌晨会生成一份几乎只有昨天内容的「今天的日报」。"""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Asia/Shanghai")
    s = _scheduler_with(tmp_path, _FakeAgent())
    assert s._daily_time_passed(datetime(2026, 9, 15, 9, 0, tzinfo=tz)) is True
    assert s._daily_time_passed(datetime(2026, 9, 15, 7, 59, tzinfo=tz)) is False
    assert s._daily_time_passed(datetime(2026, 9, 15, 8, 0, tzinfo=tz)) is True


def test_daily_time_passed_skips_complex_cron(tmp_path) -> None:
    """带星期/日期限制的表达式不猜（宁可不补，也不要补错时间）。"""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    s = _scheduler_with(tmp_path, _FakeAgent(), cron_daily="0 8 * * 1")
    assert s._daily_time_passed(datetime(2026, 9, 15, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))) is False


@pytest.mark.asyncio
async def test_catch_up_generates_missing_reports(tmp_path, monkeypatch) -> None:
    agent = _FakeAgent(daily_exists=False, missing={"weekly", "monthly"})
    s = _scheduler_with(tmp_path, agent)
    monkeypatch.setattr(s, "_daily_time_passed", lambda now=None: True)

    ran = await s.catch_up_missing()
    assert ran == ["daily", "weekly", "monthly"]
    assert agent.calls == ["daily", "weekly", "monthly"]


@pytest.mark.asyncio
async def test_catch_up_skips_existing_reports(tmp_path, monkeypatch) -> None:
    """已经有的不重跑（补跑必须幂等，否则每次重启都会白烧一遍 LLM）。"""
    agent = _FakeAgent(daily_exists=True, missing=set())
    s = _scheduler_with(tmp_path, agent)
    monkeypatch.setattr(s, "_daily_time_passed", lambda now=None: True)

    assert await s.catch_up_missing() == []
    assert agent.calls == []


@pytest.mark.asyncio
async def test_catch_up_before_daily_time_only_periodic(tmp_path, monkeypatch) -> None:
    """还没到日报时间点：只补周期报告，不补日报。"""
    agent = _FakeAgent(daily_exists=False, missing={"weekly"})
    s = _scheduler_with(tmp_path, agent)
    monkeypatch.setattr(s, "_daily_time_passed", lambda now=None: False)

    assert await s.catch_up_missing() == ["weekly"]
    assert agent.calls == ["weekly"]


@pytest.mark.asyncio
async def test_catch_up_disabled(tmp_path, monkeypatch) -> None:
    agent = _FakeAgent()
    s = _scheduler_with(tmp_path, agent)
    s._config.enabled = False
    monkeypatch.setattr(s, "_daily_time_passed", lambda now=None: True)
    assert await s.catch_up_missing() == []
    assert agent.calls == []


@pytest.mark.asyncio
async def test_catch_up_failure_is_recorded_not_raised(tmp_path, monkeypatch) -> None:
    """补跑失败：记状态（进而推告警），不把启动流程搞挂。"""
    agent = _FakeAgent(daily_exists=False, missing=set())

    async def boom(force: bool = False):  # noqa: ANN201
        raise RuntimeError("SSLError: 连接被重置")

    agent.refresh = boom  # type: ignore[method-assign]
    s = _scheduler_with(tmp_path, agent)
    monkeypatch.setattr(s, "_daily_time_passed", lambda now=None: True)

    assert await s.catch_up_missing() == ["daily"]
    data = json.loads((tmp_path / "news" / "last_status.json").read_text(encoding="utf-8"))
    assert data["ok"] is False and "SSLError" in data["error"]


def test_expected_period_labels(tmp_path) -> None:
    """`expected_period` 决定「该不该有一份报告」，标签错就会一直补跑或一直漏。"""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from app.agents.news.service import NewsAgent

    agent = NewsAgent.__new__(NewsAgent)
    agent._config = _config(tmp_path)
    agent._config.timezone = "Asia/Shanghai"
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    assert agent.expected_period("daily") == today.isoformat()
    # 周报=本周一 - 7 天（上一整周）
    assert agent.expected_period("weekly") == (
        today - timedelta(days=today.weekday() + 7)
    ).isoformat()
    # 月报=上个月
    assert agent.expected_period("monthly") == (
        today.replace(day=1) - timedelta(days=1)
    ).strftime("%Y-%m")

