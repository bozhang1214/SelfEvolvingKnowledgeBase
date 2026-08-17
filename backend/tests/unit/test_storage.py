"""
JSON 存储的单元测试

测试内容：
- 创建会话 → 返回 conv_id
- 获取会话 → 返回正确元信息
- 列出会话 → 按用户过滤
- 追加消息 → 消息计数递增
- 获取消息 → 按时间排序
- 删除会话 → 软删除
- 更新会话统计
- 原子写入（数据一致性）
"""

from __future__ import annotations

import json

import pytest

from app.core.exceptions import StorageError
from app.storage.json_storage import JSONStorage


# ============================================================
# 测试夹具
# ============================================================

@pytest.fixture
def storage(tmp_path):
    """创建一个使用临时目录的 JSONStorage 实例"""
    index_file = tmp_path / "index.json"
    conversations_dir = tmp_path / "conversations"
    return JSONStorage(index_file=index_file, conversations_dir=conversations_dir)


def _make_message(role: str, content: str, **kwargs) -> dict:
    """构造测试用消息字典"""
    msg = {"role": role, "content": content}
    msg.update(kwargs)
    return msg


# ============================================================
# 创建会话测试
# ============================================================

class TestCreateConversation:
    """测试 create_conversation 方法"""

    @pytest.mark.asyncio
    async def test_create_returns_conv_id(self, storage):
        """测试创建会话返回非空的 conv_id"""
        conv_id = await storage.create_conversation("default", "测试会话")
        assert conv_id is not None
        assert isinstance(conv_id, str)
        assert len(conv_id) > 0

    @pytest.mark.asyncio
    async def test_create_conv_id_is_uuid_format(self, storage):
        """测试 conv_id 是 UUID 格式"""
        conv_id = await storage.create_conversation("default", "测试")
        parts = conv_id.split("-")
        assert len(parts) == 5

    @pytest.mark.asyncio
    async def test_create_multiple_unique_ids(self, storage):
        """测试多次创建会话返回不同的 conv_id"""
        id1 = await storage.create_conversation("default", "会话1")
        id2 = await storage.create_conversation("default", "会话2")
        assert id1 != id2


# ============================================================
# 获取会话测试
# ============================================================

class TestGetConversation:
    """测试 get_conversation 方法"""

    @pytest.mark.asyncio
    async def test_get_returns_correct_meta(self, storage):
        """测试获取会话返回正确的元信息"""
        conv_id = await storage.create_conversation("alice", "我的会话")
        meta = await storage.get_conversation(conv_id)

        assert meta is not None
        assert meta["conv_id"] == conv_id
        assert meta["user_id"] == "alice"
        assert meta["title"] == "我的会话"
        assert meta["status"] == "active"

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_none(self, storage):
        """测试获取不存在的会话返回 None"""
        result = await storage.get_conversation("nonexistent-conv-id")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_has_timestamps(self, storage):
        """测试会话元信息包含创建与更新时间戳"""
        conv_id = await storage.create_conversation("default", "测试")
        meta = await storage.get_conversation(conv_id)
        assert "created_at" in meta
        assert "updated_at" in meta
        assert len(meta["created_at"]) > 0
        assert len(meta["updated_at"]) > 0


# ============================================================
# 列出会话测试
# ============================================================

class TestListConversations:
    """测试 list_conversations 方法"""

    @pytest.mark.asyncio
    async def test_list_filters_by_user(self, storage):
        """测试按用户过滤会话"""
        await storage.create_conversation("alice", "Alice 的会话")
        await storage.create_conversation("bob", "Bob 的会话")
        await storage.create_conversation("alice", "Alice 的另一个会话")

        alice_convs = await storage.list_conversations("alice")
        bob_convs = await storage.list_conversations("bob")

        assert len(alice_convs) == 2
        assert len(bob_convs) == 1
        for conv in alice_convs:
            assert conv["user_id"] == "alice"
        assert bob_convs[0]["user_id"] == "bob"

    @pytest.mark.asyncio
    async def test_list_excludes_deleted(self, storage):
        """测试列出会话时排除已删除的"""
        conv1 = await storage.create_conversation("alice", "保留的")
        conv2 = await storage.create_conversation("alice", "删除的")

        await storage.delete_conversation(conv2)

        convs = await storage.list_conversations("alice")
        assert len(convs) == 1
        assert convs[0]["conv_id"] == conv1

    @pytest.mark.asyncio
    async def test_list_sorted_by_updated_at_desc(self, storage):
        """测试会话按 updated_at 倒序排列"""
        conv1 = await storage.create_conversation("alice", "第一个")
        # 通过追加消息更新 conv1 的 updated_at
        await storage.append_message(conv1, _make_message("user", "hello"))
        conv2 = await storage.create_conversation("alice", "第二个")

        convs = await storage.list_conversations("alice")
        # conv2 最后被创建/更新，应排在前面
        assert convs[0]["conv_id"] == conv2

    @pytest.mark.asyncio
    async def test_list_pagination(self, storage):
        """测试分页参数"""
        for i in range(5):
            await storage.create_conversation("alice", f"会话{i}")

        page1 = await storage.list_conversations("alice", limit=2, offset=0)
        page2 = await storage.list_conversations("alice", limit=2, offset=2)

        assert len(page1) == 2
        assert len(page2) == 2

    @pytest.mark.asyncio
    async def test_list_empty_user(self, storage):
        """测试列出无会话用户的空结果"""
        convs = await storage.list_conversations("nobody")
        assert convs == []


