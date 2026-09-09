"""
职位市场批量分析服务。

对当前采集到的职位列表做「一键分析」：
1. 采集职位（复用 JobCollector）
2. 程序化统计（公司分布 / 职位方向 / 热点关键词）
3. LLM 综合生成市场报告（概况 / 趋势 / 重点机会 / 建议）
4. 结果缓存 7 天：一周内再次进入直接返回缓存，避免重复分析

缓存按 user_id 隔离，持久化在 ``data/job_market_report.json``。
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.job.generator import _resolve_prompt_dir
from app.core.logging import get_logger

logger = get_logger(__name__)

_CACHE_FILE = Path("data/job_market_report.json")
_CACHE_TTL_SECONDS = 14 * 24 * 3600  # 14 天

# 职位方向分类关键词（按优先级，首个命中即归类）
_ROLE_RULES: list[tuple[str, list[str]]] = [
    ("评测/质量", ["评测", "评估", "Evaluation", "测试"]),
    ("安全", ["安全"]),
    ("算法/模型", ["算法", "NLP", "大模型", "LLM", "模型", "AIOps"]),
    ("架构师/Leader", ["架构师", "Tech Lead", "技术负责人", "Leader", "架构研发"]),
    ("产品经理", ["产品经理", "产品", "PM", "策略"]),
    ("运营/策略", ["运营", "数据策略", "数据"]),
    (
        "研发/工程",
        ["后端", "引擎", "研发工程师", "开发工程师", "Harness", "Infra", "基础设施", "编排", "Orchestration", "应用"],
    ),
]

# 热点技术关键词（在标题里统计频次）
_HOT_KEYWORDS = [
    "Harness", "Infra", "编排", "Orchestration", "评测", "Evaluation",
    "Claw", "ArkClaw", "RAG", "大模型", "LLM", "多模态", "AIOps", "SOC", "安全",
]

# 批量分析提示词（prompt/job 目录下，站在「求职者选赛道」视角）
_PROMPT_MARKET = "批量职位分析.md"
_PROMPT_KNOWLEDGE = "职位知识迭代.md"

# 每份 JD 喂给 LLM 的摘要截断长度（控制 token，避免几十份 JD 全文超限）
_JD_BRIEF_MAX = 400


def _load_prompt(filename: str) -> str:
    """加载 prompt/job 下的提示词文件；缺失返回空串。"""
    base = _resolve_prompt_dir()
    if not base:
        return ""
    p = base / filename
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _extract_json(raw: Any) -> dict:
    """从 LLM 响应中稳健提取 JSON 对象，失败返回空结构。"""
    text = raw if isinstance(raw, str) else str(raw)
    text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        logger.error("批量分析 LLM 返回非 JSON", raw=text[:200])
        return {}
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        logger.error("批量分析 JSON 解析失败", error=str(e))
        return {}


def _build_job_summaries(jobs: list[dict[str, Any]]) -> str:
    """构造 JD 摘要文本（职位名/公司/薪资/城市 + JD 截断），供 LLM 分析。"""
    lines: list[str] = []
    for i, j in enumerate(jobs, 1):
        title = (j.get("title") or "").strip() or "（无标题）"
        meta = " | ".join(
            x for x in [
                (j.get("company") or "").strip(),
                (j.get("salary") or "").strip(),
                (j.get("city") or "").strip(),
            ] if x
        )
        jd = (j.get("jd_text") or "").strip()
        brief = jd[:_JD_BRIEF_MAX] + ("…" if len(jd) > _JD_BRIEF_MAX else "")
        line = f"{i}. 【{title}】{meta}" if meta else f"{i}. 【{title}】"
        if brief:
            line += f"\n   {brief}"
        lines.append(line)
    return "\n\n".join(lines)


async def _llm_call(
    llm_factory: Any, filename: str, user_profile: str, job_summaries: str
) -> dict:
    """用 prompt/job 下的提示词调用 LLM，返回解析后的 JSON；失败降级为空结构。"""
    prompt = _load_prompt(filename)
    if not prompt:
        logger.warning("批量分析提示词缺失，跳过", filename=filename)
        return {}
    llm = llm_factory.get("job_analysis")
    payload = f"用户画像：\n{user_profile}\n\nJD 摘要列表：\n{job_summaries}"
    try:
        resp = await llm.ainvoke([
            SystemMessage(content=prompt),
            HumanMessage(content=payload),
        ])
        raw = resp.content if hasattr(resp, "content") else str(resp)
        return _extract_json(raw)
    except Exception as e:  # noqa: BLE001
        logger.error("批量分析 LLM 调用失败", filename=filename, error=str(e)[:200])
        return {}


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


def _classify_role(title: str) -> str:
    t = (title or "").lower()
    for role, keywords in _ROLE_RULES:
        if any(k.lower() in t for k in keywords):
            return role
    return "其他"


def compute_stats(jobs: list[dict[str, Any]]) -> dict[str, Any]:
    """程序化统计：公司 / 方向 / 热点关键词。"""
    company = Counter((j.get("company") or "未知").strip() for j in jobs)
    role = Counter(_classify_role(j.get("title") or "") for j in jobs)

    all_titles = " ".join((j.get("title") or "") for j in jobs).lower()
    hot = [
        {"keyword": k, "count": all_titles.count(k.lower())}
        for k in _HOT_KEYWORDS
        if all_titles.count(k.lower()) > 0
    ]
    hot.sort(key=lambda x: -x["count"])

    return {
        "company_distribution": [
            {"name": c, "count": n} for c, n in company.most_common()
        ],
        "role_distribution": [
            {"name": r, "count": n} for r, n in role.most_common()
        ],
        "hot_keywords": hot,
    }


def _normalize_job(j: dict[str, Any]) -> dict[str, Any]:
    """归一化前端传入的职位字典，补齐必需字段。"""
    return {
        "job_id": j.get("job_id", ""),
        "title": (j.get("title") or "").strip(),
        "company": (j.get("company") or "").strip(),
        "salary": (j.get("salary") or "").strip(),
        "city": (j.get("city") or "").strip(),
        "source": (j.get("source") or "手动上传").strip(),
        "job_url": j.get("job_url", ""),
        "jd_text": (j.get("jd_text") or "").strip(),
    }


async def analyze_market(
    ctx: Any,
    user_id: str,
    keyword: str,
    city: str,
    llm_factory: Any,
    user_profile: str,
    jobs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """执行批量分析，返回 {cached, report}。

    jobs=None 时自动采集并缓存 7 天；jobs 提供时直接分析传入的职位（不缓存，
    用于「职位收集 → 批量分析」联动，避免重复采集、保证数据一致）。
    """
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
    else:
        jobs = [
            _normalize_job(j) for j in (jobs or []) if (j.get("title") or j.get("jd_text"))
        ]

    stats = compute_stats(jobs)
    job_summaries = _build_job_summaries(jobs)

    # 并行：市场行情（批量职位分析）+ 知识迭代（职位知识迭代），各自独立降级
    market, knowledge = await asyncio.gather(
        _llm_call(llm_factory, _PROMPT_MARKET, user_profile, job_summaries),
        _llm_call(llm_factory, _PROMPT_KNOWLEDGE, user_profile, job_summaries),
    )

    # 组装报告
    report: dict[str, Any] = {
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "analyzed_at_ts": time.time(),
        "keyword": keyword,
        "city": city,
        "job_count": len(jobs),
        "stats": stats,
        "market": market,
        "knowledge_iteration": knowledge,
        "jobs": [
            {
                "job_id": j.get("job_id", ""),
                "title": j.get("title"),
                "company": j.get("company"),
                "salary": j.get("salary"),
                "city": j.get("city"),
                "source": j.get("source"),
                "job_url": j.get("job_url"),
                "jd_text": (j.get("jd_text") or "")[:2000],
            }
            for j in jobs
        ],
    }

    if not from_provided:
        cache = _load_cache()
        cache[user_id] = report
        _save_cache(cache)
    logger.info(
        "市场批量分析完成", user_id=user_id, jobs=len(jobs), from_provided=from_provided
    )
    return {"cached": False, "report": report}
