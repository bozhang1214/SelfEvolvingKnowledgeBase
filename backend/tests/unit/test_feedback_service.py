"""反馈数据飞轮（feedback_service）的单元测试。"""

from __future__ import annotations

import pytest

from app.services.feedback_service import apply_feedback


class _FakeEntry:
    def __init__(self, importance: float):
        self.importance_score = importance


class _FakeKB:
    """模拟 KnowledgeBaseBackend（含 update_metadata / delete / get）。"""

    def __init__(self):
        self.entries = {"e1": _FakeEntry(0.5), "e2": _FakeEntry(0.1)}
        self.deleted: list[str] = []

    async def get(self, entry_id: str):
        return self.entries.get(entry_id)

    async def update_metadata(self, entry_id: str, metadata_updates: dict):
        self.entries[entry_id].importance_score = metadata_updates["importance_score"]

    async def update(self, entry_id: str, metadata_updates: dict | None = None, content: str | None = None):
        if metadata_updates:
            self.entries[entry_id].importance_score = metadata_updates["importance_score"]

    async def delete(self, entry_id: str):
        self.deleted.append(entry_id)
        self.entries.pop(entry_id, None)


class TestApplyFeedback:
    async def test_thumbs_up_increases_importance(self):
        kb = _FakeKB()
        result = await apply_feedback(kb, "thumbs_up", ["e1"])
        assert result["adjusted"] == 1
        assert kb.entries["e1"].importance_score == pytest.approx(0.6)

    async def test_thumbs_up_capped_at_one(self):
        kb = _FakeKB()
        kb.entries["e1"].importance_score = 0.95
        await apply_feedback(kb, "thumbs_up", ["e1"])
        assert kb.entries["e1"].importance_score == 1.0

    async def test_thumbs_down_decreases_importance(self):
        kb = _FakeKB()
        result = await apply_feedback(kb, "thumbs_down", ["e1"])
        assert result["adjusted"] == 1
        assert kb.entries["e1"].importance_score == pytest.approx(0.3)

    async def test_thumbs_down_to_zero_deletes(self):
        kb = _FakeKB()
        # e2 importance=0.1，-0.2 → 0.0 → 删除
        result = await apply_feedback(kb, "thumbs_down", ["e2"])
        assert result["deleted"] == 1
        assert "e2" in kb.deleted
        assert "e2" not in kb.entries

    async def test_no_entry_ids_is_noop(self):
        kb = _FakeKB()
        result = await apply_feedback(kb, "thumbs_up", [])
        assert result == {"adjusted": 0, "deleted": 0}

    async def test_none_kb_is_noop(self):
        result = await apply_feedback(None, "thumbs_up", ["e1"])
        assert result == {"adjusted": 0, "deleted": 0}

    async def test_unknown_rating_is_noop(self):
        kb = _FakeKB()
        result = await apply_feedback(kb, "neutral", ["e1"])
        assert result == {"adjusted": 0, "deleted": 0}

    async def test_missing_entry_skipped(self):
        kb = _FakeKB()
        result = await apply_feedback(kb, "thumbs_up", ["missing", "e1"])
        assert result["adjusted"] == 1
