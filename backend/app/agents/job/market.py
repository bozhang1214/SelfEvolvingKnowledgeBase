"""职位市场批量分析（SEKB 侧编排）。

**P0 起「分析」部分已抽到 ``jobcopilot`` 包**，本模块只负责 SEKB 特有的三件事：

1. **采集**：``jobs`` 未提供时调 ``JobCollector`` 抓职位（爬虫留在 SEKB，避免合规风险外溢）；
2. **缓存**：结果按 user_id 隔离持久化在 ``data/job_market_report.json``（14 天）；
3. **存档**：由调用方（``api/routes/job.py``）写入历史报告。

统计口径 / 提示词 / LLM 编排 / 降级策略全在内核 ``analyze_jobs_batch`` 里。

为兼容既有调用方，本模块继续导出 ``compute_stats`` / ``classify_role``
（现为内核实现的转发）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from jobcopilot.core.stats import classify_role, compute_stats  # noqa: F401  (对外转发)

from app.agents.job.generator import _resolve_prompt_dir
from app.agents.job.llm_adapter import SekbLLMAdapter
from app.core.logging import get_logger

logger = get_logger(__name__)

_CACHE_FILE = Path("data/job_market_report.json")
_CACHE_TTL_SECONDS = 14 * 24 * 3600  # 14 天
_CACHE_VERSION = 2

__all__ = [
    "analyze_market",
    "get_cached_report",
    "save_report_cache",
    "delete_report",
    "delete_report_by_search_id",
    "compute_stats",
    "classify_role",
]


def _load_cache() -> dict[str, Any]:
    """读取报告缓存。

    v2 结构（当前）：``{"__v": 2, "<search_id>": {user_id, keyword, city, min_salary_k, report}}``
    即**一次搜索对应一份报告**（与职位缓存一一对应）。

    兼容 v1（``{user_id: report}``，每用户仅一份）：旧条目读入 ``legacy`` 槽，
    仅用于「不传 search_id 时取最新一份」的兜底，不再写回。
    """
    empty: dict[str, Any] = {"version": _CACHE_VERSION, "reports": {}, "legacy": {}}
    if not _CACHE_FILE.exists():
        return empty
    try:
        raw = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return empty
    if not isinstance(raw, dict):
        return empty

    if raw.get("__v") == _CACHE_VERSION:
        return {
            "version": _CACHE_VERSION,
            "reports": {k: v for k, v in raw.items() if k not in ("__v", "__legacy")},
            "legacy": raw.get("__legacy") or {},
        }

    # v1：{user_id: report}
    logger.info("报告缓存为 v1 格式，按兼容模式读取", entries=len(raw))
    return {"version": 1, "reports": {}, "legacy": raw}


def _save_cache(store: dict[str, Any]) -> None:
    _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"__v": _CACHE_VERSION}
    payload.update(store.get("reports", {}))
    legacy = store.get("legacy") or {}
    if legacy:
        # 保留 v1 旧条目，避免升级时丢数据（只读兜底，不再新增）
        payload["__legacy"] = legacy
    _CACHE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _fresh(report: dict[str, Any]) -> bool:
    """报告是否在 TTL 内。"""
    return time.time() - (report.get("analyzed_at_ts", 0) or 0) <= _CACHE_TTL_SECONDS


def get_cached_report(user_id: str, search_id: str | None = None) -> dict[str, Any] | None:
    """取缓存报告（14 天内）。

    - 传 ``search_id``：返回**该次搜索**对应的报告；不存在或过期返回 None；
    - 不传：返回该用户**最新一份**（v2 按 analyzed_at_ts 取最大，无则回退 v1 旧条目）。
    """
    store = _load_cache()
    reports: dict[str, Any] = store["reports"]

    if search_id:
        entry = reports.get(search_id)
        if not entry:
            return None
        report = entry.get("report")
        return report if isinstance(report, dict) and _fresh(report) else None

    best: dict[str, Any] | None = None
    best_ts = 0.0
    for entry in reports.values():
        if entry.get("user_id") != user_id:
            continue
        report = entry.get("report") or {}
        ts = report.get("analyzed_at_ts", 0) or 0
        if _fresh(report) and ts > best_ts:
            best, best_ts = report, ts
    if best is not None:
        return best

    legacy = (store.get("legacy") or {}).get(user_id)
    if isinstance(legacy, dict) and _fresh(legacy):
        return legacy
    return None


def save_report_cache(
    user_id: str,
    search_id: str,
    keyword: str,
    city: str,
    min_salary_k: int,
    report: dict[str, Any],
) -> None:
    """把某次搜索的报告写入缓存（按 search_id 存，与职位缓存一一对应）。"""
    store = _load_cache()
    store["reports"][search_id] = {
        "user_id": user_id,
        "keyword": keyword,
        "city": city,
        "min_salary_k": min_salary_k,
        "report": report,
    }
    _save_cache(store)


def delete_report(user_id: str, search_id: str | None = None) -> bool:
    """删除缓存报告，返回是否真的删了。

    - 传 ``search_id``：只删该次搜索的报告；
    - 不传：删除该用户的**全部**报告（含 v1 旧条目），保持旧调用语义。
    """
    store = _load_cache()
    removed = False

    if search_id:
        if search_id in store["reports"]:
            store["reports"].pop(search_id)
            removed = True
    else:
        keep = {k: v for k, v in store["reports"].items() if v.get("user_id") != user_id}
        removed = len(keep) != len(store["reports"])
        store["reports"] = keep
        legacy = store.get("legacy") or {}
        if user_id in legacy:
            legacy.pop(user_id)
            removed = True

    if removed:
        _save_cache(store)
    return removed


def delete_report_by_search_id(search_id: str) -> bool:
    """按 search_id 删除报告（供过期清理使用，无需知道 user_id）。"""
    store = _load_cache()
    if search_id not in store["reports"]:
        return False
    store["reports"].pop(search_id)
    _save_cache(store)
    return True


async def _run_analysis(
    ctx: Any,
    jobs: list[dict[str, Any]],
    user_profile: str,
    keyword: str,
    city: str,
) -> dict[str, Any]:
    """执行批量分析：默认走 MCP 内核，``transport=direct`` 时进程内直连。

    两条路径都返回同构报告；差异只在内核怎么被调用。
    """
    transport = getattr(ctx.config.job, "transport", "mcp")

    if transport == "mcp":
        from app.agents.job.mcp_client import get_shared_kernel

        kernel = get_shared_kernel(ctx.config.job)
        report = await kernel.call(
            "analyze_jobs_batch",
            {
                "jobs": jobs,
                "keyword": keyword,
                "city": city,
                # 多用户系统必须逐请求注入画像（内核的全局画像承载不了）
                "user_profile": user_profile,
            },
        )
        _log_kernel_usage(report.get("usage"), "批量分析")
        return report

    from jobcopilot import analyze_jobs_batch

    prompt_dir = _resolve_prompt_dir()
    return await analyze_jobs_batch(
        SekbLLMAdapter(ctx.llm_factory),
        jobs,
        user_profile,
        keyword=keyword,
        city=city,
        prompt_dir=str(prompt_dir) if prompt_dir else None,
    )


def _log_kernel_usage(usage: Any, scene: str) -> None:
    """记录内核回报的 token 用量。

    走 MCP 后是内核自己调 LLM，**SEKB 的 LLMFactory 看不到这些调用**——
    若不记录，招聘分析的费用就从成本统计里消失了。这里落到结构化日志
    （Loki 可检索/聚合），保住既有可见性。
    """
    if not isinstance(usage, dict) or not usage.get("calls"):
        return
    logger.info(
        "内核分析用量",
        scene=scene,
        calls=usage.get("calls"),
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
    )


async def analyze_market(
    ctx: Any,
    user_id: str,
    keyword: str,
    city: str,
    llm_factory: Any,
    user_profile: str,
    jobs: list[dict[str, Any]] | None = None,
    search_id: str = "",
    min_salary_k: int = 0,
) -> dict[str, Any]:
    """执行批量分析，返回 ``{cached, report}``。

    ``jobs=None`` 时自动采集；``jobs`` 提供时直接分析传入的职位（「职位收集 →
    批量分析」联动，不重复采集）。无论哪种方式，报告都会**按 search_id 缓存**
    （与职位缓存一一对应），供「批量分析」tab 按当前搜索展示。
    """
    from app.agents.job.job_cache import cache_key, make_search_id

    # 未显式给 search_id 时，按「用户|关键词|城市|薪资」派生（与职位缓存同构）
    sid = search_id or make_search_id(cache_key(user_id, keyword, city, min_salary_k))

    from_provided = jobs is not None
    if not from_provided:
        cached = get_cached_report(user_id, sid if search_id else None)
        if cached is not None:
            return {"cached": True, "report": cached, "search_id": sid}

        from app.agents.job.collector import JobCollector

        cfg = ctx.config.job
        collector = JobCollector(
            city=city,
            min_salary_k=min_salary_k or cfg.default_min_salary_k,
            exclude_companies=cfg.exclude_companies,
        )
        result = await collector.fetch_all(keyword=keyword, page=0, limit=20)
        jobs = result["jobs"]

    report = await _run_analysis(ctx, jobs or [], user_profile, keyword, city)

    # 按 search_id 缓存报告（一次搜索 ↔ 一份报告）
    save_report_cache(user_id, sid, keyword, city, min_salary_k, report)
    logger.info(
        "市场批量分析完成",
        user_id=user_id,
        jobs=report["job_count"],
        from_provided=from_provided,
        search_id=sid,
    )
    return {"cached": False, "report": report, "search_id": sid}
