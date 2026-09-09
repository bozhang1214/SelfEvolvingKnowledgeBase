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
        "http://browser:1300/login/qr/status", json={"site": "boss"}
    )
    assert result == {"phase": "done"}


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
