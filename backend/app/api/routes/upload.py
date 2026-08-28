"""
文件上传路由模块

Phase 2 文件上传入库功能的 HTTP 端点，支持将用户上传的文档解析、分块并入库到
L3 长期知识库（ChromaDB），实现 RAG 检索增强：

- ``POST   /api/v1/upload``                文件上传并入库
- ``GET    /api/v1/upload/status``        知识库状态查询（条目总数 + L3 启用状态）
- ``DELETE /api/v1/upload/entries/{entry_id}``  删除指定知识条目

流程：
    1. 接收 multipart/form-data 文件，落盘到临时目录
    2. 使用 ``FileProcessor`` 解析文件内容并智能分块
    3. 使用 ``DirectVectorStore`` 将每个分块作为 KnowledgeEntry 入库
    4. 删除临时文件并返回上传结果

若 L3 知识库未启用（``ctx.vector_store`` 为 None），上传与删除接口返回 503。
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field

from app.api.server import get_app_context
from app.core.auth import get_current_user
from app.core.bootstrap import AppContext
from app.core.exceptions import SEKBError
from app.core.logging import get_logger
from app.tools.file_processor import FileProcessor
from app.tools.image_processor import SUPPORTED_IMAGE_EXTENSIONS

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/upload", tags=["upload"])

# 文档来源标记与默认重要性评分
_DOCUMENT_SOURCE = "document"
_IMAGE_SOURCE = "image"
_DEFAULT_IMPORTANCE_SCORE = 0.5

# 单文件最大大小（50MB），超过则拒绝
_MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024

# 图片原图保存目录
_IMAGE_UPLOAD_DIR = Path("data/uploads/images")


def _get_image_config() -> dict[str, Any]:
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


def _is_image_file(file_name: str) -> bool:
    """判断文件是否为图片格式。"""
    ext = Path(file_name).suffix.lower()
    return ext in SUPPORTED_IMAGE_EXTENSIONS


def _save_image_original(src_path: str, file_name: str) -> str:
    """保存图片原图到持久化目录，返回保存后的路径。"""
    _IMAGE_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    # 避免文件名冲突：添加时间戳前缀
    import time
    timestamp = int(time.time())
    dest_name = f"{timestamp}_{file_name}"
    dest_path = _IMAGE_UPLOAD_DIR / dest_name
    shutil.copy2(src_path, dest_path)
    logger.info("图片原图已保存", file_name=file_name, saved_path=str(dest_path))
    return str(dest_path)


# ============================================================
# 响应模型
# ============================================================

class UploadResponse(BaseModel):
    """文件上传响应。"""

    file_name: str = Field(..., description="上传文件名")
    file_size: int = Field(..., description="文件字节数")
    chunks_count: int = Field(..., description="分块总数")
    ingested_count: int = Field(..., description="成功入库的分块数")
    status: str = Field(..., description="处理状态：success | partial | error")
    error: str = Field("", description="错误信息（status=error 时填充）")
    entry_ids: list[str] = Field(
        default_factory=list, description="已入库的条目 ID 列表"
    )


class KnowledgeBaseStatus(BaseModel):
    """知识库状态响应。"""

    total_entries: int = Field(..., description="知识库条目总数")
    l3_enabled: bool = Field(..., description="L3 知识库是否启用")
    user_id: str = Field(..., description="查询所用的用户 ID")


class DeleteEntryResponse(BaseModel):
    """删除知识条目响应。"""

    entry_id: str = Field(..., description="被删除的条目 ID")
    deleted: bool = Field(..., description="是否删除成功")


# ============================================================
# 内部辅助
# ============================================================

def _require_vector_store(ctx: AppContext) -> Any:
    """
    校验 L3 知识库已启用，返回 DirectVectorStore 实例。

    若 ``ctx.vector_store`` 为 None（L3 未启用），抛出 503 HTTPException，
    避免在上游产生 AttributeError。

    Returns:
        DirectVectorStore 实例
    """
    if ctx.vector_store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="L3 知识库未启用，无法处理文件上传/删除请求",
        )
    return ctx.vector_store


async def _ingest_chunks(
    ctx: AppContext,
    chunks: list[str],
    user_id: str,
    file_name: str,
    source: str = _DOCUMENT_SOURCE,
    category: dict[str, Any] | None = None,
) -> tuple[list[str], str]:
    """
    将分块批量入库到知识库。

    为每个分块创建 KnowledgeEntry，
    importance_score 默认 0.5，source_id 记录原文件名以便溯源，
    category 为文档级分类结果（同一文件所有分块共享）。

    Args:
        ctx: 应用上下文（已确保 vector_store 可用）
        chunks: 文本分块列表
        user_id: 用户 ID
        file_name: 原文件名，写入 source_id 用于溯源
        source: 来源标记（"document" 或 "image"）
        category: 分类结果 {l1, l2, l3, confidence}，None 时使用默认值

    Returns:
        (entry_ids, error_message) 二元组；全部成功时 error_message 为空串
    """
    vector_store = ctx.vector_store
    assert vector_store is not None  # 由调用方 _require_vector_store 保证

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
                importance_score=_DEFAULT_IMPORTANCE_SCORE,
                category_l1=cat_l1,
                category_l2=cat_l2,
                category_l3=cat_l3,
                category_confidence=cat_conf,
                category_source="auto",
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


async def _process_and_ingest(
    ctx: AppContext,
    file_path: str,
    file_name: str,
    user_id: str,
) -> UploadResponse:
    """
    执行 解析 → 分块 → 入库 完整流程并构造响应。

    被 POST /api/v1/upload 主流程与 BackgroundTasks 后台任务共用。
    图片文件会额外保存原图到持久化目录。
    """
    vector_store = _require_vector_store(ctx)
    image_config = _get_image_config()
    processor = FileProcessor(image_config=image_config)

    # 图片文件：保存原图到持久化目录
    is_image = _is_image_file(file_name)
    if is_image:
        try:
            _save_image_original(file_path, file_name)
        except Exception as e:
            logger.warning("图片原图保存失败", file_name=file_name, error=str(e))

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
        return UploadResponse(
            file_name=file_name,
            file_size=result.file_size,
            chunks_count=0,
            ingested_count=0,
            status="error",
            error=result.error,
        )

    if not result.chunks:
        logger.info("文件无有效文本，跳过入库", file_name=file_name)
        return UploadResponse(
            file_name=file_name,
            file_size=result.file_size,
            chunks_count=0,
            ingested_count=0,
            status="success",
        )

    # 图片使用 "image" 来源标记，文档使用 "document"
    source = _IMAGE_SOURCE if is_image else _DOCUMENT_SOURCE

    # 文档级自动分类：用文件名 + 首个分块作为样本（同一文件所有分块共享分类）
    category = await _classify_document(ctx, result.chunks, file_name)

    entry_ids, error_msg = await _ingest_chunks(
        ctx=ctx,
        chunks=result.chunks,
        user_id=user_id,
        file_name=file_name,
        source=source,
        category=category,
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

    return UploadResponse(
        file_name=file_name,
        file_size=result.file_size,
        chunks_count=result.chunks_count,
        ingested_count=len(entry_ids),
        status=final_status,
        error=error_msg,
        entry_ids=entry_ids,
    )


async def _background_ingest(
    ctx: AppContext,
    file_path: str,
    file_name: str,
    user_id: str,
) -> None:
    """
    后台异步处理大文件入库任务。

    处理完成后清理临时文件。异常仅记录日志，不向上抛出（后台任务上下文）。
    """
    try:
        await _process_and_ingest(
            ctx=ctx,
            file_path=file_path,
            file_name=file_name,
            user_id=user_id,
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


async def _classify_document(
    ctx: AppContext,
    chunks: list[str],
    file_name: str,
) -> dict[str, Any]:
    """
    对文档进行自动分类。

    使用 LLM 分析文件名 + 首个分块内容，匹配到三级分类目录。
    失败时返回默认 "其他/待分类/未分类"，不阻断上传主流程。

    Args:
        ctx: 应用上下文
        chunks: 文档分块列表
        file_name: 文件名

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


