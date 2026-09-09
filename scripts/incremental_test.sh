#!/usr/bin/env bash
# ============================================================
# SEKB 增量测试脚本
# ============================================================
# 用途：每次提交代码前对「本次改动涉及的范围」做快速回归，而不是
# 每次都跑全量（全量可每两周/每月跑一次，见 scripts/full_test.sh）。
#
# 用法：
#   bash scripts/incremental_test.sh            # 对比 HEAD~1，测改动范围
#   bash scripts/incremental_test.sh --staged   # 对比暂存区（提交前推荐）
#   bash scripts/incremental_test.sh --all      # 等价全量（可不带参跑该脚本）
#
# 范围映射：
#   backend/**/*.py   → ruff + mypy 回归门禁 + 改动涉及的 pytest 文件
#   frontend/**/*.{ts,tsx} → tsc + eslint（改动文件）+ vitest 相关测试
#   其余文件（docs 等）→ 仅静态检查跳过项说明
#
# 依赖：
#   - Docker（后端测试跑在 sekb-toolbox 镜像内，自带 ruff/mypy/pytest）
#   - 本地 Node（前端 tsc/eslint/vitest）
#
# 首次自动构建 toolbox 镜像（基于 self-evolving-kb-backend + 测试工具）。
# ============================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_IMAGE="sekb-toolbox:latest"
TOOLBOX_DEPS="ruff mypy pytest pytest-asyncio pytest-cov feedparser types-python-dateutil types-PyYAML types-requests"

cd "${ROOT}"

# ---------- 参数解析 ----------
BASE="HEAD~1"
case "${1:-}" in
  --staged) BASE="";;
  --all)    BASE="";;
  -h|--help) sed -n '1,30p' "$0"; exit 0;;
esac

if [ -n "${BASE}" ]; then
  CHANGED="$(git diff --name-only "${BASE}" HEAD)"
else
  CHANGED="$(git diff --cached --name-only)"
fi
[ -n "${CHANGED}" ] || { echo "✅ 无改动文件，无需增量测试"; exit 0; }

echo "========== 改动文件（$(echo "${CHANGED}" | wc -l | tr -d ' ') 个）=========="
echo "${CHANGED}" | sed 's/^/  /'

# ---------- 改动归类 ----------
BACKEND_PY="$(echo "${CHANGED}" | grep -E '^backend/.*\.py$' || true)"
FRONTEND_TS="$(echo "${CHANGED}" | grep -E '^frontend/.*\.(ts|tsx)$' || true)"

FAILED=0

# ---------- 后端增量测试（docker toolbox）----------
if [ -n "${BACKEND_PY}" ]; then
  echo ""
  echo "========== [后端] 增量测试 =========="
  if ! docker image inspect "${BACKEND_IMAGE}" >/dev/null 2>&1; then
    echo "==> 构建测试镜像 ${BACKEND_IMAGE}（首次较慢）..."
    BASE_IMG="self-evolving-kb-backend:latest"
    docker image inspect "${BASE_IMG}" >/dev/null 2>&1 || { echo "❌ 缺少 ${BASE_IMG}，请先构建后端镜像"; exit 1; }
    CID="$(docker create "${BASE_IMG}" sh -c "pip install -q ${TOOLBOX_DEPS}")"
    docker start -a "${CID}" || exit 1
    docker commit "${CID}" "${BACKEND_IMAGE}" >/dev/null
    docker rm "${CID}" >/dev/null
  fi

  # 1) ruff：只检查改动文件（行宽等规则配置在 backend/pyproject.toml）
  echo "--- ruff check（改动文件）---"
  FILES_REL="$(echo "${BACKEND_PY}" | sed 's#^backend/##' | tr '\n' ' ')"
  if ! docker run --rm -v "${ROOT}/backend:/bt" -w /bt "${BACKEND_IMAGE}" \
      python -m ruff check ${FILES_REL}; then
    echo "❌ ruff 失败"; FAILED=1
  fi

  # 2) mypy 回归门禁（全量类型检查，严格模式从 backend 目录生效）
  echo "--- mypy 回归门禁 ---"
  if ! docker run --rm -v "${ROOT}/backend:/bt" -w /bt "${BACKEND_IMAGE}" \
      bash scripts/mypy_gate.sh; then
    echo "❌ mypy 回归门禁失败"; FAILED=1
  fi

  # 3) pytest：改动涉及的测试文件 + 同模块相邻测试（无涉及时跑最小冒烟）
  PY_FILES="$(echo "${BACKEND_PY}" | sed 's#^backend/##')"
  TARGETS=""
  for f in ${PY_FILES}; do
    # 改动文件本身是测试 → 直接跑
    case "${f}" in
      tests/*) TARGETS="${TARGETS} ${f}";;
      app/*) 
        # 找同前缀测试：app/agents/news/*.py → tests/unit/test_news*.py
        MOD="$(basename "${f}" .py)"
        MATCH="$(ls tests/unit/test_${MOD}.py tests/integration/test_${MOD}.py 2>/dev/null || true)"
        if [ -n "${MATCH}" ]; then TARGETS="${TARGETS} ${MATCH}"; fi
        ;;
    esac
  done
  TARGETS="$(echo ${TARGETS} | tr ' ' '\n' | sort -u | tr '\n' ' ')"
  if [ -z "${TARGETS}" ]; then
    echo "（改动无直接对应测试文件，跳过 pytest；请补充增量测试用例）"
  else
    echo "--- pytest ${TARGETS} ---"
    if ! docker run --rm -v "${ROOT}/backend:/bt" -w /bt "${BACKEND_IMAGE}" \
        python -m pytest ${TARGETS} -q 2>&1 | tail -6; then
      echo "❌ pytest 失败"; FAILED=1
    fi
  fi
else
  echo ""
  echo "========== [后端] 无 .py 改动，跳过 =========="
fi

# ---------- 前端增量测试（本地 node）----------
if [ -n "${FRONTEND_TS}" ]; then
  echo ""
  echo "========== [前端] 增量测试 =========="
  cd "${ROOT}/frontend"

  echo "--- tsc --noEmit ---"
  if ! npx tsc --noEmit; then
    echo "❌ tsc 失败"; FAILED=1
  fi

  echo "--- eslint（改动文件）---"
  ESLINT_FILES="$(echo "${FRONTEND_TS}" | sed 's#^frontend/##' | tr '\n' ' ')"
  if ! npx eslint ${ESLINT_FILES}; then
    echo "❌ eslint 失败（可用 npm run lint 查看全量）"; FAILED=1
  fi

  echo "--- vitest（改动测试文件，无则全量）---"
  TEST_FILES="$(echo "${FRONTEND_TS}" | grep -E '\.test\.(ts|tsx)$' | sed 's#^frontend/##' | tr '\n' ' ')"
  if [ -n "${TEST_FILES}" ]; then
    npx vitest run ${TEST_FILES} 2>&1 | tail -6 || { echo "❌ vitest 失败"; FAILED=1; }
  else
    npx vitest run 2>&1 | tail -6 || { echo "❌ vitest 失败"; FAILED=1; }
  fi
  cd "${ROOT}"
else
  echo ""
  echo "========== [前端] 无 .ts/.tsx 改动，跳过 =========="
fi

echo ""
if [ "${FAILED}" -eq 0 ]; then
  echo "✅ 增量测试全部通过——可以提交"
else
  echo "❌ 增量测试有失败项——请修复后再提交"
  exit 1
fi
