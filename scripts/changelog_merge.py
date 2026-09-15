#!/usr/bin/env python3
"""把 CHANGELOG 碎片合并进 ``docs/CHANGELOG.md``（发版/需要时执行）。

配套 :mod:`scripts.changelog_add`。合并后**删除已合并的碎片**，避免重复。

用法::

    python3 scripts/changelog_merge.py            # 合并 + 删除碎片
    python3 scripts/changelog_merge.py --check    # 只校验碎片格式（CI / 提交前用）
    python3 scripts/changelog_merge.py --dry-run  # 看看会合并什么，不落盘

碎片格式要求：非空、且含至少一个 ``## `` 标题行（与 CHANGELOG 条目同构）。
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "docs" / "CHANGELOG.md"
FRAGMENT_DIR = ROOT / "docs" / "changelog.d"

_LAST_UPDATED_RE = re.compile(r"^> 最后更新：.*$", re.M)


def load_fragments() -> list[tuple[Path, str]]:
    """读取全部碎片，按文件名倒序（时间新的在前）。

    Raises:
        SystemExit: 碎片缺失标题 —— 宁可不合并，也不要把半成品塞进 CHANGELOG。
    """
    if not FRAGMENT_DIR.exists():
        return []
    out: list[tuple[Path, str]] = []
    problems: list[str] = []
    # 排除目录说明文件（它不是条目碎片）
    for p in sorted(
        (x for x in FRAGMENT_DIR.glob("*.md") if x.name.lower() != "readme.md"),
        reverse=True,
    ):
        text = p.read_text(encoding="utf-8").strip()
        if not text:
            problems.append(f"{p.name}: 内容为空")
            continue
        if not re.search(r"^##\s+\S", text, re.M):
            problems.append(f"{p.name}: 缺少 `## 日期（标题）` 标题行")
            continue
        out.append((p, text))
    if problems:
        print("❌ 碎片格式有问题，已中止合并：")
        for x in problems:
            print(f"   - {x}")
        raise SystemExit(1)
    return out


def insert_into_changelog(changelog: str, entries: str, today: str) -> str:
    """把 ``entries`` 插到 CHANGELOG 头部说明之后（**不替换任何已有标题**）。

    ⚠️ 这里刻意不用「替换下一个标题」的写法：本项目曾三次因为「插入写成替换」
    而吃掉下一条目的标题。所以定位点是**文件头说明块之后**，与条目标题无关。
    """
    m = _LAST_UPDATED_RE.search(changelog)
    if m is None:
        raise SystemExit("❌ 找不到 `> 最后更新：` 行，CHANGELOG 结构异常，已中止")
    sep = changelog.find("\n---\n", m.end())
    if sep == -1:
        raise SystemExit("❌ 找不到头部说明后的 `---` 分隔，CHANGELOG 结构异常，已中止")
    insert_at = sep + len("\n---\n")

    head = changelog[:insert_at]
    tail = changelog[insert_at:].lstrip("\n")
    head = _LAST_UPDATED_RE.sub(f"> 最后更新：{today}", head, count=1)
    return f"{head}\n{entries.rstrip()}\n\n---\n\n{tail}"


def check() -> int:
    """只校验碎片格式。返回进程退出码。"""
    fragments = load_fragments()
    if not fragments:
        print("✅ 没有待合并的碎片")
        return 0
    print(f"✅ {len(fragments)} 个碎片格式正常：")
    for p, _ in fragments:
        print(f"   - {p.name}")
    return 0


def merge(dry_run: bool = False) -> int:
    """执行合并。返回进程退出码。"""
    fragments = load_fragments()
    if not fragments:
        print("ℹ️ 没有待合并的碎片，什么都没做")
        return 0

    entries = "\n\n".join(text for _, text in fragments)
    if dry_run:
        print(f"（dry-run）会把 {len(fragments)} 个碎片合并进 {CHANGELOG.name}：")
        for p, _ in fragments:
            print(f"   - {p.name}")
        return 0

    if not CHANGELOG.exists():
        raise SystemExit(f"❌ 找不到 {CHANGELOG}")
    merged = insert_into_changelog(
        CHANGELOG.read_text(encoding="utf-8"), entries, date.today().isoformat()
    )
    CHANGELOG.write_text(merged, encoding="utf-8")
    for p, _ in fragments:
        p.unlink()

    print(f"✅ 已合并 {len(fragments)} 个碎片进 {CHANGELOG.relative_to(ROOT)} 并删除碎片")
    print("   别忘了 git add 这两处改动（CHANGELOG + 被删除的碎片）")
    return 0


def main(argv: list[str] | None = None) -> int:
    """命令行入口。"""
    p = argparse.ArgumentParser(description="合并 CHANGELOG 碎片")
    p.add_argument("--check", action="store_true", help="只校验碎片格式")
    p.add_argument("--dry-run", action="store_true", help="只显示会合并什么")
    args = p.parse_args(argv)
    return check() if args.check else merge(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
