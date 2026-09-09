"""
L1 短期记忆实现模块

基于内存的对话历史管理，作为 Phase 1 的 L1 记忆层实现。
核心特性：
- 内存缓存：使用 dict 缓存会话消息，key 为 conv_id
- 滑动窗口：保留最近 N 轮对话（max_turns 控制，1 轮 = user + assistant）
- Token 计数：使用 tiktoken 统计 token 数
- 层次化压缩：超过 max_tokens 时调用 LLM 将旧消息压缩为摘要
- 摘要合并：保留的摘要数量不超过 max_compressed_summaries，超出时合并最早的两个

压缩流程：
    1. add_message 后调用 compress_if_needed
    2. 若总 token > max_tokens，取出滑动窗口之外的旧消息
    3. 调用 LLM 生成摘要，加入 _summaries
    4. 若 summaries 数量 > max_compressed_summaries，合并最早的两个
    5. 从 _messages 中移除已压缩的旧消息

使用方式：
    from app.memory.short_term import ShortTermMemory
    from app.core.config import get_config
    config = get_config()
    memory = ShortTermMemory(config.memory.l1_working, llm_factory)
    await memory.add_message("default", conv_id, HumanMessage(content="你好"))
"""

from __future__ import annotations

import asyncio

import tiktoken
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from app.core.config import L1MemoryConfig
from app.core.exceptions import SEKBMemoError
from app.core.llm_factory import LLMFactory
from app.core.logging import get_logger
from app.memory.base import ShortTermMemoryBackend

logger = get_logger(__name__)


# ============================================================
# L1 短期记忆实现
# ============================================================

