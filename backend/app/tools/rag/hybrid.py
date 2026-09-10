"""混合检索模块：向量 + BM25 多路召回 → RRF 融合 → 可选重排/查询改写。

与 ``DirectVectorStore`` 保持相同的 ``search`` 契约，可作为 ``RAGRetriever`` 的
``vector_store`` 直接替换使用，上层（意图路由 / 重要性过滤）零改动。

检索流程：
    1. （可选）查询改写：用 LLM 改写查询，改善召回（默认关闭）
    2. 向量召回：top_k*2，应用 min_score
    3. BM25 召回：top_k*2（关键词精确匹配通道）
    4. RRF 融合：按 entry_id 合并去重，rank 融合排序
    5. （可选）LLM 列表式重排：对融合结果二次排序（默认关闭）
    6. 截断返回 top_k

使用方式：
    from app.tools.rag.hybrid import HybridRetriever

    hybrid = HybridRetriever(direct_vector_store, config={...}, llm_factory=llm_factory)
    results = await hybrid.search("查询", user_id="u1", top_k=5, min_score=0.3)
"""

from __future__ import annotations

import time
from typing import Any

from app.core.logging import get_logger
from app.tools.rag.bm25 import BM25Index
from app.tools.rag.reranker import rerank_docs

logger = get_logger(__name__)

# RRF 融合常数（rank 从 1 起）
_RRF_K = 60.0


