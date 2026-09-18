"""
科技资讯定时调度器。

使用 APScheduler 的 AsyncIOScheduler，在应用启动时按 cron 注册日报/周报任务。
- 日报：config.news.daily_cron（默认每天 09:00）
- 周报/月报：预留（后续接入 news_generate 聚合逻辑）
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.logging import get_logger

logger = get_logger(__name__)


class NewsScheduler:
    """科技资讯定时调度器。"""

    def __init__(self, config: Any, news_agent: Any) -> None:
        self._config = config
        self._agent = news_agent
        self._scheduler = AsyncIOScheduler()

    #: 状态文件名（放在报告目录里，和报告一起被保留/清理）
    STATUS_FILE = "last_status.json"
    #: 失败重试次数与间隔（秒）。网络/TLS 抖动是实测最常见的失败原因
    RETRY_TIMES = 1
    RETRY_DELAY_S = 60
    #: 错过触发后的宽限时间（秒）。默认只有 1 秒 —— 容器恰好在 08:00 前后重启时，
    #: 这次运行会被**静默跳过**（周一没生成周报很可能就是这个原因），因此放宽到 1 小时。
    MISFIRE_GRACE_S = 3600
    #: 启动补跑的延迟（秒）：等 embedding/MCP 预热完再补，避免和启动抢 CPU
    CATCH_UP_DELAY_S = 60

    def _status_path(self) -> Path:
        return Path(self._config.report_dir) / self.STATUS_FILE

    def _record_status(self, kind: str, *, ok: bool, period: str = "", error: str = "",
                       started_at: str = "", duration_s: float = 0.0) -> None:
        """把每次定时任务的**结果**落盘（成功也记）。

        为什么必须落盘：原来失败只在 APScheduler 里打一行 "raised an exception"，
        没人会去看，于是「日报没出来」只能靠人肉眼发现。落盘后接口/前端能直接展示
        「今日日报生成失败：<原因>」，问题就不会再静默。
        """
        payload = {
            "kind": kind, "ok": ok, "period": period, "error": error,
            "started_at": started_at, "finished_at": datetime.now(timezone.utc).isoformat(),
            "duration_s": round(duration_s, 1),
        }
        try:
            path = self._status_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as e:  # 落盘失败不能把任务本身搞挂
            logger.warning("写入资讯任务状态失败", error=str(e)[:200])
        if ok:
            logger.info("资讯定时任务完成", **payload)
        else:
            logger.error("资讯定时任务失败", **payload)

    async def _run_guarded(self, kind: str, factory: Any) -> None:
        """执行一次定时任务：失败重试 + 结果落盘（**不再静默失败**）。"""
        import asyncio

        started = datetime.now(timezone.utc)
        last_error = ""
        for attempt in range(self.RETRY_TIMES + 1):
            try:
                result = await factory()
                period = ""
                if isinstance(result, dict):
                    period = str(result.get("period") or "")
                self._record_status(
                    kind, ok=True, period=period,
                    started_at=started.isoformat(),
                    duration_s=(datetime.now(timezone.utc) - started).total_seconds(),
                )
                return
            except Exception as e:  # noqa: BLE001
                last_error = f"{type(e).__name__}: {str(e)[:200]}"
                logger.warning("资讯定时任务执行失败", kind=kind, attempt=attempt + 1, error=last_error)
                if attempt < self.RETRY_TIMES:
                    await asyncio.sleep(self.RETRY_DELAY_S)
        self._record_status(
            kind, ok=False, error=last_error,
            started_at=started.isoformat(),
            duration_s=(datetime.now(timezone.utc) - started).total_seconds(),
        )

    def _job_kwargs(self) -> dict:
        """定时任务的公共参数：放宽 misfire 宽限 + 不并发 + 合并补跑。"""
        return {
            "replace_existing": True,
            "misfire_grace_time": self.MISFIRE_GRACE_S,
            "coalesce": True,
            "max_instances": 1,
        }

    def start(self) -> None:
        """注册并启动定时任务。"""
        if not self._config.enabled:
            logger.info("科技资讯未启用（news.enabled=false），跳过调度")
            return

        # ⚠️ 必须注册**协程函数**（用 partial 绑定参数），不能写成
        # `lambda: self._run_guarded(...)`：lambda 是同步函数，它只是**创建**了协程
        # 对象就返回了，APScheduler 拿到后不会 await —— 任务体从来不执行，
        # 只在日志里留一行 `RuntimeWarning: coroutine ... was never awaited`，
        # 而 APScheduler 照样报 "executed successfully"（2026-09-15 的修复就是这么
        # 把日报定时任务整个变成空转的：09-17、09-18 连续两天没日报）。
        self._scheduler.add_job(
            partial(self._run_guarded, "daily", self._agent.refresh),
            CronTrigger.from_crontab(self._config.daily_cron, timezone=self._config.timezone),
            id="news_daily",
            name="科技资讯（每日）",
            **self._job_kwargs(),
        )
        self._scheduler.add_job(
            partial(self._run_guarded, "weekly", partial(self._agent.generate_periodic, "weekly")),
            CronTrigger.from_crontab(self._config.weekly_cron, timezone=self._config.timezone),
            id="news_weekly",
            name="科技周报（每周一）",
            **self._job_kwargs(),
        )
        self._scheduler.add_job(
            partial(self._run_guarded, "monthly", partial(self._agent.generate_periodic, "monthly")),
            CronTrigger.from_crontab(self._config.monthly_cron, timezone=self._config.timezone),
            id="news_monthly",
            name="科技月报（每月 1 日）",
            **self._job_kwargs(),
        )
        self._scheduler.start()
        logger.info(
            "科技资讯调度已启动",
            daily_cron=self._config.daily_cron,
            weekly_cron=self._config.weekly_cron,
            timezone=self._config.timezone,
        )
        self._schedule_catch_up()

    # ---------------- 启动补跑（错过的计划不再永久丢失） ----------------

    def _schedule_catch_up(self) -> None:
        """启动后台补跑任务（不阻塞应用启动）。"""
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 理论上 start() 总在事件循环里调用（lifespan）；真没有就放弃补跑，
            # 绝不能因为「补跑排不进去」把应用启动搞挂。
            logger.warning("没有事件循环，跳过启动补跑（不影响定时调度）")
            return

        async def _run_later() -> None:
            # 等一会再补：让 embedding/MCP 预热完成，别和启动抢 CPU
            await asyncio.sleep(self.CATCH_UP_DELAY_S)
            try:
                await self.catch_up_missing()
            except Exception as e:  # noqa: BLE001 - 补跑是旁路能力，绝不影响主流程
                logger.warning("启动补跑异常", error=f"{type(e).__name__}: {str(e)[:200]}")

        self._catch_up_task = loop.create_task(_run_later())  # 持强引用，避免被 GC

    def _daily_time_passed(self, now: datetime | None = None) -> bool:
        """现在是否已过「今天该跑日报」的时间点。

        为什么日报必须判时间：`refresh()` 的窗口是滚动 24 小时，凌晨补跑会生成一份
        几乎只有昨天内容的「今天的日报」。周报/月报不用判 —— 它们的 period 恒为
        **已结束**的自然周/月，缺了就补没有歧义。
        """
        from zoneinfo import ZoneInfo

        tz: Any
        try:
            tz = ZoneInfo(self._config.timezone)
        except Exception:  # noqa: BLE001
            tz = timezone.utc
        now = now or datetime.now(tz)
        parts = self._config.daily_cron.split()
        # 只在「每天固定时刻」这种简单表达式下补跑；带星期/日期限制的复杂表达式不猜
        if len(parts) != 5 or not parts[0].isdigit() or not parts[1].isdigit():
            return False
        if parts[2] != "*" or parts[4] != "*":
            return False
        return (now.hour, now.minute) >= (int(parts[1]), int(parts[0]))

    async def catch_up_missing(self) -> list[str]:
        """补跑**缺失**的报告，返回实际补跑的类型列表。

        为什么必须有：定时任务在计划时刻错过超过 misfire 宽限（容器恰好正在部署/重启，
        2026-09-15 的日报与上周周报都是这么丢的）后，APScheduler 直接跳过，
        **这一天/这一期就永远不会再生成**，只能靠人发现或手动点一次。
        补跑是幂等的：只补「该有但现在没有」的那份，跑完就写状态（失败会触发告警）。
        """
        if not self._config.enabled:
            return []

        ran: list[str] = []

        # 日报：到点 + 今天没有 → 补（refresh 自身也有存在性检查，双保险）
        if self._daily_time_passed():
            day = self._agent.expected_period("daily")
            if self._agent.read_report(day) is None:
                logger.info("启动补跑：今日日报缺失，补一次", day=day)
                await self._run_guarded("daily", lambda: self._agent.refresh())
                ran.append("daily")

        # 周报/月报：期望的 period 恒为已结束的周期，缺了就补
        for kind in ("weekly", "monthly"):
            label = self._agent.expected_period(kind)
            if self._agent.read_periodic(kind, label) is None:
                logger.info("启动补跑：周期报告缺失，补一次", kind=kind, period=label)
                await self._run_guarded(
                    kind, lambda k=kind: self._agent.generate_periodic(k)
                )
                ran.append(kind)

        if ran:
            logger.info("启动补跑完成", kinds=ran)
        else:
            logger.info("启动补跑：没有缺失的报告")
        return ran

    def shutdown(self) -> None:
        """关闭调度器。"""
        task = getattr(self, "_catch_up_task", None)
        if task is not None and not task.done():
            task.cancel()
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("科技资讯调度已关闭")
