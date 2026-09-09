"""profile_service 单测（WP2/WP3 从 chat.py 画像子系统解耦）。"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.profile_service import (
    _build_pref_patch,
    extract_and_update_profile,
    extract_json_from_llm,
)


def test_build_pref_patch_extracts_fields():
    patch = _build_pref_patch({
        "target_roles": ["AI 工程师"],
        "target_cities": ["深圳"],
        "min_salary_k": 30,
        "keywords": ["Python"],
        "skills": ["PyTorch"],
        "career_goal": "成为架构师",
    })
    assert patch["job_preferences"] == {
        "target_roles": ["AI 工程师"],
        "target_cities": ["深圳"],
        "min_salary_k": 30,
        "keywords": ["Python"],
    }
    assert patch["skills"] == ["PyTorch"]
    assert patch["career_goal"] == "成为架构师"


def test_build_pref_patch_empty():
    assert _build_pref_patch({}) == {}


@pytest.mark.asyncio
async def test_extract_and_update_profile_no_tag_is_noop():
    answer = "普通回复，无标签"
    assert await extract_and_update_profile("u1", answer) == answer


@pytest.mark.asyncio
async def test_extract_and_update_profile_strips_tag_and_upserts():
    with patch("app.storage.profile_storage.ProfileStorage") as storage_cls:
        storage_cls.return_value.upsert_update = AsyncMock()
        answer = "这是回复 <PREF>{\"target_roles\":[\"AI\"]}</PREF> 结束"
        cleaned = await extract_and_update_profile("u1", answer)
        assert "<PREF>" not in cleaned
        assert "这是回复" in cleaned
        assert "结束" in cleaned
        storage_cls.return_value.upsert_update.assert_awaited_once()


def test_extract_json_from_llm_fenced():
    text = '```json\n{"a": 1}\n```'
    assert extract_json_from_llm(text) == {"a": 1}


def test_extract_json_from_llm_plain():
    assert extract_json_from_llm('{"b": 2}') == {"b": 2}


def test_extract_json_from_llm_no_json():
    assert extract_json_from_llm("没有 JSON") is None
    assert extract_json_from_llm("") is None