class HybridRetriever:
    """向量 + BM25 混合检索器，实现与 ``DirectVectorStore`` 一致的 search 接口。"""

    def __init__(
        self,
        vector_store: Any,
        config: dict[str, Any] | None = None,
        llm_factory: Any = None,
    ) -> None:
        """
        Args:
            vector_store: DirectVectorStore（需暴露 ``kb`` 属性以便构建 BM25 语料）
            config: 混合检索配置，支持：
                - bm25_enabled: 是否启用 BM25 通道（默认 True）
                - bm25_cache_ttl: BM25 索引缓存秒数（默认 300）
                - bm25_page_size: 分页拉取语料每页条数（默认 1000）
                - rerank_enabled: 是否启用 LLM 重排（默认 False）
                - rerank_top_n: 重排后保留条数（默认取 top_k）
                - rerank_role: 重排 LLM 角色（默认 "rerank"）
                - query_rewrite_enabled: 是否启用查询改写（默认 False）
                - query_rewrite_role: 查询改写 LLM 角色（默认 "rerank"）
            llm_factory: LLM 工厂（重排/改写需要；None 时自动降级跳过）
        """
        cfg = config or {}
        self.vector_store = vector_store
        self.llm_factory = llm_factory
        self.bm25_enabled = bool(cfg.get("bm25_enabled", True))
        self.bm25_cache_ttl = float(cfg.get("bm25_cache_ttl", 300))
        self.bm25_page_size = int(cfg.get("bm25_page_size", 1000))
        self.rerank_enabled = bool(cfg.get("rerank_enabled", False))
        self.rerank_top_n = int(cfg.get("rerank_top_n", 0))  # 0 = 用 top_k
        self.rerank_role = cfg.get("rerank_role", "rerank")
        self.query_rewrite_enabled = bool(cfg.get("query_rewrite_enabled", False))
        self.query_rewrite_role = cfg.get("query_rewrite_role", "rerank")

        # BM25 索引缓存：user_id -> (timestamp, BM25Index)
        self._bm25_cache: dict[str, tuple[float, BM25Index]] = {}

    async def search(
        self,
        query: str,
        user_id: str = "default",
        top_k: int = 5,
        min_score: float = 0.3,
        category_l1: str | None = None,
        category_l2: str | None = None,
        category_l3: str | None = None,
    ) -> list[dict[str, Any]]:
        """多路召回 → RRF 融合 → 可选重排，返回 top_k 个结果（格式同 DirectVectorStore.search）。"""
        recall_k = max(top_k * 2, 10)

        # 0. 查询改写（可选，失败静默降级为原查询）
        effective_query = await self._rewrite_query(query)

        # 1. 向量召回
        vec_results = await self.vector_store.search(
            query=effective_query,
            user_id=user_id,
            top_k=recall_k,
            min_score=min_score,
            category_l1=category_l1,
            category_l2=category_l2,
            category_l3=category_l3,
        )

        # 2. BM25 召回
        bm25_results: list[dict[str, Any]] = []
        if self.bm25_enabled:
            index = await self._get_bm25_index(user_id)
            for doc, _score in index.search(effective_query, top_k=recall_k):
                bm25_results.append(dict(doc))

        # 3. RRF 融合（按 entry_id 去重）
        fused = self._rrf_fuse(vec_results, bm25_results)

        # 4. 可选重排
        if self.rerank_enabled and len(fused) > top_k:
            rerank_n = self.rerank_top_n or top_k
            fused = await rerank_docs(
                self.llm_factory, effective_query, fused, top_n=rerank_n, role=self.rerank_role
            )

        logger.info(
            "混合检索完成",
            user_id=user_id,
            vec_count=len(vec_results),
            bm25_count=len(bm25_results),
            fused_count=len(fused),
            top_k=top_k,
        )
        return fused[:top_k]

    # ============================================================
    # 内部实现
    # ============================================================

    async def _get_bm25_index(self, user_id: str) -> BM25Index:
        """获取（或构建）某用户的 BM25 索引，带 TTL 缓存。"""
        now = time.time()
        cached = self._bm25_cache.get(user_id)
        if cached is not None and now - cached[0] < self.bm25_cache_ttl:
            return cached[1]

        index = BM25Index()
        kb = getattr(self.vector_store, "kb", None)
        if kb is not None:
            docs: list[dict[str, Any]] = []
            offset = 0
            while True:
                batch = await kb.list_entries(
                    user_id=user_id, limit=self.bm25_page_size, offset=offset
                )
                if not batch:
                    break
                docs.extend(
                    {
                        "entry_id": e.entry_id,
                        "content": e.content,
                        "source": e.source,
                        "source_id": e.source_id,
                        "importance": e.importance_score,
                        "topic": e.topic,
                    }
                    for e in batch
                )
                offset += len(batch)
                if len(batch) < self.bm25_page_size:
                    break
            index.build(docs)
        self._bm25_cache[user_id] = (now, index)
        return index

    async def _rewrite_query(self, query: str) -> str:
        """用 LLM 改写查询以改善召回；关闭或失败时返回原查询。"""
        if not self.query_rewrite_enabled or self.llm_factory is None:
            return query
        try:
            from langchain_core.messages import HumanMessage, SystemMessage

            messages = [
                SystemMessage(
                    content="你是检索查询改写器。把用户的提问改写为更适合知识库检索的"
                    "简短查询（补充同义词、去除口语/指代），只输出改写后的查询文本，不要解释。"
                ),
                HumanMessage(content=query),
            ]
            resp = await self.llm_factory.ainvoke_with_stats(self.query_rewrite_role, messages)
            text = (resp.content if hasattr(resp, "content") else str(resp)).strip()
            return text or query
        except Exception as e:  # noqa: BLE001 - 改写失败不影响主流程
            logger.warning("查询改写失败，使用原查询", error=str(e))
            return query

    @staticmethod
    def _rrf_fuse(
        vec_results: list[dict[str, Any]],
        bm25_results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """按 entry_id 做 RRF 融合，返回按融合分降序的列表。"""
        rrf: dict[str, float] = {}
        merged: dict[str, dict[str, Any]] = {}
        vec_score: dict[str, float] = {}
        bm25_rank: dict[str, int] = {}

        for rank, r in enumerate(vec_results):
            eid = r.get("entry_id") or f"vec-{rank}"
            rrf[eid] = rrf.get(eid, 0.0) + 1.0 / (_RRF_K + rank + 1)
            vec_score[eid] = float(r.get("score", 0.0))
            merged[eid] = r
        for rank, r in enumerate(bm25_results):
            eid = r.get("entry_id") or f"bm25-{rank}"
            rrf[eid] = rrf.get(eid, 0.0) + 1.0 / (_RRF_K + rank + 1)
            if eid not in merged:
                merged[eid] = r
                bm25_rank[eid] = rank

        ordered = sorted(merged, key=lambda e: -rrf[e])
        out: list[dict[str, Any]] = []
        for eid in ordered:
            item = dict(merged[eid])
            if eid in vec_score:
                item["score"] = vec_score[eid]
            else:
                # BM25 独有命中：用 rank 伪相似度（0~1，仅用于展示）
                item["score"] = round(1.0 / (bm25_rank.get(eid, 0) + 2), 3)
            out.append(item)
        return out
