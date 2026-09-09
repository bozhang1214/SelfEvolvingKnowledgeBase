#!/usr/bin/env bash
# 链接可达性 + 孤儿文档检查（docs/tech）
# 用法：bash docs/tech/.validation/check-links.sh
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/../../.." && pwd)"  # HERE=docs/tech/.validation → ROOT=仓库根
cd "$ROOT"
FAIL=0
# 收集 docs/tech 与 docs/ 下存在的 md
while IFS= read -r f; do :; done < /dev/null
# 检查 tech 内相对链接（绝对路径解析）
for f in docs/tech/0*.md; do
  while IFS= read -r link; do
    case "$link" in
      *.md|*\.md#*|../ops) ;;
      *) continue;;
    esac
    target="${link%%#*}"
    target="${target%%\\n*}"
    case "$target" in
      ../ops) resolved="$ROOT/docs/ops";;
      ../BACKLOG.md) resolved="$ROOT/docs/BACKLOG.md";;
      ../CHANGELOG.md) resolved="$ROOT/docs/CHANGELOG.md";;
      ./[0-9]*.md) resolved="$ROOT/docs/tech/$(basename "$target")";;
      ./.facts/*.md) resolved="$ROOT/docs/tech/.facts/$(basename "$target")";;
      ./*.md) resolved="$ROOT/docs/tech/$(basename "$target")";;
      ../*.md) resolved="$ROOT/docs/$(basename "$target")";;
      *) resolved="";;
    esac
    if [ -n "$resolved" ] && [ ! -d "$resolved" ] && [ ! -f "$resolved" ]; then
      echo "❌ $f -> 断链: $link (解析到 $resolved)"; FAIL=1
    fi
  done < <(grep -oE '\]\([^)]+\)' "$f" | sed -E 's/^\]\(//; s/\)$//')
done

# 孤儿检查：tech 下文档都在 00-README 地图
for f in docs/tech/0*.md; do
  b="$(basename "$f")"
  [ "$b" = "00-README.md" ] && continue
  stem="${b%.md}"
  grep -qF "$stem" docs/tech/00-README.md || { echo "❌ 孤儿文档(未在 00-README 列出): $f"; FAIL=1; }
done
[ "$FAIL" -eq 0 ] && echo "✅ 链接与孤儿检查通过"
exit $FAIL
