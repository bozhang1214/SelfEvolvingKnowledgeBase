"""job_service 单测（WP2 从 job.py import_jobs 提取）。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.job_service import parse_job_files


def _fake_file(name: str, raw: bytes) -> SimpleNamespace:
    return SimpleNamespace(filename=name, read=AsyncMock(return_value=raw))


@pytest.mark.asyncio
async def test_parse_job_files_uses_file_processor():
    processor = SimpleNamespace(parse_file=AsyncMock(return_value="解析后的 JD 内容"))
    with patch("app.services.job_service.FileProcessor", return_value=processor):
        jobs = await parse_job_files([_fake_file("职位A.md", "# 原文".encode("utf-8"))])

    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "职位A"
    assert j["jd_text"] == "解析后的 JD 内容"
    assert j["source"] == "手动上传"


@pytest.mark.asyncio
async def test_parse_job_files_falls_back_to_utf8_decode():
    processor = SimpleNamespace(parse_file=AsyncMock(side_effect=RuntimeError("boom")))
    with patch("app.services.job_service.FileProcessor", return_value=processor):
        jobs = await parse_job_files([_fake_file("职位B.txt", "纯文本职位内容".encode("utf-8"))])

    assert jobs[0]["title"] == "职位B"
    assert jobs[0]["jd_text"] == "纯文本职位内容"


@pytest.mark.asyncio
async def test_parse_job_files_multiple_files():
    processor = SimpleNamespace(parse_file=AsyncMock(return_value="内容"))
    with patch("app.services.job_service.FileProcessor", return_value=processor):
        files = [
            _fake_file("a.txt", b"x"),
            _fake_file("b.pdf", b"y"),
        ]
        jobs = await parse_job_files(files)

    assert [j["title"] for j in jobs] == ["a", "b"]
    assert len(jobs) == 2
