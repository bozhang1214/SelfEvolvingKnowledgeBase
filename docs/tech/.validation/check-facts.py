#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「活数字」一致性校验（根治 RC-4：同一事实被抄在多处 → 必然分叉）。

复核时手工逮到的分叉（都是同一事实在不同文档里写了不同值）：
    * 覆盖率：`10-TESTING.md` 写 43%，`.github/workflows/ci.yml` 注释写 44%（实测 54%）
    * 指标未埋点数：`07-DESIGN-PATTERNS.md` 写 4，`09-OBSERVABILITY.md` 写 3
    * 死配置数：`07` 写 49，`06-CONFIG-REFERENCE.md` 重算 29
    * 内核工具数：平台手册写 6，实际 7（`self_check` 后加）
    * JWT 有效期：`config.yaml` 168h / `config.py` 默认 2160 / 前端注释 90 天

它们**不会报错、不会有测试失败**，只会让读文档的人按错的数去做。靠人肉记住"这个数
在另外三处也有"是不可靠的——所以改成机器强制：

约定（见 `DOC-TEMPLATE.md`）
    在写「活数字」的那一行加一个 HTML 注释标记：
        <!-- fact:mypy=300 -->
    含义：`mypy` 这个事实的值是 `300`，由**发布该事实的文档**负责更新；
    其他文档要么不重复写，要么写同一个值。

本脚本做三件事
    1. 同一个 `fact:NAME` 在不同文件/行出现**不同值** → **ERROR**（真正的自相矛盾）
    2. 同一个 `fact:NAME` 出现在多个文件 → **WARN**（重复，建议只留一处 + 链接）
    3. 打印事实清单，便于一眼看到当前所有活数字及其归属

用法：
    python3 docs/tech/.validation/check-facts.py
    python3 docs/tech/.validation/check-facts.py --no-warn
退出码：0 = 一致；1 = 存在互相矛盾的值
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections import defaultdict

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
FACT_RE = re.compile(r"<!--\s*fact:([A-Za-z_][A-Za-z0-9_]*)=([^>\s]+)\s*-->")

#: 只检查「活文档」；存档文档记录的是当时的值，不应被要求与今天一致
SKIP_PREFIXES = ("docs/CHANGELOG.md", "docs/changelog.d/", "docs/codeReview/")


def tracked_md() -> list[str]:
    out = subprocess.run(["git", "-C", ROOT, "ls-files", "-z", "*.md"],
                         capture_output=True, text=True, check=True).stdout
    return [p for p in out.split("\0") if p]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-warn", action="store_true")
    args = ap.parse_args()

    facts: dict[str, list[tuple[str, int, str]]] = defaultdict(list)  # name -> [(file, line, value)]
    for f in tracked_md():
        if f.startswith(SKIP_PREFIXES):
            continue
        try:
            with open(os.path.join(ROOT, f), "r", encoding="utf-8", errors="ignore") as fh:
                lines = fh.read().splitlines()
        except OSError:
            continue
        in_fence = False
        for i, line in enumerate(lines, 1):
            if line.lstrip().startswith("```"):
                in_fence = not in_fence
                continue          # 围栏代码块里的标记是「示例」，不是事实断言
            if in_fence:
                continue
            for m in FACT_RE.finditer(line):
                facts[m.group(1)].append((f, i, m.group(2)))

    errors: list[str] = []
    warns: list[str] = []
    for name, hits in sorted(facts.items()):
        values = {v for _, _, v in hits}
        files = {f for f, _, _ in hits}
        if len(values) > 1:
            detail = "；".join(f"{f}:{ln} = {v}" for f, ln, v in hits)
            errors.append(f"事实 `{name}` 取值矛盾：{detail}")
        elif len(files) > 1:
            warns.append(f"事实 `{name}` 在 {len(files)} 个文件重复出现"
                         f"（{'、'.join(sorted(files))}）——建议只留一处，其余改为链接")

    print(f"已知活数字 {len(facts)} 项："
          + ("、".join(f"{k}={v[0][2]}" for k, v in sorted(facts.items())) if facts else "（无）"))
    if errors:
        print(f"\n❌ 矛盾 {len(errors)} 处：")
        for e in errors:
            print("    " + e)
    if warns and not args.no_warn:
        print(f"\n⚠️  重复 {len(warns)} 处（不阻断）：")
        for w in warns:
            print("    " + w)
    print("\n✅ 活数字一致" if not errors else "\n❌ 活数字存在互相矛盾的值")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
