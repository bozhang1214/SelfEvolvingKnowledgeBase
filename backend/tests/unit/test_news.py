"""
资讯日报 Agent 单元测试

覆盖：
- NewsFilter：关键词 / 排除词 / 去重 / 时效窗口 / 空关键词不过滤
- DailyReportGenerator._parse_json：裸 JSON / markdown 围栏 / 前后多余文本 / 非法输入
- NewsStorage：保存 / 列表 / 读取
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.agents.news.filter import NewsFilter
from app.agents.news.generator import DailyReportGenerator
from app.agents.news.rss_fetcher import NewsItem
from app.agents.news.storage import NewsStorage


def _item(title, link="", published="", summary=""):
    return NewsItem(title=title, link=link, source="test.com", summary=summary, published=published)


class TestNewsFilter:
    def test_keyword_match(self):
        f = NewsFilter(keywords=["AI", "Agent"])
        items = [_item("AI 新模型发布"), _item("菜谱分享"), _item("Agent 框架更新")]
        out = f.filter(items)
        assert [i.title for i in out] == ["AI 新模型发布", "Agent 框架更新"]

    def test_exclude_keyword(self):
        f = NewsFilter(keywords=["AI"], exclude_keywords=["广告"])
        items = [_item("AI 新模型发布"), _item("AI 课程广告")]
        out = f.filter(items)
        assert [i.title for i in out] == ["AI 新模型发布"]

    def test_dedup_by_link(self):
        f = NewsFilter(keywords=[])
        items = [_item("同一篇", link="http://x/1"), _item("同一篇", link="http://x/1")]
        assert len(f.filter(items)) == 1

    def test_time_window_filters_stale(self):
        f = NewsFilter(keywords=[], time_window_hours=24)
        fresh = datetime.now(timezone.utc) - timedelta(hours=1)
        stale = datetime.now(timezone.utc) - timedelta(hours=48)
        items = [
            _item("新", published=fresh.isoformat()),
            _item("旧", published=stale.isoformat()),
        ]
        out = f.filter(items)
        assert [i.title for i in out] == ["新"]

    def test_empty_keywords_keep_all(self):
        f = NewsFilter(keywords=[])
        assert len(f.filter([_item("任意"), _item("内容")])) == 2


class TestParseJson:
    def test_plain_json(self):
        result = DailyReportGenerator._parse_json('{"date":"2026-09-02","total_count":1}')
        assert result["date"] == "2026-09-02"

    def test_markdown_fenced(self):
        raw = '```json\n{"date":"2026-09-02"}\n```'
        assert DailyReportGenerator._parse_json(raw)["date"] == "2026-09-02"

    def test_surrounding_text(self):
        raw = '好的，日报如下：{"date":"2026-09-02"} 以上。'
        assert DailyReportGenerator._parse_json(raw)["date"] == "2026-09-02"

    def test_invalid_returns_error(self):
        result = DailyReportGenerator._parse_json("这不是 JSON")
        assert "error" in result


class TestNewsStorage:
    def test_save_list_read(self, tmp_path):
        store = NewsStorage(str(tmp_path), retention_days=70)
        day = "2026-09-02"
        report = {
            "total_count": 1,
            "headline": {"title": "头条", "summary": "摘要", "impact": "影响"},
            "sections": [{"category": "大模型", "items": [{"title": "t", "source": "s", "one_liner": "l"}]}],
        }
        path = store.save_daily(day, report)
        assert path.endswith(f"daily_{day}.md")

        reports = store.list_reports()
        assert len(reports) == 1
        assert reports[0]["date"] == day

        read = store.read_report(day)
        assert read is not None
        assert "头条" in read["markdown"]
