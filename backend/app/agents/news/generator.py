"""
日报生成模块。

加载 `prompt/news/daily_report.md` 作为 system prompt，
调用 LLM 将筛选后的资讯条目生成结构化日报（JSON）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.logging import get_logger

logger = get_logger(__name__)

# 兜底提示词（当 prompt 文件缺失时使用，保证不崩溃）
_FALLBACK_PROMPT = """你是 AI 行业资讯编辑，根据输入的资讯条目生成结构化日报。
输出严格 JSON：{"date":"...","headline":{"title":"...","summary":"...","impact":"..."},"sections":[{"category":"...","items":[{"title":"...","source":"...","link":"...","one_liner":"...","why_matters":"..."}]}],"total_count":N}
只基于输入条目，不要编造。"""


def _resolve_prompt_path(explicit: str | None = None) -> Path | None:
    """定位提示词文件：优先显式路径，其次仓库根 prompt/ 目录。"""
    if explicit:
        p = Path(explicit)
        return p if p.exists() else None
    # 本文件位于 backend/app/agents/news/，向上 4 级即仓库根
    root = Path(__file__).resolve().parents[4]
    p = root / "prompt" / "news" / "daily_report.md"
    return p if p.exists() else None


class DailyReportGenerator:
    """基于 LLM 生成结构化日报。"""

    def __init__(self, llm_factory: Any, prompt_path: str | None = None) -> None:
        self._llm_factory = llm_factory
        path = _resolve_prompt_path(prompt_path)
        if path:
            self._system_prompt = path.read_text(encoding="utf-8")
            logger.info("已加载日报提示词", path=str(path))
        else:
            self._system_prompt = _FALLBACK_PROMPT
            logger.warning("日报提示词文件未找到，使用内置兜底提示词")

    async def generate(self, items: list[dict], role: str = "chat_simple") -> dict:
        """调用 LLM 生成日报，返回解析后的 JSON 字典。"""
        llm = self._llm_factory.get(role)
        items_json = json.dumps(items, ensure_ascii=False, indent=2)
        messages = [
            SystemMessage(content=self._system_prompt),
            HumanMessage(
                content=f"以下是今日采集的资讯条目（已筛选）：\n{items_json}\n\n请按模板生成日报。"
            ),
        ]
        resp = await llm.ainvoke(messages)
        raw = resp.content if hasattr(resp, "content") else str(resp)
        return self._parse_json(raw)

    @staticmethod
    def _parse_json(raw: Any) -> dict:
        """从 LLM 响应中稳健提取 JSON 对象。"""
        text = raw if isinstance(raw, str) else str(raw)
        # 去掉 markdown 代码围栏
        text = re.sub(r"```(?:json)?\s*", "", text).strip()
        # 截取首个 {...} 块
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            logger.error("日报生成结果非 JSON", raw=text[:200])
            return {"error": "invalid_response", "raw": text[:500]}
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError as e:
            logger.error("日报 JSON 解析失败", error=str(e), raw=text[:200])
            return {"error": "json_parse_failed", "raw": text[:500]}