# ============================================================
# 追加消息测试
# ============================================================

class TestAppendMessage:
    """测试 append_message 方法"""

    @pytest.mark.asyncio
    async def test_append_returns_msg_id(self, storage):
        """测试追加消息返回 msg_id"""
        conv_id = await storage.create_conversation("default", "测试")
        msg_id = await storage.append_message(conv_id, _make_message("user", "你好"))
        assert msg_id is not None
        assert isinstance(msg_id, str)
        assert len(msg_id) > 0

    @pytest.mark.asyncio
    async def test_append_increments_message_count(self, storage):
        """测试追加消息后 message_count 递增"""
        conv_id = await storage.create_conversation("default", "测试")

        await storage.append_message(conv_id, _make_message("user", "第一条"))
        meta = await storage.get_conversation(conv_id)
        assert meta["message_count"] == 1

        await storage.append_message(conv_id, _make_message("assistant", "回复"))
        meta = await storage.get_conversation(conv_id)
        assert meta["message_count"] == 2

    @pytest.mark.asyncio
    async def test_append_accumulates_tokens(self, storage):
        """测试追加消息时 token 统计累加"""
        conv_id = await storage.create_conversation("default", "测试")

        await storage.append_message(conv_id, _make_message(
            "user", "你好", tokens_input=10, tokens_output=0, cost_usd=0.001,
        ))
        await storage.append_message(conv_id, _make_message(
            "assistant", "回复", tokens_input=0, tokens_output=20, cost_usd=0.002,
        ))

        meta = await storage.get_conversation(conv_id)
        assert meta["total_input_tokens"] == 10
        assert meta["total_output_tokens"] == 20
        assert meta["total_cost_usd"] == pytest.approx(0.003)

    @pytest.mark.asyncio
    async def test_append_to_nonexistent_raises(self, storage):
        """测试向不存在的会话追加消息抛出 StorageError"""
        with pytest.raises(StorageError):
            await storage.append_message("nonexistent", _make_message("user", "测试"))


# ============================================================
# 获取消息测试
# ============================================================

class TestGetMessages:
    """测试 get_messages 方法"""

    @pytest.mark.asyncio
    async def test_get_messages_chronological_order(self, storage):
        """测试获取消息按时间正序排列"""
        conv_id = await storage.create_conversation("default", "测试")
        await storage.append_message(conv_id, _make_message("user", "第一条"))
        await storage.append_message(conv_id, _make_message("assistant", "第二条"))
        await storage.append_message(conv_id, _make_message("user", "第三条"))

        messages = await storage.get_messages(conv_id)
        assert len(messages) == 3
        assert messages[0]["content"] == "第一条"
        assert messages[1]["content"] == "第二条"
        assert messages[2]["content"] == "第三条"

    @pytest.mark.asyncio
    async def test_get_messages_limit(self, storage):
        """测试 limit 参数限制返回消息数"""
        conv_id = await storage.create_conversation("default", "测试")
        for i in range(5):
            await storage.append_message(conv_id, _make_message("user", f"消息{i}"))

        messages = await storage.get_messages(conv_id, limit=3)
        assert len(messages) == 3
        # 应返回最后 3 条
        assert messages[0]["content"] == "消息2"
        assert messages[2]["content"] == "消息4"

    @pytest.mark.asyncio
    async def test_get_messages_empty(self, storage):
        """测试获取空会话的消息返回空列表"""
        conv_id = await storage.create_conversation("default", "测试")
        messages = await storage.get_messages(conv_id)
        assert messages == []

    @pytest.mark.asyncio
    async def test_get_messages_nonexistent_returns_empty(self, storage):
        """测试获取不存在的会话消息返回空列表"""
        messages = await storage.get_messages("nonexistent")
        assert messages == []


# ============================================================
# 删除会话测试
# ============================================================

