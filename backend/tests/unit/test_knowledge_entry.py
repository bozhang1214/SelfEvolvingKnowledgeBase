"""
KnowledgeEntry 数据模型的单元测试

测试内容：
- KnowledgeEntry 创建（默认值、自定义值）
- to_chroma_metadata() 转换（字段映射、supersedes 空值处理）
- from_chroma_record() 构建（往返一致性、distance 写入 metadata）
- similarity_score 属性（1 - distance，下限裁剪到 0）
- supersedes 为 None 和有值的情况
"""

from __future__ import annotations

import uuid

import pytest

from app.memory.knowledge_entry import KnowledgeEntry


# ============================================================
# 创建测试
# ============================================================

class TestKnowledgeEntryCreation:
    """测试 KnowledgeEntry 创建逻辑"""

    def test_create_with_defaults(self):
        """仅提供必填的 content，其余字段使用默认值"""
        entry = KnowledgeEntry(content="测试内容")

        assert entry.content == "测试内容"
        assert entry.source == "conversation"
        assert entry.source_id == ""
        assert entry.user_id == "default"
        assert entry.topic == ""
        assert entry.importance_score == 0.5
        assert entry.version == 1
        assert entry.supersedes is None
        assert entry.metadata == {}
        assert entry.access_count == 0
        # entry_id 为合法 UUID
        uuid.UUID(entry.entry_id)
        # 三个时间戳字段非空
        assert entry.created_at
        assert entry.updated_at
        assert entry.last_accessed_at

    def test_create_with_custom_values(self):
        """所有自定义字段均能正确赋值"""
        entry = KnowledgeEntry(
            content="Python 装饰器是语法糖",
            source="document",
            source_id="/data/file.pdf",
            user_id="user-001",
            topic="python",
            importance_score=0.85,
            version=3,
            supersedes="old-entry-id-123",
            metadata={"chunk_index": 2, "file_name": "file.pdf"},
            access_count=5,
        )

        assert entry.content == "Python 装饰器是语法糖"
        assert entry.source == "document"
        assert entry.source_id == "/data/file.pdf"
        assert entry.user_id == "user-001"
        assert entry.topic == "python"
        assert entry.importance_score == 0.85
        assert entry.version == 3
        assert entry.supersedes == "old-entry-id-123"
        assert entry.metadata["chunk_index"] == 2
        assert entry.metadata["file_name"] == "file.pdf"
        assert entry.access_count == 5

    def test_create_generates_unique_entry_ids(self):
        """多次创建生成不同的 entry_id"""
        e1 = KnowledgeEntry(content="a")
        e2 = KnowledgeEntry(content="b")
        assert e1.entry_id != e2.entry_id

    def test_supersedes_none_by_default(self):
        """默认 supersedes 为 None"""
        entry = KnowledgeEntry(content="内容")
        assert entry.supersedes is None

    def test_supersedes_with_value(self):
        """显式设置 supersedes"""
        entry = KnowledgeEntry(content="内容", supersedes="old-id")
        assert entry.supersedes == "old-id"


# ============================================================
# to_chroma_metadata 测试
# ============================================================

class TestToChromaMetadata:
    """测试 to_chroma_metadata 转换"""

    def test_contains_all_expected_fields(self):
        """转换结果包含所有必需字段"""
        entry = KnowledgeEntry(content="内容", user_id="u1", source="document")
        meta = entry.to_chroma_metadata()

        expected_keys = {
            "user_id", "source", "source_id", "topic",
            "importance_score", "version", "supersedes",
            "created_at", "updated_at", "last_accessed_at", "access_count",
            "category_l1", "category_l2", "category_l3",
            "category_confidence", "category_source", "series",
        }
        assert set(meta.keys()) == expected_keys

    def test_field_values_match_entry(self):
        """字段值与原条目一致"""
        entry = KnowledgeEntry(
            content="内容",
            user_id="u1",
            source="manual",
            source_id="src-1",
            topic="t1",
            importance_score=0.7,
            version=2,
            access_count=3,
        )
        meta = entry.to_chroma_metadata()

        assert meta["user_id"] == "u1"
        assert meta["source"] == "manual"
        assert meta["source_id"] == "src-1"
        assert meta["topic"] == "t1"
        assert meta["importance_score"] == 0.7
        assert meta["version"] == 2
        assert meta["access_count"] == 3

    def test_supersedes_none_becomes_empty_string(self):
        """supersedes 为 None 时转换为空字符串（ChromaDB 不支持 None）"""
        entry = KnowledgeEntry(content="内容", supersedes=None)
        meta = entry.to_chroma_metadata()
        assert meta["supersedes"] == ""

    def test_supersedes_with_value_preserved(self):
        """supersedes 有值时原样保留"""
        entry = KnowledgeEntry(content="内容", supersedes="old-id-abc")
        meta = entry.to_chroma_metadata()
        assert meta["supersedes"] == "old-id-abc"

    def test_does_not_include_content_or_entry_id(self):
        """转换结果不含 content 与 entry_id（它们由 ChromaDB 单独存储）"""
        entry = KnowledgeEntry(content="内容")
        meta = entry.to_chroma_metadata()
        assert "content" not in meta
        assert "entry_id" not in meta
        assert "metadata" not in meta


# ============================================================
# from_chroma_record 测试
# ============================================================

