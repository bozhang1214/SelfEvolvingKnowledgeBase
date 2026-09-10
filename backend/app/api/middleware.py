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
    基于 IP + 路由分组的滑动窗口限流（纯 ASGI 实现）。

    - 按 (client_ip, 路由组) 粒度独立计数，避免不同组互相影响；
    - 路由组用最长前缀匹配（如 ``/api/v1/share/`` 覆盖所有分享子路径）；
    - 不使用 BaseHTTPMiddleware，避免缓冲 SSE 流式响应。
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
        # route_limits: 前缀 -> 每分钟上限；按前缀长度降序排列以便最长匹配
        self.route_limits = sorted(
            (route_limits or {}).items(), key=lambda kv: -len(kv[0])
        )
        self._windows: dict[tuple[str, str], list[float]] = defaultdict(list)

    def _match_group(self, path: str) -> tuple[str, int]:
        """返回 (分组键, 上限)，最长前缀优先，无匹配用默认。"""
        for prefix, limit in self.route_limits:
            if path.startswith(prefix):
                return prefix, limit
        return "default", self.default_limit

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

        # 按 (ip, 组) 独立计数
        group, limit = self._match_group(path)
        window = self.default_window
        now = time.time()
        key = (client_ip, group)

        # 清理过期记录
        self._windows[key] = [ts for ts in self._windows[key] if now - ts < window]

        # 检查是否超限
        if len(self._windows[key]) >= limit:
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
        self._windows[key].append(now)

        # 包装 send 以添加限流响应头
        original_send = send
        rate_limit_remaining = max(0, limit - len(self._windows[key]))

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = message.get("headers", [])
                headers.append((b"x-ratelimit-limit", str(limit).encode()))
                headers.append((b"x-ratelimit-remaining", str(rate_limit_remaining).encode()))
                message["headers"] = headers
            await original_send(message)

        await self.app(scope, receive, send_with_headers)
