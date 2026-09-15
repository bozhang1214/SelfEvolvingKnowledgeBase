"""限流中间件（RateLimitMiddleware）的单元测试。"""

from __future__ import annotations

from app.api.middleware import RateLimitMiddleware


async def _run_request(
    mw: RateLimitMiddleware, path: str, client_ip: str = "1.2.3.4", method: str = "GET"
) -> list[dict]:
    """模拟一次 HTTP 请求，返回 send 收到的消息列表。"""
    scope = {"type": "http", "path": path, "method": method, "client": (client_ip, 12345)}
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


class TestMethodAwareGroups:
    """同一前缀下「读」和「写」成本差一个数量级，必须能分开放额度。

    真实背景：``/api/v1/news/`` 整组 10 次/分钟，把**读列表/读正文/状态轮询**也一起限死，
    前端翻两下页面就 429（owner 报的「加载周期报告列表失败 / 点第二次就失败」）。
    """

    LIMITS = {"/api/v1/news/": 120, "POST /api/v1/news/": 6}

    def test_post_uses_method_specific_group(self):
        mw = RateLimitMiddleware(None, default_limit=60, route_limits=self.LIMITS)
        assert mw._match_group("/api/v1/news/refresh", "POST") == ("POST /api/v1/news/", 6)
        assert mw._match_group("/api/v1/news/weekly", "POST") == ("POST /api/v1/news/", 6)

    def test_get_falls_back_to_prefix_group(self):
        mw = RateLimitMiddleware(None, default_limit=60, route_limits=self.LIMITS)
        assert mw._match_group("/api/v1/news/status", "GET") == ("/api/v1/news/", 120)
        assert mw._match_group("/api/v1/news/periodic/weekly", "GET") == ("/api/v1/news/", 120)

    def test_unknown_method_never_matches_method_group(self):
        """method 未知（""）时不能被塞进小额度的方法组里误限。"""
        mw = RateLimitMiddleware(None, default_limit=60, route_limits=self.LIMITS)
        assert mw._match_group("/api/v1/news/status", "") == ("/api/v1/news/", 120)

    def test_longer_prefix_still_wins_within_same_kind(self):
        mw = RateLimitMiddleware(None, default_limit=60, route_limits={
            "POST /api/v1/news/": 6,
            "POST /api/v1/news/refresh": 2,
        })
        assert mw._match_group("/api/v1/news/refresh", "POST") == ("POST /api/v1/news/refresh", 2)
        assert mw._match_group("/api/v1/news/weekly", "POST") == ("POST /api/v1/news/", 6)

    async def test_read_budget_not_consumed_by_generation(self):
        """关键行为：生成把额度用完，**读取不受影响**（两条计数互不干扰）。"""
        mw = _make_mw(default_limit=60, route_limits=self.LIMITS)
        for _ in range(6):  # POST 额度（6）用满
            await _run_request(mw, "/api/v1/news/refresh", method="POST")
        assert _status(await _run_request(mw, "/api/v1/news/refresh", method="POST")) == 429
        # 读还能正常用（这里只验证不被 POST 的计数拖累，读额度是 120）
        assert _status(await _run_request(mw, "/api/v1/news/status", method="GET")) == 200


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
