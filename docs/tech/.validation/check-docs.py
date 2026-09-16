#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文档引用校验：Markdown 的**相对链接**与**代码引用**是否仍然成立。

为什么要它（2026-09-16 技术文档复核复盘的根因 RC-2）:
    技术文档用 `file:line` 当证据，而**任何一次在上方增删行都会让引用失效**，
    且此前没有任何检查。复核时实测到的最危险错误不是「行号偏了几十行」——
    而是**引用超出了文件总行数**：
        `state.py:498-519`（该文件只有 244 行）
        `upload.py:494-1006`（该文件只有 653 行）
        `chat.py:698-835`（该文件只有 675 行）
    这类错误**只读文档永远发现不了**，而本脚本一次就能全抓出来。

两类文档，宽严不同（关键设计）:
    * **活文档**（`docs/tech/`、`docs/ops/`、`docs/research/`、`docs/BACKLOG.md`、
      `docs/README.md`、`README.md`、`AGENTS.md`、`prompt/`）：
      引用必须成立，否则 **ERROR**（可阻断 CI）。
    * **存档文档**（`CHANGELOG.md`、`changelog.d/`、`docs/codeReview/`）：
      是**某时点的记录**，行号/链接随代码演进必然失效——只报 **WARN**，不阻断。
      （否则历史记录会被迫反复改写，反而破坏「当时是什么样」的价值。）

刻意忽略的引用:
    * 运行时数据路径（`data/…`、`logs/…`、`resume/…`、`backups/…`）——
      描述的是容器内数据位置，不是仓库文件。

支持的引用写法（都写在反引号里）:
    `backend/app/api/routes/chat.py:260`
    `backend/app/api/routes/chat.py:320-325`
    `backend/app/agents/job/mcp_client.py::get_shared_kernel`
    `backend/app/core/config.py`            ← 仅校验文件是否存在

用法:
    python3 docs/tech/.validation/check-docs.py            # 报告；有 ERROR 时退出码 1
    python3 docs/tech/.validation/check-docs.py --quiet     # 只输出汇总
    python3 docs/tech/.validation/check-docs.py --no-warn   # 不列 WARN
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import urllib.parse
from collections import defaultdict

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

#: 活文档（引用必须成立）
LIVING_PREFIXES = (
    "docs/tech/", "docs/ops/", "docs/research/", "docs/BACKLOG.md",
    "docs/README.md", "docs/COORDINATION.md", "README.md", "AGENTS.md", "prompt/",
)
#: 存档文档（引用/链接失效是正常的，只提示）
ARCHIVE_PREFIXES = ("docs/CHANGELOG.md", "docs/changelog.d/", "docs/codeReview/")

#: 运行时数据路径（不是仓库文件）
RUNTIME_PREFIXES = ("data/", "logs/", "resume/", "backups/", "/tmp/", "tmp/")

#: 引用常省略的路径前缀（按此顺序尝试）
PREFIX_ROOTS = [
    "", "backend/", "backend/app/", "backend/app/api/routes/", "backend/app/agents/",
    "backend/app/core/", "backend/app/services/", "backend/app/tools/", "backend/app/storage/",
    "backend/app/memory/", "backend/app/models/", "backend/app/graph/", "backend/app/scheduler/",
    "frontend/", "frontend/src/", "frontend/src/services/", "frontend/src/pages/",
    "deploy/", "scripts/", "jobcopilot/", "jobcopilot/src/jobcopilot/", "docs/",
]

# 注意：**必须包含 md**。否则「反引号里写的 .md 路径」既不被链接检查（它没有 []() 形式）
# 也不被引用检查，形成盲区——2026-09-16 首跑就因此漏掉了 docs/BACKLOG.md 指向
# 未版本化 docs/tmp/ 的引用。
CODE_EXT = r"(?:md|py|ts|tsx|js|jsx|yaml|yml|sh|conf|json|toml|sql|css|html)"
CITE_RE = re.compile(
    r"`(?P<path>[A-Za-z0-9_][A-Za-z0-9_./\-]*\." + CODE_EXT + r")"
    r"(?:(?:::(?P<symbol>[A-Za-z_][A-Za-z0-9_.]*))|(?::(?P<start>\d+)(?:-(?P<end>\d+))?))?`"
)
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def _git(*args: str) -> list[str]:
    out = subprocess.run(["git", "-C", ROOT, "ls-files", "-z", *args],
                         capture_output=True, text=True, check=True).stdout
    return [p for p in out.split("\0") if p]


