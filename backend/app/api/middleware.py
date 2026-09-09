"""
API 中间件：CORS、限流（Phase 3）

注意：限流中间件使用纯 ASGI 实现，而非 BaseHTTPMiddleware。
因为 BaseHTTPMiddleware 会缓冲整个响应体，破坏 SSE 流式传输。
"""
from __future__ import annotations

import json
import time
from collections import defaultdict

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_config


def setup_cors(app: FastAPI) -> None:
    """配置 CORS 中间件。"""
    config = get_config()
    origins = config.api.cors_origins or ["*"]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id", "X-RateLimit-Remaining"],
    )


class RateLimitMiddleware:
    """
    基于 IP 的简单限流中间件（纯 ASGI 实现）。

    使用滑动窗口计数，支持按路由分组配置不同速率。
    不使用 BaseHTTPMiddleware，避免缓冲 SSE 流式响应。
    """

    def __init__(
        self,
        app,
        default_limit: int = 60,
        default_window_seconds: int = 60,
        route_limits: dict[str, int] | None = None,
    ) -> None:
        self.app = app
        self.default_limit = default_limit
        self.default_window = default_window_seconds
        self.route_limits = route_limits or {}
        self._windows: dict[str, list[float]] = defaultdict(list)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "")

        # 非 API 路由或 OPTIONS 预检请求直接放行
        if not path.startswith("/api/") or method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        # 获取客户端 IP
        client = scope.get("client")
        client_ip = client[0] if client else "unknown"

        # 获取限流配置
        limit = self.route_limits.get(path, self.default_limit)
        window = self.default_window
        now = time.time()

        # 清理过期记录
        self._windows[client_ip] = [
            ts for ts in self._windows[client_ip]
            if now - ts < window
        ]

        # 检查是否超限
        if len(self._windows[client_ip]) >= limit:
            body = json.dumps({
                "code": 3001,
                "message": "请求过于频繁，请稍后再试",
                "data": None,
            }).encode()

            await send({
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"x-ratelimit-limit", str(limit).encode()),
                    (b"x-ratelimit-remaining", b"0"),
                    (b"retry-after", str(int(window)).encode()),
                ],
            })
            await send({
                "type": "http.response.body",
                "body": body,
            })
            return

        # 记录本次请求
        self._windows[client_ip].append(now)

        # 包装 send 以添加限流响应头
        original_send = send
        rate_limit_remaining = max(0, limit - len(self._windows[client_ip]))

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = message.get("headers", [])
                headers.append((b"x-ratelimit-limit", str(limit).encode()))
                headers.append((b"x-ratelimit-remaining", str(rate_limit_remaining).encode()))
                message["headers"] = headers
            await original_send(message)

        await self.app(scope, receive, send_with_headers)
