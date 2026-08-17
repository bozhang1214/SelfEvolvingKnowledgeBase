"""
MCP Client 统一封装模块

提供 MCP（Model Context Protocol）客户端的统一封装，支持：
- 子进程模式（stdio）：通过启动子进程并使用标准输入输出通信
- HTTP 模式（SSE）：通过 HTTP SSE 连接远程 MCP Server
- 工具列表查询、工具调用、健康检查
- 转换为 LangChain Tool（依赖 langchain-mcp-adapters）

使用方式：
    # 子进程模式
    client = MCPClient(server_command=["python", "-m", "app.tools.mcp.bocha_server"])
    await client.connect()
    tools = await client.list_tools()
    result = await client.call_tool("web_search", {"query": "DeepSeek"})
    await client.disconnect()

    # 上下文管理器
    async with MCPClient(server_command=[...]) as client:
        tools = await client.list_tools()

    # HTTP 模式
    async with MCPClient(server_url="http://localhost:8000/sse") as client:
        tools = await client.list_tools()
"""

from __future__ import annotations

import json
from contextlib import AsyncExitStack
from typing import Any

from app.core.exceptions import MCPError
from app.core.logging import get_logger

logger = get_logger(__name__)

# ============================================================
# 依赖探测：优先使用 langchain-mcp-adapters，退回到 mcp 原生客户端
# ============================================================

try:
    from mcp import ClientSession, StdioServerParameters, stdio_client
    from mcp.client.sse import sse_client
    from mcp.client.stdio import get_default_environment

    _MCP_AVAILABLE = True
except ImportError:  # pragma: no cover - 依赖缺失时的兜底分支
    _MCP_AVAILABLE = False
    ClientSession = None  # type: ignore[assignment,misc]
    StdioServerParameters = None  # type: ignore[assignment,misc]
    stdio_client = None  # type: ignore[assignment,misc]
    sse_client = None  # type: ignore[assignment,misc]
    get_default_environment = None  # type: ignore[assignment,misc]

try:
    from langchain_mcp_adapters.client import load_mcp_tools

    _ADAPTERS_AVAILABLE = True
except ImportError:  # pragma: no cover - 依赖缺失时的兜底分支
    _ADAPTERS_AVAILABLE = False
    load_mcp_tools = None  # type: ignore[assignment,misc]


