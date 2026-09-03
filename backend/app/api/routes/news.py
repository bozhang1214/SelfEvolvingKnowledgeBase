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

from fastapi import APIRouter, Body, Depends, HTTPException, Query

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


@router.post("/refresh")
async def refresh_news(user_id: str = Depends(get_current_user)):
    """手动触发一次日报生成。"""
    agent = _require_news_agent()
    try:
        result = await agent.refresh()
    except Exception as e:
        logger.error("手动触发日报失败", error=str(e), exc_info=True)
        raise HTTPException(500, f"日报生成失败: {e}")
    return result


@router.get("/reports")
async def list_reports():
    """列出历史日报（元信息）。"""
    agent = _require_news_agent()
    return {"reports": agent.list_reports()}


@router.get("/report")
async def get_report(
    date: str | None = Query(None, description="日报日期 YYYY-MM-DD，缺省最新"),
):
    """读取指定日期的日报（含 Markdown）。"""
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


@router.post("/{report_type}")
async def generate_periodic(
    report_type: str,
    body: dict | None = Body(None),
    user_id: str = Depends(get_current_user),
):
    """生成周报（weekly）或月报（monthly），可传 period/supplement。"""
    if report_type not in ("weekly", "monthly"):
        raise HTTPException(400, "report_type 只支持 weekly 或 monthly")
    agent = _require_news_agent()
    body = body or {}
    try:
        return await agent.generate_periodic(
            report_type,
            period=body.get("period"),
            supplement=body.get("supplement"),
        )
    except Exception as e:
        logger.error("周期报告生成失败", type=report_type, error=str(e), exc_info=True)
        raise HTTPException(500, f"周期报告生成失败: {e}")


@router.get("/{report_type}")
async def list_periodic(report_type: str, period: str | None = Query(None)):
    """列出周报/月报，或读取某期（?period=）。"""
    if report_type not in ("weekly", "monthly"):
        raise HTTPException(400, "report_type 只支持 weekly 或 monthly")
    agent = _require_news_agent()
    if period:
        report = agent.read_periodic(report_type, period)
        if report is None:
            raise HTTPException(404, f"报告不存在: {report_type}/{period}")
        return report
    return {"reports": agent.list_periodic(report_type)}
