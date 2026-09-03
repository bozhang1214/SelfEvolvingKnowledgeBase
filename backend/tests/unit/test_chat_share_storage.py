"""
聊天会话分享存储与数据模型的单元测试

测试内容：
- SharedConversation.is_valid（启用/停用/过期）
- SharedConversation 消息快照默认值
- ChatShareStorage 分享记录 CRUD（创建/获取/列表/删除）
- 消息快照随记录持久化，重建实例后可恢复
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.chat_share import SharedConversation
from app.storage.chat_share_storage import ChatShareStorage


class TestSharedConversationModel:
    def test_default_valid(self):
        share = SharedConversation(owner_user_id="u1", conv_id="c1")
        assert share.share_id
        assert share.permission == "read_only"
        assert share.is_active is True
        assert share.expires_at is None
        assert share.messages == []
        assert share.is_valid() is True

    def test_inactive_invalid(self):
        share = SharedConversation(owner_user_id="u1", conv_id="c1", is_active=False)
        assert share.is_valid() is False

    def test_expired_invalid(self):
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        share = SharedConversation(owner_user_id="u1", conv_id="c1", expires_at=past)
        assert share.is_valid() is False

    def test_share_id_url_safe(self):
        for _ in range(100):
            sid = SharedConversation(owner_user_id="u1", conv_id="c1").share_id
            assert "/" not in sid
            assert "+" not in sid
            assert "=" not in sid


@pytest.fixture
def chat_share_storage(tmp_path):
    return ChatShareStorage(tmp_path)


class TestChatShareStorage:
    @pytest.mark.asyncio
    async def test_create_and_get_share(self, chat_share_storage):
        share = await chat_share_storage.create_share(
            owner_user_id="owner-1",
            conv_id="conv-1",
            title="测试会话",
            messages=[{"role": "user", "content": "你好", "created_at": "2024-01-01T00:00:00Z"}],
        )
        assert share.title == "测试会话"
        assert len(share.messages) == 1

        fetched = await chat_share_storage.get_share(share.share_id)
        assert fetched is not None
        assert fetched.owner_user_id == "owner-1"
        assert fetched.messages[0]["content"] == "你好"

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_none(self, chat_share_storage):
        assert await chat_share_storage.get_share("no-such-id") is None

    @pytest.mark.asyncio
    async def test_list_by_owner_filters(self, chat_share_storage):
        await chat_share_storage.create_share("alice", "c1", "A1")
        await chat_share_storage.create_share("alice", "c2", "A2")
        await chat_share_storage.create_share("bob", "c3", "B1")

        assert len(await chat_share_storage.list_by_owner("alice")) == 2
        assert len(await chat_share_storage.list_by_owner("bob")) == 1

    @pytest.mark.asyncio
    async def test_delete_share(self, chat_share_storage):
        created = await chat_share_storage.create_share("owner-1", "c1")
        assert await chat_share_storage.delete_share(created.share_id) is True
        assert await chat_share_storage.get_share(created.share_id) is None

    @pytest.mark.asyncio
    async def test_persistence_reload(self, tmp_path):
        storage1 = ChatShareStorage(tmp_path)
        created = await storage1.create_share(
            owner_user_id="owner-1",
            conv_id="c1",
            title="持久化",
            messages=[{"role": "assistant", "content": "回答", "created_at": "2024-01-01T00:00:00Z"}],
        )

        storage2 = ChatShareStorage(tmp_path)
        fetched = await storage2.get_share(created.share_id)
        assert fetched is not None
        assert fetched.title == "持久化"
        assert fetched.messages[0]["role"] == "assistant"
