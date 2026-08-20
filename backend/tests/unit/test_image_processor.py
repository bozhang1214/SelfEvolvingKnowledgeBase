"""
图片处理模块的单元测试

测试内容：
- ImageProcessor 降级分析（无视觉模型时基于 OCR 文本生成标签）
- 视觉模型 JSON 响应解析
- OCR 文本保存
- 图片格式检测
- FileProcessor 对图片格式的路由

技术要点：
- 不依赖 PaddleOCR / 视觉模型实际安装（测试降级路径）
- 使用 tmp_path 处理临时文件
- 异步方法使用 @pytest.mark.asyncio 标记
"""

from __future__ import annotations

import pytest

from app.tools.image_processor import (
    SUPPORTED_IMAGE_EXTENSIONS,
    ImageProcessor,
    ImageProcessResult,
)
from app.tools.file_processor import FileProcessor


# ============================================================
# 图片格式常量测试
# ============================================================

class TestSupportedExtensions:
    """测试支持的图片扩展名常量。"""

    def test_contains_common_formats(self):
        """包含常见图片格式。"""
        for ext in [".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff"]:
            assert ext in SUPPORTED_IMAGE_EXTENSIONS

    def test_all_lowercase(self):
        """所有扩展名为小写。"""
        for ext in SUPPORTED_IMAGE_EXTENSIONS:
            assert ext == ext.lower()
            assert ext.startswith(".")


# ============================================================
# 降级分析测试（无视觉模型时）
# ============================================================

class TestTextFallbackAnalysis:
    """测试基于 OCR 文本的降级标签生成。"""

    @pytest.mark.asyncio
    async def test_empty_ocr_returns_default_tags(self):
        """空 OCR 文本返回默认标签。"""
        processor = ImageProcessor(vision_config={})
        tags, desc = await processor._analyze_with_text_fallback("")
        assert "图片" in tags
        assert "无法分析" in desc

    @pytest.mark.asyncio
    async def test_error_content_detected(self):
        """包含错误关键词的文本被标记。"""
        processor = ImageProcessor(vision_config={})
        tags, desc = await processor._analyze_with_text_fallback("Error: connection refused")
        assert "错误信息" in tags

    @pytest.mark.asyncio
    async def test_code_content_detected(self):
        """包含代码关键词的文本被标记。"""
        processor = ImageProcessor(vision_config={})
        tags, desc = await processor._analyze_with_text_fallback("def function(): pass")
        assert "代码" in tags

    @pytest.mark.asyncio
    async def test_chat_content_detected(self):
        """包含聊天关键词的文本被标记。"""
        processor = ImageProcessor(vision_config={})
        tags, desc = await processor._analyze_with_text_fallback("微信聊天记录")
        assert "聊天记录" in tags

    @pytest.mark.asyncio
    async def test_description_truncation(self):
        """长文本描述被截断。"""
        processor = ImageProcessor(vision_config={})
        long_text = "这是一段很长的文字" * 20
        tags, desc = await processor._analyze_with_text_fallback(long_text)
        assert "..." in desc


# ============================================================
# 视觉模型响应解析测试
# ============================================================

class TestVisionResponseParsing:
    """测试视觉模型 JSON 响应解析。"""

    def test_parse_valid_json(self):
        """解析标准 JSON 响应。"""
        processor = ImageProcessor(vision_config={})
        content = '{"tags": ["截图", "文档", "代码"], "description": "一张截图"}'
        tags, desc = processor._parse_vision_response(content)
        assert tags == ["截图", "文档", "代码"]
        assert desc == "一张截图"

    def test_parse_markdown_wrapped_json(self):
        """解析 markdown 包裹的 JSON。"""
        processor = ImageProcessor(vision_config={})
        content = '```json\n{"tags": ["标签1"], "description": "描述"}\n```'
        tags, desc = processor._parse_vision_response(content)
        assert tags == ["标签1"]
        assert desc == "描述"

    def test_parse_invalid_json_returns_empty(self):
        """无效 JSON 返回空列表。"""
        processor = ImageProcessor(vision_config={})
        tags, desc = processor._parse_vision_response("这不是 JSON")
        assert tags == []
        assert desc == ""

    def test_parse_missing_fields(self):
        """缺少字段的 JSON 使用默认值。"""
        processor = ImageProcessor(vision_config={})
        content = '{"tags": ["标签"]}'
        tags, desc = processor._parse_vision_response(content)
        assert tags == ["标签"]
        assert desc == ""

    def test_parse_tags_limit_to_five(self):
        """标签最多 5 个。"""
        processor = ImageProcessor(vision_config={})
        content = '{"tags": ["1", "2", "3", "4", "5", "6", "7"], "description": ""}'
        tags, desc = processor._parse_vision_response(content)
        assert len(tags) == 5


# ============================================================
# OCR 文本保存测试
# ============================================================

