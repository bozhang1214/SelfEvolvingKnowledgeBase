"""
RSS 采集模块。

使用 feedparser 抓取并解析 RSS/Atom 源，输出统一的 NewsItem 列表。
- 并发抓取多个源（asyncio.gather + to_thread，避免阻塞事件循环）
- 单个源失败不影响整体（记日志、跳过）
- 时间字段统一解析为 ISO 8601 字符串
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

import feedparser

from app.core.logging import get_logger

logger = get_logger(__name__)

# 单个源最多取多少条
_MAX_ENTRIES_PER_SOURCE = 100
# 抓取时的 User-Agent（部分源拒绝无 UA 的请求）
_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


@dataclass
class NewsItem:
    """标准化资讯条目。"""

    title: str
    link: str
    source: str
    summary: str = ""
    published: str = ""  # ISO 8601 时间字符串
    score: float = 0.0


class RSSFetcher:
    """RSS 采集器：抓取并解析 RSS/Atom 源。"""

    def __init__(self, sources: list[str], timeout: int = 15) -> None:
        self._sources = sources
        self._timeout = timeout

    async def fetch_all(self) -> list[NewsItem]:
        """并发抓取所有 RSS 源，返回标准化条目列表（去重前）。"""
        if not self._sources:
            logger.warning("未配置 RSS 源，跳过采集")
            return []

        results = await asyncio.gather(
            *[self._fetch_one(url) for url in self._sources],
            return_exceptions=True,
        )

        items: list[NewsItem] = []
        for url, res in zip(self._sources, results):
            if isinstance(res, Exception):
                logger.warning("RSS 源抓取失败", source=url, error=str(res))
                continue
            items.extend(res)

        logger.info("RSS 采集完成", source_count=len(self._sources), item_count=len(items))
        return items

    async def _fetch_one(self, url: str) -> list[NewsItem]:
        """抓取单个源（httpx 带超时 + feedparser 解析内容字符串）。"""
        import httpx

        try:
            async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
                resp = await client.get(url, headers={"User-Agent": _USER_AGENT})
                resp.raise_for_status()
                content = resp.text
        except Exception as e:
            logger.warning("RSS 源抓取失败", source=url, error=str(e))
            return []

        # 解析已抓取的字符串（本地操作，无网络）
        feed = feedparser.parse(content)
        source = self._source_name(url)
        out: list[NewsItem] = []
        for entry in feed.entries[:_MAX_ENTRIES_PER_SOURCE]:
            title = (entry.get("title") or "").strip()
            link = (entry.get("link") or "").strip()
            if not title:
                continue
            out.append(
                NewsItem(
                    title=title,
                    link=link,
                    source=source,
                    summary=(entry.get("summary") or entry.get("description") or "").strip(),
                    published=self._parse_time(entry),
                )
            )
        return out

    @staticmethod
    def _source_name(url: str) -> str:
        """从 URL 提取源名（域名）。"""
        try:
            return urlparse(url).netloc
        except Exception:
            return url

    @staticmethod
    def _parse_time(entry: object) -> str:
        """解析 feedparser 的结构化时间，转为 ISO 8601 字符串。"""
        try:
            t = entry.get("published_parsed") or entry.get("updated_parsed")
            if t:
                return datetime(*t[:6], tzinfo=timezone.utc).isoformat()
        except Exception:
            pass
        return entry.get("published") or entry.get("updated") or ""
