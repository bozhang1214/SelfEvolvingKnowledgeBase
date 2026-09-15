"""
科技资讯 Agent 服务编排。

串联流水线：RSS 采集 → 关键词筛选 → 原文正文抽取 → LLM 生成日报 → 存储。
"""
from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.agents.news.content_extractor import fetch_article_text
from app.agents.news.filter import NewsFilter
from app.agents.news.generator import DailyReportGenerator
from app.agents.news.rss_fetcher import RSSFetcher
from app.agents.news.storage import NewsStorage
from app.core.logging import get_logger

logger = get_logger(__name__)

# 原文正文抓取的并发数（过大易被目标站限流）
_CONTENT_CONCURRENCY = 10
# 单条正文传给 LLM 的最大字符数（150~200 字摘要足够）
_CONTENT_MAX_CHARS = 1200


class NewsAgent:
    """科技资讯 Agent。"""

    # 各周期的时间窗口（小时）：日报用配置值，周报/月报按跨度放宽
    _WINDOW_HOURS = {"daily": None, "weekly": 7 * 24, "monthly": 30 * 24}

    def __init__(self, config: Any, llm_factory: Any) -> None:
        self._config = config
        self._fetcher = RSSFetcher(config.rss_sources)
        from app.agents.news.web_fetcher import WebFetcher

        self._web_fetcher = WebFetcher()
        # 关键词筛选用「顶层 keywords ∪ 所有大类关键词」，确保融资/安全/开源等
        # 大类相关内容不会在分类前被顶层筛选误杀。
        all_keywords = list(config.keywords or [])
        for c in config.categories or []:
            all_keywords.extend(c.keywords or [])
        self._keywords = list(dict.fromkeys(all_keywords))  # 去重保序
        self._exclude_keywords = config.exclude_keywords
        self._generator = DailyReportGenerator(llm_factory)
        self._storage = NewsStorage(config.report_dir, config.retention_days)

    async def refresh(self, force: bool = False) -> dict:
        """
        执行一次完整日报刷新。

        force=False（默认，调度器用）：今日已生成则跳过（幂等），避免多 worker 重复生成。
        force=True（手动「重新生成」）：无论是否已生成都重新跑。
        """
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if not force and self._storage.daily_exists(day):
            logger.info("今日日报已存在，跳过生成", day=day)
            return {"type": "daily", "period": day, "skipped": True, "fetched": 0, "filtered": 0}

        # 文件锁：跨 worker 互斥，保证同一时刻只有一个进程在生成
        lock = await self._acquire_lock("daily")
        if lock is None:
            logger.warning("获取日报生成锁超时，跳过", day=day)
            return {"type": "daily", "period": day, "skipped": True, "fetched": 0, "filtered": 0}
        try:
            # 拿到锁后二次检查（可能在等待锁期间已被别的 worker 生成）
            if not force and self._storage.daily_exists(day):
                logger.info("今日日报已存在（锁内二次检查），跳过", day=day)
                return {"type": "daily", "period": day, "skipped": True, "fetched": 0, "filtered": 0}
            return await self._generate_report("daily")
        finally:
            self._release_lock(lock)

    async def _acquire_lock(self, name: str, timeout: float = 600) -> Path | None:
        """基于 O_EXCL 原子创建锁文件的跨进程互斥锁，返回锁文件路径或超时 None。"""
        lock_path = self._storage._dir / f".lock_{name}"
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return lock_path
            except FileExistsError:
                # 陈旧锁（进程崩溃残留）超过 timeout 视为失效
                try:
                    if time.time() - lock_path.stat().st_mtime > timeout:
                        lock_path.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                await asyncio.sleep(2)
        return None

    @staticmethod
    def _release_lock(lock_path: Path | None) -> None:
        """释放锁文件。"""
        if lock_path is not None:
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _span_text(window: tuple[datetime, datetime] | None) -> str | None:
        """把 UTC 区间渲染成「2026-09-07 ~ 2026-09-13」这样的中文提示词用语。"""
        if window is None:
            return None
        since, until = window
        return f"{since:%Y-%m-%d} ~ {until - timedelta(days=1):%Y-%m-%d}"

    @staticmethod
    def _period_window(
        report_type: str, label: str, tz_name: str
    ) -> tuple[datetime, datetime] | None:
        """按 period 标签算出**自然周期**的起止时间（本地时区，返回 UTC，左闭右开）。

        返回的是**本地时区**的 aware datetime（自然周/月属本地日历概念）。
        用它生成周报时（例如周二生成）会把**本周**（周二往回 7 天）算进去，而期号却写
        「上周一」—— 标签与内容不一致。这里改为按标签算自然周期：

        - ``weekly``：label = 上周一 ``YYYY-MM-DD`` → ``[该日 00:00, +7 天)``
        - ``monthly``：label = ``YYYY-MM`` → ``[该月 1 日 00:00, 次月 1 日)``

        Args:
            report_type: ``weekly`` / ``monthly``（其他返回 ``None``）。
            label: 周期标签。
            tz_name: 本地时区名（如 ``Asia/Shanghai``）。

        Returns:
            ``(since, until)``（本地时区）；无法解析时返回 ``None``（调用方回退小时窗口）。
        """
        from zoneinfo import ZoneInfo

        try:
            tz = ZoneInfo(tz_name)
            if report_type == "weekly":
                start = datetime.strptime(label, "%Y-%m-%d").replace(tzinfo=tz)
                end = start + timedelta(days=7)
            elif report_type == "monthly":
                year, month = (int(x) for x in label.split("-"))
                start = datetime(year, month, 1, tzinfo=tz)
                end = datetime(year + (month // 12), (month % 12) + 1, 1, tzinfo=tz)
            else:
                return None
        except (ValueError, KeyError):
            logger.warning(f"周期标签无法解析，回退到小时窗口 type={report_type} label={label}")
            return None
        # 返回**本地时区**的 aware datetime：自然周/月是本地日历概念，直接渲染才对
        # （曾经转成 UTC 再减一天渲染，结果日期差了一天）。与条目的 UTC 时间比较仍
        # 按瞬时进行，因此过滤器不需要改动。
        return start, end

    async def _generate_report(self, report_type: str, period: str | None = None) -> dict:
        """日报/周报/月报共用流水线：采集 → 按时间窗口筛选 → 正文抽取 → 逐类生成 → 存储。"""
        # 1. 周期标签与时间窗口
        #    ⚠️ 必须先算标签：周报/月报的窗口要按 period 算**自然周期**，
        #    而不是「生成时刻往前 N 小时」（否则标签写上周、内容却含本周）。
        if report_type == "daily":
            period_label = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            window = None
        else:
            period_label = self._period_label(report_type, period)
            window = self._period_window(report_type, period_label, self._config.timezone)

        window_hours = self._WINDOW_HOURS[report_type]
        if window_hours is None:
            window_hours = self._config.time_window_hours

        # 2. 采集 + 筛选
        items = await self._fetcher.fetch_all()
        # 网页采集源（CSDN 热榜 / 魔搭模型库）补充到资讯池
        web_items = await self._web_fetcher.fetch_all()
        items.extend(web_items)
        flt = NewsFilter(
            keywords=self._keywords,
            exclude_keywords=self._exclude_keywords,
            time_window_hours=window_hours,
            since=window[0] if window else None,
            until=window[1] if window else None,
        )
        filtered = flt.filter(items)

        # 3. 原文正文抽取
        items_json = await self._enrich_content([self._item_to_dict(it) for it in filtered])

        # 4. 生成（头条 + 逐类 + 综合分析，时间语境随 report_type 变化）
        report = await self._generator.generate(
            items_json,
            self._config.llm_role,
            self._config.categories,
            period_type=report_type,
            min_items_per_category=getattr(self._config, "min_items_per_category", 10),
            # 把**真实日期区间**交给提示词，避免模型按「本周/过去一周」的口径写
            time_span_override=self._span_text(window),
        )

        # 5. 存储（period_label 已在第 1 步算好）
        if report_type == "daily":
            path = self._storage.save_daily(period_label, report)
        else:
            path = self._storage.save_periodic(report_type, period_label, report)

        logger.info(
            "报告生成完成",
            type=report_type, period=period_label,
            fetched=len(items), filtered=len(filtered), path=path,
        )
        return {
            "type": report_type,
            "period": period_label,
            "fetched": len(items),
            "filtered": len(filtered),
            "path": path,
            "report": report,
        }

    def list_reports(self) -> list[dict]:
        """列出历史日报。"""
        return self._storage.list_reports()

    def read_report(self, day: str) -> dict | None:
        """读取指定日期的日报。"""
        return self._storage.read_report(day)

    def list_periodic(self, report_type: str) -> list[dict]:
        """列出周报/月报。"""
        return self._storage.list_periodic(report_type)

    def read_periodic(self, report_type: str, period: str) -> dict | None:
        """读取某期周报/月报。"""
        return self._storage.read_periodic(report_type, period)

    async def generate_periodic(
        self,
        report_type: str,
        period: str | None = None,
        supplement: list[dict] | None = None,
    ) -> dict:
        """生成周报/月报（与日报同格式，时间跨度为一周/一个月）。supplement 保留兼容但已不使用。"""
        if report_type not in ("weekly", "monthly"):
            raise ValueError(f"未知报告类型: {report_type}")
        return await self._generate_report(report_type, period)

    @staticmethod
    def _period_label(report_type: str, period: str | None) -> str:
        """计算周期标签（文件名用）：周报=周一日期，月报=YYYY-MM。"""
        today = datetime.now(timezone.utc).date()
        if report_type == "weekly":
            if period:
                return period
            this_monday = today - timedelta(days=today.weekday())
            return (this_monday - timedelta(days=7)).isoformat()  # 上周一
        if period:
            return period
        first_this_month = today.replace(day=1)
        return (first_this_month - timedelta(days=1)).strftime("%Y-%m")  # 上月

    @staticmethod
    def _item_to_dict(item: Any) -> dict:
        return {
            "title": item.title,
            "source": item.source,
            "link": item.link,
            "published": item.published,
            "summary": item.summary,  # RSS 摘要（兜底）
            "content": item.content,  # RSS 全文（content:encoded，可能为空）
        }

    async def _enrich_content(self, items_json: list[dict]) -> list[dict]:
        """为每条资讯补全正文：优先 RSS 全文/摘要，不足时并发抓取原文正文。"""
        sem = asyncio.Semaphore(_CONTENT_CONCURRENCY)

        async def enrich(d: dict) -> dict:
            rss_summary = (d.get("summary") or "").strip()
            rss_content = (d.get("content") or "").strip()
            fetched = ""
            # RSS 已有较完整内容时直接用（「用原文的摘要」）；不足则抓原文正文
            if max(len(rss_content), len(rss_summary)) < 200 and d.get("link"):
                async with sem:
                    fetched = await fetch_article_text(d["link"])
            best = max([rss_content, rss_summary, fetched], key=len)
            d["content"] = best[:_CONTENT_MAX_CHARS]
            # 保留 summary（RSS 摘要）供分类关键词匹配；content 供 LLM 写摘要
            return d

        return list(await asyncio.gather(*[enrich(d) for d in items_json]))
