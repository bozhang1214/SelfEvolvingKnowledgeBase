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

from jobcopilot import analyze_jobs_batch
from jobcopilot.core.stats import classify_role, compute_stats  # noqa: F401  (对外转发)

from app.agents.job.llm_adapter import SekbLLMAdapter
from app.core.logging import get_logger

logger = get_logger(__name__)

_CACHE_FILE = Path("data/job_market_report.json")
_CACHE_TTL_SECONDS = 14 * 24 * 3600  # 14 天

__all__ = [
    "analyze_market",
    "get_cached_report",
    "delete_report",
    "compute_stats",
    "classify_role",
]


def _load_cache() -> dict[str, Any]:
    if not _CACHE_FILE.exists():
        return {}
    try:
        return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(data: dict[str, Any]) -> None:
    _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_cached_report(user_id: str) -> dict[str, Any] | None:
    """返回 user_id 的缓存报告；过期或不存在返回 None。"""
    cache = _load_cache().get(user_id)
    if not cache:
        return None
    ts = cache.get("analyzed_at_ts", 0)
    if time.time() - ts > _CACHE_TTL_SECONDS:
        return None
    return cache


def delete_report(user_id: str) -> bool:
    """删除 user_id 的缓存报告，返回是否真的删了。"""
    cache = _load_cache()
    existed = user_id in cache
    cache.pop(user_id, None)
    _save_cache(cache)
    return existed


async def analyze_market(
    ctx: Any,
    user_id: str,
    keyword: str,
    city: str,
    llm_factory: Any,
    user_profile: str,
    jobs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """执行批量分析，返回 ``{cached, report}``。

    ``jobs=None`` 时自动采集；``jobs`` 提供时直接分析传入的职位（「职位收集 →
    批量分析」联动，不重复采集）。无论哪种方式，报告都会缓存，供「批量分析」
    tab 默认展示最近一次结果。
    """
    from app.agents.job.generator import _resolve_prompt_dir

    from_provided = jobs is not None
    if not from_provided:
        cached = get_cached_report(user_id)
        if cached is not None:
            return {"cached": True, "report": cached}

        from app.agents.job.collector import JobCollector

        cfg = ctx.config.job
        collector = JobCollector(
            city=city,
            min_salary_k=cfg.default_min_salary_k,
            exclude_companies=cfg.exclude_companies,
        )
        result = await collector.fetch_all(keyword=keyword, page=0, limit=20)
        jobs = result["jobs"]

    prompt_dir = _resolve_prompt_dir()
    report = await analyze_jobs_batch(
        SekbLLMAdapter(llm_factory),
        jobs or [],
        user_profile,
        keyword=keyword,
        city=city,
        prompt_dir=str(prompt_dir) if prompt_dir else None,
    )

    # 无论是否提供 jobs，都缓存报告，供「批量分析」tab 默认展示最近一次结果
    cache = _load_cache()
    cache[user_id] = report
    _save_cache(cache)
    logger.info(
        "市场批量分析完成", user_id=user_id, jobs=report["job_count"], from_provided=from_provided
    )
    return {"cached": False, "report": report}
