"""
科技资讯定时调度器。

使用 APScheduler 的 AsyncIOScheduler，在应用启动时按 cron 注册日报/周报任务。
- 日报：config.news.daily_cron（默认每天 09:00）
- 周报/月报：预留（后续接入 news_generate 聚合逻辑）
"""
from __future__ import annotations

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

    def start(self) -> None:
        """注册并启动定时任务。"""
        if not self._config.enabled:
            logger.info("科技资讯未启用（news.enabled=false），跳过调度")
            return

        self._scheduler.add_job(
            self._agent.refresh,
            CronTrigger.from_crontab(self._config.daily_cron, timezone=self._config.timezone),
            id="news_daily",
            name="科技资讯（每日）",
            replace_existing=True,
        )
        self._scheduler.add_job(
            lambda: self._agent.generate_periodic("weekly"),
            CronTrigger.from_crontab(self._config.weekly_cron, timezone=self._config.timezone),
            id="news_weekly",
            name="科技周报（每周一）",
            replace_existing=True,
        )
        self._scheduler.add_job(
            lambda: self._agent.generate_periodic("monthly"),
            CronTrigger.from_crontab(self._config.monthly_cron, timezone=self._config.timezone),
            id="news_monthly",
            name="科技月报（每月 1 日）",
            replace_existing=True,
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
