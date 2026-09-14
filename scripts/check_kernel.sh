#!/usr/bin/env bash
# ============================================================
# JobCopilot 内核（git 子模块）状态自检
# ============================================================
# 解决一个子模块的固有风险：**静默过期**。
#   - `git pull` 拉完 SEKB，子模块纹丝不动，没有任何提示；
#   - 改完内核忘了回 SEKB 提交指针，部署用的还是旧内核。
#
# 本脚本把「内核到底是不是最新可用」变成一条命令能看清的事，
# 首次部署前、日常运行前都可以跑，任何人（含新机器、协作者）都适用。
#
# 用法：
#   bash scripts/check_kernel.sh          # 人类可读
#   bash scripts/check_kernel.sh --quiet  # 只在异常时输出（CI / 部署闸门用）
#
# 退出码：0=正常；1=有问题（未初始化 / commit 不一致 / 工作区脏 / 内核不可用）
# ============================================================
set -uo pipefail

QUIET=false
STRICT=false
for arg in "$@"; do
    case "$arg" in
        --quiet)  QUIET=true ;;
        --strict) STRICT=true ;;   # 警告也视为失败（部署闸门用）
    esac
done

GREEN=$'\033[0;32m'; RED=$'\033[0;31m'; YELLOW=$'\033[1;33m'
BLUE=$'\033[0;34m'; BOLD=$'\033[1m'; NC=$'\033[0m'

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

SUBMODULE_PATH="jobcopilot"
PROBLEMS=0
WARNINGS=0

say()  { $QUIET || echo -e "$1"; }
ok()   { say "  ${GREEN}✅${NC} $1"; }
bad()  { PROBLEMS=$((PROBLEMS + 1)); echo -e "  ${RED}❌${NC} $1"; }
warn() { WARNINGS=$((WARNINGS + 1)); echo -e "  ${YELLOW}⚠${NC} $1"; }
tip()  { echo -e "     ${YELLOW}→${NC} $1"; }

if ! $QUIET; then
    echo -e "${BLUE}============================================${NC}"
    echo -e "${BLUE}  JobCopilot 内核（git 子模块）状态检查${NC}"
    echo -e "${BLUE}============================================${NC}"
    echo ""
fi

# ---------- 1. 是否初始化 ----------
# git submodule status 首字符语义：
#   ' ' 正常 | '-' 未初始化 | '+' 已检出但与钉住的 commit 不符 | 'U' 冲突
STATUS_LINE="$(git submodule status "$SUBMODULE_PATH" 2>/dev/null || true)"

if [ -z "$STATUS_LINE" ]; then
    bad "SEKB 未登记 $SUBMODULE_PATH 子模块（.gitmodules 缺失或损坏）"
    tip "git submodule status   # 查看登记情况"
fi

PREFIX="${STATUS_LINE:0:1}"
# ⚠️ 不要用 `tr -d '+-U'` 去前缀：BSD（macOS）的 tr 会把 `+-U` 当成 ASCII **区间**
#    （`+`=43 到 `U`=85），区间内包含全部数字，结果把 SHA 里的数字全删掉。
#    用 sed 精确去掉首字符。
EXPECTED_SHA="$(printf '%s' "$STATUS_LINE" | sed -E 's/^[ +U-]//' | awk '{print $1}')"
ACTUAL_SHA="$(git -C "$SUBMODULE_PATH" rev-parse HEAD 2>/dev/null || echo "")"

if [ ! -f "$SUBMODULE_PATH/pyproject.toml" ]; then
    bad "内核子模块未初始化（$SUBMODULE_PATH/ 是空的）"
    tip "git submodule update --init"
    tip "首次 clone 建议直接：git clone --recurse-submodules <仓库地址>"
    echo ""
    echo -e "  ${RED}${BOLD}内核不可用，构建/运行一定失败。${NC}"
    exit 1
fi
ok "子模块已初始化"

# ---------- 2. commit 是否与 SEKB 钉住的一致 ----------
say ""
say "  ${BOLD}版本一致性${NC}"
say "  期望 commit  ${EXPECTED_SHA:-未知}  ${BLUE}(SEKB 钉住的版本)${NC}"
say "  实际 commit  ${ACTUAL_SHA:-未知}"

