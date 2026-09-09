"""
文档自动分类服务的单元测试

测试内容：
- classify 正常返回合法分类（mock LLM）
- classify 分类结果不在目录内时降级
- classify LLM 调用异常时降级
- classify 空内容时直接降级（不调用 LLM）
- _parse_response 容忍前后多余文本
- _parse_response 非法 JSON / 非字典时降级
- _fallback 返回默认分类且 confidence=0.0
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.categories import DEFAULT_FALLBACK_CATEGORY
from app.services.classifier import DocumentClassifier


def _make_factory(content: str | None = None, exc: Exception | None = None) -> MagicMock:
    """构造 mock LLM 工厂，返回指定 content 或抛异常。"""
    factory = MagicMock()
    if exc is not None:
        factory.ainvoke_with_stats = AsyncMock(side_effect=exc)
    else:
        resp = MagicMock()
        resp.content = content
        factory.ainvoke_with_stats = AsyncMock(return_value=resp)
    return factory


class TestClassify:
    """测试 classify 主流程"""

    @pytest.mark.asyncio
    async def test_valid_category(self):
        raw = '{"l1": "技术开发", "l2": "编程语言", "l3": "Python", "confidence": 0.9}'
        factory = _make_factory(raw)
        result = await DocumentClassifier(factory).classify(content="print('hi')", file_name="x.py")
        assert result["l1"] == "技术开发"
        assert result["l2"] == "编程语言"
        assert result["l3"] == "Python"
        assert result["confidence"] == pytest.approx(0.9)

    @pytest.mark.asyncio
    async def test_invalid_category_falls_back(self):
        raw = '{"l1": "不存在", "l2": "x", "l3": "y", "confidence": 0.9}'
        factory = _make_factory(raw)
        result = await DocumentClassifier(factory).classify(content="内容", file_name="x.txt")
        l1, l2, l3 = DEFAULT_FALLBACK_CATEGORY
        assert (result["l1"], result["l2"], result["l3"]) == (l1, l2, l3)
        assert result["confidence"] == 0.0

    @pytest.mark.asyncio
    async def test_llm_exception_falls_back(self):
        factory = _make_factory(exc=RuntimeError("boom"))
        result = await DocumentClassifier(factory).classify(content="内容", file_name="x.txt")
        l1, l2, l3 = DEFAULT_FALLBACK_CATEGORY
        assert (result["l1"], result["l2"], result["l3"]) == (l1, l2, l3)
        assert result["confidence"] == 0.0

    @pytest.mark.asyncio
    async def test_empty_content_skips_llm(self):
        factory = _make_factory('{"l1": "技术开发", "l2": "编程语言", "l3": "Python", "confidence": 0.9}')
        result = await DocumentClassifier(factory).classify(content="   ", file_name="x.txt")
        l1, l2, l3 = DEFAULT_FALLBACK_CATEGORY
        assert (result["l1"], result["l2"], result["l3"]) == (l1, l2, l3)
        # 空内容不应触发 LLM 调用
        factory.ainvoke_with_stats.assert_not_called()


class TestParseResponse:
    """测试响应解析"""

    def _classifier(self) -> DocumentClassifier:
        return DocumentClassifier(MagicMock())

    def test_plain_json(self):
        result = self._classifier()._parse_response(
            '{"l1": "技术开发", "l2": "AI / Agent 开发", "l3": "RAG 检索增强", "confidence": 0.8}'
        )
        assert result["l3"] == "RAG 检索增强"

    def test_json_with_surrounding_text(self):
        """容忍前后多余文本（LLM 常见行为）"""
        raw = '好的，分类结果如下：\n{"l1": "技术开发", "l2": "编程语言", "l3": "Go", "confidence": 0.7}\n以上。'
        result = self._classifier()._parse_response(raw)
        assert (result["l1"], result["l2"], result["l3"]) == ("技术开发", "编程语言", "Go")

    def test_invalid_json_falls_back(self):
        result = self._classifier()._parse_response("这不是 JSON")
        assert result["confidence"] == 0.0

    def test_non_string_list_input(self):
        """langchain 返回 list 时取首个文本块"""
        result = self._classifier()._parse_response(
            [{"type": "text", "text": '{"l1": "技术开发", "l2": "编程语言", "l3": "Java", "confidence": 0.6}'}]
        )
        assert result["l3"] == "Java"

    def test_confidence_clamped(self):
        """confidence 超出 [0,1] 时被裁剪"""
        result = self._classifier()._parse_response(
            '{"l1": "技术开发", "l2": "编程语言", "l3": "Python", "confidence": 2.5}'
        )
        assert result["confidence"] == 1.0


class TestFallback:
    """测试降级结果"""

    def test_fallback_shape(self):
        result = DocumentClassifier(MagicMock())._fallback()
        l1, l2, l3 = DEFAULT_FALLBACK_CATEGORY
        assert result == {"l1": l1, "l2": l2, "l3": l3, "confidence": 0.0}
