"""四层职位「搜索 + 批量分析」的定时刷新服务。

**为什么需要它**：SEKB 的职位分析此前只能在前端手工点——搜索、筛选、批量分析、
存档四步都要人守着。定时化之后，四层搜索每两周自动刷新一次，报告随之更新。

**关键设计（踩过的坑都在注释里）**：

1. **层标签 ≠ 搜索词**。`第一层·解决方案售前` 这类是**合成标签**，直接拿去搜没有结果——
   当初的列表是人工把多个真实关键词的结果合并出来的。所以配置里每层同时给
   ``label``（合成标签，**决定 search_id，必须保持稳定**）与 ``keywords``（真实搜索词）。
   采集用 ``keywords``，落库用 ``label``——这样前端的四层 Tab 与既有 search_id 都不变。
2. **刷新是「替换 + 筛选」**。裸采的噪声极大（实测 FDE 22 词采到 1481 条，筛选后 89 条），
   所以每层配 ``include_any`` / ``require_ai`` / ``exclude`` / ``salary_floor_k`` 规则，
   过滤后再替换旧列表。规则写在 ``config.yaml``，可随时改，不必动代码。
3. **报告与职位列表一起更新**。刷新后必须删掉该 search_id 的旧报告缓存再重跑，
   否则前端会拿到「新列表 + 旧报告」的组合（``report_matched`` 变 false）。
4. **隔周靠「周序号奇偶」而不是「进程内计时器」**。cron 只能表达「每周一」，
   若用 IntervalTrigger(weeks=2)，容器一重启计时就从重启时刻重算，周期会漂。
   本实现让 cron 每周一都触发，再用**日期本身的 ISO 周奇偶**决定是否真跑——
   与进程存活无关，重启、迁移、补跑都不会漂。
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

from app.agents.job.collector import JobCollector
from app.agents.job.job_cache import cache_key, make_search_id, save_cached_jobs
from app.agents.job.profile import load_user_profile
from app.core.logging import get_logger

logger = get_logger(__name__)

# AI 信号（require_ai 用）：标题或 JD 正文命中其一即算 AI 相关岗
_AI_RE = re.compile(
    r"AI\b|AIGC|大模型|LLM|Agent|智能体|人工智能|机器学习|深度学习|NLP|多模态|"
    r"Copilot|数字人|RAG|知识库|生成式|智能|模型",
    re.I,
)
_JD_SCAN_CHARS = 2000  # JD 正文只扫前 N 字，够判断是否 AI 相关


# ============================================================
# 薪资解析（区间上限）
# ============================================================


def parse_salary_max_k(salary: str) -> int | None:
    """解析薪资区间**上限**（K/月），无法解析返回 None。

    为什么不复用 ``fetcher.parse_min_salary``：那个函数只取下限，且对「万 / 元」单位
    **没有换算**（实测 `2-3万`→2、`8000-16000元`→8000，应为 20 与 8），
    本模块的 ``salary_floor_k`` 规则依赖正确的上限值，故在此独立实现。
    （该 bug 已单独记录上报，是否修 ``parse_min_salary`` 由 owner 决定，避免擅改共享过滤语义。）

    支持：`35-55k·15薪` / `2.5-5万·15薪` / `1.6-2.5万` / `8000-16000元` / `40k` / `面议`。
    """
    if not salary:
        return None
    text = salary.strip()
    # 先取出数值区间（支持小数），再看单位决定换算
    m = re.search(r"(\d+(?:\.\d+)?)\s*[-~—到至]\s*(\d+(?:\.\d+)?)", text)
    raw = m.group(2) if m else None
    if raw is None:
        # 单值：30k / 5万 / 12000元
        m2 = re.search(r"(\d+(?:\.\d+)?)", text)
        if not m2:
            return None
        raw = m2.group(1)
    value = float(raw)
    # 单位判定：万 → ×10（万/月 ≈ 10K）；元 → ÷1000；缺省 k → ×1
    if "万" in text:
        return int(round(value * 10))
    if "元" in text:
        return int(round(value / 1000))
    return int(round(value))


# ============================================================
# 隔周判定
# ============================================================


def is_run_week(today: date, anchor: date, biweekly: bool = True) -> bool:
    """今天是否该跑（隔周语义）：**距锚点的天数能被 14 整除**。

    ``biweekly=False`` 时恒为 True（每周都跑）。

    为什么不数周序号：最初用「ISO 周奇偶」，但**跨年时会断**——
    锚点 2026-12-28 是第 53 周（奇），+2 周后 2027-01-11 是第 2 周（偶），
    奇偶翻转导致该跑的那次被误判为跳过（也可能反向多跑一次）。
    改用「距锚点天数 % 14」后，跨年、跨闰年、跨 53 周年都精确成立；
    且判据只依赖**日期本身**，容器重启/迁移/补跑都不会让周期漂移。
    """
    if not biweekly:
        return True
    return (today - anchor).days % 14 == 0


# ============================================================
# 单层筛选
# ============================================================


def filter_layer_jobs(
    jobs: list[dict[str, Any]], layer: Any
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """按层规则筛选职位，返回 ``(保留, 各原因剔除计数)``。

    规则（全部可选，空/0 表示不限制）：
    - ``include_any``：标题命中任一才保留；
    - ``require_ai``：还要求标题或 JD 含 AI 信号；
    - ``exclude``：标题命中任一即剔除；
    - ``salary_floor_k``：薪资区间**上限**低于此值则剔除（薪资不可解析时保留，
      避免把「面议」的大厂岗误杀）。
    """
    include_any = list(getattr(layer, "include_any", None) or [])
    exclude = list(getattr(layer, "exclude", None) or [])
    require_ai = bool(getattr(layer, "require_ai", False))
    floor_k = int(getattr(layer, "salary_floor_k", 0) or 0)

    kept: list[dict[str, Any]] = []
    dropped: dict[str, int] = {}

    def _drop(reason: str) -> None:
        dropped[reason] = dropped.get(reason, 0) + 1

    for j in jobs:
        title = (j.get("title") or "").strip()
        if not title:
            _drop("无标题")
            continue
        if include_any and not any(k in title for k in include_any):
            _drop("不含 include_any")
            continue
        if exclude and any(k in title for k in exclude):
            _drop("命中 exclude")
            continue
        if require_ai:
            blob = title + " " + (j.get("jd_text") or "")[:_JD_SCAN_CHARS]
            if not _AI_RE.search(blob):
                _drop("无 AI 信号")
                continue
        if floor_k > 0:
            top = parse_salary_max_k(j.get("salary") or "")
            if top is not None and top < floor_k:
                _drop(f"薪资上限<{floor_k}K")
                continue
        kept.append(j)
    return kept, dropped


# ============================================================
# 单层刷新 / 全部刷新
# ============================================================


async def refresh_one_layer(
    ctx: Any, user_id: str, layer: Any, *, force_analyze: bool = True
) -> dict[str, Any]:
    """刷新单层：采集 → 筛选 → 落库 → 重跑分析 → 存档。"""
    from app.agents.job.archive import save_report
    from app.agents.job.market import analyze_market, delete_report

    cfg = ctx.config.job
    label = str(layer.label).strip()
    city = (getattr(layer, "city", "") or cfg.default_city).strip()
    keywords = [k.strip() for k in (layer.keywords or []) if k.strip()]
    min_salary_k = int(getattr(layer, "min_salary_k", 0) or 0)
    if not label or not keywords:
        return {"label": label, "ok": False, "error": "label 或 keywords 为空"}

    collected = 0
    kept: list[dict[str, Any]] = []
    dropped: dict[str, int] = {}
    for kw in keywords:
        collector = JobCollector(
            city=city,
            min_salary_k=min_salary_k,
            exclude_companies=cfg.exclude_companies,
        )
        result = await collector.fetch_all(keyword=kw, page=0, limit=40)
        collected += int(result.get("count") or 0)
        part, d = filter_layer_jobs(result.get("jobs") or [], layer)
        kept.extend(part)
        for k, v in d.items():
            dropped[k] = dropped.get(k, 0) + v

    # 跨关键词去重（按 job_url，其次 title+company）
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for j in kept:
        key = j.get("job_url") or f"{j.get('title', '')}@{j.get('company', '')}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(j)

    key = cache_key(user_id, label, city, min_salary_k)
    search_id = make_search_id(key)
    save_cached_jobs(key, unique)

    if force_analyze:
        # 必须先删旧报告：否则 analyze_market 命中缓存，前端会拿到「新列表 + 旧报告」
        delete_report(user_id, search_id)

    analysis = await analyze_market(
        ctx=ctx,
        user_id=user_id,
        keyword=label,
        city=city,
        llm_factory=ctx.llm_factory,
        user_profile=load_user_profile(user_id),
        jobs=unique,
        search_id=search_id,
        min_salary_k=min_salary_k,
    )
    rep = analysis["report"]
    save_report(
        user_id,
        "batch",
        f"批量分析 · {rep['keyword']}（{rep['job_count']} 个职位）",
        rep,
        search_id=analysis.get("search_id", search_id),
    )
    summary = {
        "label": label,
        "ok": True,
        "search_id": search_id,
        "keywords": len(keywords),
        "collected": collected,
        "kept": len(unique),
        "dropped": dropped,
        "job_count": rep.get("job_count"),
    }
    logger.info("定时刷新：单层完成", **{k: v for k, v in summary.items() if k != "dropped"})
    return summary


async def refresh_all_layers(
    ctx: Any, user_id: str, layers: list[Any], *, force_analyze: bool = True
) -> dict[str, Any]:
    """逐层刷新（**串行**：采集已受 source_guard 节流，并行只会给站点更大压力）。"""
    results: list[dict[str, Any]] = []
    for layer in layers:
        try:
            results.append(await refresh_one_layer(ctx, user_id, layer, force_analyze=force_analyze))
        except Exception as e:  # noqa: BLE001 - 单层失败不阻断其它层
            logger.error("定时刷新：单层失败", label=getattr(layer, "label", "?"), error=str(e)[:200])
            results.append({"label": getattr(layer, "label", "?"), "ok": False, "error": str(e)[:200]})
    ok = sum(1 for r in results if r.get("ok"))
    logger.info("定时刷新：全部完成", layers=len(layers), ok=ok)
    return {"layers": len(layers), "ok": ok, "results": results}
