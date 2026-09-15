# CHANGELOG 碎片

一次改动一个文件，发版时合并进 `docs/CHANGELOG.md`。

```bash
python3 scripts/changelog_add.py --title "标题"   # 新建
python3 scripts/changelog_merge.py --check        # 校验（提交前/CI）
python3 scripts/changelog_merge.py                # 合并 + 删除碎片
```

**为什么**：`CHANGELOG.md` 是冲突热点（所有人都往顶部插条目，必然冲突，
本项目已因此吃掉过 3 个条目标题）。碎片每人一个文件，天然不冲突。

格式：与 CHANGELOG 条目同构，且必须含 `## 日期（标题）` 标题行。
