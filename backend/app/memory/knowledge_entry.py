"""
L3 长期知识库条目数据模型。

定义 KnowledgeEntry——知识库中的最小检索单元，包含：
- 内容（文本摘要/事实/文档片段）
- 向量（由 Embedding 模型生成，存储在 ChromaDB 中）
- 元数据（来源、用户、重要性、版本等）

使用方式：
    from app.memory.knowledge_entry import KnowledgeEntry

    entry = KnowledgeEntry(
        content="Python 装饰器是一种语法糖...",
        source="conversation",
        source_id="conv-abc-123",
        user_id="default",
        importance_score=0.85,
    )
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


def _now_iso() -> str:
    """返回当前 UTC 时间的 ISO 格式字符串。"""
    return datetime.now(timezone.utc).isoformat()


class KnowledgeEntry(BaseModel):
    """
    知识库条目模型。

    Attributes:
        entry_id: 条目唯一 ID（自动生成 UUID）
        content: 知识内容文本（摘要/事实/文档片段）
        source: 来源类型：conversation（对话）| document（文档）| manual（手动）
        source_id: 来源 ID（conv_id / file_path / None）
        user_id: 所属用户 ID（用于多用户隔离）
        topic: 主题标签（可选，便于分类检索）
        importance_score: 重要性评分（0.0~1.0），低于阈值的条目可被驱逐
        version: 版本号，冲突合并时递增
        supersedes: 被此条目替代的旧条目 ID（None 表示全新条目）
        metadata: 扩展元数据（file_name、chunk_index 等）
        created_at: 创建时间
        updated_at: 最后更新时间
        last_accessed_at: 最后访问时间（检索时更新）
        access_count: 被检索命中的次数
        category_l1: 一级分类大类（如 技术开发）
        category_l2: 二级分类子类（如 编程语言）
        category_l3: 三级分类细类（如 Python）
        category_confidence: 自动分类置信度（0.0~1.0）
        category_source: 分类来源（auto 自动 / manual 手动）
        series: 系列名（如「Flutter 教程」，用于系列文章归组；无系列为空串）
    """

    entry_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    content: str
    source: str = "conversation"  # conversation | document | manual
    source_id: str = ""
    user_id: str = "default"
    topic: str = ""
    importance_score: float = 0.5
    version: int = 1
    supersedes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)
    last_accessed_at: str = Field(default_factory=_now_iso)
    access_count: int = 0
    # 多级分类字段
    category_l1: str = "其他"
    category_l2: str = "待分类"
    category_l3: str = "未分类"
    category_confidence: float = 0.0
    category_source: str = "auto"
    series: str = ""

    def to_chroma_metadata(self) -> dict[str, Any]:
        """
        转换为 ChromaDB 存储的 metadata 格式。

        ChromaDB metadata 仅支持 str/int/float/bool 值，
        复杂类型需序列化。
        """
        return {
            "user_id": self.user_id,
            "source": self.source,
            "source_id": self.source_id,
            "topic": self.topic,
            "importance_score": self.importance_score,
            "version": self.version,
            "supersedes": self.supersedes or "",
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_accessed_at": self.last_accessed_at,
            "access_count": self.access_count,
            "category_l1": self.category_l1,
            "category_l2": self.category_l2,
            "category_l3": self.category_l3,
            "category_confidence": self.category_confidence,
            "category_source": self.category_source,
            "series": self.series,
        }

    @classmethod
    def from_chroma_record(
        cls,
        doc_id: str,
        document: str,
        metadata: dict[str, Any],
        distance: float = 0.0,
    ) -> KnowledgeEntry:
        """
        从 ChromaDB 查询结果构建 KnowledgeEntry。

        Args:
            doc_id: ChromaDB 中的文档 ID
            document: 文档内容
            metadata: ChromaDB 返回的元数据
            distance: 向量距离（cosine distance），越小越相似
        """
        return cls(
            entry_id=doc_id,
            content=document,
            source=metadata.get("source", "conversation"),
            source_id=metadata.get("source_id", ""),
            user_id=metadata.get("user_id", "default"),
            topic=metadata.get("topic", ""),
            importance_score=float(metadata.get("importance_score", 0.5)),
            version=int(metadata.get("version", 1)),
            supersedes=metadata.get("supersedes") or None,
            created_at=metadata.get("created_at", _now_iso()),
            updated_at=metadata.get("updated_at", _now_iso()),
            last_accessed_at=metadata.get("last_accessed_at", _now_iso()),
            access_count=int(metadata.get("access_count", 0)),
            category_l1=metadata.get("category_l1", "其他"),
            category_l2=metadata.get("category_l2", "待分类"),
            category_l3=metadata.get("category_l3", "未分类"),
            category_confidence=float(metadata.get("category_confidence", 0.0)),
            category_source=metadata.get("category_source", "auto"),
            series=metadata.get("series", ""),
            metadata={"distance": distance, **{k: v for k, v in metadata.items() if k not in (
                "user_id", "source", "source_id", "topic", "importance_score",
                "version", "supersedes", "created_at", "updated_at",
                "last_accessed_at", "access_count",
                "category_l1", "category_l2", "category_l3",
                "category_confidence", "category_source", "series",
            )}},
        )

    @property
    def similarity_score(self) -> float:
        """
        从 metadata 中的 distance 计算相似度分数（0.0~1.0）。

        缺失 distance 时返回 0.0（保守策略：宁可并存不可误删），
        避免 _detect_conflicts 中因默认值过高触发误合并。
        """
        distance = self.metadata.get("distance", 1.0)
        return max(0.0, 1.0 - distance)
