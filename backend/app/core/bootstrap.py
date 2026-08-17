"""
应用启动引导模块

集中初始化应用所有运行时组件，提供：
- AppContext：聚合所有组件引用的 dataclass
- initialize_app：异步初始化函数，完成依赖装配
- shutdown_app：资源清理函数

所有 CLI 命令与 API 服务都通过本模块完成依赖装配，确保初始化逻辑一致、可复用。

使用方式：
    from app.core.bootstrap import initialize_app, shutdown_app

    ctx = await initialize_app("config.yaml")
    try:
        # 使用 ctx.graph / ctx.llm_factory / ...
        ...
    finally:
        await shutdown_app(ctx)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agents.strategies.base import ReflectionStrategy
from app.agents.strategies.factory import create_reflection_strategy
from app.core.config import AppConfig, get_config
from app.core.llm_factory import LLMFactory
from app.core.logging import get_logger, setup_logging
from app.core.tracing import setup_tracing
from app.memory.short_term import ShortTermMemory
from app.storage.json_storage import JSONStorage
from app.tools.registry import ToolRegistry

# GraphBuilder 延迟导入：与 EvalRunner._get_graph 保持一致，
# 避免在模块加载阶段引入 langgraph 重依赖，便于 CLI 在未安装
# langgraph 的环境中也能完成命令注册与帮助信息渲染。

logger = get_logger(__name__)


@dataclass
class AppContext:
    """
    应用上下文，聚合所有运行时组件的引用。

    Attributes:
        config: 应用全局配置
        llm_factory: LLM 工厂实例
        storage: 存储后端实例
        memory: L1 短期记忆实例
        tool_registry: 工具注册表实例
        reflection_strategy: 反思策略实例
        graph: 编译后的 LangGraph 工作流
    """

    config: AppConfig
    llm_factory: LLMFactory
    storage: JSONStorage
    memory: ShortTermMemory
    tool_registry: ToolRegistry
    reflection_strategy: ReflectionStrategy
    graph: Any


async def initialize_app(config_path: str = "config.yaml") -> AppContext:
    """
    初始化应用所有组件，返回 AppContext。

    初始化顺序：
        1. 加载配置（get_config 单例）
        2. 初始化日志（setup_logging）
        3. 初始化链路追踪（setup_tracing）
        4. 创建 LLM 工厂
        5. 创建 JSON 存储
        6. 创建 L1 短期记忆
        7. 创建并初始化工具注册表（启动 MCP Server 子进程）
        8. 创建反思策略
        9. 构建 LangGraph 工作流

    Args:
        config_path: 配置文件路径，默认为 config.yaml

    Returns:
        AppContext：包含所有已初始化组件的上下文对象

    Raises:
        ConfigError: 配置加载或校验失败
        ToolError: 工具注册表初始化失败
        Exception: 其他初始化异常
    """
    # 1. 加载配置
    config = get_config(config_path)

    # 2. 初始化日志（必须在最前面，便于后续日志输出）
    setup_logging(config)
    logger.info(
        "应用初始化开始",
        app=config.app.name,
        version=config.app.version,
        environment=config.app.environment,
    )

    # 3. 初始化链路追踪
    try:
        setup_tracing(config)
    except Exception as e:
        logger.warning("链路追踪初始化失败，已跳过", error=str(e))

    # 4. 创建 LLM 工厂
    llm_factory = LLMFactory(config)

    # 5. 创建 JSON 存储
    storage = JSONStorage(
        index_file=config.storage.index_file,
        conversations_dir=config.storage.conversations_dir,
    )

    # 6. 创建 L1 短期记忆
    memory = ShortTermMemory(config.memory.l1_working, llm_factory)

    # 7. 创建并初始化工具注册表
    tool_registry = ToolRegistry(config)
    await tool_registry.initialize()

    # 8. 创建反思策略
    reflection_strategy = create_reflection_strategy(config)

    # 9. 构建 LangGraph 工作流（延迟导入，避免模块加载阶段引入 langgraph）
    from app.graph.builder import GraphBuilder

    graph_builder = GraphBuilder(
        config=config,
        llm_factory=llm_factory,
        tool_registry=tool_registry,
        memory=memory,
        reflection_strategy=reflection_strategy,
    )
    graph = graph_builder.build()

    logger.info("应用初始化完成")

    return AppContext(
        config=config,
        llm_factory=llm_factory,
        storage=storage,
        memory=memory,
        tool_registry=tool_registry,
        reflection_strategy=reflection_strategy,
        graph=graph,
    )


async def shutdown_app(ctx: AppContext) -> None:
    """
    关闭应用，释放资源。

    主要清理：
        - 工具注册表：断开 MCP 连接、终止子进程
        - 其他组件目前无需显式清理

    可安全地多次调用。所有异常会被捕获并记录日志，不会向上抛出。

    Args:
        ctx: 应用上下文
    """
    try:
        await ctx.tool_registry.shutdown()
    except Exception as e:
        logger.warning("工具注册表关闭异常", error=str(e))

    logger.info("应用已关闭")
