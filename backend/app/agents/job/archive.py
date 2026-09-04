"""
职位分析报告存档（历史报告）。

批量分析 / 单职位分析完成后，报告自动存档到 ``data/job_reports.json``，
支持按用户列出、读取、删除，方便用户日后回顾。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_FILE = Path("data/job_reports.json")
_MAX_REPORTS = 200  # 每个用户最多保留 200 条


def _load() -> list[dict[str, Any]]:
    if not _FILE.exists():
        return []
    try:
        return json.loads(_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _save(data: list[dict[str, Any]]) -> None:
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    _FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def save_report(user_id: str, type_: str, title: str, report: dict[str, Any]) -> str:
    """存档一份报告，返回报告 id。"""
    rid = uuid.uuid4().hex[:12]
    item = {
        "id": rid,
        "user_id": user_id,
        "type": type_,
        "title": title,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "report": report,
    }
    data = _load()
    data.insert(0, item)
    _save(data[:_MAX_REPORTS])
    return rid


def list_reports(user_id: str) -> list[dict[str, Any]]:
    """列出某用户的报告元信息（不含报告正文），按时间倒序。"""
    return [
        {
            "id": x["id"],
            "type": x["type"],
            "title": x["title"],
            "created_at": x.get("created_at", ""),
        }
        for x in _load()
        if x.get("user_id") == user_id
    ]


def get_report(user_id: str, report_id: str) -> dict[str, Any] | None:
    """读取某用户的一份报告（含正文）。"""
    for x in _load():
        if x.get("id") == report_id and x.get("user_id") == user_id:
            return x
    return None


def delete_report(user_id: str, report_id: str) -> bool:
    """删除某用户的一份报告，返回是否真的删了。"""
    data = _load()
    new = [x for x in data if not (x.get("id") == report_id and x.get("user_id") == user_id)]
    _save(new)
    return len(new) != len(data)
