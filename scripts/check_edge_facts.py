#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端侧「活数字」守卫：**文档里写的端侧统计量，必须等于代码/测试实测值**。

为什么需要它（这是本项目真实踩过的坑，不是假想）
------------------------------------------------
`docs/tech/.validation/check-facts.py` 只能发现「同一个 `fact:NAME` 在不同文档里**取值矛盾**」。
它**抓不到更常见也更危险的一种漂移**：所有文档都写着同一个**过时的**数——于是校验全绿，
而下一位接手的人按过时的数去评估工作量/判断现状。已实测到的例子：

* `apps/android/docs/VERIFICATION.md` 的 `fact:android_unit_cases=231`，
  而 `app/build/test-results/testDebugUnitTest/*.xml` 实测 **291**（36 个类）——
  **同一个假数字同时出现在 4 份文档里**（Android README、技术方案 RFC、鸿蒙端方案都引用了它）；
* 共享层规模「35 文件 / 4,066 行」被抄进 RFC 与 `apps/README.md`，实测 **37 / 4,394**。

本脚本把「文档数字 ↔ 实测数字」变成机器可判：**实测值与文档标记值不一致 → ERROR**。

判据怎么来的（**不跑测试也能复核**，这是刻意的）
------------------------------------------------
`@Test` 注解数 == Gradle 报告的用例数（Android 侧实测 291 == 291）。所以用例总数可以**静态数出来**，
`scripts/android.sh test` 不必重跑；XML 测试报告存在时再用它交叉验证一次（存在即必须一致）。

覆盖的事实
----------
| fact 名 | 实测来源 | 说明 |
|---|---|---|
| `android_unit_cases` | `apps/android/app/src/test/**` 的 `@Test` 数（+XML 报告交叉验证） | 端侧单测总数 |
| `android_main_files` / `android_main_lines` | `apps/android/app/src/main/**/*.kt` | Android 端侧平台代码规模 |
| `shared_main_files` / `shared_main_lines` | `apps/shared/src/commonMain/**/*.kt` | 共享层（KMP）规模 |
| `ios_app_files` / `ios_app_lines` | `apps/ios/App/*.swift`（Mac 复用同一份） | iOS/Mac UI+端口源码规模 |
| `contract_cases` | `apps/contract/*.json` 的 cases 总数 | 跨端契约夹具 |

用法：
    python3 scripts/check_edge_facts.py            # 有 ERROR 退出 1
    python3 scripts/check_edge_facts.py --quiet    # 只打印结论
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

#: 只认这些文档里的标记（避免把 changelog/存档文档拉进来——它们记的是**当时**的值）
FACT_RE = re.compile(r"<!--\s*fact:([A-Za-z_][A-Za-z0-9_]*)=([^>\s]+)\s*-->")

#: 参与校验的文档（活文档；存档/历史文档里的旧值不应被要求与今天一致）
LIVE_DOCS = [
    "apps/android/README.md",
    "apps/android/docs/VERIFICATION.md",
    "apps/android/docs/RETRIEVAL-EVAL.md",
    "apps/README.md",
    "apps/ios/README.md",
    "apps/mac/README.md",
    "apps/harmony/README.md",
    "docs/RFC-多端跨端方案.md",
    "docs/多端跨端-阶段汇总.md",
    "docs/多端跨端-鸿蒙端方案.md",
    "docs/多端跨端-KMP方案.md",
]


def _count_lines(paths: list[str]) -> int:
    total = 0
    for p in paths:
        with open(p, encoding="utf-8", errors="replace") as fh:
            total += sum(1 for _ in fh)
    return total


def measured() -> dict[str, int]:
    """所有可静态复核的端侧事实（键名与文档里的 `fact:` 标记一致）。"""
    android_main = sorted(glob.glob(os.path.join(ROOT, "apps/android/app/src/main/**/*.kt"), recursive=True))
    android_test = sorted(glob.glob(os.path.join(ROOT, "apps/android/app/src/test/**/*.kt"), recursive=True))
    shared_main = sorted(glob.glob(os.path.join(ROOT, "apps/shared/src/commonMain/**/*.kt"), recursive=True))
    ios_app = sorted(glob.glob(os.path.join(ROOT, "apps/ios/App/*.swift")))

    # `@Test` 计数：与 Gradle 报告的用例数一致（实测 291 == 291）
    unit_cases = 0
    for p in android_test:
        with open(p, encoding="utf-8", errors="replace") as fh:
            unit_cases += len(re.findall(r"^\s*@Test\b", fh.read(), re.M))

    contract_cases = 0
    for p in sorted(glob.glob(os.path.join(ROOT, "apps/contract/*.json"))):
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
        cases = data.get("cases")
        if isinstance(cases, list):
            contract_cases += len(cases)

    return {
        "android_unit_cases": unit_cases,
        "android_main_files": len(android_main),
        "android_main_lines": _count_lines(android_main),
        "shared_main_files": len(shared_main),
        "shared_main_lines": _count_lines(shared_main),
        "ios_app_files": len(ios_app),
        "ios_app_lines": _count_lines(ios_app),
        "contract_cases": contract_cases,
    }


