"""
科技资讯 API 路由（Phase 5）。

- ``POST /api/v1/news/refresh``   手动触发一次日报生成
- ``GET  /api/v1/news/reports``   列出历史日报
- ``GET  /api/v1/news/report``    读取指定日期的日报（?date=YYYY-MM-DD，缺省最新）
- ``POST /api/v1/news/{weekly|monthly}``  生成周报/月报
- ``GET  /api/v1/news/{weekly|monthly}``  列出/读取周报/月报
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query

from app.core.access import require_full_access
from app.core.auth import get_current_user
from app.core.bootstrap import get_app_context
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/news", tags=["news"])


def _require_news_agent() -> Any:
    """获取科技资讯 Agent，未启用则 503。"""
    ctx = get_app_context()
    if ctx.news_agent is None:
        raise HTTPException(503, "科技资讯未启用（news.enabled=false）")
    return ctx.news_agent


async def _run_in_background(agent: Any, kind: str, factory: Any) -> None:
    """后台执行生成任务。

    为什么改成后台执行：周报/月报实测约 10 分钟，同步请求会让浏览器/网关先超时
    （日志里能看到 `POST /news/refresh` 返回 **499** 客户端断开），而服务端还在跑、
    用户既看不到进度也看不到结果，只能反复点。现在提交后立即返回，前端轮询
    ``/api/v1/news/status`` 取结果。
    """
    try:
        await factory()
    except Exception as e:  # noqa: BLE001 - agent 已写状态，这里只兜底日志
        logger.error("后台生成任务失败", type=kind, error=str(e)[:200], exc_info=True)


@router.post("/refresh")
async def refresh_news(
    background: BackgroundTasks,
    body: dict | None = Body(None),
    user_id: str = Depends(require_full_access),
):
    """提交一次日报生成（**立即返回**，结果由 /status 汇报）。

    force=true（默认）表示「重新生成」；false 表示今日已生成则跳过。
    已在生成中时返回 409，前端据此提示「正在生成中」，而不是当成失败。
    """
    agent = _require_news_agent()
    force = bool((body or {}).get("force", True))
    if agent.is_running("daily"):
        raise HTTPException(409, "日报正在生成中，请稍候（可在页面查看进度）")
    background.add_task(_run_in_background, agent, "daily", lambda: agent.refresh(force=force))
    return {"accepted": True, "kind": "daily"}


@router.get("/reports")
async def list_reports(user_id: str = Depends(get_current_user)):
    """列出历史日报（元信息，需登录，预览账号可读）。"""
    agent = _require_news_agent()
    return {"reports": agent.list_reports()}


@router.get("/report")
async def get_report(
    date: str | None = Query(None, description="日报日期 YYYY-MM-DD，缺省最新"),
    user_id: str = Depends(get_current_user),
):
    """读取指定日期的日报（含 Markdown，需登录，预览账号可读）。"""
    agent = _require_news_agent()
    if date:
        report = agent.read_report(date)
        if report is None:
            raise HTTPException(404, f"日报不存在: {date}")
        return report
    reports = agent.list_reports()
    if not reports:
        raise HTTPException(404, "暂无日报")
    return agent.read_report(reports[0]["date"])


@router.get("/status")
async def news_status(user_id: str = Depends(get_current_user)):
    """最近一次定时任务（日报/周报/月报）的执行状态。

    为什么要这个接口：定时任务失败以前只在服务端日志里留一行，用户看不到，
    只能靠「咦今天怎么没日报」发现。现在前端可以直接展示失败原因。
    """
    agent = _require_news_agent()
    return {"status": agent.read_status()}


@router.post("/{report_type}")
async def generate_periodic(
    report_type: str,
    background: BackgroundTasks,
    body: dict | None = Body(None),
    user_id: str = Depends(require_full_access),
):
    """生成周报（weekly）或月报（monthly），可传 period/supplement。"""
    if report_type not in ("weekly", "monthly"):
        raise HTTPException(400, "report_type 只支持 weekly 或 monthly")
    agent = _require_news_agent()
    body = body or {}
    if agent.is_running(report_type):
        label = "周报" if report_type == "weekly" else "月报"
        raise HTTPException(409, f"{label}正在生成中，请稍候（可在页面查看进度）")
    period = body.get("period")
    supplement = body.get("supplement")
    background.add_task(
        _run_in_background,
        agent,
        report_type,
        lambda: agent.generate_periodic(report_type, period=period, supplement=supplement),
    )
    return {"accepted": True, "kind": report_type, "period": period or ""}


@router.get("/{report_type}")
async def list_periodic(
    report_type: str,
    period: str | None = Query(None),
    user_id: str = Depends(get_current_user),
):
    """列出周报/月报，或读取某期（?period=，需登录，预览账号可读）。"""
    if report_type not in ("weekly", "monthly"):
        raise HTTPException(400, "report_type 只支持 weekly 或 monthly")
    agent = _require_news_agent()
    if period:
        report = agent.read_periodic(report_type, period)
        if report is None:
            raise HTTPException(404, f"报告不存在: {report_type}/{period}")
        return report
    return {"reports": agent.list_periodic(report_type)}
