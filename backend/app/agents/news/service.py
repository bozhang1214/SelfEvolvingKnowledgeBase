"""
资讯日报 Agent 服务编排。

串联流水线：RSS 采集 → 关键词筛选 → LLM 生成日报 → 存储。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.agents.news.filter import NewsFilter
from app.agents.news.generator import DailyReportGenerator
from app.agents.news.rss_fetcher import RSSFetcher
from app.agents.news.storage import NewsStorage
from app.core.logging import get_logger

logger = get_logger(__name__)


class NewsAgent:
    """资讯日报 Agent。"""

    def __init__(self, config: Any, llm_factory: Any) -> None:
        self._config = config
        self._fetcher = RSSFetcher(config.rss_sources)
        self._filter = NewsFilter(
            keywords=config.keywords,
            exclude_keywords=config.exclude_keywords,
            time_window_hours=config.time_window_hours,
        )
        self._generator = DailyReportGenerator(llm_factory)
        self._storage = NewsStorage(config.report_dir, config.retention_days)

    async def refresh(self) -> dict:
        """执行一次完整日报刷新，返回结果摘要。"""
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # 1. 采集
        items = await self._fetcher.fetch_all()
        # 2. 筛选
        filtered = self._filter.filter(items)
        # 3. 生成
        items_json = [self._item_to_dict(it) for it in filtered]
        report = await self._generator.generate(items_json, self._config.llm_role)
        # 4. 存储
        path = self._storage.save_daily(day, report)

        logger.info(
            "日报刷新完成",
            date=day,
            fetched=len(items),
            filtered=len(filtered),
            path=path,
        )
        return {
            "date": day,
            "fetched": len(items),
            "filtered": len(filtered),
            "path": path,
            "report": report,
        }

    def list_reports(self) -> list[dict]:
        """列出历史日报。"""
        return self._storage.list_reports()

    def read_report(self, day: str) -> dict | None:
        """读取指定日期的日报。"""
        return self._storage.read_report(day)

    @staticmethod
    def _item_to_dict(item: Any) -> dict:
        return {
            "title": item.title,
            "source": item.source,
            "link": item.link,
            "published": item.published,
            "score": item.score,
        }
