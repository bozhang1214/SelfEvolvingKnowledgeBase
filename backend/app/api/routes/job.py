"""
招聘分析 API 路由（Phase 2）。

- ``POST /api/v1/job/analyze``  对单个职位 JD 做全流程分析，返回聚合的结构化结果
- ``POST /api/v1/job/fetch``    从猎聘采集真实职位列表（关键词/城市/分页）
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


class JobFetchRequest(BaseModel):
    """职位采集请求体。"""

    keyword: str = Field("", description="搜索关键词（空则用配置默认 default_keyword）")
    city: str = Field("", description="城市过滤（空则用配置默认 default_city）")
    page: int = Field(0, ge=0, description="页码，从 0 开始")
    limit: int = Field(20, ge=1, le=40, description="每页数量（部分源固定返回约 40）")


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


@router.post("/fetch")
async def fetch_jobs(body: JobFetchRequest, user_id: str = Depends(get_current_user)):
    """从多源采集真实职位列表，并应用默认筛选（关键词/城市/薪资）。"""
    ctx = get_app_context()
    _require_job_agent()
    from app.agents.job.collector import JobCollector

    cfg = ctx.config.job
    keyword = (body.keyword or "").strip() or cfg.default_keyword
    city = (body.city or "").strip() or cfg.default_city

    collector = JobCollector(
        city=city,
        min_salary_k=cfg.default_min_salary_k,
        exclude_companies=cfg.exclude_companies,
    )
    try:
        result = await collector.fetch_all(keyword=keyword, page=body.page, limit=body.limit)
    except Exception as e:
        logger.error("职位采集失败", error=str(e), exc_info=True)
        raise HTTPException(500, f"职位采集失败: {e}")

    return {
        "keyword": keyword,
        "city": city,
        "min_salary_k": cfg.default_min_salary_k,
        "source_count": len(result["sources"]),
        "sources": result["sources"],
        "count": result["count"],
        "jobs": result["jobs"],
    }
