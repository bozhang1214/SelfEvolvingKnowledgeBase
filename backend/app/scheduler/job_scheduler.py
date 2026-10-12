"""四层职位「搜索 + 批量分析」定时调度器。

与 :mod:`app.scheduler.scheduler`（科技资讯）分开实现，因为二者配置与生命周期独立；
但**刻意复用同一套踩坑结论**（见下），不要简化掉：

1. **必须注册协程函数**（用 ``partial`` 绑定参数），**不能写 lambda**：
   lambda 只是创建了协程对象就返回，APScheduler 拿到后不会 await —— 任务体从来不执行，
   只在日志里留一行 ``RuntimeWarning: coroutine ... was never awaited``，
   而 APScheduler 照样报 "executed successfully"。2026-09-15 就是这样把日报任务变成空转的。
2. **``misfire_grace_time`` 要放宽**：容器恰好在触发时刻重启时，默认 1 秒宽限会让这次运行
   **静默跳过**。这里给 2 小时。
3. **结果落盘**：失败只打一行日志没人会看；落盘后接口/前端能直接展示「上次刷新失败：<原因>」。
4. **``coalesce`` + ``max_instances=1``**：多次补跑合并成一次，且不并发（采集本身已有节流，
   再并发只会给招聘站点更大压力）。
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.logging import get_logger
from app.services.job_schedule_service import is_run_week, refresh_all_layers

logger = get_logger(__name__)


class JobSearchScheduler:
    """四层职位搜索 + 分析的定时调度器。"""

    #: 状态文件（与职位缓存同目录，便于一起备份/排查）
    STATUS_FILE = "data/job_schedule_status.json"
    #: 错过触发后的宽限时间（秒）：容器在 06:00 前后重启不应丢掉这次刷新
    MISFIRE_GRACE_S = 7200
    #: 启动补跑延迟（秒）：等 MCP 内核/embedding 预热完再跑
    CATCH_UP_DELAY_S = 120
    JOB_ID = "job_layers_refresh"

    def __init__(self, config: Any, ctx: Any) -> None:
        self._config = config
        self._ctx = ctx
        self._scheduler = AsyncIOScheduler()

    # ---------------- 状态落盘 ----------------

    def status_path(self) -> Path:
        return Path(self.STATUS_FILE)

    def read_status(self) -> dict[str, Any]:
        """读上次执行结果（供排查/展示）。"""
        path = self.status_path()
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def _record_status(self, payload: dict[str, Any]) -> None:
        try:
            path = self.status_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(path)
        except OSError as e:  # 落盘失败不应影响任务本身
            logger.warning("定时刷新状态写入失败", error=str(e)[:120])

    # ---------------- 任务体 ----------------

    async def run_once(self, *, reason: str = "manual") -> dict[str, Any]:
        """执行一次全量刷新（可被定时触发或手工调用）。"""
        cfg = self._config
        started = datetime.now(timezone.utc)
        user_id = (cfg.user_id or "").strip()
        if not user_id:
            logger.warning("定时刷新缺少 user_id，跳过", reason=reason)
            return {"ok": False, "error": "user_id 为空"}
        if not cfg.layers:
            logger.warning("定时刷新未配置任何层，跳过", reason=reason)
            return {"ok": False, "error": "layers 为空"}

        try:
            summary = await refresh_all_layers(
                self._ctx, user_id, cfg.layers, force_analyze=bool(cfg.force_analyze)
            )
        except Exception as e:  # noqa: BLE001 - 兜底落盘，避免静默失败
            logger.error("定时刷新整体失败", reason=reason, error=str(e)[:200], exc_info=True)
            self._record_status(
                {
                    "ok": False,
                    "reason": reason,
                    "started_at": started.isoformat(),
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "error": str(e)[:500],
                }
            )
            return {"ok": False, "error": str(e)[:500]}

        # ⚠️ 顺序要紧：**summary 必须在前**。它自己带一个整数 `ok`（成功层数），
        # 若把它展开在 `"ok"` 之后，会把下面算好的布尔值覆盖掉——
        # 于是 `out["ok"]` 变成 1/2 这种整数，`if out["ok"] is True` 静默失效。
        payload = {
            **summary,
            "ok": summary.get("ok") == summary.get("layers"),
            "reason": reason,
            "started_at": started.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "duration_s": round((datetime.now(timezone.utc) - started).total_seconds(), 1),
        }
        self._record_status(payload)
        logger.info("定时刷新完成", reason=reason, ok=payload["ok"], layers=summary.get("layers"))
        return payload

    async def _run_scheduled(self) -> None:
        """cron 触发入口：先做隔周奇偶校验，再决定是否真跑。"""
        cfg = self._config
        today = date.today()
        try:
            anchor = date.fromisoformat(str(cfg.anchor_date))
        except ValueError:
            logger.warning("anchor_date 非法，退化为每周执行", anchor_date=cfg.anchor_date)
            anchor = today
        if not is_run_week(today, anchor, bool(cfg.biweekly)):
            logger.info("本周不执行（隔周）", today=str(today), anchor=str(anchor), week=today.isocalendar()[1])
            return
        await self.run_once(reason=f"scheduled:{today.isoformat()}")

    # ---------------- 注册与生命周期 ----------------

    def _job_kwargs(self) -> dict[str, Any]:
        return {
            "replace_existing": True,
            "misfire_grace_time": self.MISFIRE_GRACE_S,
            "coalesce": True,
            "max_instances": 1,
        }

    def start(self) -> None:
        """注册定时任务并启动。"""
        cfg = self._config
        if not cfg.enabled:
            logger.info("职位定时刷新未启用（job.schedule.enabled=false），跳过调度")
            return
        if not cfg.user_id:
            logger.warning("职位定时刷新已启用但未配置 user_id，跳过调度")
            return

        # ⚠️ 必须注册协程函数（partial 绑定），不能是 lambda —— 见模块 docstring 第 1 条
        self._scheduler.add_job(
            partial(self._run_scheduled),
            CronTrigger.from_crontab(cfg.cron, timezone=cfg.timezone),
            id=self.JOB_ID,
            name="四层职位搜索+分析",
            **self._job_kwargs(),
        )
        self._scheduler.start()
        next_run = self._scheduler.get_job(self.JOB_ID)
        logger.info(
            "职位定时刷新已启动",
            cron=cfg.cron,
            biweekly=cfg.biweekly,
            anchor_date=cfg.anchor_date,
            timezone=cfg.timezone,
            layers=len(cfg.layers),
            next_fire=str(next_run.next_run_time) if next_run else "?",
        )

    def next_run_time(self) -> str:
        """下一次 cron 触发时间（注意：隔周跳过的周也会显示，实际是否执行看奇偶）。"""
        if not self._scheduler.running:
            return ""
        job = self._scheduler.get_job(self.JOB_ID)
        return str(job.next_run_time) if job and job.next_run_time else ""

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
