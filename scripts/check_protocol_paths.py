#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端云协议三方一致守卫：**文档 ↔ 服务端路由 ↔ 端侧客户端**。

为什么需要
----------
协议文档（`docs/ops/16-端云协同协议.md`）是端云两侧共享的契约。它有三种漂移方式，
都不会报错、不会有测试失败，只会让读文档的人按错的接口去写代码：

1. **文档凭空写了一个服务端并不存在的端点**（改了路由忘了改文档）；
2. **端侧客户端调了一个文档里没有的端点**（偷偷加接口，别人无法据此实现第二端）；
3. **客户端在调一个服务端已经删掉的端点**（改名后只改了一边）。

本脚本把这三条变成机器可判：

| 检查 | 规则 |
|---|---|
| 文档 → 服务端 | 文档里出现的每个 `/api/v1/...` 必须能匹配到真实路由 |
| 客户端 → 文档 | 客户端代码里出现的每个 `/api/v1/...` 必须在文档里声明 |
| 客户端 → 服务端 | 客户端调用的每个路径必须存在真实路由（由上一条 + 第一条推出，仍单独报，便于定位） |

用法：`python3 scripts/check_protocol_paths.py`（退出码 0 = 一致）
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
PROTOCOL = os.path.join(ROOT, "docs", "ops", "16-端云协同协议.md")
ROUTES_DIR = os.path.join(ROOT, "backend", "app", "api", "routes")
CLIENT = os.path.join(
    ROOT, "apps", "android", "app", "src", "main", "kotlin",
    "com", "sekb", "ondevice", "net", "SekbApi.kt",
)

PATH_RE = re.compile(r"/api/v1/[A-Za-z0-9_\-/{}]*")
PREFIX_RE = re.compile(r'APIRouter\(\s*prefix="([^"]+)"')
ENDPOINT_RE = re.compile(r'@router\.(?:get|post|put|patch|delete)\(\s*"([^"]*)"')


def norm(path: str) -> str:
    """归一化：去查询串、去尾斜杠、把路径参数统一成 `{}`、压掉重复斜杠。"""
    p = path.split("?")[0].strip().rstrip(".")
    p = re.sub(r"\{[^}]*\}", "{}", p)
    p = re.sub(r"/{2,}", "/", p)
    return p.rstrip("/") or "/"


def backend_routes() -> set[str]:
    """从路由文件里解析出真实端点（`APIRouter(prefix=...)` + `@router.x("...")`）。"""
    routes: set[str] = set()
    for name in sorted(os.listdir(ROUTES_DIR)):
        if not name.endswith(".py"):
            continue
        text = open(os.path.join(ROUTES_DIR, name), encoding="utf-8").read()
        m = PREFIX_RE.search(text)
        prefix = m.group(1) if m else ""
        for suffix in ENDPOINT_RE.findall(text):
            routes.add(norm(f"{prefix}/{suffix.lstrip('/')}"))
    return routes


def doc_paths() -> set[str]:
    text = open(PROTOCOL, encoding="utf-8").read()
    return {norm(p) for p in PATH_RE.findall(text)}


def client_paths() -> set[str]:
    if not os.path.exists(CLIENT):
        return set()
    text = open(CLIENT, encoding="utf-8").read()
    return {norm(p) for p in PATH_RE.findall(text)}


def main() -> int:
    routes, doc, client = backend_routes(), doc_paths(), client_paths()
    errors: list[str] = []

    for p in sorted(doc):
        if p not in routes:
            errors.append(f"文档声明了服务端不存在的端点：{p}")

    for p in sorted(client):
        if p not in doc:
            errors.append(f"客户端调了文档未声明的端点：{p}（要么补文档，要么别调）")
        if p not in routes:
            errors.append(f"客户端调了服务端不存在的端点：{p}")

    print(f"服务端路由 {len(routes)} 个 / 文档声明 {len(doc)} 个 / 客户端调用 {len(client)} 个")
    if errors:
        print(f"\n❌ 协议不一致 {len(errors)} 处：")
        for e in errors:
            print(f"   - {e}")
        return 1
    print("✅ 协议三方一致（文档 ↔ 服务端 ↔ 端侧客户端）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
