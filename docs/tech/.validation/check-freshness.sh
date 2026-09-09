#!/usr/bin/env bash
# ============================================================
# 文档新鲜度检查（定时 / CI 触发）
# ============================================================
# 目的：扫描 docs/tech/*.md 的 front-matter：
#   - last-updated 距今超过 THRESHOLD_DAYS（默认 90）→ 告警
#   - based-on-commit 与当前 HEAD 偏离超过 LOG_DEPTH（默认 200 commits）→ 告警
# 用法：bash .validation/check-freshness.sh [days] [log-depth]
# 退出码：0 = 全绿；1 = 存在过期文档（可配合 CI 告警，不阻断本地）
# ============================================================
set -uo pipefail

DAYS="${1:-90}"
LOG_DEPTH="${2:-200}"
DOC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HEAD="$(git -C "${DOC_DIR}/../.." rev-parse --short HEAD 2>/dev/null || echo unknown)"

STALE=0
for doc in "${DOC_DIR}"/0*.md; do
  [ -f "$doc" ] || continue
  name="$(basename "$doc")"
  updated="$(sed -n 's/^last-updated: *//p' "$doc" | head -1)"
  commit="$(sed -n 's/^based-on-commit: *//p' "$doc" | head -1)"
  if [ -n "$updated" ] && [ "$updated" != "TBD" ]; then
    if command -v python3 >/dev/null 2>&1; then
      days_old="$(python3 -c "
from datetime import date
try:
    y,m,d=map(int,'$updated'.split('-'))
    print((date.today()-date(y,m,d)).days)
except Exception:
    print(-1)")"
      if [ "$days_old" -gt "$DAYS" ]; then
        echo "⚠️  $name: last-updated=$updated 距今 ${days_old} 天（> ${DAYS}），请审阅是否过期"
        STALE=1
      fi
    fi
  fi
  if [ -n "$commit" ] && [ "$commit" != "TBD" ] && [ "$HEAD" != "unknown" ]; then
    behind="$(git -C "${DOC_DIR}/../.." rev-list --count "${commit}..HEAD" 2>/dev/null || echo 0)"
    if [ "${behind:-0}" -gt "$LOG_DEPTH" ]; then
      echo "⚠️  $name: based-on-commit=${commit} 落后当前 HEAD ${behind} commits（> ${LOG_DEPTH}）"
      STALE=1
    fi
  fi
done

if [ "$STALE" -eq 0 ]; then
  echo "✅ 文档新鲜度检查通过（HEAD=${HEAD}）"
fi
exit "$STALE"