class TestFromChromaRecord:
    """测试 from_chroma_record 构建"""

    def test_build_with_full_metadata(self):
        """完整元数据可正确重建条目"""
        meta = {
            "user_id": "u1",
            "source": "document",
            "source_id": "file.pdf",
            "topic": "python",
            "importance_score": 0.9,
            "version": 2,
            "supersedes": "old-id",
            "created_at": "2024-01-01T00:00:00+00:00",
            "updated_at": "2024-01-02T00:00:00+00:00",
            "last_accessed_at": "2024-01-03T00:00:00+00:00",
            "access_count": 4,
        }
        entry = KnowledgeEntry.from_chroma_record(
            doc_id="doc-1",
            document="文档内容",
            metadata=meta,
            distance=0.2,
        )

        assert entry.entry_id == "doc-1"
        assert entry.content == "文档内容"
        assert entry.user_id == "u1"
        assert entry.source == "document"
        assert entry.source_id == "file.pdf"
        assert entry.topic == "python"
        assert entry.importance_score == 0.9
        assert entry.version == 2
        assert entry.supersedes == "old-id"
        assert entry.created_at == "2024-01-01T00:00:00+00:00"
        assert entry.access_count == 4

    def test_build_with_empty_supersedes(self):
        """supersedes 为空字符串时转换为 None"""
        meta = {"supersedes": ""}
        entry = KnowledgeEntry.from_chroma_record(
            doc_id="doc-1", document="内容", metadata=meta
        )
        assert entry.supersedes is None

    def test_build_with_missing_supersedes_key(self):
        """metadata 缺少 supersedes 键时默认为 None"""
        entry = KnowledgeEntry.from_chroma_record(
            doc_id="doc-1", document="内容", metadata={}
        )
        assert entry.supersedes is None

    def test_build_with_missing_keys_uses_defaults(self):
        """缺少字段时使用默认值"""
        entry = KnowledgeEntry.from_chroma_record(
            doc_id="doc-1", document="内容", metadata={}
        )
        assert entry.source == "conversation"
        assert entry.source_id == ""
        assert entry.user_id == "default"
        assert entry.topic == ""
        assert entry.importance_score == 0.5
        assert entry.version == 1
        assert entry.access_count == 0

    def test_distance_stored_in_metadata(self):
        """distance 参数被写入 metadata 字典"""
        entry = KnowledgeEntry.from_chroma_record(
            doc_id="doc-1", document="内容", metadata={}, distance=0.3
        )
        assert entry.metadata["distance"] == 0.3

    def test_default_distance_is_zero(self):
        """未传 distance 时默认为 0.0"""
        entry = KnowledgeEntry.from_chroma_record(
            doc_id="doc-1", document="内容", metadata={}
        )
        assert entry.metadata["distance"] == 0.0

    def test_extra_metadata_preserved(self):
        """metadata 中非标准字段被保留到 entry.metadata"""
        meta = {
            "user_id": "u1",
            "chunk_index": 5,
            "file_name": "doc.pdf",
        }
        entry = KnowledgeEntry.from_chroma_record(
            doc_id="doc-1", document="内容", metadata=meta, distance=0.1
        )
        # distance 与额外字段都保留
        assert entry.metadata["distance"] == 0.1
        assert entry.metadata["chunk_index"] == 5
        assert entry.metadata["file_name"] == "doc.pdf"
        # 标准字段不应重复出现在 metadata 中
        assert "user_id" not in entry.metadata

    def test_round_trip(self):
        """to_chroma_metadata → from_chroma_record 往返一致"""
        original = KnowledgeEntry(
            content="往返测试内容",
            user_id="u1",
            source="document",
            source_id="src",
            topic="t",
            importance_score=0.66,
            version=4,
            supersedes="old",
            access_count=7,
        )
        meta = original.to_chroma_metadata()
        rebuilt = KnowledgeEntry.from_chroma_record(
            doc_id=original.entry_id,
            document=original.content,
            metadata=meta,
            distance=0.0,
        )

        assert rebuilt.entry_id == original.entry_id
        assert rebuilt.content == original.content
        assert rebuilt.user_id == original.user_id
        assert rebuilt.source == original.source
        assert rebuilt.source_id == original.source_id
        assert rebuilt.topic == original.topic
        assert rebuilt.importance_score == original.importance_score
        assert rebuilt.version == original.version
        assert rebuilt.supersedes == original.supersedes
        assert rebuilt.access_count == original.access_count


# ============================================================
# similarity_score 属性测试
# ============================================================

class TestSimilarityScore:
    """测试 similarity_score 属性"""

    def test_distance_zero_returns_one(self):
        """distance=0 时相似度为 1.0"""
        entry = KnowledgeEntry(content="内容")
        entry.metadata["distance"] = 0.0
        assert entry.similarity_score == 1.0

    def test_distance_within_range(self):
        """distance=0.2 时相似度为 0.8"""
        entry = KnowledgeEntry(content="内容")
        entry.metadata["distance"] = 0.2
        assert entry.similarity_score == 0.8

    def test_distance_one_returns_zero(self):
        """distance=1.0 时相似度为 0.0"""
        entry = KnowledgeEntry(content="内容")
        entry.metadata["distance"] = 1.0
        assert entry.similarity_score == 0.0

    def test_distance_above_one_clamped_to_zero(self):
        """distance>1.0 时相似度裁剪到 0.0"""
        entry = KnowledgeEntry(content="内容")
        entry.metadata["distance"] = 1.5
        assert entry.similarity_score == 0.0

    def test_no_distance_defaults_to_zero(self):
        """metadata 中无 distance 时默认相似度为 0.0（保守策略：宁可并存不可误删）"""
        entry = KnowledgeEntry(content="内容")
        assert entry.similarity_score == 0.0

    def test_from_chroma_record_similarity(self):
        """from_chroma_record 构建的条目 similarity_score 与 distance 一致"""
        entry = KnowledgeEntry.from_chroma_record(
            doc_id="d1", document="内容", metadata={}, distance=0.25
        )
        assert entry.similarity_score == 0.75
