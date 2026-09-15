#!/usr/bin/env bash
# ============================================================
# mypy 回归门禁（Batch 1 测试工具集成）
# ============================================================
# 目标：mypy 的 strict 配置（backend/pyproject.toml [tool.mypy]）必须在
# backend 目录下执行才生效。存量类型债（基线 310 个错误）不允许一次性
# 阻断所有提交，但新增错误必须被拦住——本脚本对比当前错误数与基线文件，
# 超过基线即失败（回归门禁），等同或低于基线放行。
#
# 用法：在 backend 目录下执行  bash scripts/mypy_gate.sh
#   - 需已安装 mypy（CI: pip install mypy==<基线对应版本>）
#   - 基线文件：backend/mypy-baseline.txt（首次执行若缺失则自动写入当前计数）
#
# 存量债清除流程：
#   1. 修复一部分类型错误
#   2. 重跑 mypy，把新计数写回 mypy-baseline.txt 并提交
#   3. CI 即用更低基线拦住后续回归，直至 0 后改回直接阻断
# ============================================================
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASELINE_FILE="${HERE}/../mypy-baseline.txt"
MYPY_CMD=(python -m mypy app/ --ignore-missing-imports --no-error-summary)

# 前置检查：mypy 不可用时**必须失败**，不能静默放行。
# 实测过的坑：脚本内用 `python -m mypy`，若当前环境没有 mypy（例如本机没激活
# backend 的 venv），mypy 命令整体失败、错误行数为 0 —— 于是打印
# 「✅ 0 ≤ 310，无新增类型错误」。**门禁看起来是绿的，其实一次都没跑。**
if ! python -m mypy --version >/dev/null 2>&1; then
    echo "❌ 找不到 mypy（\`python -m mypy\` 不可用）：门禁无法执行，按失败处理。"
    echo "   请先安装/激活环境，例如： cd backend && . .venv/bin/activate  （或 pip install mypy）"
    exit 1
fi
echo "==> mypy $(python -m mypy --version 2>/dev/null || true)"

# 统计错误行数（error: 开头，不含 Success/Found 汇总行）
MYPY_OUT="$("${MYPY_CMD[@]}" 2>&1)"
MYPY_RC=$?
COUNT="$(printf '%s\n' "${MYPY_OUT}" | grep -cE '^[^:]+:[0-9]+: error:')"

# mypy 退出码语义：0=无错误，1=有类型错误（正常），>1=执行失败（配置/内部错误）
if [ "${MYPY_RC}" -gt 1 ]; then
    echo "❌ mypy 执行失败（退出码 ${MYPY_RC}）：门禁无法判定，按失败处理。"
    printf '%s\n' "${MYPY_OUT}" | tail -10
    exit 1
fi

if [ ! -f "${BASELINE_FILE}" ]; then
    echo "${COUNT}" > "${BASELINE_FILE}"
    echo "==> 首次运行：写入基线 ${BASELINE_FILE} = ${COUNT}"
    exit 0
fi

BASELINE="$(cat "${BASELINE_FILE}")"
echo "==> mypy 错误数: ${COUNT}（基线: ${BASELINE}）"

if [ "${COUNT}" -gt "${BASELINE}" ]; then
    echo "❌ mypy 错误数超过基线（${COUNT} > ${BASELINE}），存在新增类型错误，请修复。"
    printf '%s\n' "${MYPY_OUT}" | grep -E '^[^:]+:[0-9]+: error:' | head -30
    exit 1
fi
echo "✅ mypy 错误数 ≤ 基线（${COUNT} ≤ ${BASELINE}），无新增类型错误。"