class MCPClient:
    """
    MCP 客户端统一封装。

    支持两种连接方式：
    - 子进程模式（stdio）：传入 server_command，客户端启动子进程并通过标准输入输出通信
    - HTTP 模式（SSE）：传入 server_url，客户端通过 HTTP SSE 连接远程 Server

    两种模式互斥，构造时必须且只能指定其一。

    使用上下文管理器（async with）可自动管理连接生命周期，也可手动调用
    connect()/disconnect()。

    Attributes:
        server_command: 子进程模式的启动命令（如 ["python", "-m", "app.tools.mcp.bocha_server"]）
        server_url: HTTP 模式的 Server URL（如 "http://localhost:8000/sse"）
        env: 子进程模式的环境变量（会与默认环境变量合并）
    """

    def __init__(
        self,
        server_command: list[str] | None = None,
        server_url: str | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        """
        初始化 MCP 客户端。

        Args:
            server_command: 子进程模式的启动命令列表，如 ["python", "-m", "mymcp.server"]
            server_url: HTTP 模式的 Server URL，如 "http://localhost:8000/sse"
            env: 子进程模式的额外环境变量（会与默认环境变量合并后传给子进程）
                典型用途：向 MCP Server 传递 API Key 等敏感配置

        Raises:
            MCPError: 未安装 mcp 库，或同时指定/未指定 server_command 和 server_url
        """
        if not _MCP_AVAILABLE:
            raise MCPError(
                "mcp 库未安装，请先执行 `pip install mcp`",
                tool_name="MCPClient",
            )

        # 校验：子进程模式和 HTTP 模式互斥，必须指定其一
        if server_command and server_url:
            raise MCPError(
                "不能同时指定 server_command 和 server_url，请二选一",
                tool_name="MCPClient",
            )
        if not server_command and not server_url:
            raise MCPError(
                "必须指定 server_command（子进程模式）或 server_url（HTTP 模式）",
                tool_name="MCPClient",
            )

        self.server_command = server_command
        self.server_url = server_url
        self.env = env

        # 运行时状态
        self._session: ClientSession | None = None
        self._exit_stack: AsyncExitStack | None = None
        self._connected = False

        logger.debug(
            "MCPClient 初始化",
            mode="stdio" if server_command else "sse",
            server_command=server_command,
            server_url=server_url,
        )

    # ============================================================
    # 连接管理
    # ============================================================

    async def connect(self) -> None:
        """
        建立 MCP 连接并完成初始化握手。

        根据构造参数自动选择 stdio 或 SSE 传输方式，建立传输层连接后
        创建 ClientSession 并完成 MCP 协议的 initialize 握手。

        Raises:
            MCPError: 已连接、或连接建立/握手失败
        """
        if self._connected:
            raise MCPError("MCP 客户端已连接，请勿重复调用 connect()", tool_name="MCPClient")

        # 使用 AsyncExitStack 统一管理嵌套的异步上下文管理器
        # （stdio_client/sse_client → ClientSession 两层），便于 disconnect 时统一清理
        self._exit_stack = AsyncExitStack()

        try:
            if self.server_command:
                session = await self._connect_stdio()
            else:
                session = await self._connect_sse()

            # 完成 MCP 协议握手
            await session.initialize()
            self._session = session
            self._connected = True

            logger.info(
                "MCP 连接建立",
                mode="stdio" if self.server_command else "sse",
            )
        except MCPError:
            await self._cleanup()
            raise
        except Exception as e:
            await self._cleanup()
            raise MCPError(
                f"MCP 连接建立失败: {e}",
                tool_name="MCPClient",
            ) from e

    async def _connect_stdio(self) -> ClientSession:
        """子进程模式：启动子进程并建立 ClientSession"""
        assert self.server_command is not None
        assert self._exit_stack is not None

        # 合并默认环境变量与用户传入的环境变量
        # 注意：StdioServerParameters.env 若指定，会替换（而非合并）子进程环境，
        # 因此需要先用 get_default_environment() 拿到基础环境再合并
        merged_env = dict(get_default_environment())
        if self.env:
            merged_env.update(self.env)

        server_params = StdioServerParameters(
            command=self.server_command[0],
            args=self.server_command[1:],
            env=merged_env,
        )

        # stdio_client 是异步上下文管理器，返回 (read_stream, write_stream)
        read_stream, write_stream = await self._exit_stack.enter_async_context(
            stdio_client(server_params)
        )

        # ClientSession 也是异步上下文管理器
        session = await self._exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        return session

    async def _connect_sse(self) -> ClientSession:
        """HTTP 模式：通过 SSE 连接远程 Server 并建立 ClientSession"""
        assert self.server_url is not None
        assert self._exit_stack is not None

        # sse_client 是异步上下文管理器，返回 (read_stream, write_stream)
        read_stream, write_stream = await self._exit_stack.enter_async_context(
            sse_client(self.server_url)
        )

        session = await self._exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        return session

    async def disconnect(self) -> None:
        """
        断开 MCP 连接并释放资源。

        关闭 ClientSession 和传输层连接。可安全地多次调用。
        """
        await self._cleanup()
        logger.info("MCP 连接已断开")

    async def _cleanup(self) -> None:
        """内部清理逻辑：关闭 exit_stack 持有的所有资源"""
        self._session = None
        self._connected = False
        if self._exit_stack is not None:
            try:
                await self._exit_stack.aclose()
            except Exception as e:
                logger.warning("MCP 资源清理异常", error=str(e))
            finally:
                self._exit_stack = None

    # ============================================================
    # 工具操作
    # ============================================================

    async def list_tools(self) -> list[dict]:
        """
        列出 MCP Server 提供的所有工具。

        Returns:
            工具定义字典列表，每项包含 name、description、inputSchema 等字段

        Raises:
            MCPError: 未连接或查询失败
        """
        session = self._require_session()
        try:
            result = await session.list_tools()
            tools = [self._serialize_tool(tool) for tool in result.tools]
            logger.debug("MCP list_tools", count=len(tools))
            return tools
        except MCPError:
            raise
        except Exception as e:
            raise MCPError(
                f"MCP list_tools 失败: {e}",
                tool_name="MCPClient",
            ) from e

    async def call_tool(self, name: str, arguments: dict) -> Any:
        """
        调用 MCP Server 上的工具。

        Args:
            name: 工具名称
            arguments: 工具参数字典

        Returns:
            工具返回结果。若返回内容为单个文本且可解析为 JSON，则返回解析后的对象；
            否则返回原始文本内容（字符串）；若有多条内容，则返回字典列表。

        Raises:
            MCPError: 未连接、工具调用失败或服务端返回错误
        """
        session = self._require_session()
        try:
            result = await session.call_tool(name, arguments)

            # 服务端可通过 is_error 标识工具执行失败
            if getattr(result, "is_error", False):
                text = self._extract_text(result.content)
                raise MCPError(
                    f"MCP 工具调用返回错误: {text}",
                    tool_name=name,
                )

            content = result.content or []
            logger.debug("MCP call_tool", tool=name, content_count=len(content))

            # 单条文本内容：尝试 JSON 解析，便于上层直接使用结构化数据
            if len(content) == 1:
                text = self._extract_text(content)
                return self._try_parse_json(text)

            # 多条内容：返回序列化后的字典列表
            return [self._serialize_content(item) for item in content]
        except MCPError:
            raise
        except Exception as e:
            raise MCPError(
                f"MCP call_tool 失败: {e}",
                tool_name=name,
            ) from e

    async def health_check(self) -> bool:
        """
        健康检查：向 MCP Server 发送 ping 并等待响应。

        Returns:
            True 表示连接健康；False 表示连接异常或未连接
        """
        if not self._connected or self._session is None:
            return False
        try:
            await self._session.send_ping()
            return True
        except Exception as e:
            logger.warning("MCP 健康检查失败", error=str(e))
            return False

    async def get_langchain_tools(self) -> list[Any]:
        """
        将 MCP Server 上的工具转换为 LangChain Tool 列表。

        依赖 langchain-mcp-adapters 库；若未安装则抛出 MCPError。

        Returns:
            LangChain BaseTool 实例列表

        Raises:
            MCPError: 未连接、或 langchain-mcp-adapters 未安装
        """
        if not _ADAPTERS_AVAILABLE:
            raise MCPError(
                "langchain-mcp-adapters 未安装，无法转换为 LangChain Tool",
                tool_name="MCPClient",
            )
        session = self._require_session()
        try:
            tools = await load_mcp_tools(session)
            logger.debug("MCP get_langchain_tools", count=len(tools))
            return tools
        except MCPError:
            raise
        except Exception as e:
            raise MCPError(
                f"MCP get_langchain_tools 失败: {e}",
                tool_name="MCPClient",
            ) from e

    # ============================================================
    # 上下文管理器协议
    # ============================================================

    async def __aenter__(self) -> MCPClient:
        await self.connect()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.disconnect()

    # ============================================================
    # 内部工具方法
    # ============================================================

    def _require_session(self) -> ClientSession:
        """获取当前会话，未连接时报错"""
        if not self._connected or self._session is None:
            raise MCPError(
                "MCP 客户端未连接，请先调用 connect()",
                tool_name="MCPClient",
            )
        return self._session

    @staticmethod
    def _serialize_tool(tool: Any) -> dict:
        """将 MCP Tool 对象序列化为字典"""
        # MCP Tool 是 pydantic BaseModel，使用 model_dump 序列化
        if hasattr(tool, "model_dump"):
            data = tool.model_dump(by_alias=True, exclude_none=True)
        elif hasattr(tool, "dict"):
            data = tool.dict(by_alias=True, exclude_none=True)  # type: ignore[attr-defined]
        else:
            data = {
                "name": getattr(tool, "name", None),
                "description": getattr(tool, "description", None),
                "inputSchema": getattr(tool, "inputSchema", None),
            }
        return data

    @staticmethod
    def _extract_text(content: Any) -> str:
        """从 CallToolResult.content 中提取纯文本"""
        if not content:
            return ""
        # content 可能是列表或单个对象
        items = content if isinstance(content, list) else [content]
        texts = []
        for item in items:
            text = getattr(item, "text", None)
            if text is not None:
                texts.append(text)
        return "\n".join(texts)

    @staticmethod
    def _serialize_content(item: Any) -> dict:
        """将单个 content 对象序列化为字典"""
        if hasattr(item, "model_dump"):
            return item.model_dump(by_alias=True, exclude_none=True)
        if hasattr(item, "dict"):
            return item.dict(by_alias=True, exclude_none=True)  # type: ignore[attr-defined]
        return {"type": getattr(item, "type", "unknown"), "text": getattr(item, "text", "")}

    @staticmethod
    def _try_parse_json(text: str) -> Any:
        """尝试将文本解析为 JSON，失败则返回原始文本"""
        if not text:
            return text
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return text
