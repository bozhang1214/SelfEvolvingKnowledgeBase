"""
招聘分析 API 路由（Phase 2）。

- ``POST /api/v1/job/analyze``  对单个职位 JD 做全流程分析，返回聚合的结构化结果
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.auth import get_current_user
from app.core.bootstrap import get_app_context
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/job", tags=["job"])


class JobAnalyzeRequest(BaseModel):
    """职位分析请求体。"""

    jd_text: str = Field(..., min_length=1, description="职位描述（JD）原文")
    job_meta: dict[str, Any] | None = Field(
        None, description="职位元信息（公司/职位名/薪资/城市等，可选）"
    )


def _require_job_agent() -> Any:
    """获取招聘分析 Agent，未启用则 503。"""
    ctx = get_app_context()
    if ctx.job_agent is None:
        raise HTTPException(503, "招聘分析未启用（job.enabled=false）")
    return ctx.job_agent


@router.post("/analyze")
async def analyze_job(body: JobAnalyzeRequest, user_id: str = Depends(get_current_user)):
    """分析单个职位 JD，返回聚合的结构化结果。"""
    agent = _require_job_agent()
    try:
        return await agent.analyze_job(body.jd_text, body.job_meta)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error("职位分析失败", error=str(e), exc_info=True)
        raise HTTPException(500, f"职位分析失败: {e}")
