"""职位采集缓存（job_cache）单元测试。

覆盖：
- cache_key 构造
- save / get 往返
- get_latest_cached 取 ts 最新的一组
- list_all_cached 返回用户全部未过期集合，按 ts 倒序，含 count/jobs
- TTL 过期后不可见
- 用户隔离
"""
from __future__ import annotations

import time

import pytest

from app.agents.job import job_cache


@pytest.fixture(autouse=True)
def _isolate_storage(tmp_path, monkeypatch):
    """把存储文件重定向到临时目录，测试间互不污染。"""
    monkeypatch.setattr(job_cache, "_FILE", tmp_path / "job_cache.json")


def _job(title: str, company: str = "某公司") -> dict:
    return {
        "job_id": title,
        "title": title,
        "company": company,
        "salary": "",
        "city": "北京",
        "job_url": "",
        "jd_text": "",
    }


class TestCacheKey:
    def test_key_format(self):
        assert job_cache.cache_key("u1", "Agent", "北京", 50) == "u1|Agent|北京|50"


class TestSaveAndGet:
    def test_roundtrip(self):
        key = job_cache.cache_key("u1", "Agent", "北京", 0)
        job_cache.save_cached_jobs(key, [_job("A"), _job("B")])
        jobs = job_cache.get_cached_jobs(key)
        assert jobs is not None
        assert [j["title"] for j in jobs] == ["A", "B"]

    def test_missing_key_returns_none(self):
        assert job_cache.get_cached_jobs("u1|不存在|北京|0") is None

    def test_max_jobs_cap(self, monkeypatch):
        monkeypatch.setattr(job_cache, "_MAX_JOBS", 3)
        key = job_cache.cache_key("u1", "Agent", "北京", 0)
        job_cache.save_cached_jobs(key, [_job(f"J{i}") for i in range(10)])
        assert len(job_cache.get_cached_jobs(key) or []) == 3


class TestLatestCached:
    def test_picks_most_recent(self):
        k1 = job_cache.cache_key("u1", "Agent", "北京", 0)
        k2 = job_cache.cache_key("u1", "解决方案/售前", "北京", 0)
        job_cache.save_cached_jobs(k1, [_job("旧")])
        time.sleep(0.01)
        job_cache.save_cached_jobs(k2, [_job("新")])
        latest = job_cache.get_latest_cached("u1")
        assert latest is not None
        assert latest["keyword"] == "解决方案/售前"
        assert [j["title"] for j in latest["jobs"]] == ["新"]

    def test_none_when_no_cache(self):
        assert job_cache.get_latest_cached("u1") is None

    def test_user_isolation(self):
        job_cache.save_cached_jobs(job_cache.cache_key("u2", "Agent", "北京", 0), [_job("别人的")])
        assert job_cache.get_latest_cached("u1") is None


class TestListAllCached:
    def test_returns_all_sets_sorted_by_ts_desc(self):
        k1 = job_cache.cache_key("u1", "Agent", "北京", 0)
        job_cache.save_cached_jobs(k1, [_job("A1"), _job("A2")])
        time.sleep(0.01)
        k2 = job_cache.cache_key("u1", "技术型产品", "北京", 0)
        job_cache.save_cached_jobs(k2, [_job("B1")])

        caches = job_cache.list_all_cached("u1")
        assert len(caches) == 2
        # ts 倒序：后存的在前
        assert caches[0]["keyword"] == "技术型产品"
        assert caches[1]["keyword"] == "Agent"
        # count 与 jobs 一致
        assert caches[1]["count"] == 2
        assert len(caches[1]["jobs"]) == 2
        assert caches[0]["city"] == "北京"

    def test_empty_for_unknown_user(self):
        assert job_cache.list_all_cached("nobody") == []

    def test_expired_sets_excluded(self, monkeypatch):
        k = job_cache.cache_key("u1", "Agent", "北京", 0)
        job_cache.save_cached_jobs(k, [_job("A")])
        # 把 TTL 设成负数，模拟全部过期
        monkeypatch.setattr(job_cache, "_TTL_SECONDS", -1)
        assert job_cache.list_all_cached("u1") == []
        assert job_cache.get_cached_jobs(k) is None
