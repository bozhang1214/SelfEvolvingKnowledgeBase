#!/usr/bin/env bash
# ============================================================
# BOSS 直聘扫码登录 CLI 脚本（APP 扫码流程，两步扫码）
# 用法：
#   SEKB_TOKEN=<你的登录token> BASE_URL=<后端地址> bash scripts/boss_qr_login.sh
# 说明：
#   - 脚本向后端请求生成 BOSS 登录二维码（PNG，保存到临时文件并自动打开）；
#   - 你用手机 BOSS App「扫一扫」这个二维码；
#   - 扫完第一张会换第二张码（BOSS 双码验证），再扫一次并在 App 上确认；
#   - 登录成功后自动把 Cookie 存到服务器，用于后续采集 BOSS 职位。
# 获取 SEKB_TOKEN：
#   打开 SEKB 网页 → F12 → Application → Local Storage → 找到 sekb_token 的值。
# ============================================================
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
TOKEN="${SEKB_TOKEN:-}"
QR_FILE="${TMPDIR:-/tmp}/boss_qr.png"

if [ -z "$TOKEN" ]; then
  echo "错误：请先设置 SEKB_TOKEN 环境变量（SEKB 网页 localStorage 里的 sekb_token）"
  exit 1
fi

PY=python3
command -v $PY >/dev/null 2>&1 || PY=python

# 把 data URL 解码成 PNG 文件
save_qr() {
  $PY - "$1" "$QR_FILE" <<'PYEOF'
import base64, sys
url = sys.argv[1]
out = sys.argv[2]
if url.startswith("data:image/png;base64,"):
    raw = url.split(",", 1)[1]
    with open(out, "wb") as f:
        f.write(base64.b64decode(raw))
elif url.startswith("data:image/svg"):
    with open(out, "w") as f:
        f.write(url)
else:
    # 普通 URL：直接打印，不保存
    sys.exit(3)
PYEOF
}

show_qr() {
  # 尝试用系统默认程序打开图片
  if command -v open >/dev/null 2>&1; then
    open "$QR_FILE" >/dev/null 2>&1 || true
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$QR_FILE" >/dev/null 2>&1 || true
  fi
}

echo "==> 正在生成 BOSS 登录二维码..."
RESP=$(curl -s -X POST "$BASE_URL/api/v1/job/boss/qr/start" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json")

QR_URL=$(echo "$RESP" | $PY -c "import sys,json; print(json.load(sys.stdin).get('qr_image_url',''))" 2>/dev/null || echo "")
QR_ID=$(echo "$RESP" | $PY -c "import sys,json; print(json.load(sys.stdin).get('qr_id',''))" 2>/dev/null || echo "")

if [ -z "$QR_ID" ]; then
  echo "启动失败，返回：$RESP"
  exit 1
fi

if save_qr "$QR_URL"; then
  show_qr
  echo "二维码已保存并打开：$QR_FILE"
else
  echo "二维码（复制到浏览器打开）："
  echo "$QR_URL"
fi

echo ""
echo "请用手机 BOSS App「扫一扫」此二维码。扫完第一张会换第二张码，请再扫一次并在 App 上确认登录。"
echo ""
echo "==> 等待扫码确认（最多 3 分钟）..."

for i in $(seq 1 90); do
  ST=$(curl -s -X POST "$BASE_URL/api/v1/job/boss/qr/status" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"qr_id\":\"$QR_ID\"}")

  PHASE=$(echo "$ST" | $PY -c "import sys,json; print(json.load(sys.stdin).get('phase',''))" 2>/dev/null || echo "")
  NEW_QR=$(echo "$ST" | $PY -c "import sys,json; print(json.load(sys.stdin).get('qr_image_url',''))" 2>/dev/null || echo "")

  case "$PHASE" in
    waiting_scan)
      ;;
    waiting_second_scan)
      if [ -n "$NEW_QR" ]; then
        if save_qr "$NEW_QR"; then show_qr; fi
        echo "[$(date +%H:%M:%S)] 已扫描第一张码，请扫第二张新码并确认"
      fi
      ;;
    waiting_confirm)
      echo "[$(date +%H:%M:%S)] 已扫描，请在 BOSS App 上点击「确认登录」"
      ;;
    success)
      echo ""
      echo "✅ BOSS 登录成功，Cookie 已保存到服务器。"
      echo "$ST" | $PY -m json.tool 2>/dev/null || echo "$ST"
      exit 0
      ;;
    expired)
      echo ""
      echo "❌ 二维码已过期，请重新运行本脚本。"
      exit 1
      ;;
    login_failed)
      echo ""
      echo "❌ 登录失败：$ST"
      exit 1
      ;;
  esac

  sleep 2
done

echo ""
echo "❌ 等待扫码超时（3 分钟）。"
exit 1
