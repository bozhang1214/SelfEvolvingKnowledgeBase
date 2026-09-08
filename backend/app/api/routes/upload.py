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

import hashlib
import json
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

# 文档原始文件保存目录（L1 兜底：ChromaDB 全毁时可用原始文件重灌）
_DOCUMENT_UPLOAD_DIR = Path("data/uploads/documents")

# 已上传文件内容 MD5 索引（file_name -> md5），用于「同名同内容」跳过
_MD5_FILE = Path("data/uploaded_md5.json")


def _load_md5_index() -> dict[str, str]:
    """读取已上传文件的 MD5 索引；文件不存在或损坏返回空字典。"""
    if not _MD5_FILE.exists():
        return {}
    try:
        return json.loads(_MD5_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_md5_index(data: dict[str, str]) -> None:
    """保存已上传文件的 MD5 索引。"""
    _MD5_FILE.parent.mkdir(parents=True, exist_ok=True)
    _MD5_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _record_md5(file_name: str, md5: str, user_id: str) -> None:
    """记录某文件的 MD5（按用户隔离，覆盖同用户同名旧值）。"""
    data = _load_md5_index()
    data[f"{user_id}|{file_name}"] = md5
    _save_md5_index(data)


def _compute_md5(content: bytes) -> str:
    """计算字节内容的 SHA-256 十六进制摘要（前端 crypto.subtle 可用，比 MD5 更安全）。"""
    return hashlib.sha256(content).hexdigest()


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


def _save_image_original(src_path: str, file_name: str, user_id: str) -> str:
    """保存图片原图到持久化目录（按用户隔离），返回保存后的路径。"""
    dest_dir = _IMAGE_UPLOAD_DIR / user_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    # 避免文件名冲突：添加时间戳前缀
    import time
    timestamp = int(time.time())
    dest_name = f"{timestamp}_{file_name}"
    dest_path = dest_dir / dest_name
    shutil.copy2(src_path, dest_path)
    logger.info("图片原图已保存", file_name=file_name, saved_path=str(dest_path))
    return str(dest_path)


def _safe_rel_path(file_name: str) -> Path:
    """把上传文件名安全地转为相对路径，拒绝绝对路径与 ``..`` 穿越。"""
    p = Path(file_name)
    if p.is_absolute():
        p = Path(p.name)
    parts = [part for part in p.parts if part not in ("", ".", "..")]
    return Path(*parts) if parts else Path("unnamed")


def _save_document_original(src_path: str, file_name: str, user_id: str) -> str:
    """保存文档原始文件到持久化目录（按用户 + 相对路径建子目录），返回保存路径。"""
    dest_path = _DOCUMENT_UPLOAD_DIR / user_id / _safe_rel_path(file_name)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_path, dest_path)
    logger.info("文档原文件已保存", file_name=file_name, saved_path=str(dest_path))
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
    category: dict[str, Any] | None = Field(
        None, description="自动分类结果 {l1,l2,l3,confidence}"
    )
    series: str = Field("", description="识别到的系列名（无则空串）")


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


