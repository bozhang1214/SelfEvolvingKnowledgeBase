"""RAGAS 式检索质量指标（LLM-as-Judge，零第三方依赖）。

对标 RAGAS 的四个核心指标，用 LLM 作为裁判打分（0~1），覆盖：
- ``context_recall``：检索上下文对标准答案（ground truth）关键信息的覆盖度
- ``context_precision``：检索上下文与查询的相关度
- ``faithfulness``：答案是否完全由检索上下文支撑（防幻觉）
- ``answer_relevance``：答案与查询的相关性

每个指标函数在 ``llm_factory`` 为 None 或裁判失败时返回 ``None``（不阻断评测），
便于在无 LLM 环境下对提示词构建 / 分数解析做纯函数单测。

使用方式：
    from app.eval.ragas_metrics import context_recall

    score = await context_recall(llm_factory, "查询", ["上下文1"], "标准答案")
"""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.logging import get_logger

logger = get_logger(__name__)

_JUDGE_SYSTEM = (
    "你是检索/生成质量的评估裁判。请严格按指令输出 JSON："
    '{"score": 0.0~1.0 之间的小数, "reason": "一句中文理由"}'
)

# 单条上下文注入提示词的最大字符数（避免超长）
_MAX_CONTEXT_CHARS = 2000


def _format_contexts(contexts: list[str]) -> str:
    """把上下文列表格式化为编号文本。"""
    if not contexts:
        return "（无）"
    blocks = []
    for i, c in enumerate(contexts, start=1):
        text = (c or "").strip()
        if len(text) > _MAX_CONTEXT_CHARS:
            text = text[:_MAX_CONTEXT_CHARS] + "…"
        blocks.append(f"[{i}] {text}")
    return "\n".join(blocks)


def build_context_recall_prompt(query: str, contexts: list[str], ground_truth: str) -> str:
    """构造 context_recall 裁判提示词。"""
    return (
        f"查询：{query}\n\n"
        f"标准答案（ground truth）：{ground_truth}\n\n"
        f"检索到的上下文：\n{_format_contexts(contexts)}\n\n"
        "请评估：检索到的上下文在多大程度上覆盖了标准答案中的关键信息？\n"
        "1.0 = 完全覆盖；0.0 = 完全未覆盖。"
    )


def build_context_precision_prompt(query: str, contexts: list[str]) -> str:
    """构造 context_precision 裁判提示词。"""
    return (
        f"查询：{query}\n\n"
        f"检索到的上下文：\n{_format_contexts(contexts)}\n\n"
        "请评估：这些上下文中有多大比例是与查询真正相关的？\n"
        "1.0 = 全部相关；0.0 = 全部不相关。"
    )


def build_faithfulness_prompt(query: str, contexts: list[str], answer: str) -> str:
    """构造 faithfulness（忠实度）裁判提示词。"""
    return (
        f"查询：{query}\n\n"
        f"检索到的上下文：\n{_format_contexts(contexts)}\n\n"
        f"待评估答案：{answer}\n\n"
        "请评估：答案中的陈述有多大比例能被检索到的上下文所支持（无编造/幻觉）？\n"
        "1.0 = 完全被支撑；0.0 = 完全无支撑。"
    )


def build_answer_relevance_prompt(query: str, answer: str) -> str:
    """构造 answer_relevance 裁判提示词。"""
    return (
        f"查询：{query}\n\n"
        f"待评估答案：{answer}\n\n"
        "请评估：该答案与查询的相关程度如何？\n"
        "1.0 = 高度相关；0.0 = 完全不相关。"
    )


def parse_judge_score(text: str) -> float | None:
    """从裁判返回文本中解析 0~1 分数；失败返回 None。

    兼容 ``{"score": 0.8}``、裸 JSON ``0.8``、以及夹杂说明文字中的浮点数。
    """
    if not text:
        return None
    s = text.strip()
    # 1. 整体 JSON
    try:
        data = json.loads(s)
        if isinstance(data, dict) and "score" in data:
            return _clamp_score(data["score"])
        if isinstance(data, (int, float)):
            return _clamp_score(data)
    except (json.JSONDecodeError, ValueError):
        pass
    # 2. 正则抓 "score" 后的数值
    m = re.search(r'"score"\s*:\s*([0-9]*\.?[0-9]+)', s)
    if m:
        return _clamp_score(float(m.group(1)))
    # 3. 抓首个浮点数
    m = re.search(r"([0-9]*\.?[0-9]+)", s)
    if m:
        return _clamp_score(float(m.group(1)))
    return None


def _clamp_score(v: Any) -> float:
    """把任意数值裁剪到 0~1。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, f))


async def _judge(llm_factory: Any, prompt: str, role: str) -> float | None:
    """调用 LLM 裁判打分，失败返回 None。"""
    if llm_factory is None:
        return None
    try:
        resp = await llm_factory.ainvoke_with_stats(
            role, [SystemMessage(content=_JUDGE_SYSTEM), HumanMessage(content=prompt)]
        )
        text = resp.content if hasattr(resp, "content") else str(resp)
        score = parse_judge_score(text)
        if score is None:
            logger.warning("裁判打分解析失败", role=role, raw=text[:200])
        return score
    except Exception as e:  # noqa: BLE001 - 裁判失败不阻断评测
        logger.warning("裁判打分失败", role=role, error=str(e))
        return None


async def context_recall(
    llm_factory: Any,
    query: str,
    contexts: list[str],
    ground_truth: str,
    role: str = "ragas",
) -> float | None:
    """检索上下文对标准答案的覆盖度（0~1）。"""
    return await _judge(llm_factory, build_context_recall_prompt(query, contexts, ground_truth), role)


async def context_precision(
    llm_factory: Any,
    query: str,
    contexts: list[str],
    role: str = "ragas",
) -> float | None:
    """检索上下文与查询的相关度（0~1）。"""
    return await _judge(llm_factory, build_context_precision_prompt(query, contexts), role)


async def faithfulness(
    llm_factory: Any,
    query: str,
    contexts: list[str],
    answer: str,
    role: str = "ragas",
) -> float | None:
    """答案被检索上下文支撑的比例（0~1，防幻觉）。"""
    return await _judge(llm_factory, build_faithfulness_prompt(query, contexts, answer), role)


async def answer_relevance(
    llm_factory: Any,
    query: str,
    answer: str,
    role: str = "ragas",
) -> float | None:
    """答案与查询的相关性（0~1）。"""
    return await _judge(llm_factory, build_answer_relevance_prompt(query, answer), role)