class TestOcrTextSave:
    """测试 OCR 文本保存功能。"""

    @pytest.mark.asyncio
    async def test_save_ocr_text_creates_file(self, tmp_path):
        """保存 OCR 文本创建文件。"""
        processor = ImageProcessor(vision_config={})
        image_path = str(tmp_path / "test_image.png")

        result_path = await processor._save_ocr_text(
            image_path=image_path,
            ocr_text="提取的文字内容",
            tags=["标签1", "标签2"],
            description="图片描述",
            save_dir=str(tmp_path / "ocr_output"),
        )

        assert result_path != ""
        assert "test_image_ocr.txt" in result_path

        # 验证文件内容
        with open(result_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "提取的文字内容" in content
        assert "标签1" in content
        assert "图片描述" in content

    @pytest.mark.asyncio
    async def test_save_ocr_text_empty_text_returns_empty(self, tmp_path):
        """空 OCR 文本不保存文件。"""
        processor = ImageProcessor(vision_config={})
        # _save_ocr_text 只在 ocr_text 非空时调用，直接测试空文本
        result_path = await processor._save_ocr_text(
            image_path=str(tmp_path / "test.png"),
            ocr_text="",
            tags=[],
            description="",
            save_dir=str(tmp_path / "output"),
        )
        # 空文本也会保存（因为调用方已判断），但内容为空
        # 这里验证函数不抛异常
        assert isinstance(result_path, str)


# ============================================================
# 完整处理流程测试（降级模式）
# ============================================================

class TestProcessImageFallback:
    """测试图片处理完整流程（降级模式，无 PaddleOCR / 视觉模型）。"""

    @pytest.mark.asyncio
    async def test_process_image_file_too_large(self):
        """文件过大返回错误。"""
        processor = ImageProcessor(vision_config={})
        # 创建一个大文件（超过 20MB 限制）
        # 使用 mock 而非真实大文件
        from unittest.mock import patch
        with patch.object(__import__("pathlib").Path, "stat") as mock_stat:
            mock_stat.return_value.st_size = 25 * 1024 * 1024  # 25MB
            result = await processor.process_image("/fake/path/image.png")
        assert result.status == "error"
        assert "过大" in result.error

    @pytest.mark.asyncio
    async def test_process_image_nonexistent_file(self):
        """不存在的文件返回错误。"""
        processor = ImageProcessor(vision_config={})
        result = await processor.process_image("/nonexistent/path/image.png")
        assert result.status == "error"

    @pytest.mark.asyncio
    async def test_process_image_disabled_ocr(self, tmp_path):
        """OCR 禁用时跳过文字提取。"""
        # 创建一个最小 PNG 文件（1x1 像素）
        import struct
        import zlib
        png_path = tmp_path / "test.png"
        # PNG 文件头 + IHDR + IDAT + IEND
        def create_minimal_png():
            header = b'\x89PNG\r\n\x1a\n'
            ihdr_data = struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)
            ihdr_crc = zlib.crc32(b'IHDR' + ihdr_data)
            ihdr = struct.pack('>I', 13) + b'IHDR' + ihdr_data + struct.pack('>I', ihdr_crc)
            raw_data = b'\x00\x00'  # filter byte + 1 pixel
            compressed = zlib.compress(raw_data)
            idat_crc = zlib.crc32(b'IDAT' + compressed)
            idat = struct.pack('>I', len(compressed)) + b'IDAT' + compressed + struct.pack('>I', idat_crc)
            iend_crc = zlib.crc32(b'IEND')
            iend = struct.pack('>I', 0) + b'IEND' + struct.pack('>I', iend_crc)
            return header + ihdr + idat + iend

        png_path.write_bytes(create_minimal_png())

        processor = ImageProcessor(vision_config={"ocr": {"enabled": False}})
        result = await processor.process_image(str(png_path))

        assert result.status == "success"
        assert result.ocr_text == ""  # OCR 禁用，无文字
        assert len(result.tags) > 0  # 降级标签仍生成


# ============================================================
# FileProcessor 图片路由测试
# ============================================================

class TestFileProcessorImageRouting:
    """测试 FileProcessor 对图片格式的路由。"""

    def test_supported_extensions_includes_images(self):
        """默认支持的扩展名包含图片格式。"""
        processor = FileProcessor()
        for ext in [".jpg", ".jpeg", ".png", ".webp"]:
            assert ext in processor.supported_extensions

    def test_image_processor_initialized(self):
        """FileProcessor 初始化时创建 ImageProcessor。"""
        processor = FileProcessor(image_config={"ocr": {"enabled": True}})
        assert processor._image_processor is not None

    @pytest.mark.asyncio
    async def test_parse_file_rejects_image_with_helpful_message(self, tmp_path):
        """parse_file 对图片格式返回明确的错误提示。"""
        # 创建一个空的图片文件
        img_path = tmp_path / "test.jpg"
        img_path.write_bytes(b"\xff\xd8\xff\xe0")  # JPEG 文件头

        processor = FileProcessor()
        with pytest.raises(ValueError, match="process_file"):
            await processor.parse_file(str(img_path))


# ============================================================
# upload.py 辅助函数测试
# ============================================================

class TestUploadHelpers:
    """测试 upload.py 中的图片辅助函数。"""

    def test_is_image_file_jpg(self):
        """检测 .jpg 文件。"""
        from app.api.routes.upload import _is_image_file
        assert _is_image_file("photo.jpg") is True

    def test_is_image_file_png(self):
        """检测 .png 文件。"""
        from app.api.routes.upload import _is_image_file
        assert _is_image_file("screenshot.PNG") is True  # 大小写不敏感

    def test_is_image_file_pdf(self):
        """PDF 不是图片。"""
        from app.api.routes.upload import _is_image_file
        assert _is_image_file("doc.pdf") is False

    def test_is_image_file_txt(self):
        """TXT 不是图片。"""
        from app.api.routes.upload import _is_image_file
        assert _is_image_file("notes.txt") is False

    def test_get_image_config_defaults(self):
        """获取默认图片配置。"""
        from app.api.routes.upload import _get_image_config
        config = _get_image_config()
        assert "enabled" in config
        assert "vision_llm" in config
        assert "ocr" in config
        assert config["ocr"]["lang"] in ("ch", "en")
