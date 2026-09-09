"""
招聘分析 API 路由（Phase 2）。

- ``POST /api/v1/job/analyze``  对单个职位 JD 做全流程分析，返回聚合的结构化结果
- ``POST /api/v1/job/fetch``    从猎聘采集真实职位列表（关键词/城市/分页）
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.core.access import require_full_access
from app.core.auth import get_current_user
from app.core.bootstrap import get_app_context
from app.core.logging import get_logger
from app.services.browser_client import BrowserClient
from app.services.job_service import parse_job_files

logger = get_logger(__name__)

_browser = BrowserClient()

router = APIRouter(prefix="/api/v1/job", tags=["job"],
    dependencies=[Depends(require_full_access)],
)


class JobAnalyzeRequest(BaseModel):
    """职位分析请求体。"""

    jd_text: str = Field(..., min_length=1, description="职位描述（JD）原文")
    job_meta: dict[str, Any] | None = Field(
        None, description="职位元信息（公司/职位名/薪资/城市等，可选）"
    )


class JobFetchRequest(BaseModel):
    """职位采集请求体。"""

    keyword: str = Field("", description="搜索关键词，支持空格拼接多个关键词（空则用配置默认 default_keyword）")
    city: str = Field("", description="工作地过滤（空/「不限」= 不过滤）")
    min_salary_k: int = Field(0, ge=0, description="最低月薪（K），0=不限")
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
        result = await agent.analyze_job(jd, body.job_meta, user_id)
        save_analysis(user_id, jd, result)
        # 自动存档到历史报告
        from app.agents.job.archive import save_report

        pos = (body.job_meta or {}).get("position") or result.get("job_analysis", {}).get("position") or "单职位分析"
        comp = (body.job_meta or {}).get("company") or ""
        title = f"{pos}" + (f" @ {comp}" if comp else "")
        save_report(user_id, "single", title, result)
        return {**result, "cached": False}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error("职位分析失败", error=str(e), exc_info=True)
        raise HTTPException(500, f"职位分析失败: {e}")


@router.post("/fetch")
async def fetch_jobs(body: JobFetchRequest, user_id: str = Depends(get_current_user)):
    """从多源采集真实职位列表（14 天内命中缓存直接返回）。"""
    ctx = get_app_context()
    _require_job_agent()
    from app.agents.job.collector import JobCollector
    from app.agents.job.job_cache import cache_key, get_cached_jobs, save_cached_jobs

    cfg = ctx.config.job
    keyword = (body.keyword or "").strip() or cfg.default_keyword
    # 城市「不限」= 空串 = 不过滤城市
    city = (body.city or "").strip()
    if city in ("不限", "全部", "全国"):
        city = ""

    key = cache_key(user_id, keyword, city, body.min_salary_k)

    # 命中缓存直接返回（14 天内）
    cached = get_cached_jobs(key)
    if cached is not None:
        return {
            "keyword": keyword,
            "city": city,
            "min_salary_k": body.min_salary_k,
            "source_count": len({j.get("source") for j in cached if j.get("source")}),
            "sources": {},
            "count": len(cached),
            "jobs": cached,
            "cached": True,
        }

    collector = JobCollector(
        city=city,
        min_salary_k=body.min_salary_k,
        exclude_companies=cfg.exclude_companies,
    )
    try:
        result = await collector.fetch_all(keyword=keyword, page=body.page, limit=body.limit)
    except Exception as e:
        logger.error("职位采集失败", error=str(e), exc_info=True)
        raise HTTPException(500, f"职位采集失败: {e}")

    save_cached_jobs(key, result["jobs"])

    return {
        "keyword": keyword,
        "city": city,
        "min_salary_k": body.min_salary_k,
        "source_count": len(result["sources"]),
        "sources": result["sources"],
        "count": result["count"],
        "jobs": result["jobs"],
        "cached": False,
    }


class BossQrStatusReq(BaseModel):
    qr_id: str = Field(..., min_length=1, description="start 返回的 qr_id")


@router.post("/boss/qr/start")
async def boss_qr_start(user_id: str = Depends(get_current_user)):
    """启动 BOSS 扫码登录，返回第一张二维码（data URL）+ qr_id。"""
    _require_job_agent()
    try:
        return await _browser.post("/login/qr/start", {"site": "boss"}, timeout=30)
    except Exception as e:
        logger.error("BOSS 扫码启动失败", error=str(e), exc_info=True)
        raise HTTPException(500, f"BOSS 扫码启动失败: {e}")


@router.post("/boss/qr/status")
async def boss_qr_status(body: BossQrStatusReq, user_id: str = Depends(get_current_user)):
    """轮询 BOSS 扫码状态机，返回 phase（及第二张码 / 登录 Cookie）。"""
    _require_job_agent()
    try:
        return await _browser.post(
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
    jobs: list[dict[str, Any]] | None = Field(
        None, description="前端已收集的职位列表（提供则直接分析这批职位，不重复采集）"
    )


@router.post("/batch-analyze")
async def batch_analyze(body: BatchAnalyzeReq, user_id: str = Depends(get_current_user)):
    """
    一键批量分析职位，生成市场分析报告。

    - ``jobs`` 提供时：直接分析传入的职位（「职位收集 → 批量分析」联动，不重复采集、不缓存）。
    - ``jobs`` 为空时：自动采集并生成报告，7 天内命中缓存直接返回。
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
        result = await analyze_market(
            ctx=ctx,
            user_id=user_id,
            keyword=keyword,
            city=city,
            llm_factory=ctx.llm_factory,
            user_profile=load_user_profile(user_id),
            jobs=body.jobs,
        )
        # 自动存档到历史报告
        from app.agents.job.archive import save_report

        rep = result["report"]
        save_report(user_id, "batch", f"批量分析 · {rep['keyword']}（{rep['job_count']} 个职位）", rep)
        return result
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


