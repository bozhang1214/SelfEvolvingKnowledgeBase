"""
重建 ChromaDB 知识库向量索引（恢复脚本）。

适用场景：
    ChromaDB 的 HNSW 段损坏/丢失，导致检索（search）返回空，
    但 chroma.sqlite3 里的文档文本与元数据仍然完好。

原理：
    文档文本在 SQLite 中完整保留，嵌入模型（BAAI/bge-small-zh-v1.5）是确定的，
    因此可以「重嵌入文档 → 重建向量」，无需原始文件即可恢复检索能力。

流程：
    1. 读取 collection 全部 (id, document, metadata)，先导出 JSON 备份。
    2. 用应用同一 embedding 函数重新嵌入所有文档。
    3. 删除旧 collection，重建同名 collection，分批复写（保留原 id + 原 metadata + 新向量）。
    4. 校验：count 一致 + 一次检索探针返回非空。

用法（需在容器内执行，且建议先停 backend 避免并发写）：
    docker exec sekb-backend python3 /app/scripts/rebuild_chroma_vectors.py \
        [--path /app/data/chroma_db] [--collection knowledge] [--batch 500]

参数：
    --path         ChromaDB 持久化目录（默认 /app/data/chroma_db）
    --collection   collection 名称（默认 knowledge）
    --batch        批量大小（默认 500）
    --dry-run      只导出备份并计算向量数，不真正重建
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def _log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).isoformat()}] {msg}", flush=True)


def load_embedding_fn():
    """复用应用同一 embedding 函数，保证向量与历史一致。"""
    sys.path.insert(0, "/app")
    from app.core.embedding import get_embedding_function

    fn = get_embedding_function(allow_hash_fallback=False)
    _log(f"Embedding 函数就绪: {getattr(fn, 'model_name', 'unknown')}")
    return fn


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", default="/app/data/chroma_db")
    parser.add_argument("--collection", default="knowledge")
    parser.add_argument("--batch", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    import chromadb

    _log(f"打开 PersistentClient: {args.path}")
    client = chromadb.PersistentClient(path=args.path)
    col = client.get_collection(args.collection)
    total = col.count()
    _log(f"collection={args.collection} count={total} metadata={col.metadata}")

    if total == 0:
        _log("collection 为空，无需重建")
        return 0

    # 1. 读取全部文档与元数据（不加载向量，避免大内存）
    _log("读取全部 (id, document, metadata) ...")
    t0 = time.time()
    data = col.get(include=["documents", "metadatas"])
    ids = data["ids"]
    docs = data["documents"]
    metas = data["metadatas"]
    _log(f"读取 {len(ids)} 条，耗时 {time.time() - t0:.1f}s")

    # 校验返回一致性
    assert len(ids) == len(docs) == len(metas) == total, (
        f"读取不一致: ids={len(ids)} docs={len(docs)} metas={len(metas)} total={total}"
    )

    # 2. 导出 JSON 备份（解耦 ChromaDB 的二次保险）
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    export_path = Path(args.path).parent / f"kb_export_{ts}.json"
    _log(f"导出备份到 {export_path} ...")
    payload = [
        {"id": i, "document": d, "metadata": m}
        for i, d, m in zip(ids, docs, metas)
    ]
    with open(export_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    _log(f"备份完成: {export_path} ({len(payload)} 条)")

    if args.dry_run:
        _log("--dry-run 模式，到此为止")
        return 0

    # 3. 重嵌入
    embed_fn = load_embedding_fn()
    _log("开始重嵌入（首次会下载模型，可能较慢）...")
    t0 = time.time()
    embeddings = []
    for start in range(0, len(docs), args.batch):
        batch = docs[start : start + args.batch]
        vecs = embed_fn(batch)
        embeddings.extend(vecs)
        _log(f"  嵌入进度 {min(start + args.batch, len(docs))}/{len(docs)}")
    _log(f"重嵌入完成，耗时 {time.time() - t0:.1f}s，维度={len(embeddings[0]) if embeddings else 0}")

    # 4. 重建 collection
    _log("删除旧 collection ...")
    client.delete_collection(args.collection)
    col = client.create_collection(
        name=args.collection,
        metadata={"hnsw:space": "cosine"},
    )
    _log("重建 collection 完成，开始分批复写 ...")
    for start in range(0, len(ids), args.batch):
        end = start + args.batch
        col.add(
            ids=ids[start:end],
            documents=docs[start:end],
            metadatas=metas[start:end],
            embeddings=embeddings[start:end],
        )
        _log(f"  写入进度 {end}/{len(ids)}")

    # 5. 校验
    new_total = col.count()
    _log(f"校验 count: 期望 {total}，实际 {new_total}")
    if new_total != total:
        _log("❌ count 不一致，恢复可能不完整")
        return 1

    # 检索探针：用一条常见词做一次 query，确认能返回结果
    _log("运行检索探针 ...")
    probe_vec = embed_fn(["技术开发 向量检索 知识库"])
    res = col.query(query_embeddings=probe_vec, n_results=3)
    hit = len(res["ids"][0]) if res.get("ids") else 0
    _log(f"检索探针命中 {hit} 条")
    if hit == 0:
        _log("❌ 检索探针返回空，向量可能未生效")
        return 1

    _log("✅ 向量重建完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
