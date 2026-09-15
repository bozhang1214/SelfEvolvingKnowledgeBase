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


# ============================================================
# search_id / 搜索历史（2026-09-15 新增）
# ============================================================

class TestSearchId:
    def test_search_id_is_stable_and_url_safe(self):
        key = job_cache.cache_key("u1", "第一层·解决方案售前", "北京", 0)
        sid = job_cache.make_search_id(key)
        assert sid == job_cache.make_search_id(key)  # 稳定
        assert len(sid) == 16
        assert sid.isalnum() and sid.isascii()  # URL 安全、无中文

    def test_search_id_differs_by_key(self):
        a = job_cache.make_search_id(job_cache.cache_key("u1", "Agent", "北京", 0))
        b = job_cache.make_search_id(job_cache.cache_key("u1", "Agent", "上海", 0))
        assert a != b

    def test_parse_key(self):
        meta = job_cache.parse_key("user_x|第一层·解决方案售前|北京|30")
        assert meta == {
            "user_id": "user_x",
            "keyword": "第一层·解决方案售前",
            "city": "北京",
            "min_salary_k": 30,
        }

    def test_parse_key_tolerates_garbage(self):
        assert job_cache.parse_key("onlyuser")["keyword"] == ""
        assert job_cache.parse_key("u|k|c|abc")["min_salary_k"] == 0

    def test_saved_entry_carries_search_id_and_count(self):
        key = job_cache.cache_key("u1", "Agent", "北京", 0)
        job_cache.save_cached_jobs(key, [_job("A"), _job("B")])
        entry = job_cache._load()[key]
        assert entry["search_id"] == job_cache.make_search_id(key)
        assert entry["count"] == 2


class TestListSearches:
    def test_lists_all_searches_sorted_desc(self):
        k1 = job_cache.cache_key("u1", "Agent", "北京", 0)
        job_cache.save_cached_jobs(k1, [_job("A")])
        time.sleep(0.01)
        k2 = job_cache.cache_key("u1", "技术型产品", "北京", 0)
        job_cache.save_cached_jobs(k2, [_job("B"), _job("C")])

        searches = job_cache.list_searches("u1")
        assert [s["keyword"] for s in searches] == ["技术型产品", "Agent"]
        assert searches[0]["count"] == 2
        assert searches[0]["expired"] is False
        assert searches[0]["has_jobs"] is True
        assert searches[0]["search_id"] == job_cache.make_search_id(k2)
        # 列表接口不返回 jobs（保持响应轻量）
        assert "jobs" not in searches[0]

    def test_lazy_cleanup_purges_jobs_but_keeps_entry(self, monkeypatch):
        k = job_cache.cache_key("u1", "Agent", "北京", 0)
        job_cache.save_cached_jobs(k, [_job("A"), _job("B")])
        monkeypatch.setattr(job_cache, "_TTL_SECONDS", -1)  # 视为过期

        searches = job_cache.list_searches("u1")
        assert len(searches) == 1
        assert searches[0]["expired"] is True
        assert searches[0]["has_jobs"] is False
        assert searches[0]["count"] == 2  # 条数保留

        # 条目仍在，但 jobs 已被清空
        entry = job_cache._load()[k]
        assert entry["jobs"] == []
        assert entry["count"] == 2

    def test_user_isolation(self):
        job_cache.save_cached_jobs(job_cache.cache_key("u2", "Agent", "北京", 0), [_job("X")])
        assert job_cache.list_searches("u1") == []


class TestGetBySearchId:
    def test_returns_jobs_and_meta(self):
        key = job_cache.cache_key("u1", "Agent", "北京", 30)
        job_cache.save_cached_jobs(key, [_job("A"), _job("B")])
        sid = job_cache.make_search_id(key)

        entry = job_cache.get_by_search_id("u1", sid)
        assert entry is not None
        assert entry["keyword"] == "Agent"
        assert entry["city"] == "北京"
        assert entry["min_salary_k"] == 30
        assert [j["title"] for j in entry["jobs"]] == ["A", "B"]
        assert entry["expired"] is False

    def test_unknown_id_returns_none(self):
        assert job_cache.get_by_search_id("u1", "deadbeefdeadbeef") is None

    def test_other_user_cannot_read(self):
        key = job_cache.cache_key("u2", "Agent", "北京", 0)
        job_cache.save_cached_jobs(key, [_job("X")])
        assert job_cache.get_by_search_id("u1", job_cache.make_search_id(key)) is None

    def test_expired_returns_meta_without_jobs(self, monkeypatch):
        key = job_cache.cache_key("u1", "Agent", "北京", 0)
        job_cache.save_cached_jobs(key, [_job("A")])
        monkeypatch.setattr(job_cache, "_TTL_SECONDS", -1)

        entry = job_cache.get_by_search_id("u1", job_cache.make_search_id(key))
        assert entry is not None
        assert entry["expired"] is True
        assert entry["jobs"] == []


class TestExpiredSearchIds:
    def test_only_expired_returned(self):
        k1 = job_cache.cache_key("u1", "旧", "北京", 0)
        k2 = job_cache.cache_key("u1", "新", "北京", 0)
        job_cache.save_cached_jobs(k1, [_job("A")])
        job_cache.save_cached_jobs(k2, [_job("B")])
        # 直接把 k1 的 ts 改老（超过 14 天 TTL）
        data = job_cache._load()
        data[k1]["ts"] = time.time() - 30 * 24 * 3600
        job_cache._save(data)

        ids = job_cache.expired_search_ids("u1")
        assert ids == [job_cache.make_search_id(k1)]
