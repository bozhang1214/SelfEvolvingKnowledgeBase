"""
网页采集模块（非 RSS 源，直接调用公开 JSON 接口）。

- CSDN 热榜：``GET https://blog.csdn.net/phoenix/web/blog/hot-rank``
- 魔搭 ModelScope 模型库：``PUT https://www.modelscope.cn/api/v1/models``

两者均为公开接口（无需 Token），输出统一 ``NewsItem``。
"""
from __future__ import annotations

import asyncio

import httpx

from app.agents.news.rss_fetcher import NewsItem
from app.core.logging import get_logger

logger = get_logger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# 每个网页源最多取多少条
_MAX_PER_SOURCE = 25


class WebFetcher:
    """网页采集器：从公开 JSON 接口抓取信息，归一化为 NewsItem。"""

    def __init__(self, timeout: int = 15) -> None:
        self._timeout = timeout

    async def fetch_all(self) -> list[NewsItem]:
        """并发抓取 CSDN 热榜 + 魔搭模型库。"""
        results = await asyncio.gather(
            self._fetch_csdn(),
            self._fetch_modelscope(),
            return_exceptions=True,
        )
        items: list[NewsItem] = []
        for res in results:
            if isinstance(res, Exception):
                logger.warning("网页采集失败", error=str(res))
                continue
            items.extend(res)
        logger.info("网页采集完成", item_count=len(items))
        return items

    async def _fetch_csdn(self) -> list[NewsItem]:
        """抓取 CSDN 热榜（动态接口，返回 JSON）。"""
        url = "https://blog.csdn.net/phoenix/web/blog/hot-rank?page=0&pageSize=25&type="
        try:
            async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
                resp = await client.get(url, headers={"User-Agent": _USER_AGENT})
                resp.raise_for_status()
                payload = resp.json()
        except Exception as e:
            logger.warning("CSDN 热榜抓取失败", error=str(e))
            return []

        out: list[NewsItem] = []
        for it in (payload.get("data") or [])[:_MAX_PER_SOURCE]:
            title = (it.get("articleTitle") or "").strip()
            link = (it.get("articleDetailUrl") or "").strip()
            if not title:
                continue
            out.append(
                NewsItem(
                    title=title,
                    link=link,
                    source="CSDN",
                    summary="",
                    content="",
                    published="",
                )
            )
        return out

    async def _fetch_modelscope(self) -> list[NewsItem]:
        """抓取魔搭模型库列表（公开接口，PUT 方法）。"""
        url = "https://www.modelscope.cn/api/v1/models"
        try:
            async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
                resp = await client.put(
                    url,
                    json={"page_number": 1, "page_size": _MAX_PER_SOURCE, "search": ""},
                    headers={"User-Agent": _USER_AGENT, "Content-Type": "application/json"},
                )
                resp.raise_for_status()
                payload = resp.json()
        except Exception as e:
            logger.warning("魔搭模型库抓取失败", error=str(e))
            return []

        models = (payload.get("Data") or {}).get("Models") or []
        out: list[NewsItem] = []
        for it in models:
            title = (it.get("ChineseName") or it.get("Name") or "").strip()
            if not title:
                continue
            backend = it.get("BackendSupport") or {}
            model_id = backend.get("model_id") or it.get("Name") or ""
            link = f"https://modelscope.cn/models/{model_id}" if model_id else ""
            summary = (it.get("Description") or "").strip()
            out.append(
                NewsItem(
                    title=title,
                    link=link,
                    source="魔搭",
                    summary=summary,
                    content=summary,
                    published="",
                )
            )
        return out
