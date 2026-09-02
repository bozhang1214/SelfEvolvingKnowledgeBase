"""
日报存储模块。

- 日报正文存 Markdown 文件：`{report_dir}/daily_{YYYY-MM-DD}.md`
- 索引存 JSON：`{report_dir}/index.json`（日期 → 元信息，便于列表/检索）
- 自动清理超过保留天数的旧日报
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


class NewsStorage:
    """日报存储：Markdown + 索引 JSON。"""

    def __init__(self, report_dir: str, retention_days: int = 70) -> None:
        self._dir = Path(report_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._retention = retention_days
        self._index_file = self._dir / "index.json"

    def save_daily(self, day: str, report: dict) -> str:
        """保存某天的日报，返回 Markdown 文件路径。"""
        md_path = self._dir / f"daily_{day}.md"
        md_path.write_text(self._to_markdown(day, report), encoding="utf-8")

        self._update_index(day, report, md_path)
        self._cleanup()
        return str(md_path)

    def list_reports(self) -> list[dict]:
        """列出所有日报元信息（按日期倒序）。"""
        index = self._read_index()
        return sorted(index, key=lambda x: x.get("date", ""), reverse=True)

    def read_report(self, day: str) -> dict | None:
        """读取某天的日报（返回索引项 + Markdown 内容）。"""
        for item in self._read_index():
            if item.get("date") == day:
                md_path = Path(item["path"])
                item["markdown"] = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
                return item
        return None

    # ---------- 内部 ----------

    def _update_index(self, day: str, report: dict, md_path: Path) -> None:
        index = self._read_index()
        # 覆盖同一天
        index = [it for it in index if it.get("date") != day]
        index.append({
            "date": day,
            "total_count": report.get("total_count", 0),
            "headline": (report.get("headline") or {}).get("title", ""),
            "path": str(md_path),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        self._write_index(index)

    def _read_index(self) -> list[dict]:
        if not self._index_file.exists():
            return []
        try:
            return json.loads(self._index_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("日报索引解析失败，重置为空", path=str(self._index_file))
            return []

    def _write_index(self, index: list[dict]) -> None:
        self._index_file.write_text(
            json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _cleanup(self) -> None:
        """清理超过保留天数的旧日报文件。"""
        if self._retention <= 0:
            return
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._retention)
        for f in self._dir.glob("daily_*.md"):
            try:
                day = f.stem.replace("daily_", "")
                d = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                if d < cutoff:
                    f.unlink()
                    logger.info("清理过期日报", file=f.name)
            except (ValueError, OSError):
                continue

    @staticmethod
    def _to_markdown(day: str, report: dict) -> str:
        """把日报 JSON 渲染为 Markdown。"""
        lines = [f"# AI 资讯日报 · {day}", ""]
        headline = report.get("headline") or {}
        if headline:
            lines += [
                f"## 头条：{headline.get('title', '')}",
                "",
                headline.get("summary", ""),
                "",
                f"> 影响：{headline.get('impact', '')}",
                "",
            ]
        for section in report.get("sections", []):
            lines.append(f"## {section.get('category', '其他')}")
            lines.append("")
            for item in section.get("items", []):
                lines.append(f"- **{item.get('title', '')}**（{item.get('source', '')}）")
                lines.append(f"  - {item.get('one_liner', '')}")
                if item.get("why_matters"):
                    lines.append(f"  - 关注点：{item.get('why_matters')}")
                if item.get("link"):
                    lines.append(f"  - {item.get('link')}")
            lines.append("")
        return "\n".join(lines)
