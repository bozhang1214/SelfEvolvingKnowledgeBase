# 通用浏览器登录/采集服务（Playwright + Chromium）
# 用 python slim 基础镜像 + 国内镜像源装 Chromium，避免 mcr.microsoft.com 拉取慢
FROM python:3.11-slim

ARG APT_MIRROR=mirrors.cloud.tencent.com
ARG PIP_INDEX=https://mirrors.cloud.tencent.com/pypi/simple/

WORKDIR /app

# 配置国内 apt 源（debian bookworm 用 .sources；bullseye 用 sources.list）
RUN if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
      sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources; \
    else \
      sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list; \
    fi

# 装系统依赖 + playwright
COPY requirements.txt .
RUN pip install --no-cache-dir -i ${PIP_INDEX} \
      --trusted-host mirrors.cloud.tencent.com -r requirements.txt

# 用国内镜像下载 Chromium 并装系统依赖
ENV PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright/
RUN playwright install --with-deps chromium

COPY app.py .

ENV DATA_DIR=/data
EXPOSE 1300

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "1300"]