class TestDeleteConversation:
    """测试 delete_conversation 方法"""

    @pytest.mark.asyncio
    async def test_delete_soft_delete(self, storage):
        """测试删除会话是软删除（标记 status=deleted）"""
        conv_id = await storage.create_conversation("default", "测试")
        await storage.delete_conversation(conv_id)

        # 直接读取索引文件验证 status 字段
        meta = await storage.get_conversation(conv_id)
        assert meta is not None
        assert meta["status"] == "deleted"

    @pytest.mark.asyncio
    async def test_delete_removes_message_file(self, storage, tmp_path):
        """测试删除会话后消息文件被物理删除"""
        conv_id = await storage.create_conversation("default", "测试")
        await storage.append_message(conv_id, _make_message("user", "消息"))

        # 确认消息文件存在
        msg_file = tmp_path / "conversations" / f"{conv_id}.json"
        assert msg_file.exists()

        await storage.delete_conversation(conv_id)
        assert not msg_file.exists()

    @pytest.mark.asyncio
    async def test_delete_nonexistent_raises(self, storage):
        """测试删除不存在的会话抛出 StorageError"""
        with pytest.raises(StorageError):
            await storage.delete_conversation("nonexistent-conv-id")


# ============================================================
# 更新会话测试
# ============================================================

class TestUpdateConversation:
    """测试 update_conversation 方法"""

    @pytest.mark.asyncio
    async def test_update_title(self, storage):
        """测试更新会话标题"""
        conv_id = await storage.create_conversation("default", "旧标题")
        await storage.update_conversation(conv_id, {"title": "新标题"})

        meta = await storage.get_conversation(conv_id)
        assert meta["title"] == "新标题"

    @pytest.mark.asyncio
    async def test_update_status(self, storage):
        """测试更新会话状态"""
        conv_id = await storage.create_conversation("default", "测试")
        await storage.update_conversation(conv_id, {"status": "archived"})

        meta = await storage.get_conversation(conv_id)
        assert meta["status"] == "archived"

    @pytest.mark.asyncio
    async def test_update_nonexistent_raises(self, storage):
        """测试更新不存在的会话抛出 StorageError"""
        with pytest.raises(StorageError):
            await storage.update_conversation("nonexistent", {"title": "x"})


# ============================================================
# 更新会话统计测试
# ============================================================

class TestUpdateConversationStats:
    """测试 update_conversation_stats 方法"""

    @pytest.mark.asyncio
    async def test_update_stats(self, storage):
        """测试更新会话统计信息"""
        conv_id = await storage.create_conversation("default", "测试")
        await storage.update_conversation_stats(conv_id, {
            "total_input_tokens": 500,
            "total_output_tokens": 300,
            "total_cost_usd": 0.05,
        })

        meta = await storage.get_conversation(conv_id)
        assert meta["total_input_tokens"] == 500
        assert meta["total_output_tokens"] == 300
        assert meta["total_cost_usd"] == pytest.approx(0.05)

    @pytest.mark.asyncio
    async def test_update_stats_nonexistent_raises(self, storage):
        """测试更新不存在的会话统计抛出 StorageError"""
        with pytest.raises(StorageError):
            await storage.update_conversation_stats("nonexistent", {"x": 1})


# ============================================================
# 原子写入测试
# ============================================================

class TestAtomicWrite:
    """测试原子写入（数据一致性）"""

    @pytest.mark.asyncio
    async def test_no_tmp_file_left_after_write(self, storage, tmp_path):
        """测试写入后不残留临时文件"""
        conv_id = await storage.create_conversation("default", "测试")
        await storage.append_message(conv_id, _make_message("user", "消息"))

        # 检查 conversations 目录下没有 .tmp 文件
        conv_dir = tmp_path / "conversations"
        tmp_files = list(conv_dir.glob("*.tmp"))
        assert len(tmp_files) == 0

    @pytest.mark.asyncio
    async def test_index_no_tmp_file_after_write(self, storage, tmp_path):
        """测试索引文件写入后不残留临时文件"""
        await storage.create_conversation("default", "测试")
        await storage.create_conversation("default", "测试2")

        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0

    @pytest.mark.asyncio
    async def test_data_consistent_after_multiple_writes(self, storage):
        """测试多次写入后数据一致"""
        conv_id = await storage.create_conversation("default", "测试")

        # 连续写入 10 条消息
        for i in range(10):
            await storage.append_message(conv_id, _make_message("user", f"消息{i}"))

        messages = await storage.get_messages(conv_id)
        assert len(messages) == 10

        meta = await storage.get_conversation(conv_id)
        assert meta["message_count"] == 10

        # 验证消息顺序正确
        for i, msg in enumerate(messages):
            assert msg["content"] == f"消息{i}"

    @pytest.mark.asyncio
    async def test_index_file_valid_json(self, storage, tmp_path):
        """测试索引文件是合法 JSON"""
        await storage.create_conversation("default", "测试1")
        await storage.create_conversation("default", "测试2")

        index_path = tmp_path / "index.json"
        with index_path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        assert isinstance(data, list)
        assert len(data) == 2
