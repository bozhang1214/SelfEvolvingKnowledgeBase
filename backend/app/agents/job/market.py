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

import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.logging import get_logger

logger = get_logger(__name__)

_CACHE_FILE = Path("data/job_market_report.json")
_CACHE_TTL_SECONDS = 7 * 24 * 3600  # 7 天

# 职位方向分类关键词（按优先级，首个命中即归类）
_ROLE_RULES: list[tuple[str, list[str]]] = [
    ("评测/质量", ["评测", "评估", "Evaluation", "测试"]),
    ("安全", ["安全"]),
    ("算法/模型", ["算法", "NLP", "大模型", "LLM", "模型", "AIOps"]),
    ("架构师/Leader", ["架构师", "Tech Lead", "技术负责人", "Leader", "架构研发"]),
    ("产品经理", ["产品经理", "产品", "PM", "策略"]),
    ("运营/策略", ["运营", "数据策略", "数据"]),
    ("研发/工程", ["后端", "引擎", "研发工程师", "开发工程师", "Harness", "Infra", "基础设施", "编排", "Orchestration", "应用"]),
]

# 热点技术关键词（在标题里统计频次）
_HOT_KEYWORDS = [
    "Harness", "Infra", "编排", "Orchestration", "评测", "Evaluation",
    "Claw", "ArkClaw", "RAG", "大模型", "LLM", "多模态", "AIOps", "SOC", "安全",
]

_SYSTEM_PROMPT = """你是资深招聘分析师。根据给定的职位采集统计与用户画像，生成一份市场分析报告。

要求：
- overview：3~5 句话概括整体市场概况
- trends：2~4 条市场趋势（Agent 方向的技术/岗位趋势）
- opportunities：5~8 个重点机会职位，每个给出 title/company/reason，reason 要结合用户画像说明为什么适合
- recommendations：3~5 条求职行动建议（投递方向/补短板/简历包装等）

只输出 JSON，格式：
{"overview": "...", "trends": ["..."], "opportunities": [{"title": "...", "company": "...", "reason": "..."}], "recommendations": ["..."]}"""


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


async def _llm_synthesize(
    llm_factory: Any,
    user_profile: str,
    keyword: str,
    city: str,
    job_count: int,
    stats: dict[str, Any],
    title_samples: str,
) -> dict[str, Any]:
    """调用 LLM 生成 overview/trends/opportunities/recommendations。"""
    payload = (
        f"用户画像：\n{user_profile}\n\n"
        f"职位采集（关键词={keyword}，城市={city}，共 {job_count} 个职位）：\n"
        f"公司分布：{json.dumps(stats['company_distribution'], ensure_ascii=False)}\n"
        f"职位方向分布：{json.dumps(stats['role_distribution'], ensure_ascii=False)}\n"
        f"热点技术关键词：{json.dumps(stats['hot_keywords'], ensure_ascii=False)}\n"
        f"职位标题样例（按公司）：\n{title_samples}"
    )
    llm = llm_factory.get("job_analysis")
    resp = await llm.ainvoke([
        SystemMessage(content=_SYSTEM_PROMPT),
        HumanMessage(content=payload),
    ])
    raw = resp.content if hasattr(resp, "content") else str(resp)
    # 稳健提取 JSON
    text = raw.strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        logger.error("市场报告 LLM 返回非 JSON", raw=text[:200])
        return {}
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        logger.error("市场报告 JSON 解析失败", error=str(e))
        return {}


async def analyze_market(
    ctx: Any,
    user_id: str,
    keyword: str,
    city: str,
    llm_factory: Any,
    user_profile: str,
) -> dict[str, Any]:
    """执行批量分析（带缓存），返回 {cached, report}。"""
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

    stats = compute_stats(jobs)

    # 标题样例：每家公司最多 6 条
    by_company: dict[str, list[str]] = {}
    for j in jobs:
        by_company.setdefault(j.get("company") or "未知", []).append(j.get("title") or "")
    samples_lines = []
    for c, titles in by_company.items():
        samples_lines.append(f"- {c}：{'、'.join(titles[:6])}")
    title_samples = "\n".join(samples_lines)

    llm = await _llm_synthesize(
        llm_factory=llm_factory,
        user_profile=user_profile,
        keyword=keyword,
        city=city,
        job_count=len(jobs),
        stats=stats,
        title_samples=title_samples,
    )

    # 组装报告
    report: dict[str, Any] = {
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "analyzed_at_ts": time.time(),
        "keyword": keyword,
        "city": city,
        "job_count": len(jobs),
        "stats": stats,
        "overview": llm.get("overview", ""),
        "trends": llm.get("trends", []),
        "opportunities": llm.get("opportunities", []),
        "recommendations": llm.get("recommendations", []),
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

    cache = _load_cache()
    cache[user_id] = report
    _save_cache(cache)
    logger.info("市场批量分析完成并缓存", user_id=user_id, jobs=len(jobs))
    return {"cached": False, "report": report}
