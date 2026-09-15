"""
关键词筛选与去重模块。

筛选链：
1. 时效过滤：只保留「时间窗口内」的条目（无时间信息的条目保留，避免误杀）
2. 关键词过滤：标题/摘要命中关键词即保留，命中排除词即丢弃
3. 去重：按 link（缺省用 title）去重
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.agents.news.rss_fetcher import NewsItem


class NewsFilter:
    """关键词筛选 + 去重 + 时效过滤。"""

    def __init__(
        self,
        keywords: list[str] | None = None,
        exclude_keywords: list[str] | None = None,
        time_window_hours: int = 24,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> None:
        self._keywords = [k.lower() for k in (keywords or [])]
        self._exclude = [k.lower() for k in (exclude_keywords or [])]
        self._window = time_window_hours
        # 显式起止时间（**优先于** time_window_hours）：
        # 周报/月报要的是「自然周/自然月」，而 time_window_hours 只能表达
        # 「相对当前时刻往前 N 小时」——用它生成周报会把本周内容算进「上周」的期号里。
        self._since = since
        self._until = until

    def filter(self, items: list[NewsItem]) -> list[NewsItem]:
        """执行完整筛选链，返回筛选后的条目列表。"""
        items = self._filter_by_time(items)
        items = self._filter_by_keywords(items)
        items = self._dedup(items)
        return items

    def _filter_by_time(self, items: list[NewsItem]) -> list[NewsItem]:
        if self._since is None and self._window <= 0:
            return items
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self._window)
        out: list[NewsItem] = []
        for it in items:
            if not it.published:
                out.append(it)  # 无时间信息则保留
                continue
            try:
                t = datetime.fromisoformat(it.published.replace("Z", "+00:00"))
                if t.tzinfo is None:
                    t = t.replace(tzinfo=timezone.utc)
                if self._since is not None:
                    # 显式自然周期（周报/月报）：左闭右开
                    if t < self._since:
                        continue
                    if self._until is not None and t >= self._until:
                        continue
                elif t < cutoff:
                    continue
                out.append(it)
            except (ValueError, TypeError):
                out.append(it)  # 时间解析失败则保留
        return out

    def _filter_by_keywords(self, items: list[NewsItem]) -> list[NewsItem]:
        if not self._keywords:
            return items
        out: list[NewsItem] = []
        for it in items:
            text = f"{it.title} {it.summary}".lower()
            if not any(k in text for k in self._keywords):
                continue
            if self._exclude and any(e in text for e in self._exclude):
                continue
            out.append(it)
        return out

    @staticmethod
    def _dedup(items: list[NewsItem]) -> list[NewsItem]:
        seen: set[str] = set()
        out: list[NewsItem] = []
        for it in items:
            key = it.link or it.title
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(it)
        return out
