"""
答案 token 流式回传（R2-06 真流式）。

用 contextvars 在「SSE 输出层」与「Executor 生成答案」之间传递一个 token 回调，
Executor 生成草稿答案时逐 token 调用该回调，SSE 层据此实时推流。

- 默认 None：非流式（Executor 走 ainvoke_with_stats，兼容 CLI/非流式端点）。
- chat_stream 在跑图前 set_token_sink(queue 回调)，跑图时消费队列。
"""
from __future__ import annotations

import contextvars
from typing import Awaitable, Callable

TokenSink = Callable[[str], Awaitable[None]]

_token_sink: contextvars.ContextVar[TokenSink | None] = contextvars.ContextVar(
    "sekb_token_sink", default=None
)


def set_token_sink(sink: TokenSink | None) -> contextvars.Token:
    """设置当前上下文的 token 回调，返回用于恢复的 token。"""
    return _token_sink.set(sink)


def reset_token_sink(token: contextvars.Token) -> None:
    """恢复上下文（与 set_token_sink 配对）。"""
    _token_sink.reset(token)


def get_token_sink() -> TokenSink | None:
    """获取当前上下文的 token 回调（无则 None）。"""
    return _token_sink.get()