async def _process_and_ingest(
    ctx: AppContext,
    file_path: str,
    file_name: str,
    user_id: str,
    overwrite: bool = False,
) -> UploadResponse:
    """
    执行 解析 → 分块 → 入库 完整流程并构造响应。

    被 POST /api/v1/upload 主流程与 BackgroundTasks 后台任务共用。
    图片文件会额外保存原图到持久化目录。
    overwrite=True 时，先删除同名旧文件的全部条目再入库（覆盖）。
    """
    vector_store = _require_vector_store(ctx)

    # 覆盖上传：删除同名旧文件条目
    if overwrite:
        deleted = await _delete_entries_by_source_id(ctx, user_id, file_name)
        if deleted:
            logger.info("覆盖上传：已删除旧文件条目", file_name=file_name, deleted=deleted)

    image_config = _get_image_config()
    processor = FileProcessor(image_config=image_config)

    # 图片文件：保存原图到持久化目录；文档文件：保存原始文件（L1 兜底）
    is_image = _is_image_file(file_name)
    if is_image:
        try:
            _save_image_original(file_path, file_name, user_id)
        except Exception as e:
            logger.warning("图片原图保存失败", file_name=file_name, error=str(e))
    else:
        try:
            _save_document_original(file_path, file_name, user_id)
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

    # 系列文章识别（文件名启发式，无需 LLM）
    from app.services.series import detect_series
    series_info = detect_series(file_name)
    series_name = str(series_info["series"]) if series_info["is_series"] else ""

    entry_ids, error_msg = await _ingest_chunks(
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

    return UploadResponse(
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


async def _delete_entries_by_source_id(
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
        if e.source_id == source_id and e.source in (_DOCUMENT_SOURCE, _IMAGE_SOURCE)
    ]
    if ids:
        await ctx.knowledge_base.delete_batch(ids)
    return len(ids)


async def _background_ingest(
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
        await _process_and_ingest(
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
    overwrite: bool = Query(
        default=False,
        description="是否覆盖同名旧文件（true 时先删除旧条目再入库）",
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
    若 ``overwrite=true``，先删除同名旧文件的条目再入库（覆盖）。
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
    md5 = _compute_md5(content)

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
            overwrite=overwrite,
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
        result = await _process_and_ingest(
            ctx=ctx,
            file_path=tmp_path,
            file_name=file_name,
            user_id=user_id,
            overwrite=overwrite,
        )
        # 入库成功（含部分成功）后记录 MD5，供后续「同名同内容」跳过判断
        if result.status in ("success", "partial"):
            _record_md5(file_name, md5, user_id)
        return result
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


@router.get("/series")
async def list_series(
    user_id: str = Depends(get_current_user),
) -> dict[str, Any]:
    """
    列出识别到的「系列文章」分组。

    从 document 来源的条目中，按 series 字段归组（series 非空），
    组内文件按系列序号排序，返回系列名 + 文件列表 + 数量。
    """
    from app.services.series import detect_series

    ctx: AppContext = get_app_context()
    _require_vector_store(ctx)

    try:
        entries = await ctx.knowledge_base.list_entries(
            user_id=user_id, source=_DOCUMENT_SOURCE, limit=5000
        )
    except Exception as e:
        logger.error("列出系列分组失败", error=str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"查询系列分组失败: {e}",
        ) from e

    groups: dict[str, dict[str, Any]] = {}
    for e in entries:
        s = (e.series or "").strip()
        if not s:
            continue
        fname = e.source_id or ""
        g = groups.setdefault(s, {"series": s, "files": {}, "category": [e.category_l1, e.category_l2, e.category_l3]})
        if fname and fname not in g["files"]:
            g["files"][fname] = detect_series(fname)

    result = []
    for g in groups.values():
        files = [
            {
                "file_name": name,
                "part": info.get("part") or 0,
            }
            for name, info in g["files"].items()
        ]
        files.sort(key=lambda x: (x["part"], x["file_name"]))
        result.append({
            "series": g["series"],
            "category": g["category"],
            "count": len(files),
            "files": files,
        })
    result.sort(key=lambda x: x["series"])
    return {"series": result}


@router.get("/files")
async def list_files(user_id: str = Depends(get_current_user)) -> dict[str, Any]:
    """
    列出历史已上传文件（按文件名归组，跨会话持久）。

    从 document/image 来源的条目中，按 source_id（原文件名）归组，
    返回文件名、来源类型、分块数、自动分类、系列名、首次入库时间。
    """
    ctx: AppContext = get_app_context()
    _require_vector_store(ctx)

    try:
        entries = await ctx.knowledge_base.list_entries(user_id=user_id, limit=5000)
    except Exception as e:
        logger.error("列出文件历史失败", error=str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"查询文件历史失败: {e}",
        ) from e

    files: dict[str, dict[str, Any]] = {}
    for e in entries:
        if e.source not in (_DOCUMENT_SOURCE, _IMAGE_SOURCE):
            continue
        fname = e.source_id or "(未命名)"
        f = files.setdefault(fname, {
            "file_name": fname,
            "source": e.source,
            "chunk_count": 0,
            "category": None,
            "series": e.series or "",
            "uploaded_at": e.created_at,
        })
        f["chunk_count"] += 1
        if f["category"] is None:
            f["category"] = {
                "l1": e.category_l1,
                "l2": e.category_l2,
                "l3": e.category_l3,
                "confidence": e.category_confidence,
            }

    result = sorted(files.values(), key=lambda x: x.get("uploaded_at", ""), reverse=True)

    # 合并 MD5（用于前端「同名同内容」跳过判断，按用户隔离）
    md5_index = _load_md5_index()
    for f in result:
        f["md5"] = md5_index.get(f"{user_id}|{f.get('file_name', '')}", "")

    return {"files": result, "count": len(result)}


@router.post("/reclassify")
async def reclassify_files(user_id: str = Depends(get_current_user)) -> dict[str, Any]:
    """
    对当前知识库的所有文档文件重新执行自动分类 + 系列识别。

    按 source_id（文件名）归组，用文件名 + 首块内容重新分类，
    并把新的分类（l1/l2/l3）与系列名更新到该文件的所有分块元数据。
    """
    from app.services.series import detect_series

    ctx: AppContext = get_app_context()
    _require_vector_store(ctx)

    try:
        entries = await ctx.knowledge_base.list_entries(
            user_id=user_id, source=_DOCUMENT_SOURCE, limit=5000
        )
    except Exception as e:
        logger.error("重分类失败：查询条目异常", error=str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"查询条目失败: {e}",
        ) from e

    by_file: dict[str, list[Any]] = {}
    for e in entries:
        by_file.setdefault(e.source_id or "(未命名)", []).append(e)

    reclassified = 0
    for fname, chunk_entries in by_file.items():
        sample = chunk_entries[0].content if chunk_entries else ""
        category = await _classify_document(ctx, [sample], fname)
        series_info = detect_series(fname)
        series_name = str(series_info["series"]) if series_info["is_series"] else ""
        updates = {
            "category_l1": category.get("l1", "其他"),
            "category_l2": category.get("l2", "待分类"),
            "category_l3": category.get("l3", "未分类"),
            "category_confidence": float(category.get("confidence", 0.0) or 0.0),
            "category_source": "auto",
            "series": series_name,
        }
        for e in chunk_entries:
            try:
                await ctx.knowledge_base.update_metadata(e.entry_id, updates)
            except Exception as ex:
                logger.warning("更新条目分类失败", entry_id=e.entry_id, error=str(ex))
        reclassified += 1

    logger.info("知识库重分类完成", files=reclassified, entries=len(entries))
    return {"files_reclassified": reclassified, "entries_updated": len(entries)}


@router.post("/re-series")
async def re_series(user_id: str = Depends(get_current_user)) -> dict[str, Any]:
    """
    重新识别所有文档的系列名（不重新分类，轻量）。

    用最新的 detect_series 规则（含「同一文件夹下多为同一系列」）重新计算 series，
    并更新到该文件的所有分块元数据。适用于系列识别规则升级后，对存量文件做一次性纠偏。
    """
    from app.services.series import detect_series

    ctx: AppContext = get_app_context()
    _require_vector_store(ctx)

    try:
        # 分页拉全量文档条目，避免条目数超过单次 limit 时静默漏掉部分文件
        entries: list[Any] = []
        page_size = 1000
        offset = 0
        while True:
            batch = await ctx.knowledge_base.list_entries(
                user_id=user_id, source=_DOCUMENT_SOURCE, limit=page_size, offset=offset
            )
            if not batch:
                break
            entries.extend(batch)
            if len(batch) < page_size:
                break
            offset += page_size
    except Exception as e:
        logger.error("重新识别系列失败：查询条目异常", error=str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"查询条目失败: {e}",
        ) from e

    by_file: dict[str, list[Any]] = {}
    for e in entries:
        by_file.setdefault(e.source_id or "(未命名)", []).append(e)

    updated_files = 0
    updates: list[tuple[str, dict[str, Any]]] = []
    for fname, chunk_entries in by_file.items():
        series_info = detect_series(fname)
        series_name = str(series_info["series"]) if series_info["is_series"] else ""
        for e in chunk_entries:
            if (e.series or "") != series_name:
                updates.append((e.entry_id, {"series": series_name}))
        updated_files += 1

    updated_entries = 0
    if updates:
        try:
            updated_entries = await ctx.knowledge_base.update_metadata_batch(updates)
        except Exception as ex:
            logger.error("批量更新系列失败", error=str(ex), exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"批量更新系列失败: {ex}",
            ) from ex

    logger.info("系列重新识别完成", files=updated_files, entries_updated=updated_entries)
    return {"files_re_series": updated_files, "entries_updated": updated_entries}


@router.post("/analyze")
async def analyze_knowledge_base(user_id: str = Depends(get_current_user)) -> dict[str, Any]:
    """
    对当前知识库做自动分析与分类。

    1. 重新分类（复用 reclassify 逻辑）
    2. 统计分类分布（按 l1 大类）
    3. 用 LLM 生成知识库概览（覆盖主题、结构、缺口）
    """
    ctx: AppContext = get_app_context()
    _require_vector_store(ctx)

    try:
        entries = await ctx.knowledge_base.list_entries(user_id=user_id, limit=5000)
    except Exception as e:
        logger.error("知识库分析失败：查询条目异常", error=str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"查询条目失败: {e}",
        ) from e

    # 分类分布（按 l1）
    dist: dict[str, int] = {}
    for e in entries:
        if e.source == "conversation":
            continue
        l1 = e.category_l1 or "其他"
        dist[l1] = dist.get(l1, 0) + 1

    doc_entries = [e for e in entries if e.source == _DOCUMENT_SOURCE]
    file_names = sorted({e.source_id for e in doc_entries if e.source_id})

    # LLM 概览（失败降级为纯统计）
    overview = ""
    try:
        overview = await _generate_kb_overview(ctx, dist, file_names)
    except Exception as e:
        logger.warning("知识库 LLM 概览生成失败，降级为纯统计", error=str(e))

    return {
        "total_entries": len(entries),
        "document_files": len(file_names),
        "category_distribution": [
            {"category": k, "count": v} for k, v in sorted(dist.items(), key=lambda x: -x[1])
        ],
        "files": file_names[:200],
        "overview": overview,
    }


async def _generate_kb_overview(
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
    llm = ctx.llm_factory.get("job_analysis")
    resp = await llm.ainvoke([
        SystemMessage(content=prompt),
        HumanMessage(content=payload),
    ])
    raw = resp.content if hasattr(resp, "content") else str(resp)
    return raw.strip()
