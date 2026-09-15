"""周报/月报的时间窗口必须按**自然周期**算，而不是「生成时刻往前 N 小时」。

背景（owner 报的 bug）：周二点「生成周报」，期号写的是上周一（正确），但内容覆盖的是
**最近 7 天（含本周）**—— 因为窗口来自 `time_window_hours`，只能表达「相对当前时刻
往前 N 小时」。这里锁住修复后的行为：

- `weekly`：按标签（上周一）取 `[该日 00:00, +7 天)`（本地时区 → UTC，左闭右开）
- `monthly`：按标签（YYYY-MM）取 `[该月 1 日 00:00, 次月 1 日)`
- `daily` 仍走小时窗口（不受影响）
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.agents.news.filter import NewsFilter
from app.agents.news.rss_fetcher import NewsItem
from app.agents.news.service import NewsAgent


def _item(published: str, link: str = "u") -> NewsItem:
    return NewsItem(title="标题", link=link, source="test.com", published=published)


# ---------- 自然周期窗口 ----------


def test_weekly_window_is_natural_week() -> None:
    """上周一标签 → 上周一 00:00 到本周一 00:00（CST = UTC+8）。"""
    window = NewsAgent._period_window("weekly", "2026-09-07", "Asia/Shanghai")
    assert window is not None
    since, until = window
    # 2026-09-07 00:00 CST == 2026-09-06 16:00 UTC
    # 返回本地时区（CST），用瞬时比较
    assert since.astimezone(timezone.utc) == datetime(2026, 9, 6, 16, 0, tzinfo=timezone.utc)
    assert until.astimezone(timezone.utc) == datetime(2026, 9, 13, 16, 0, tzinfo=timezone.utc)
    assert until - since == timedelta(days=7)


def test_monthly_window_is_natural_month() -> None:
    window = NewsAgent._period_window("monthly", "2026-08", "Asia/Shanghai")
    assert window is not None
    since, until = window
    assert since.astimezone(timezone.utc) == datetime(2026, 7, 31, 16, 0, tzinfo=timezone.utc)   # 8/1 00:00 CST
    assert until.astimezone(timezone.utc) == datetime(2026, 8, 31, 16, 0, tzinfo=timezone.utc)   # 9/1 00:00 CST


def test_monthly_window_december_rollover() -> None:
    """12 月的次月必须是**次年 1 月**（跨年边界容易写错）。"""
    window = NewsAgent._period_window("monthly", "2026-12", "Asia/Shanghai")
    assert window is not None
    since, until = window
    assert since.astimezone(timezone.utc) == datetime(2026, 11, 30, 16, 0, tzinfo=timezone.utc)
    assert until.astimezone(timezone.utc) == datetime(2026, 12, 31, 16, 0, tzinfo=timezone.utc)


def test_period_window_daily_returns_none() -> None:
    """日报不按自然周期（返回 None，调用方回退小时窗口）。"""
    assert NewsAgent._period_window("daily", "2026-09-15", "Asia/Shanghai") is None


def test_period_window_bad_label_returns_none() -> None:
    """标签坏了不能抛异常（否则整次生成失败）——回退小时窗口即可。"""
    assert NewsAgent._period_window("weekly", "不是日期", "Asia/Shanghai") is None
    assert NewsAgent._period_window("monthly", "2026", "Asia/Shanghai") is None


def test_span_text_renders_inclusive_range() -> None:
    """提示词里给人看的区间是闭区间（含最后一天），便于模型理解。"""
    window = NewsAgent._period_window("weekly", "2026-09-07", "Asia/Shanghai")
    assert NewsAgent._span_text(window) == "2026-09-07 ~ 2026-09-13"
    assert NewsAgent._span_text(None) is None


# ---------- 过滤器：显式区间优先于小时窗口 ----------


def test_filter_uses_explicit_window() -> None:
    """给了 since/until 就只保留区间内条目（左闭右开），不再看 hours。"""
    since = datetime(2026, 9, 6, 16, 0, tzinfo=timezone.utc)
    until = datetime(2026, 9, 13, 16, 0, tzinfo=timezone.utc)
    f = NewsFilter(since=since, until=until, time_window_hours=24 * 365)
    items = [
        _item("2026-09-06T15:59:00+00:00", "before"),   # 区间外（早 1 分钟）
        _item("2026-09-06T16:00:00+00:00", "left"),     # 左闭：保留
        _item("2026-09-10T00:00:00+00:00", "inside"),   # 保留
        _item("2026-09-13T15:59:00+00:00", "last"),     # 保留
        _item("2026-09-13T16:00:00+00:00", "right"),    # 右开：排除
    ]
    kept = {i.link for i in f.filter(items)}
    assert kept == {"left", "inside", "last"}


def test_filter_explicit_window_beats_one_year_hours() -> None:
    """反证：若只有 hours（一年）这些条目会全保留 —— 说明是区间在起作用。"""
    items = [
        _item("2026-09-06T15:59:00+00:00", "before"),
        _item("2026-09-13T16:00:00+00:00", "right"),
    ]
    only_hours = NewsFilter(time_window_hours=24 * 365).filter(items)
    assert len(only_hours) == 2


def test_filter_keeps_items_without_timestamp() -> None:
    """无时间信息的条目在显式区间下仍保留（与小时窗口行为一致）。"""
    since = datetime(2026, 9, 1, tzinfo=timezone.utc)
    until = datetime(2026, 9, 8, tzinfo=timezone.utc)
    kept = NewsFilter(since=since, until=until).filter([_item("", "no-ts")])
    assert [i.link for i in kept] == ["no-ts"]
