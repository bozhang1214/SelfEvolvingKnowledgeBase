"""
职位采集模块（猎聘公开 JSON 接口）。

基于猎聘 liepin.com 的免登录搜索接口抓取真实职位列表。
关键：必须走「先 GET 首页拿 Cookie（XSRF-TOKEN/acw_tc/__gc_id）→ 再带同会话
Cookie + X-Xsrf-Token + X-Fscp-* 网关头 POST」两步流程，且请求体需带全所有
表单字段（缺字段会返回 flag=0 code=-1400）。

采用 requests.Session（同步）在线程池中执行，保证两次请求复用同一会话 Cookie
（这是接口正常返回的关键）。任何一步失败都降级为空列表，不阻断整个招聘分析流程。

使用方式：
    from app.agents.job.fetcher import LiepinJobFetcher
    jobs = await LiepinJobFetcher().fetch(keyword="AI", city="410", page=0)
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

import requests

from app.core.logging import get_logger

logger = get_logger(__name__)

_LIEPIN_HOME = "https://www.liepin.com"
_LIEPIN_SEARCH_API = "https://api-c.liepin.com/api/com.liepin.searchfront4c.pc-search-job"

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
)


def _fetch_sync(keyword: str, city: str, page: int, limit: int) -> list[dict[str, Any]]:
    """同步采集实现（requests.Session 复用 Cookie）。"""
    session = requests.Session()

    # 第一步：访问首页，拿 Cookie（XSRF-TOKEN / acw_tc / __gc_id）
    session.get(
        _LIEPIN_HOME,
        headers={"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9"},
        timeout=15,
    )
    xsrf = session.cookies.get("XSRF-TOKEN", "")

    # 第二步：搜索接口（完整 body + 完整 headers，同会话 Cookie 自动带上）
    headers = {
        "User-Agent": _UA,
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://www.liepin.com",
        "Referer": f"https://www.liepin.com/zhaopin/?key={keyword}",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "X-Client-Type": "web",
        "X-Requested-With": "XMLHttpRequest",
        "X-Fscp-Std-Info": '{"client_id": "40108"}',
        "X-Fscp-Version": "1.1",
        "X-Fscp-Trace-Id": str(uuid.uuid4()),
        "X-Xsrf-Token": xsrf,
    }
    body = {
        "data": {
            "mainSearchPcConditionForm": {
                "city": city,
                "dq": city,
                "pubTime": "",
                "currentPage": page,
                "pageSize": limit,
                "key": keyword,
                "suggestTag": "",
                "workYearCode": "",
                "compId": "",
                "compName": "",
                "compTag": "",
                "industry": "",
                "salaryCode": "",
                "jobKind": "",
                "compScale": "",
                "compKind": "",
                "compStage": "",
                "eduLevel": "",
                "salaryLow": "",
                "salaryHigh": "",
            },
            "passThroughForm": {
                "scene": "init",
                "skId": "",
                "fkId": "",
                "ckId": "",
                "suggest": None,
            },
        }
    }
    resp = session.post(_LIEPIN_SEARCH_API, headers=headers, json=body, timeout=20)
    resp.raise_for_status()
    payload = resp.json()

    if payload.get("flag") != 1:
        logger.warning(
            "猎聘搜索返回异常", keyword=keyword,
            code=payload.get("code"), msg=payload.get("msg"),
        )
        return []

    data = payload.get("data") or {}
    inner = data.get("data") if isinstance(data, dict) else {}
    cards = inner.get("jobCardList") or data.get("jobCardList") or []
    return [_normalize(c) for c in cards if isinstance(c, dict)]


def _normalize(card: dict[str, Any]) -> dict[str, Any]:
    """把猎聘 jobCard 归一化为统一职位结构（兼容 job/comp 嵌套结构）。"""
    job = card.get("job") if isinstance(card.get("job"), dict) else {}
    comp = card.get("comp") if isinstance(card.get("comp"), dict) else {}
    job_id = job.get("jobId") or card.get("jobId") or card.get("job_id") or ""
    return {
        "job_id": str(job_id),
        "title": job.get("title") or card.get("title") or "",
        "company": comp.get("compName") or card.get("companyName") or "",
        "salary": job.get("salary") or card.get("salary") or "",
        "city": job.get("dq") or card.get("dq") or card.get("city") or "",
        "job_url": f"https://www.liepin.com/job/{job_id}" if job_id else "",
        "jd_text": job.get("jd") or card.get("jd") or card.get("jobAbstract") or "",
    }


class LiepinJobFetcher:
    """猎聘职位采集器（免登录公开接口）。"""

    def __init__(self, timeout: int = 20) -> None:
        self._timeout = timeout

    async def fetch(
        self,
        keyword: str,
        city: str = "410",  # 410 = 全国
        page: int = 0,
        limit: int = 40,
    ) -> list[dict[str, Any]]:
        """按关键词搜索职位，返回归一化职位列表；失败返回空列表。"""
        if not keyword.strip():
            return []
        try:
            jobs = await asyncio.to_thread(_fetch_sync, keyword, city, page, limit)
            logger.info("猎聘职位采集成功", keyword=keyword, count=len(jobs))
            return jobs
        except Exception as e:  # noqa: BLE001
            logger.warning("猎聘职位采集失败", keyword=keyword, error=str(e)[:200])
            return []
