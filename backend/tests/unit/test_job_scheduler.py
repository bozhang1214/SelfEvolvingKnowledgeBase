"""四层职位定时调度器单测（隔周跳过 / 状态落盘 / 缺配置兜底）。"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from app.core.config import JobLayerConfig, JobScheduleConfig
from app.scheduler import job_scheduler as mod
from app.scheduler.job_scheduler import JobSearchScheduler


def _cfg(**kw: Any) -> JobScheduleConfig:
    base: dict[str, Any] = {
        "enabled": True,
        "cron": "0 6 * * 1",
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
