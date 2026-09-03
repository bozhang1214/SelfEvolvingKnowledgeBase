"""
科技资讯 Agent 单元测试

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

    def test_invalid_returns_empty(self):
        result = DailyReportGenerator._parse_json("这不是 JSON")
        assert result == {}


class TestNewsGeneratorClassify:
    def test_classify_by_keyword(self):
        from app.core.config import CategoryConfig

        gen = object.__new__(DailyReportGenerator)
        cats = [
            CategoryConfig(name="Android", keywords=["android", "安卓"]),
            CategoryConfig(name="前端", keywords=["react", "vue", "前端"]),
        ]
        items = [
            {"title": "Android 15 发布", "summary": ""},
            {"title": "React 19 特性", "summary": "前端框架更新"},
            {"title": "安卓性能优化", "summary": ""},
        ]
        classified = DailyReportGenerator._classify(gen, items, cats)
        assert [it["title"] for it in classified["Android"]] == [
            "Android 15 发布",
            "安卓性能优化",
        ]
        assert [it["title"] for it in classified["前端"]] == ["React 19 特性"]

    def test_classify_multi_assign(self):
        # 一条资讯同时命中多个大类时，应同时归入多个大类（多归属）
        from app.core.config import CategoryConfig

        gen = object.__new__(DailyReportGenerator)
        cats = [
            CategoryConfig(name="大模型", keywords=["大模型", "模型"]),
            CategoryConfig(name="前端", keywords=["react", "前端"]),
        ]
        items = [{"title": "React 19 驱动大模型前端应用", "summary": ""}]
        classified = DailyReportGenerator._classify(gen, items, cats)
        assert [it["title"] for it in classified["大模型"]] == [
            "React 19 驱动大模型前端应用"
        ]
        assert [it["title"] for it in classified["前端"]] == [
            "React 19 驱动大模型前端应用"
        ]

    def test_classify_unmatched_dropped(self):
        from app.core.config import CategoryConfig

        gen = object.__new__(DailyReportGenerator)
        cats = [CategoryConfig(name="鸿蒙", keywords=["harmony", "鸿蒙"])]
        items = [{"title": "无关资讯", "summary": ""}]
        classified = DailyReportGenerator._classify(gen, items, cats)
        assert classified["鸿蒙"] == []


class TestNewsStorage:
    def test_save_list_read(self, tmp_path):
        store = NewsStorage(str(tmp_path), retention_days=70)
        day = "2026-09-02"
        report = {
            "total_count": 1,
            "headline": {
                "title": "头条标题",
                "source": "s",
                "link": "http://x/headline",
                "importance": 9.5,
                "abstract": "头条摘要。",
                "analysis": "头条深度分析：为什么这是今天最重要的一条。",
                "attention": "头条关注建议。",
            },
            "sections": [
                {
                    "category": "大模型",
                    "summary": "本周大模型总结与预测。",
                    "items": [
                        {
                            "title": "t",
                            "source": "s",
                            "link": "http://x/1",
                            "abstract": "这是一段 150~200 字的条目摘要。",
                            "attention": "关注建议：值得关注并跟进。",
                            "importance": 9.0,
                        }
                    ],
                }
            ],
            "comprehensive": {
                "correlation": "大模型与Agent的关联分析。",
                "forecast": "未来半月预计有重大发布会。",
            },
        }
        path = store.save_daily(day, report)
        assert path.endswith(f"daily_{day}.md")

        reports = store.list_reports()
        assert len(reports) == 1
        assert reports[0]["date"] == day
        assert reports[0]["headline"] == "头条标题"

        read = store.read_report(day)
        assert read is not None
        assert "🔥 头条" in read["markdown"]
        assert "头条标题" in read["markdown"]
        assert "头条深度分析：为什么这是今天最重要的一条。" in read["markdown"]
        assert "总结与预测" in read["markdown"]
        assert "⭐" in read["markdown"]
        assert "摘要：" in read["markdown"]
        assert "这是一段 150~200 字的条目摘要" in read["markdown"]
        assert "关注：" in read["markdown"]
        assert "关注建议：值得关注并跟进。" in read["markdown"]
        assert "🔮 综合分析" in read["markdown"]
        assert "关联分析" in read["markdown"]
        assert "大模型与Agent的关联分析。" in read["markdown"]
        assert "趋势预测" in read["markdown"]
        assert "未来半月预计有重大发布会。" in read["markdown"]
