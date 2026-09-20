#!/usr/bin/env bash
# ============================================================
# 文档守卫（单一入口）—— 2026-09-16 技术文档复核后新增
# ============================================================
# 为什么要有这个入口（复盘结论）：
#   复核前有三个校验脚本，但**一个都没接在必经之路上**——
#     · check-links.sh     只扫 docs/tech/0*.md，其余目录的「✅」是假绿
#     · check-freshness.sh 从未接入 pre-commit/CI，告警没人看得见
#     · check-doc-sync.sh  默认 exit 0（只提示不阻断）
#   结果：12 篇技术文档的行号/事实可以静默漂移 200+ 个提交，
#   直到有人（或 owner）撞上才发现。本脚本把校验收到**一个入口**，
#   再由 CI / pre-commit 调用，让漂移在提交那一刻就红。
#
# 用法：
#   bash docs/tech/.validation/doc_guard.sh                # 全部检查
#   bash docs/tech/.validation/doc_guard.sh --no-warn      # 不列存档文档的提示
# 退出码：0 = 全绿；1 = 有必须修的问题
# ============================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${HERE}/../../.." && pwd)"
ROOT="$(cd "${HERE}/../../.." && pwd)"
cd "$ROOT"

PASS_ARGS=()
FAIL=0

echo "── 1/6 文档链接与代码引用（全仓 Markdown）──"
if command -v python3 >/dev/null 2>&1; then
    python3 "${HERE}/check-docs.py" "$@" || FAIL=1
else
    echo "⚠️  未找到 python3，跳过引用校验（CI 环境必须装 python3）"
fi

echo ""
echo "── 2/6 技术文档孤儿与 tech 内链接 ──"
bash "${HERE}/check-links.sh" || FAIL=1

echo ""
echo "── 3/6 文档新鲜度（last-updated / based-on-commit）──"
bash "${HERE}/check-freshness.sh" || FAIL=1

echo ""
echo "── 4/6 活数字一致性（同一事实多处不得取值矛盾）──"
if command -v python3 >/dev/null 2>&1; then
    python3 "${HERE}/check-facts.py" || FAIL=1
fi

echo ""
echo "── 5/6 端云协议三方一致（文档 ↔ 服务端路由 ↔ 端侧客户端）──"
if command -v python3 >/dev/null 2>&1; then
    python3 "${ROOT_DIR}/scripts/check_protocol_paths.py" || FAIL=1
fi

echo ""
echo "── 6/6 生成物与代码同步（T1 端点表）──"
if command -v python3 >/dev/null 2>&1; then
    python3 "${ROOT_DIR}/scripts/gen_facts_routes.py" --check || FAIL=1
fi

echo ""
if [ "$FAIL" -eq 0 ]; then
    echo "✅ 文档守卫全部通过"
else
    echo "❌ 文档守卫发现问题（见上）。修好后重跑：bash docs/tech/.validation/doc_guard.sh"
fi
exit "$FAIL"
