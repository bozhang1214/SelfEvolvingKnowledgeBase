"""BM25 关键词检索模块（基于 ``rank_bm25`` + ``jieba`` 分词）。

作为 RAG 混合检索的关键词召回通道，与向量检索互补：
- 向量检索擅长语义相似，但对专有名词 / API 名 / 版本号 / 编号等精确词不敏感；
- BM25 擅长精确词匹配，可召回向量通道漏掉的硬匹配结果。

使用方式：
    from app.tools.rag.bm25 import BM25Index, tokenize

    idx = BM25Index()
    idx.build([{"entry_id": "e1", "content": "Python 装饰器语法糖"}])
    hits = idx.search("装饰器", top_k=5)  # [(doc_dict, score), ...]
"""

from __future__ import annotations

import re
from typing import Any

import jieba
from rank_bm25 import BM25Okapi

# 中文字符范围（用于判断是否需要 jieba 分词，英文/数字直接按词切分）
_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]")
# 英文单词 / 数字（jieba 分词结果里会保留，此处主要用于纯英文回退）
_ALNUM_PATTERN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """把文本切分为词元列表（中文 jieba 搜索模式分词 + 英文/数字小写）。

    - 含中文时使用 ``jieba.lcut_for_search``（更细粒度，利于召回）；
    - 纯英文/数字时按非字母数字切分并小写（避免 jieba 把英文短语切得过碎）。
    返回过滤掉空白/纯标点后的词元列表。
    """
    s = (text or "").strip().lower()
    if not s:
        return []
    if _CJK_PATTERN.search(s):
        tokens = [t for t in jieba.lcut_for_search(s) if t.strip()]
    else:
        tokens = _ALNUM_PATTERN.findall(s)
    # 去空、去纯标点（jieba 可能返回单个标点符号）
    return [t for t in tokens if t.strip() and not _is_punct_only(t)]


def _is_punct_only(token: str) -> bool:
    """判断词元是否仅由标点/空白组成。"""
    return not any(ch.isalnum() or _CJK_PATTERN.match(ch) for ch in token)


class BM25Index:
    """BM25 倒排索引封装：构建语料 → 关键词打分召回。"""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self._docs: list[dict[str, Any]] = []
        self._bm25: BM25Okapi | None = None

    def build(self, docs: list[dict[str, Any]]) -> None:
        """用文档列表构建索引。

        Args:
            docs: 文档字典列表，每项至少含 ``content`` 字段；
                  其余字段（entry_id / source / importance 等）原样保留，
                  供 ``search`` 返回时随命中一起带回。
        """
        self._docs = list(docs)
        if not self._docs:
            self._bm25 = None
            return
        corpus = [tokenize(d.get("content", "")) for d in self._docs]
        self._bm25 = BM25Okapi(corpus, k1=self.k1, b=self.b)

    def search(self, query: str, top_k: int = 5) -> list[tuple[dict[str, Any], float]]:
        """按 BM25 打分返回 top_k 个命中。

        Returns:
            ``[(doc_dict, score), ...]`` 按分数降序；score>0 才返回。
        """
        if self._bm25 is None or not self._docs:
            return []
        q_tokens = tokenize(query)
        if not q_tokens:
            return []
        scores = self._bm25.get_scores(q_tokens)
        ranked = sorted(range(len(scores)), key=lambda i: -scores[i])
        return [
            (self._docs[i], float(scores[i]))
            for i in ranked[:top_k]
            if scores[i] > 0
        ]

    @property
    def doc_count(self) -> int:
        """语料文档数。"""
        return len(self._docs)
