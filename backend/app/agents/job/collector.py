"""
多源职位采集器。

并行调用多个招聘渠道（猎聘 + 字节/腾讯/百度/小米/阿里/小红书等），
合并去重后统一做客户端筛选（城市前缀 + 薪资下限）。

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

from app.agents.job.fetcher import LiepinJobFetcher, filter_jobs
from app.core.logging import get_logger

logger = get_logger(__name__)


def _load_sources() -> list[Any]:
    """加载所有采集源；sources.py 未就绪时仅用猎聘（不阻断）。"""
    sources: list[Any] = [LiepinJobFetcher()]
    try:
        from app.agents.job.sources import (
            AlibabaSource,
            BaiduSource,
            BytedanceSource,
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
        ]
    except ImportError as e:  # noqa: BLE001
        logger.warning("多源采集模块未就绪，仅使用猎聘", error=str(e))
    return sources


class JobCollector:
    """多源职位采集器。"""

    def __init__(self, city: str = "北京", min_salary_k: int = 50) -> None:
        self._city = city
        self._min_salary_k = min_salary_k
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
