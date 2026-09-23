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
