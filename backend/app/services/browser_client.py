"""通用浏览器服务客户端（sekb-browser）。

从 `api/routes/job.py` 内联的 `_call_browser` 提取（REFACTORING-PLAN WP2），
提供可配置 base_url 的 HTTP 客户端，便于单测与依赖注入。
"""

from __future__ import annotations

from typing import Any

import httpx


class BrowserClient:
    """调用通用浏览器服务（sekb-browser，BOSS 扫码登录等）。"""

    def __init__(self, base_url: str = "http://browser:1300") -> None:
        self._base_url = base_url.rstrip("/")

    async def post(
        self, path: str, payload: dict[str, Any], timeout: float = 30.0
    ) -> dict[str, Any]:
        """POST JSON 到浏览器服务，失败抛 ``httpx.HTTPError``。"""
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{self._base_url}{path}", json=payload)
            resp.raise_for_status()
            return resp.json()
