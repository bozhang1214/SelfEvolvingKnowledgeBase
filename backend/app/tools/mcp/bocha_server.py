"""
博查搜索 MCP Server 模块

将博查（Bocha）AI 搜索 API 封装为标准的 MCP（Model Context Protocol）Server，
提供 `web_search` 工具供 MCP Client 调用。

支持两种使用方式：
1. 作为独立进程运行（stdio 模式）：
    python -m app.tools.mcp.bocha_server
   环境变量 BOCHA_API_KEY 必须设置。

2. 编程式创建：
    from app.tools.mcp.bocha_server import create_bocha_server
    server = create_bocha_server(api_key="sk-xxx")
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())

工具返回格式：
    [{"title": "...", "url": "...", "snippet": "...", "source": "bocha"}, ...]
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import httpx

from app.core.exceptions import ToolError
from app.core.logging import get_logger

logger = get_logger(__name__)

# ============================================================
# 常量定义
# ============================================================

# 博查搜索 API 默认端点
_DEFAULT_ENDPOINT = "https://api.bochaai.com/v1/web-search"

# web_search 工具的输入 Schema（JSON Schema 格式）
_WEB_SEARCH_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "搜索查询关键词，支持自然语言",
        },
        "count": {
            "type": "integer",
            "description": "返回结果数量，默认 5",
            "default": 5,
            "minimum": 1,
            "maximum": 20,
        },
    },
    "required": ["query"],
}

# web_search 工具的描述
_WEB_SEARCH_DESCRIPTION = (
    "联网搜索工具，基于博查 AI 搜索 API。"
    "输入查询关键词，返回网页搜索结果列表（含标题、URL、摘要）。"
    "适用于需要获取最新信息、实时数据或外部知识的场景。"
)


# ============================================================
# 依赖探测：mcp 库
# ============================================================

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool

    _MCP_AVAILABLE = True
except ImportError:  # pragma: no cover - 依赖缺失时的兜底分支
    _MCP_AVAILABLE = False
    Server = None  # type: ignore[assignment,misc]
    stdio_server = None  # type: ignore[assignment,misc]
    TextContent = None  # type: ignore[assignment,misc]
    Tool = None  # type: ignore[assignment,misc]


# ============================================================
# 博查 API 调用
# ============================================================

async def _bocha_web_search(
    api_key: str,
    query: str,
    endpoint: str = _DEFAULT_ENDPOINT,
    count: int = 5,
    timeout_seconds: int = 10,
    max_retries: int = 2,
) -> list[dict[str, Any]]:
    """
    调用博查搜索 API 并返回标准化结果。

    Args:
        api_key: 博查 API Key
        query: 搜索查询关键词
        endpoint: API 端点 URL
        count: 返回结果数量
        timeout_seconds: 单次请求超时秒数
        max_retries: 最大重试次数（指数退避）

    Returns:
        标准化的搜索结果列表，每项包含：
        - title: 网页标题
        - url: 网页链接
        - snippet: 内容摘要
        - source: 固定为 "bocha"

    Raises:
        ToolError: API Key 缺失、网络超时、API 返回错误等
    """
    if not api_key:
        raise ToolError(
            "博查 API Key 未设置，请检查 BOCHA_API_KEY 环境变量或配置 tools.web_search.api_key",
            tool_name="web_search",
        )

    if not query or not query.strip():
        raise ToolError("搜索查询关键词不能为空", tool_name="web_search")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "query": query,
        "count": count,
        "summary": True,
    }

    # 指数退避重试
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.post(endpoint, headers=headers, json=payload)

            # 检查 HTTP 状态码
            if response.status_code >= 400:
                raise ToolError(
                    f"博查 API 返回 HTTP {response.status_code}: {response.text[:200]}",
                    tool_name="web_search",
                )

            data = response.json()
            return _parse_bocha_response(data)

        except httpx.TimeoutException as e:
            last_error = e
            logger.warning(
                "博查搜索请求超时，准备重试",
                query=query,
                attempt=attempt + 1,
                max_attempts=max_retries + 1,
                timeout=timeout_seconds,
            )
        except httpx.HTTPError as e:
            last_error = e
            logger.warning(
                "博查搜索请求失败，准备重试",
                query=query,
                attempt=attempt + 1,
                error=str(e),
            )
        except ToolError:
            # API 业务错误（如 4xx）不重试，直接抛出
            raise
        except Exception as e:
            last_error = e
            logger.warning(
                "博查搜索异常，准备重试",
                query=query,
                attempt=attempt + 1,
                error=str(e),
            )

        # 指数退避等待：1s, 2s, 4s, ...
        if attempt < max_retries:
            await asyncio.sleep(2**attempt)

    # 所有重试均失败
    raise ToolError(
        f"博查搜索在 {max_retries + 1} 次尝试后仍失败: {last_error}",
        tool_name="web_search",
    )


def _parse_bocha_response(data: dict[str, Any]) -> list[dict[str, Any]]:
    """
    解析博查 API 响应为标准化结果列表。

    博查 API 响应格式参考：
        {
          "code": 200,
          "data": {
            "webPages": {
              "value": [
                {"name": "标题", "url": "链接", "summary": "摘要", ...}
              ]
            }
          }
        }

    Args:
        data: 博查 API 返回的 JSON 数据

    Returns:
        标准化结果列表：[{"title": "...", "url": "...", "snippet": "...", "source": "bocha"}, ...]
    """
    # 兼容不同的响应结构
    # 1. 标准结构：data.webPages.value
    # 2. 直接返回 webPages.value
    # 3. 直接返回 value 列表
    web_pages = data.get("data", {}).get("webPages", {})
    if not web_pages:
        web_pages = data.get("webPages", {})

    raw_results: list[dict[str, Any]] = web_pages.get("value", [])
    if not raw_results and isinstance(data.get("data"), list):
        raw_results = data["data"]
    if not raw_results and isinstance(data.get("value"), list):
        raw_results = data["value"]

    standardized: list[dict[str, Any]] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        # 博查返回 name/url/summary，标准化为 title/url/snippet
        title = item.get("name") or item.get("title") or ""
        url = item.get("url") or item.get("link") or ""
        snippet = item.get("summary") or item.get("snippet") or item.get("description") or ""
        if not title and not url and not snippet:
            continue
        standardized.append({
            "title": title,
            "url": url,
            "snippet": snippet,
            "source": "bocha",
        })

    logger.debug(
        "博查搜索结果解析",
        raw_count=len(raw_results),
        standardized_count=len(standardized),
    )
    return standardized


# ============================================================
# MCP Server 创建
# ============================================================

def create_bocha_server(
    api_key: str | None = None,
    endpoint: str = _DEFAULT_ENDPOINT,
    max_results: int = 5,
    timeout_seconds: int = 10,
    max_retries: int = 2,
) -> Any:
    """
    创建并配置博查搜索 MCP Server。

    注册一个 `web_search` 工具，内部调用博查搜索 API。

    Args:
        api_key: 博查 API Key。若为 None，则从环境变量 BOCHA_API_KEY 读取
        endpoint: 博查 API 端点 URL
        max_results: 默认返回结果数量
        timeout_seconds: 单次请求超时秒数
        max_retries: 最大重试次数

    Returns:
        配置好的 mcp.server.Server 实例

    Raises:
        ToolError: mcp 库未安装
    """
    if not _MCP_AVAILABLE:
        raise ToolError(
            "mcp 库未安装，请先执行 `pip install mcp`",
            tool_name="web_search",
        )

    # 若未显式传入 api_key，从环境变量读取
    resolved_api_key = api_key if api_key is not None else os.getenv("BOCHA_API_KEY", "")

    server: Any = Server("bocha-search")

    @server.list_tools()  # type: ignore[misc]
    async def _list_tools() -> list[Any]:  # type: ignore[type-arg]
        """返回 MCP Server 支持的工具列表"""
        return [
            Tool(
                name="web_search",
                description=_WEB_SEARCH_DESCRIPTION,
                inputSchema=_WEB_SEARCH_INPUT_SCHEMA,
            )
        ]

    @server.call_tool()  # type: ignore[misc]
    async def _call_tool(name: str, arguments: dict[str, Any]) -> list[Any]:  # type: ignore[type-arg]
        """
        处理工具调用请求。

        Args:
            name: 工具名称
            arguments: 工具参数

        Returns:
            MCP TextContent 列表，文本为 JSON 格式的搜索结果

        Raises:
            ToolError: 未知工具或搜索失败
        """
        if name != "web_search":
            raise ToolError(f"未知工具: {name}", tool_name=name)

        query = arguments.get("query", "")
        count = arguments.get("count", max_results)

        logger.info("收到 web_search 调用", query=query, count=count)

        results = await _bocha_web_search(
            api_key=resolved_api_key,
            query=query,
            endpoint=endpoint,
            count=count,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )

        # MCP 工具返回 TextContent 列表，文本内容为 JSON 格式的搜索结果
        return [TextContent(type="text", text=json.dumps(results, ensure_ascii=False))]

    logger.info(
        "博查 MCP Server 创建",
        endpoint=endpoint,
        max_results=max_results,
        api_key_configured=bool(resolved_api_key),
    )
    return server


# ============================================================
# 独立进程入口（stdio 模式）
# ============================================================

def _configure_stdio_logging() -> None:
    """
    配置 stdio 模式下的日志输出到 stderr。

    MCP stdio 协议使用 stdout 传输 JSON-RPC 消息，任何写入 stdout 的非协议
    内容（如日志）都会破坏协议通信。因此子进程模式下必须将所有日志重定向到 stderr。

    本函数配置标准库 logging 和 structlog，确保日志仅输出到 stderr。
    """
    import logging
    import sys

    # 配置标准库 logging：仅输出到 stderr
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)

    # 配置 structlog 使用标准库 LoggerFactory（走标准库 logging → stderr）
    try:
        import structlog

        structlog.configure(
            processors=[
                structlog.contextvars.merge_contextvars,
                structlog.stdlib.add_logger_name,
                structlog.stdlib.add_log_level,
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.dev.ConsoleRenderer(),
            ],
            wrapper_class=structlog.stdlib.BoundLogger,
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=True,
        )
    except ImportError:  # pragma: no cover - structlog 未安装时退化为标准库 logging
        pass


async def main() -> None:
    """
    独立进程入口：以 stdio 模式运行博查搜索 MCP Server。

    环境变量：
        BOCHA_API_KEY: 博查 API Key（必需）
        BOCHA_ENDPOINT: API 端点（可选，默认为官方端点）
        BOCHA_MAX_RESULTS: 默认返回结果数（可选，默认 5）
        BOCHA_TIMEOUT_SECONDS: 请求超时秒数（可选，默认 10）
        BOCHA_MAX_RETRIES: 最大重试次数（可选，默认 2）
    """
    # 重要：stdio 模式下必须将日志重定向到 stderr，避免污染 stdout 的 MCP 协议通道
    _configure_stdio_logging()

    # 从环境变量读取配置
    api_key = os.getenv("BOCHA_API_KEY", "")
    endpoint = os.getenv("BOCHA_ENDPOINT", _DEFAULT_ENDPOINT)
    max_results = int(os.getenv("BOCHA_MAX_RESULTS", "5"))
    timeout_seconds = int(os.getenv("BOCHA_TIMEOUT_SECONDS", "10"))
    max_retries = int(os.getenv("BOCHA_MAX_RETRIES", "2"))

    server = create_bocha_server(
        api_key=api_key,
        endpoint=endpoint,
        max_results=max_results,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
    )

    # stdio 模式：从标准输入读取请求，向标准输出写入响应
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
