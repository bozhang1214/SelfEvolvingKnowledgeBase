"""
RAG 检索器模块

基于意图路由的知识库检索增强（Retrieval-Augmented Generation）。

设计要点：
- 按意图执行不同检索策略（严格 / 优先 / 辅助 / 跳过）
- vector_store 为 None 时优雅降级（L3 未启用）
- 检索耗时统计
- 中文注释

使用方式：
    from app.tools.direct.vector_store import DirectVectorStore
    from app.tools.rag.retriever import RAGRetriever, format_rag_context

    retriever = RAGRetriever(vector_store, config={"retrieval_top_k": 5})
    result = await retriever.retrieve_for_query("查询", "user1", "kb_strict")
    if result.context:
        context_text = format_rag_context(result.context)
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from app.core.logging import get_logger
from app.tools.direct.vector_store import DirectVectorStore

logger = get_logger(__name__)


# ============================================================
# 意图常量
# ============================================================
# 与 app.graph.state.IntentType 的字符串值保持一致。
# 此处使用字符串常量而非直接导入 IntentType，避免 retriever 与 graph 层耦合；
# IntentType 是 str Enum，因此传入枚举值或原始字符串均可正确匹配。
INTENT_KB_STRICT = "kb_strict"        # 严格基于知识库
INTENT_KB_PREFER = "kb_prefer"        # 优先知识库
INTENT_WEB_DEFAULT = "web_default"    # 默认联网增强
INTENT_TASK_PLAN = "task_plan"        # 复杂任务规划
INTENT_CHITCHAT = "chitchat"          # 闲聊
INTENT_CHAT_SIMPLE = "chat_simple"    # 简单对话（未在 IntentType 中，预留）

# 需要跳过 RAG 的意图集合
_SKIP_INTENTS = frozenset({INTENT_CHITCHAT, INTENT_CHAT_SIMPLE})

# 作为辅助参考的意图集合
_AUXILIARY_INTENTS = frozenset({INTENT_WEB_DEFAULT, INTENT_TASK_PLAN})


# ============================================================
# 数据结构
# ============================================================

@dataclass
class RAGResult:
    """
    RAG 检索结果。

    Attributes:
        mode: 检索模式（strict | prefer | auxiliary | disabled）
        context: 检索结果列表（每项为 DirectVectorStore.search 返回的字典）；无结果或跳过时为 None
        fallback_message: 无结果时的提示文案，可注入到 Prompt
        retrieval_count: 实际返回的结果数
        latency_ms: 检索耗时（毫秒）
    """
    mode: str
    context: list[dict[str, Any]] | None
    fallback_message: str = ""
    retrieval_count: int = 0
    latency_ms: int = 0


# ============================================================
# RAG 检索器
# ============================================================

class RAGRetriever:
    """
    基于意图路由的 RAG 检索器。

    根据对话意图选择检索策略：
        - kb_strict  : 仅返回知识库结果，无结果时提示"知识库中暂无相关信息"
        - kb_prefer  : 返回知识库结果作为主答案来源
        - web_default / task_plan : 返回知识库结果作为辅助参考（top_k 更少、min_score 更高）
        - chitchat / chat_simple   : 跳过 RAG

    当 vector_store 为 None（L3 未启用）时，所有检索返回 mode="disabled"。
    """

    def __init__(self, vector_store: DirectVectorStore | None, config: dict[str, Any]):
        """
        Args:
            vector_store: 直连向量检索器实例；为 None 表示 L3 未启用，将优雅降级
            config: 检索配置，支持以下键：
                - retrieval_top_k: 默认返回条目数（默认 5）
                - min_score: 默认最小相似度阈值（默认 0.3）
                - importance_threshold: 重要性下限，低于此值的条目被过滤（默认 0.0 表示不过滤）
                - auxiliary_top_k: 辅助模式返回条目数（默认 retrieval_top_k 的一半，至少 2）
                - auxiliary_min_score: 辅助模式最小相似度（默认 min_score + 0.15，不超过 0.9）
        """
        self.vector_store = vector_store
        self.retrieval_top_k: int = int(config.get("retrieval_top_k", 5))
        self.min_score: float = float(config.get("min_score", 0.3))
        self.importance_threshold: float = float(config.get("importance_threshold", 0.0))
        self.auxiliary_top_k: int = int(
            config.get("auxiliary_top_k", max(2, self.retrieval_top_k // 2))
        )
        self.auxiliary_min_score: float = float(
            config.get(
                "auxiliary_min_score",
                min(0.9, self.min_score + 0.15),
            )
        )

    async def retrieve_for_query(
        self,
        query: str,
        user_id: str,
        intent: str,
    ) -> RAGResult:
        """
        根据意图执行检索策略。

        Args:
            query: 用户查询文本
            user_id: 用户 ID（用于知识库隔离）
            intent: 意图字符串（IntentType 枚举值或原始字符串均可）

        Returns:
            RAGResult 检索结果
        """
        # 1. 闲聊 / 简单对话：跳过 RAG
        if intent in _SKIP_INTENTS:
            logger.debug(
                "RAG 跳过（闲聊意图）",
                intent=intent,
                user_id=user_id,
            )
            return RAGResult(mode="disabled", context=None)

        # 2. L3 未启用：优雅降级
        if self.vector_store is None:
            logger.debug(
                "RAG 降级（vector_store 未启用）",
                intent=intent,
                user_id=user_id,
            )
            return RAGResult(
                mode="disabled",
                context=None,
                fallback_message="知识库未启用",
            )

        # 3. 按意图选择检索参数
        if intent == INTENT_KB_STRICT:
            mode = "strict"
            top_k = self.retrieval_top_k
            min_score = self.min_score
        elif intent == INTENT_KB_PREFER:
            mode = "prefer"
            top_k = self.retrieval_top_k
            min_score = self.min_score
        elif intent in _AUXILIARY_INTENTS:
            mode = "auxiliary"
            top_k = self.auxiliary_top_k
            min_score = self.auxiliary_min_score
        else:
            # 未识别的意图：保守跳过，避免无谓检索
            logger.debug("RAG 跳过（未识别意图）", intent=intent, user_id=user_id)
            return RAGResult(mode="disabled", context=None)

        # 4. 执行检索
        start = time.perf_counter()
        results = await self.vector_store.search(
            query=query,
            user_id=user_id,
            top_k=top_k,
            min_score=min_score,
        )
        latency_ms = int((time.perf_counter() - start) * 1000)

        # 5. 重要性过滤
        if self.importance_threshold > 0.0 and results:
            results = [
                r for r in results
                if float(r.get("importance", 0.0)) >= self.importance_threshold
            ]

        # 6. 构造 fallback 提示
        fallback_message = self._build_fallback_message(mode, len(results))

        logger.info(
            "RAG 检索完成",
            mode=mode,
            intent=intent,
            user_id=user_id,
            retrieval_count=len(results),
            latency_ms=latency_ms,
        )

        return RAGResult(
            mode=mode,
            context=results if results else None,
            fallback_message=fallback_message,
            retrieval_count=len(results),
            latency_ms=latency_ms,
        )

    @staticmethod
    def _build_fallback_message(mode: str, count: int) -> str:
        """根据模式和结果数生成 fallback 提示文案。"""
        if count > 0:
            return ""
        if mode == "strict":
            return "知识库中暂无相关信息"
        if mode == "prefer":
            return "知识库中暂无相关信息，将结合通用能力回答"
        # auxiliary：静默，不提示
        return ""


# ============================================================
# 上下文格式化
# ============================================================

# 来源中文映射
_SOURCE_LABELS = {
    "conversation": "对话",
    "document": "文档",
    "manual": "手工录入",
}


def format_rag_context(results: list[dict[str, Any]]) -> str:
    """
    将检索结果格式化为可注入 Prompt 的文本。

    格式：
        【知识库参考】
        以下是从您的知识库中检索到的相关信息：
        [参考1]（重要性：0.85，来源：对话）
        内容...
        [参考2]（重要性：0.72，来源：文档）
        内容...

    Args:
        results: DirectVectorStore.search 返回的检索结果列表

    Returns:
        格式化后的上下文文本；results 为空时返回空字符串
    """
    if not results:
        return ""

    lines: list[str] = [
        "【知识库参考】",
        "以下是从您的知识库中检索到的相关信息：",
    ]

    for idx, item in enumerate(results, start=1):
        importance = float(item.get("importance", 0.0))
        source = item.get("source", "unknown")
        source_label = _SOURCE_LABELS.get(source, source)
        content = (item.get("content") or "").strip()

        lines.append(f"[参考{idx}]（重要性：{importance:.2f}，来源：{source_label}）")
        lines.append(content)

    return "\n".join(lines)
