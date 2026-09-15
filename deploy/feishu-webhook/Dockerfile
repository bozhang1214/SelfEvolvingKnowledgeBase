FROM python:3.11-slim

WORKDIR /app

# 镜像源：与后端镜像一致走腾讯云镜像（VPC 内自动走内网）。
# 为什么必须给：deploy.sh 现在每次部署都 `up -d --build` 监控栈，构建缓存被清理时
# 这层会去公网 PyPI 拉 fastapi/pydantic 等（实测 60~90s，国内还常超时；
# docs/ops/07 的「故障 2」正是把 pip 超时列为已知故障）。换平台可用 --build-arg 覆盖。
ARG PIP_INDEX=https://mirrors.cloud.tencent.com/pypi/simple/
RUN pip config set global.index-url "$PIP_INDEX" \
    && pip install --no-cache-dir fastapi uvicorn httpx

COPY feishu_gateway.py .

EXPOSE 5001

CMD ["uvicorn", "feishu_gateway:app", "--host", "0.0.0.0", "--port", "5001"]
