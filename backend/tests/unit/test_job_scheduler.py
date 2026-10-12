"""四层职位定时调度器单测（隔周跳过 / 状态落盘 / 缺配置兜底）。"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from apscheduler.triggers.cron import CronTrigger

from app.core.config import JobLayerConfig, JobScheduleConfig
from app.scheduler import job_scheduler as mod
from app.scheduler.job_scheduler import JobSearchScheduler


def _cfg(**kw: Any) -> JobScheduleConfig:
    base: dict[str, Any] = {
        "enabled": True,
        "cron": "0 6 * * mon",
        "biweekly": True,
        "anchor_date": "2026-10-12",
        "user_id": "u1",
        "layers": [JobLayerConfig(label="L1", keywords=["Agent"])],
    }
    base.update(kw)
    return JobScheduleConfig(**base)


@pytest.fixture
def sched(tmp_path, monkeypatch):
    """把状态文件指到临时目录，避免污染 data/。"""
    monkeypatch.setattr(JobSearchScheduler, "STATUS_FILE", str(tmp_path / "status.json"))
    return JobSearchScheduler(_cfg(), ctx=object())


class TestRunOnce:
    @pytest.mark.asyncio
    async def test_missing_user_id_is_reported_not_raised(self, tmp_path, monkeypatch):
        monkeypatch.setattr(JobSearchScheduler, "STATUS_FILE", str(tmp_path / "s.json"))
        s = JobSearchScheduler(_cfg(user_id=""), ctx=object())
        out = await s.run_once(reason="test")
        assert out["ok"] is False and "user_id" in out["error"]

    @pytest.mark.asyncio
    async def test_no_layers_is_reported(self, tmp_path, monkeypatch):
        monkeypatch.setattr(JobSearchScheduler, "STATUS_FILE", str(tmp_path / "s.json"))
        s = JobSearchScheduler(_cfg(layers=[]), ctx=object())
        out = await s.run_once(reason="test")
        assert out["ok"] is False and "layers" in out["error"]

    @pytest.mark.asyncio
    async def test_success_is_persisted_to_status_file(self, sched, monkeypatch):
        async def fake_refresh(ctx, user_id, layers, **kw):
            return {"layers": len(layers), "ok": len(layers), "results": []}

        monkeypatch.setattr(mod, "refresh_all_layers", fake_refresh)
        out = await sched.run_once(reason="test")

        assert out["ok"] is True and out["layers"] == 1
        status = sched.read_status()
        assert status["ok"] is True
        assert status["reason"] == "test"
        assert "finished_at" in status and "duration_s" in status

    @pytest.mark.asyncio
    async def test_partial_failure_marks_not_ok(self, sched, monkeypatch):
        async def fake_refresh(ctx, user_id, layers, **kw):
            return {"layers": 2, "ok": 1, "results": []}

        monkeypatch.setattr(mod, "refresh_all_layers", fake_refresh)
        out = await sched.run_once(reason="test")
        assert out["ok"] is False  # 2 层只成功 1 层

    @pytest.mark.asyncio
    async def test_exception_is_persisted_not_swallowed_silently(self, sched, monkeypatch):
        async def boom(ctx, user_id, layers, **kw):
            raise RuntimeError("kernel down")

        monkeypatch.setattr(mod, "refresh_all_layers", boom)
        out = await sched.run_once(reason="test")

        assert out["ok"] is False and "kernel down" in out["error"]
        status = sched.read_status()
        assert status["ok"] is False and "kernel down" in status["error"]


class TestScheduledGuard:
    @pytest.mark.asyncio
    async def test_skips_on_off_week(self, sched, monkeypatch):
        """非执行周：不调用 run_once（这是"隔周"在调度层的体现）。"""
        anchor = date(2026, 10, 12)
        off = anchor + timedelta(days=7)  # 下周一 → 应跳过

        class _FakeDate(date):
            @classmethod
            def today(cls):
                return off

        monkeypatch.setattr(mod, "date", _FakeDate)
        called: list[str] = []

        async def spy(reason: str = "manual"):
            called.append(reason)
            return {}

        monkeypatch.setattr(sched, "run_once", spy)
        await sched._run_scheduled()
        assert called == []

    @pytest.mark.asyncio
    async def test_runs_on_anchor_week(self, sched, monkeypatch):
        anchor = date(2026, 10, 12)

        class _FakeDate(date):
            @classmethod
            def today(cls):
                return anchor

        monkeypatch.setattr(mod, "date", _FakeDate)
        called: list[str] = []

        async def spy(reason: str = "manual"):
            called.append(reason)
            return {}

        monkeypatch.setattr(sched, "run_once", spy)
        await sched._run_scheduled()
        assert called == ["scheduled:2026-10-12"]

    @pytest.mark.asyncio
    async def test_bad_anchor_date_degrades_to_weekly(self, sched, monkeypatch):
        """锚点非法时不应崩，也不应永久不跑 —— 退化为每周执行。"""
        sched._config.anchor_date = "不是日期"
        monkeypatch.setattr(mod, "date", date)
        called: list[str] = []

        async def spy(reason: str = "manual"):
            called.append(reason)
            return {}

        monkeypatch.setattr(sched, "run_once", spy)
        await sched._run_scheduled()
        assert len(called) == 1


class TestStartGuards:
    def test_disabled_does_not_register_job(self, tmp_path, monkeypatch):
        monkeypatch.setattr(JobSearchScheduler, "STATUS_FILE", str(tmp_path / "s.json"))
        s = JobSearchScheduler(_cfg(enabled=False), ctx=object())
        s.start()
        assert s.next_run_time() == ""

    def test_enabled_without_user_id_does_not_register_job(self, tmp_path, monkeypatch):
        monkeypatch.setattr(JobSearchScheduler, "STATUS_FILE", str(tmp_path / "s.json"))
        s = JobSearchScheduler(_cfg(user_id=""), ctx=object())
        s.start()
        assert s.next_run_time() == ""

class TestCronDayOfWeek:
    """回归：**星期必须解析到周一**。

    APScheduler 的 ``day_of_week`` 数字编号是 **0=周一**，而标准 crontab 是 **1=周一**，
    且 ``CronTrigger.from_crontab`` **不做换算** —— 所以写 ``0 6 * * 1`` 会落到**周二**
    （2026-10-12 部署后实测 ``next_fire`` 是 10-13 周二，才发现）。改用名称 ``mon`` 无歧义。
    本用例把结论锁住，避免以后有人"顺手改回数字"。
    """

    #: 一个已知的周一中午：用它推算"下一个周一 06:00"
    MONDAY_NOON = datetime(2026, 10, 12, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    def _next_fire(self, cron: str):
        trig = CronTrigger.from_crontab(cron, timezone=ZoneInfo("Asia/Shanghai"))
        return trig.get_next_fire_time(None, self.MONDAY_NOON)

    def test_numeric_one_lands_on_tuesday(self):
        """留着这条是为了说明"为什么不能用数字"：1 → 周二（APScheduler 语义）。"""
        nxt = self._next_fire("0 6 * * 1")
        assert nxt is not None and nxt.weekday() == 1  # 1 = Tuesday

    def test_model_default_cron_is_monday(self):
        nxt = self._next_fire(JobScheduleConfig().cron)
        assert nxt is not None
        assert nxt.weekday() == 0, f"默认 cron 未落在周一，而是 {nxt:%Y-%m-%d %A}"
        assert (nxt.hour, nxt.minute) == (6, 0)

    def test_shipped_config_cron_is_monday(self):
        """直接校验仓库里 config.yaml 的真实取值（默认值对了不代表配置没被改错）。"""
        from pathlib import Path

        import yaml

        cfg_path = Path(__file__).resolve().parents[2] / "config.yaml"
        cron = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))["job"]["schedule"]["cron"]
        nxt = self._next_fire(cron)
        assert nxt is not None
        assert nxt.weekday() == 0, f"config.yaml 的 job.schedule.cron={cron!r} 未落在周一，而是 {nxt:%Y-%m-%d %A}"
        assert (nxt.hour, nxt.minute) == (6, 0)
