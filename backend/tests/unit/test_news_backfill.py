"""科技资讯日报「指定日期回填」测试（``refresh(day=...)``）。

背景（2026-09-18 排查）：``refresh()`` 的目标日期曾经写死成「今天」，启动补跑也只补
今天，于是**历史缺失的日报永远补不回来**——09-17 的日报就是因为定时任务空转而
永久丢失。这里钉住三条契约：

1. 传 ``day`` 时：标签=该日期，内容窗口=该**自然日** ``[00:00, 次日 00:00)``；
2. 不传 ``day`` 时：窗口仍是「此刻往前 N 小时」的滚动窗口（**日常调度行为不变**）；
3. 已存在的日期在 ``force=False`` 下跳过，不重复烧 LLM。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.agents.news.service import NewsAgent


def _report() -> dict:
    """最小但结构完整的报告（够 storage 渲染 Markdown 与写索引）。"""
    return {
        "total_count": 1,
        "headline": {
            "title": "回填头条",
            "source": "s",
            "link": "http://x/headline",
            "importance": 9.5,
            "abstract": "回填头条摘要。",
            "analysis": "回填头条分析。",
            "attention": "关注建议。",
        },
        "sections": [
            {
                "category": "大模型",
                "summary": "回填分类总结。",
                "items": [
                    {
                        "title": "t",
                        "source": "s",
                        "link": "http://x/1",
                        "abstract": "回填条目摘要。",
                        "attention": "关注建议。",
                        "importance": 9.0,
                    }
                ],
            }
        ],
        "comprehensive": {"correlation": "关联分析。", "forecast": "预测。"},
    }


class _CapturingGenerator:
    """假 LLM 生成器：只记录调用参数，不打网络。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def generate(self, items, role, categories, **kwargs):  # noqa: ANN001, ANN003
        self.calls.append(kwargs)
        return _report()


class _EmptyFetcher:
    """假采集器：不联网，返回空列表（窗口断言只看传给生成器的参数）。"""

    async def fetch_all(self):
        return []


def _agent(tmp_path) -> NewsAgent:
    config = SimpleNamespace(
        rss_sources=[],
        keywords=[],
        exclude_keywords=[],
        categories=[SimpleNamespace(keywords=[])],
        report_dir=str(tmp_path / "news"),
        retention_days=70,
        llm_role="news_report",
        time_window_hours=24,
        timezone="Asia/Shanghai",
        min_items_per_category=1,
    )
    agent = NewsAgent(config, llm_factory=None)
    (tmp_path / "news").mkdir(parents=True, exist_ok=True)
    agent._fetcher = _EmptyFetcher()
    agent._web_fetcher = _EmptyFetcher()
    agent._generator = _CapturingGenerator()
    return agent


def _yesterday(agent: NewsAgent) -> str:
    """按 agent 自己时区取的昨天（避免测试在午夜附近因为时区差而变成未来日期）。"""
    today = datetime.strptime(agent._today_local(), "%Y-%m-%d")
    return (today - timedelta(days=1)).strftime("%Y-%m-%d")


class TestValidateDay:
    def test_accepts_canonical_past_date(self, tmp_path):
        agent = _agent(tmp_path)
        day = _yesterday(agent)
        assert agent.validate_day(day) == day

    def test_accepts_today(self, tmp_path):
        agent = _agent(tmp_path)
        today = agent._today_local()
        assert agent.validate_day(today) == today

    @pytest.mark.parametrize("bad", ["2026/09/17", "20260917", "2026-9-7", "", "不是日期"])
    def test_rejects_bad_format(self, tmp_path, bad):
        agent = _agent(tmp_path)
        with pytest.raises(ValueError):
            agent.validate_day(bad)

    def test_rejects_future_date(self, tmp_path):
        """未来日期不可能有资讯，生成出来只会是一份空报告。"""
        agent = _agent(tmp_path)
        today = datetime.strptime(agent._today_local(), "%Y-%m-%d")
        future = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        with pytest.raises(ValueError, match="未来"):
            agent.validate_day(future)


class TestBackfillRefresh:
    @pytest.mark.asyncio
    async def test_backfill_uses_natural_day_window_and_label(self, tmp_path):
        agent = _agent(tmp_path)
        day = _yesterday(agent)

        result = await agent.refresh(force=True, day=day)

        assert result["period"] == day
        assert result["path"].endswith(f"daily_{day}.md")
        # 内容窗口 = 该自然日（经 _span_text 渲染成「同一天 ~ 同一天」）
        assert agent._generator.calls[-1]["time_span_override"] == f"{day} ~ {day}"
        # 真的落盘，且进了索引（前端列表读索引）
        assert (tmp_path / "news" / f"daily_{day}.md").exists()
        assert [r["date"] for r in agent.list_reports()] == [day]

    @pytest.mark.asyncio
    async def test_scheduled_refresh_keeps_rolling_window(self, tmp_path):
        """不传 day（定时任务路径）：窗口必须是滚动小时窗口，不能被自然日窗口替换。"""
        agent = _agent(tmp_path)

        result = await agent.refresh(force=True)

        assert result["period"] == agent._today_local()
        assert agent._generator.calls[-1]["time_span_override"] is None

    @pytest.mark.asyncio
    async def test_existing_day_is_skipped_without_force(self, tmp_path):
        agent = _agent(tmp_path)
        day = _yesterday(agent)
        await agent.refresh(force=True, day=day)
        calls_before = len(agent._generator.calls)

        result = await agent.refresh(force=False, day=day)

        assert result["skipped"] is True
        assert len(agent._generator.calls) == calls_before  # 没有重复烧 LLM

    @pytest.mark.asyncio
    async def test_backfill_rejects_bad_day_before_doing_work(self, tmp_path):
        agent = _agent(tmp_path)
        with pytest.raises(ValueError):
            await agent.refresh(force=True, day="2026-9-7")
        assert agent._generator.calls == []  # 参数校验在最前面，没白跑采集
