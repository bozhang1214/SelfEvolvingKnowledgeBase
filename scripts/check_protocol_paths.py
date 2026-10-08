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
#: 端侧客户端的**扫描根**（不是单个文件）。
#
# 为什么要改成"多来源扫描根"（2026-10-25 修的真缺陷）：
#   端侧客户端早已从 `apps/android/.../net/SekbApi.kt` 搬进共享层
#   `apps/shared/src/commonMain/.../net/SekbApi.kt`（四端共用一份），
#   而本脚本仍指向那个**已不存在**的旧路径 → `client_paths()` 静默返回空集，
#   输出"客户端调用 0 个"**看起来全绿**。也就是说：**共享层里调一个文档没写的端点，
#   这道守卫一句话都不会说**——"会骗人的检查比没有检查更糟"。
#   现在改为扫描共享层（四端实际共用）+ 各端可能的自有客户端目录，
#   并且**扫描根不存在就报错**（而不是静默跳过）。
CLIENT_ROOTS = [
    # ① 共享层（KMP，Android/iOS/Mac 经 framework 共用；鸿蒙经 NAPI 共用同一份 Kotlin）
    os.path.join(ROOT, "apps", "shared", "src"),
    # ② Android 平台侧（历史位置 + 未来的平台自有客户端）
    os.path.join(ROOT, "apps", "android", "app", "src"),
    # ③ iOS / Mac（Swift 侧若出现自有 URL 拼装，同样纳入守卫）
    os.path.join(ROOT, "apps", "ios"),
    os.path.join(ROOT, "apps", "mac"),
    # ④ 鸿蒙（ArkTS 侧若出现自有 URL 拼装；Kotlin 侧已在 ① 覆盖）
    os.path.join(ROOT, "apps", "harmony"),
]
#: 只扫这些后缀：ArkTS(`.ets`)/Kotlin(`.kt`)/Swift(`.swift`)/TypeScript(`.ts`)
CLIENT_EXTS = (".kt", ".ets", ".ts", ".swift")
#: 至少这些根必须存在——它们是"端侧客户端代码"的定义域，缺任一说明仓库结构变了、
#: 守卫的假设已失效（这时**必须失败**，让维护者来改守卫，而不是让它继续假绿）。
REQUIRED_CLIENT_ROOTS = [CLIENT_ROOTS[0], CLIENT_ROOTS[1]]

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


def client_paths() -> tuple[set[str], list[str], int]:
    """扫描所有客户端源的 `/api/v1/...` 字面量。

    返回 `(路径集合, 错误列表, 扫描到的文件数)`；文件数与来源清单会打印出来，
    让"守卫真的扫到了东西"这一事实**可见**（过去正是因为扫到 0 个文件而假绿）。
    """
    paths: set[str] = set()
    errors: list[str] = []
    scanned: list[str] = []
    for root in CLIENT_ROOTS:
        if not os.path.exists(root):
            if root in REQUIRED_CLIENT_ROOTS:
                errors.append(f"客户端扫描根不存在：{os.path.relpath(root, ROOT)}"
                              "（端侧代码搬家了？请同步本守卫的 CLIENT_ROOTS）")
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            # 构建产物/第三方源码不进守卫（它们不是我们维护的客户端代码）
            dirnames[:] = [d for d in dirnames
                           if d not in {"build", ".gradle", "node_modules", ".tooling",
                                        "oh_modules", "Pods", "DerivedData"}]
            for name in sorted(filenames):
                if not name.endswith(CLIENT_EXTS):
                    continue
                full = os.path.join(dirpath, name)
                try:
                    text = open(full, encoding="utf-8").read()
                except (UnicodeDecodeError, OSError):
                    continue
                found = {norm(p) for p in PATH_RE.findall(text)}
                if found:
                    scanned.append(os.path.relpath(full, ROOT))
                    paths |= found
    if not scanned:
        errors.append("没有任何客户端文件包含 `/api/v1/...` —— 守卫扫到了 0 个来源，"
                      "这几乎一定是路径配错了（不要让守卫假绿）")
    return paths, errors, len(scanned)


def main() -> int:
    routes, doc = backend_routes(), doc_paths()
    client, scan_errors, scanned_files = client_paths()
    calls = sorted(client)
    errors: list[str] = list(scan_errors)

    for p in sorted(doc):
        if p not in routes:
            errors.append(f"文档声明了服务端不存在的端点：{p}")

    for p in calls:
        if p not in doc:
            errors.append(f"客户端调了文档未声明的端点：{p}（要么补文档，要么别调）")
        if p not in routes:
            errors.append(f"客户端调了服务端不存在的端点：{p}")

    print(f"服务端路由 {len(routes)} 个 / 文档声明 {len(doc)} 个 / "
          f"客户端调用 {len(calls)} 个（来自 {scanned_files} 个源文件）")
    if errors:
        print(f"\n❌ 协议不一致 {len(errors)} 处：")
        for e in errors:
            print(f"   - {e}")
        return 1
    print("✅ 协议 N 方一致（文档 ↔ 服务端 ↔ 各端客户端）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
