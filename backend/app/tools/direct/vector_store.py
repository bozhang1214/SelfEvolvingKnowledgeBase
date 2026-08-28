"""
向量检索直连模块。

绕过 MCP 协议，直接 Python 调用 ChromaDB 知识库，实现低延迟检索（< 10ms）。

设计理由：
- 向量检索是延迟敏感型操作，每次对话都要调用
- MCP 协议涉及序列化/反序列化/进程通信，增加 ~40ms 开销
- 本地调用直接操作 ChromaDB 内存索引，延迟 < 10ms

使用方式：
    from app.tools.direct.vector_store import DirectVectorStore
    from app.memory.knowledge_base import ChromaKnowledgeBase

    kb = ChromaKnowledgeBase("data/chroma_db")
    vector_store = DirectVectorStore(kb)
    results = await vector_store.search("查询文本", user_id="default", top_k=5)
"""

from __future__ import annotations

import logging
from typing import Any

from app.memory.base import KnowledgeBaseBackend

logger = logging.getLogger(__name__)


class DirectVectorStore:
    """
    本地向量检索直连，绕过 MCP 协议。

    封装 KnowledgeBaseBackend 的检索接口，返回简化的字典格式，
    供 RAG 检索器和 Graph 节点直接使用。
    """

    def __init__(self, kb: KnowledgeBaseBackend):
        """
        Args:
            kb: 知识库后端实例（ChromaKnowledgeBase 或其他实现）
        """
        self.kb = kb

    async def search(
        self,
        query: str,
        user_id: str = "default",
        top_k: int = 5,
        min_score: float = 0.3,
    ) -> list[dict[str, Any]]:
        """
        直接调用知识库检索。

        Args:
            query: 查询文本
            user_id: 用户 ID（用于隔离）
            top_k: 返回的最大条目数
            min_score: 最小相似度阈值

        Returns:
            检索结果列表，每项包含：
            - content: 知识内容
            - score: 相似度分数（0.0~1.0）
            - source: 来源（conversation/document/manual）
            - source_id: 来源 ID
            - importance: 重要性评分
            - entry_id: 条目 ID
        """
        entries = await self.kb.retrieve(
            query=query,
            user_id=user_id,
            top_k=top_k,
            min_score=min_score,
        )

        results: list[dict[str, Any]] = []
        for entry in entries:
            results.append({
                "content": entry.content,
                "score": entry.similarity_score,
                "source": entry.source,
                "source_id": entry.source_id,
                "importance": entry.importance_score,
                "entry_id": entry.entry_id,
                "topic": entry.topic,
            })

        logger.debug(
            "向量检索完成",
            extra={
                "query_length": len(query),
                "user_id": user_id,
                "top_k": top_k,
                "returned": len(results),
            },
        )
        return results

    async def add(
        self,
        content: str,
        user_id: str = "default",
        source: str = "conversation",
        source_id: str = "",
        importance_score: float = 0.5,
        topic: str = "",
        category_l1: str = "其他",
        category_l2: str = "待分类",
        category_l3: str = "未分类",
        category_confidence: float = 0.0,
        category_source: str = "auto",
    ) -> str:
        """
        添加知识条目到向量库（便捷方法）。

        Args:
            content: 知识内容
            user_id: 用户 ID
            source: 来源类型
            source_id: 来源 ID
            importance_score: 重要性评分
            topic: 主题标签
            category_l1: 一级分类大类
            category_l2: 二级分类子类
            category_l3: 三级分类细类
            category_confidence: 自动分类置信度
            category_source: 分类来源（auto/manual）

        Returns:
            新条目的 entry_id
        """
        from app.memory.knowledge_entry import KnowledgeEntry

        entry = KnowledgeEntry(
            content=content,
            source=source,
            source_id=source_id,
            user_id=user_id,
            importance_score=importance_score,
            topic=topic,
            category_l1=category_l1,
            category_l2=category_l2,
            category_l3=category_l3,
            category_confidence=category_confidence,
            category_source=category_source,
        )
        return await self.kb.add(entry)

    async def count(self, user_id: str | None = None) -> int:
        """返回知识库条目总数。"""
        return await self.kb.count(user_id)
