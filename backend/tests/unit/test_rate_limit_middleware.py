"""限流中间件（RateLimitMiddleware）的单元测试。"""

from __future__ import annotations

from app.api.middleware import RateLimitMiddleware


async def _run_request(mw: RateLimitMiddleware, path: str, client_ip: str = "1.2.3.4") -> list[dict]:
    """模拟一次 HTTP 请求，返回 send 收到的消息列表。"""
    scope = {"type": "http", "path": path, "method": "GET", "client": (client_ip, 12345)}
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await mw(scope, receive, send)
    return sent


def _make_mw(default_limit: int, route_limits: dict[str, int] | None = None) -> RateLimitMiddleware:
    """构造一个包装「返回 200」假应用的限流中间件。"""
    async def inner_app(_scope, _receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    return RateLimitMiddleware(
        inner_app, default_limit=default_limit, route_limits=route_limits
    )


def _status(sent: list[dict]) -> int:
    for m in sent:
        if m["type"] == "http.response.start":
            return m["status"]
    return 0


class TestMatchGroup:
    def test_longest_prefix_wins(self):
        mw = RateLimitMiddleware(None, default_limit=60, route_limits={
            "/api/v1/share/": 20,
            "/api/v1/auth/": 5,
        })
        assert mw._match_group("/api/v1/share/abc/chat/stream") == ("/api/v1/share/", 20)
        assert mw._match_group("/api/v1/auth/login") == ("/api/v1/auth/", 5)

    def test_default_fallback(self):
        mw = RateLimitMiddleware(None, default_limit=60, route_limits={"/api/v1/auth/": 5})
        assert mw._match_group("/api/v1/chat") == ("default", 60)

    def test_upload_prefix_matches_exact_and_subpath(self):
        mw = RateLimitMiddleware(None, default_limit=60, route_limits={"/api/v1/upload": 30})
        assert mw._match_group("/api/v1/upload") == ("/api/v1/upload", 30)
        assert mw._match_group("/api/v1/upload/batch") == ("/api/v1/upload", 30)


class TestRateLimitEnforcement:
    async def test_429_when_exceeded(self):
        mw = _make_mw(default_limit=2)
        await _run_request(mw, "/api/v1/chat")
        await _run_request(mw, "/api/v1/chat")
        sent = await _run_request(mw, "/api/v1/chat")
        assert _status(sent) == 429

    async def test_groups_independent(self):
        # auth 限 1 次，chat 限 5 次：auth 超限不应影响 chat 计数
        mw = _make_mw(default_limit=5, route_limits={"/api/v1/auth/": 1})
        # auth 用满
        assert _status(await _run_request(mw, "/api/v1/auth/login")) == 200
        assert _status(await _run_request(mw, "/api/v1/auth/login")) == 429
        # chat 不受 auth 计数影响（还能请求 5 次）
        assert _status(await _run_request(mw, "/api/v1/chat")) == 200

    async def test_non_api_passthrough(self):
        mw = _make_mw(default_limit=1)
        # 非 /api/ 路径不受限
        for _ in range(3):
            assert _status(await _run_request(mw, "/healthz")) == 200

    async def test_different_ips_isolated(self):
        mw = _make_mw(default_limit=1)
        assert _status(await _run_request(mw, "/api/v1/chat", client_ip="1.1.1.1")) == 200
        # 同一 IP 第二次超限
        assert _status(await _run_request(mw, "/api/v1/chat", client_ip="1.1.1.1")) == 429
        # 不同 IP 不受影响
        assert _status(await _run_request(mw, "/api/v1/chat", client_ip="2.2.2.2")) == 200
