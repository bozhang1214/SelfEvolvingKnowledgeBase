"""招聘文件导入解析服务（WP2 从 job.py 的 import_jobs 提取）。"""
from __future__ import annotations

import os
import tempfile
from typing import Any

from fastapi import UploadFile

from app.core.logging import get_logger
from app.tools.file_processor import FileProcessor

logger = get_logger(__name__)


async def parse_job_files(files: list[UploadFile]) -> list[dict[str, Any]]:
    """批量解析职位文件（每个文件对应一个职位），返回职位 dict 列表。

    支持 .txt/.md/.markdown/.pdf/.docx（复用 FileProcessor 解析），
    文件名（去扩展名）作为职位标题，正文作为 JD。
    """
    processor = FileProcessor()
    jobs: list[dict[str, Any]] = []
    for f in files:
        name = f.filename or "未命名"
        raw = await f.read()
        content = ""
        # 优先用 FileProcessor 按扩展名解析（.pdf/.docx/.md/.txt 等）
        suffix = os.path.splitext(name)[1].lower() or ".txt"
        tmp_path = ""
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix="sekb_job_")
            with os.fdopen(fd, "wb") as tmp:
                tmp.write(raw)
            content = (await processor.parse_file(tmp_path)).strip()
        except Exception as e:  # noqa: BLE001  解析失败退回原始字节解码
            logger.warning("职位文件解析失败，退回纯文本解码", file=name, error=str(e)[:120])
            try:
                content = raw.decode("utf-8")
            except UnicodeDecodeError:
                content = raw.decode("gbk", errors="ignore")
            content = (content or "").strip()
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
