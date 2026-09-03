"""
职位采集模块（猎聘公开 JSON 接口）。

基于猎聘 liepin.com 的免登录搜索接口抓取真实职位列表。
接口较脆弱（依赖 XSRF cookie + 固定 header），任何一步失败都降级为空列表，
不阻断整个招聘分析流程。

流程：
    1. GET https://www.liepin.com 拿 XSRF-TOKEN cookie
    2. POST api-c.liepin.com 搜索接口（带 X-Xsrf-Token + 固定 header）
    3. 解析 jobCardList → 归一化职位列表

使用方式：
    from app.agents.job.fetcher import LiepinJobFetcher
    jobs = await LiepinJobFetcher().fetch(keyword="AI", city="410", page=0)
"""
from __future__ import annotations

import uuid
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

_LIEPIN_HOME = "https://www.liepin.com"
_LIEPIN_SEARCH_API = "https://api-c.liepin.com/api/com.liepin.searchfront4c.pc-search-job"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "X-Client-Type": "web",
    "X-Fscp-Std-Info": '{"client_id":"40108"}',
    "X-Fscp-Version": "1.1",
}


class LiepinJobFetcher:
    """猎聘职位采集器（免登录公开接口）。"""

    def __init__(self, timeout: int = 12) -> None:
        self._timeout = timeout

    async def fetch(
        self,
        keyword: str,
        city: str = "410",  # 410 = 全国
        page: int = 0,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """按关键词搜索职位，返回归一化职位列表；失败返回空列表。"""
        if not keyword.strip():
            return []
        try:
            cookie, xsrf = await self._get_cookie()
            return await self._search(keyword, city, page, limit, cookie, xsrf)
        except Exception as e:  # noqa: BLE001
            logger.warning("猎聘职位采集失败", keyword=keyword, error=str(e)[:200])
            return []

    async def _get_cookie(self) -> tuple[str, str]:
        """访问首页拿 XSRF-TOKEN cookie。"""
        async with httpx.AsyncClient(
            timeout=self._timeout, follow_redirects=True
        ) as client:
            resp = await client.get(_LIEPIN_HOME, headers={"User-Agent": _HEADERS["User-Agent"]})
            cookies = client.cookies
            xsrf = cookies.get("XSRF-TOKEN", "")
            cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
            return cookie_str, xsrf

    async def _search(
        self,
        keyword: str,
        city: str,
        page: int,
        limit: int,
        cookie: str,
        xsrf: str,
    ) -> list[dict[str, Any]]:
        """调用搜索接口并归一化 jobCardList。"""
        headers = {
            **_HEADERS,
            "Cookie": cookie,
            "X-Xsrf-Token": xsrf,
            "X-Fscp-Trace-Id": uuid.uuid4().hex,
            "Content-Type": "application/json;charset=UTF-8",
        }
        body = {
            "data": {
                "mainSearchPcConditionForm": {
                    "key": keyword,
                    "city": city,
                    "dq": city,
                    "currentPage": page,
                    "pageSize": limit,
                },
                "passThroughForm": {"scene": "init"},
            }
        }
        async with httpx.AsyncClient(
            timeout=self._timeout, follow_redirects=True
        ) as client:
            resp = await client.post(_LIEPIN_SEARCH_API, headers=headers, json=body)
            resp.raise_for_status()
            payload = resp.json()

        cards = payload.get("data", {}).get("data", {}).get("jobCardList") or []
        jobs = [self._normalize(c) for c in cards if isinstance(c, dict)]
        logger.info("猎聘职位采集成功", keyword=keyword, count=len(jobs))
        return jobs

    @staticmethod
    def _normalize(card: dict[str, Any]) -> dict[str, Any]:
        """把猎聘 jobCard 归一化为统一职位结构。"""
        comp = card.get("comp") or {}
        return {
            "job_id": card.get("jobId") or card.get("job_id") or "",
            "title": card.get("title") or "",
            "company": comp.get("compName") or card.get("companyName") or "",
            "salary": card.get("salary") or "",
            "city": card.get("dq") or card.get("city") or "",
            "job_url": f"https://www.liepin.com/job/{card.get('jobId')}"
            if card.get("jobId") else "",
            "jd_text": card.get("jd") or card.get("jobAbstract") or card.get("description") or "",
        }
