# ============================================================
# 自迭代个人知识库 Agent - 后端生产镜像
# ============================================================
# 多阶段构建：
#   stage 1 (builder): 安装 Python 依赖到独立目录
#   stage 2 (runtime): 仅复制依赖与应用代码，最小化镜像体积
#
# 构建参数（通过 --build-arg 或 docker-compose.yml args 传入）：
#   APT_MIRROR  apt 镜像源（默认阿里云；腾讯云用 mirrors.cloud.tencent.com）
#   PIP_INDEX   pip 镜像源（默认阿里云；腾讯云用 https://mirrors.cloud.tencent.com/pypi/simple/）
#
# 构建示例：
#   docker build -t self-evolving-kb-backend:latest ./backend
#   docker build --build-arg APT_MIRROR=mirrors.cloud.tencent.com \
#                --build-arg PIP_INDEX=https://mirrors.cloud.tencent.com/pypi/simple/ \
#                -t self-evolving-kb-backend:latest ./backend
#
# 运行示例：
#   docker run --rm -p 8000:8000 \
#     -e DEEPSEEK_API_KEY=sk-xxx \
#     -e BOCHA_API_KEY=sk-xxx \
#     -e JWT_SECRET=your-secret \
#     -v sekb_data:/app/data \
#     self-evolving-kb-backend:latest
# ============================================================

# --------------- stage 1: builder ---------------
FROM python:3.11-slim AS builder

# 镜像源参数（默认阿里云；腾讯云构建时传腾讯云内网源）
ARG APT_MIRROR=mirrors.aliyun.com
ARG PIP_INDEX=https://mirrors.aliyun.com/pypi/simple/

WORKDIR /app

# 替换为国内镜像源，加速 apt 下载
RUN sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list 2>/dev/null || true

