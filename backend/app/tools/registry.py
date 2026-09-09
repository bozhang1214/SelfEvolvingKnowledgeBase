"""
工具注册表模块

统一管理项目所有工具的生命周期与访问入口，遵循 MCP（Model Context Protocol）架构：

- 联网搜索：博查搜索，封装为 MCP Server，通过 MCP Client 调用，并转换为 LangChain Tool
- （后续阶段）文件系统、向量库、文档解析等工具将逐步接入

职责：
1. 启动并管理 MCP Server 子进程（如博查搜索 Server）
2. 通过 MCPClient 建立 MCP 连接
3. 将 MCP 工具转换为 LangChain Tool，供 Executor 直接调用
4. 提供工具健康检查与可用工具列表查询

使用方式：
    from app.core.config import get_config
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry(get_config())
    await registry.initialize()
    web_search = await registry.get_web_search_tool()
    result = await web_search.ainvoke({"query": "DeepSeek API"})
    await registry.shutdown()
"""

from __future__ import annotations

import json
import sys
from typing import Any, Callable

from app.core.config import AppConfig
from app.core.exceptions import MCPError, ToolError
from app.core.logging import get_logger
from app.tools.mcp.bocha_server import _bocha_web_search
from app.tools.mcp.client import MCPClient

logger = get_logger(__name__)

# langchain_core.tools.Tool 用于 MCP 不可用时的直接封装兜底
try:
    from langchain_core.tools import BaseTool
    from langchain_core.tools import tool as langchain_tool

    _LANGCHAIN_AVAILABLE = True
except ImportError:  # pragma: no cover - 依赖缺失时的兜底分支
    _LANGCHAIN_AVAILABLE = False
    BaseTool = None  # type: ignore[assignment,misc]
    langchain_tool = None  # type: ignore[assignment,misc]


# web_search 工具名（与 MCP Server 注册的工具名保持一致）
_WEB_SEARCH_TOOL_NAME = "web_search"

# web_search 工具描述（用于直接封装兜底场景）
_WEB_SEARCH_DESCRIPTION = (
    "联网搜索工具，基于博查 AI 搜索 API。"
    "输入查询关键词，返回网页搜索结果列表（含标题、URL、摘要）。"
    "适用于需要获取最新信息、实时数据或外部知识的场景。"
)


