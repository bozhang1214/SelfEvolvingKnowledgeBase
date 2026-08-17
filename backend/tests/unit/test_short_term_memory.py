"""
L1 短期记忆的单元测试

测试内容：
- 添加消息 → 列表增长
- 滑动窗口 → 超过 max_turns 时丢弃旧消息
- Token 计数 → 正确统计
- 获取上下文 → 返回格式化字符串
- 清除记忆
- 压缩（降级模式）
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.memory.short_term import ShortTermMemory


# ============================================================
# 添加消息测试
# ============================================================

class TestAddMessage:
    """测试 add_message 方法"""

    @pytest.mark.asyncio
    async def test_add_message_grows_list(self, sample_config):
        """测试添加消息后消息列表增长"""
        memory = ShortTermMemory(sample_config.memory.l1_working)
        conv_id = "test-conv-add"

        await memory.add_message("default", conv_id, HumanMessage(content="你好"))
        messages = await memory.get_messages("default", conv_id)
        assert len(messages) == 1

        await memory.add_message("default", conv_id, AIMessage(content="你好啊"))
        messages = await memory.get_messages("default", conv_id)
        assert len(messages) == 2

    @pytest.mark.asyncio
    async def test_add_multiple_messages(self, sample_config):
        """测试添加多条消息"""
        memory = ShortTermMemory(sample_config.memory.l1_working)
        conv_id = "test-conv-multi"

        for i in range(5):
            await memory.add_message("default", conv_id, HumanMessage(content=f"用户{i}"))
            await memory.add_message("default", conv_id, AIMessage(content=f"助手{i}"))

        messages = await memory.get_messages("default", conv_id)
        assert len(messages) == 10

    @pytest.mark.asyncio
    async def test_add_message_preserves_content(self, sample_config):
        """测试添加消息后内容正确保留"""
        memory = ShortTermMemory(sample_config.memory.l1_working)
        conv_id = "test-conv-content"

        await memory.add_message("default", conv_id, HumanMessage(content="测试内容123"))
        messages = await memory.get_messages("default", conv_id)
        assert messages[0].content == "测试内容123"

    @pytest.mark.asyncio
    async def test_add_message_to_different_conversations(self, sample_config):
        """测试不同会话的消息相互隔离"""
        memory = ShortTermMemory(sample_config.memory.l1_working)

        await memory.add_message("default", "conv-a", HumanMessage(content="A的消息"))
        await memory.add_message("default", "conv-b", HumanMessage(content="B的消息"))

        msgs_a = await memory.get_messages("default", "conv-a")
        msgs_b = await memory.get_messages("default", "conv-b")

        assert len(msgs_a) == 1
        assert len(msgs_b) == 1
        assert msgs_a[0].content == "A的消息"
        assert msgs_b[0].content == "B的消息"


# ============================================================
# 滑动窗口测试
# ============================================================

class TestSlidingWindow:
    """测试滑动窗口机制"""

    @pytest.mark.asyncio
    async def test_sliding_window_drops_old_messages(self, small_l1_config):
        """测试超过 max_turns*2 时丢弃旧消息"""
        # max_turns=2，最多保留 4 条
        memory = ShortTermMemory(small_l1_config)
        conv_id = "test-conv-slide"

        # 添加 6 条消息（3 轮）
        for i in range(3):
            await memory.add_message("default", conv_id, HumanMessage(content=f"用户{i}"))
            await memory.add_message("default", conv_id, AIMessage(content=f"助手{i}"))

        messages = await memory.get_messages("default", conv_id)
        # 滑动窗口只保留最近 4 条
        assert len(messages) == 4
        # 最旧的 2 条被丢弃
        assert messages[0].content == "用户1"
        assert messages[1].content == "助手1"
        assert messages[2].content == "用户2"
        assert messages[3].content == "助手2"

    @pytest.mark.asyncio
    async def test_sliding_window_exact_limit(self, small_l1_config):
        """测试消息数恰好等于窗口大小时不丢弃"""
        # max_turns=2，窗口大小=4
        memory = ShortTermMemory(small_l1_config)
        conv_id = "test-conv-exact"

        for i in range(2):
            await memory.add_message("default", conv_id, HumanMessage(content=f"用户{i}"))
            await memory.add_message("default", conv_id, AIMessage(content=f"助手{i}"))

        messages = await memory.get_messages("default", conv_id)
        assert len(messages) == 4

    @pytest.mark.asyncio
    async def test_sliding_window_under_limit(self, small_l1_config):
        """测试消息数小于窗口大小时全部保留"""
        memory = ShortTermMemory(small_l1_config)
        conv_id = "test-conv-under"

        await memory.add_message("default", conv_id, HumanMessage(content="单条"))
        messages = await memory.get_messages("default", conv_id)
        assert len(messages) == 1

    @pytest.mark.asyncio
    async def test_sliding_window_returns_copy(self, small_l1_config):
        """测试 get_messages 返回的是副本，修改不影响内部状态"""
        memory = ShortTermMemory(small_l1_config)
        conv_id = "test-conv-copy"

        await memory.add_message("default", conv_id, HumanMessage(content="原始"))
        messages1 = await memory.get_messages("default", conv_id)
        messages1.clear()

        messages2 = await memory.get_messages("default", conv_id)
        assert len(messages2) == 1


# ============================================================
# Token 计数测试
# ============================================================

class TestTokenCounting:
    """测试 Token 计数功能"""

    def test_count_text_tokens_positive(self, sample_config):
        """测试非空文本的 token 数大于 0"""
        memory = ShortTermMemory(sample_config.memory.l1_working)
        tokens = memory._count_text_tokens("Hello, world!")
        assert tokens > 0

    def test_count_text_tokens_empty(self, sample_config):
        """测试空文本的 token 数为 0"""
        memory = ShortTermMemory(sample_config.memory.l1_working)
        tokens = memory._count_text_tokens("")
        assert tokens == 0

    def test_count_text_tokens_chinese(self, sample_config):
        """测试中文文本的 token 数大于 0"""
        memory = ShortTermMemory(sample_config.memory.l1_working)
        tokens = memory._count_text_tokens("你好世界，这是一个测试。")
        assert tokens > 0

    def test_count_messages_tokens(self, sample_config):
        """测试消息列表的 token 总数"""
        memory = ShortTermMemory(sample_config.memory.l1_working)
        messages = [
            HumanMessage(content="你好"),
            AIMessage(content="你好啊"),
        ]
        total = memory._count_messages_tokens(messages)
        assert total > 0

    def test_count_more_text_more_tokens(self, sample_config):
        """测试更长的文本产生更多 token"""
        memory = ShortTermMemory(sample_config.memory.l1_working)
        short_tokens = memory._count_text_tokens("hi")
        long_tokens = memory._count_text_tokens("This is a much longer text with many words.")
        assert long_tokens > short_tokens


# ============================================================
# 获取上下文测试
# ============================================================

class TestGetContext:
    """测试 get_context 方法"""

    @pytest.mark.asyncio
    async def test_context_contains_messages(self, sample_config):
        """测试上下文包含消息内容"""
        memory = ShortTermMemory(sample_config.memory.l1_working)
        conv_id = "test-conv-ctx"

        await memory.add_message("default", conv_id, HumanMessage(content="用户问题"))
        await memory.add_message("default", conv_id, AIMessage(content="助手回答"))

        context = await memory.get_context("default", conv_id, max_tokens=1000)
        assert "用户问题" in context
        assert "助手回答" in context

    @pytest.mark.asyncio
    async def test_context_formats_role(self, sample_config):
        """测试上下文中消息格式为 'role: content'"""
        memory = ShortTermMemory(sample_config.memory.l1_working)
        conv_id = "test-conv-fmt"

        await memory.add_message("default", conv_id, HumanMessage(content="测试消息"))
        context = await memory.get_context("default", conv_id, max_tokens=1000)
        assert "user: 测试消息" in context

    @pytest.mark.asyncio
    async def test_context_includes_summaries(self, sample_config):
        """测试上下文包含压缩摘要"""
        memory = ShortTermMemory(sample_config.memory.l1_working)
        conv_id = "test-conv-summ"

        # 手动注入摘要
        memory._summaries[conv_id] = ["之前的对话摘要内容"]
        memory._register_user(conv_id, "default")

        context = await memory.get_context("default", conv_id, max_tokens=1000)
        assert "[历史对话摘要]" in context
        assert "之前的对话摘要内容" in context

    @pytest.mark.asyncio
    async def test_context_respects_max_tokens(self, sample_config):
        """测试上下文遵守 max_tokens 限制"""
        memory = ShortTermMemory(sample_config.memory.l1_working)
        conv_id = "test-conv-limit"

        # 添加多条消息
        for i in range(5):
            await memory.add_message("default", conv_id, HumanMessage(content=f"消息内容{i}"))

        # 设置很小的 max_tokens，只容纳部分消息
        context = await memory.get_context("default", conv_id, max_tokens=10)
        # 不会包含所有消息
        assert len(context) > 0
        # 上下文的 token 数不应远超 max_tokens（允许格式开销）
        context_tokens = memory._count_text_tokens(context)
        assert context_tokens < 50  # 宽松验证

    @pytest.mark.asyncio
    async def test_context_empty_conversation(self, sample_config):
        """测试空会话返回空上下文"""
        memory = ShortTermMemory(sample_config.memory.l1_working)
        context = await memory.get_context("default", "empty-conv", max_tokens=1000)
        assert context == ""


# ============================================================
# 清除记忆测试
# ============================================================

class TestClearMemory:
    """测试 clear 方法"""

    @pytest.mark.asyncio
    async def test_clear_removes_messages(self, sample_config):
        """测试清除后消息为空"""
        memory = ShortTermMemory(sample_config.memory.l1_working)
        conv_id = "test-conv-clear"

        await memory.add_message("default", conv_id, HumanMessage(content="消息1"))
        await memory.add_message("default", conv_id, AIMessage(content="消息2"))

        await memory.clear("default", conv_id)

        messages = await memory.get_messages("default", conv_id)
        assert messages == []

    @pytest.mark.asyncio
    async def test_clear_removes_summaries(self, sample_config):
        """测试清除后摘要为空"""
        memory = ShortTermMemory(sample_config.memory.l1_working)
        conv_id = "test-conv-clear-s"

        memory._summaries[conv_id] = ["摘要1", "摘要2"]
        memory._register_user(conv_id, "default")

        await memory.clear("default", conv_id)
        assert conv_id not in memory._summaries

    @pytest.mark.asyncio
    async def test_clear_does_not_affect_other_conversations(self, sample_config):
        """测试清除一个会话不影响其他会话"""
        memory = ShortTermMemory(sample_config.memory.l1_working)

        await memory.add_message("default", "conv-a", HumanMessage(content="A的消息"))
        await memory.add_message("default", "conv-b", HumanMessage(content="B的消息"))

        await memory.clear("default", "conv-a")

        msgs_b = await memory.get_messages("default", "conv-b")
        assert len(msgs_b) == 1
        assert msgs_b[0].content == "B的消息"

    @pytest.mark.asyncio
    async def test_clear_nonexistent_no_error(self, sample_config):
        """测试清除不存在的会话不报错"""
        memory = ShortTermMemory(sample_config.memory.l1_working)
        # 不应抛出异常
        await memory.clear("default", "nonexistent-conv")


# ============================================================
# 压缩测试（降级模式，无 LLM）
# ============================================================

class TestCompressIfNeeded:
    """测试 compress_if_needed 方法（使用降级模式，llm_factory=None）"""

    @pytest.mark.asyncio
    async def test_no_compression_when_under_limit(self, sample_config):
        """测试 token 数未超限时不触发压缩"""
        memory = ShortTermMemory(sample_config.memory.l1_working)
        conv_id = "test-conv-no-compress"

        await memory.add_message("default", conv_id, HumanMessage(content="短消息"))
        result = await memory.compress_if_needed("default", conv_id)
        assert result is False

    @pytest.mark.asyncio
    async def test_compression_triggers_when_over_limit(self, small_l1_config):
        """测试 token 数超限时触发压缩"""
        # max_tokens=100，使用降级模式
        memory = ShortTermMemory(small_l1_config)
        conv_id = "test-conv-compress"

        # 添加大量消息以超过 max_tokens=100
        for i in range(10):
            await memory.add_message(
                "default", conv_id,
                HumanMessage(content=f"这是一段较长的测试消息内容用于触发压缩机制编号{i}"),
            )

        result = await memory.compress_if_needed("default", conv_id)
        assert result is True
        # 压缩后应生成摘要
        assert conv_id in memory._summaries
        assert len(memory._summaries[conv_id]) > 0

    @pytest.mark.asyncio
    async def test_compression_empty_messages(self, sample_config):
        """测试空消息列表时不压缩"""
        memory = ShortTermMemory(sample_config.memory.l1_working)
        result = await memory.compress_if_needed("default", "empty-conv")
        assert result is False

    @pytest.mark.asyncio
    async def test_compression_degraded_summary_content(self, small_l1_config):
        """测试降级模式生成的摘要包含原始内容片段"""
        memory = ShortTermMemory(small_l1_config)  # llm_factory=None, max_tokens=100
        conv_id = "test-conv-degraded"

        # 添加足够多的消息以超过 max_tokens=100，触发压缩
        for i in range(10):
            await memory.add_message(
                "default", conv_id,
                HumanMessage(content=f"这是一段较长的测试消息内容用于触发压缩机制编号{i}"),
            )
        await memory.compress_if_needed("default", conv_id)

        # 降级摘要应包含 "[降级摘要]" 前缀
        summaries = memory._summaries.get(conv_id, [])
        assert len(summaries) > 0
        assert "[降级摘要]" in summaries[0]
