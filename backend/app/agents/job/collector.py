"""
多源职位采集器。

并行调用多个招聘渠道（猎聘 + 字节/腾讯/百度/小米/阿里/小红书等），
合并去重后统一做客户端筛选（城市包含匹配 + 薪资下限）。

各源提供统一接口：``async def fetch(keyword, page=0, limit=20) -> list[dict]``，
返回归一化职位字典（title/company/salary/city/job_url/jd_text/source）。
单个源失败不影响整体（降级为空列表并记录 warning）。

``keyword`` 支持**多关键词**：用英文逗号 ``,``（或中文 `，`）分隔，采集时逐词抓取后合并去重。
这让「一条搜索」能覆盖同义/近义岗位族（如 ``FDE,前沿部署,前向部署``），
报告因此更全面，用户在前端也只需维护一条搜索。

.. warning::
   分隔符**不能用 ``|``**——``job_cache.parse_key()`` 用 ``|`` 切分存储键来取 keyword，
   关键词里含 ``|`` 会把键解析错位（keyword/city/薪资 全部串位）。

使用方式：
    from app.agents.job.collector import JobCollector
    collector = JobCollector(city="北京", min_salary_k=50)
    result = await collector.fetch_all(keyword="Agent", page=0)
    # result = {"sources": {源名: {"raw": n, "count": n}}, "count": n, "jobs": [...]}
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

from app.agents.job.fetcher import LiepinJobFetcher, filter_jobs
from app.core.logging import get_logger

logger = get_logger(__name__)

# 多关键词分隔符：英文逗号为主，兼容中文逗号。**不可用 ``|``**（见模块 docstring）。
_KEYWORD_SEPARATORS = (",", "，")


def split_keywords(keyword: str) -> list[str]:
    """把 ``keyword`` 拆成关键词列表（去空白、去重、保序、丢弃空项）。

    单关键词输入原样返回单元素列表，因此旧调用方行为完全不变。
    """
    text = (keyword or "").strip()
    if not text:
        return []
    for sep in _KEYWORD_SEPARATORS:
        text = text.replace(sep, "\x00")
    out: list[str] = []
    seen: set[str] = set()
    for part in text.split("\x00"):
        term = part.strip()
        if term and term not in seen:
            seen.add(term)
            out.append(term)
    return out


class _BrowserSource:
    """浏览器采集源基类：调用通用浏览器服务（sekb-browser）的 ``/scrape``。

    子类只需给出 ``name`` / ``site``。浏览器服务侧按 ``site`` 路由到对应站点的采集器
    （当前注册了 ``boss`` / ``zhaopin``）。

    - **登录态**：BOSS 需要导入 Cookie 或扫码登录；**智联免登录可用**（只有其 `/recommend`
      页需要登录）。两者未登录/服务不可达时都**静默降级为空列表**，不阻断其它源。
    - 超时给到 90s：浏览器要启动 Chromium + 导航 + 等渲染，比普通 HTTP 源慢一个量级。
    """

    name = ""
    site = ""

    def __init__(self, base_url: str = "http://browser:1300") -> None:
        self._base_url = base_url

    async def fetch(
        self, keyword: str, page: int = 0, limit: int = 20, city: str = "北京"
    ) -> list[dict[str, Any]]:
        if not keyword or not keyword.strip():
            return []
        try:
            # 内部服务鉴权（S12）：浏览器服务持有登录 Cookie，需带 X-Internal-Token
            token = os.getenv("BROWSER_INTERNAL_TOKEN", "").strip()
            headers = {"X-Internal-Token": token} if token else {}
            async with httpx.AsyncClient(timeout=90) as client:
                resp = await client.post(
                    f"{self._base_url}/scrape",
                    json={
                        "site": self.site,
                        "keyword": keyword.strip(),
                        "city": city,
                        "page": page,
                        "limit": limit,
                    },
                    headers=headers,
                )
                if resp.status_code != 200:
                    logger.warning(
                        "浏览器采集服务返回非 200", site=self.site, status=resp.status_code
                    )
                    return []
                jobs = resp.json().get("jobs") or []
                for j in jobs:
                    j.setdefault("source", self.name)
                return jobs
        except Exception as e:  # noqa: BLE001
            # 浏览器服务不可达、未启动、或站点未登录时静默降级
            logger.warning("浏览器采集服务调用失败", site=self.site, error=str(e)[:150])
            return []


class BossBrowserSource(_BrowserSource):
    """BOSS 直聘采集源（需登录：导入 Cookie 或扫码）。"""

    name = "BOSS直聘"
    site = "boss"


class ZhaopinBrowserSource(_BrowserSource):
    """智联招聘采集源（**免登录可用**）。

    城市码由浏览器服务放在 `jl` 路径段（见 `browser-service/app.py` 的
    ``ZHAOPIN_CITY_CODES``）；未收录的城市会被跳过而非静默采到外地岗。
    """

    name = "智联招聘"
    site = "zhaopin"


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
            ZhaopinBrowserSource(),  # 智联免登录可用
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
        """按关键词（可多个，逗号分隔）并行采集各源，合并去重 + 客户端筛选。

        多关键词时**逐词**采集（每个词仍是各源并行），而不是把所有词×源一次性并发——
        避免对招聘站点瞬时压力过大。合并后统一去重，因此同义词重复命中的岗位只留一条。
        """
        keywords = split_keywords(keyword)
        if not keywords:
            return {"sources": {}, "count": 0, "jobs": []}

        per_source: dict[str, dict[str, int]] = {}
        merged: list[dict[str, Any]] = []
        for term in keywords:
            results = await asyncio.gather(
                *[self._safe_fetch(s, term, page, limit) for s in self._sources]
            )
            for name, jobs in results:
                filtered = filter_jobs(
                    jobs, city=self._city, min_salary_k=self._min_salary_k
                )
                # 通用平台（猎聘/BOSS）排除大厂公司
                if name in self._GENERAL_BOARDS and self._exclude:
                    filtered = exclude_big_tech(filtered, self._exclude)
                slot = per_source.setdefault(name, {"raw": 0, "count": 0})
                slot["raw"] += len(jobs)
                slot["count"] += len(filtered)
                merged.extend(filtered)

        merged = self._dedup(merged)
        logger.info(
            "多源职位采集完成",
            keyword=keyword,
            keywords=keywords,
            sources=len(self._sources),
            total=len(merged),
        )
        return {"sources": per_source, "count": len(merged), "jobs": merged}

    async def _safe_fetch(
        self, source: Any, keyword: str, page: int, limit: int
    ) -> tuple[str, list[dict[str, Any]]]:
        name = getattr(source, "name", source.__class__.__name__)
        try:
            # 浏览器源需要城市参数（BOSS 用它拼查询词、智联用它取城市码，否则永远只采北京）
            if isinstance(source, _BrowserSource):
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
