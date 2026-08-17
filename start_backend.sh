#!/bin/bash
#start_backend.sh
# 设置hugging-face镜像地址
export HF_ENDPOINT=https://hf-mirror.com

# 加载项目 ID
if [ -f ".env.project" ]; then
    source .env.project
fi
PROJECT_ID=${PROJECT_ID:-0}

BACKEND_PORT=$((8000 + PROJECT_ID))

echo "🚀 启动后端服务 (项目 ID=$PROJECT_ID)"
echo "后端端口: $BACKEND_PORT"

cd backend
# 安装依赖（如果 requirements.txt 存在）
if [ -f "requirements.txt" ]; then
    echo "安装 Python 依赖..."
    pip install -r requirements.txt
    if [ $? -ne 0 ]; then
        echo "❌ 依赖安装失败，请检查 requirements.txt"
        exit 1
    fi
fi

# 启动 uvicorn（前台运行，日志会输出到终端）
uvicorn app.api.main:app --host 0.0.0.0 --port $BACKEND_PORT --reload