case "$PREFIX" in
    "+")
        bad "实际 commit 与 SEKB 钉住的不一致（内核被单独升级或回退了）"
        tip "git submodule update              # 对齐回 SEKB 钉住的版本（推荐）"
        tip "git submodule update --remote     # 确实要升级内核的话；完成后必须回 SEKB 提交指针，否则别人拉不到"
        ;;
    "U")
        bad "子模块存在合并冲突"
        tip "cd $SUBMODULE_PATH && git status   # 手工解决后再 git submodule update"
        ;;
    "-")
        bad "子模块未初始化"
        tip "git submodule update --init"
        ;;
    *)
        if [ "$EXPECTED_SHA" = "$ACTUAL_SHA" ]; then
            ok "与 SEKB 钉住的 commit 完全一致"
        else
            warn "无法比对（缺少钉住的 commit 信息）"
        fi
        ;;
esac

# ---------- 3. 工作区是否干净 ----------
say ""
say "  ${BOLD}工作区状态${NC}"
DIRTY="$(git -C "$SUBMODULE_PATH" status --porcelain 2>/dev/null | head -5)"
if [ -z "$DIRTY" ]; then
    ok "干净（部署/运行的就是钉住的那份代码）"
else
    warn "有未提交的本地改动 —— 部署进去的将不是钉住的版本"
    echo "$DIRTY" | sed 's/^/       /'
    tip "cd $SUBMODULE_PATH && git status   # 确认是否要提交/丢弃"
    tip "git submodule update --force       # 直接丢弃本地改动、对齐钉住版本"
fi

# ---------- 4. 内核内容可用性 ----------
say ""
say "  ${BOLD}内容可用性${NC}"

VERSION="$(grep -m1 '^version' "$SUBMODULE_PATH/pyproject.toml" 2>/dev/null | sed 's/.*"\(.*\)".*/\1/')"
say "  包版本       ${VERSION:-未知}"

PROMPT_DIR="$SUBMODULE_PATH/src/jobcopilot/core/prompts/base"
if [ -d "$PROMPT_DIR" ]; then
    PROMPT_COUNT="$(find "$PROMPT_DIR" -maxdepth 1 -name '*.md' | wc -l | tr -d ' ')"
    if [ "$PROMPT_COUNT" -ge 12 ]; then
        ok "内置提示词完整（$PROMPT_COUNT 份）"
    else
        bad "内置提示词不完整（只有 $PROMPT_COUNT 份，期望 ≥12）"
        tip "git submodule update --init --force"
    fi
else
    bad "找不到内置提示词目录：$PROMPT_DIR"
fi

# 提示词不得含个人数据（防泄漏护栏的运行时版）
if [ -f "$PROMPT_DIR/README.md" ]; then
    bad "base/ 里出现 README.md —— 该文件含个人画像，不得进开源包"
    tip "rm $PROMPT_DIR/README.md   # 并回内核仓库修正"
fi

# 能被 Python 导入才算真可用（可选，缺 python 不算错）
# 解释器优先用项目 venv（jobcopilot 装在这里），系统 python3 通常没有。
PY=""
for c in "$PROJECT_ROOT/backend/.venv/bin/python" \
         "$PROJECT_ROOT/.venv/bin/python" \
         "$SUBMODULE_PATH/.venv/bin/python" \
         "python3" "python"; do
    if [ -x "$c" ] || command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -n "$PY" ]; then
    if STEPS="$("$PY" -c "
import jobcopilot
from jobcopilot.core.prompts import base_dir
print(len(list(base_dir().glob('*.md'))))
" 2>/dev/null)"; then
        ok "内核可被 Python 导入（可见 ${STEPS} 份内置提示词）"
    else
        warn "选中的解释器（$PY）导入不到 jobcopilot（只做部署构建可忽略）"
        tip "cd $SUBMODULE_PATH && pip install -e '.[dev]'"
    fi
else
    warn "未找到 Python，跳过导入检查"
fi

# ---------- 结论 ----------
echo ""
if [ "$PROBLEMS" -eq 0 ] && [ "$WARNINGS" -eq 0 ]; then
    echo -e "  ${GREEN}${BOLD}✅ 内核状态正常，可以部署 / 运行${NC}"
    exit 0
elif [ "$PROBLEMS" -eq 0 ] && ! $STRICT; then
    echo -e "  ${YELLOW}${BOLD}⚠ 内核可用，但有 $WARNINGS 项警告（见上）${NC}"
    exit 0
elif [ "$PROBLEMS" -eq 0 ]; then
    echo -e "  ${RED}${BOLD}❌ 严格模式：有 $WARNINGS 项警告即视为失败${NC}"
    echo -e "  ${YELLOW}部署进去的将不是 SEKB 钉住的版本，请先按提示对齐。${NC}"
    exit 1
else
    echo -e "  ${RED}${BOLD}❌ 内核状态异常（$PROBLEMS 项问题），请按上面的提示修复后重试${NC}"
    exit 1
fi
