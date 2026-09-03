"""
日报生成模块（逐类生成）。

流程：关键词分类 → 每个大类单独调用 LLM（生成总结预测 + 打分排序条目）→ 组装。
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.logging import get_logger

logger = get_logger(__name__)

# 兜底提示词（当 prompt 文件缺失时使用，保证不崩溃）
_FALLBACK_PROMPT = """你是技术资讯编辑，针对「{{category}}」大类生成总结预测+精选条目。
输出严格 JSON：{"category":"...","summary":"≤600字总结+预测","items":[{"title","source","link","abstract","attention","importance"}]}
只基于输入，不编造，items 按 importance（0~10 十分制）降序最多 10 条。"""

# 头条分析兜底提示词
_HEADLINE_FALLBACK_PROMPT = """你是资深技术资讯主编，请对「今天最重要的一条资讯」写一段头条深度分析。
输出严格 JSON：{"analysis":"300~500字：为什么这是今天最重要的一条 + 对行业/读者的深层含义与后续走向"}。
只基于输入，不编造。"""

# 综合分析兜底提示词
_COMPREHENSIVE_FALLBACK_PROMPT = """你是资深技术资讯主编，请对当日全部大类资讯做跨大类关联分析 + 未来半月重大事件预测。
输出严格 JSON：{"correlation":"300~500字跨大类关联分析","forecast":"300~500字未来半月重大事件预测（3~6条）"}。
只基于输入，不编造，预测用不确定措辞。"""

# 各周期的时间语境（用于填充提示词里的 {{trigger}}/{{time_span}}/{{period_label}}/{{forecast_horizon}}）
_PERIOD_CONTEXTS = {
    "daily": {
        "trigger": "每天 09:00",
        "time_span": "过去 24 小时",
        "period_label": "当天",
        "forecast_horizon": "未来半个月",
    },
    "weekly": {
        "trigger": "每周一 09:00",
        "time_span": "过去一周",
        "period_label": "本周",
        "forecast_horizon": "未来一个月",
    },
    "monthly": {
        "trigger": "每月 1 日 09:00",
        "time_span": "过去一个月",
        "period_label": "本月",
        "forecast_horizon": "未来两个月",
    },
}


def _resolve_prompt_path(
    explicit: str | None = None, filename: str = "daily_report.md"
) -> Path | None:
    """定位提示词文件：优先显式路径，其次多个候选目录。"""
    if explicit:
        p = Path(explicit)
        return p if p.exists() else None
    here = Path(__file__).resolve()
    candidates = [
        here.parents[4] / "prompt",  # 本地：backend/app/agents/news/ → 仓库根
        here.parents[3] / "prompt",  # 容器：/app/app/agents/news/ → /app
        Path.cwd() / "prompt",
    ]
    for d in candidates:
        p = d / "news" / filename
        if p.exists():
            return p
    return None


class DailyReportGenerator:
    """基于 LLM 逐类生成结构化日报。"""

    def __init__(self, llm_factory: Any, prompt_path: str | None = None) -> None:
        self._llm_factory = llm_factory
        path = _resolve_prompt_path(prompt_path)
        if path:
            self._system_prompt = path.read_text(encoding="utf-8")
            logger.info("已加载日报提示词", path=str(path))
        else:
            self._system_prompt = _FALLBACK_PROMPT
            logger.warning("日报提示词文件未找到，使用内置兜底提示词")

        headline_path = _resolve_prompt_path(None, "headline.md")
        if headline_path:
            self._headline_prompt = headline_path.read_text(encoding="utf-8")
            logger.info("已加载头条分析提示词", path=str(headline_path))
        else:
            self._headline_prompt = _HEADLINE_FALLBACK_PROMPT
            logger.warning("头条分析提示词文件未找到，使用内置兜底提示词")

        comprehensive_path = _resolve_prompt_path(None, "comprehensive.md")
        if comprehensive_path:
            self._comprehensive_prompt = comprehensive_path.read_text(encoding="utf-8")
            logger.info("已加载综合分析提示词", path=str(comprehensive_path))
        else:
            self._comprehensive_prompt = _COMPREHENSIVE_FALLBACK_PROMPT
            logger.warning("综合分析提示词文件未找到，使用内置兜底提示词")

        # 周期语境默认按日报；生成周报/月报时由 generate(period_type=...) 覆盖
        self._ctx = self._PERIOD_CONTEXTS["daily"]

    async def generate(
        self,
        items: list[dict],
        role: str = "news_report",
        categories: list[Any] | None = None,
        period_type: str = "daily",
    ) -> dict:
        """按给定周期生成结构化报告（头条 + 逐类 + 综合分析），日报/周报/月报共用。"""
        self._ctx = self._PERIOD_CONTEXTS.get(period_type, self._PERIOD_CONTEXTS["daily"])
        cats = categories or []

        # 1. 关键词分类（多归属：一条资讯可同时归入多个大类）
        classified = self._classify(items, cats)
        for cat in cats:
            n = len(classified.get(cat.name, []))
            if n == 0:
                logger.warning("大类无分类条目", category=cat.name)
            else:
                logger.info("大类分类条目数", category=cat.name, count=n)

        # 2. 逐类生成
        sections = []
        for cat in cats:
            cat_items = classified.get(cat.name, [])
            if not cat_items:
                continue
            section = await self._generate_category(cat.name, cat_items, role)
            if section and section.get("items"):
                sections.append(section)
            elif section:
                # 该分类生成了但无条目（如"当日无相关资讯"），跳过
                logger.info("大类无条目，跳过", category=cat.name)

        # 3. 选出当天最重要的一条作为「头条」并深度分析
        headline = await self._generate_headline(sections, items, role)

        # 4. 篇尾综合分析：跨大类关联分析 + 未来半月重大事件预测
        comprehensive = await self._generate_comprehensive(headline, sections, role)

        total = sum(len(s.get("items", [])) for s in sections)
        logger.info("逐类生成完成", section_count=len(sections), total_items=total,
                    headline=bool(headline), comprehensive=bool(comprehensive))
        return {
            "headline": headline,
            "sections": sections,
            "comprehensive": comprehensive,
            "total_count": total,
        }

    # ---------- 分类 ----------

    def _classify(self, items: list[dict], categories: list[Any]) -> dict[str, list[dict]]:
        """按关键词把条目归入各大类（多归属：命中多个大类则同时归入，避免后序大类被饿死）。"""
        classified: dict[str, list[dict]] = {c.name: [] for c in categories}
        for it in items:
            text = f"{it.get('title', '')} {it.get('summary', '')}".lower()
            for c in categories:
                if any(k.lower() in text for k in c.keywords):
                    classified[c.name].append(it)
        return classified

    def _apply_period(self, prompt: str) -> str:
        """把提示词里的时间语境占位符替换为当前周期（日报/周报/月报）的值。"""
        for key, value in self._ctx.items():
            prompt = prompt.replace("{{" + key + "}}", value)
        return prompt

    # ---------- 单类生成 ----------

    # 单类输入条目上限：降低 token 成本，并减少内容风控误判概率
    _MAX_ITEMS_PER_CATEGORY = 30
    # 单类生成最大尝试次数（内容风控多为偶发，重试常可绕过）
    _MAX_ATTEMPTS = 3

    async def _generate_category(
        self, category_name: str, items: list[dict], role: str
    ) -> dict:
        """调用 LLM 生成单类的总结预测 + 打分条目（带重试与降噪回退）。"""
        prompt = self._apply_period(self._system_prompt).replace("{{category}}", category_name)
        items = items[: self._MAX_ITEMS_PER_CATEGORY]

        llm = self._llm_factory.get(role)
        last_error = ""
        for attempt in range(1, self._MAX_ATTEMPTS + 1):
            messages = self._build_messages(category_name, items, prompt)
            try:
                resp = await llm.ainvoke(messages)
                raw = resp.content if hasattr(resp, "content") else str(resp)
                parsed = self._parse_json(raw)
                if parsed and parsed.get("items"):
                    return parsed
                last_error = "非 JSON 输出或无条目"
            except Exception as e:
                last_error = str(e)
                # 内容风控（Content Exists Risk 等）偶发，重试时降噪：仅保留标题/来源/链接
                if "content exists risk" in last_error.lower() or " 400" in last_error:
                    items = [
                        {k: it.get(k) for k in ("title", "source", "link", "published")}
                        for it in items
                    ]
            if attempt < self._MAX_ATTEMPTS:
                logger.warning(
                    "大类生成失败，重试", category=category_name,
                    attempt=attempt, error=last_error[:200],
                )
                await asyncio.sleep(2 * attempt)
        logger.error("大类生成最终失败", category=category_name, error=last_error[:200])
        return {}

    @staticmethod
    def _build_messages(category_name: str, items: list[dict], prompt: str) -> list:
        items_json = json.dumps(items, ensure_ascii=False)
        return [
            SystemMessage(content=prompt),
            HumanMessage(
                content=(
                    f"以下是「{category_name}」大类下的资讯条目：\n{items_json}\n\n"
                    "请生成总结预测 + 精选打分条目。"
                )
            ),
        ]

    # ---------- 头条 ----------

    async def _generate_headline(
        self, sections: list[dict], items: list[dict], role: str
    ) -> dict | None:
        """从所有大类条目中选出 importance 最高的一条作为头条，并生成深度分析。"""
        best = None
        for s in sections:
            for it in s.get("items", []):
                score = it.get("importance") or 0
                if best is None or score > (best.get("importance") or 0):
                    best = it
        if not best:
            return None
        content = self._match_content(best, items)
        analysis = await self._generate_headline_analysis(best, content, role)
        return {
            "title": best.get("title", ""),
            "source": best.get("source", ""),
            "link": best.get("link", ""),
            "importance": best.get("importance"),
            "abstract": best.get("abstract", ""),
            "attention": best.get("attention", ""),
            "analysis": analysis,
        }

    @staticmethod
    def _match_content(headline_item: dict, items: list[dict]) -> str:
        """按 link/title 把头条条目映射回原始输入，取回原文正文供深度分析。"""
        link = (headline_item.get("link") or "").strip()
        title = (headline_item.get("title") or "").strip()
        if link:
            for it in items:
                if (it.get("link") or "").strip() == link:
                    return it.get("content", "") or ""
        if title:
            for it in items:
                if (it.get("title") or "").strip() == title:
                    return it.get("content", "") or ""
        return ""

    async def _generate_headline_analysis(
        self, headline_item: dict, content: str, role: str
    ) -> str:
        """调用 LLM 生成头条深度分析（300~500 字）。"""
        payload = {
            "title": headline_item.get("title", ""),
            "source": headline_item.get("source", ""),
            "link": headline_item.get("link", ""),
            "importance": headline_item.get("importance"),
            "abstract": headline_item.get("abstract", ""),
            "attention": headline_item.get("attention", ""),
            "content": (content or "")[:2000],
        }
        messages = [
            SystemMessage(content=self._apply_period(self._headline_prompt)),
            HumanMessage(
                content=f"以下是{self._ctx['period_label']}最重要的一条资讯：\n{json.dumps(payload, ensure_ascii=False)}\n\n请写头条深度分析。"
            ),
        ]
        try:
            llm = self._llm_factory.get(role)
            resp = await llm.ainvoke(messages)
            raw = resp.content if hasattr(resp, "content") else str(resp)
            parsed = self._parse_json(raw)
            return (parsed.get("analysis") or "").strip()
        except Exception as e:  # noqa: BLE001
            logger.error("头条分析生成失败", error=str(e)[:200])
            return ""

    # ---------- 综合分析 ----------

    async def _generate_comprehensive(
        self, headline: dict | None, sections: list[dict], role: str
    ) -> dict:
        """生成篇尾综合分析：跨大类关联分析 + 未来半月重大事件预测。"""
        context = {
            "headline": (
                {
                    "title": headline.get("title", ""),
                    "importance": headline.get("importance"),
                    "analysis": headline.get("analysis", ""),
                }
                if headline else None
            ),
            "categories": [
                {"category": s.get("category", ""), "summary": s.get("summary", "")}
                for s in sections
            ],
        }
        messages = [
            SystemMessage(content=self._apply_period(self._comprehensive_prompt)),
            HumanMessage(
                content=(
                    f"以下是{self._ctx['period_label']}报告的头条与各分类总结：\n"
                    f"{json.dumps(context, ensure_ascii=False)}\n\n"
                    f"请做跨大类关联分析 + {self._ctx['forecast_horizon']}重大事件预测。"
                )
            ),
        ]
        try:
            llm = self._llm_factory.get(role)
            resp = await llm.ainvoke(messages)
            raw = resp.content if hasattr(resp, "content") else str(resp)
            parsed = self._parse_json(raw)
            return {
                "correlation": (parsed.get("correlation") or "").strip(),
                "forecast": (parsed.get("forecast") or "").strip(),
            }
        except Exception as e:  # noqa: BLE001
            logger.error("综合分析生成失败", error=str(e)[:200])
            return {}

    # ---------- 解析 ----------

    @staticmethod
    def _parse_json(raw: Any) -> dict:
        """从 LLM 响应中稳健提取 JSON 对象。"""
        text = raw if isinstance(raw, str) else str(raw)
        text = re.sub(r"```(?:json)?\s*", "", text).strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            logger.error("大类生成结果非 JSON", raw=text[:200])
            return {}
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError as e:
            logger.error("大类 JSON 解析失败", error=str(e), raw=text[:200])
            return {}
