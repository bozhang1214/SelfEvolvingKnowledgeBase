"""
职位分析报告存档（历史报告，Markdown 文件）。

批量分析 / 单职位分析完成后，报告自动生成 Markdown 存到 ``data/job_reports/``，
并维护 ``index.json`` 索引。data/ 目录已在 .gitignore，报告只存本地、不进 git。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_DIR = Path("data/job_reports")
_INDEX_FILE = _DIR / "index.json"
_MAX_REPORTS = 200  # 每个用户最多保留 200 条

_SINGLE_SECTIONS = {
    "job_analysis": "岗位分析",
    "knowledge_priority": "知识点优先级",
    "interview_qa": "面试 Q&A",
    "gap_analysis": "差距分析",
    "resume_advice": "简历建议",
    "project_iteration": "项目迭代",
    "job_strategy": "求职策略",
}


def _render_value(v: Any, level: int = 0) -> list[str]:
    """递归渲染 dict/list/scalar 为 Markdown 嵌套列表。"""
    lines: list[str] = []
    indent = "  " * level
    if isinstance(v, dict):
        for k, val in v.items():
            if isinstance(val, (dict, list)) and val:
                lines.append(f"{indent}- **{k}**:")
                lines.extend(_render_value(val, level + 1))
            else:
                lines.append(f"{indent}- **{k}**: {val}")
    elif isinstance(v, list):
        for item in v:
            if isinstance(item, dict):
                lines.extend(_render_value(item, level))
            elif isinstance(item, list):
                lines.extend(_render_value(item, level + 1))
            else:
                lines.append(f"{indent}- {item}")
    else:
        lines.append(f"{indent}{v}")
    return lines


def _batch_to_md(title: str, report: dict[str, Any]) -> str:
    lines = [f"# {title}", ""]
    lines.append(f"- 分析时间: {report.get('analyzed_at', '')}")
    lines.append(f"- 关键词: {report.get('keyword', '')}")
    lines.append(f"- 城市: {report.get('city', '')}")
    lines.append(f"- 职位数: {report.get('job_count', 0)}")
    lines.append("")

    stats = report.get("stats") or {}
    if stats.get("company_distribution"):
        lines.append("## 公司分布")
        lines += [f"- {c['name']} × {c['count']}" for c in stats["company_distribution"]]
        lines.append("")
    if stats.get("role_distribution"):
        lines.append("## 职位方向")
        lines += [f"- {c['name']} × {c['count']}" for c in stats["role_distribution"]]
        lines.append("")
    if stats.get("hot_keywords"):
        lines.append("## 热点关键词")
        lines += [f"- {c['keyword']} × {c['count']}" for c in stats["hot_keywords"]]
        lines.append("")

    if report.get("market"):
        lines.append("## 市场行情")
        lines.extend(_render_value(report["market"]))
        lines.append("")

    if report.get("knowledge_iteration"):
        lines.append("## 知识迭代")
        lines.extend(_render_value(report["knowledge_iteration"]))
        lines.append("")

    jobs = report.get("jobs") or []
    if jobs:
        lines.append(f"## 全部职位（{len(jobs)}）")
        lines += [f"- {j.get('title', '')} | {j.get('company', '')} | {j.get('salary', '')} | {j.get('city', '')}" for j in jobs]

    return "\n".join(lines).strip() + "\n"


def _single_to_md(title: str, report: dict[str, Any]) -> str:
    lines = [f"# {title}", ""]
    ja = report.get("job_analysis") or {}
    if ja.get("positioning") or ja.get("position"):
        lines += ["## 岗位定位", ja.get("positioning") or ja.get("position") or "", ""]

    for key, zh in _SINGLE_SECTIONS.items():
        if key in report and report[key]:
            lines.append(f"## {zh}")
            lines.extend(_render_value(report[key]))
            lines.append("")

    return "\n".join(lines).strip() + "\n"


def report_to_markdown(type_: str, title: str, report: dict[str, Any]) -> str:
    """把报告转成 Markdown。"""
    if type_ == "batch":
        return _batch_to_md(title, report)
    return _single_to_md(title, report)


def _load_index() -> list[dict[str, Any]]:
    if not _INDEX_FILE.exists():
        return []
    try:
        return json.loads(_INDEX_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _save_index(data: list[dict[str, Any]]) -> None:
    _DIR.mkdir(parents=True, exist_ok=True)
    _INDEX_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def save_report(user_id: str, type_: str, title: str, report: dict[str, Any]) -> str:
    """把报告存为 Markdown 文件，返回报告 id。"""
    _DIR.mkdir(parents=True, exist_ok=True)  # 先建目录，避免 .md 写入时目录不存在
    rid = uuid.uuid4().hex[:12]
    md = report_to_markdown(type_, title, report)
    (_DIR / f"{rid}.md").write_text(md, encoding="utf-8")

    index = _load_index()
    index.insert(0, {
        "id": rid,
        "user_id": user_id,
        "type": type_,
        "title": title,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    _save_index(index[:_MAX_REPORTS])
    return rid


def list_reports(user_id: str) -> list[dict[str, Any]]:
    """列出某用户的报告元信息（不含正文），按时间倒序。"""
    return [
        {
            "id": x["id"],
            "type": x["type"],
            "title": x["title"],
            "created_at": x.get("created_at", ""),
        }
        for x in _load_index()
        if x.get("user_id") == user_id
    ]


def get_report(user_id: str, report_id: str) -> dict[str, Any] | None:
    """读取某用户的一份报告，返回 {id,type,title,created_at,markdown}。"""
    meta = None
    for x in _load_index():
        if x.get("id") == report_id and x.get("user_id") == user_id:
            meta = x
            break
    if meta is None:
        return None
    md_path = _DIR / f"{report_id}.md"
    if not md_path.exists():
        return None
    return {
        "id": meta["id"],
        "type": meta["type"],
        "title": meta["title"],
        "created_at": meta.get("created_at", ""),
        "markdown": md_path.read_text(encoding="utf-8"),
    }


def delete_report(user_id: str, report_id: str) -> bool:
    """删除某用户的一份报告，返回是否真的删了。"""
    index = _load_index()
    new = [x for x in index if not (x.get("id") == report_id and x.get("user_id") == user_id)]
    if len(new) == len(index):
        return False
    _save_index(new)
    (_DIR / f"{report_id}.md").unlink(missing_ok=True)
    return True
