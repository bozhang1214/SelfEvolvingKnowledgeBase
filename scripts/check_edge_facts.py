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
                    f"（改了代码/测试就要同步改这里——注意：**新增/删除测试也会改实测值**，"
                    f"所以加测试的那一次提交必须同时更新权威行，否则守卫会红；"
                    f"改不动就说明你正基于过时数字做判断）"
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

    # ⑤ **每类升级信号都必须被夹具覆盖**（只看 case 总数是不够的）。
    # 为什么（2026-10-25 补）：`signals.json` 的"≥13"只保证**数量**——把某类信号的 3 个 case
    # 删到只剩 1 个、或用别的信号 case 顶上，总数照样够，而**那一类行为就没人测了**。
    # 6 类信号是端云两侧"升级率可比"的语义基础（`PlaneRouter.evaluate` 与服务端 plane_router 同口径），
    # 少覆盖一类 = 该类的漂移不会有任何机械检查能发现。
    # `context_overflow` **不算在内**：它是**事前**不走端侧的预算判据，由 routing.json 覆盖
    # （见 `docs/ops/16-端云协同协议.md` §6 的备注），不要把它当成缺失项。
    required_signals = {
        "json_invalid", "empty", "degenerate", "timeout", "low_confidence", "tool_hallucination",
    }
    signals_path = os.path.join(ROOT, "apps/contract", "signals.json")
    covered: set[str] = set()
    if os.path.exists(signals_path):
        with open(signals_path, encoding="utf-8") as fh:
            sig_cases = json.load(fh).get("cases") or []
        for c in sig_cases:
            covered.update(c.get("expectSignals") or [])
        missing = sorted(required_signals - covered)
        if missing:
            errors.append(
                "契约夹具 signals.json **没有覆盖**这些升级信号：" + "、".join(missing)
                + "（删掉某类信号的用例不会让任何检查变红，但该类行为从此无人验证）"
            )

    # ⑥ 共享层（`commonMain`）**不得出现 JVM 专有 API**。
    #
    # 为什么把它做成静态检查而不是"等编译报错"（2026-10-25 补）：
    # `EvalStats.formatRate` 用了 `"%.1f".format(...)`，在 `commonMain` 里编不过——
    # 但它是**鸿蒙第一次真的把共享层编到 ohosArm64** 时才暴露的。原因是这里的编译矩阵有缺口：
    # iOS/Mac 只在 `-PsekbNativeTargets=true` 时才声明 native target，而日常（含 CI）跑的是
    # Android 变体；于是"共享层零平台依赖"这句结论对**没被编到的部分**是空的。
    # 这条检查 5 毫秒可跑、不需要任何工具链，比"再等下一次多端编译"早得多。
    # ⚠️ 检测器刻意**收窄**（2026-10-25 实测调过）：第一版把 `%C`/`%` 这类
    # 普通字符串里的百分号也当成 printf（"95%CI …%" 报了两处假阳性）。
    # 现在只报两类**明确**的信号：① `.format(` 调用（EvalStats 那次真实事故就是这个）；
    # ② `java.*`/`javax.*`/`System.*` 引用。想用 printf 风格的人会写成 `"…".format(…)`，
    # 照样命中；而"能绕过的守卫"正是仓库要求写明的（见 EdgePolicy.kt:118 的注释里那条）。
    jvm_only = re.compile(
        r"\.format\(|printf\(|"
        r"\bjava\.|\bjavax\.|System\.currentTimeMillis|System\.getProperty|"
        r"\bLocale\b"
    )
    #: 逃生阀（**显式、可见、可审计**）：文件里写了这一行就整文件豁免。
    #  存在的理由：`commonMain` 里偶尔必须**自己实现**一个格式化器/编解码器
    #  （例如 `EdgePolicy.kt` 的 `formatChar`：JSON 转义需要 `\uXXXX`，而它按名字会被
    #   `String.format` 之类的检查误伤）。豁免必须写进代码、并被打印出来——
    #   "静默豁免"就等于没有守卫。想要的不是零豁免，而是**每一处豁免都看得见**。
    allow_marker = "sekb:allow-jvm-api"
    leaks: list[str] = []
    allowed: list[str] = []
    for path in sorted(glob.glob(os.path.join(ROOT, "apps/shared/src/commonMain/**/*.kt"), recursive=True)):
        rel = os.path.relpath(path, ROOT)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        # 豁免**按文件**记录并**照样打印**：豁免是为了"命名/自实现格式化器"这类正当情形，
        # 不是为了静默绕过。有人在共享层加豁免时，`verify_all_ends.sh` 的输出里会看得到。
        if allow_marker in text:
            allowed.append(f"{rel}（文件内声明了 `{allow_marker}` 豁免）")
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            # 注释里正因为"这是 JVM 专有，所以不用"而提到这些名字 → 不该报
            if stripped.startswith(("//", "*", "/*")):
                continue
            m = jvm_only.search(line)
            if not m:
                continue
            entry = f"{rel}:{lineno} 出现 `{m.group(0)}` → {stripped[:80]}"
            if allow_marker not in text:
                leaks.append(entry)
    if leaks:
        errors.append(
            "共享层 `commonMain` 出现 JVM 专有 API（Android 编得过、iOS/鸿蒙编不过，"
            "而日常构建只编 Android → 会一直藏着）：\n     " + "\n     ".join(leaks)
            + "\n     改用共享层自己的工具（`Fmt.fixed` / `Fmt.pct`）或 expect/actual 端口；"
            + f"\n     若确有必要（例如自己实现格式化器），在文件里写明 `// {allow_marker}: <理由>` 显式豁免。"
        )

    # ⑥ 共享层的**公开方法**若会抛异常，必须标 `@Throws`。
    #
    # 为什么（2026-10-25 补，有实测证据）：Kotlin 异常跨到 Swift/Objective-C 时，
    # 没有 `@Throws` 声明就**变成未捕获异常、直接崩进程**——Apple 侧实测：
    # `SekbApi.login` 收到 HTTP 500 → `Uncaught Kotlin exception: SekbApiException` → 应用崩溃。
    # 后果不是"报错难看"，而是**架构退化**：iOS 为了不崩，重写了一份 `SekbApiBridge.swift`
    # （248 行，把 5 个协议路径与响应解析又抄了一遍）——正是 RFC 最反对的"制造第二套事实"。
    # 这条检查把"新增公开 API 忘了标 `@Throws`"变成机械可判。
    # 只查**会 throw 且公开**（public / 无修饰符 / internal）的函数；private 的不会跨到 Swift。
    func_throws = re.compile(
        r"^[ \t]*(?:(?:public|internal|private|protected|open|final|override|suspend|inline|operator|tailrec|external)\s+)*"
        r"fun\s+(?:<[^>]*>\s*)?(?:[\w.<>?, ]+\.)?(\w+)\s*\(", re.M)
    missing_throws: list[str] = []
    for path in sorted(glob.glob(os.path.join(ROOT, "apps/shared/src/commonMain/**/*.kt"), recursive=True)):
        rel = os.path.relpath(path, ROOT)
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
        if "throw " not in raw:
            continue
        # 去掉块注释与行注释：注释里出现 `throw`/`fun` 都不该算
        code = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
        code = "\n".join(l if not l.strip().startswith("//") else "" for l in code.splitlines())
        decls = [(m.start(), m.group(1), m.group(0)) for m in func_throws.finditer(code)]
        for idx, (pos, name, decl) in enumerate(decls):
            end = decls[idx + 1][0] if idx + 1 < len(decls) else len(code)
            body = code[pos:end]
            if "throw " not in body:
                continue
            # private / protected → 不跨语言，Swift 调不到，不要求
            if re.search(r"\b(private|protected)\b", decl):
                continue
            # 声明正上方 160 字符内要有 `@Throws(...)`
            if "@Throws(" not in code[max(0, pos - 160):pos]:
                line_no = code[:pos].count("\n") + 1
                missing_throws.append(f"{rel}:{line_no} `{name}()` 会抛异常但没有 `@Throws(...)`")
    if missing_throws:
        errors.append(
            "共享层公开方法会抛异常但没标 `@Throws`（Swift 侧只能崩，"
            "于是各端会各自重写一份客户端）：\n     " + "\n     ".join(missing_throws)
            + "\n     补 `@Throws(SekbApiException::class)`（或改返回 Result 类型）。"
        )

    # ⑦ **注释块语法**：行首 ` *` 出现在块注释之外、或块注释未闭合。
    #
    # 为什么（2026-10-25 补，**我自己刚踩过**）：给 `SekbApi` 的 KDoc 追加
    # `@Throws` 说明时，脚本把一个 ` */` 插在了原注释中间——于是第 26–33 行的 ` * …`
    # 落在注释**外面**，成为顶层语法错误：
    #   `e: SekbApi.kt:33:17 Syntax error: Expecting a top level declaration.`
    # 后果不是"我这一处坏"，而是**所有端的构建一起失败**（iOS / Mac / 鸿蒙都链接 `:shared`），
    # 而 iOS 侧那轮构建跑了 17 分钟才报出来——代价很大。Kotlin 的这类断法**没有别的静态检查**，
    # 所以补一条 30 毫秒可跑、不需要工具链的检查。
    comment_errors: list[str] = []
    comment_roots = [
        "apps/shared/src/commonMain", "apps/shared-core/src",
        "apps/harmony/spike/knspike/src", "apps/ios/App", "apps/mac",
    ]
    for rel_root in comment_roots:
        root_dir = os.path.join(ROOT, rel_root)
        if not os.path.isdir(root_dir):
            continue
        for dirpath, dirnames, filenames in os.walk(root_dir):
            dirnames[:] = [d for d in dirnames if d not in {"build", ".gradle", "node_modules", ".cxx"}]
            for fname in sorted(filenames):
                if not fname.endswith((".kt", ".swift")):
                    continue
                full = os.path.join(dirpath, fname)
                rel = os.path.relpath(full, ROOT)
                in_block = False
                with open(full, encoding="utf-8") as fh:
                    for lineno, line in enumerate(fh, 1):
                        stripped = line.strip()
                        if not in_block and stripped.startswith("*"):
                            comment_errors.append(
                                f"{rel}:{lineno} 行首是 `*` 但**不在块注释里**（`*/` 被提前闭合？）"
                                f" → {stripped[:60]}")
                        # ⚠️ 统计块注释标记时必须排除行注释：
                        #   ① Kotlin 的 `//` 后面可能出现 `/*`（注释里讨论注释）；
                        #   ② Swift 的文档注释是 `///`，用朴素正则 `.*/\*` 会把 `/// x` 误判成"块注释开始"
                        #      （实测把 SekbApiBridge.swift 判成"块注释未闭合"，是**假阳性**）。
                        # 做法：先砍掉本行的行注释部分再计数。
                        code_part = re.sub(r"//.*$", "", line)
                        opens, closes = code_part.count("/*"), code_part.count("*/")
                        if opens > closes:
                            in_block = True
                        elif closes > opens:
                            in_block = False
                if in_block:
                    comment_errors.append(f"{rel} 块注释**没有闭合**（`/*` 多于 `*/`）")
    if comment_errors:
        errors.append(
            "注释块语法错误（会让**所有端**的构建一起失败，而这类断法没有别的静态检查）：\n     "
            + "\n     ".join(comment_errors)
        )

    if not args.quiet:
        print("── 端侧事实：实测值 ──")
        for name, value in sorted(actual.items()):
            print(f"   {name} = {value}")
        if actual_minimums:
            print("   契约夹具下限核对：" + "、".join(
                f"{k}={v}" for k, v in sorted(actual_minimums.items())))
        if covered:
            print("   升级信号覆盖：" + "、".join(sorted(covered)))
        print(f"   commonMain 的 JVM 专有 API：{len(leaks)} 处（另有 {len(allowed)} 处显式豁免）")
        print(f"   会抛异常却缺 `@Throws` 的公开方法：{len(missing_throws)} 处")
        print(f"   端侧源码的注释块语法问题：{len(comment_errors)} 处")
        if allowed:
            for a in allowed:
                print(f"     · 已豁免：{a}")
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
