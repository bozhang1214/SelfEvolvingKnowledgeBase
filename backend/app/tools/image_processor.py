"""
图片处理模块：OCR 文字提取 + 多模态 LLM 打标签。

功能：
1. 使用 PaddleOCR 提取图片中的文字（支持中英文）
2. 调用多模态 LLM 分析图片内容并生成标签和描述
3. 若未配置视觉模型，降级为基于 OCR 文本生成标签

设计要点：
- PaddleOCR 和多模态 LLM 均采用懒加载，未安装/未配置时降级处理
- OCR 在线程中执行（同步库，避免阻塞事件循环）
- 图片以 base64 编码发送给多模态 LLM（OpenAI 兼容接口）

使用方式：
    from app.tools.image_processor import ImageProcessor

    processor = ImageProcessor()
    result = await processor.process_image("/path/to/image.png", user_id="u1")
    if result.status == "success":
        print(result.ocr_text)    # 提取的文字
        print(result.tags)        # ["截图", "文档", ...]
        print(result.description) # "这是一张关于...的截图"
"""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# 支持的图片扩展名
SUPPORTED_IMAGE_EXTENSIONS: list[str] = [".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff"]

# 单张图片最大大小（20MB，防止 base64 膨胀过大）
_MAX_IMAGE_SIZE_BYTES = 20 * 1024 * 1024


@dataclass
class ImageProcessResult:
    """图片处理结果。"""

    file_path: str
    file_name: str
    file_size: int
    ocr_text: str = ""           # 提取的文字（空串表示无文字或 OCR 失败）
    tags: list[str] = field(default_factory=list)  # 图片标签
    description: str = ""        # 图片描述
    ocr_text_path: str = ""      # OCR 文本保存路径（若已保存）
    status: str = "success"      # "success" | "error"
    error: str = ""


