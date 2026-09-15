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

from app.core.logging import get_logger

logger = get_logger(__name__)

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
        lines += [
            f"- {j.get('title', '')} | {j.get('company', '')} | {j.get('salary', '')} | {j.get('city', '')}"
            for j in jobs
        ]

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


def save_report(
    user_id: str,
    type_: str,
    title: str,
    report: dict[str, Any],
    search_id: str = "",
) -> str:
    """把报告存为 Markdown 文件，返回报告 id。

    **批量报告的保留策略**：同一「用户 + 关键词 + 城市」只保留**最新一份**——
    本次写入后，同组更旧的批量报告（含 .md/.json）会被删除，避免历史报告无限堆积。
    （单职位分析报告不参与去重，每次保留。）

    Args:
        search_id: 该批量报告归属的搜索 id（用于「删除报告时同步清缓存报告」）
    """
    _DIR.mkdir(parents=True, exist_ok=True)  # 先建目录，避免 .md 写入时目录不存在
    rid = uuid.uuid4().hex[:12]
    md = report_to_markdown(type_, title, report)
    (_DIR / f"{rid}.md").write_text(md, encoding="utf-8")
    # 额外存结构化 JSON，供前端用与批量/单职位一致的语义化布局渲染
    (_DIR / f"{rid}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    keyword = str(report.get("keyword") or "") if type_ == "batch" else ""
    city = str(report.get("city") or "") if type_ == "batch" else ""

    index = _load_index()
    index.insert(0, {
        "id": rid,
        "user_id": user_id,
        "type": type_,
        "title": title,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "search_id": search_id,
        "keyword": keyword,
        "city": city,
    })

    if type_ == "batch":
        index, removed = _dedupe_batch(index, user_id, keyword, city, keep_id=rid)
        for old_id in removed:
            _remove_files(old_id)
        if removed:
            logger.info(
                "批量报告去重", user_id=user_id, keyword=keyword, city=city, removed=len(removed)
            )

    _save_index(index[:_MAX_REPORTS])
    return rid


def _dedupe_batch(
    index: list[dict[str, Any]],
    user_id: str,
    keyword: str,
    city: str,
    keep_id: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """同一「用户 + 关键词 + 城市」的批量报告只保留 keep_id。

    Returns:
        ``(新索引, 被移除的报告 id 列表)``
    """
    kept: list[dict[str, Any]] = []
    removed: list[str] = []
    for x in index:
        same_group = False
        if x.get("type") == "batch" and x.get("user_id") == user_id:
            _backfill_entry_meta(x)  # 旧条目补元数据，否则会漏掉而留下重复
            same_group = (
                x.get("keyword") == keyword
                and str(x.get("city") or "") == str(city or "")
            )
        if same_group and x.get("id") != keep_id:
            removed.append(str(x.get("id")))
            continue
        kept.append(x)
    return kept, removed


def _remove_files(report_id: str) -> None:
    """删除一份报告的两个落盘文件（.md / .json）。"""
    (_DIR / f"{report_id}.md").unlink(missing_ok=True)
    (_DIR / f"{report_id}.json").unlink(missing_ok=True)


def _backfill_entry_meta(entry: dict[str, Any]) -> None:
    """旧索引缺 ``keyword``/``city`` 时，从报告 JSON 补齐（原地修改）。

    背景：``keyword``/``city`` 是后加的字段，早期条目没有；若不去补，去重时
    这些条目会因关键词为空而被误判成「同一组」。
    """
    if entry.get("type") != "batch" or entry.get("keyword"):
        return
    json_path = _DIR / f"{entry.get('id')}.json"
    if not json_path.exists():
        return
    try:
        rep = json.loads(json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    entry["keyword"] = rep.get("keyword") or ""
    entry["city"] = rep.get("city") or ""


def dedupe_batch_reports(user_id: str) -> dict[str, Any]:
    """对已有历史做一次性清理：同「关键词 + 城市」的批量报告只留最新一份。

    两条安全约束：
    1. 先用 :func:`_backfill_entry_meta` 补齐旧索引缺失的 keyword/city，
       避免「元数据为空」的条目被误并入同一组；
    2. **关键词为空且补不出来的条目不参与去重**（无法判定分组，宁可不删）。

    Returns:
        ``{"removed": N, "kept": M, "groups": {组: 1}}``
    """
    index = _load_index()

    newest: dict[tuple[str, str], tuple[str, str]] = {}
    for x in index:
        if x.get("type") != "batch" or x.get("user_id") != user_id:
            continue
        _backfill_entry_meta(x)
        if not x.get("keyword"):
            continue  # 无法判定分组 → 不参与去重
        gk = (str(x.get("keyword")), str(x.get("city") or ""))
        created = str(x.get("created_at") or "")
        if gk not in newest or created > newest[gk][0]:
            newest[gk] = (created, str(x.get("id")))

    keep_ids = {rid for _created, rid in newest.values()}
    kept: list[dict[str, Any]] = []
    removed: list[str] = []
    for x in index:
        is_ours_batch = x.get("type") == "batch" and x.get("user_id") == user_id
        # 只有「能判定分组且不是该组最新」的才删
        if is_ours_batch and x.get("keyword") and str(x.get("id")) not in keep_ids:
            removed.append(str(x.get("id")))
            continue
        kept.append(x)

    for rid in removed:
        _remove_files(rid)
    if removed or any(x.get("keyword") for x in index):
        _save_index(kept[:_MAX_REPORTS])

    groups = {f"{gk[0]}|{gk[1]}": 1 for gk in newest}
    return {"removed": len(removed), "kept": len(kept), "groups": groups}


def list_reports(user_id: str) -> list[dict[str, Any]]:
    """列出某用户的报告元信息（不含正文），按时间倒序。"""
    return [
        {
            "id": x["id"],
            "type": x["type"],
            "title": x["title"],
            "created_at": x.get("created_at", ""),
            "search_id": x.get("search_id", ""),
        }
        for x in _load_index()
        if x.get("user_id") == user_id
    ]


def get_report(user_id: str, report_id: str) -> dict[str, Any] | None:
    """读取某用户的一份报告，返回 {id,type,title,created_at,markdown,report}。"""
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
    # 结构化 JSON（供前端语义化渲染；旧报告可能没有，返回 None）
    report = None
    json_path = _DIR / f"{report_id}.json"
    if json_path.exists():
        try:
            report = json.loads(json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            report = None
    return {
        "id": meta["id"],
        "type": meta["type"],
        "title": meta["title"],
        "created_at": meta.get("created_at", ""),
        "markdown": md_path.read_text(encoding="utf-8"),
        "report": report,
    }


def delete_report(user_id: str, report_id: str) -> dict[str, Any] | None:
    """删除某用户的一份报告。

    返回被删除报告的元信息（含 ``search_id``，供调用方同步清理缓存报告）；
    报告不存在时返回 None。
    """
    index = _load_index()
    target = next(
        (x for x in index if x.get("id") == report_id and x.get("user_id") == user_id),
        None,
    )
    if target is None:
        return None
    # 旧索引可能没记 keyword/city：删文件前从报告 JSON 补齐，供上层同步清缓存报告
    if not target.get("keyword"):
        json_path = _DIR / f"{report_id}.json"
        if json_path.exists():
            try:
                rep = json.loads(json_path.read_text(encoding="utf-8"))
                target["keyword"] = rep.get("keyword") or ""
                target["city"] = rep.get("city") or ""
            except (json.JSONDecodeError, OSError):
                pass
    _save_index([x for x in index if not (x.get("id") == report_id and x.get("user_id") == user_id)])
    _remove_files(report_id)
    return target