class ShortTermMemory(ShortTermMemoryBackend):
    """
    L1 短期记忆实现（基于内存）。

    使用内存字典缓存每个会话的对话历史，支持滑动窗口与层次化压缩。
    压缩时调用注入的 LLMFactory 实例生成摘要。
    """

    def __init__(
        self,
        config: L1MemoryConfig,
        llm_factory: LLMFactory | None = None,
        llm_role: str = "scribe",
        encoding_name: str = "cl100k_base",
    ) -> None:
        """
        初始化短期记忆。

        Args:
            config: L1 记忆配置（来自 config.memory.l1_working）
            llm_factory: LLM 工厂实例，用于压缩时调用 LLM；
                         为 None 时使用文本拼接降级（仅供测试）
            llm_role: 压缩时使用的 LLM 角色名（默认 "scribe"）
            encoding_name: tiktoken 编码名（默认 "cl100k_base"，兼容 OpenAI/DeepSeek）

        Raises:
            SEKBMemoError: tiktoken 编码加载失败
        """
        self.config: L1MemoryConfig = config
        self.llm_factory: LLMFactory | None = llm_factory
        self.llm_role: str = llm_role

        # conv_id -> 消息列表（BaseMessage）
        self._messages: dict[str, list[BaseMessage]] = {}
        # conv_id -> 压缩摘要列表（str）
        self._summaries: dict[str, list[str]] = {}
        # conv_id -> user_id 映射（用于权限校验，Phase 1 默认放行）
        self._conv_users: dict[str, str] = {}
        # conv_id -> asyncio.Lock（P2-15：compress_if_needed 按会话粒度串行化）
        self._locks: dict[str, asyncio.Lock] = {}

        # tiktoken 编码器（用于 token 计数）
        try:
            self._encoder = tiktoken.get_encoding(encoding_name)
        except Exception as e:
            raise SEKBMemoError(f"tiktoken 编码加载失败: {e}") from e

        logger.info(
            "L1 短期记忆初始化完成",
            max_turns=config.max_turns,
            max_tokens=config.max_tokens,
            hard_token_limit=config.hard_token_limit,
            compress_strategy=config.compress_strategy,
            max_compressed_summaries=config.max_compressed_summaries,
        )

    # ============ 公共 API ============

    async def get_messages(self, user_id: str, conv_id: str) -> list[BaseMessage]:
        """
        获取会话的对话历史（已应用滑动窗口）。

        滑动窗口保留最近 max_turns * 2 条消息（1 轮 = user + assistant 两条）。
        """
        self._register_user(conv_id, user_id)
        messages = self._messages.get(conv_id, [])
        # 滑动窗口：保留最近 max_turns*2 条
        max_msgs = self.config.max_turns * 2
        if len(messages) > max_msgs:
            return list(messages[-max_msgs:])
        return list(messages)

    async def add_message(self, user_id: str, conv_id: str, message: BaseMessage) -> None:
        """
        追加一条消息到会话历史。

        注意：本方法不会自动触发压缩，调用方应在适当时机调用 compress_if_needed。
        """
        self._register_user(conv_id, user_id)
        self._messages.setdefault(conv_id, []).append(message)
        logger.debug(
            "消息已加入短期记忆",
            conv_id=conv_id,
            role=message.__class__.__name__,
            total_messages=len(self._messages[conv_id]),
        )

    async def get_context(self, user_id: str, conv_id: str, max_tokens: int) -> str:
        """
        获取构建好的上下文字符串。

        构建顺序：
            1. [历史对话摘要] 块：列出所有压缩摘要
            2. 近期消息：从后往前加入，直到达到 max_tokens 限制

        Returns:
            拼接好的上下文字符串
        """
        self._register_user(conv_id, user_id)
        parts: list[str] = []

        # 1. 加入压缩摘要
        summaries = self._summaries.get(conv_id, [])
        if summaries:
            parts.append("[历史对话摘要]")
            for i, s in enumerate(summaries, 1):
                parts.append(f"摘要{i}: {s}")
            parts.append("[/历史对话摘要]")

        # 2. 加入近期消息（受 max_tokens 限制，从后往前累加）
        messages = self._messages.get(conv_id, [])
        recent_lines: list[str] = []
        used_tokens = self._count_text_tokens("\n".join(parts))

        for msg in reversed(messages):
            line = self._format_message(msg)
            line_tokens = self._count_text_tokens(line)
            if used_tokens + line_tokens > max_tokens:
                break
            recent_lines.insert(0, line)
            used_tokens += line_tokens

        parts.extend(recent_lines)
        return "\n".join(parts)

    async def compress_if_needed(self, user_id: str, conv_id: str) -> bool:
        """
        检查并执行压缩。

        当对话历史 token 数超过 max_tokens 时：
            1. 取出滑动窗口之外的旧消息（保留最近 max_turns*2 条）
            2. 调用 LLM 生成摘要
            3. 加入 _summaries；若超出 max_compressed_summaries，合并最早的两个
            4. 从 _messages 中移除已压缩的旧消息

        Returns:
            True 表示触发了压缩，False 表示无需压缩

        Raises:
            SEKBMemoError: 压缩过程中 LLM 调用失败
        """
        lock = self._locks.setdefault(conv_id, asyncio.Lock())
        async with lock:
            return await self._compress_impl(user_id, conv_id)

    async def _compress_impl(self, user_id: str, conv_id: str) -> bool:
        """内部实现：在按会话粒度的锁保护下执行压缩（P2-15）。"""
        self._register_user(conv_id, user_id)
        messages = self._messages.get(conv_id, [])
        if not messages:
            return False

        # 统计当前 token 数
        total_tokens = self._count_messages_tokens(messages)
        if total_tokens <= self.config.max_tokens:
            return False

        # 计算需要压缩的消息数：保留最近 max_turns*2 条，其余压缩
        keep_count = self.config.max_turns * 2
        if len(messages) <= keep_count:
            # 消息数不足滑动窗口时，压缩前一半
            compress_count = max(1, len(messages) // 2)
        else:
            compress_count = len(messages) - keep_count

        to_compress = messages[:compress_count]
        remaining = messages[compress_count:]

        # 调用 LLM 生成摘要
        summary = await self._generate_summary(to_compress)

        # 在本地副本上操作，避免合并 LLM 调用失败导致 _summaries 部分突变
        # （NEW-B 修复：原实现 summaries = self._summaries.get(...) 取的是已存储
        #  列表的引用，append 会立即突变存储；若随后的 _merge_summaries 抛异常，
        #  _summaries 已被追加但 _messages 未更新，造成状态不一致）
        summaries = list(self._summaries.get(conv_id, []))
        summaries.append(summary)

        # 层次化合并：若摘要数超过上限，合并最早的两个
        while len(summaries) > self.config.max_compressed_summaries:
            oldest_two = summaries[:2]
            merged = await self._merge_summaries(oldest_two)
            summaries = [merged] + summaries[2:]

        # 所有 LLM 调用成功后再提交状态变更（原子化提交）
        self._summaries[conv_id] = summaries
        self._messages[conv_id] = remaining

        logger.info(
            "短期记忆已压缩",
            conv_id=conv_id,
            compressed_count=compress_count,
            remaining_count=len(remaining),
            summaries_count=len(summaries),
            before_tokens=total_tokens,
            after_tokens=self._count_messages_tokens(remaining),
        )
        return True

    async def clear(self, user_id: str, conv_id: str) -> None:
        """清空指定会话的对话历史与压缩摘要。"""
        self._messages.pop(conv_id, None)
        self._summaries.pop(conv_id, None)
        self._conv_users.pop(conv_id, None)
        logger.info("短期记忆已清空", conv_id=conv_id)

    # ============ 内部辅助方法 ============

    def _register_user(self, conv_id: str, user_id: str) -> None:
        """记录 conv_id 与 user_id 的映射（Phase 1 不做严格校验）"""
        if conv_id not in self._conv_users:
            self._conv_users[conv_id] = user_id

    def _count_text_tokens(self, text: str) -> int:
        """统计文本的 token 数"""
        return len(self._encoder.encode(text))

    def _count_messages_tokens(self, messages: list[BaseMessage]) -> int:
        """统计消息列表的总 token 数"""
        total = 0
        for msg in messages:
            total += self._count_text_tokens(self._format_message(msg))
        return total

    def _format_message(self, msg: BaseMessage) -> str:
        """
        将消息格式化为字符串。

        格式为 "{role}: {content}"，role 映射为 user/assistant/system。
        """
        if isinstance(msg, HumanMessage):
            role = "user"
        elif isinstance(msg, AIMessage):
            role = "assistant"
        elif isinstance(msg, SystemMessage):
            role = "system"
        else:
            # 兜底：使用类名小写
            role = msg.__class__.__name__.lower()
        return f"{role}: {msg.content}"

    async def _generate_summary(self, messages: list[BaseMessage]) -> str:
        """
        调用 LLM 将对话历史压缩为摘要。

        若未注入 LLMFactory，降级为文本截断（仅供测试）。

        Args:
            messages: 待压缩的消息列表

        Returns:
            摘要字符串

        Raises:
            SEKBMemoError: LLM 调用失败
        """
        dialog = "\n".join(self._format_message(m) for m in messages)

        if self.llm_factory is None:
            # 降级：简单截断前 500 字
            logger.warning("未提供 LLMFactory，使用文本拼接降级摘要")
            return f"[降级摘要] {dialog[:500]}"

        prompt = (
            "请将以下对话历史压缩为一段简洁的中文摘要，"
            "保留关键信息、用户意图与已确认的事实，不超过 200 字：\n\n"
            f"{dialog}"
        )
        try:
            response = await self.llm_factory.ainvoke_with_stats(
                self.llm_role,
                [HumanMessage(content=prompt)],
            )
            # 提取响应文本
            if hasattr(response, "content"):
                return str(response.content)
            return str(response)
        except SEKBMemoError:
            raise
        except Exception as e:
            raise SEKBMemoError(f"LLM 生成摘要失败: {e}") from e

    async def _merge_summaries(self, summaries: list[str]) -> str:
        """
        调用 LLM 合并多个摘要为一段。

        若未注入 LLMFactory，降级为直接拼接。

        Args:
            summaries: 待合并的摘要列表

        Returns:
            合并后的摘要字符串

        Raises:
            SEKBMemoError: LLM 调用失败
        """
        if self.llm_factory is None:
            # 降级：用分隔符拼接
            logger.warning("未提供 LLMFactory，使用文本拼接降级合并")
            return " | ".join(summaries)

        joined = "\n---\n".join(f"摘要{i+1}: {s}" for i, s in enumerate(summaries))
        prompt = (
            "请将以下多个对话摘要合并为一段连贯的中文摘要，"
            "保留所有关键信息，不要丢失事实，不超过 300 字：\n\n"
            f"{joined}"
        )
        try:
            response = await self.llm_factory.ainvoke_with_stats(
                self.llm_role,
                [HumanMessage(content=prompt)],
            )
            if hasattr(response, "content"):
                return str(response.content)
            return str(response)
        except SEKBMemoError:
            raise
        except Exception as e:
            raise SEKBMemoError(f"LLM 合并摘要失败: {e}") from e
