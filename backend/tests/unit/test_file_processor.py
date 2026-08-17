"""
文件处理器与文本分块模块的单元测试

测试内容：
- chunk_text 分块逻辑（段落、句子、空文本、overlap、参数校验）
- FileProcessor 支持的扩展名
- parse_file 方法（txt 文件解析）
- process_file 完整流程
- 不支持的类型抛出 ValueError
- ProcessResult 字段验证

技术要点：
- 使用 tmp_path 内置夹具处理临时文件
- 异步方法使用 @pytest.mark.asyncio 标记
"""

from __future__ import annotations

import pytest

from app.tools.file_processor import (
    FileProcessor,
    ProcessResult,
    chunk_text,
)


# ============================================================
# chunk_text 分块逻辑测试
# ============================================================

class TestChunkTextBasic:
    """测试 chunk_text 基础分块"""

    def test_empty_text_returns_empty_list(self):
        """空文本返回空列表"""
        assert chunk_text("") == []

    def test_whitespace_only_text_returns_empty_list(self):
        """纯空白文本返回空列表"""
        assert chunk_text("   \n\n  \t  ") == []

    def test_short_text_returns_single_chunk(self):
        """短文本（< chunk_size）返回单个块"""
        text = "这是一段短文本。"
        result = chunk_text(text, chunk_size=500, overlap=50)
        assert len(result) == 1
        assert result[0] == text

    def test_paragraph_splitting(self):
        """多段落文本按段落聚合"""
        para1 = "段落一的内容。"
        para2 = "段落二的内容。"
        text = f"{para1}\n\n{para2}"
        # chunk_size 足够大，两个段落应合并为一个块
        result = chunk_text(text, chunk_size=500, overlap=0)
        assert len(result) == 1
        assert para1 in result[0]
        assert para2 in result[0]

    def test_multiple_paragraphs_split_when_exceeding_chunk_size(self):
        """段落总长超过 chunk_size 时被切分为多个块"""
        para1 = "A" * 200
        para2 = "B" * 200
        para3 = "C" * 200
        text = f"{para1}\n\n{para2}\n\n{para3}"
        # chunk_size=250，每块最多容纳一个 200 字符段落
        result = chunk_text(text, chunk_size=250, overlap=0)
        assert len(result) >= 2

    def test_sentence_splitting(self):
        """超长段落按句子切分"""
        text = "第一句话内容。第二句话内容。第三句话内容。"
        # chunk_size 较小，触发句子级切分
        result = chunk_text(text, chunk_size=12, overlap=0)
        assert len(result) >= 2

    def test_hard_cut_for_long_sentence(self):
        """超长句子（无句末标点）按 chunk_size 硬切"""
        text = "abcdefghij" * 5  # 50 字符无标点
        result = chunk_text(text, chunk_size=20, overlap=0)
        # 应被硬切为至少 3 块
        assert len(result) >= 3
        # 每块不超过 chunk_size（最后一块可能更短）
        for chunk in result:
            assert len(chunk) <= 20

    def test_chinese_sentence_end_punctuation(self):
        """中文句末标点（。！？）正确触发切分"""
        text = "你好。世界！测试？"
        result = chunk_text(text, chunk_size=6, overlap=0)
        # 切分为多个块（每个句末标点保留在前一句）
        assert len(result) >= 2


class TestChunkTextOverlap:
    """测试 chunk_text 重叠（overlap）逻辑"""

    def test_overlap_creates_context_overlap(self):
        """overlap>0 时相邻块存在字符重叠"""
        # 构造足够长的文本触发多块
        text = "段落A" * 50 + "\n\n" + "段落B" * 50
        result = chunk_text(text, chunk_size=30, overlap=10)
        if len(result) >= 2:
            # 第二块开头应包含第一块末尾的部分字符（overlap）
            first_tail = result[0][-10:]
            # overlap 字符应出现在第二块开头附近
            assert first_tail in result[1]

    def test_zero_overlap_no_overlap(self):
        """overlap=0 时相邻块不以上一块末尾字符开头"""
        # 构造足够长的文本触发多块
        text = "段落A" * 50 + "\n\n" + "段落B" * 50
        result = chunk_text(text, chunk_size=30, overlap=0)
        assert len(result) >= 2
        # overlap=0 时，下一块不应以上一块末尾字符作为前缀（无重叠）
        for i in range(1, len(result)):
            prev_tail = result[i - 1][-5:]
            assert not result[i].startswith(prev_tail)

    def test_overlap_zero_vs_positive_total_length(self):
        """overlap>0 时总字符数不少于 overlap=0（overlap 重复内容）"""
        text = "段落A" * 50 + "\n\n" + "段落B" * 50
        no_overlap = chunk_text(text, chunk_size=30, overlap=0)
        with_overlap = chunk_text(text, chunk_size=30, overlap=10)
        # 有 overlap 时总字符数应大于等于无 overlap
        assert sum(len(c) for c in with_overlap) >= sum(len(c) for c in no_overlap)


