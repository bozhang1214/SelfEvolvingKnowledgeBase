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
import tempfile
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
from app.core.access import require_full_access
from app.core.auth import get_current_user
from app.core.bootstrap import AppContext
from app.core.exceptions import SEKBError
from app.core.logging import get_logger
from app.services import upload_service

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/upload", tags=["upload"],
    dependencies=[Depends(require_full_access)],
)


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




async def _list_all_entries(
    ctx: AppContext, user_id: str, source: str | None = None
) -> list[Any]:
    """分页拉取全部条目，避免单次 limit 截断（知识库块数超过单页上限时）。"""
    entries: list[Any] = []
    page_size = 1000
    offset = 0
    while True:
        batch = await ctx.knowledge_base.list_entries(
            user_id=user_id, source=source, limit=page_size, offset=offset
        )
        if not batch:
            break
        entries.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    return entries


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

    # 分块读取并校验大小：若先 `await file.read()` 全量读入再判上限，超大文件会在
    # 校验生效前就打爆内存（DoS）。分块边读边累计，超限立即中断。
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > upload_service.MAX_FILE_SIZE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"文件过大：超过上限 {upload_service.MAX_FILE_SIZE_BYTES} bytes",
            )
        chunks.append(chunk)
    content = b"".join(chunks)
    md5 = upload_service.compute_md5(content)

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
            upload_service.background_ingest,
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
        result = await upload_service.process_and_ingest(
            ctx=ctx,
            file_path=tmp_path,
            file_name=file_name,
            user_id=user_id,
            overwrite=overwrite,
        )
        # 入库成功（含部分成功）后记录 MD5，供后续「同名同内容」跳过判断
        if result.status in ("success", "partial"):
            upload_service.record_md5(file_name, md5, user_id)
        return UploadResponse(
            file_name=result.file_name,
            file_size=result.file_size,
            chunks_count=result.chunks_count,
            ingested_count=result.ingested_count,
            status=result.status,
            error=result.error,
            entry_ids=result.entry_ids,
            category=result.category,
            series=result.series,
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
        entries = await _list_all_entries(ctx, user_id, upload_service.DOCUMENT_SOURCE)
    except Exception as e:
        logger.error("列出系列分组失败", error=str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"查询系列分组失败: {e}",
        ) from e

    def _rel_key(fname: str, series: str) -> str:
        """系列内相对路径键：截断到系列名「段」之后（跨父目录去重）。"""
        segs = fname.split("/")
        if series in segs:
            i = segs.index(series)
            return "/".join(segs[i + 1:]) or fname
        return fname

    groups: dict[str, dict[str, Any]] = {}
    for e in entries:
        s = (e.series or "").strip()
        if not s:
            continue
        fname = e.source_id or ""
        if not fname:
            continue
        g = groups.setdefault(s, {"series": s, "files": {}, "category": [e.category_l1, e.category_l2, e.category_l3]})
        # 同一系列内按「系列名之后的相对路径」去重，避免同一批文件因上传路径不同被重复计数
        key = _rel_key(fname, s)
        prev = g["files"].get(key)
        if prev is None or len(fname) < len(prev):
            g["files"][key] = fname

    result = []
    for g in groups.values():
        files = []
        for fname in g["files"].values():
            info = detect_series(fname)
            files.append({"file_name": fname, "part": info.get("part") or 0})
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
        entries = await _list_all_entries(ctx, user_id)
    except Exception as e:
        logger.error("列出文件历史失败", error=str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"查询文件历史失败: {e}",
        ) from e

    files: dict[str, dict[str, Any]] = {}
    for e in entries:
        if e.source not in (upload_service.DOCUMENT_SOURCE, upload_service.IMAGE_SOURCE):
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
    md5_index = upload_service.load_md5_index()
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
        entries = await _list_all_entries(ctx, user_id, upload_service.DOCUMENT_SOURCE)
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
        category = await upload_service.classify_document(ctx, [sample], fname)
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
                user_id=user_id, source=upload_service.DOCUMENT_SOURCE, limit=page_size, offset=offset
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
        entries = await _list_all_entries(ctx, user_id)
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

    doc_entries = [e for e in entries if e.source == upload_service.DOCUMENT_SOURCE]
    file_names = sorted({e.source_id for e in doc_entries if e.source_id})

    # LLM 概览（失败降级为纯统计）
    overview = ""
    try:
        overview = await upload_service.generate_kb_overview(ctx, dist, file_names)
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


