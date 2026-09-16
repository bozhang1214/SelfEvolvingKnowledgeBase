"""
日报存储模块。

- 日报正文存 Markdown 文件：`{report_dir}/daily_{YYYY-MM-DD}.md`
- 索引存 JSON：`{report_dir}/index.json`（日期 → 元信息，便于列表/检索）
- 自动清理超过保留天数的旧日报
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
        md_path.write_text(self._to_markdown("AI 科技资讯", report, f"日报 · {day}"), encoding="utf-8")
        # 额外落一份结构化 JSON，供周报/月报聚合使用
        json_path = self._dir / f"daily_{day}.json"
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        self._update_index(day, report, md_path)
        self._cleanup()
        return str(md_path)

    def daily_exists(self, day: str) -> bool:
        """判断某天的日报是否已生成（幂等判断用）。"""
        return (self._dir / f"daily_{day}.md").exists()

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

    def read_daily_structured(self, day: str) -> dict | None:
        """读取某天日报的结构化 JSON（供周报/月报聚合），无则返回 None。"""
        json_path = self._dir / f"daily_{day}.json"
        if not json_path.exists():
            return None
        try:
            return json.loads(json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    # ---------- 周期报告（周报/月报） ----------

    def save_periodic(self, report_type: str, period: str, report: dict) -> str:
        """保存周报/月报（与日报同格式），返回 Markdown 文件路径。"""
        md_path = self._dir / f"{report_type}_{period}.md"
        label = "周报" if report_type == "weekly" else "月报"
        md_path.write_text(
            self._to_markdown("AI 科技资讯", report, f"{label} · {period}"), encoding="utf-8"
        )
        return str(md_path)

    def list_periodic(self, report_type: str) -> list[dict]:
        """列出某类周期报告（按 period 倒序）。"""
        out = []
        for f in self._dir.glob(f"{report_type}_*.md"):
            period = f.stem.replace(f"{report_type}_", "")
            out.append({"type": report_type, "period": period, "path": str(f)})
        return sorted(out, key=lambda x: x.get("period", ""), reverse=True)

    def read_periodic(self, report_type: str, period: str) -> dict | None:
        """读取某期周报/月报。"""
        md_path = self._dir / f"{report_type}_{period}.md"
        if not md_path.exists():
            return None
        return {
            "type": report_type,
            "period": period,
            "markdown": md_path.read_text(encoding="utf-8"),
        }

    # ---------- 内部 ----------

    def _update_index(self, day: str, report: dict, md_path: Path) -> None:
        index = self._read_index()
        # 覆盖同一天
        index = [it for it in index if it.get("date") != day]
        headline_title = (report.get("headline") or {}).get("title", "")
        if not headline_title and report.get("sections"):
            headline_title = report["sections"][0].get("summary", "")[:100]
        index.append({
            "date": day,
            "total_count": report.get("total_count", 0),
            "headline": headline_title[:100],
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
        # 原子写：先写临时文件再 replace。非原子写一旦崩溃截断，_read_index 会静默
        # 返回空列表 → 全量日报索引丢失（md 还在但列表页空了）且无任何告警。
        tmp = self._index_file.with_name(self._index_file.name + ".tmp")
        tmp.write_text(
            json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(self._index_file)

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
    def _highlight_keywords(text: str, keywords: list[str]) -> str:
        """把 text 里命中 keywords 的词用 markdown 加粗标出（英文关键词大小写不敏感）。"""
        if not text or not keywords:
            return text
        for kw in keywords:
            if not kw:
                continue
            try:
                text = re.sub(
                    re.escape(kw),
                    lambda m: f"**{m.group(0)}**",
                    text,
                    flags=re.IGNORECASE,
                )
            except re.error:
                text = text.replace(kw, f"**{kw}**")
        return text

    @staticmethod
    def _to_markdown(title: str, report: dict, subtitle: str = "") -> str:
        """把日报/周报/月报 JSON 渲染为结构清晰的 Markdown（含头条 + 总结预测 + 打分）。"""
        lines = [f"# {title}", ""]
        if subtitle:
            lines.append(f"> **📅 {subtitle}**")
            lines.append("")

        # 头条（本周期最重要的一条，置顶）
        headline = report.get("headline") or {}
        if headline.get("title"):
            lines.append("## 🔥 头条")
            lines.append("")
            title = headline.get("title", "")
            source = headline.get("source", "")
            score = headline.get("importance")
            score_tag = f" ⭐{score}" if isinstance(score, (int, float)) else ""
            lines.append(f"**{title}**（{source}）{score_tag}")
            if headline.get("abstract"):
                lines.append(f"- 摘要：{headline.get('abstract')}")
            if headline.get("analysis"):
                lines.append(f"- 分析：{headline.get('analysis')}")
            if headline.get("attention"):
                lines.append(f"- 关注：{headline.get('attention')}")
            if headline.get("link"):
                lines.append(f"- 🔗 [原文链接]({headline.get('link')})")
            lines.append("")

        for section in report.get("sections", []):
            items = section.get("items", [])
            if not items:
                continue
            category = section.get("category", "其他")
            summary = section.get("summary", "")
            keywords = section.get("keywords") or []
            lines.append(f"## {category}（{len(items)} 条）")
            lines.append("")
            if summary:
                lines.append(f"> **📊 总结与预测**：{summary}")
                lines.append("")
            for i, item in enumerate(items, 1):
                # 标题保持整体加粗（醒目），关键词高亮只作用于摘要（避免与标题整行加粗的 markdown 嵌套冲突）
                title = item.get("title", "")
                source = item.get("source", "")
                abstract = NewsStorage._highlight_keywords(
                    item.get("abstract", "") or item.get("one_liner", ""), keywords
                )
                attention = item.get("attention", "") or item.get("why_matters", "")
                link = item.get("link", "")
                score = item.get("importance")
                score_tag = f" ⭐{score}" if isinstance(score, (int, float)) else ""
                lines.append(f"**{i}. {title}**（{source}）{score_tag}")
                if abstract:
                    lines.append(f"- 摘要：{abstract}")
                if attention:
                    lines.append(f"- 关注：{attention}")
                if link:
                    lines.append(f"- 🔗 [原文链接]({link})")
                lines.append("")

        # 篇尾综合分析：跨大类关联分析 + 趋势预测
        comp = report.get("comprehensive") or {}
        if comp.get("correlation") or comp.get("forecast"):
            lines.append("## 🔮 综合分析 · 趋势展望")
            lines.append("")
            if comp.get("correlation"):
                lines.append(f"> **🔗 关联分析**：{comp.get('correlation')}")
                lines.append("")
            if comp.get("forecast"):
                lines.append(f"> **🔮 趋势预测**：{comp.get('forecast')}")
                lines.append("")
        return "\n".join(lines)
