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
    """分析单个职位 JD，返回聚合的结构化结果（14 天缓存）。"""
    agent = _require_job_agent()
    from app.agents.job.analysis_cache import get_cached_analysis, save_analysis

    jd = (body.jd_text or "").strip()
    if not jd:
        raise HTTPException(400, "jd_text 不能为空")

    # 命中缓存直接返回
    cached = get_cached_analysis(user_id, jd)
    if cached is not None:
        return {**cached, "cached": True}

    try:
        result = await agent.analyze_job(jd, body.job_meta)
        save_analysis(user_id, jd, result)
        return {**result, "cached": False}
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


_BROWSER_BASE = "http://browser:1300"


async def _call_browser(path: str, payload: dict, timeout: float = 30.0) -> dict:
    """调用通用浏览器服务（sekb-browser）。"""
    import httpx

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{_BROWSER_BASE}{path}", json=payload)
        resp.raise_for_status()
        return resp.json()


class BossQrStatusReq(BaseModel):
    qr_id: str = Field(..., min_length=1, description="start 返回的 qr_id")


@router.post("/boss/qr/start")
async def boss_qr_start(user_id: str = Depends(get_current_user)):
    """启动 BOSS 扫码登录，返回第一张二维码（data URL）+ qr_id。"""
    _require_job_agent()
    try:
        return await _call_browser("/login/qr/start", {"site": "boss"}, timeout=30)
    except Exception as e:
        logger.error("BOSS 扫码启动失败", error=str(e), exc_info=True)
        raise HTTPException(500, f"BOSS 扫码启动失败: {e}")


@router.post("/boss/qr/status")
async def boss_qr_status(body: BossQrStatusReq, user_id: str = Depends(get_current_user)):
    """轮询 BOSS 扫码状态机，返回 phase（及第二张码 / 登录 Cookie）。"""
    _require_job_agent()
    try:
        return await _call_browser(
            "/login/qr/status", {"site": "boss", "qr_id": body.qr_id}, timeout=15
        )
    except Exception as e:
        logger.error("BOSS 扫码状态查询失败", error=str(e), exc_info=True)
        raise HTTPException(500, f"BOSS 扫码状态查询失败: {e}")


class BatchAnalyzeReq(BaseModel):
    """批量分析请求体。"""

    keyword: str = Field("", description="采集关键词（空则用配置默认）")
    city: str = Field("", description="城市（空则用配置默认）")
    force: bool = Field(False, description="true 强制重新分析（忽略 7 天缓存）")


@router.post("/batch-analyze")
async def batch_analyze(body: BatchAnalyzeReq, user_id: str = Depends(get_current_user)):
    """
    一键批量分析采集结果，生成市场分析报告。

    7 天内已分析过则直接返回缓存报告；``force=true`` 或缓存被删除时重新分析。
    """
    ctx = get_app_context()
    _require_job_agent()
    from app.agents.job.market import analyze_market, delete_report
    from app.agents.job.profile import load_user_profile

    cfg = ctx.config.job
    keyword = (body.keyword or "").strip() or cfg.default_keyword
    city = (body.city or "").strip() or cfg.default_city

    try:
        if body.force:
            delete_report(user_id)
        return await analyze_market(
            ctx=ctx,
            user_id=user_id,
            keyword=keyword,
            city=city,
            llm_factory=ctx.llm_factory,
            user_profile=load_user_profile(),
        )
    except Exception as e:
        logger.error("批量分析失败", error=str(e), exc_info=True)
        raise HTTPException(500, f"批量分析失败: {e}")


@router.delete("/batch-analyze")
async def delete_batch_analysis(user_id: str = Depends(get_current_user)):
    """删除批量分析缓存（下次进入会重新分析）。"""
    _require_job_agent()
    from app.agents.job.market import delete_report

    deleted = delete_report(user_id)
    return {"deleted": deleted}
