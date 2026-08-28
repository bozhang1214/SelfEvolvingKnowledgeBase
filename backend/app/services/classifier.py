"""
文档自动分类服务。

基于 LLM 分析文档内容，匹配到预设的三级分类目录（大类 → 子类 → 细类）。

设计要点：
- 使用 ``supervisor`` 角色（deepseek-chat, JSON 输出, 低温度），保证分类稳定
- 输入文档内容样本（文件名 + 前 N 字符），输出 (l1, l2, l3, confidence)
- 失败时降级到默认 "其他/待分类/未分类"，不影响上传主流程
- 校验 LLM 输出是否在合法目录内，非法则降级

使用方式：
    from app.services.classifier import DocumentClassifier

    classifier = DocumentClassifier(ctx.llm_factory)
    result = await classifier.classify(content=chunk_text, file_name="python_guide.pdf")
    # result = {"l1": "技术开发", "l2": "编程语言", "l3": "Python", "confidence": 0.9}
"""
from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.categories import (
    DEFAULT_FALLBACK_CATEGORY,
    build_category_prompt,
    validate_category,
)
from app.core.llm_factory import LLMFactory
from app.core.logging import get_logger

logger = get_logger(__name__)

# 分类时截取的内容样本长度（避免超出 token 限制）
_SAMPLE_MAX_CHARS = 1500

# 复用 supervisor 角色（JSON 输出 + 低温度，适合分类）
_CLASSIFY_ROLE = "supervisor"

_SYSTEM_PROMPT = """你是一个文档分类助手。你的任务是根据文档内容，将其归类到预设的三级分类目录中。

可用分类目录如下（大类 > 子类 > 细类）：
{categories}

分类规则：
1. 必须从上述目录中选择，不可自创分类
2. 优先匹配到最具体的细类（l3）
3. 如果文档内容跨多个类别，选择最主要的一个
4. confidence 表示你对本次分类的把握（0.0~1.0），把握不大时给低分

只输出 JSON，格式如下：
{{"l1": "一级大类", "l2": "二级子类", "l3": "三级细类", "confidence": 0.85}}"""


class DocumentClassifier:
    """基于 LLM 的文档自动分类器。"""

    def __init__(self, llm_factory: LLMFactory) -> None:
        self._llm_factory = llm_factory
        self._system_prompt = _SYSTEM_PROMPT.format(
            categories=build_category_prompt()
        )

    async def classify(
        self,
        content: str,
        file_name: str = "",
    ) -> dict[str, Any]:
        """
        分析文档内容并返回三级分类结果。

        Args:
            content: 文档文本内容（将截取前 1500 字作为样本）
            file_name: 文件名（辅助分类判断）

        Returns:
            {"l1": ..., "l2": ..., "l3": ..., "confidence": ...}
            失败时返回默认 "其他/待分类/未分类"，confidence=0.0
        """
        sample = self._build_sample(content, file_name)
        if not sample.strip():
            return self._fallback()

        try:
            llm = self._llm_factory.get(_CLASSIFY_ROLE)
            messages = [
                SystemMessage(content=self._system_prompt),
                HumanMessage(content=f"文件名: {file_name or '(未知)'}\n\n文档内容:\n{sample}"),
            ]
            response = await llm.ainvoke(messages)
            raw = response.content if hasattr(response, "content") else str(response)
            return self._parse_response(raw)
        except Exception as e:
            logger.warning("文档自动分类失败，降级为默认分类", error=str(e), file_name=file_name)
            return self._fallback()

    def _build_sample(self, content: str, file_name: str) -> str:
        """构建分类输入样本（截断 + 去空白）。"""
        text = content.strip()
        if len(text) > _SAMPLE_MAX_CHARS:
            text = text[:_SAMPLE_MAX_CHARS]
        return text

    def _parse_response(self, raw: str | list) -> dict[str, Any]:
        """解析 LLM 返回的 JSON，校验合法性。"""
        try:
            # 兼容 langchain 返回 list(含 tool_calls) 或 str
            if isinstance(raw, list):
                # 取第一个文本块
                raw = next(
                    (b.get("text", "") for b in raw if isinstance(b, dict) and b.get("type") == "text"),
                    "",
                )
            if not isinstance(raw, str):
                raw = str(raw)

            # 提取首个 JSON 对象（容忍前后多余文本）
            raw = raw.strip()
            start = raw.find("{")
            end = raw.rfind("}")
            if start == -1 or end == -1:
                return self._fallback()
            payload = json.loads(raw[start: end + 1])

            l1 = str(payload.get("l1", "")).strip()
            l2 = str(payload.get("l2", "")).strip()
            l3 = str(payload.get("l3", "")).strip()
            confidence = float(payload.get("confidence", 0.0) or 0.0)

            # 校验分类合法性
            if not validate_category(l1, l2, l3):
                logger.info("LLM 分类结果不在目录内，降级", l1=l1, l2=l2, l3=l3)
                return self._fallback()

            return {
                "l1": l1,
                "l2": l2,
                "l3": l3,
                "confidence": max(0.0, min(1.0, confidence)),
            }
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning("分类响应解析失败", error=str(e), raw=str(raw)[:200])
            return self._fallback()

    def _fallback(self) -> dict[str, Any]:
        """返回默认降级分类。"""
        l1, l2, l3 = DEFAULT_FALLBACK_CATEGORY
        return {"l1": l1, "l2": l2, "l3": l3, "confidence": 0.0}
