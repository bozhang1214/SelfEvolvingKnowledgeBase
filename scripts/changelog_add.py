#!/usr/bin/env python3
"""新建一条 **CHANGELOG 碎片**（多协作者下的零冲突写法）。

为什么要有碎片
--------------
`docs/CHANGELOG.md` 是历史冲突热点：所有人的约定都是「往顶部插一条」，
于是**必然冲突**——本项目已因此真实吃掉过 3 个条目的标题。

碎片把「一次改动」落到**一个独立文件**里，天然不冲突；发版时再合并进 CHANGELOG。

用法::

    python3 scripts/changelog_add.py --title "招聘分析：搜索历史 + 报告按搜索隔离"
    python3 scripts/changelog_add.py --title "P6：云端平台适配" --date 2026-09-16
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRAGMENT_DIR = ROOT / "docs" / "changelog.d"

TEMPLATE = """## {date}（{title}）

- **背景**：<为什么要做>

- **改动**：
  - <做了什么>

- **验证**：<跑过什么、结果如何>

"""


def slugify(title: str, max_len: int = 24) -> str:
    """把标题转成文件名安全的短 slug（保留中文，去掉标点与空白）。"""
    cleaned = re.sub(r"[\s:：,，。.、/\\()（）「」【】\[\]\"'`|]+", "-", title.strip())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    return cleaned[:max_len] or "change"


def main(argv: list[str] | None = None) -> int:
    """命令行入口。"""
    p = argparse.ArgumentParser(description="新建 CHANGELOG 碎片")
    p.add_argument("--title", required=True, help="条目标题（不含日期）")
    p.add_argument("--date", default=date.today().isoformat(), help="日期 YYYY-MM-DD（默认今天）")
    p.add_argument(
        "--body", help="直接给出正文；不给则生成模板留待编辑"
    )
    args = p.parse_args(argv)

    FRAGMENT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = FRAGMENT_DIR / f"{stamp}-{slugify(args.title)}.md"

    body = args.body if args.body else TEMPLATE.format(date=args.date, title=args.title)
    path.write_text(body, encoding="utf-8")

    print(f"✅ 已创建碎片: {path.relative_to(ROOT)}")
    print("   编辑它填正文，然后（发版时）跑：python3 scripts/changelog_merge.py")
    print("   ⚠️ 不要再直接往 docs/CHANGELOG.md 顶部插条目 —— 那是冲突热点。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
