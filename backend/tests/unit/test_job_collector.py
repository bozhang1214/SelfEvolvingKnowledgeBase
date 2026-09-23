"""JobCollector 多关键词采集单测（一条搜索覆盖同义岗位族）。"""
from __future__ import annotations

from typing import Any

import pytest

from app.agents.job.collector import JobCollector, split_keywords


class _StubSource:
    """按关键词返回预置职位的采集源桩。"""

    def __init__(self, name: str, jobs_by_keyword: dict[str, list[dict[str, Any]]]) -> None:
        self.name = name
        self._jobs = jobs_by_keyword
        self.calls: list[str] = []

    async def fetch(self, keyword: str, page: int = 0, limit: int = 20) -> list[dict[str, Any]]:
        self.calls.append(keyword)
        return [dict(j) for j in self._jobs.get(keyword, [])]


def _job(title: str, url: str, company: str = "某公司", salary: str = "40-60k") -> dict[str, Any]:
    return {
        "title": title,
        "company": company,
        "salary": salary,
        "city": "北京",
        "job_url": url,
        "jd_text": f"{title} 的 JD",
        "source": "桩",
    }


def _collector(sources: list[Any]) -> JobCollector:
    c = JobCollector(city="北京", min_salary_k=0)
    c._sources = sources  # 注入桩，避免真实网络采集
    return c


# ---------------- split_keywords ----------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("FDE", ["FDE"]),
        ("  FDE  ", ["FDE"]),
        ("", []),
        ("   ", []),
        (None, []),
        ("FDE,前沿部署", ["FDE", "前沿部署"]),
        ("FDE，前沿部署", ["FDE", "前沿部署"]),  # 中文逗号
        ("FDE, 前沿部署 , 前向部署", ["FDE", "前沿部署", "前向部署"]),
        ("FDE,,前沿部署,", ["FDE", "前沿部署"]),  # 空项丢弃
        ("FDE,FDE,前沿部署", ["FDE", "前沿部署"]),  # 去重且保序
    ],
)
def test_split_keywords(raw, expected):
    assert split_keywords(raw) == expected


def test_split_keywords_keeps_pipe_as_literal():
    """``|`` 是存储键分隔符，绝不能被当成关键词分隔符吞掉。"""
    assert split_keywords("a|b") == ["a|b"]


# ---------------- fetch_all ----------------


@pytest.mark.asyncio
async def test_fetch_all_single_keyword_unchanged():
    src = _StubSource("甲", {"FDE": [_job("FDE 工程师", "u1")]})
    result = await _collector([src]).fetch_all(keyword="FDE")

    assert src.calls == ["FDE"]
    assert result["count"] == 1
    assert result["sources"]["甲"] == {"raw": 1, "count": 1}


@pytest.mark.asyncio
async def test_fetch_all_multi_keyword_merges_and_dedups():
    """多关键词：逐词采集，跨词重复的岗位只保留一条（按 job_url 去重）。"""
    shared = _job("FDE 工程师", "u-shared")
    src = _StubSource(
        "甲",
        {
            "FDE": [shared, _job("FDE 专家", "u-fde")],
            "前沿部署": [shared, _job("前沿部署工程师", "u-frontier")],
            "前向部署": [_job("前向部署专家", "u-forward")],
        },
    )
    result = await _collector([src]).fetch_all(keyword="FDE,前沿部署,前向部署")

    assert src.calls == ["FDE", "前沿部署", "前向部署"]
    assert result["count"] == 4, "u-shared 应被去重"
    assert {j["job_url"] for j in result["jobs"]} == {
        "u-shared",
        "u-fde",
        "u-frontier",
        "u-forward",
    }


@pytest.mark.asyncio
async def test_fetch_all_multi_keyword_aggregates_per_source():
    src = _StubSource(
        "甲",
        {"FDE": [_job("a", "u1")], "前沿部署": [_job("b", "u2"), _job("c", "u3")]},
    )
    result = await _collector([src]).fetch_all(keyword="FDE,前沿部署")

    # 计数是各关键词的累加值（采集量口径），去重只作用于合并后的 jobs
    assert result["sources"]["甲"] == {"raw": 3, "count": 3}
    assert result["count"] == 3


@pytest.mark.asyncio
async def test_fetch_all_multi_keyword_applies_city_and_salary_filter():
    src = _StubSource(
        "甲",
        {
            "FDE": [
                _job("北京岗", "u-bj"),
                {**_job("上海岗", "u-sh"), "city": "上海"},
            ],
            "前沿部署": [
                {**_job("低薪岗", "u-low"), "salary": "10-15k"},
                _job("正常岗", "u-ok"),
            ],
        },
    )
    c = JobCollector(city="北京", min_salary_k=30)
    c._sources = [src]
    result = await c.fetch_all(keyword="FDE,前沿部署")

    assert {j["job_url"] for j in result["jobs"]} == {"u-bj", "u-ok"}


