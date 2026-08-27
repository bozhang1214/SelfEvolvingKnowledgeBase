#!/usr/bin/env python3
"""
迁移脚本：将 user_id="default" 的会话改为指定用户的真实 user_id。

用法（在云服务器项目根目录执行）：
    # 方式 1：在 backend 容器内执行
    docker exec sekb-backend python /app/deploy/migrate_default_user.py 56878097@qq.com

    # 方式 2：宿主机直接执行（需能访问 data 目录）
    python3 deploy/migrate_default_user.py 56878097@qq.com --data-dir /path/to/data

逻辑：
    1. 从 users.json 查找邮箱对应的真实 user_id
    2. 读取 index.json，将所有 user_id="default" 的会话改为真实 user_id
    3. 同时更新会话消息文件里的 user_id 字段（如果有）
    4. 备份原文件（.bak 后缀）
"""
from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path


def find_user_id_by_email(users_file: Path, email: str) -> str | None:
    """从 users.json 查找邮箱对应的 user_id。"""
    if not users_file.exists():
        print(f"[ERROR] 用户文件不存在: {users_file}")
        return None
    with open(users_file) as f:
        users = json.load(f)
    for uid, data in users.items():
        if data.get("email") == email:
            return uid
    print(f"[ERROR] 未找到邮箱 {email} 对应的用户")
    print(f"  已注册用户: {[d.get('email') for d in users.values()]}")
    return None


def migrate_index(index_file: Path, old_user_id: str, new_user_id: str) -> int:
    """迁移 index.json，返回修改的会话数。"""
    if not index_file.exists():
        print(f"[ERROR] index 文件不存在: {index_file}")
        return 0
    with open(index_file) as f:
        index = json.load(f)

    changed = 0
    for conv in index:
        if conv.get("user_id") == old_user_id:
            conv["user_id"] = new_user_id
            conv["updated_at"] = datetime.utcnow().isoformat()
            changed += 1
            print(f"  迁移会话: {conv.get('conv_id')} ({conv.get('title', '无标题')})")

    if changed > 0:
        # 备份原文件
        bak = index_file.with_suffix(".json.bak")
        shutil.copy2(index_file, bak)
        print(f"  已备份原文件到 {bak}")

        with open(index_file, "w") as f:
            json.dump(index, f, indent=2, default=str, ensure_ascii=False)

    return changed


def migrate_messages(conversations_dir: Path, old_user_id: str, new_user_id: str) -> int:
    """迁移会话消息文件里的 user_id 字段。"""
    if not conversations_dir.exists():
        print(f"[WARN] 会话目录不存在: {conversations_dir}")
        return 0

    changed = 0
    for msg_file in conversations_dir.glob("*.json"):
        try:
            with open(msg_file) as f:
                data = json.load(f)

            modified = False
            # 消息文件格式可能是 {"messages": [...]} 或直接是列表
            if isinstance(data, dict) and "messages" in data:
                messages = data["messages"]
            elif isinstance(data, list):
                messages = data
            else:
                continue

            for msg in messages:
                if msg.get("user_id") == old_user_id:
                    msg["user_id"] = new_user_id
                    modified = True

            if modified:
                bak = msg_file.with_suffix(".json.bak")
                shutil.copy2(msg_file, bak)
                with open(msg_file, "w") as f:
                    json.dump(data, f, indent=2, default=str, ensure_ascii=False)
                changed += 1
        except Exception as e:
            print(f"  [WARN] 跳过 {msg_file.name}: {e}")

    return changed


def main():
    if len(sys.argv) < 2:
        print("用法: python migrate_default_user.py <email> [--data-dir DIR]")
        print("示例: python migrate_default_user.py 56878097@qq.com")
        sys.exit(1)

    email = sys.argv[1]
    data_dir = Path("/app/data")  # 容器内默认路径

    # 解析 --data-dir 参数
    if "--data-dir" in sys.argv:
        idx = sys.argv.index("--data-dir")
        data_dir = Path(sys.argv[idx + 1])

    users_file = data_dir / "users.json"
    index_file = data_dir / "storage" / "index.json"
    conversations_dir = data_dir / "storage" / "conversations"

    print(f"[1/3] 查找邮箱 {email} 对应的 user_id...")
    new_user_id = find_user_id_by_email(users_file, email)
    if new_user_id is None:
        sys.exit(1)
    print(f"  找到 user_id: {new_user_id}")

    print(f"[2/3] 迁移 index.json（user_id: default → {new_user_id}）...")
    conv_count = migrate_index(index_file, "default", new_user_id)
    print(f"  迁移了 {conv_count} 个会话")

    print(f"[3/3] 迁移会话消息文件...")
    msg_count = migrate_messages(conversations_dir, "default", new_user_id)
    print(f"  迁移了 {msg_count} 个消息文件")

    print()
    print(f"[完成] 共迁移 {conv_count} 个会话，{msg_count} 个消息文件")
    if conv_count == 0 and msg_count == 0:
        print("  没有需要迁移的数据（可能已经是新版本，或旧会话已删除）")
    print()
    print("后续步骤:")
    print("  1. 重启 backend 容器: docker restart sekb-backend")
    print("  2. 刷新前端页面，历史对话应出现在列表中")


if __name__ == "__main__":
    main()