def tracked_with_submodules() -> set:
    """父仓 + 各子模块的被跟踪文件（子模块文件在父仓 `git ls-files` 里看不到，
    但它们**确实是版本控制的**，例如 `jobcopilot/docs/integrations/*.md`）。
    漏掉这层会把子模块内的正常引用误判成断链。
    """
    tracked = set(_git())
    try:
        mods = subprocess.run(
            ["git", "-C", ROOT, "config", "--file", ".gitmodules", "--get-regexp", r"^submodule\..*\.path$"],
            capture_output=True, text=True).stdout
    except OSError:
        mods = ""
    for line in mods.splitlines():
        _, _, sub = line.partition(" ")
        sub = sub.strip()
        if not sub:
            continue
        out = subprocess.run(["git", "-C", os.path.join(ROOT, sub), "ls-files", "-z"],
                             capture_output=True, text=True).stdout
        for f in out.split("\0"):
            if f:
                tracked.add(f"{sub}/{f}")
    return tracked


def is_living(rel: str) -> bool:
    return rel.startswith(LIVING_PREFIXES)


def top_level_entries() -> set:
    """仓库根的一级条目名：用来判断「带目录的路径」到底是不是仓库内文件。

    这样 `search/joblist.json`（BOSS 的 HTTP 接口路径）不会被当成文件引用。
    """
    try:
        return {e for e in os.listdir(ROOT) if not e.startswith(".git")}
    except OSError:
        return set()


def build_index(paths: list[str]) -> dict[str, list[str]]:
    idx: dict[str, list[str]] = defaultdict(list)
    for p in paths:
        idx[os.path.basename(p)].append(p)
    return idx


def candidates(raw: str, index: dict[str, list[str]], tracked: set) -> list[str]:
    """所有可能的目标路径（显式前缀优先，其次同名文件）。

    **只接受被 git 跟踪的文件**：这样「本地存在但被 gitignore」的文件（典型是
    `docs/tmp/*`）不会被判为通过——否则本地绿、干净克隆（CI/服务器）红，
    守卫给出的信号就不可信了。
    """
    out: list[str] = []
    raw = raw.strip()
    for prefix in PREFIX_ROOTS:
        cand = os.path.normpath(os.path.join(ROOT, prefix, raw))
        if os.path.isfile(cand) and cand.startswith(ROOT):
            rel = os.path.relpath(cand, ROOT)
            if rel in tracked and rel not in out:
                out.append(rel)
    for rel in index.get(os.path.basename(raw), []):
        if rel in tracked and rel not in out:
            out.append(rel)
    return out


def line_count(rel: str) -> int:
    try:
        with open(os.path.join(ROOT, rel), "rb") as fh:
            return fh.read().count(b"\n") + 1
    except OSError:
        return 0


