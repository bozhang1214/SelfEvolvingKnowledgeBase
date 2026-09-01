"""
L3 长期知识库 ChromaDB 实现。

基于 ChromaDB PersistentClient 实现知识条目的持久化存储与向量检索：
- 支持添加、检索、更新、删除、计数
- 用户隔离：通过 metadata where 条件过滤
- 相似度过滤：cosine distance → similarity score
- 延迟低：本地磁盘持久化，检索 < 10ms

使用方式：
    from app.memory.knowledge_base import ChromaKnowledgeBase
    from app.core.embedding import get_embedding_function

    embed_fn = get_embedding_function()
    kb = ChromaKnowledgeBase(persist_path="data/chroma_db", embedding_fn=embed_fn)
    entry_id = await kb.add(entry)
    results = await kb.retrieve("查询文本", user_id="default", top_k=5)
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from app.memory.base import KnowledgeBaseBackend
from app.memory.knowledge_entry import KnowledgeEntry

logger = logging.getLogger(__name__)


def _op_id() -> str:
    """生成短操作 ID，用于并发追踪。"""
    return uuid.uuid4().hex[:8]


def _task_name() -> str:
    """获取当前 asyncio Task 名称，用于并发排查。"""
    try:
        task = asyncio.current_task()
        return task.get_name() if task else "no-task"
    except RuntimeError:
        return "no-loop"


def _log_op(
    op: str,
    op_id: str,
    event: str,
    *,
    entry_id: str = "",
    user_id: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    """统一的并发排查日志格式。"""
    payload: dict[str, Any] = {
        "op": op,
        "op_id": op_id,
        "event": event,
        "task": _task_name(),
        "entry_id": entry_id,
        "user_id": user_id,
    }
    if extra:
        payload.update(extra)
    logger.info(f"[KB-TRACE] {op}/{event}", extra=payload)


def _build_where_filter(
    user_id: str | None = None,
    source: str | None = None,
    category_l1: str | None = None,
    category_l2: str | None = None,
    category_l3: str | None = None,
) -> dict[str, Any] | None:
    """
    构建 ChromaDB 元数据过滤条件（多字段 AND）。

    任一字段非空即参与过滤；单字段时直接返回该字段条件，
    多字段时用 ``$and`` 组合。全部为空返回 None（不过滤）。
    """
    conditions: dict[str, Any] = {}
    if user_id:
        conditions["user_id"] = user_id
    if source:
        conditions["source"] = source
    if category_l1:
        conditions["category_l1"] = category_l1
    if category_l2:
        conditions["category_l2"] = category_l2
    if category_l3:
        conditions["category_l3"] = category_l3

    if not conditions:
        return None
    if len(conditions) == 1:
        return conditions
    return {"$and": [{k: v} for k, v in conditions.items()]}


class ChromaKnowledgeBase(KnowledgeBaseBackend):
    """
    基于 ChromaDB 的 L3 长期知识库实现。

    数据持久化到本地磁盘（PersistentClient），重启后自动恢复。
    向量检索使用 cosine similarity，通过 metadata where 条件实现用户隔离。
    """

    def __init__(
        self,
        persist_path: str = "data/chroma_db",
        embedding_fn: Any = None,
        collection_name: str = "knowledge",
    ):
        """
        Args:
            persist_path: ChromaDB 持久化目录路径
            embedding_fn: Embedding 函数，需实现 __call__(texts) -> list[list[float]]
                         若为 None，则使用默认的本地 Embedding
            collection_name: ChromaDB collection 名称
        """
        if embedding_fn is None:
            from app.core.embedding import get_embedding_function
            embedding_fn = get_embedding_function()

        self._embedding_fn = embedding_fn
        self._persist_path = persist_path
        self._collection_name = collection_name
        self._client: Any = None
        self._collection: Any = None
        self._initialized = False

    def _ensure_initialized(self) -> None:
        """懒初始化 ChromaDB 客户端和 collection。"""
        if self._initialized:
            return
        try:
            import chromadb

            self._client = chromadb.PersistentClient(path=self._persist_path)
            self._collection = self._client.get_or_create_collection(
                name=self._collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            self._initialized = True
            logger.info(
                "ChromaDB 知识库初始化完成",
                extra={
                    "persist_path": self._persist_path,
                    "collection": self._collection_name,
                    "existing_count": self._collection.count(),
                },
            )
        except ImportError:
            raise ImportError(
                "chromadb 未安装，请执行 pip install chromadb 安装"
            )

    def _embed(self, texts: list[str]) -> list[list[float]]:
        """调用 Embedding 函数生成向量（同步方法）。"""
        return self._embedding_fn(texts)

    async def _async_embed(self, texts: list[str]) -> list[list[float]]:
        """异步生成向量（用 asyncio.to_thread 包装，避免阻塞事件循环）。"""
        return await asyncio.to_thread(self._embed, texts)

    async def add(self, entry: KnowledgeEntry | dict) -> str:
        """
        添加知识条目到知识库。

        Args:
            entry: KnowledgeEntry 实例或字典（需包含 content 字段）

        Returns:
            新条目的 entry_id
        """
        oid = _op_id()
        self._ensure_initialized()

        if isinstance(entry, dict):
            entry = KnowledgeEntry(**entry)

        _log_op("add", oid, "start", entry_id=entry.entry_id, user_id=entry.user_id,
                extra={"content_len": len(entry.content), "source": entry.source})
        t0 = time.perf_counter()

        # 生成向量（异步，避免阻塞事件循环）
        embeddings = await self._async_embed([entry.content])
        _log_op("add", oid, "embed_done", entry_id=entry.entry_id,
                extra={"embed_latency_ms": int((time.perf_counter() - t0) * 1000)})

        # ChromaDB 写入用 to_thread 包装（同步 IO）
        t1 = time.perf_counter()
        await asyncio.to_thread(
            self._collection.add,
            ids=[entry.entry_id],
            embeddings=embeddings,
            documents=[entry.content],
            metadatas=[entry.to_chroma_metadata()],
        )
        _log_op("add", oid, "end", entry_id=entry.entry_id, user_id=entry.user_id,
                extra={"total_latency_ms": int((time.perf_counter() - t0) * 1000),
                       "chroma_write_ms": int((time.perf_counter() - t1) * 1000)})
        return entry.entry_id

    async def add_batch(self, entries: list[KnowledgeEntry | dict]) -> list[str]:
        """
        批量添加知识条目。

        Args:
            entries: KnowledgeEntry 实例列表或字典列表

        Returns:
            新条目的 entry_id 列表
        """
        if not entries:
            return []

        self._ensure_initialized()

        models = [
            KnowledgeEntry(**e) if isinstance(e, dict) else e
            for e in entries
        ]

        # 批量生成向量（异步）
        embeddings = await self._async_embed([m.content for m in models])

        await asyncio.to_thread(
            self._collection.add,
            ids=[m.entry_id for m in models],
            embeddings=embeddings,
            documents=[m.content for m in models],
            metadatas=[m.to_chroma_metadata() for m in models],
        )

        logger.debug(
            "批量知识入库完成",
            extra={"count": len(models)},
        )
        return [m.entry_id for m in models]

    async def retrieve(
        self,
        query: str,
        user_id: str | None = None,
        top_k: int = 5,
        min_score: float = 0.3,
    ) -> list[KnowledgeEntry]:
        """
        检索相关知识条目。

        Args:
            query: 查询文本
            user_id: 用户 ID（用于隔离，None 表示不过滤）
            top_k: 返回的最大条目数
            min_score: 最小相似度阈值（0.0~1.0），低于此值的结果被过滤

        Returns:
            KnowledgeEntry 列表，按相似度降序排列
        """
        oid = _op_id()
        self._ensure_initialized()

        _log_op("retrieve", oid, "start", user_id=user_id or "",
                extra={"query_len": len(query), "top_k": top_k, "min_score": min_score})
        t0 = time.perf_counter()

        # 生成查询向量（异步）
        query_embeddings = await self._async_embed([query])

        # 构建 where 条件（用户隔离）
        where_filter: dict[str, Any] | None = None
        if user_id is not None:
            where_filter = {"user_id": user_id}

        # ChromaDB 查询用 to_thread 包装
        t1 = time.perf_counter()
        results = await asyncio.to_thread(
            self._collection.query,
            query_embeddings=query_embeddings,
            n_results=top_k,
            where=where_filter,
            include=["documents", "metadatas", "distances"],
        )
        chroma_query_ms = int((time.perf_counter() - t1) * 1000)

        # 解析结果
        entries: list[KnowledgeEntry] = []
        ids_list = results.get("ids", [[]])
        docs_list = results.get("documents", [[]])
        metas_list = results.get("metadatas", [[]])
        dists_list = results.get("distances", [[]])

        if not ids_list or not ids_list[0]:
            _log_op("retrieve", oid, "end", user_id=user_id or "",
                    extra={"result_count": 0, "total_latency_ms": int((time.perf_counter() - t0) * 1000),
                           "chroma_query_ms": chroma_query_ms})
            return entries

        for i, doc_id in enumerate(ids_list[0]):
            distance = dists_list[0][i] if i < len(dists_list[0]) else 1.0
            score = max(0.0, 1.0 - distance)

            if score < min_score:
                continue

            entry = KnowledgeEntry.from_chroma_record(
                doc_id=doc_id,
                document=docs_list[0][i] if i < len(docs_list[0]) else "",
                metadata=metas_list[0][i] if i < len(metas_list[0]) else {},
                distance=distance,
            )
            entries.append(entry)

        _log_op("retrieve", oid, "end", user_id=user_id or "",
                extra={"result_count": len(entries), "raw_count": len(ids_list[0]),
                       "total_latency_ms": int((time.perf_counter() - t0) * 1000),
                       "chroma_query_ms": chroma_query_ms,
                       "top_score": entries[0].similarity_score if entries else 0.0})
        return entries

    async def update(
        self,
        entry_id: str,
        content: str | None = None,
        metadata_updates: dict[str, Any] | None = None,
    ) -> KnowledgeEntry | None:
        """
        更新知识条目的内容或元数据。

        Args:
            entry_id: 条目 ID
            content: 新内容（None 表示不更新内容）
            metadata_updates: 需要更新的元数据字段

        Returns:
            更新后的 KnowledgeEntry，若条目不存在返回 None
        """
        self._ensure_initialized()

        # 先读取现有条目（异步）
        existing = await asyncio.to_thread(self._collection.get, ids=[entry_id])
        if not existing["ids"]:
            return None

        old_meta = existing["metadatas"][0] if existing["metadatas"] else {}
        old_doc = existing["documents"][0] if existing["documents"] else ""

        # 合并元数据
        new_meta = {**old_meta}
        if metadata_updates:
            new_meta.update(metadata_updates)
        new_meta["updated_at"] = datetime.now(timezone.utc).isoformat()

        # 更新内容
        new_doc = content if content is not None else old_doc

        # 无论内容是否变更都显式生成向量：
        # 若只传 documents 不传 embeddings，chromadb 会懒加载默认 embedding 函数
        # （下载 all-MiniLM ONNX 模型），在离线/弱网环境下会长时间阻塞
        new_embeddings = await self._async_embed([new_doc])
        await asyncio.to_thread(
            self._collection.update,
            ids=[entry_id],
            embeddings=new_embeddings,
            documents=[new_doc],
            metadatas=[new_meta],
        )

        return KnowledgeEntry.from_chroma_record(
            doc_id=entry_id,
            document=new_doc,
            metadata=new_meta,
        )

    async def delete(self, entry_id: str) -> None:
        """删除知识条目。"""
        oid = _op_id()
        self._ensure_initialized()
        _log_op("delete", oid, "start", entry_id=entry_id)
        t0 = time.perf_counter()
        await asyncio.to_thread(self._collection.delete, ids=[entry_id])
        _log_op("delete", oid, "end", entry_id=entry_id,
                extra={"latency_ms": int((time.perf_counter() - t0) * 1000)})

    async def delete_batch(self, entry_ids: list[str]) -> None:
        """批量删除知识条目。"""
        if not entry_ids:
            return
        self._ensure_initialized()
        await asyncio.to_thread(self._collection.delete, ids=entry_ids)
        logger.debug("批量删除知识条目", extra={"count": len(entry_ids)})

    async def get(self, entry_id: str) -> KnowledgeEntry | None:
        """根据 ID 获取单个知识条目。"""
        self._ensure_initialized()
        result = await asyncio.to_thread(self._collection.get, ids=[entry_id])
        if not result["ids"]:
            return None
        return KnowledgeEntry.from_chroma_record(
            doc_id=result["ids"][0],
            document=result["documents"][0] if result["documents"] else "",
            metadata=result["metadatas"][0] if result["metadatas"] else {},
        )

    async def count(self, user_id: str | None = None) -> int:
        """返回知识库中的条目总数。"""
        self._ensure_initialized()
        if user_id is None:
            return await asyncio.to_thread(self._collection.count)
        # ChromaDB count 不支持 where 过滤，需要 get 后计数
        result = await asyncio.to_thread(
            self._collection.get, where={"user_id": user_id}
        )
        return len(result["ids"]) if result["ids"] else 0

    async def find_similar(
        self,
        query: str,
        user_id: str | None = None,
        threshold: float = 0.8,
        top_k: int = 10,
    ) -> list[KnowledgeEntry]:
        """
        查找相似条目（用于冲突检测）。

        Args:
            query: 查询文本
            user_id: 用户 ID
            threshold: 最小相似度阈值
            top_k: 返回最大条目数

        Returns:
            相似度 >= threshold 的条目列表
        """
        oid = _op_id()
        _log_op("find_similar", oid, "start", user_id=user_id or "",
                extra={"query_len": len(query), "threshold": threshold, "top_k": top_k})
        t0 = time.perf_counter()
        results = await self.retrieve(
            query=query,
            user_id=user_id,
            top_k=top_k,
            min_score=threshold,
        )
        _log_op("find_similar", oid, "end", user_id=user_id or "",
                extra={"result_count": len(results),
                       "latency_ms": int((time.perf_counter() - t0) * 1000),
                       "top_similarity": results[0].similarity_score if results else 0.0})
        return results

    async def list_entries(
        self,
        user_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
        source: str | None = None,
        category_l1: str | None = None,
        category_l2: str | None = None,
        category_l3: str | None = None,
    ) -> list[KnowledgeEntry]:
        """
        列出知识库条目（按 ChromaDB 存储顺序返回，未保证时间排序）。

        Args:
            user_id: 用户 ID（None 表示所有用户）
            limit: 返回最大条目数
            offset: 偏移量
            source: 来源类型过滤（conversation/document/manual/image）
            category_l1: 一级分类过滤
            category_l2: 二级分类过滤
            category_l3: 三级分类过滤

        Returns:
            KnowledgeEntry 列表
        """
        self._ensure_initialized()

        where_filter = _build_where_filter(
            user_id=user_id,
            source=source,
            category_l1=category_l1,
            category_l2=category_l2,
            category_l3=category_l3,
        )

        result = await asyncio.to_thread(
            self._collection.get,
            where=where_filter,
            limit=limit + offset,
        )

        entries = []
        ids = result.get("ids", [])
        docs = result.get("documents", [])
        metas = result.get("metadatas", [])

        for i in range(len(ids)):
            if i < offset:
                continue
            entries.append(
                KnowledgeEntry.from_chroma_record(
                    doc_id=ids[i],
                    document=docs[i] if i < len(docs) else "",
                    metadata=metas[i] if i < len(metas) else {},
                )
            )

        return entries

    async def count_entries(
        self,
        user_id: str | None = None,
        source: str | None = None,
        category_l1: str | None = None,
        category_l2: str | None = None,
        category_l3: str | None = None,
    ) -> int:
        """
        统计符合过滤条件的知识条目总数（支持与 list_entries 相同的过滤条件）。

        与 list_entries 配套用于分页：返回的是满足 where 条件的真实总数，
        避免仅靠 list_entries 的 limit 截断导致 total 失真。
        """
        self._ensure_initialized()
        where_filter = _build_where_filter(
            user_id=user_id,
            source=source,
            category_l1=category_l1,
            category_l2=category_l2,
            category_l3=category_l3,
        )
        if where_filter is None:
            return await asyncio.to_thread(self._collection.count)
        # include=[] 仅返回 ids，避免加载 documents/metadatas（大数据量时更省内存）
        result = await asyncio.to_thread(
            self._collection.get, where=where_filter, include=[]
        )
        return len(result.get("ids", []) or [])
