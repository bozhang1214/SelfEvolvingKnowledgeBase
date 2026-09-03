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
输出严格 JSON：{"category":"...","summary":"≤600字总结+预测","items":[{"title","source","link","one_liner","why_matters","importance"}]}
只基于输入，不编造，items 按 importance 降序最多 10 条。"""


def _resolve_prompt_path(explicit: str | None = None) -> Path | None:
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
        p = d / "news" / "daily_report.md"
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

    async def generate(
        self,
        items: list[dict],
        role: str = "news_report",
        categories: list[Any] | None = None,
    ) -> dict:
        """逐类生成日报，返回 {"sections": [...], "total_count": N}。"""
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

        total = sum(len(s.get("items", [])) for s in sections)
        logger.info("逐类生成完成", section_count=len(sections), total_items=total)
        return {"sections": sections, "total_count": total}

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

    # ---------- 单类生成 ----------

    # 单类输入条目上限：降低 token 成本，并减少内容风控误判概率
    _MAX_ITEMS_PER_CATEGORY = 30
    # 单类生成最大尝试次数（内容风控多为偶发，重试常可绕过）
    _MAX_ATTEMPTS = 3

    async def _generate_category(
        self, category_name: str, items: list[dict], role: str
    ) -> dict:
        """调用 LLM 生成单类的总结预测 + 打分条目（带重试与降噪回退）。"""
        prompt = self._system_prompt.replace("{{category}}", category_name)
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
