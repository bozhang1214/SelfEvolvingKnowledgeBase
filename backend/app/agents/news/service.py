"""
资讯日报 Agent 服务编排。

串联流水线：RSS 采集 → 关键词筛选 → 原文正文抽取 → LLM 生成日报 → 存储。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from app.agents.news.content_extractor import fetch_article_text
from app.agents.news.filter import NewsFilter
from app.agents.news.generator import DailyReportGenerator
from app.agents.news.rss_fetcher import RSSFetcher
from app.agents.news.storage import NewsStorage
from app.core.logging import get_logger

logger = get_logger(__name__)

# 原文正文抓取的并发数（过大易被目标站限流）
_CONTENT_CONCURRENCY = 10
# 单条正文传给 LLM 的最大字符数（150~200 字摘要足够）
_CONTENT_MAX_CHARS = 1200


class NewsAgent:
    """资讯日报 Agent。"""

    def __init__(self, config: Any, llm_factory: Any) -> None:
        self._config = config
        self._fetcher = RSSFetcher(config.rss_sources)
        # 关键词筛选用「顶层 keywords ∪ 所有大类关键词」，确保融资/安全/开源等
        # 大类相关内容不会在分类前被顶层筛选误杀。
        all_keywords = list(config.keywords or [])
        for c in config.categories or []:
            all_keywords.extend(c.keywords or [])
        self._filter = NewsFilter(
            keywords=list(dict.fromkeys(all_keywords)),  # 去重保序
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
        # 3. 原文正文抽取（RSS 摘要/全文不足时抓取原文，供 150~200 字摘要用）
        items_json = [self._item_to_dict(it) for it in filtered]
        items_json = await self._enrich_content(items_json)
        # 4. 生成（逐类）
        report = await self._generator.generate(
            items_json,
            self._config.llm_role,
            self._config.categories,
        )
        # 5. 存储
        path = self._storage.save_daily(day, report)
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
            "summary": item.summary,  # RSS 摘要（兜底）
            "content": item.content,  # RSS 全文（content:encoded，可能为空）
        }

    async def _enrich_content(self, items_json: list[dict]) -> list[dict]:
        """为每条资讯补全正文：优先 RSS 全文/摘要，不足时并发抓取原文正文。"""
        sem = asyncio.Semaphore(_CONTENT_CONCURRENCY)

        async def enrich(d: dict) -> dict:
            rss_summary = (d.get("summary") or "").strip()
            rss_content = (d.get("content") or "").strip()
            fetched = ""
            # RSS 已有较完整内容时直接用（「用原文的摘要」）；不足则抓原文正文
            if max(len(rss_content), len(rss_summary)) < 200 and d.get("link"):
                async with sem:
                    fetched = await fetch_article_text(d["link"])
            best = max([rss_content, rss_summary, fetched], key=len)
            d["content"] = best[:_CONTENT_MAX_CHARS]
            # 保留 summary（RSS 摘要）供分类关键词匹配；content 供 LLM 写摘要
            return d

        return list(await asyncio.gather(*[enrich(d) for d in items_json]))