def file_text(rel: str) -> str:
    try:
        with open(os.path.join(ROOT, rel), "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read()
    except OSError:
        return ""


def symbol_in(rel: str, symbol: str) -> bool:
    text = file_text(rel)
    if not text:
        return False
    parts = symbol.split(".")
    names = [parts[-1]]
    if len(parts) > 1:
        names.append(parts[0])
    for name in names:
        pat = re.compile(
            r"(^|\n)\s*(?:async\s+)?(?:def|class)\s+" + re.escape(name) + r"\b"
            r"|(^|\n)\s*" + re.escape(name) + r"\s*(?::[^=\n]+)?="
        )
        if pat.search(text):
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--no-warn", action="store_true")
    args = ap.parse_args()

    mds = _git("*.md")
    if not mds:
        print("⚠️  没有找到被跟踪的 Markdown 文件（不在 git 仓库根？）")
        return 0
    TRACKED = tracked_with_submodules()
    index = build_index(sorted(TRACKED))
    TOP_LEVEL = top_level_entries()

    errors: list[tuple[str, str]] = []
    warns: list[tuple[str, str]] = []
    n_links = n_cites = 0

    def report(rel: str, msg: str, *, hard: bool) -> None:
        # 存档文档里的问题一律降级为 WARN
        (errors if (hard and is_living(rel)) else warns).append((rel, msg))

    for f in mds:
        try:
            with open(os.path.join(ROOT, f), "r", encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
        except OSError:
            continue
        parent = os.path.dirname(os.path.join(ROOT, f))

        for m in LINK_RE.finditer(text):
            link = m.group(1)
            if link.startswith(("http://", "https://", "mailto:", "#", "...")):
                continue
            target = link.split("#")[0]
            if not target:
                continue
            n_links += 1
            resolved = os.path.normpath(os.path.join(parent, urllib.parse.unquote(target)))
            if os.path.isdir(resolved):
                continue                      # 目录（如 ../ops）放行
            rel_t = os.path.relpath(resolved, ROOT)
            if not (os.path.isfile(resolved) and rel_t in TRACKED):
                report(f, f"断链: {link}"
                          + ("" if os.path.exists(resolved) else "（目标不存在）")
                          + ("（目标存在但**未被版本库跟踪**，干净克隆里没有）"
                             if os.path.exists(resolved) else ""), hard=True)

        for lineno, line in enumerate(text.splitlines(), 1):
            if "check-docs:ignore" in line:
                continue  # 行级忽略：用于刻意引用「历史错例」的场景，见文件头说明
            for m in CITE_RE.finditer(line):
                raw = m.group("path")
                n_cites += 1
                if raw.startswith(RUNTIME_PREFIXES):
                    continue
                cands = candidates(raw, index, TRACKED)
                if not cands:
                    # 外部/API 路径（首段不是仓库一级目录）→ 不是文件引用，跳过
                    if "/" in raw and raw.split("/")[0] not in TOP_LEVEL:
                        continue
                    # 带目录的按「明确引用」处理；裸文件名多为口语简称，降级
                    report(f, f"{'引用文件不存在' if '/' in raw else '裸文件名未匹配'}: {raw}",
                           hard="/" in raw)
                    continue
                sym, start, end = m.group("symbol"), m.group("start"), m.group("end")
                if sym is not None:
                    if not any(symbol_in(rel, sym) for rel in cands):
                        report(f, f"符号不存在: {raw}::{sym}"
                                  f"（候选 {','.join(cands)} 内无该 def/class/赋值）", hard=True)
                    continue
                if start is None:
                    continue
                lo, hi = int(start), int(end or start)
                # 同名多候选时：只要**任一**候选行号成立即算通过（避免歧义误报）
                if any(lo >= 1 and hi <= line_count(rel) for rel in cands):
                    continue
                best = max(cands, key=line_count)
                report(f, f"行号越界: {raw}:{start}{'-' + end if end else ''}"
                          f"（{best} 只有 {line_count(best)} 行）", hard=True)

    if not args.quiet:
        if errors:
            print(f"❌ 明确错误 {len(errors)} 处（活文档，可阻断）：")
            for f, msg in errors:
                print(f"    {f}  →  {msg}")
        if warns and not args.no_warn:
            print(f"⚠️  提示 {len(warns)} 处（存档文档 / 可疑但非阻断）：")
            for f, msg in warns:
                print(f"    {f}  →  {msg}")

    print(f"检查 {len(mds)} 个 Markdown：相对链接 {n_links} 个、代码引用 {n_cites} 个"
          f" → 错误 {len(errors)}、提示 {len(warns)}")
    print("✅ 文档链接与代码引用校验通过" if not errors else "❌ 存在必须修复的引用错误")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