class TestChunkTextParameterValidation:
    """测试 chunk_text 参数校验"""

    def test_chunk_size_zero_raises_value_error(self):
        """chunk_size=0 抛出 ValueError"""
        with pytest.raises(ValueError, match="chunk_size"):
            chunk_text("文本", chunk_size=0)

    def test_chunk_size_negative_raises_value_error(self):
        """chunk_size 为负数抛出 ValueError"""
        with pytest.raises(ValueError, match="chunk_size"):
            chunk_text("文本", chunk_size=-10)

    def test_overlap_negative_raises_value_error(self):
        """overlap 为负数抛出 ValueError"""
        with pytest.raises(ValueError, match="overlap"):
            chunk_text("文本", chunk_size=100, overlap=-1)

    def test_overlap_equal_to_chunk_size_raises_value_error(self):
        """overlap == chunk_size 抛出 ValueError"""
        with pytest.raises(ValueError, match="overlap"):
            chunk_text("文本", chunk_size=100, overlap=100)

    def test_overlap_greater_than_chunk_size_raises_value_error(self):
        """overlap > chunk_size 抛出 ValueError"""
        with pytest.raises(ValueError, match="overlap"):
            chunk_text("文本", chunk_size=50, overlap=60)

    def test_overlap_just_below_chunk_size_is_valid(self):
        """overlap = chunk_size - 1 是合法的（边界值）"""
        text = "A" * 200
        # 不应抛出异常
        result = chunk_text(text, chunk_size=50, overlap=49)
        assert isinstance(result, list)


# ============================================================
# FileProcessor 创建与扩展名测试
# ============================================================

class TestFileProcessorCreation:
    """测试 FileProcessor 创建与扩展名"""

    def test_default_supported_extensions(self):
        """默认支持 .txt/.md/.markdown/.pdf/.docx"""
        processor = FileProcessor()
        assert ".txt" in processor.supported_extensions
        assert ".md" in processor.supported_extensions
        assert ".markdown" in processor.supported_extensions
        assert ".pdf" in processor.supported_extensions
        assert ".docx" in processor.supported_extensions

    def test_custom_supported_extensions(self):
        """自定义扩展名列表"""
        processor = FileProcessor(supported_extensions=[".txt", ".log"])
        assert processor.supported_extensions == [".txt", ".log"]

    def test_extensions_normalized_to_lowercase(self):
        """扩展名被转为小写"""
        processor = FileProcessor(supported_extensions=[".TXT", ".MD"])
        assert ".txt" in processor.supported_extensions
        assert ".md" in processor.supported_extensions
        assert ".TXT" not in processor.supported_extensions


# ============================================================
# parse_file 测试
# ============================================================

class TestParseFile:
    """测试 parse_file 方法"""

    async def test_parse_txt_file(self, tmp_path):
        """解析 txt 文件返回内容"""
        file_path = tmp_path / "test.txt"
        file_path.write_text("这是测试文本内容。", encoding="utf-8")

        processor = FileProcessor()
        content = await processor.parse_file(str(file_path))

        assert content == "这是测试文本内容。"

    async def test_parse_markdown_file(self, tmp_path):
        """解析 md 文件返回内容"""
        file_path = tmp_path / "test.md"
        file_path.write_text("# 标题\n\n正文内容。", encoding="utf-8")

        processor = FileProcessor()
        content = await processor.parse_file(str(file_path))

        assert "# 标题" in content
        assert "正文内容。" in content

    async def test_parse_unsupported_type_raises_value_error(self, tmp_path):
        """不支持的扩展名抛出 ValueError"""
        file_path = tmp_path / "test.xyz"
        file_path.write_text("内容", encoding="utf-8")

        processor = FileProcessor()
        with pytest.raises(ValueError, match="不支持的文件类型"):
            await processor.parse_file(str(file_path))

    async def test_parse_nonexistent_file_raises_not_found(self, tmp_path):
        """文件不存在抛出 FileNotFoundError"""
        processor = FileProcessor()
        with pytest.raises(FileNotFoundError):
            await processor.parse_file(str(tmp_path / "nonexistent.txt"))

    async def test_parse_unsupported_type_checked_before_existence(self, tmp_path):
        """不支持的类型在文件存在性校验之前就拒绝"""
        # 文件不存在但扩展名不支持，应优先抛出 ValueError
        processor = FileProcessor()
        with pytest.raises(ValueError, match="不支持的文件类型"):
            await processor.parse_file(str(tmp_path / "nonexistent.xyz"))

    async def test_parse_txt_with_utf8_chinese(self, tmp_path):
        """txt 文件 UTF-8 中文内容正确解析"""
        file_path = tmp_path / "chinese.txt"
        file_path.write_text("你好世界，中文测试。", encoding="utf-8")

        processor = FileProcessor()
        content = await processor.parse_file(str(file_path))

        assert content == "你好世界，中文测试。"


