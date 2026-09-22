#!/usr/bin/env bash
# ============================================================
# 机械检查：`$VAR` 紧邻非 ASCII 字符
# ============================================================
# bash 会把那个多字节字符的**首字节**并进变量名，于是报
#   `VAR\xef: unbound variable`
# 或者更糟：静默取到空值。错误信息看起来像乱码、不像变量名问题，排查成本高。
#
# ## 为什么值得写一个检查（而不是"记住就行"）
#
# 这个坑在**一个会话里踩了三次**：`scripts/ios_app.sh`（`$v（`）、
# `scripts/model_to_ms.sh`（`$MS_SHA）`）、`scripts/mac_app.sh`（`$APP_BUNDLE（`）。
# 三次都是"变量紧接着中文全角标点"。靠记忆已经失败三次 → 改成机器检查。
#
# 正确写法：`${VAR}（中文）`   错误写法：`$VAR（中文）`
#
# ## 为什么是**基线制**而不是"发现即失败"
#
# 全仓扫描会发现 ~18 处历史写法，大多在 `deploy/*`、`docs/tmp/*` 等**别人负责的共享文件**里，
# 且其中相当一部分位于**单引号或 `<<'EOF'` heredoc 内**——那里 bash 根本不展开变量，
# 属于**误报**（本检查是纯词法扫描，不解析引号上下文，无法区分）。
# 因此"发现即失败"会立刻把一个没人能修的绿灯变成红灯；与仓库既有的
# `backend/mypy-baseline.txt` 同一思路，这里也走**基线制**：只禁止**新增**。
#
# 用法：
#   bash scripts/lint_shell_unicode_vars.sh            # 与基线比较，超出则失败
#   bash scripts/lint_shell_unicode_vars.sh --list     # 只列出全部命中（含基线内的）
#   bash scripts/lint_shell_unicode_vars.sh --update   # 下调/调整基线到当前值
# ============================================================
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
BASELINE_FILE="scripts/.shell_unicode_vars_baseline"
SELF="scripts/lint_shell_unicode_vars.sh"

scan() {
    python3 - "$SELF" <<'PY'
import pathlib, re, sys
self_path = sys.argv[1]
pat = re.compile(rb'\$[A-Za-z_][A-Za-z0-9_]*(?=[\x80-\xff])')
for p in sorted(pathlib.Path('.').rglob('*.sh')):
    s = str(p)
    if any(x in s for x in ('/.git/', '/.tooling/', '/node_modules/', '/build/', '/docs/tmp/')):
        continue
    if s == self_path or s == './' + self_path:
        continue   # 本文件的注释/heredoc 里必然有示例，跳过自身
    data = p.read_bytes()
    for m in pat.finditer(data):
        line = data[:m.start()].count(b'\n') + 1
        frag = data[m.start():m.start()+20].decode('utf-8', 'replace').replace('\n', ' ')
        print(f"{s}:{line}\t{frag}")
PY
}

case "${1:-}" in
    --list)
        scan
        exit 0
        ;;
    --update)
        scan | wc -l | tr -d ' ' > "$BASELINE_FILE"
        echo "✅ 基线已更新为 $(cat "$BASELINE_FILE")"
        exit 0
        ;;
esac

BASELINE="$(cat "$BASELINE_FILE" 2>/dev/null || echo 0)"
NOW="$(scan | wc -l | tr -d ' ')"
echo "shell 全角变量名检查：当前 ${NOW} 处 / 基线 ${BASELINE} 处"

if [ "$NOW" -gt "$BASELINE" ]; then
    echo "❌ 新增了 $((NOW - BASELINE)) 处「变量紧邻非 ASCII」的写法："
    SCAN_ALL="$(scan)"
    printf '%s\n' "$SCAN_ALL" | tail -n "+$((BASELINE + 1))" | sed 's/^/   /'
    echo
    echo '改成 ${VAR}。若确认是单引号/heredoc 内的误报，可把基线调整到当前值：'
    echo "   bash scripts/lint_shell_unicode_vars.sh --update"
    exit 1
fi

if [ "$NOW" -lt "$BASELINE" ]; then
    echo "✅ 比基线少 $((BASELINE - NOW)) 处（可用 --update 下调基线）"
else
    echo "✅ 未超过基线"
fi
