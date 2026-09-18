#!/usr/bin/env python3
"""Gitea 推送镜像运维工具：查看状态 / 触发同步 / 重建（换 token 用）。

用法（在服务器上执行）：
    python3 scripts/gitea_mirror.py status     # 查看四个仓库的镜像状态（默认）
    python3 scripts/gitea_mirror.py sync       # 触发一次推送（同步到 GitHub）
    python3 scripts/gitea_mirror.py rebuild    # 用当前 token 重建镜像（token 轮换后用）

依赖：Gitea 访问 token（环境变量 GITEA_TOKEN，或 /home/<user>/.gitea-token）；
      GitHub token 从 /home/<user>/.github-token 读取（仅 rebuild 需要）。

⚠️ 两个实测踩过的坑，都在这里处理掉了：
  1. **触发同步的端点**：`POST /repos/{o}/{r}/mirror-sync` 是给**拉取镜像**用的，
     对推送镜像返回 `400 Repository is not a mirror`。推送镜像必须用
     `POST /repos/{o}/{r}/push_mirrors-sync`。
  2. **Gitea 仓库名 ≠ GitHub 仓库名**：Gitea 侧叫 `sekb`，GitHub 侧叫
     `SelfEvolvingKnowledgeBase`。用 Gitea 名拼 GitHub URL 会把镜像指到不存在的
     仓库，而且**不报错、静默失效**。所以这里用显式映射，不靠同名巧合。
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

GITEA_BASE = os.environ.get("GITEA_BASE", "http://localhost:3000/api/v1")
GITEA_OWNER = os.environ.get("GITEA_OWNER", "bo")
GITHUB_OWNER = os.environ.get("GITHUB_OWNER", "bozhang1214")
INTERVAL = os.environ.get("MIRROR_INTERVAL", "8h0m0s")

#: Gitea 仓库名 -> GitHub 仓库名（**必须显式写死**，切勿用同名假设）
REPO_MAP: dict[str, str] = {
    "sekb": "SelfEvolvingKnowledgeBase",
    "jobcopilot": "jobcopilot",
    "jobcopilot-prompts": "jobcopilot-prompts",
    "jobcopilot-dsh-plugin": "jobcopilot-dsh-plugin",
    # 注：sekb-ondevice-agent 已于 2026-09-18 删除——端侧代码并入主仓 apps/android/
    # （见 docs/RFC §17）。这里不再保留条目，否则 status 会对不存在的仓库报 ❌。
}

HOME = Path.home()


def gitea_token() -> str:
    """读 Gitea token：环境变量优先，其次 ~/.gitea-token。"""
    tok = os.environ.get("GITEA_TOKEN", "").strip()
    if tok:
        return tok
    f = HOME / ".gitea-token"
    if f.exists():
        return f.read_text(encoding="utf-8").strip()
    sys.exit("缺少 Gitea token：设置 GITEA_TOKEN 或写入 ~/.gitea-token")


def github_token() -> str:
    """读 GitHub token（仅 rebuild 需要）。"""
    f = HOME / ".github-token"
    if not f.exists():
        sys.exit(f"缺少 GitHub token：{f} 不存在")
    return f.read_text(encoding="utf-8").strip()


def api(path: str, method: str = "GET", payload: dict | None = None) -> object:
    """调用 Gitea API；HTTP 错误以 ``{"__http_error__": ...}`` 返回而不抛。"""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"{GITEA_BASE}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"token {gitea_token()}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode()
        return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        return {"__http_error__": e.code, "body": e.read().decode()[:200]}


def cmd_status() -> int:
    """打印四个仓库的镜像状态；地址不符或同步报错则返回非 0。"""
    bad = 0
    for gitea_name, gh_name in REPO_MAP.items():
        mirrors = api(f"/repos/{GITEA_OWNER}/{gitea_name}/push_mirrors")
        expected = f"https://github.com/{GITHUB_OWNER}/{gh_name}.git"
        if not isinstance(mirrors, list) or not mirrors:
            print(f"  ❌ {gitea_name:22s} 未配置推送镜像")
            bad += 1
            continue
        m = mirrors[0]
        err = (m.get("last_error") or "").strip().replace("\n", " ")
        addr_ok = m["remote_address"] == expected
        ok = addr_ok and not err
        print(f"  {'✅' if ok else '❌'} {gitea_name:22s} {m['remote_address']}")
        print(f"  {'':4s} 上次同步 {m.get('last_update')}")
        if not addr_ok:
            print(f"  {'':4s} ⚠️ 地址不符，期望 {expected}")
        if err:
            print(f"  {'':4s} 错误 {err[:160]}")
        if not ok:
            bad += 1
    return 1 if bad else 0


def _sync_once(gitea_name: str) -> bool:
    """触发一次同步并返回是否已无错误。"""
    api(f"/repos/{GITEA_OWNER}/{gitea_name}/push_mirrors-sync", method="POST")
    mirrors = api(f"/repos/{GITEA_OWNER}/{gitea_name}/push_mirrors")
    if not isinstance(mirrors, list) or not mirrors:
        return False
    err = (mirrors[0].get("last_error") or "").strip()
    return not err


def cmd_sync(retries: int = 3) -> int:
    """触发推送镜像同步（带重试），等待后打印结果。

    ⚠️ 为什么需要重试：实测会偶发
    ``PushRejected ... cannot lock ref 'refs/heads/main'``——这是**并发同步的锁竞争**
    （手动触发恰好撞上 ``sync_on_commit`` 或定时同步）。网络与配置都没问题，
    重试一次即恢复；把它当成真错误去改配置，反而会把镜像地址改坏。
    """
    for gitea_name in REPO_MAP:
        print(f"  {gitea_name:22s} 触发中 ...")
    print("  等待 35s 让 Gitea 完成推送 ...")
    time.sleep(35)

    for i in range(1, retries + 1):
        pending = [r for r in REPO_MAP if not _sync_once(r)]
        if not pending:
            print(f"  ✅ 全部同步成功（第 {i} 次尝试）")
            break
        print(f"  第 {i} 次仍有未完成: {pending}（偶发锁竞争，重试）")
        time.sleep(15)
    return cmd_status()


def cmd_rebuild() -> int:
    """用当前 GitHub token 重建四个镜像（**token 轮换后必做**）。"""
    gh = github_token()
    for gitea_name, gh_name in REPO_MAP.items():
        print(f"  === {gitea_name} -> {gh_name} ===")
        for m in api(f"/repos/{GITEA_OWNER}/{gitea_name}/push_mirrors"):
            if isinstance(m, dict) and "remote_name" in m:
                api(
                    f"/repos/{GITEA_OWNER}/{gitea_name}/push_mirrors/{m['remote_name']}",
                    method="DELETE",
                )
                print(f"    删除旧镜像（原地址 {m['remote_address']}）")
        res = api(
            f"/repos/{GITEA_OWNER}/{gitea_name}/push_mirrors",
            method="POST",
            payload={
                "remote_address": f"https://github.com/{GITHUB_OWNER}/{gh_name}.git",
                "remote_username": GITHUB_OWNER,
                "remote_password": gh,
                "interval": INTERVAL,
                "sync_on_commit": True,
            },
        )
        if isinstance(res, dict) and "remote_address" in res:
            print(f"    ✅ {res['remote_address']}")
        else:
            print(f"    ❌ 失败: {res}")
            return 1
    return 0


COMMANDS = {"status": cmd_status, "sync": cmd_sync, "rebuild": cmd_rebuild}

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd not in COMMANDS:
        sys.exit(f"未知命令 {cmd!r}；可用: {', '.join(COMMANDS)}")
    raise SystemExit(COMMANDS[cmd]())
