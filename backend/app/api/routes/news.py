"""
资讯日报 API 路由（Phase 5）。

- ``POST /api/v1/news/refresh``   手动触发一次日报生成
- ``GET  /api/v1/news/reports``   列出历史日报
- ``GET  /api/v1/news/report``    读取指定日期的日报（?date=YYYY-MM-DD，缺省最新）
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth import get_current_user
from app.core.bootstrap import get_app_context
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/news", tags=["news"])


def _require_news_agent() -> Any:
    """获取资讯日报 Agent，未启用则 503。"""
    ctx = get_app_context()
    if ctx.news_agent is None:
        raise HTTPException(503, "资讯日报未启用（news.enabled=false）")
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