class ImageProcessor:
    """
    图片处理器：OCR 提取 + 多模态标签。

    Args:
        vision_config: 视觉模型配置（来自 config.yaml 的 image_analysis 段）
            {
                "enabled": True,
                "vision_llm": {
                    "base_url": "https://...",
                    "api_key": "sk-...",
                    "model": "qwen-vl-plus",
                },
                "ocr": {"lang": "ch"},
            }
    """

    def __init__(self, vision_config: dict[str, Any] | None = None) -> None:
        self.config = vision_config or {}
        self._ocr_engine: Any = None
        self._ocr_initialized = False

    # ============================================================
    # 公开接口
    # ============================================================

    async def process_image(
        self,
        image_path: str,
        user_id: str = "default",
        save_dir: str | None = None,
    ) -> ImageProcessResult:
        """
        完整处理流程：OCR 提取 → 多模态标签 → 保存文本。

        Args:
            image_path: 图片文件路径
            user_id: 用户 ID（日志上下文）
            save_dir: OCR 文本保存目录，为 None 时不保存

        Returns:
            ImageProcessResult
        """
        path = Path(image_path)
        try:
            file_size = path.stat().st_size
        except OSError:
            file_size = 0

        if file_size > _MAX_IMAGE_SIZE_BYTES:
            return ImageProcessResult(
                file_path=image_path,
                file_name=path.name,
                file_size=file_size,
                status="error",
                error=f"图片过大：{file_size} bytes，上限 {_MAX_IMAGE_SIZE_BYTES} bytes",
            )

        try:
            # 1. OCR 提取文字
            ocr_text = await self._extract_text(image_path)

            # 2. 多模态 LLM 分析图片（生成标签和描述）
            tags, description = await self._analyze_image(image_path, ocr_text)

            # 3. 保存 OCR 文本到文件（用于入库知识库）
            ocr_text_path = ""
            if save_dir and ocr_text.strip():
                ocr_text_path = await self._save_ocr_text(
                    image_path, ocr_text, tags, description, save_dir
                )

            logger.info(
                "图片处理完成",
                file_path=str(path),
                user_id=user_id,
                ocr_length=len(ocr_text),
                tags_count=len(tags),
                has_description=bool(description),
            )

            return ImageProcessResult(
                file_path=image_path,
                file_name=path.name,
                file_size=file_size,
                ocr_text=ocr_text,
                tags=tags,
                description=description,
                ocr_text_path=ocr_text_path,
                status="success",
            )
        except Exception as e:
            logger.error(
                "图片处理失败",
                file_path=str(path),
                user_id=user_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            return ImageProcessResult(
                file_path=image_path,
                file_name=path.name,
                file_size=file_size,
                status="error",
                error=str(e),
            )

    # ============================================================
    # OCR 文字提取
    # ============================================================

    async def _extract_text(self, image_path: str) -> str:
        """使用 PaddleOCR 提取图片中的文字。"""
        if not self.config.get("ocr", {}).get("enabled", True):
            logger.info("OCR 未启用，跳过文字提取", image_path=image_path)
            return ""

        try:
            engine = await self._get_ocr_engine()
            if engine is None:
                logger.warning("PaddleOCR 未安装，跳过 OCR", image_path=image_path)
                return ""

            # PaddleOCR 是同步库，在线程中执行
            # 3.x 推荐 predict()，2.x 用 ocr()
            try:
                # 优先 3.x predict 方法
                result = await asyncio.to_thread(engine.predict, image_path)
            except AttributeError:
                # 降级到 2.x ocr 方法
                result = await asyncio.to_thread(engine.ocr, image_path)

            # 解析 OCR 结果
            # PaddleOCR 返回格式：[[[box], (text, confidence)], ...]
            # 或 3.x 格式：rec_results 等
            texts: list[str] = []
            if result:
                # result 可能是 list[list] 或 dict
                pages = result if isinstance(result, list) else [result]
                for page in pages:
                    if not page:
                        continue
                    # 2.x 格式：page 是 [[box, (text, conf)], ...]
                    if isinstance(page, list):
                        for line in page:
                            if line and len(line) >= 2:
                                text_info = line[1]
                                if isinstance(text_info, (list, tuple)) and len(text_info) >= 1:
                                    texts.append(str(text_info[0]))
                                elif isinstance(text_info, str):
                                    texts.append(text_info)
                    # 3.x 可能返回 dict 格式
                    elif isinstance(page, dict):
                        rec_texts = page.get("rec_texts", [])
                        texts.extend([str(t) for t in rec_texts])

            ocr_text = "\n".join(texts)
            logger.info("OCR 提取完成", image_path=image_path, text_length=len(ocr_text))
            return ocr_text
        except ImportError as e:
            logger.warning("PaddleOCR 未安装，跳过 OCR", error=str(e))
            return ""
        except Exception as e:
            logger.error("OCR 提取失败", image_path=image_path, error=str(e))
            return ""

    async def _get_ocr_engine(self) -> Any:
        """懒加载 PaddleOCR 引擎（首次调用时初始化）。"""
        if self._ocr_initialized:
            return self._ocr_engine

        self._ocr_initialized = True
        try:
            from paddleocr import PaddleOCR

            lang = self.config.get("ocr", {}).get("lang", "ch")
            # PaddleOCR 3.x API：use_angle_cls → use_textline_orientation
            # show_log 已废弃
            try:
                # 尝试 3.x API
                self._ocr_engine = PaddleOCR(
                    use_textline_orientation=True,
                    lang=lang,
                )
            except TypeError:
                # 降级到 2.x API
                self._ocr_engine = PaddleOCR(use_angle_cls=True, lang=lang)
            logger.info("PaddleOCR 引擎已初始化", lang=lang)
        except ImportError:
            logger.warning("paddleocr 未安装，OCR 功能不可用")
            self._ocr_engine = None
        except Exception as e:
            logger.error("PaddleOCR 初始化失败", error=str(e))
            self._ocr_engine = None

        return self._ocr_engine

    # ============================================================
    # 多模态 LLM 图片分析
    # ============================================================

    async def _analyze_image(
        self, image_path: str, ocr_text: str
    ) -> tuple[list[str], str]:
        """
        调用多模态 LLM 分析图片，生成标签和描述。

        若未配置视觉模型，降级为基于 OCR 文本生成标签。
        """
        vision_config = self.config.get("vision_llm", {})
        api_key = vision_config.get("api_key", "")

        if api_key:
            # 有视觉模型配置，直接分析图片
            return await self._analyze_with_vision_llm(image_path, vision_config)
        else:
            # 降级：基于 OCR 文本生成标签
            return await self._analyze_with_text_fallback(ocr_text)

    async def _analyze_with_vision_llm(
        self, image_path: str, vision_config: dict[str, Any]
    ) -> tuple[list[str], str]:
        """使用多模态 LLM 分析图片内容。"""
        try:
            from langchain_openai import ChatOpenAI
            from langchain_core.messages import HumanMessage
        except ImportError as e:
            logger.warning("langchain-openai 未安装，降级为文本分析", error=str(e))
            return await self._analyze_with_text_fallback("")

        try:
            # 读取图片并编码为 base64
            image_data = await asyncio.to_thread(self._read_image_base64, image_path)
            if not image_data:
                return [], ""

            # 创建视觉 LLM 实例
            llm = ChatOpenAI(
                model=vision_config.get("model", "qwen-vl-plus"),
                base_url=vision_config.get("base_url", ""),
                api_key=vision_config["api_key"],
                max_tokens=500,
                temperature=0.3,
                timeout=30,
            )

            # 构造多模态消息
            ext = Path(image_path).suffix.lower().lstrip(".")
            mime_type = "jpeg" if ext in ("jpg", "jpeg") else ext
            prompt = (
                "请分析这张图片，返回 JSON 格式：\n"
                '{"tags": ["标签1", "标签2", "标签3"], "description": "一句话描述图片内容"}\n'
                "要求：\n"
                "1. tags 生成 3-5 个简短标签（中文，每个不超过 4 字）\n"
                "2. description 用一句话概括图片主要内容\n"
                "3. 只返回 JSON，不要其他内容"
            )

            message = HumanMessage(
                content=[
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/{mime_type};base64,{image_data}"},
                    },
                ]
            )

            response = await llm.ainvoke([message])
            content = response.content if hasattr(response, "content") else str(response)

            # 解析 JSON 响应
            tags, description = self._parse_vision_response(content)
            logger.info("视觉模型分析完成", tags=tags, description_len=len(description))
            return tags, description

        except Exception as e:
            logger.error("视觉模型分析失败，降级为文本", error=str(e))
            return [], ""

    async def _analyze_with_text_fallback(
        self, ocr_text: str
    ) -> tuple[list[str], str]:
        """降级方案：基于 OCR 文本生成标签（无视觉模型时）。"""
        if not ocr_text.strip():
            return ["图片"], "无法分析的图片"

        # 基于文本内容简单分类
        tags: list[str] = []
        text_lower = ocr_text.lower()

        if any(kw in text_lower for kw in ["截图", "screenshot", "screen"]):
            tags.append("截图")
        if any(kw in text_lower for kw in ["错误", "error", "exception", "失败"]):
            tags.append("错误信息")
        if any(kw in text_lower for kw in ["代码", "code", "function", "class"]):
            tags.append("代码")
        if any(kw in text_lower for kw in ["文档", "doc", "说明", "手册"]):
            tags.append("文档")
        if any(kw in text_lower for kw in ["表格", "table", "数据"]):
            tags.append("表格")
        if any(kw in text_lower for kw in ["微信", "聊天", "消息"]):
            tags.append("聊天记录")
        if any(kw in text_lower for kw in ["发票", "收据", "金额"]):
            tags.append("票据")

        if not tags:
            tags.append("文字图片")

        # 截取前 50 字作为描述
        description = f"包含文字的图片，内容摘要：{ocr_text[:50]}"
        if len(ocr_text) > 50:
            description += "..."

        return tags, description

    # ============================================================
    # 辅助方法
    # ============================================================

    def _read_image_base64(self, image_path: str) -> str:
        """读取图片文件并编码为 base64。"""
        try:
            with open(image_path, "rb") as f:
                data = f.read()
            return base64.b64encode(data).decode("utf-8")
        except Exception as e:
            logger.error("读取图片失败", image_path=image_path, error=str(e))
            return ""

    def _parse_vision_response(self, content: str) -> tuple[list[str], str]:
        """解析视觉模型的 JSON 响应。"""
        # 尝试提取 JSON（模型可能返回 markdown 包裹的 JSON）
        text = content.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        try:
            data = json.loads(text)
            tags = data.get("tags", [])
            description = data.get("description", "")
            if isinstance(tags, list):
                tags = [str(t) for t in tags][:5]  # 最多 5 个标签
            else:
                tags = []
            return tags, str(description)
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("视觉模型响应解析失败", content=content[:200], error=str(e))
            return [], ""

    async def _save_ocr_text(
        self,
        image_path: str,
        ocr_text: str,
        tags: list[str],
        description: str,
        save_dir: str,
    ) -> str:
        """将 OCR 文本和标签保存为文本文件，返回保存路径。"""
        try:
            save_path = Path(save_dir)
            save_path.mkdir(parents=True, exist_ok=True)

            # 文件名：原图片名（去扩展名）+ .txt
            stem = Path(image_path).stem
            txt_path = save_path / f"{stem}_ocr.txt"

            # 构造文本内容
            content_parts = [
                f"# 图片 OCR 文本\n",
                f"原图片: {Path(image_path).name}\n",
                f"标签: {', '.join(tags) if tags else '无'}\n",
                f"描述: {description}\n",
                f"\n---\n\n",
                ocr_text,
            ]
            content = "\n".join(content_parts)

            await asyncio.to_thread(self._write_file, str(txt_path), content)
            logger.info("OCR 文本已保存", path=str(txt_path))
            return str(txt_path)
        except Exception as e:
            logger.error("保存 OCR 文本失败", error=str(e))
            return ""

    def _write_file(self, path: str, content: str) -> None:
        """同步写文件（供 asyncio.to_thread 调用）。"""
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