# ============================================================
# 路由
# ============================================================

@router.post("", response_model=UploadResponse)
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="待上传的文档文件"),
    async_process: bool = Query(
        default=False,
        description="是否后台异步处理（大文件建议开启）",
    ),
    user_id: str = Depends(get_current_user),
) -> UploadResponse:
    """
    上传文件并入库到知识库。

    接收 multipart/form-data 文件上传，落盘到临时目录后：
        1. 使用 ``FileProcessor`` 解析文件内容并智能分块
        2. 使用 ``DirectVectorStore`` 将分块入库到 L3 知识库
        3. 删除临时文件并返回上传结果

    若 ``async_process=true``，立即返回占位响应，实际入库在后台执行。
    """
    ctx: AppContext = get_app_context()
    _require_vector_store(ctx)

    file_name = file.filename or "unnamed"

    # 读取文件内容并做大小校验
    content = await file.read()
    if len(content) > _MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"文件过大：{len(content)} bytes，上限 {_MAX_FILE_SIZE_BYTES} bytes",
        )

    # 落盘到临时文件（保留原扩展名，便于 FileProcessor 按扩展名选择解析器）
    suffix = os.path.splitext(file_name)[1]
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix="sekb_upload_")
    try:
        with os.fdopen(tmp_fd, "wb") as tmp_file:
            tmp_file.write(content)
    except Exception:
        # fdopen 成功时 fd 已由 with 块关闭，无需再 close；
        # fdopen 失败时 fd 仍需手动关闭。安全清理 + 删除临时文件
        try:
            os.close(tmp_fd)
        except OSError:
            pass  # fd 已关闭
        try:
            os.unlink(tmp_path)
        except OSError:
            pass  # 文件可能未创建
        raise

    logger.info(
        "收到文件上传",
        file_name=file_name,
        file_size=len(content),
        user_id=user_id,
        async_process=async_process,
        tmp_path=tmp_path,
    )

    if async_process:
        # 后台处理：立即返回占位响应，由 BackgroundTasks 在响应后执行入库与清理
        background_tasks.add_task(
            _background_ingest,
            ctx=ctx,
            file_path=tmp_path,
            file_name=file_name,
            user_id=user_id,
        )
        return UploadResponse(
            file_name=file_name,
            file_size=len(content),
            chunks_count=0,
            ingested_count=0,
            status="success",
            error="后台异步处理中，请稍后通过 /api/v1/upload/status 查询条目数",
        )

    # 同步处理
    try:
        return await _process_and_ingest(
            ctx=ctx,
            file_path=tmp_path,
            file_name=file_name,
            user_id=user_id,
        )
    except HTTPException:
        raise
    except SEKBError as e:
        logger.warning("文件上传处理失败", error=str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=e.message,
        ) from e
    except Exception as e:
        logger.error("文件上传意外异常", error=str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"内部错误: {e}",
        ) from e
    finally:
        # 同步路径在主线程完成处理后清理临时文件
        try:
            os.remove(tmp_path)
        except OSError as e:
            logger.warning("临时文件清理失败", file_path=tmp_path, error=str(e))


