"""share_service 单测（WP2 从 share.py/chat_share.py 去重）。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.services.share_service import get_valid_share, owner_display_name


class _Ctx:
    def __init__(self, user_storage):
        self.user_storage = user_storage


class _User:
    def __init__(self, name="", email=""):
        self.name = name
        self.email = email


def test_owner_display_name_fallback_when_no_storage():
    ctx = _Ctx(None)
    assert owner_display_name(ctx, "u1", "知识库所有者") == "知识库所有者"


def test_owner_display_name_fallback_when_user_missing():
    storage = SimpleNamespace(find_by_id=lambda uid: None)
    ctx = _Ctx(storage)
    assert owner_display_name(ctx, "u1", "对话所有者") == "对话所有者"


def test_owner_display_name_prefers_name():
    storage = SimpleNamespace(find_by_id=lambda uid: _User(name="张三", email="z@x.com"))
    ctx = _Ctx(storage)
    assert owner_display_name(ctx, "u1", "fallback") == "张三"


def test_owner_display_name_falls_back_to_email_prefix():
    storage = SimpleNamespace(find_by_id=lambda uid: _User(name="", email="zhang@x.com"))
    ctx = _Ctx(storage)
    assert owner_display_name(ctx, "u1", "fallback") == "zhang"


@pytest.mark.asyncio
async def test_get_valid_share_returns_share():
    share = SimpleNamespace(is_valid=lambda: True)
    storage = SimpleNamespace(get_share=AsyncMock(return_value=share))
    assert await get_valid_share(storage, "sid") is share


@pytest.mark.asyncio
async def test_get_valid_share_404_when_missing():
    storage = SimpleNamespace(get_share=AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as e:
        await get_valid_share(storage, "sid")
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_get_valid_share_403_when_invalid():
    share = SimpleNamespace(is_valid=lambda: False)
    storage = SimpleNamespace(get_share=AsyncMock(return_value=share))
    with pytest.raises(HTTPException) as e:
        await get_valid_share(storage, "sid")
    assert e.value.status_code == 403