# 安装编译依赖（构建期临时使用）
# - gcc/build-essential: 编译 psycopg2、tiktoken、orjson 等 C 扩展
# - libpq-dev: PostgreSQL 客户端头文件（asyncpg/psycopg2 构建）
# - git: 个别包通过 git 安装
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        build-essential \
        libpq-dev \
        git \
    && rm -rf /var/lib/apt/lists/*

# 升级 pip 并安装 wheel 加速后续安装
# --trusted-host 避免 SSL 证书验证失败 fallback 到官方源
# --timeout 120 防止单个包下载超时卡住整个构建
RUN pip install --no-cache-dir -i ${PIP_INDEX} \
        --trusted-host mirrors.cloud.tencent.com \
        --trusted-host mirrors.aliyun.com \
        --timeout 120 --retries 3 \
        --upgrade pip wheel setuptools

# 先复制依赖清单，利用 Docker 层缓存（依赖变化少时跳过重装）
COPY requirements.txt ./

# ---------- 先装 CPU-only torch（必须在 requirements 之前）----------
# 为什么：torch 不是本文件的直接依赖，而是被 sentence-transformers **传递**拉进来的，
# 而 PyPI（及其国内镜像）上的 Linux torch 默认是 **CUDA 构建** —— 会额外拖进
# 19 个 nvidia-* 包（实测 site-packages/nvidia 占 3.2GB），而本机没有 GPU
# （torch.cuda.is_available() 为 False），纯属死重量。它还让每次构建的磁盘峰值
# 大幅上升（曾因此把 59G 磁盘挤到构建失败）。
#
# 顺序是**关键**：先装 `+cpu` 版本，后面 `-r requirements.txt` 看到 `torch>=x` 已被
# 满足就不会再去 PyPI 拉 CUDA 版。`+cpu` 这个本地版本号只存在于 PyTorch 官方
# CPU 索引，所以 pinned 写死即可，不会与镜像源歧义。
#
# --extra-index-url 用同一镜像源：torch 的运行时依赖（sympy/networkx/jinja2 等）
# 不在 PyTorch 索引上，必须能回落到常规源解析。
RUN pip install --no-cache-dir --prefix=/install \
        --index-url https://download.pytorch.org/whl/cpu \
        --extra-index-url ${PIP_INDEX} \
        --trusted-host mirrors.cloud.tencent.com \
        --trusted-host mirrors.aliyun.com \
        --timeout 120 --retries 3 \
        "torch==2.14.0+cpu"

# 安装依赖到独立前缀目录，便于 stage 2 整体复制
RUN pip install --no-cache-dir --prefix=/install \
        -i ${PIP_INDEX} \
        --trusted-host mirrors.cloud.tencent.com \
        --trusted-host mirrors.aliyun.com \
        --timeout 120 --retries 3 \
        -r requirements.txt

# 安装本地内核包 jobcopilot（独立仓库，通过**命名构建上下文**传入，
# 见 docker-compose.prod.yml 的 additional_contexts）。
# 顺序放在 requirements 之后：内核改动只失效这一层，依赖层缓存得以保留。
# 本地/服务器都需要在仓库根放一份 jobcopilot 检出（该目录已 gitignore）。
COPY --from=jobcopilot . /jobcopilot-src
RUN pip install --no-cache-dir --prefix=/install \
        -i ${PIP_INDEX} \
        --trusted-host mirrors.cloud.tencent.com \
        --trusted-host mirrors.aliyun.com \
        --timeout 120 --retries 3 \
        /jobcopilot-src

# --------------- stage 2: runtime ---------------
FROM python:3.11-slim

# 镜像源参数（需在每个 stage 重新声明）
ARG APT_MIRROR=mirrors.aliyun.com

# 内核（jobcopilot 子模块）的 commit，由 deploy.sh 传入。
# 烧进镜像后，容器启动日志与 /health 都能报出「线上跑的到底是哪个内核」——
# 这是子模块钉版本在**运行时**的可见性兜底（不用进容器翻文件）。
ARG JOBCOPILOT_COMMIT=unknown
ENV JOBCOPILOT_COMMIT=${JOBCOPILOT_COMMIT}

WORKDIR /app

# 替换为国内镜像源，加速 apt 下载
RUN sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list 2>/dev/null || true

# 安装运行期系统依赖：
# - libpq5: PostgreSQL 客户端运行库（asyncpg/psycopg2 运行期依赖）
# - libgomp1: PyTorch OpenMP 运行库（sentence-transformers 依赖）
# - curl: 容器健康检查用
# - ca-certificates: HTTPS 请求证书
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        libgomp1 \
        curl \
        ca-certificates \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

# 从 builder 复制已安装的 Python 依赖
COPY --from=builder /install /usr/local

# 显式安装 packaging（langchain_core 依赖；builder 中被 setuptools vendored 副本遮蔽，--prefix 不会复制）
RUN pip install --no-cache-dir packaging

# 复制应用代码
COPY app/ ./app/
COPY config.yaml ./
COPY pyproject.toml ./

# 创建非 root 用户并准备数据目录
# - appuser: UID 1000，避免以 root 运行容器
# - 数据目录：会话/索引/向量库/日志/trace 都写入 /app/data，挂载卷后可持久化
RUN useradd -m -u 1000 appuser \
    && mkdir -p \
        data/conversations \
        data/traces \
        data/eval_logs \
        data/logs \
        data/chroma_db \
        data/uploads \
    && chown -R appuser:appuser /app

USER appuser

# 健康检查：使用 /api/v1/health/live 存活探针（不依赖外部服务，避免误重启）
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/v1/health/live || exit 1

EXPOSE 8000

# 生产环境 uvicorn 启动命令：
# - --workers 1：单写进程，避免多进程共享同一 ChromaDB/SQLite 目录并发写损坏
#   （CON-01）、Prometheus 指标失真（OPS-01）、scheduler 重复执行（CON-03）。
#   个人知识库单 worker 吞吐足够；若未来需水平扩容，改为 Chroma client/server + 外置存储。
# - --no-access-log：关闭 access log（由应用 structlog 自行记录）
# - --proxy-headers：信任 Nginx 转发的 X-Forwarded-* 头
# - --forwarded-allow-ips：仅信任反代来源
CMD ["uvicorn", "app.api.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--no-access-log", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*"]
