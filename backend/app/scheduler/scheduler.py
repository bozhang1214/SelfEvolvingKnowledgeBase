"""
科技资讯定时调度器。

使用 APScheduler 的 AsyncIOScheduler，在应用启动时按 cron 注册日报/周报任务。
- 日报：config.news.daily_cron（默认每天 09:00）
- 周报/月报：预留（后续接入 news_generate 聚合逻辑）
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.logging import get_logger

logger = get_logger(__name__)


class NewsScheduler:
    """科技资讯定时调度器。"""

    def __init__(self, config: Any, news_agent: Any) -> None:
        self._config = config
        self._agent = news_agent
        self._scheduler = AsyncIOScheduler()

    #: 状态文件名（放在报告目录里，和报告一起被保留/清理）
    STATUS_FILE = "last_status.json"
    #: 失败重试次数与间隔（秒）。网络/TLS 抖动是实测最常见的失败原因
    RETRY_TIMES = 1
    RETRY_DELAY_S = 60
    #: 错过触发后的宽限时间（秒）。默认只有 1 秒 —— 容器恰好在 08:00 前后重启时，
    #: 这次运行会被**静默跳过**（周一没生成周报很可能就是这个原因），因此放宽到 1 小时。
    MISFIRE_GRACE_S = 3600

    def _status_path(self) -> Path:
        return Path(self._config.report_dir) / self.STATUS_FILE

    def _record_status(self, kind: str, *, ok: bool, period: str = "", error: str = "",
                       started_at: str = "", duration_s: float = 0.0) -> None:
        """把每次定时任务的**结果**落盘（成功也记）。

        为什么必须落盘：原来失败只在 APScheduler 里打一行 "raised an exception"，
        没人会去看，于是「日报没出来」只能靠人肉眼发现。落盘后接口/前端能直接展示
        「今日日报生成失败：<原因>」，问题就不会再静默。
        """
        payload = {
            "kind": kind, "ok": ok, "period": period, "error": error,
            "started_at": started_at, "finished_at": datetime.now(timezone.utc).isoformat(),
            "duration_s": round(duration_s, 1),
        }
        try:
            path = self._status_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as e:  # 落盘失败不能把任务本身搞挂
            logger.warning("写入资讯任务状态失败", error=str(e)[:200])
        if ok:
            logger.info("资讯定时任务完成", **payload)
        else:
            logger.error("资讯定时任务失败", **payload)

    async def _run_guarded(self, kind: str, factory: Any) -> None:
        """执行一次定时任务：失败重试 + 结果落盘（**不再静默失败**）。"""
        import asyncio

        started = datetime.now(timezone.utc)
        last_error = ""
        for attempt in range(self.RETRY_TIMES + 1):
            try:
                result = await factory()
                period = ""
                if isinstance(result, dict):
                    period = str(result.get("period") or "")
                self._record_status(
                    kind, ok=True, period=period,
                    started_at=started.isoformat(),
                    duration_s=(datetime.now(timezone.utc) - started).total_seconds(),
                )
                return
            except Exception as e:  # noqa: BLE001
                last_error = f"{type(e).__name__}: {str(e)[:200]}"
                logger.warning("资讯定时任务执行失败", kind=kind, attempt=attempt + 1, error=last_error)
                if attempt < self.RETRY_TIMES:
                    await asyncio.sleep(self.RETRY_DELAY_S)
        self._record_status(
            kind, ok=False, error=last_error,
            started_at=started.isoformat(),
            duration_s=(datetime.now(timezone.utc) - started).total_seconds(),
        )

    def _job_kwargs(self) -> dict:
        """定时任务的公共参数：放宽 misfire 宽限 + 不并发 + 合并补跑。"""
        return {
            "replace_existing": True,
            "misfire_grace_time": self.MISFIRE_GRACE_S,
            "coalesce": True,
            "max_instances": 1,
        }

    def start(self) -> None:
        """注册并启动定时任务。"""
        if not self._config.enabled:
            logger.info("科技资讯未启用（news.enabled=false），跳过调度")
            return

        self._scheduler.add_job(
            lambda: self._run_guarded("daily", lambda: self._agent.refresh()),
            CronTrigger.from_crontab(self._config.daily_cron, timezone=self._config.timezone),
            id="news_daily",
            name="科技资讯（每日）",
            **self._job_kwargs(),
        )
        self._scheduler.add_job(
            lambda: self._run_guarded("weekly", lambda: self._agent.generate_periodic("weekly")),
            CronTrigger.from_crontab(self._config.weekly_cron, timezone=self._config.timezone),
            id="news_weekly",
            name="科技周报（每周一）",
            **self._job_kwargs(),
        )
        self._scheduler.add_job(
            lambda: self._run_guarded("monthly", lambda: self._agent.generate_periodic("monthly")),
            CronTrigger.from_crontab(self._config.monthly_cron, timezone=self._config.timezone),
            id="news_monthly",
            name="科技月报（每月 1 日）",
            **self._job_kwargs(),
        )
        self._scheduler.start()
        logger.info(
            "科技资讯调度已启动",
            daily_cron=self._config.daily_cron,
            weekly_cron=self._config.weekly_cron,
            timezone=self._config.timezone,
        )

    def shutdown(self) -> None:
        """关闭调度器。"""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("科技资讯调度已关闭")