@pytest.mark.asyncio
async def test_fetch_all_blank_keyword_returns_empty():
    src = _StubSource("甲", {})
    result = await _collector([src]).fetch_all(keyword=" , , ")

    assert result == {"sources": {}, "count": 0, "jobs": []}
    assert src.calls == [], "空关键词不应触发任何采集"


# ---------------- 浏览器采集源（BOSS / 智联）----------------


class _FakeResp:
    def __init__(self, status_code: int = 200, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> Any:
        return self._payload


class _FakeAsyncClient:
    """httpx.AsyncClient 的最小替身，记录最后一次 POST。"""

    last: dict[str, Any] = {}

    def __init__(self, resp: _FakeResp | None = None, exc: Exception | None = None, **kw: Any) -> None:
        self._resp = resp or _FakeResp()
        self._exc = exc

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    async def post(self, url: str, json: Any = None, headers: Any = None) -> _FakeResp:
        _FakeAsyncClient.last = {"url": url, "json": json, "headers": headers}
        if self._exc:
            raise self._exc
        return self._resp


def _patch_client(monkeypatch, resp=None, exc=None):
    from app.agents.job import collector as col

    monkeypatch.setattr(
        col.httpx, "AsyncClient", lambda **kw: _FakeAsyncClient(resp=resp, exc=exc)
    )
    _FakeAsyncClient.last = {}


def test_browser_sources_config():
    """智联免登录、BOSS 需登录；两者只差 site/name，共用同一基类。"""
    from app.agents.job.collector import (
        BossBrowserSource,
        ZhaopinBrowserSource,
        _BrowserSource,
    )

    assert issubclass(BossBrowserSource, _BrowserSource)
    assert issubclass(ZhaopinBrowserSource, _BrowserSource)
    assert BossBrowserSource.site == "boss"
    assert BossBrowserSource.name == "BOSS直聘"
    assert ZhaopinBrowserSource.site == "zhaopin"
    assert ZhaopinBrowserSource.name == "智联招聘"


@pytest.mark.asyncio
async def test_zhaopin_source_posts_site_and_fills_source(monkeypatch):
    from app.agents.job.collector import ZhaopinBrowserSource

    _patch_client(monkeypatch, resp=_FakeResp(200, {"jobs": [{"title": "FDE 工程师"}]}))
    jobs = await ZhaopinBrowserSource().fetch(keyword="FDE", city="北京")

    assert [j["title"] for j in jobs] == ["FDE 工程师"]
    assert jobs[0]["source"] == "智联招聘"  # 站点没给 source 时由客户端补
    sent = _FakeAsyncClient.last["json"]
    assert sent["site"] == "zhaopin"
    assert sent["city"] == "北京"
    assert sent["keyword"] == "FDE"


@pytest.mark.asyncio
async def test_boss_source_posts_its_own_site(monkeypatch):
    from app.agents.job.collector import BossBrowserSource

    _patch_client(monkeypatch, resp=_FakeResp(200, {"jobs": [{"title": "x"}]}))
    await BossBrowserSource().fetch(keyword="Agent", city="北京")

    assert _FakeAsyncClient.last["json"]["site"] == "boss"


@pytest.mark.asyncio
async def test_zhaopin_source_skips_blank_keyword(monkeypatch):
    from app.agents.job.collector import ZhaopinBrowserSource

    _patch_client(monkeypatch, resp=_FakeResp(200, {"jobs": [{"title": "x"}]}))
    assert await ZhaopinBrowserSource().fetch(keyword="   ") == []
    assert _FakeAsyncClient.last == {}, "空关键词不应发起请求"


@pytest.mark.asyncio
async def test_zhaopin_source_degrades_on_non_200(monkeypatch):
    from app.agents.job.collector import ZhaopinBrowserSource

    _patch_client(monkeypatch, resp=_FakeResp(404, {"detail": "站点 zhaopin 未注册采集器"}))
    assert await ZhaopinBrowserSource().fetch(keyword="FDE") == []


@pytest.mark.asyncio
async def test_zhaopin_source_degrades_on_exception(monkeypatch):
    """浏览器服务不可达时静默降级，不把异常抛给采集编排。"""
    from app.agents.job.collector import ZhaopinBrowserSource

    _patch_client(monkeypatch, exc=RuntimeError("connection refused"))
    assert await ZhaopinBrowserSource().fetch(keyword="FDE") == []


def test_load_sources_registers_zhaopin():
    from app.agents.job.collector import _load_sources

    names = [getattr(s, "name", s.__class__.__name__) for s in _load_sources()]
    assert "智联招聘" in names
    assert "BOSS直聘" in names