# ============================================================
# process_file 测试
# ============================================================

class TestProcessFile:
    """测试 process_file 完整流程"""

    async def test_process_file_success(self, tmp_path):
        """成功处理返回 status=success 的 ProcessResult"""
        file_path = tmp_path / "test.txt"
        content = "这是一段测试文本内容，用于验证处理流程。"
        file_path.write_text(content, encoding="utf-8")

        processor = FileProcessor()
        result = await processor.process_file(
            file_path=str(file_path),
            user_id="u1",
            metadata={"original_name": "test.txt"},
        )

        assert result.status == "success"
        assert result.file_name == "test.txt"
        assert result.file_size > 0
        assert result.content_length == len(content)
        assert result.chunks_count >= 1
        assert len(result.chunks) == result.chunks_count
        assert result.error == ""

    async def test_process_file_result_is_process_result_instance(self, tmp_path):
        """返回值为 ProcessResult 实例"""
        file_path = tmp_path / "test.txt"
        file_path.write_text("内容", encoding="utf-8")

        processor = FileProcessor()
        result = await processor.process_file(
            file_path=str(file_path), user_id="u1", metadata={}
        )

        assert isinstance(result, ProcessResult)

    async def test_process_file_error_on_nonexistent(self, tmp_path):
        """文件不存在返回 status=error"""
        processor = FileProcessor()
        result = await processor.process_file(
            file_path=str(tmp_path / "nonexistent.txt"),
            user_id="u1",
            metadata={},
        )

        assert result.status == "error"
        assert result.error != ""
        assert result.chunks == []
        assert result.chunks_count == 0

    async def test_process_file_error_on_unsupported_type(self, tmp_path):
        """不支持的文件类型返回 status=error"""
        file_path = tmp_path / "test.xyz"
        file_path.write_text("内容", encoding="utf-8")

        processor = FileProcessor()
        result = await processor.process_file(
            file_path=str(file_path), user_id="u1", metadata={}
        )

        assert result.status == "error"
        assert "不支持" in result.error

    async def test_process_file_empty_content(self, tmp_path):
        """空内容文件处理成功但分块为空"""
        file_path = tmp_path / "empty.txt"
        file_path.write_text("", encoding="utf-8")

        processor = FileProcessor()
        result = await processor.process_file(
            file_path=str(file_path), user_id="u1", metadata={}
        )

        assert result.status == "success"
        assert result.chunks == []
        assert result.chunks_count == 0
        assert result.content_length == 0

    async def test_process_file_chunks_are_non_empty_strings(self, tmp_path):
        """成功处理的分块均为非空字符串"""
        # 构造较长文本以确保产生多个分块
        content = "这是第一段内容。" * 50 + "\n\n" + "这是第二段内容。" * 50
        file_path = tmp_path / "long.txt"
        file_path.write_text(content, encoding="utf-8")

        processor = FileProcessor()
        result = await processor.process_file(
            file_path=str(file_path),
            user_id="u1",
            metadata={},
        )

        assert result.status == "success"
        for chunk in result.chunks:
            assert isinstance(chunk, str)
            assert chunk.strip() != ""


# ============================================================
# ProcessResult 数据结构测试
# ============================================================

class TestProcessResult:
    """测试 ProcessResult 数据结构"""

    def test_success_result_fields(self):
        """成功结果字段验证"""
        result = ProcessResult(
            file_path="/path/file.txt",
            file_name="file.txt",
            file_size=100,
            content_length=80,
            chunks=["块1", "块2"],
            chunks_count=2,
            status="success",
        )
        assert result.file_path == "/path/file.txt"
        assert result.file_name == "file.txt"
        assert result.file_size == 100
        assert result.content_length == 80
        assert result.chunks == ["块1", "块2"]
        assert result.chunks_count == 2
        assert result.status == "success"
        assert result.error == ""  # 默认值

    def test_error_result_has_error_message(self):
        """错误结果包含 error 信息"""
        result = ProcessResult(
            file_path="/path/file.xyz",
            file_name="file.xyz",
            file_size=0,
            content_length=0,
            chunks=[],
            chunks_count=0,
            status="error",
            error="不支持的文件类型",
        )
        assert result.status == "error"
        assert result.error == "不支持的文件类型"
        assert result.chunks == []

    def test_error_default_empty(self):
        """error 字段默认为空字符串"""
        result = ProcessResult(
            file_path="p",
            file_name="n",
            file_size=0,
            content_length=0,
            chunks=[],
            chunks_count=0,
            status="success",
        )
        assert result.error == ""
