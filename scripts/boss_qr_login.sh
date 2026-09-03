#!/usr/bin/env bash
# ============================================================
# BOSS 直聘扫码登录 CLI 脚本
# 用法：
#   SEKB_TOKEN=<你的登录token> BASE_URL=<后端地址> bash scripts/boss_qr_login.sh
# 说明：
#   - 脚本先向后端请求生成 BOSS 登录二维码，打印二维码图片 URL；
#   - 你用手机 BOSS App 扫这个二维码并确认登录；
#   - 脚本阻塞等待，登录成功后自动把 Cookie 存到服务器，用于后续采集 BOSS 职位。
# 获取 SEKB_TOKEN：
#   打开 SEKB 网页 → F12 → Application → Local Storage → 找到 sekb_token 的值。
# ============================================================
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
TOKEN="${SEKB_TOKEN:-}"

if [ -z "$TOKEN" ]; then
  echo "错误：请先设置 SEKB_TOKEN 环境变量（SEKB 网页 localStorage 里的 sekb_token）"
  exit 1
fi

PY=python3
command -v $PY >/dev/null 2>&1 || PY=python

echo "==> 正在生成 BOSS 登录二维码..."
RESP=$(curl -s -X POST "$BASE_URL/api/v1/job/boss/qr/start" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json")

QR_URL=$(echo "$RESP" | $PY -c "import sys,json; print(json.load(sys.stdin).get('qr_image_url',''))" 2>/dev/null || echo "")
QR_ID=$(echo "$RESP" | $PY -c "import sys,json; print(json.load(sys.stdin).get('qr_id',''))" 2>/dev/null || echo "")

if [ -z "$QR_URL" ] || [ -z "$QR_ID" ]; then
  echo "启动失败，返回：$RESP"
  exit 1
fi

echo ""
echo "请用手机 BOSS App「扫一扫」下面的二维码（把链接复制到浏览器打开）："
echo ""
echo "    $QR_URL"
echo ""

echo "==> 等待扫码确认（最多 3 分钟）..."
RESULT=$(curl -s -X POST "$BASE_URL/api/v1/job/boss/qr/complete" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"qr_id\":\"$QR_ID\",\"timeout_seconds\":180}")

echo ""
echo "结果："
echo "$RESULT" | $PY -m json.tool 2>/dev/null || echo "$RESULT"
