#!/usr/bin/env python3
"""iOS app 自检的**记录与校验**工具（方案 C：登记式，不做 CI 门禁）。

为什么需要它
============
iOS / Mac / 鸿蒙 三端都需要 Xcode / 模拟器 / 鸿蒙模拟器这类**重环境**，Linux CI 跑不了，
所以它们的验收只能靠人跑。而"靠人记"会漏 —— 本项目真的漏过：主线汇报过 iOS，
但实际只跑了 framework 冒烟（`ios.sh smoke`，验的是共享层纯逻辑），**没跑 app 自检**，
而同一次验收里 Mac 跑的是真 app。两端强度不对等，谁也没发现。

这份工具把"跑过没有"变成**机器可判**：
- `run`   ：跑 `scripts/ios_app.sh run`，把结果**自动**写进 `apps/ios/docs/验收记录.json`
            （带 commit SHA —— 这是关键，没有 SHA 就没法判断记录是否还新鲜）；
- `check` ：读记录，检查"自那次通过以来，**iOS 相关文件有没有再被改过**"；
            改了 → 报**陈旧**（退出码 1），并直接给出该跑的命令。

两种模式
========
- 默认 **非阻塞**：`check` 有问题也只打印 ⚠️、退出码 0（方案 C 的定义：不挡提交）。
- `--strict`：有问题就退出码 1（发版/收尾时用，或将来想升级成 CI 门禁时直接接上）。

用法
====
    bash scripts/ios_record.py run                 # 跑 + 记录（几分钟）
    bash scripts/ios_record.py check               # 校验（秒级，非阻塞）
    bash scripts/ios_record.py check --strict      # 校验（阻塞，用于发版）
    bash scripts/ios_record.py show                # 只看记录
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RECORD = ROOT / "apps" / "ios" / "docs" / "验收记录.json"

# **哪些文件一改就让 iOS 记录失效**（判断"陈旧"的口径，必须写清，否则校验会变成玄学）
#
# 为什么是这些：
#   · apps/ios/App/**           —— app 源码本身
#   · apps/ios/**               —— 工程文件 / 资源 / 文档（Xcode 工程配置也在内）
#   · apps/shared/**            —— KMP 共享逻辑：iOS framework 就是它编出来的，
#                                  共享层一改，iOS 的行为可能跟着变（这是最容易漏的一项）
#   · apps/shared-core/**       —— 鸿蒙用的那份纯 KMP（同一份 commonMain 源码）
#   · scripts/ios*.sh           —— 验收脚本本身改了，旧结果的判据可能已经不同
#   · scripts/verify_all_ends.sh—— 验收总入口
#   · apps/ios/docs/验收记录.json 自己不算（否则每次记录都会让自己失效）
SENSITIVE_PREFIXES = (
    "apps/ios/",
    "apps/shared/",
    "apps/shared-core/",
    "scripts/ios.sh",
    "scripts/ios_app.sh",
    "scripts/verify_all_ends.sh",
)
SENSITIVE_EXACT = ("apps/shared/build.gradle.kts", "apps/shared-core/build.gradle.kts")


def git(*args: str) -> str:
    out = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    )
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败：{out.stderr.strip()}")
    return out.stdout.strip()


def head_sha() -> str:
    return git("rev-parse", "HEAD")


def changed_since(sha: str) -> list[str]:
    """自 `sha` 以来改动过的文件（含未提交改动 —— 这一条很重要：
    "记录之后我又改了代码但还没提交"同样让记录失效）。"""
    files: list[str] = []
    for rng in (f"{sha}..HEAD", "HEAD"):
        try:
            args = ["diff", "--name-only", rng] if ".." in rng else ["diff", "--name-only"]
            files += [f for f in git(*args).splitlines() if f.strip()]
        except RuntimeError:
            pass
    # 去重且保序
    seen: set[str] = set()
    out: list[str] = []
    for f in files:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def is_sensitive(path: str) -> bool:
    if path == "apps/ios/docs/验收记录.json":
        return False
    return path in SENSITIVE_EXACT or path.startswith(SENSITIVE_PREFIXES)


def load() -> dict:
    if not RECORD.exists():
        return {"记录": []}
    return json.loads(RECORD.read_text(encoding="utf-8"))


def save(data: dict) -> None:
    RECORD.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def parse_summary(text: str) -> dict | None:
    m = re.search(r"SEKB_IOS_SELFTEST PASS=(\d+) FAIL=(\d+) SKIP=(\d+)", text)
    if not m:
        return None
    return {"pass": int(m.group(1)), "fail": int(m.group(2)), "skip": int(m.group(3))}


def skipped_items(text: str) -> list[str]:
    return re.findall(r"SEKB_IOS_SELFTEST \[SKIP\] (\S+)", text)


def do_run() -> int:
    sys.stderr.write("── 跑 iOS app 自检（scripts/ios_app.sh run）──\n")
    sys.stderr.write("   几分钟量级：编 app → 起模拟器 → 装 → 跑（含 ONNX 模型 + 检索评测）\n")
    proc = subprocess.run(
        ["bash", str(ROOT / "scripts" / "ios_app.sh"), "run"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    out = proc.stdout + proc.stderr
    summary = parse_summary(out)
    if summary is None:
        sys.stderr.write(out[-3000:])
        sys.stderr.write("\n❌ 没抓到 SEKB_IOS_SELFTEST 汇总行 —— 不记录（避免把'没跑成'记成'跑过'）\n")
        return 1
    sha = head_sha()
    device = ""
    dev = re.search(r"模拟器：(\S+)", out)
    if dev:
        device = dev.group(1)
    entry = {
        "commit": sha,
        "时间": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "命令": "bash scripts/ios_app.sh run",
        **summary,
        "SKIP项": skipped_items(out),
        "设备": device,
    }
    data = load()
    # 同一个 commit 重复跑：覆盖而不是堆叠（记录要保持可读）
    data["记录"] = [e for e in data.get("记录", []) if e.get("commit") != sha]
    data["记录"].append(entry)
    save(data)
    verdict = "✅ PASS" if summary["fail"] == 0 else f"❌ FAIL={summary['fail']}"
    sys.stderr.write(
        f"\n{verdict}  PASS={summary['pass']} FAIL={summary['fail']} SKIP={summary['skip']}"
        f"  @ {sha[:12]}\n已写入 {RECORD.relative_to(ROOT)}\n"
    )
    return 0 if summary["fail"] == 0 else 1


def do_check(strict: bool) -> int:
    data = load()
    records = data.get("记录", [])
    if not records:
        msg = (
            "⚠️ iOS app 自检**没有记录**（apps/ios/docs/验收记录.json 是空的）\n"
            "   要补记录：bash scripts/ios_record.py run"
        )
        print(msg)
        return 1 if strict else 0
    latest = records[-1]
    sha = latest.get("commit", "")
    print(f"iOS app 自检记录：最新一条 @ {sha[:12]}  "
          f"PASS={latest.get('pass')} FAIL={latest.get('fail')} SKIP={latest.get('skip')}"
          f"  （{latest.get('时间')}）")
    if latest.get("fail", 1) != 0:
        print(f"❌ 最新一条记录本身是 FAIL={latest.get('fail')} —— 不是有效证据")
        return 1 if strict else 0

    # 记录里的 commit 还在不在（浅克隆/历史被改写时会取不到）
    try:
        git("cat-file", "-e", f"{sha}^{{commit}}")
    except RuntimeError:
        print(f"⚠️ 记录里的 commit {sha[:12]} 在当前仓库里找不到（浅克隆或历史被改写？）")
        return 1 if strict else 0

    files = [f for f in changed_since(sha) if is_sensitive(f)]
    if not files:
        print("✅ 记录仍然有效：自那次通过以来，iOS 相关文件没有被改过")
        return 0
    shown = files[:8]
    more = f"（还有 {len(files) - len(shown)} 个）" if len(files) > len(shown) else ""
    print(f"⚠️ 记录**已陈旧**：自 {sha[:12]} 以来有 {len(files)} 个 iOS 相关文件被改过：")
    for f in shown:
        print(f"     · {f}")
    if more:
        print(f"     {more}")
    print("   → 请重跑并记录：bash scripts/ios_record.py run")
    print("   （方案 C：默认不阻塞提交；发版/收尾时用 --strict 让它变成硬门禁）")
    return 1 if strict else 0


def do_show() -> int:
    data = load()
    records = data.get("记录", [])
    if not records:
        print("（没有记录）")
        return 0
    for e in records:
        print(f"{e.get('时间')}  {e.get('commit','')[:12]}  "
              f"PASS={e.get('pass')} FAIL={e.get('fail')} SKIP={e.get('skip')}  "
              f"SKIP项={e.get('SKIP项')}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="iOS app 自检的记录与校验（方案 C）")
    ap.add_argument("action", choices=["run", "check", "show"])
    ap.add_argument("--strict", action="store_true",
                    help="check 时有问题就退出码 1（发版/收尾用；默认非阻塞）")
    a = ap.parse_args()
    if a.action == "run":
        return do_run()
    if a.action == "check":
        return do_check(a.strict)
    return do_show()


if __name__ == "__main__":
    sys.exit(main())