def xml_report_cases() -> int | None:
    """Gradle XML 测试报告里的用例总数（报告不存在则 None）。

    这是对「`@Test` 计数」的**交叉验证**：两者不一致说明有测试没被收集到
    （或被 `@Ignore`、或报告是旧轮的），必须人来判断。
    """
    reports = glob.glob(os.path.join(
        ROOT, "apps/android/app/build/test-results/**/TEST-*.xml"), recursive=True)
    if not reports:
        return None
    total = 0
    for path in reports:
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        for suite in [root] if root.tag == "testsuite" else root.iter("testsuite"):
            total += int(suite.get("tests", "0"))
    return total


def documented() -> dict[str, list[tuple[str, int, str]]]:
    """文档里出现的端侧事实标记：name -> [(doc, line_no, raw_value)]。"""
    found: dict[str, list[tuple[str, int, str]]] = defaultdict(list)
    for rel in LIVE_DOCS:
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                for name, value in FACT_RE.findall(line):
                    found[name].append((rel, lineno, value))
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    actual = measured()
    docs = documented()
    errors: list[str] = []

    # ① Android 用例数：静态计数 vs XML 报告（报告在就必须一致）
    report = xml_report_cases()
    if report is not None and report != actual["android_unit_cases"]:
        errors.append(
            f"`@Test` 静态计数 {actual['android_unit_cases']} != Gradle XML 报告 {report}"
            "（有测试没被收集，或报告是旧一轮的——先搞清哪个是真的）"
        )

    # ② 文档标记值 vs 实测值
    checked = 0
    for name, entries in sorted(docs.items()):
        if name not in actual:
            continue  # 非端侧事实（如 api_endpoints）由 check-facts.py 管
        want = actual[name]
        if len(entries) > 1:
            errors.append(
                f"`fact:{name}` 出现在 {len(entries)} 处（{', '.join(f'{d}:{n}' for d, n, _ in entries)}）"
                "——同一个端侧事实只允许有一处权威值，其余请改成回链"
            )
        for doc, lineno, raw in entries:
            checked += 1
            try:
                got = int(raw)
            except ValueError:
                errors.append(f"{doc}:{lineno} `fact:{name}` 的值不是整数：{raw!r}")
                continue
            if got != want:
                errors.append(
                    f"{doc}:{lineno} 写 `{name}={got}`，实测 **{want}**"
                    f"（改了代码/测试就要同步改这里；改不动就说明你正基于过时数字做判断）"
                )

    # ③ 至少要有一处权威值（防止「把标记删掉就当校验通过」）。
    #    只强制 `android_unit_cases`：它是**已经被抄成假数字**的那一个
    #    （231 同时出现在 4 份文档里）；其余规模的标记按需添加，不强制，
    #    避免"每加一个测试类就要改文档否则守卫变红"的摩擦（那会让人干脆绕过守卫）。
    if "android_unit_cases" not in docs:
        errors.append("`fact:android_unit_cases` 在活文档里没有任何标记——请在一处写出权威值"
                      "（删掉标记不能让数字消失，只会让它重新漂移）")

    # ④ 契约夹具的功能下界（鸿蒙 H2 写明的下限：routing≥10 / signals≥13 / privacy≥13）。
    #    夹具被误删/被截断时，端侧契约测试会"少测但不报错"——这里把它变成硬失败。
    minimums = {"routing": 10, "signals": 13, "privacy": 13}
    actual_minimums: dict[str, int] = {}
    for name, floor in minimums.items():
        path = os.path.join(ROOT, "apps/contract", f"{name}.json")
        if not os.path.exists(path):
            errors.append(f"契约夹具缺失：apps/contract/{name}.json")
            continue
        with open(path, encoding="utf-8") as fh:
            cases = json.load(fh).get("cases") or []
        actual_minimums[name] = len(cases)
        if len(cases) < floor:
            errors.append(f"契约夹具 {name}.json 只有 {len(cases)} 个 case，低于下限 {floor}"
                          "（端侧会少测而不报错——这正是最危险的漂移）")

    if not args.quiet:
        print("── 端侧事实：实测值 ──")
        for name, value in sorted(actual.items()):
            print(f"   {name} = {value}")
        if actual_minimums:
            print("   契约夹具下限核对：" + "、".join(
                f"{k}={v}" for k, v in sorted(actual_minimums.items())))
        if report is not None:
            print(f"   （Gradle XML 报告交叉验证：{report} 用例）")
        print(f"\n── 文档标记：{checked} 处已核对 ──")

    if errors:
        print(f"\n❌ 端侧事实漂移 {len(errors)} 处：")
        for e in errors:
            print(f"   - {e}")
        return 1
    print("✅ 端侧活数字与实测一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
