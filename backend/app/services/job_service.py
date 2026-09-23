"""招聘文件导入解析服务（WP2 从 job.py 的 import_jobs 提取）。"""
from __future__ import annotations

import json
import os
import tempfile
from typing import Any

from fastapi import UploadFile

from app.core.logging import get_logger
from app.tools.file_processor import FileProcessor

logger = get_logger(__name__)


def _decode_text(raw: bytes) -> str:
    """按 utf-8 解码，失败退回 gbk（导入文件的常见两种编码）。"""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("gbk", errors="ignore")


def _parse_json_jobs(raw: bytes) -> list[dict[str, Any]] | None:
    """把「一次采集导出的职位 JSON」解析成职位列表；不是这种结构则返回 None。

    兼容两种形状：

    - ``{"jobs": [ ... ]}``  ← 浏览器采集插件（``scripts/sekb-job-collector.user.js``）导出的格式
    - ``[ {...}, {...} ]``   ← 裸数组

    字段名做宽容处理（``title``/``position``、``job_url``/``url``、``jd_text``/``jd``），
    因为来源可能是插件、MCP 或手工导出，命名不统一。缺字段不报错，
    但**没有标题的条目会被跳过**——避免把垃圾灌进职位列表。
    """
    try:
        data = json.loads(_decode_text(raw))
    except (ValueError, TypeError):
        return None
    if isinstance(data, dict):
        data = data.get("jobs") or data.get("data") or data.get("items") or []
    if not isinstance(data, list) or not data:
        return None

    jobs: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("position") or "").strip()
        if not title:
            continue
        jobs.append({
            "job_id": str(item.get("job_id") or "").strip(),
            "title": title,
            "company": str(item.get("company") or "").strip(),
            "salary": str(item.get("salary") or "").strip(),
            "city": str(item.get("city") or "").strip(),
            "source": str(item.get("source") or "手动上传").strip() or "手动上传",
            "job_url": str(item.get("job_url") or item.get("url") or "").strip(),
            "jd_text": str(item.get("jd_text") or item.get("jd") or "").strip(),
        })
    return jobs or None


async def parse_job_files(files: list[UploadFile]) -> list[dict[str, Any]]:
    """批量解析职位文件，返回职位 dict 列表。

    两种输入：

    - **JSON**（``.json``）：一个文件里含多条职位（采集插件导出），走
      :func:`_parse_json_jobs`，**结构化字段完整保留**（公司/薪资/城市/链接）；
    - **其他**（``.txt/.md/.markdown/.pdf/.docx``）：复用 FileProcessor 解析，
      每个文件对应一个职位，文件名（去扩展名）作为标题、正文作为 JD。
    """
    processor = FileProcessor()
    jobs: list[dict[str, Any]] = []
    for f in files:
        name = f.filename or "未命名"
        raw = await f.read()
        content = ""
        suffix = os.path.splitext(name)[1].lower() or ".txt"

        # JSON：一次采集导出多条职位。放在 FileProcessor 之前，
        # 否则结构化字段会被压成一个「文件名 = 标题」的条目。
        if suffix == ".json":
            parsed = _parse_json_jobs(raw)
            if parsed is not None:
                jobs.extend(parsed)
                continue
            logger.warning("JSON 职位文件结构不识别，退回文本解析", file=name)

        # 优先用 FileProcessor 按扩展名解析（.pdf/.docx/.md/.txt 等）
        tmp_path = ""
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix="sekb_job_")
            with os.fdopen(fd, "wb") as tmp:
                tmp.write(raw)
            content = (await processor.parse_file(tmp_path)).strip()
        except Exception as e:  # noqa: BLE001  解析失败退回原始字节解码
            logger.warning("职位文件解析失败，退回纯文本解码", file=name, error=str(e)[:120])
            content = _decode_text(raw).strip()
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        title = os.path.splitext(name)[0].strip() or "未命名职位"
        jobs.append({
            "job_id": "",
            "title": title,
            "company": "",
            "salary": "",
            "city": "",
            "source": "手动上传",
            "job_url": "",
            "jd_text": content,
        })
    return jobs
