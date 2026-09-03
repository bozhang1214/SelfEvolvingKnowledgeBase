# 通用浏览器登录/采集服务（Playwright + Chromium）
# 基础镜像自带 Playwright + Chromium，无需额外下载浏览器
FROM mcr.microsoft.com/playwright/python:v1.49.1-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir \
    -i https://mirrors.cloud.tencent.com/pypi/simple/ \
    --trusted-host mirrors.cloud.tencent.com \
    -r requirements.txt

COPY app.py .

ENV DATA_DIR=/data
EXPOSE 1300

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "1300"]
