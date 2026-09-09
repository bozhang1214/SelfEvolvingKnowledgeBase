"""
存储与记忆集成的测试

测试内容：
- 创建会话 + 添加消息到存储 + 加载到记忆
- 会话切换时记忆正确切换
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.memory.short_term import ShortTermMemory
from app.storage.json_storage import JSONStorage

# ============================================================
# 测试夹具
# ============================================================

@pytest.fixture
def storage(tmp_path):
    """创建使用临时目录的 JSONStorage"""
    return JSONStorage(
        index_file=tmp_path / "index.json",
        conversations_dir=tmp_path / "conversations",
    )


@pytest.fixture
def memory(sample_config):
    """创建 ShortTermMemory 实例"""
    return ShortTermMemory(sample_config.memory.l1_working)


def _make_message(role: str, content: str) -> dict:
    """构造存储用消息字典"""
    return {"role": role, "content": content}


# ============================================================
# 存储与记忆集成测试
# ============================================================

class TestStorageMemoryIntegration:
    """测试存储后端与短期记忆的集成"""

    @pytest.mark.asyncio
    async def test_create_conversation_and_load_into_memory(self, storage, memory):
        """测试创建会话、添加消息到存储后加载到记忆"""
        # 1. 在存储中创建会话
        conv_id = await storage.create_conversation("default", "测试会话")

        # 2. 向存储追加消息
        await storage.append_message(conv_id, _make_message("user", "你好"))
        await storage.append_message(conv_id, _make_message("assistant", "你好啊"))

        # 3. 从存储读取消息并加载到记忆
        stored_messages = await storage.get_messages(conv_id)
        for msg in stored_messages:
            if msg["role"] == "user":
                await memory.add_message("default", conv_id, HumanMessage(content=msg["content"]))
            elif msg["role"] == "assistant":
                await memory.add_message("default", conv_id, AIMessage(content=msg["content"]))

        # 4. 验证记忆中的消息
        memory_messages = await memory.get_messages("default", conv_id)
        assert len(memory_messages) == 2
        assert memory_messages[0].content == "你好"
        assert memory_messages[1].content == "你好啊"

    @pytest.mark.asyncio
    async def test_conversation_switching(self, storage, memory):
        """测试会话切换时记忆正确隔离"""
        # 创建两个会话
        conv_a = await storage.create_conversation("default", "会话A")
        conv_b = await storage.create_conversation("default", "会话B")

        # 分别添加消息
        await storage.append_message(conv_a, _make_message("user", "A的第一条"))
        await storage.append_message(conv_b, _make_message("user", "B的第一条"))

        # 加载会话 A 到记忆
        msgs_a = await storage.get_messages(conv_a)
        for msg in msgs_a:
            await memory.add_message("default", conv_a, HumanMessage(content=msg["content"]))

        # 加载会话 B 到记忆
        msgs_b = await storage.get_messages(conv_b)
        for msg in msgs_b:
            await memory.add_message("default", conv_b, HumanMessage(content=msg["content"]))

        # 验证两个会话的记忆相互隔离
        memory_a = await memory.get_messages("default", conv_a)
        memory_b = await memory.get_messages("default", conv_b)

        assert len(memory_a) == 1
        assert len(memory_b) == 1
        assert memory_a[0].content == "A的第一条"
        assert memory_b[0].content == "B的第一条"

    @pytest.mark.asyncio
    async def test_storage_stats_and_memory_consistency(self, storage, memory):
        """测试存储统计与记忆消息数一致"""
        conv_id = await storage.create_conversation("default", "统计测试")

        # 添加 4 条消息（2 轮对话）
        messages = [
            ("user", "问题1"),
            ("assistant", "回答1"),
            ("user", "问题2"),
            ("assistant", "回答2"),
        ]
        for role, content in messages:
            await storage.append_message(conv_id, _make_message(role, content))
            lc_msg = HumanMessage(content=content) if role == "user" else AIMessage(content=content)
            await memory.add_message("default", conv_id, lc_msg)

        # 存储统计
        meta = await storage.get_conversation(conv_id)
        assert meta["message_count"] == 4

        # 记忆消息数
        memory_msgs = await memory.get_messages("default", conv_id)
        assert len(memory_msgs) == 4

    @pytest.mark.asyncio
    async def test_delete_conversation_then_clear_memory(self, storage, memory):
        """测试删除会话后清除对应记忆"""
        conv_id = await storage.create_conversation("default", "待删除")
        await storage.append_message(conv_id, _make_message("user", "消息"))
        await memory.add_message("default", conv_id, HumanMessage(content="消息"))

        # 确认记忆中有消息
        assert len(await memory.get_messages("default", conv_id)) == 1

        # 删除会话（存储软删除）
        await storage.delete_conversation(conv_id)
        # 清除记忆
        await memory.clear("default", conv_id)

        # 验证
        assert len(await memory.get_messages("default", conv_id)) == 0
        meta = await storage.get_conversation(conv_id)
        assert meta["status"] == "deleted"

    @pytest.mark.asyncio
    async def test_reload_messages_after_restart(self, storage, sample_config):
        """测试模拟重启后从存储重新加载消息到新的记忆实例"""
        conv_id = await storage.create_conversation("default", "重启测试")
        await storage.append_message(conv_id, _make_message("user", "重启前的消息"))
        await storage.append_message(conv_id, _make_message("assistant", "重启前的回复"))

        # 模拟重启：创建新的记忆实例
        new_memory = ShortTermMemory(sample_config.memory.l1_working)

        # 从存储重新加载
        stored_messages = await storage.get_messages(conv_id)
        for msg in stored_messages:
            if msg["role"] == "user":
                await new_memory.add_message("default", conv_id, HumanMessage(content=msg["content"]))
            else:
                await new_memory.add_message("default", conv_id, AIMessage(content=msg["content"]))

        # 验证新记忆实例中有正确消息
        memory_msgs = await new_memory.get_messages("default", conv_id)
        assert len(memory_msgs) == 2
        assert memory_msgs[0].content == "重启前的消息"
        assert memory_msgs[1].content == "重启前的回复"

    @pytest.mark.asyncio
    async def test_context_built_from_storage_messages(self, storage, memory):
        """测试从存储加载的消息构建上下文"""
        conv_id = await storage.create_conversation("default", "上下文测试")
        await storage.append_message(conv_id, _make_message("user", "什么是LangGraph？"))
        await storage.append_message(conv_id, _make_message("assistant", "LangGraph是一个工作流框架。"))

        # 加载到记忆
        stored_messages = await storage.get_messages(conv_id)
        for msg in stored_messages:
            if msg["role"] == "user":
                await memory.add_message("default", conv_id, HumanMessage(content=msg["content"]))
            else:
                await memory.add_message("default", conv_id, AIMessage(content=msg["content"]))

        # 构建上下文
        context = await memory.get_context("default", conv_id, max_tokens=1000)
        assert "什么是LangGraph" in context
        assert "工作流框架" in context
        assert "user:" in context
        assert "assistant:" in context
