"""`shutdown_app` 必须关闭内核 MCP 共享客户端（CLI 路径的 stdio_client 泄漏）。

背景（2026-09-18 实测）：`sekb news-backfill` 回填成功后、日志已经打完「应用已关闭」，
解释器退出时又抛：

    an error occurred during closing of asynchronous generator <async_generator object stdio_client ...>
    RuntimeError: Attempted to exit cancel scope in a different task than it was entered in

根因：内核 MCP 客户端是 `initialize_app` 预热时建立的（见 `_warmup_kernel_mcp`），
而关闭只写在 `app/api/server.py` 的 lifespan 里——**服务端有，CLI 没有**
（chat / eval / rag-eval / news-backfill 都只调 `shutdown_app`）。于是 stdio_client
这个异步生成器没人关，最终由异步生成器**终结器**在另一个 task 里收尾，触发 anyio 的
cancel-scope 跨 task 报错，并可能遗留子进程。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agents.job import mcp_client as mcp_module
from app.core.bootstrap import shutdown_app


class _FakeRegistry:
    def __init__(self) -> None:
        self.shutdown_called = False

    async def shutdown(self) -> None:
        self.shutdown_called = True


def _ctx() -> SimpleNamespace:
    """只带 shutdown_app 会碰的那几个字段（其余默认 None）。"""
    return SimpleNamespace(
        tool_registry=_FakeRegistry(),
        knowledge_base=None,
        session_memory=None,
        usage_service=None,
    )


@pytest.mark.asyncio
async def test_shutdown_app_closes_shared_kernel(monkeypatch) -> None:
    """关闭应用时必须顺带关掉内核 MCP 共享客户端。"""
    closed: list[bool] = []

    async def _fake_close() -> None:
        closed.append(True)

    # bootstrap 在函数内 `from ... import close_shared_kernel`，所以打在模块属性上即可生效
    monkeypatch.setattr(mcp_module, "close_shared_kernel", _fake_close)
    ctx = _ctx()

    await shutdown_app(ctx)

    assert ctx.tool_registry.shutdown_called is True
    assert closed == [True], "shutdown_app 必须关闭内核 MCP 共享客户端"


@pytest.mark.asyncio
async def test_shutdown_app_survives_kernel_close_failure(monkeypatch) -> None:
    """内核 MCP 关闭失败不能影响整体退出（与其它资源清理一致：只记警告）。"""

    async def _boom() -> None:
        raise RuntimeError("close failed")

    monkeypatch.setattr(mcp_module, "close_shared_kernel", _boom)

    await shutdown_app(_ctx())  # 不抛异常即通过
