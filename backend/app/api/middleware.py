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


def _parse_route_key(key: str) -> tuple[str, str, int]:
    """把 ``"POST /api/v1/news/"`` 拆成 (方法, 前缀, 是否带方法)。

    ``"/api/v1/news/"`` → ``("", "/api/v1/news/", 0)``（任意方法）。
    只认全大写的方法名，避免把某个以空格开头的前缀误认成方法。
    """
    parts = key.split(" ", 1)
    if len(parts) == 2 and parts[0].isupper():
        return parts[0], parts[1], 1
    return "", key, 0


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
    - **可选带方法**（如 ``"POST /api/v1/news/"``），带方法的组优先于纯前缀组；
    - 不使用 BaseHTTPMiddleware，避免缓冲 SSE 流式响应。

    为什么组要能带方法：同一前缀下「读」和「写」的成本能差一个数量级
    —— 读资讯列表/正文几乎不花钱，而 ``POST /api/v1/news/`` 会真的去调 LLM 生成报告。
    只按前缀分组就得二选一：要么把读也一起限死（用户翻两下页面就 429），
    要么放开写（生成接口被刷爆）。2026-09-15 的「加载周期报告列表失败 / 点第二次就 429」
    就是前者：``/api/v1/news/`` 整组只有 10 次/分钟，而前端状态轮询 + 读报告本身就要用掉。
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
        # route_limits: "前缀" 或 "方法 前缀" -> 每分钟上限；
        # 排序规则：带方法的排前面，其次前缀更长者优先（最长匹配）
        self.route_limits = sorted(
            (_parse_route_key(k) + (v,) for k, v in (route_limits or {}).items()),
            key=lambda item: (-item[2], -len(item[1])),
        )
        self._windows: dict[tuple[str, str], list[float]] = defaultdict(list)

    def _match_group(self, path: str, method: str = "") -> tuple[str, int]:
        """返回 (分组键, 上限)，最长前缀优先，无匹配用默认。

        ``method`` 为空串（未知方法）时只匹配不带方法的组，避免把未知方法
        误判进「生成」这种小额度组里被限死。
        """
        for req_method, prefix, is_method_specific, limit in self.route_limits:
            if not path.startswith(prefix):
                continue
            if is_method_specific and req_method != method:
                continue
            return (f"{req_method} {prefix}" if is_method_specific else prefix), limit
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
        group, limit = self._match_group(path, method)
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
