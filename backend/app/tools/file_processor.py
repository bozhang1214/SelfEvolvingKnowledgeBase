"""
文件处理器与文本分块模块。

Phase 2 文件上传功能的底层组件，提供：
1. 文本智能分块（``chunk_text``）：按段落→句子→硬切的多级策略，相邻块保留字符重叠
2. 文件解析（``FileProcessor``）：根据扩展名选择解析器，支持 .txt/.md/.markdown/.pdf/.docx
3. 处理结果（``ProcessResult``）：结构化的解析与分块结果

设计要点：
- pypdf、python-docx 使用懒导入，未安装时抛出友好错误，不影响模块加载与其他格式解析
- 异步方法内部使用 ``asyncio.to_thread`` 包装同步文件 IO，避免阻塞事件循环
- 分块参数单位为字符数（非 token），中文场景更实用
- 文件路径统一使用 ``pathlib.Path``

使用方式：
    from app.tools.file_processor import FileProcessor, chunk_text, ProcessResult

    # 文本分块
    chunks = chunk_text(long_text, chunk_size=500, overlap=50)

    # 解析并分块
    processor = FileProcessor()
    result = await processor.process_file("/path/to/file.pdf", user_id="u1", metadata={})
    if result.status == "success":
        for chunk in result.chunks:
            ...
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.tools.image_processor import SUPPORTED_IMAGE_EXTENSIONS, ImageProcessor

logger = get_logger(__name__)

# ============================================================
# 常量定义
# ============================================================

# 句子分隔正则：
# - 中文 。！？ 之后零宽切分（标点保留在前一句末尾）
# - 英文 .!? 后跟空白时切分（消费空白），避免误切 "3.14"、"U.S." 等无空白场景
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？])|(?<=[.!?])\s+")

# 默认支持的文件扩展名（小写、含点号），含图片格式
_DEFAULT_SUPPORTED_EXTENSIONS: list[str] = [
    ".txt", ".md", ".markdown", ".pdf", ".docx",
] + SUPPORTED_IMAGE_EXTENSIONS


# ============================================================
# 处理结果数据类
# ============================================================


@dataclass
class ProcessResult:
    """文件处理结果。"""

    file_path: str
    file_name: str
    file_size: int
    content_length: int
    chunks: list[str]
    chunks_count: int
    status: str  # "success" | "error"
    error: str = ""


# ============================================================
# 文本分块
# ============================================================


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """
    智能文本分块。

    分块策略（多级降级）：
        1. 优先按段落（``\\n\\n``）切分
        2. 段落超过 chunk_size 时按句子切分（中英文句末标点 ``. `` ``。`` ``！`` ``？``）
        3. 句子仍超过 chunk_size 时按 chunk_size 硬切
        4. 相邻块保留 overlap 个字符的重叠（取上一块末尾作为下一块开头）

    参数单位为字符数（非 token），中文场景更实用。

    Args:
        text: 待分块的文本
        chunk_size: 单块目标字符数，默认 500
        overlap: 相邻块重叠字符数，默认 50；必须满足 ``0 <= overlap < chunk_size``

    Returns:
        分块后的文本列表；空文本返回空列表

    Raises:
        ValueError: chunk_size 非正或 overlap 非法

    Note:
        为保留语义边界，分块以段落/句子为单位贪心聚合；当某个单元接近
        chunk_size 时，叠加 overlap 可能使该块略超 chunk_size，属预期行为。
    """
    if not text or not text.strip():
        return []
    if chunk_size <= 0:
        raise ValueError("chunk_size 必须为正整数")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap 必须满足 0 <= overlap < chunk_size")

    units = _split_into_units(text, chunk_size)
    if not units:
        return []

    chunks: list[str] = []
    current = ""
    for unit in units:
        if not current:
            # 首个单元直接作为当前块起始（单元长度已保证 <= chunk_size）
            current = unit
            continue
        candidate = current + "\n\n" + unit
        if len(candidate) <= chunk_size:
            current = candidate
        else:
            # 当前块已满，落盘并开启新块
            chunks.append(current)
            # 下一块以当前块末尾 overlap 个字符作为上下文重叠
            tail = current[-overlap:] if overlap > 0 else ""
            current = (tail + "\n\n" + unit) if tail else unit
    if current:
        chunks.append(current)

    return chunks


def _split_into_units(text: str, chunk_size: int) -> list[str]:
    """
    将文本切分为不超过 chunk_size 的原子单元。

    切分顺序：段落（``\\n\\n``）→ 句子（中英文句末标点）→ 硬切（按 chunk_size）。

    Args:
        text: 原始文本
        chunk_size: 单元最大字符数

    Returns:
        原子单元列表，每个单元长度 <= chunk_size
    """
    units: list[str] = []
    for raw_para in text.split("\n\n"):
        para = raw_para.strip()
        if not para:
            continue
        if len(para) <= chunk_size:
            units.append(para)
            continue
        # 段落过长，按句子切分
        for raw_sent in _split_sentences(para):
            sent = raw_sent.strip()
            if not sent:
                continue
            if len(sent) <= chunk_size:
                units.append(sent)
            else:
                # 句子仍过长，按 chunk_size 硬切
                for i in range(0, len(sent), chunk_size):
                    units.append(sent[i : i + chunk_size])
    return units


def _split_sentences(text: str) -> list[str]:
    """
    按中英文句末标点切分句子，标点保留在前一句末尾。

    支持的句末标点：中文 ``。！？``、英文 ``.!?``（英文需后接空白，避免误切小数）。

    Args:
        text: 待切分的文本

    Returns:
        句子列表（已去除首尾空白与空串）
    """
    parts = _SENTENCE_SPLIT_RE.split(text)
    return [p for p in (part.strip() for part in parts) if p]


# ============================================================
# 文件处理器
# ============================================================


class FileProcessor:
    """
    文件处理器。

    根据文件扩展名选择对应的解析器，提取纯文本后交由 ``chunk_text`` 分块。
    支持 .txt / .md / .markdown / .pdf / .docx 以及图片格式(.jpg/.png/.webp 等)。
    图片通过 ImageProcessor 进行 OCR 文字提取和多模态标签生成。

    PDF 与 Word 依赖（pypdf、python-docx）采用懒导入，未安装时抛出友好错误，
    不影响模块本身加载与其他格式解析。
    """

    def __init__(
        self,
        supported_extensions: list[str] | None = None,
        image_config: dict[str, Any] | None = None,
        image_save_dir: str = "data/uploads/images",
    ) -> None:
        """
        初始化文件处理器。

        Args:
            supported_extensions: 支持的扩展名列表（小写、含点号）。
                为 None 时使用默认列表（含图片格式）。
            image_config: 图片处理配置（vision_llm + ocr），来自 config.yaml。
            image_save_dir: 图片 OCR 文本保存目录。
        """
        exts = supported_extensions if supported_extensions is not None else _DEFAULT_SUPPORTED_EXTENSIONS
        self.supported_extensions: list[str] = [ext.lower() for ext in exts]
        self._image_processor = ImageProcessor(vision_config=image_config)
        self._image_save_dir = image_save_dir

    async def parse_file(self, file_path: str) -> str:
        """
        解析文件并返回纯文本内容。

        根据扩展名选择解析器，同步解析逻辑通过 ``asyncio.to_thread`` 在线程中执行，
        避免阻塞事件循环。

        Args:
            file_path: 文件路径

        Returns:
            提取的纯文本内容

        Raises:
            FileNotFoundError: 文件不存在
            ValueError: 不支持的文件类型
            ImportError: pypdf / python-docx 未安装
        """
        path = Path(file_path)

        # 先校验类型（仅依赖路径，无需文件落盘即可拒绝不支持的扩展名）
        ext = path.suffix.lower()
        if ext not in self.supported_extensions:
            raise ValueError(
                f"不支持的文件类型: {ext}，支持的扩展名: {', '.join(self.supported_extensions)}"
            )

        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        if ext == ".pdf":
            parser = self._parse_pdf
        elif ext == ".docx":
            parser = self._parse_docx
        elif ext in (".md", ".markdown"):
            parser = self._parse_markdown
        elif ext == ".txt":
            parser = self._parse_txt
        elif ext in SUPPORTED_IMAGE_EXTENSIONS:
            # 图片在 process_file 中走专用流程，parse_file 不处理
            raise ValueError(
                f"图片格式 {ext} 请通过 process_file 处理（含 OCR + 标签）"
            )
        else:
            # 扩展名在支持列表中但未实现解析器（用户自定义扩展名时可能命中）
            raise ValueError(f"扩展名 {ext} 暂无解析器实现")

        logger.info("开始解析文件", file_path=str(path), ext=ext)
        content = await asyncio.to_thread(parser, str(path))
        logger.info(
            "文件解析完成",
            file_path=str(path),
            ext=ext,
            content_length=len(content),
        )
        return content

    async def process_file(
        self,
        file_path: str,
        user_id: str,
        metadata: dict[str, Any],
    ) -> ProcessResult:
        """
        完整处理流程：解析 → 分块 → 返回结构化结果。

        任何阶段发生异常都不会向外抛出，而是封装为 ``status="error"`` 的 ProcessResult。
        ``user_id`` 用于日志上下文；``metadata`` 为预留接口（如原文件名、上传来源等），
        便于后续扩展（入库到知识库时写入条目元数据）。

        Args:
            file_path: 文件路径
            user_id: 用户 ID
            metadata: 附加元数据

        Returns:
            处理结果 ProcessResult（成功或错误均以结构化结果返回）
        """
        path = Path(file_path)
        # 预先读取文件大小（文件可能不存在，读取失败时记为 0）
        try:
            file_size = path.stat().st_size
        except OSError:
            file_size = 0

        # 图片走专用处理流程（OCR + 多模态标签）
        if path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
            return await self._process_image_file(file_path, user_id, metadata)

        try:
            content = await self.parse_file(file_path)
            chunks = chunk_text(content)

            logger.info(
                "文件处理完成",
                file_path=str(path),
                user_id=user_id,
                file_size=file_size,
                content_length=len(content),
                chunks_count=len(chunks),
            )
            return ProcessResult(
                file_path=str(path),
                file_name=path.name,
                file_size=file_size,
                content_length=len(content),
                chunks=chunks,
                chunks_count=len(chunks),
                status="success",
            )
        except Exception as e:
            logger.error(
                "文件处理失败",
                file_path=str(path),
                user_id=user_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            return ProcessResult(
                file_path=str(path),
                file_name=path.name,
                file_size=file_size,
                content_length=0,
                chunks=[],
                chunks_count=0,
                status="error",
                error=str(e),
            )

    # ------------------------------------------------------------
    # 各格式解析器（同步实现，懒导入第三方依赖）
    # ------------------------------------------------------------

    def _parse_txt(self, file_path: str) -> str:
        """读取纯文本文件（UTF-8）。"""
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()

    def _parse_markdown(self, file_path: str) -> str:
        """读取 Markdown 文件并保留原始格式。"""
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()

    def _parse_pdf(self, file_path: str) -> str:
        """
        使用 pypdf 提取 PDF 文本。

        Raises:
            ImportError: pypdf 未安装
        """
        try:
            from pypdf import PdfReader
        except ImportError as e:
            raise ImportError(
                "pypdf 未安装，请执行 `pip install pypdf` 以支持 PDF 解析"
            ) from e

        reader = PdfReader(file_path)
        pages: list[str] = []
        for page in reader.pages:
            page_text = page.extract_text() or ""
            pages.append(page_text)
        return "\n\n".join(pages)

    def _parse_docx(self, file_path: str) -> str:
        """
        使用 python-docx 提取 Word(.docx) 文本。

        Raises:
            ImportError: python-docx 未安装
        """
        try:
            from docx import Document
        except ImportError as e:
            raise ImportError(
                "python-docx 未安装，请执行 `pip install python-docx` 以支持 Word 文档解析"
            ) from e

        doc = Document(file_path)
        paragraphs = [p.text for p in doc.paragraphs if p.text and p.text.strip()]
        return "\n\n".join(paragraphs)

    # ------------------------------------------------------------
    # 图片处理（委托给 ImageProcessor）
    # ------------------------------------------------------------

    async def _process_image_file(
        self,
        file_path: str,
        user_id: str,
        metadata: dict[str, Any],
    ) -> ProcessResult:
        """
        图片处理流程：OCR 提取文字 + 多模态标签 + 保存文本。

        处理后的内容（OCR 文本 + 标签 + 描述）作为 chunks 返回，
        供上层入库到知识库。
        """
        path = Path(file_path)
        try:
            file_size = path.stat().st_size
        except OSError:
            file_size = 0

        try:
            result = await self._image_processor.process_image(
                image_path=file_path,
                user_id=user_id,
                save_dir=self._image_save_dir,
            )

            if result.status == "error":
                return ProcessResult(
                    file_path=str(path),
                    file_name=path.name,
                    file_size=file_size,
                    content_length=0,
                    chunks=[],
                    chunks_count=0,
                    status="error",
                    error=result.error,
                )

            # 构造入库内容：OCR 文本 + 标签 + 描述
            chunks: list[str] = []
            if result.description:
                chunks.append(f"图片描述：{result.description}")
            if result.tags:
                chunks.append(f"图片标签：{', '.join(result.tags)}")
            if result.ocr_text.strip():
                ocr_chunks = chunk_text(result.ocr_text)
                chunks.extend(ocr_chunks)

            logger.info(
                "图片文件处理完成",
                file_path=str(path),
                user_id=user_id,
                tags=result.tags,
                ocr_length=len(result.ocr_text),
                chunks_count=len(chunks),
            )

            return ProcessResult(
                file_path=str(path),
                file_name=path.name,
                file_size=file_size,
                content_length=len(result.ocr_text),
                chunks=chunks,
                chunks_count=len(chunks),
                status="success",
            )
        except Exception as e:
            logger.error(
                "图片文件处理失败",
                file_path=str(path),
                user_id=user_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            return ProcessResult(
                file_path=str(path),
                file_name=path.name,
                file_size=file_size,
                content_length=0,
                chunks=[],
                chunks_count=0,
                status="error",
                error=str(e),
            )
