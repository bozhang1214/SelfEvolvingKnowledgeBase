#!/usr/bin/env bash
# ============================================================
# SEKB 全量测试脚本（周期回归）
# ============================================================
# 用途：每两周或每月一次的完整回归（用户约定的测试节奏）。
# 相比增量测试（scripts/incremental_test.sh 每次提交前跑），
# 本脚本覆盖全部后端单测 + 集成测试 + 类型/风格门禁 + 前端全量。
#
# 用法：
#   bash scripts/full_test.sh          # 后端+前端全量
#   bash scripts/full_test.sh backend  # 仅后端
#   bash scripts/full_test.sh frontend # 仅前端
#
# 依赖：Docker（后端，sekb-toolbox 镜像自动构建）、本地 Node（前端）
# ============================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_IMAGE="sekb-toolbox:latest"
TOOLBOX_DEPS="ruff mypy pytest pytest-asyncio pytest-cov feedparser types-python-dateutil types-PyYAML types-requests"
SCOPE="${1:-all}"
cd "${ROOT}"

FAILED=0

# ---------- 后端全量 ----------
run_backend() {
  echo ""
  echo "========== [后端] 全量测试 =========="
  if ! docker image inspect "${BACKEND_IMAGE}" >/dev/null 2>&1; then
    echo "==> 构建测试镜像 ${BACKEND_IMAGE}（首次较慢）..."
    BASE_IMG="self-evolving-kb-backend:latest"
    docker image inspect "${BASE_IMG}" >/dev/null 2>&1 || { echo "❌ 缺少 ${BASE_IMG}"; exit 1; }
    CID="$(docker create "${BASE_IMG}" sh -c "pip install -q ${TOOLBOX_DEPS}")"
    docker start -a "${CID}" || exit 1
    docker commit "${CID}" "${BACKEND_IMAGE}" >/dev/null
    docker rm "${CID}" >/dev/null
  fi

  echo "--- ruff check app/ tests/ ---"
  docker run --rm -v "${ROOT}/backend:/bt" -w /bt "${BACKEND_IMAGE}" \
    python -m ruff check app/ tests/ || { echo "❌ ruff"; FAILED=1; }

  echo "--- mypy 回归门禁 ---"
  docker run --rm -v "${ROOT}/backend:/bt" -w /bt "${BACKEND_IMAGE}" \
    bash scripts/mypy_gate.sh || { echo "❌ mypy"; FAILED=1; }

  echo "--- pytest tests/unit（含覆盖率汇总）---"
  docker run --rm -v "${ROOT}/backend:/bt" -w /bt "${BACKEND_IMAGE}" \
    python -m pytest tests/unit -q --cov=app --cov-report=term 2>&1 | tail -6 \
    || { echo "❌ unit"; FAILED=1; }

  echo "--- pytest tests/integration ---"
  docker run --rm -v "${ROOT}/backend:/bt" -w /bt "${BACKEND_IMAGE}" \
    python -m pytest tests/integration -q 2>&1 | tail -6 \
    || { echo "❌ integration"; FAILED=1; }
}

# ---------- 前端全量 ----------
run_frontend() {
  echo ""
  echo "========== [前端] 全量测试 =========="
  cd "${ROOT}/frontend"
  echo "--- npm ci ---"
  npm ci --no-audit --no-fund || { FAILED=1; }
  echo "--- tsc --noEmit ---"
  npx tsc --noEmit || { echo "❌ tsc"; FAILED=1; }
  echo "--- eslint . ---"
  npm run lint || { echo "❌ eslint"; FAILED=1; }
  echo "--- vitest run ---"
  npx vitest run 2>&1 | tail -6 || { echo "❌ vitest"; FAILED=1; }
  echo "--- production build ---"
  npm run build 2>&1 | tail -6 || { echo "❌ build"; FAILED=1; }
  cd "${ROOT}"
}

case "${SCOPE}" in
  backend)  run_backend;;
  frontend) run_frontend;;
  all)      run_backend; run_frontend;;
  *) echo "用法: bash scripts/full_test.sh [backend|frontend|all]"; exit 1;;
esac

echo ""
if [ "${FAILED}" -eq 0 ]; then
  echo "✅ 全量测试全部通过"
else
  echo "❌ 全量测试存在失败项（详见上方输出）"
  exit 1
fi