@router.get("/status", response_model=KnowledgeBaseStatus)
async def knowledge_base_status(
    user_id: str = Depends(get_current_user),
) -> KnowledgeBaseStatus:
    """
    查询知识库状态。

    返回知识库条目总数与 L3 是否启用。L3 未启用时总数返回 0。
    """
    ctx: AppContext = get_app_context()

    if ctx.vector_store is None:
        logger.info("L3 知识库未启用，状态查询返回 0")
        return KnowledgeBaseStatus(
            total_entries=0,
            l3_enabled=False,
            user_id=user_id,
        )

    try:
        total = await ctx.vector_store.count(user_id=user_id)
    except Exception as e:
        logger.error("知识库 count 失败", error=str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"查询知识库状态失败: {e}",
        ) from e

    return KnowledgeBaseStatus(
        total_entries=total,
        l3_enabled=True,
        user_id=user_id,
    )


@router.delete("/entries/{entry_id}", response_model=DeleteEntryResponse)
async def delete_entry(
    entry_id: str,
    user_id: str = Depends(get_current_user),
) -> DeleteEntryResponse:
    """
    删除指定知识条目。

    先校验条目归属（user_id），不属于当前用户的返回 404。
    条目不存在视为已删除（deleted=True，幂等语义）。
    """
    ctx: AppContext = get_app_context()
    vector_store = _require_vector_store(ctx)

    # 先校验条目存在且归属当前用户
    try:
        existing = await ctx.knowledge_base.get(entry_id) if ctx.knowledge_base else None
    except Exception as e:
        logger.error("查询条目失败", entry_id=entry_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"查询条目失败: {e}",
        ) from e

    if existing is None:
        # 条目不存在：幂等删除语义，返回成功
        logger.info("待删条目不存在，按幂等语义返回成功", entry_id=entry_id)
        return DeleteEntryResponse(entry_id=entry_id, deleted=True)

    # 用户归属校验：防止越权删除
    if existing.user_id != user_id:
        logger.warning(
            "删除被拒：条目不属于当前用户",
            entry_id=entry_id,
            entry_user=existing.user_id,
            request_user=user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="条目不存在或无权操作",
        )

    try:
        await vector_store.kb.delete(entry_id)
    except Exception as e:
        logger.error(
            "删除知识条目失败",
            entry_id=entry_id,
            error=str(e),
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"删除条目失败: {e}",
        ) from e

    logger.info("知识条目已删除", entry_id=entry_id, user_id=user_id)
    return DeleteEntryResponse(entry_id=entry_id, deleted=True)