class ToolRegistry:
    """
    工具注册表。

    统一管理所有工具的生命周期：初始化（启动 MCP Server 子进程）→
    提供工具访问入口 → 关闭（释放资源）。

    当前管理：
    - web_search：博查联网搜索（MCP Server 模式，子进程 stdio 通信）

    Attributes:
        config: 应用全局配置
    """

    def __init__(self, config: AppConfig) -> None:
        """
        初始化工具注册表。

        Args:
            config: 应用全局配置
        """
        self.config = config

        # MCP 客户端（管理博查 MCP Server 子进程连接）
        self._mcp_client: MCPClient | None = None
        # 已加载的 LangChain Tool 缓存
        self._web_search_tool: Any = None
        # 兜底模式标记：MCP 不可用时直接封装博查 API
        self._use_direct_fallback = False
        # 初始化状态
        self._initialized = False

    # ============================================================
    # 生命周期管理
    # ============================================================

    async def initialize(self) -> None:
        """
        初始化所有工具：启动 MCP Server 子进程并建立连接。

        流程：
            1. 构造博查 MCP Server 的启动命令（子进程模式）
            2. 通过 MCPClient 启动子进程并建立 stdio 连接
            3. 从 MCP Server 加载 LangChain Tool

        若 MCP 连接失败，降级为直接封装博查 API 的 LangChain Tool
        （绕过 MCP，直接在进程内调用博查 API），保证系统可用性。

        Raises:
            ToolError: 初始化彻底失败（MCP 和兜底方式均不可用）
        """
        if self._initialized:
            logger.warning("ToolRegistry 已初始化，跳过重复初始化")
            return

        web_search_config = self.config.tools.web_search
        provider = web_search_config.provider

        logger.info("ToolRegistry 初始化开始", provider=provider)

        # 仅对 bocha 提供商启用 MCP Server 模式
        if provider == "bocha":
            try:
                await self._init_bocha_mcp()
            except Exception as e:
                logger.warning(
                    "博查 MCP 初始化失败，降级为直接封装模式",
                    error=str(e),
                    error_type=type(e).__name__,
                )
                self._init_direct_fallback()
        else:
            # 非博查提供商暂不支持，使用兜底
            logger.warning("不支持的搜索提供商，使用直接封装模式", provider=provider)
            self._init_direct_fallback()

        self._initialized = True
        logger.info(
            "ToolRegistry 初始化完成",
            mode="direct_fallback" if self._use_direct_fallback else "mcp",
        )

    async def _init_bocha_mcp(self) -> None:
        """启动博查 MCP Server 子进程并通过 MCPClient 建立连接"""
        web_search_config = self.config.tools.web_search

        # 构造子进程启动命令：python -m app.tools.mcp.bocha_server
        server_command = [sys.executable, "-m", "app.tools.mcp.bocha_server"]

        # 通过环境变量向子进程传递配置
        env = {
            "BOCHA_API_KEY": web_search_config.api_key,
            "BOCHA_ENDPOINT": web_search_config.endpoint,
            "BOCHA_MAX_RESULTS": str(web_search_config.max_results),
            "BOCHA_TIMEOUT_SECONDS": str(web_search_config.timeout_seconds),
            "BOCHA_MAX_RETRIES": str(web_search_config.max_retries),
        }

        self._mcp_client = MCPClient(server_command=server_command, env=env)
        await self._mcp_client.connect()

        # 从 MCP Server 加载 LangChain Tool
        try:
            tools = await self._mcp_client.get_langchain_tools()
        except MCPError as e:
            # langchain-mcp-adapters 不可用，尝试从 list_tools 查找
            logger.warning("get_langchain_tools 失败，尝试手动查找工具", error=str(e))
            tools = []

        # 在返回的工具列表中查找 web_search
        for tool in tools:
            if getattr(tool, "name", None) == _WEB_SEARCH_TOOL_NAME:
                self._web_search_tool = tool
                logger.info("博查 MCP web_search 工具已加载")
                return

        # 未找到 web_search 工具
        raise MCPError(
            f"MCP Server 未提供 {_WEB_SEARCH_TOOL_NAME} 工具",
            tool_name=_WEB_SEARCH_TOOL_NAME,
        )

    def _init_direct_fallback(self) -> None:
        """兜底模式：直接封装博查 API 为 LangChain Tool（绕过 MCP）"""
        if not _LANGCHAIN_AVAILABLE:
            raise ToolError(
                "langchain_core 未安装，无法创建 LangChain Tool",
                tool_name=_WEB_SEARCH_TOOL_NAME,
            )

        self._web_search_tool = _create_direct_web_search_tool(self.config)
        self._use_direct_fallback = True
        logger.info("博查搜索已以直接封装模式加载（绕过 MCP）")

    async def shutdown(self) -> None:
        """
        清理资源：断开 MCP 连接、终止子进程。

        可安全地多次调用。
        """
        if self._mcp_client is not None:
            try:
                await self._mcp_client.disconnect()
            except Exception as e:
                logger.warning("MCP 客户端断开异常", error=str(e))
            finally:
                self._mcp_client = None

        self._web_search_tool = None
        self._use_direct_fallback = False
        self._initialized = False
        logger.info("ToolRegistry 已关闭")

    # ============================================================
    # 工具访问
    # ============================================================

    async def get_web_search_tool(self) -> Callable[..., Any]:
        """
        获取联网搜索工具（LangChain Tool 格式）。

        若尚未初始化，会自动触发初始化。

        Returns:
            LangChain BaseTool 实例，可通过 .ainvoke({"query": "..."}) 调用

        Raises:
            ToolError: 工具未初始化或不可用
        """
        if not self._initialized:
            await self.initialize()

        if self._web_search_tool is None:
            raise ToolError(
                "web_search 工具未加载，请检查初始化日志",
                tool_name=_WEB_SEARCH_TOOL_NAME,
            )

        return self._web_search_tool

    async def list_available_tools(self) -> list[str]:
        """
        列出当前可用的工具名称。

        Returns:
            可用工具名列表（如 ["web_search"]）
        """
        if not self._initialized:
            await self.initialize()

        tools: list[str] = []
        if self._web_search_tool is not None:
            tools.append(_WEB_SEARCH_TOOL_NAME)
        return tools

    async def health_check(self) -> dict[str, bool]:
        """
        对所有工具执行健康检查。

        Returns:
            字典：{工具名: 是否健康}。MCP 模式下通过 ping 检查；
            直接封装模式下视为健康（实际可用性取决于网络与 API Key）。
        """
        result: dict[str, bool] = {}

        if not self._initialized:
            result[_WEB_SEARCH_TOOL_NAME] = False
            return result

        if self._use_direct_fallback:
            # 直接封装模式：无法 ping，视为健康
            result[_WEB_SEARCH_TOOL_NAME] = self._web_search_tool is not None
        elif self._mcp_client is not None:
            # MCP 模式：通过 ping 检查连接
            result[_WEB_SEARCH_TOOL_NAME] = await self._mcp_client.health_check()
        else:
            result[_WEB_SEARCH_TOOL_NAME] = False

        return result


# ============================================================
# 直接封装兜底：将博查 API 包装为 LangChain Tool
# ============================================================

def _create_direct_web_search_tool(config: AppConfig) -> Any:
    """
    创建直接封装博查 API 的 LangChain Tool（绕过 MCP）。

    用于 MCP Server 子进程不可用时的降级方案。

    Args:
        config: 应用全局配置

    Returns:
        LangChain BaseTool 实例
    """
    web_search_config = config.tools.web_search
    api_key = web_search_config.api_key
    endpoint = web_search_config.endpoint
    max_results = web_search_config.max_results
    timeout_seconds = web_search_config.timeout_seconds
    max_retries = web_search_config.max_retries

    @langchain_tool(_WEB_SEARCH_TOOL_NAME, description=_WEB_SEARCH_DESCRIPTION)
    async def web_search(query: str) -> str:
        """
        联网搜索工具，基于博查 AI 搜索 API。

        Args:
            query: 搜索查询关键词

        Returns:
            JSON 格式的搜索结果列表字符串
        """
        results = await _bocha_web_search(
            api_key=api_key,
            query=query,
            endpoint=endpoint,
            count=max_results,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )
        return json.dumps(results, ensure_ascii=False)

    return web_search
