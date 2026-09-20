#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 T1 路由事实表（`docs/tech/.facts/T1-routes.md` 的端点清单部分）。

为什么用生成而不是手抄
----------------------
T1 是 2026-09-09 逆向分析期手写的，2026-09-16 复核发现**行号与计数已经过期**
（当时 65 条 vs 实测 75 个端点）。手抄的表必然再次漂移——所以端点清单改成
**从代码生成**：跑一次脚本即可与代码对齐，`file:line` 天然准确。

只生成"机械可得"的部分（方法/路径/认证/函数/证据行号）；**设计意图、鉴权模型说明**
这类需要理解的文字仍写在 T1 正文里，由人维护。

用法：
    python3 scripts/gen_facts_routes.py            # 打印到 stdout
    python3 scripts/gen_facts_routes.py --write    # 写回 T1 的生成区块（标记之间）
"""
from __future__ import annotations

import argparse
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
ROUTES_DIR = os.path.join(ROOT, "backend", "app", "api", "routes")
T1 = os.path.join(ROOT, "docs", "tech", ".facts", "T1-routes.md")
FACT_API = os.path.join(ROOT, "docs", "tech", "05-API-REFERENCE.md")

BEGIN = "<!-- BEGIN GENERATED: endpoints -->"
END = "<!-- END GENERATED: endpoints -->"

PREFIX_RE = re.compile(r'APIRouter\(\s*\n?\s*prefix="([^"]+)"([^)]*)\)', re.S)
ROUTER_DEPS_RE = re.compile(r"dependencies=\[(.*?)\]", re.S)
ENDPOINT_RE = re.compile(r'@router\.(get|post|put|patch|delete)\(\s*\n?\s*"([^"]*)"', re.S)

#: 非鉴权依赖（依赖注入等）：出现在签名里但不代表调用方身份，不参与"认证"列
NON_AUTH = {"get_app_context", "get_config", "get_store", "security"}

#: 依赖函数 → 认证口径（新增路由时若用了新依赖，脚本会打印"未知"提醒补这里）
AUTH = {
    "require_full_access": "用户/设备（白名单）",
    "require_user_account": "**仅用户**（设备 token 403）",
    "get_current_user": "用户/设备",
    "get_current_claims": "用户/设备（含设备生命周期校验）",
    "get_current_device": "**仅设备**",
    "get_current_user_or_none": "可选",
}

MODULES = [
    "auth", "chat", "chat_share", "conversations", "device", "edge", "health", "job",
    "knowledge", "metrics", "monitoring", "news", "profile", "share", "upload",
]


def scan(module: str) -> tuple[str, str, list[tuple[str, str, int, str, str]]]:
    """返回 (prefix, router 级认证, [(method, path, line, func, 端点级依赖)])。"""
    path = os.path.join(ROUTES_DIR, f"{module}.py")
    src = open(path, encoding="utf-8").read()
    lines = src.splitlines()
    m = PREFIX_RE.search(src)
    prefix = m.group(1) if m else ""
    router_deps = ""
    if m and m.group(2):
        dep_m = ROUTER_DEPS_RE.search(m.group(2))
        if dep_m:
            names = [n for n in re.findall(r"Depends\((\w+)\)", dep_m.group(1)) if n not in NON_AUTH]
            router_deps = ",".join(AUTH.get(n, f"{n}?") for n in names)

    out = []
    for em in ENDPOINT_RE.finditer(src):
        method, suffix = em.group(1).upper(), em.group(2)
        line = src[: em.start()].count("\n") + 1
        # 该端点签名里出现的依赖（向下找 12 行）
        window = "\n".join(lines[line - 1: line + 12])
        names = [n for n in re.findall(r"Depends\((\w+)\)", window) if n not in NON_AUTH]
        deps = [AUTH.get(n, f"{n}?") for n in names] or [""]
        full = re.sub(r"/{2,}", "/", f"{prefix}/{suffix.lstrip('/')}") or "/"
        full = full.rstrip("/") or "/"
        out.append((method, full, line, _func_name(window), ",".join(sorted(set(d for d in deps if d)))))
    return prefix, router_deps, out


def _func_name(window: str) -> str:
    m = re.search(r"async def (\w+)\(", window)
    return m.group(1) if m else "?"


def generate() -> tuple[str, int]:
    """返回（生成区块文本, 端点总数）。总数单独返回，避免调用方从文本里"数数"。"""
    total = 0
    unknown: list[str] = []
    per_module: list[tuple[str, str, list]] = []
    for mod in MODULES:
        if not os.path.exists(os.path.join(ROUTES_DIR, f"{mod}.py")):
            continue
        prefix, router_deps, rows = scan(mod)
        rows.sort(key=lambda r: (r[1], r[0]))
        per_module.append((mod, prefix, rows))
        total += len(rows)
        unknown += [d for _, _, _, _, deps in rows for d in deps.split(",") if d.endswith("?")]

    lines = [
        BEGIN,
        f"> 本区块由 `python3 scripts/gen_facts_routes.py --write` **生成**（端点 {total} 个）。",
        "> 行号来自当次代码，代码改动后请重跑脚本；**不要手工编辑本区块**。",
        "",
        f"**端点合计：{total} 个**（与 `docs/tech/05-API-REFERENCE.md` 的 `fact:api_endpoints` 对齐）。",
        "",
    ]
    for mod, prefix, rows in per_module:
        lines.append(f"### {mod}（{len(rows)} 个，prefix `{prefix}`）")
        lines.append("")
        lines.append("| 方法 | 路径 | 端点级认证 | 处理函数 | 证据 |")
        lines.append("|---|---|---|---|---|")
        for method, path, line, func, deps in rows:
            lines.append(
                f"| {method} | `{path}` | {deps or '（随路由级）'} | `{func}` | "
                f"`app/api/routes/{mod}.py:{line}` |"
            )
        lines.append("")
    lines.append(END)
    if unknown:
        print("⚠️ 出现未登记的依赖（请在脚本 AUTH 表里补）：", sorted(set(unknown)), file=sys.stderr)
    return "\n".join(lines), total


def check_fact_file(total: int) -> int:
    """T1 是生成物，05-API-REFERENCE 的 fact:api_endpoints 是它的下游数字。

    两处数字必须相等：否则「同一个事实两个取值」，比单处过期更隐蔽。
    """
    if not os.path.exists(FACT_API):
        print(f"⚠️  未找到 {os.path.relpath(FACT_API, ROOT)}，跳过下游数字校验", file=sys.stderr)
        return 0
    src = open(FACT_API, encoding="utf-8").read()
    m = re.search(r"<!--\s*fact:api_endpoints=(\d+)\s*-->", src)
    if not m:
        print(f"❌ {os.path.relpath(FACT_API, ROOT)} 缺少 `fact:api_endpoints` 标记",
              file=sys.stderr)
        return 1
    if int(m.group(1)) != total:
        print(f"❌ 下游数字不一致：{os.path.relpath(FACT_API, ROOT)} 写 {m.group(1)}，"
              f"代码实测 {total}。改 05 的标记或先跑 --write 对齐 T1。", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--check", action="store_true", help="只检查 T1 的生成块是否与代码同步")
    args = ap.parse_args()
    block, total = generate()
    if args.check:
        src = open(T1, encoding="utf-8").read()
        if BEGIN not in src or END not in src:
            print("❌ T1 缺少生成区块标记（BEGIN/END）", file=sys.stderr)
            return 1
        current = src[src.index(BEGIN): src.index(END) + len(END)]
        if current.strip() != block.strip():
            print("❌ T1 的端点表与代码不同步：请跑 python3 scripts/gen_facts_routes.py --write",
                  file=sys.stderr)
            return 1
        if check_fact_file(total):
            return 1
        print("✅ T1 端点表与代码同步（%d 个端点，且 05-API-REFERENCE 数字一致）" % total)
        return 0
    if not args.write:
        print(block)
        return 0
    src = open(T1, encoding="utf-8").read()
    if BEGIN in src and END in src:
        src = src[: src.index(BEGIN)] + block + src[src.index(END) + len(END):]
    else:
        src = src.rstrip() + "\n\n" + block + "\n"
    open(T1, "w", encoding="utf-8").write(src)
    print(f"✅ 已写回 {os.path.relpath(T1, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
