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


# ─────────────────── JSON 导入（浏览器采集插件导出格式） ───────────────────

def _json_file(payload: str, name: str = "sekb-jobs.json") -> SimpleNamespace:
    return _fake_file(name, payload.encode("utf-8"))


@pytest.mark.asyncio
async def test_parse_job_files_json_keeps_structured_fields():
    """插件导出的 {"jobs": [...]} 要保留公司/薪资/城市/链接，而不是压成一个条目。"""
    payload = """
    {
      "source": "SEKB 职位采集插件",
      "count": 2,
      "jobs": [
        {"job_id": "abc123", "title": "AI Agent 工程师", "company": "某公司",
         "salary": "40-60K", "city": "北京", "source": "BOSS直聘",
         "job_url": "https://www.zhipin.com/job_detail/abc123.html", "jd_text": ""},
        {"job_id": "def456", "title": "端侧推理工程师", "company": "另一公司",
         "salary": "50-70K", "city": "北京-海淀区", "source": "智联招聘",
         "job_url": "https://www.zhaopin.com/jobdetail/def456.htm", "jd_text": "职责…"}
      ]
    }
    """
    processor = SimpleNamespace(parse_file=AsyncMock(side_effect=AssertionError("JSON 不该走 FileProcessor")))
    with patch("app.services.job_service.FileProcessor", return_value=processor):
        jobs = await parse_job_files([_json_file(payload)])

    assert len(jobs) == 2
    a = jobs[0]
    assert a["job_id"] == "abc123"
    assert a["title"] == "AI Agent 工程师"
    assert a["company"] == "某公司"
    assert a["salary"] == "40-60K"
    assert a["city"] == "北京"
    assert a["source"] == "BOSS直聘"
    assert a["job_url"] == "https://www.zhipin.com/job_detail/abc123.html"
    assert jobs[1]["jd_text"] == "职责…"


@pytest.mark.asyncio
async def test_parse_job_files_json_bare_array_and_aliases():
    """裸数组 + 别名字段（position/url/jd）也要认。"""
    payload = '[{"position": "Agent 架构师", "company": "X", "url": "https://e.com/1", "jd": "JD 正文"}]'
    with patch("app.services.job_service.FileProcessor"):
        jobs = await parse_job_files([_json_file(payload)])

    assert len(jobs) == 1
    assert jobs[0]["title"] == "Agent 架构师"
    assert jobs[0]["job_url"] == "https://e.com/1"
    assert jobs[0]["jd_text"] == "JD 正文"
    assert jobs[0]["source"] == "手动上传"  # 缺 source 时的默认值


@pytest.mark.asyncio
async def test_parse_job_files_json_skips_untitled_entries():
    """没有标题的条目要跳过（避免把垃圾灌进职位列表）。"""
    payload = '[{"title": "有效的岗"}, {"company": "没有标题的公司"}, {"title": "   "}]'
    with patch("app.services.job_service.FileProcessor"):
        jobs = await parse_job_files([_json_file(payload)])

    assert [j["title"] for j in jobs] == ["有效的岗"]


@pytest.mark.asyncio
async def test_parse_job_files_invalid_json_falls_back_to_text():
    """坏 JSON 不能整条丢掉——退回文本解析，用文件名当标题。"""
    processor = SimpleNamespace(parse_file=AsyncMock(return_value="退回的正文"))
    with patch("app.services.job_service.FileProcessor", return_value=processor):
        jobs = await parse_job_files([_fake_file("坏文件.json", b"{ not json at all")])

    assert len(jobs) == 1
    assert jobs[0]["title"] == "坏文件"
    assert jobs[0]["jd_text"] == "退回的正文"


@pytest.mark.asyncio
async def test_parse_job_files_json_and_text_mixed():
    """同一次导入里 JSON 与普通文件并存。"""
    json_payload = '[{"title": "JSON 岗", "company": "A"}]'
    processor = SimpleNamespace(parse_file=AsyncMock(return_value="文本正文"))
    with patch("app.services.job_service.FileProcessor", return_value=processor):
        jobs = await parse_job_files([
            _json_file(json_payload),
            _fake_file("普通岗.md", b"# x"),
        ])

    assert [j["title"] for j in jobs] == ["JSON 岗", "普通岗"]
    assert jobs[0]["company"] == "A"
    assert jobs[1]["company"] == ""
