"""报告缓存 v2（按 search_id 存）单元测试。

覆盖：
- save / get（按 search_id）/ 取最新一份
- TTL 过期
- v1 旧格式（{user_id: report}）兼容读取
- 删除（单个 search_id / 整个用户）/ 按 search_id 删除（过期清理用）
"""
from __future__ import annotations

import json
import time

import pytest

from app.agents.job import market


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    """把报告缓存重定向到临时文件，测试间互不污染。"""
    monkeypatch.setattr(market, "_CACHE_FILE", tmp_path / "job_market_report.json")


def _report(keyword: str = "Agent", ts: float | None = None, job_id: str = "j1") -> dict:
    return {
        "analyzed_at": "2026-09-15T00:00:00+00:00",
        "analyzed_at_ts": ts if ts is not None else time.time(),
        "keyword": keyword,
        "city": "北京",
        "job_count": 1,
        "jobs": [{"job_id": job_id, "title": "职位", "company": "公司"}],
    }


class TestSaveAndGet:
    def test_roundtrip_by_search_id(self):
        market.save_report_cache("u1", "sid-a", "Agent", "北京", 0, _report("Agent"))
        got = market.get_cached_report("u1", "sid-a")
        assert got is not None
        assert got["keyword"] == "Agent"

    def test_unknown_search_id_returns_none(self):
        market.save_report_cache("u1", "sid-a", "Agent", "北京", 0, _report())
        assert market.get_cached_report("u1", "sid-b") is None

    def test_latest_ignores_other_users(self):
        market.save_report_cache("u2", "sid-x", "别人的", "北京", 0, _report("别人的"))
        assert market.get_cached_report("u1") is None

    def test_latest_picks_max_ts_for_user(self):
        market.save_report_cache("u1", "sid-old", "旧", "北京", 0, _report("旧", ts=time.time() - 100))
        market.save_report_cache("u1", "sid-new", "新", "北京", 0, _report("新", ts=time.time()))

        assert market.get_cached_report("u1")["keyword"] == "新"  # 不传 search_id → 最新
        assert market.get_cached_report("u1", "sid-old")["keyword"] == "旧"  # 指定 → 精确

    def test_expired_report_invisible(self):
        market.save_report_cache(
            "u1", "sid-a", "Agent", "北京", 0, _report(ts=time.time() - 30 * 24 * 3600)
        )
        assert market.get_cached_report("u1", "sid-a") is None
        assert market.get_cached_report("u1") is None


class TestLegacyCompat:
    def test_reads_v1_format(self, tmp_path, monkeypatch):
        """v1 = {user_id: report}（无 __v 标记）应仍可读。"""
        legacy_file = tmp_path / "job_market_report.json"
        legacy_file.write_text(
            json.dumps({"u1": _report("旧格式")}, ensure_ascii=False), encoding="utf-8"
        )
        monkeypatch.setattr(market, "_CACHE_FILE", legacy_file)

        assert market.get_cached_report("u1")["keyword"] == "旧格式"
        # 但按 search_id 查不到（v1 没有该维度）
        assert market.get_cached_report("u1", "sid-a") is None

    def test_v1_preserved_after_v2_write(self, tmp_path, monkeypatch):
        """升级后写 v2，不应丢掉 v1 旧条目。"""
        legacy_file = tmp_path / "job_market_report.json"
        legacy_file.write_text(json.dumps({"u1": _report("旧格式")}, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(market, "_CACHE_FILE", legacy_file)

        market.save_report_cache("u1", "sid-a", "新", "北京", 0, _report("新"))
        raw = json.loads(legacy_file.read_text(encoding="utf-8"))
        assert raw["__v"] == 2
        assert raw["__legacy"]["u1"]["keyword"] == "旧格式"
        assert market.get_cached_report("u1")["keyword"] == "新"  # v2 优先


class TestDelete:
    def test_delete_single_search(self):
        market.save_report_cache("u1", "sid-a", "A", "北京", 0, _report("A"))
        market.save_report_cache("u1", "sid-b", "B", "北京", 0, _report("B"))

        assert market.delete_report("u1", "sid-a") is True
        assert market.get_cached_report("u1", "sid-a") is None
        assert market.get_cached_report("u1", "sid-b") is not None  # 另一个还在

    def test_delete_whole_user(self):
        market.save_report_cache("u1", "sid-a", "A", "北京", 0, _report("A"))
        market.save_report_cache("u1", "sid-b", "B", "北京", 0, _report("B"))
        market.save_report_cache("u2", "sid-c", "C", "北京", 0, _report("C"))

        assert market.delete_report("u1") is True
        assert market.get_cached_report("u1", "sid-a") is None
        assert market.get_cached_report("u1", "sid-b") is None
        assert market.get_cached_report("u2", "sid-c") is not None  # 不影响他人

    def test_delete_missing_returns_false(self):
        assert market.delete_report("u1", "nope") is False
        assert market.delete_report("u1") is False

    def test_delete_by_search_id(self):
        market.save_report_cache("u1", "sid-a", "A", "北京", 0, _report("A"))
        assert market.delete_report_by_search_id("sid-a") is True
        assert market.delete_report_by_search_id("sid-a") is False
        assert market.get_cached_report("u1", "sid-a") is None
