"""职位报告存档（archive）单元测试。

覆盖：
- save_report / list_reports / get_report / delete_report 基本读写
- **批量报告去重**：同「用户 + 关键词 + 城市」只保留最新一份（含落盘文件被删）
- 单职位报告不参与去重
- delete_report 返回元信息（含 search_id）供上层同步清缓存
- 旧索引缺 keyword 时，删除能回退从报告 JSON 补齐
- dedupe_batch_reports 一次性清理
"""
from __future__ import annotations

import json

import pytest

from app.agents.job import archive


@pytest.fixture(autouse=True)
def _isolate_storage(tmp_path, monkeypatch):
    """把存档目录重定向到临时目录。"""
    monkeypatch.setattr(archive, "_DIR", tmp_path / "job_reports")
    monkeypatch.setattr(archive, "_INDEX_FILE", tmp_path / "job_reports" / "index.json")


def _batch_report(keyword: str = "Agent", city: str = "北京", job_id: str = "j1") -> dict:
    return {
        "analyzed_at": "2026-09-15T00:00:00+00:00",
        "analyzed_at_ts": 1.0,
        "keyword": keyword,
        "city": city,
        "job_count": 1,
        "jobs": [{"job_id": job_id, "title": "职位", "company": "公司"}],
    }


class TestBasicCrud:
    def test_save_and_list(self):
        rid = archive.save_report("u1", "batch", "批量分析 · Agent（1 个职位）", _batch_report())
        reports = archive.list_reports("u1")
        assert len(reports) == 1
        assert reports[0]["id"] == rid
        assert reports[0]["type"] == "batch"

    def test_get_report_returns_markdown_and_json(self):
        rid = archive.save_report("u1", "batch", "标题", _batch_report())
        detail = archive.get_report("u1", rid)
        assert detail is not None
        assert detail["markdown"].startswith("# 标题")
        assert detail["report"]["keyword"] == "Agent"

    def test_get_report_other_user_returns_none(self):
        rid = archive.save_report("u1", "batch", "标题", _batch_report())
        assert archive.get_report("u2", rid) is None

    def test_delete_returns_meta_and_removes_files(self):
        rid = archive.save_report("u1", "batch", "标题", _batch_report(), search_id="sid-a")
        meta = archive.delete_report("u1", rid)
        assert meta is not None
        assert meta["search_id"] == "sid-a"
        assert archive.get_report("u1", rid) is None
        assert not (archive._DIR / f"{rid}.md").exists()
        assert not (archive._DIR / f"{rid}.json").exists()

    def test_delete_missing_returns_none(self):
        assert archive.delete_report("u1", "nope") is None


class TestBatchDedupe:
    def test_same_keyword_city_keeps_only_latest(self):
        old = archive.save_report("u1", "batch", "旧", _batch_report(job_id="old"))
        new = archive.save_report("u1", "batch", "新", _batch_report(job_id="new"))

        reports = archive.list_reports("u1")
        assert [r["id"] for r in reports] == [new]  # 只剩最新
        assert archive.get_report("u1", old) is None
        assert not (archive._DIR / f"{old}.md").exists()  # 旧文件已删

    def test_different_city_not_deduped(self):
        a = archive.save_report("u1", "batch", "北京", _batch_report(city="北京"))
        b = archive.save_report("u1", "batch", "上海", _batch_report(city="上海"))
        ids = {r["id"] for r in archive.list_reports("u1")}
        assert ids == {a, b}

    def test_different_keyword_not_deduped(self):
        a = archive.save_report("u1", "batch", "A", _batch_report(keyword="A"))
        b = archive.save_report("u1", "batch", "B", _batch_report(keyword="B"))
        assert len(archive.list_reports("u1")) == 2
        assert {r["id"] for r in archive.list_reports("u1")} == {a, b}

    def test_other_user_not_affected(self):
        mine = archive.save_report("u1", "batch", "M", _batch_report())
        theirs = archive.save_report("u2", "batch", "T", _batch_report())
        assert [r["id"] for r in archive.list_reports("u1")] == [mine]
        assert [r["id"] for r in archive.list_reports("u2")] == [theirs]

    def test_single_reports_never_deduped(self):
        a = archive.save_report("u1", "single", "岗位A", {"job_analysis": {"position": "A"}})
        b = archive.save_report("u1", "single", "岗位B", {"job_analysis": {"position": "B"}})
        assert {r["id"] for r in archive.list_reports("u1")} == {a, b}


class TestDedupeExisting:
    def test_cleans_existing_duplicates(self):
        """模拟历史遗留：手工写入 3 条同组报告，dedupe 后只留最新。"""
        ids = [
            archive.save_report("u1", "batch", "1", _batch_report(job_id=f"j{i}"))
            for i in range(3)
        ]
        # save_report 已自带去重，这里构造"历史遗留"：直接改索引绕过
        index = archive._load_index()
        assert len(index) == 1  # 已被自动去重

        # 构造遗留脏数据（3 条同组）以验证一次性清理
        archive._save_index([
            {"id": "a1", "user_id": "u1", "type": "batch", "title": "1",
             "created_at": "2026-09-13T00:00:00+00:00", "keyword": "Agent", "city": "北京"},
            {"id": "a2", "user_id": "u1", "type": "batch", "title": "2",
             "created_at": "2026-09-14T00:00:00+00:00", "keyword": "Agent", "city": "北京"},
            {"id": "a3", "user_id": "u1", "type": "batch", "title": "3",
             "created_at": "2026-09-15T00:00:00+00:00", "keyword": "Agent", "city": "北京"},
            {"id": "a4", "user_id": "u1", "type": "batch", "title": "4",
             "created_at": "2026-09-15T00:00:00+00:00", "keyword": "另一个", "city": "北京"},
        ])
        res = archive.dedupe_batch_reports("u1")
        assert res["removed"] == 2
        remaining = {r["id"] for r in archive.list_reports("u1")}
        # 索引按时间倒序，首个即最新 → 组内保留 a3
        assert remaining == {"a3", "a4"}
        assert ids  # 保持引用，避免 lint 未使用


class TestLegacyKeywordFallback:
    def test_delete_backfills_keyword_from_json(self):
        """旧索引没有 keyword 时，删除应能从报告 JSON 读出关键词。"""
        rid = archive.save_report("u1", "batch", "标题", _batch_report(keyword="Agent"))
        # 模拟旧索引：抹掉 keyword / city
        index = archive._load_index()
        for x in index:
            x.pop("keyword", None)
            x.pop("city", None)
        archive._save_index(index)

        meta = archive.delete_report("u1", rid)
        assert meta is not None
        assert meta["keyword"] == "Agent"  # 已从 JSON 补齐
        assert meta["city"] == "北京"

    def test_index_file_is_valid_json(self):
        archive.save_report("u1", "batch", "标题", _batch_report())
        raw = json.loads(archive._INDEX_FILE.read_text(encoding="utf-8"))
        assert isinstance(raw, list) and raw