@router.get("/batch-analyze/cached")
async def get_cached_batch_analysis(user_id: str = Depends(get_current_user)):
    """返回最后一次缓存的批量分析报告（14 天内），无则 report=None。"""
    _require_job_agent()
    from app.agents.job.market import get_cached_report

    report = get_cached_report(user_id)
    return {"cached": report is not None, "report": report}


@router.post("/import")
async def import_jobs(
    files: list[UploadFile] = File(...),
    user_id: str = Depends(get_current_user),
):
    """
    批量导入职位文件（每个文件对应一个职位），返回解析后的职位列表。

    支持 .txt/.md/.markdown/.pdf/.docx（复用 FileProcessor 解析），
    文件名（去扩展名）作为职位标题，正文作为 JD。
    """
    _require_job_agent()
    jobs = await parse_job_files(files)
    return {"jobs": jobs, "count": len(jobs)}


class RefreshJobReq(BaseModel):
    """单职位刷新请求。"""

    job_url: str = Field("", description="职位详情链接")
    source: str = Field("", description="数据来源（猎聘等）")


@router.post("/refresh")
async def refresh_job(body: RefreshJobReq, user_id: str = Depends(get_current_user)):
    """刷新单个职位的 JD（重新抓详情页），返回新的 JD 文本。"""
    _require_job_agent()
    from app.agents.job.fetcher import refresh_job_jd

    jd = await refresh_job_jd(body.job_url, body.source)
    return {"jd_text": jd, "refreshed": bool(jd)}


class SaveCacheReq(BaseModel):
    """保存职位缓存请求（删除/刷新后同步）。"""

    keyword: str = Field("", description="采集关键词")
    city: str = Field("", description="城市")
    min_salary_k: int = Field(0, ge=0, description="最低月薪（K）")
    jobs: list[dict[str, Any]] = Field(default_factory=list, description="当前职位列表")


@router.post("/cache/save")
async def save_job_cache(body: SaveCacheReq, user_id: str = Depends(get_current_user)):
    """保存（覆盖）职位缓存，用于单职位删除/刷新后同步。"""
    _require_job_agent()
    from app.agents.job.job_cache import cache_key, save_cached_jobs

    keyword = (body.keyword or "").strip() or "Agent"
    city = (body.city or "").strip()
    if city in ("不限", "全部", "全国"):
        city = ""
    key = cache_key(user_id, keyword, city, body.min_salary_k)
    save_cached_jobs(key, body.jobs)
    return {"saved": len(body.jobs)}


@router.get("/cache/latest")
async def get_latest_job_cache(user_id: str = Depends(get_current_user)):
    """返回某用户最后一次缓存的职位 + 筛选条件，供「职位收集」页默认回填展示。"""
    _require_job_agent()
    from app.agents.job.job_cache import get_latest_cached

    cached = get_latest_cached(user_id)
    if cached is None:
        return {"cached": False, "keyword": "", "city": "", "min_salary_k": 0, "jobs": [], "count": 0}
    return {**cached, "cached": True, "count": len(cached["jobs"])}


@router.get("/reports")
async def list_archived_reports(user_id: str = Depends(get_current_user)):
    """列出历史存档报告（元信息，不含正文）。"""
    _require_job_agent()
    from app.agents.job.archive import list_reports

    return {"reports": list_reports(user_id)}


@router.get("/reports/{report_id}")
async def get_archived_report(report_id: str, user_id: str = Depends(get_current_user)):
    """读取一份历史存档报告（含正文）。"""
    _require_job_agent()
    from app.agents.job.archive import get_report

    report = get_report(user_id, report_id)
    if report is None:
        raise HTTPException(404, "报告不存在")
    return report


@router.delete("/reports/{report_id}")
async def delete_archived_report(report_id: str, user_id: str = Depends(get_current_user)):
    """删除一份历史存档报告。"""
    _require_job_agent()
    from app.agents.job.archive import delete_report

    deleted = delete_report(user_id, report_id)
    return {"deleted": deleted}
