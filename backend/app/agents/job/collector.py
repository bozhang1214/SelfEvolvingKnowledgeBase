"""
多源职位采集器。

并行调用多个招聘渠道（猎聘 + 字节/腾讯/百度/小米/阿里/小红书等），
合并去重后统一做客户端筛选（城市包含匹配 + 薪资下限）。

各源提供统一接口：``async def fetch(keyword, page=0, limit=20) -> list[dict]``，
返回归一化职位字典（title/company/salary/city/job_url/jd_text/source）。
单个源失败不影响整体（降级为空列表并记录 warning）。

使用方式：
    from app.agents.job.collector import JobCollector
    collector = JobCollector(city="北京", min_salary_k=50)
    result = await collector.fetch_all(keyword="Agent", page=0)
    # result = {"sources": {源名: {"raw": n, "count": n}}, "count": n, "jobs": [...]}
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.agents.job.fetcher import LiepinJobFetcher, filter_jobs
from app.core.logging import get_logger

logger = get_logger(__name__)


class BossBrowserSource:
    """BOSS 直聘采集源：调用通用浏览器服务（sekb-browser）。

    需要先在浏览器服务里导入 BOSS Cookie 或完成扫码登录；未登录时采集返回空列表。
    """

    name = "BOSS直聘"

    def __init__(self, base_url: str = "http://browser:1300") -> None:
        self._base_url = base_url

    async def fetch(
        self, keyword: str, page: int = 0, limit: int = 20, city: str = "北京"
    ) -> list[dict[str, Any]]:
        if not keyword or not keyword.strip():
            return []
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{self._base_url}/scrape",
                    json={
                        "site": "boss",
                        "keyword": keyword.strip(),
                        "city": city,
                        "page": page,
                        "limit": limit,
                    },
                )
                if resp.status_code != 200:
                    logger.warning("BOSS 浏览器服务返回非 200", status=resp.status_code)
                    return []
                jobs = resp.json().get("jobs") or []
                for j in jobs:
                    j.setdefault("source", self.name)
                return jobs
        except Exception as e:  # noqa: BLE001
            # 浏览器服务不可达或未启动时静默降级
            logger.warning("BOSS 浏览器服务调用失败", error=str(e)[:150])
            return []


def _load_sources() -> list[Any]:
    """加载所有采集源；sources.py 未就绪时仅用猎聘（不阻断）。"""
    sources: list[Any] = [LiepinJobFetcher()]
    try:
        from app.agents.job.sources import (
            AlibabaSource,
            BaiduSource,
            BytedanceSource,
            MokahrSource,
            TencentSource,
            XiaohongshuSource,
            XiaomiSource,
        )

        sources += [
            BytedanceSource(),
            TencentSource(),
            BaiduSource(),
            XiaomiSource(),
            AlibabaSource(),
            XiaohongshuSource(),
            MokahrSource(org_slug="dji", site_id=170070, name="大疆"),
            MokahrSource(org_slug="high-flyer", site_id=140576, name="DeepSeek"),
            BossBrowserSource(),  # 需先导入 BOSS Cookie/扫码登录，未登录时返回空
        ]
    except ImportError as e:  # noqa: BLE001
        logger.warning("多源采集模块未就绪，仅使用猎聘", error=str(e))
    return sources


def exclude_big_tech(
    jobs: list[dict[str, Any]], exclude_keywords: list[str]
) -> list[dict[str, Any]]:
    """排除大厂公司：company 命中任一排除关键词的职位被过滤掉。"""
    if not exclude_keywords:
        return jobs
    out: list[dict[str, Any]] = []
    for j in jobs:
        company = (j.get("company") or "").lower()
        if any(kw.lower() in company for kw in exclude_keywords):
            continue
        out.append(j)
    return out


class JobCollector:
    """多源职位采集器。"""

    # 通用职位平台（猎聘/BOSS）：需要排除大厂（大厂已有独立渠道）
    _GENERAL_BOARDS = {"猎聘", "BOSS直聘"}

    def __init__(
        self,
        city: str = "北京",
        min_salary_k: int = 50,
        exclude_companies: list[str] | None = None,
    ) -> None:
        self._city = city
        self._min_salary_k = min_salary_k
        self._exclude = exclude_companies or []
        self._sources = _load_sources()

    @property
    def source_names(self) -> list[str]:
        return [getattr(s, "name", s.__class__.__name__) for s in self._sources]

    async def fetch_all(
        self, keyword: str, page: int = 0, limit: int = 20
    ) -> dict[str, Any]:
        """并行采集各源，合并去重 + 客户端筛选，返回汇总。"""
        keyword = (keyword or "").strip()
        if not keyword:
            return {"sources": {}, "count": 0, "jobs": []}

        results = await asyncio.gather(
            *[self._safe_fetch(s, keyword, page, limit) for s in self._sources]
        )

        per_source: dict[str, dict[str, int]] = {}
        merged: list[dict[str, Any]] = []
        for name, jobs in results:
            filtered = filter_jobs(
                jobs, city=self._city, min_salary_k=self._min_salary_k
            )
            # 通用平台（猎聘/BOSS）排除大厂公司
            if name in self._GENERAL_BOARDS and self._exclude:
                filtered = exclude_big_tech(filtered, self._exclude)
            per_source[name] = {"raw": len(jobs), "count": len(filtered)}
            merged.extend(filtered)

        merged = self._dedup(merged)
        logger.info(
            "多源职位采集完成",
            keyword=keyword,
            sources=len(self._sources),
            total=len(merged),
        )
        return {"sources": per_source, "count": len(merged), "jobs": merged}

    async def _safe_fetch(
        self, source: Any, keyword: str, page: int, limit: int
    ) -> tuple[str, list[dict[str, Any]]]:
        name = getattr(source, "name", source.__class__.__name__)
        try:
            # BOSS 浏览器服务需要城市参数才能采对应城市（否则永远只采北京）
            if isinstance(source, BossBrowserSource):
                jobs = await source.fetch(
                    keyword=keyword, page=page, limit=limit, city=self._city
                )
            else:
                jobs = await source.fetch(keyword=keyword, page=page, limit=limit)
            return name, jobs or []
        except Exception as e:  # noqa: BLE001
            logger.warning("采集源失败", source=name, error=str(e)[:150])
            return name, []

    @staticmethod
    def _dedup(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """按 job_url（缺省 job_id+company）去重，保留先出现者。"""
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for j in jobs:
            key = j.get("job_url") or ""
            if not key and j.get("job_id"):
                key = f"{j['job_id']}@{j.get('company', '')}"
            if not key:
                key = f"{j.get('title', '')}@{j.get('company', '')}"
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            out.append(j)
        return out
