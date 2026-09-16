"""BrowserClient 单测（WP2 从 job.py 提取）。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.browser_client import BrowserClient


@pytest.mark.asyncio
async def test_post_returns_json():
    resp = MagicMock()
    resp.json.return_value = {"phase": "done"}
    resp.raise_for_status = MagicMock()

    yielded = AsyncMock()
    yielded.post = AsyncMock(return_value=resp)

    ac_mock = MagicMock()
    ac_mock.__aenter__ = AsyncMock(return_value=yielded)
    ac_mock.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "app.services.browser_client.httpx.AsyncClient", return_value=ac_mock
    ) as ac_cls:
        result = await BrowserClient(base_url="http://browser:1300/").post(
            "/login/qr/status", {"site": "boss"}, timeout=15
        )

    ac_cls.assert_called_once_with(timeout=15)
    yielded.post.assert_awaited_once_with(
        "http://browser:1300/login/qr/status",
        json={"site": "boss"},
        headers={},  # 未设置 BROWSER_INTERNAL_TOKEN 时不带内部鉴权头
    )
    assert result == {"phase": "done"}


@pytest.mark.asyncio
async def test_post_sends_internal_token(monkeypatch):
    """设置了 BROWSER_INTERNAL_TOKEN 时，必须带上 X-Internal-Token（S12 内部鉴权）。"""
    monkeypatch.setenv("BROWSER_INTERNAL_TOKEN", "tok-123")
    resp = MagicMock()
    resp.json.return_value = {"ok": True}
    resp.raise_for_status = MagicMock()

    yielded = AsyncMock()
    yielded.post = AsyncMock(return_value=resp)

    ac_mock = MagicMock()
    ac_mock.__aenter__ = AsyncMock(return_value=yielded)
    ac_mock.__aexit__ = AsyncMock(return_value=False)

    with patch("app.services.browser_client.httpx.AsyncClient", return_value=ac_mock):
        await BrowserClient().post("/cookies", {"site": "boss"})

    _, kwargs = yielded.post.await_args
    assert kwargs["headers"] == {"X-Internal-Token": "tok-123"}


@pytest.mark.asyncio
async def test_post_raises_on_http_error():
    resp = MagicMock()
    resp.raise_for_status.side_effect = RuntimeError("boom")

    yielded = AsyncMock()
    yielded.post = AsyncMock(return_value=resp)

    ac_mock = MagicMock()
    ac_mock.__aenter__ = AsyncMock(return_value=yielded)
    ac_mock.__aexit__ = AsyncMock(return_value=False)

    with patch("app.services.browser_client.httpx.AsyncClient", return_value=ac_mock):
        with pytest.raises(RuntimeError):
            await BrowserClient().post("/x", {})
