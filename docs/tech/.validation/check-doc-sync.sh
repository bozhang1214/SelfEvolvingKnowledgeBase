#!/usr/bin/env bash
# ============================================================
# 文档同步检查（pre-commit hook 的一部分）
# ============================================================
# 目的：让「代码改动 → 文档更新」自动关联——当本次提交改动了
#       后端路由/配置/图/Agent/前端等代码时，检查对应技术文档
#       本次是否也同步更新（staged 中包含该文档）。
#
# 用法（pre-commit 调用，不传文件时从暂存区读取）：
#   bash docs/tech/.validation/check-doc-sync.sh [staged-files...]
#
# 退出码：0 = 无需提醒或已同步；1 = 需同步但未同步（默认仅提示不阻断）
# ============================================================
set -uo pipefail

# 代码路径前缀 → 应同步文档。兼容 bash 3.2：用 "前缀|文档" 配对，前缀从具体到宽泛。
# 注意：bash 3.2 没有 mapfile / 关联数组，此处避免二者。
declare -a RULES=(
  "backend/app/api/|docs/tech/05-API-REFERENCE.md"
  "backend/config.yaml|docs/tech/06-CONFIG-REFERENCE.md"
  "backend/app/core/config.py|docs/tech/06-CONFIG-REFERENCE.md"
  "backend/app/graph/|docs/tech/02-RUNTIME-FLOWS.md"
  "backend/app/agents/|docs/tech/03-MODULES.md"
  "backend/app/memory/|docs/tech/04-DATA-MODEL.md"
  "backend/app/storage/|docs/tech/04-DATA-MODEL.md"
  "backend/app/tools/|docs/tech/03-MODULES.md"
  "backend/app/core/metrics.py|docs/tech/09-OBSERVABILITY.md"
  "backend/app/core/logging.py|docs/tech/09-OBSERVABILITY.md"
  "docker-compose.monitoring.yml|docs/tech/09-OBSERVABILITY.md"
  "frontend/src/|docs/tech/03-MODULES.md"
  "backend/tests/|docs/tech/10-TESTING.md"
  "frontend/tests/|docs/tech/10-TESTING.md"
  "backend/requirements.txt|docs/tech/11-EVOLUTION.md"
  "frontend/package.json|docs/tech/11-EVOLUTION.md"
)

# 收集待检查文件（参数优先；否则从暂存区读）
FILES=("$@")
if [ "${#FILES[@]}" -eq 0 ]; then
  while IFS= read -r line; do
    [ -n "$line" ] && FILES+=("$line")
  done < <(git diff --cached --name-only 2>/dev/null)
fi
[ "${#FILES[@]}" -eq 0 ] && exit 0

MISSING=()
for f in "${FILES[@]}"; do
  # 忽略文档自身改动（改文档不需要再提醒文档）
  case "$f" in docs/*) continue;; esac
  for pair in "${RULES[@]}"; do
    prefix="${pair%%|*}"
    doc="${pair#*|}"
    case "$f" in
      "$prefix"*)
        # 本次提交是否也改了该文档
        staged_doc=0
        for sf in "${FILES[@]}"; do
          [ "$sf" == "$doc" ] && staged_doc=1
        done
        if [ "$staged_doc" -eq 0 ] && [ ! -f "$doc" ]; then
          MISSING+=("$f -> ${doc}（文档不存在，请新建）")
        elif [ "$staged_doc" -eq 0 ]; then
          MISSING+=("$f -> ${doc}（本次改动未同步更新该文档）")
        fi
        break
        ;;
    esac
  done
done

if [ "${#MISSING[@]}" -gt 0 ]; then
  echo "⚠️  [文档联动] 以下代码改动可能影响文档（建议同步更新，默认不阻断提交）："
  for m in "${MISSING[@]}"; do
    echo "    - $m"
  done
  echo "    更新方式：同步修改 ${doc} 相关章节，刷新 front-matter last-updated/based-on-commit，并在 docs/CHANGELOG.md 登记。"
  if [ "${DOC_SYNC_FORCE:-0}" = "1" ]; then
    exit 1
  fi
fi
exit 0
