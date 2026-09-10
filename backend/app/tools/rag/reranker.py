"""LLM 列表式重排（Listwise Rerank）模块。

在混合检索「向量 + BM25 多路召回」之后，用一次 LLM 调用对候选文档整体排序，
比逐条打分（Pointwise）更省 token 与延迟。失败时优雅降级为原顺序，不阻断检索。

使用方式：
    from app.tools.rag.reranker import rerank_docs

    ranked = await rerank_docs(llm_factory, query, docs, top_n=5)
"""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.logging import get_logger

logger = get_logger(__name__)

_SYSTEM_PROMPT = (
    "你是检索结果重排器。给定一个查询和若干候选文档（已编号），"
    "请按与查询的相关度从高到低对文档排序，只输出 JSON："
    '{"ranking": [编号列表，按相关度降序，包含所有相关编号]}'
)


def build_rerank_prompt(query: str, docs: list[dict[str, Any]], max_chars: int = 800) -> str:
    """把查询与候选文档拼成重排提示词。

    Args:
        query: 用户查询文本
        docs: 候选文档列表（每项含 ``content``）
        max_chars: 每个文档内容截断的最大字符数

    Returns:
        重排提示词文本
    """
    blocks = [f"查询：{query}", "", "候选文档："]
    for i, d in enumerate(docs, start=1):
        content = (d.get("content") or "").strip()
        if len(content) > max_chars:
            content = content[:max_chars] + "…"
        blocks.append(f"[{i}] {content}")
    return "\n".join(blocks)


def parse_ranking(text: str, n: int) -> list[int]:
    """从 LLM 返回文本中解析排序编号列表。

    兼容两种输出：
    - 纯 JSON：``{"ranking": [3, 1, 5]}``
    - 裸列表：``[3, 1, 5]``（或夹杂说明文字时用正则提取首个 ``[...]`` 中的整数）

    Args:
        text: LLM 返回文本
        n: 候选文档数量（用于过滤越界编号）

    Returns:
        合法的编号列表（1..n，去重保序）；解析失败返回空列表。
    """
    if not text:
        return []
    # 先尝试整体 JSON
    try:
        data = json.loads(text)
        raw = data.get("ranking") if isinstance(data, dict) else data
        if isinstance(raw, list):
            return _valid_ranking(raw, n)
    except (json.JSONDecodeError, ValueError):
        pass
    # 回退：正则抓第一个 [...] 内的整数
    m = re.search(r"\[([0-9,\s]+)\]", text)
    if m:
        nums = re.findall(r"\d+", m.group(1))
        return _valid_ranking([int(x) for x in nums], n)
    return []


def _valid_ranking(raw: list[Any], n: int) -> list[int]:
    """过滤非法编号（非 int、越界、重复），保持出现顺序。"""
    seen: set[int] = set()
    out: list[int] = []
    for x in raw:
        if not isinstance(x, int) or isinstance(x, bool):
            continue
        if x < 1 or x > n or x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


async def rerank_docs(
    llm_factory: Any,
    query: str,
    docs: list[dict[str, Any]],
    top_n: int = 5,
    role: str = "rerank",
    max_chars: int = 800,
) -> list[dict[str, Any]]:
    """用 LLM 对候选文档做列表式重排，返回前 top_n 个。

    Args:
        llm_factory: LLM 工厂（None 或调用失败时按原顺序返回）
        query: 用户查询
        docs: 候选文档列表
        top_n: 返回前 N 个
        role: 重排使用的 LLM 角色名
        max_chars: 每个文档内容截断字符数

    Returns:
        重排后的文档列表（不超过 top_n）；任何异常都降级为原顺序。
    """
    if not docs or len(docs) <= 1:
        return docs[:top_n]
    if llm_factory is None:
        return docs[:top_n]

    try:
        prompt = build_rerank_prompt(query, docs, max_chars)
        messages = [SystemMessage(content=_SYSTEM_PROMPT), HumanMessage(content=prompt)]
        resp = await llm_factory.ainvoke_with_stats(role, messages)
        text = resp.content if hasattr(resp, "content") else str(resp)

        ranking = parse_ranking(text, len(docs))
        if not ranking:
            logger.warning("重排解析失败，降级为原顺序", role=role, raw=text[:200])
            return docs[:top_n]

        ranked = [docs[i - 1] for i in ranking]  # 1-based → 0-based
        # 未出现在 ranking 里的文档（LLM 漏排）追加到末尾，保证不丢候选
        ranked += [d for idx, d in enumerate(docs) if (idx + 1) not in ranking]
        return ranked[:top_n]
    except Exception as e:  # noqa: BLE001 - 重排失败不阻断检索，统一降级
        logger.warning("重排失败，降级为原顺序", role=role, error=str(e))
        return docs[:top_n]
