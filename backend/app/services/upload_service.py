"""文件上传入库领域服务（WP2 从 `api/routes/upload.py` 提取）。

收敛原先内联在路由层的文件/MD5/入库流水线逻辑：
- MD5 去重索引（load/save/record/compute）
- 图片配置与图片/文档原文件持久化
- 解析 → 分块 → 入库完整流水线（process_and_ingest / ingest_chunks）
- 覆盖删除、后台异步入库、文档自动分类、知识库 LLM 概览

路由层只保留 HTTP 契约（请求/响应模型 + 鉴权 + 503 守卫 + 端点壳）。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.bootstrap import AppContext
from app.core.logging import get_logger
from app.tools.file_processor import FileProcessor
from app.tools.image_processor import SUPPORTED_IMAGE_EXTENSIONS

logger = get_logger(__name__)

# 文档来源标记与默认重要性评分
DOCUMENT_SOURCE = "document"
IMAGE_SOURCE = "image"
DEFAULT_IMPORTANCE_SCORE = 0.5

# 单文件最大大小（50MB），超过则拒绝
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024

# 图片原图保存目录
IMAGE_UPLOAD_DIR = Path("data/uploads/images")

# 文档原始文件保存目录（L1 兜底：ChromaDB 全毁时可用原始文件重灌）
DOCUMENT_UPLOAD_DIR = Path("data/uploads/documents")

# 已上传文件内容 MD5 索引（file_name -> md5），用于「同名同内容」跳过
MD5_FILE = Path("data/uploaded_md5.json")


@dataclass
class IngestResult:
    """文件入库结果（领域类型，路由层转换为 UploadResponse）。"""

    file_name: str
    file_size: int
    chunks_count: int
    ingested_count: int
    status: str  # "success" | "partial" | "error"
    error: str = ""
    entry_ids: list[str] = field(default_factory=list)
    category: dict[str, Any] | None = None
    series: str = ""


def load_md5_index() -> dict[str, str]:
    """读取已上传文件的 MD5 索引；文件不存在或损坏返回空字典。"""
    if not MD5_FILE.exists():
        return {}
    try:
        return json.loads(MD5_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_md5_index(data: dict[str, str]) -> None:
    """保存已上传文件的 MD5 索引。"""
    MD5_FILE.parent.mkdir(parents=True, exist_ok=True)
    MD5_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def record_md5(file_name: str, md5: str, user_id: str) -> None:
    """记录某文件的 MD5（按用户隔离，覆盖同用户同名旧值）。"""
    data = load_md5_index()
    data[f"{user_id}|{file_name}"] = md5
    save_md5_index(data)


def compute_md5(content: bytes) -> str:
    """计算字节内容的 SHA-256 十六进制摘要（前端 crypto.subtle 可用，比 MD5 更安全）。"""
    return hashlib.sha256(content).hexdigest()


def get_image_config() -> dict[str, Any]:
    """从环境变量读取图片处理配置（vision LLM + OCR）。"""
    return {
        "enabled": os.getenv("IMAGE_ANALYSIS_ENABLED", "true").lower() == "true",
        "vision_llm": {
            "base_url": os.getenv("VISION_LLM_BASE_URL", ""),
            "api_key": os.getenv("VISION_LLM_API_KEY", ""),
            "model": os.getenv("VISION_LLM_MODEL", "qwen-vl-plus"),
        },
        "ocr": {
            "enabled": os.getenv("OCR_ENABLED", "true").lower() == "true",
            "lang": os.getenv("OCR_LANG", "ch"),
        },
    }


def is_image_file(file_name: str) -> bool:
    """判断文件是否为图片格式。"""
    ext = Path(file_name).suffix.lower()
    return ext in SUPPORTED_IMAGE_EXTENSIONS


def save_image_original(src_path: str, file_name: str, user_id: str) -> str:
    """保存图片原图到持久化目录（按用户隔离），返回保存后的路径。"""
    dest_dir = IMAGE_UPLOAD_DIR / user_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    # 避免文件名冲突：添加时间戳前缀
    timestamp = int(time.time())
    dest_name = f"{timestamp}_{file_name}"
    dest_path = dest_dir / dest_name
    shutil.copy2(src_path, dest_path)
    logger.info("图片原图已保存", file_name=file_name, saved_path=str(dest_path))
    return str(dest_path)


def safe_rel_path(file_name: str) -> Path:
    """把上传文件名安全地转为相对路径，拒绝绝对路径与 ``..`` 穿越。"""
    p = Path(file_name)
    if p.is_absolute():
        p = Path(p.name)
    parts = [part for part in p.parts if part not in ("", ".", "..")]
    return Path(*parts) if parts else Path("unnamed")


def save_document_original(src_path: str, file_name: str, user_id: str) -> str:
    """保存文档原始文件到持久化目录（按用户 + 相对路径建子目录），返回保存路径。"""
    dest_path = DOCUMENT_UPLOAD_DIR / user_id / safe_rel_path(file_name)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_path, dest_path)
    logger.info("文档原文件已保存", file_name=file_name, saved_path=str(dest_path))
    return str(dest_path)


async def ingest_chunks(
    ctx: AppContext,
    chunks: list[str],
    user_id: str,
    file_name: str,
    source: str = DOCUMENT_SOURCE,
    category: dict[str, Any] | None = None,
    series: str = "",
) -> tuple[list[str], str]:
    """
    将分块批量入库到知识库。

    为每个分块创建 KnowledgeEntry，
    importance_score 默认 0.5，source_id 记录原文件名以便溯源，
    category 为文档级分类结果（同一文件所有分块共享），series 为系列名。

    Args:
        ctx: 应用上下文（已确保 vector_store 可用）
        chunks: 文本分块列表
        user_id: 用户 ID
        file_name: 原文件名，写入 source_id 用于溯源
        source: 来源标记（"document" 或 "image"）
        category: 分类结果 {l1, l2, l3, confidence}，None 时使用默认值
        series: 系列名（系列文章归组，无则空串）

    Returns:
        (entry_ids, error_message) 二元组；全部成功时 error_message 为空串
    """
    vector_store = ctx.vector_store
    assert vector_store is not None  # 由调用方（路由 503 守卫）保证

    # 分类参数（文档级，所有分块共享）
    cat = category or {}
    cat_l1 = cat.get("l1", "其他")
    cat_l2 = cat.get("l2", "待分类")
    cat_l3 = cat.get("l3", "未分类")
    cat_conf = float(cat.get("confidence", 0.0) or 0.0)

    entry_ids: list[str] = []
    error_msg = ""
    for idx, chunk in enumerate(chunks):
        try:
            entry_id = await vector_store.add(
                content=chunk,
                user_id=user_id,
                source=source,
                source_id=file_name,
                importance_score=DEFAULT_IMPORTANCE_SCORE,
                category_l1=cat_l1,
                category_l2=cat_l2,
                category_l3=cat_l3,
                category_confidence=cat_conf,
                category_source="auto",
                series=series,
            )
            entry_ids.append(entry_id)
        except Exception as e:
            # 单个分块入库失败不中断后续分块，记录首个错误用于返回
            logger.error(
                "分块入库失败",
                file_name=file_name,
                chunk_index=idx,
                error=str(e),
                error_type=type(e).__name__,
                ingested_so_far=len(entry_ids),
            )
            if not error_msg:
                error_msg = f"分块 {idx} 入库失败: {e}"
    return entry_ids, error_msg


async def process_and_ingest(
    ctx: AppContext,
    file_path: str,
    file_name: str,
    user_id: str,
    overwrite: bool = False,
) -> IngestResult:
    """
    执行 解析 → 分块 → 入库 完整流程并返回领域结果。

    被 POST /api/v1/upload 主流程与 BackgroundTasks 后台任务共用。
    图片文件会额外保存原图到持久化目录。
    overwrite=True 时，先删除同名旧文件的全部条目再入库（覆盖）。

    前置条件：调用方（路由）已完成 L3 启用守卫（503）。
    """
    # 覆盖上传：删除同名旧文件条目
    if overwrite:
        deleted = await delete_entries_by_source_id(ctx, user_id, file_name)
        if deleted:
            logger.info("覆盖上传：已删除旧文件条目", file_name=file_name, deleted=deleted)

    image_config = get_image_config()
    processor = FileProcessor(image_config=image_config)

    # 图片文件：保存原图到持久化目录；文档文件：保存原始文件（L1 兜底）
    is_image = is_image_file(file_name)
    if is_image:
        try:
            save_image_original(file_path, file_name, user_id)
        except Exception as e:
            logger.warning("图片原图保存失败", file_name=file_name, error=str(e))
    else:
        try:
            save_document_original(file_path, file_name, user_id)
        except Exception as e:
            logger.warning("文档原文件保存失败", file_name=file_name, error=str(e))

    result = await processor.process_file(
        file_path=file_path,
        user_id=user_id,
        metadata={"original_name": file_name},
    )

    if result.status == "error":
        logger.warning(
            "文件解析失败",
            file_name=file_name,
            error=result.error,
        )
        return IngestResult(
            file_name=file_name,
            file_size=result.file_size,
            chunks_count=0,
            ingested_count=0,
            status="error",
            error=result.error,
        )

    if not result.chunks:
        logger.info("文件无有效文本，跳过入库", file_name=file_name)
        return IngestResult(
            file_name=file_name,
            file_size=result.file_size,
            chunks_count=0,
            ingested_count=0,
            status="success",
        )

    # 图片使用 "image" 来源标记，文档使用 "document"
    source = IMAGE_SOURCE if is_image else DOCUMENT_SOURCE

    # 文档级自动分类：用文件名 + 首个分块作为样本（同一文件所有分块共享分类）
    category = await classify_document(ctx, result.chunks, file_name)

    # 系列文章识别（文件名启发式，无需 LLM）
    from app.services.series import detect_series
    series_info = detect_series(file_name)
    series_name = str(series_info["series"]) if series_info["is_series"] else ""

    entry_ids, error_msg = await ingest_chunks(
        ctx=ctx,
        chunks=result.chunks,
        user_id=user_id,
        file_name=file_name,
        source=source,
        category=category,
        series=series_name,
    )

    if error_msg:
        final_status = "partial" if entry_ids else "error"
    else:
        final_status = "success"

    logger.info(
        "文件入库完成",
        file_name=file_name,
        chunks_count=result.chunks_count,
        ingested_count=len(entry_ids),
        status=final_status,
        category_l1=category.get("l1"),
        category_l2=category.get("l2"),
        category_l3=category.get("l3"),
    )

    return IngestResult(
        file_name=file_name,
        file_size=result.file_size,
        chunks_count=result.chunks_count,
        ingested_count=len(entry_ids),
        status=final_status,
        error=error_msg,
        entry_ids=entry_ids,
        category=category,
        series=series_name,
    )


async def delete_entries_by_source_id(
    ctx: AppContext, user_id: str, source_id: str
) -> int:
    """删除某个 source_id（文件名）下 document/image 来源的全部条目，返回删除数量。"""
    try:
        entries = await ctx.knowledge_base.list_entries(user_id=user_id, limit=5000)
    except Exception as e:
        logger.warning("查询同名文件条目失败，跳过覆盖删除", error=str(e))
        return 0
    ids = [
        e.entry_id
        for e in entries
        if e.source_id == source_id and e.source in (DOCUMENT_SOURCE, IMAGE_SOURCE)
    ]
    if ids:
        await ctx.knowledge_base.delete_batch(ids)
    return len(ids)


async def background_ingest(
    ctx: AppContext,
    file_path: str,
    file_name: str,
    user_id: str,
    overwrite: bool = False,
) -> None:
    """
    后台异步处理大文件入库任务。

    处理完成后清理临时文件。异常仅记录日志，不向上抛出（后台任务上下文）。
    """
    try:
        await process_and_ingest(
            ctx=ctx,
            file_path=file_path,
            file_name=file_name,
            user_id=user_id,
            overwrite=overwrite,
        )
    except Exception as e:
        logger.error(
            "后台文件入库异常",
            file_name=file_name,
            error=str(e),
            exc_info=True,
        )
    finally:
        # 清理临时文件
        try:
            os.remove(file_path)
        except OSError as e:
            logger.warning("临时文件清理失败", file_path=file_path, error=str(e))


async def classify_document(
    ctx: AppContext,
    chunks: list[str],
    file_name: str,
) -> dict[str, Any]:
    """
    对文档进行自动分类。

    使用 LLM 分析文件名 + 首个分块内容，匹配到三级分类目录。
    失败时返回默认 "其他/待分类/未分类"，不阻断上传主流程。

    Returns:
        {"l1": ..., "l2": ..., "l3": ..., "confidence": ...}
    """
    from app.services.classifier import DocumentClassifier

    try:
        classifier = DocumentClassifier(ctx.llm_factory)
        sample = chunks[0] if chunks else ""
        return await classifier.classify(content=sample, file_name=file_name)
    except Exception as e:
        logger.warning("文档分类异常，使用默认分类", file_name=file_name, error=str(e))
        return {"l1": "其他", "l2": "待分类", "l3": "未分类", "confidence": 0.0}


async def generate_kb_overview(
    ctx: AppContext, dist: dict[str, int], file_names: list[str]
) -> str:
    """用 LLM 生成知识库概览（覆盖主题/结构/缺口）。"""
    from langchain_core.messages import HumanMessage, SystemMessage

    prompt = (
        "你是知识库分析助手。根据给定信息，用 3~6 句话概括这个知识库："
        "主要覆盖哪些主题、结构是否均衡、存在哪些明显缺口，以及可补强的方向。"
        "直接输出纯文本，不要用列表或 markdown 标题。"
    )
    payload = (
        f"分类分布（大类 → 条目数）：{json.dumps(dist, ensure_ascii=False)}\n"
        f"已上传文件（前 200 个）：{', '.join(file_names[:200])}"
    )
    resp = await ctx.llm_factory.ainvoke_with_stats("job_analysis", [
        SystemMessage(content=prompt),
        HumanMessage(content=payload),
    ])
    raw = resp.content if hasattr(resp, "content") else str(resp)
    return raw.strip()
