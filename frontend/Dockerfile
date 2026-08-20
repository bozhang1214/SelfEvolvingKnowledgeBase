# ============================================================
# 自迭代个人知识库 Agent - 前端生产镜像
# ============================================================
# 多阶段构建：
#   stage 1 (builder): node 环境，安装依赖并执行生产构建
#   stage 2 (runtime): nginx:alpine，仅托管 dist 静态资源 + 反向代理配置
#
# 生产部署架构：
#   - Nginx 托管 SPA 静态文件（/usr/share/nginx/html）
#   - Nginx 反向代理 /api/* 到后端容器
#   - 前端使用相对路径 /api/v1，由 Nginx 转发，无需 VITE_API_BASE
#
# 构建示例：
#   docker build -t self-evolving-kb-frontend:latest ./frontend
#
# 运行示例：
#   docker run --rm -p 80:80 \
#     -v ./deploy/nginx.conf:/etc/nginx/conf.d/default.conf:ro \
#     self-evolving-kb-frontend:latest
# ============================================================

# --------------- stage 1: builder ---------------
FROM node:20-alpine AS builder

WORKDIR /app

# 启用 pnpm 长缓存（可选）
ENV CI=true

# 先复制依赖清单，利用 Docker 层缓存
COPY package.json package-lock.json* ./

# 安装依赖（使用 npm ci 保证可复现构建；若无 lock 文件则回退到 npm install）
RUN if [ -f package-lock.json ]; then \
        npm ci --no-audit --no-fund; \
    else \
        npm install --no-audit --no-fund; \
    fi

# 复制源代码
COPY . .

# 生产构建：
# - 不设置 VITE_API_BASE：使前端走相对路径 /api/v1，由 Nginx 反向代理
# - 设置 NODE_ENV=production 触发 Vite 生产优化（代码压缩、tree-shaking）
ENV NODE_ENV=production

RUN npm run build

# --------------- stage 2: runtime ---------------
FROM nginx:alpine

# 清空默认配置，避免与自定义配置冲突
RUN rm -f /etc/nginx/conf.d/default.conf

# 复制构建产物到 nginx 托管目录
COPY --from=builder /app/dist /usr/share/nginx/html

# nginx 配置由外部挂载提供（deploy/nginx.conf），保持镜像与配置解耦：
# - 镜像只负责托管静态资源
# - 部署时通过 volume 挂载 nginx.conf，便于不同环境定制路由

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -q --spider http://localhost/ || exit 1

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
