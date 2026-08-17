#start_frontend.sh
#!/bin/bash
set -e  # 遇到错误立即退出

echo "=== 开始启动前端服务 ==="
if [ -f ".env.project" ]; then
    source .env.project
fi
PROJECT_ID=${PROJECT_ID:-0}
FRONTEND_PORT=$((3000 + PROJECT_ID))
BACKEND_PORT=$((8000 + PROJECT_ID))   # 计算后端端口
echo "项目 ID: $PROJECT_ID, 前端端口: $FRONTEND_PORT, 后端端口: $BACKEND_PORT"

# 设置 Next.js 构建时环境变量（会被前端代码读取）
export NEXT_PUBLIC_API_BASE="http://localhost:${BACKEND_PORT}"
echo "NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE"

echo "当前目录: $(pwd)"
echo "切换到 frontend 目录..."
cd frontend || { echo "❌ frontend 目录不存在"; exit 1; }
echo "当前目录: $(pwd)，文件列表:"
ls -la

echo "运行 npm install..."
npm install --loglevel=info
echo "运行 npm run dev..."
npm run dev -- --port $FRONTEND_PORT
