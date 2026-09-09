"""
知识库分享存储与数据模型的单元测试

测试内容：
- SharedKnowledge.is_valid（启用/停用/过期）
- generate_share_id（唯一、URL 安全）
- ShareStorage 分享记录 CRUD（创建/获取/列表/删除/启停）
- ShareStorage 分享会话（消息追加/读取/按 viewer 隔离）
- 持久化：重建实例后数据可恢复
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.share import SharedKnowledge, generate_share_id
from app.storage.share_storage import ShareStorage

# ============================================================
# SharedKnowledge 数据模型
# ============================================================

class TestSharedKnowledgeModel:
    def test_default_valid(self):
        share = SharedKnowledge(owner_user_id="u1")
        assert share.share_id
        assert share.permission == "read_chat"
        assert share.is_active is True
        assert share.expires_at is None
        assert share.is_valid() is True

    def test_inactive_invalid(self):
        share = SharedKnowledge(owner_user_id="u1", is_active=False)
        assert share.is_valid() is False

    def test_expired_invalid(self):
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        share = SharedKnowledge(owner_user_id="u1", expires_at=past)
        assert share.is_valid() is False

    def test_not_yet_expired_valid(self):
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        share = SharedKnowledge(owner_user_id="u1", expires_at=future)
        assert share.is_valid() is True


class TestGenerateShareId:
    def test_url_safe(self):
        for _ in range(100):
            sid = generate_share_id()
            assert sid
            # URL 安全字符：不含 /、+、=
            assert "/" not in sid
            assert "+" not in sid
            assert "=" not in sid

    def test_unique(self):
        ids = {generate_share_id() for _ in range(100)}
        assert len(ids) == 100


# ============================================================
# ShareStorage
# ============================================================

@pytest.fixture
def share_storage(tmp_path):
    return ShareStorage(tmp_path)


class TestShareCRUD:
    @pytest.mark.asyncio
    async def test_create_share(self, share_storage):
        share = await share_storage.create_share(owner_user_id="owner-1", title="我的知识库")
        assert share.share_id
        assert share.owner_user_id == "owner-1"
        assert share.title == "我的知识库"
        assert share.is_active is True

    @pytest.mark.asyncio
    async def test_create_default_title(self, share_storage):
        share = await share_storage.create_share(owner_user_id="owner-1")
        assert share.title == "我的知识库"

    @pytest.mark.asyncio
    async def test_get_share(self, share_storage):
        created = await share_storage.create_share(owner_user_id="owner-1", title="T")
        fetched = await share_storage.get_share(created.share_id)
        assert fetched is not None
        assert fetched.share_id == created.share_id
        assert fetched.owner_user_id == "owner-1"

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_none(self, share_storage):
        assert await share_storage.get_share("no-such-id") is None

    @pytest.mark.asyncio
    async def test_list_by_owner_filters(self, share_storage):
        await share_storage.create_share(owner_user_id="alice", title="A1")
        await share_storage.create_share(owner_user_id="alice", title="A2")
        await share_storage.create_share(owner_user_id="bob", title="B1")

        alice = await share_storage.list_by_owner("alice")
        bob = await share_storage.list_by_owner("bob")
        assert len(alice) == 2
        assert len(bob) == 1
        assert all(s.owner_user_id == "alice" for s in alice)

    @pytest.mark.asyncio
    async def test_delete_share(self, share_storage):
        created = await share_storage.create_share(owner_user_id="owner-1")
        deleted = await share_storage.delete_share(created.share_id)
        assert deleted is True
        assert await share_storage.get_share(created.share_id) is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_false(self, share_storage):
        assert await share_storage.delete_share("no-such-id") is False

    @pytest.mark.asyncio
    async def test_set_active(self, share_storage):
        created = await share_storage.create_share(owner_user_id="owner-1")
        assert await share_storage.set_active(created.share_id, False) is True
        fetched = await share_storage.get_share(created.share_id)
        assert fetched.is_active is False


class TestShareConversation:
    @pytest.mark.asyncio
    async def test_messages_empty_initially(self, share_storage):
        share = await share_storage.create_share(owner_user_id="owner-1")
        assert await share_storage.get_messages(share.share_id, "viewer-1") == []

    @pytest.mark.asyncio
    async def test_append_and_read_messages(self, share_storage):
        share = await share_storage.create_share(owner_user_id="owner-1")
        await share_storage.append_message(share.share_id, "viewer-1", {"role": "user", "content": "你好"})
        await share_storage.append_message(share.share_id, "viewer-1", {"role": "assistant", "content": "你好！"})

        msgs = await share_storage.get_messages(share.share_id, "viewer-1")
        assert len(msgs) == 2
        assert msgs[0]["content"] == "你好"
        assert msgs[1]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_messages_isolated_by_viewer(self, share_storage):
        share = await share_storage.create_share(owner_user_id="owner-1")
        await share_storage.append_message(share.share_id, "viewer-1", {"role": "user", "content": "A"})
        await share_storage.append_message(share.share_id, "viewer-2", {"role": "user", "content": "B"})

        assert len(await share_storage.get_messages(share.share_id, "viewer-1")) == 1
        assert len(await share_storage.get_messages(share.share_id, "viewer-2")) == 1


class TestSharePersistence:
    @pytest.mark.asyncio
    async def test_reload_recovers_shares(self, tmp_path):
        storage1 = ShareStorage(tmp_path)
        created = await storage1.create_share(owner_user_id="owner-1", title="持久化")
        await storage1.append_message(created.share_id, "viewer-1", {"role": "user", "content": "hi"})

        # 重建实例，模拟进程重启
        storage2 = ShareStorage(tmp_path)
        fetched = await storage2.get_share(created.share_id)
        assert fetched is not None
        assert fetched.title == "持久化"
        msgs = await storage2.get_messages(created.share_id, "viewer-1")
        assert len(msgs) == 1
        assert msgs[0]["content"] == "hi"
