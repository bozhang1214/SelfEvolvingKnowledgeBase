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
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.agents.strategies.base import ReflectionStrategy
from app.agents.strategies.factory import create_reflection_strategy
from app.core.config import AppConfig, get_config
from app.core.kernel_info import log_kernel_info
from app.core.llm_factory import LLMFactory
from app.core.logging import get_logger, setup_logging
from app.core.tracing import setup_tracing
from app.memory.short_term import ShortTermMemory
from app.storage.json_storage import JSONStorage
from app.storage.user_storage import UserStorage
from app.tools.registry import ToolRegistry

# GraphBuilder 延迟导入：与 EvalRunner._get_graph 保持一致，
# 避免在模块加载阶段引入 langgraph 重依赖，便于 CLI 在未安装
# langgraph 的环境中也能完成命令注册与帮助信息渲染。

if TYPE_CHECKING:
    # 仅用于类型注解，运行时通过延迟导入获取实际类，
    # 避免 chromadb 未安装时导入失败影响整体启动。
    from app.memory.base import KnowledgeBaseBackend
    from app.tools.direct.vector_store import DirectVectorStore

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
        knowledge_base: L3 长期知识库实例（Phase 2，可选）
        vector_store: 向量检索直连实例（Phase 2，可选）
        knowledge_ingester: 知识自迭代引擎实例（Phase 2，可选）
    """

    config: AppConfig
    llm_factory: LLMFactory
    storage: JSONStorage
    memory: ShortTermMemory
    tool_registry: ToolRegistry
    reflection_strategy: ReflectionStrategy
    graph: Any
    # L3 知识库相关组件：Phase 1 默认 None，仅当 memory.l3_knowledge.enabled=True 时装配
    knowledge_base: KnowledgeBaseBackend | None = None
    vector_store: DirectVectorStore | None = None
    # 用户存储（Phase 3，可选）
    user_storage: UserStorage | None = None
    # 知识库分享存储（Phase 3）
    share_storage: Any = None
    # 聊天会话分享存储（Phase 3）
    chat_share_storage: Any = None
    # 知识自迭代引擎实例（Phase 2，可选）
    # 实际类型为 KnowledgeIngester | None，使用 Any 避免循环导入
    knowledge_ingester: Any = None
    # 科技资讯 Agent 实例（Phase 5，可选）
    news_agent: Any = None
    # 科技资讯定时调度器（Phase 5，可选）
    news_scheduler: Any = None
    # 招聘分析 Agent 实例（Phase 2，可选）
    job_agent: Any = None
    # L2 中期记忆（Redis，可选）：仅当 memory.l2_session.enabled=True 且连接可用时装配
    session_memory: Any = None
    # Prompt 模板注册表（路径配置化 + 热重载 + 版本）
    prompt_registry: Any = None
    # 对话 token/费用统计（Redis，可选）
    usage_service: Any = None


async def _warmup_kernel_mcp(config: AppConfig) -> None:
    """在**启动任务**里预热内核 MCP 连接（P4）。

    为什么必须在启动期建连：MCPClient 内部用 anyio 的 AsyncExitStack，
    要求「进入」与「退出」发生在**同一个 task**。若等到第一个请求才懒建连，
    连接会在请求 task 里建立、却在 lifespan 的关闭 task 里释放，
    触发 `Attempted to exit cancel scope in a different task than it was entered in`，
    并可能遗留子进程。放在启动任务里，建连与关闭就同属 lifespan，干净无歧义。

    失败**不阻塞启动**（招聘模块是可选能力）：记警告，首次调用时会给出清晰错误。
    """
    if getattr(config.job, "transport", "mcp") != "mcp":
        return
    try:
        from app.agents.job.mcp_client import get_shared_kernel

        kernel = get_shared_kernel(config.job)
        ok = await kernel.health_check()
        logger.info("内核 MCP 预热完成", ok=ok, command=config.job.mcp_command)
        if not ok:
            logger.warning("内核 MCP 探活失败，招聘分析首次调用可能报错（其余功能不受影响）")
    except Exception as e:  # noqa: BLE001
        logger.warning("内核 MCP 预热异常（不阻塞启动）", error=str(e)[:200])


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
        10. 条件装配 L3 知识库（仅当 memory.l3_knowledge.enabled=True）

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

    # 1.5 安全前置校验：JWT 密钥强度（SEC-01，弱/缺失即 fail-fast 拒绝启动）
    from app.core.auth import get_jwt_secret

    get_jwt_secret()

    # 2. 初始化日志（必须在最前面，便于后续日志输出）
    setup_logging(config)
    logger.info(
        "应用初始化开始",
        app=config.app.name,
        version=config.app.version,
        environment=config.app.environment,
    )

    # 3. 初始化链路追踪（返回是否真正启用，供启动日志可见）
    try:
        tracing_enabled = setup_tracing(config)
        logger.info("链路追踪状态", enabled=tracing_enabled, provider=config.tracing.provider)
    except Exception as e:
        tracing_enabled = False
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

    # 6.5 条件装配 L2 中期记忆（Redis；连接失败降级为 None，不阻断启动）
    session_memory = None
    if config.memory.l2_session.enabled:
        try:
            from app.memory.session_memory import RedisSessionMemory

            session_memory = RedisSessionMemory(
                redis_url=config.memory.l2_session.redis_url,
                max_items=config.memory.l2_session.max_items,
                ttl_days=config.memory.l2_session.ttl_days,
            )
            if await session_memory.ping():
                logger.info("L2 中期记忆已启用（Redis）", url=config.memory.l2_session.redis_url)
            else:
                logger.warning("L2 中期记忆 Redis 不可达，降级为未启用")
                session_memory = None
        except Exception as e:
            logger.warning("L2 中期记忆装配失败，降级为未启用", error=str(e))
            session_memory = None

    # 6.6 创建 Prompt 模板注册表（路径配置化 + 热重载 + 版本）
    from app.agents.prompts.registry import PromptRegistry

    prompt_registry = PromptRegistry("prompt")

    # 6.7 条件装配对话用量统计（Redis；连接失败降级为 None，不阻断启动）
    usage_service = None
    if config.cost_control.usage.enabled:
        try:
            from app.services.usage_service import UsageService

            usage_service = UsageService(
                redis_url=config.cost_control.usage.redis_url,
                usd_to_cny=config.cost_control.usd_to_cny,
            )
            if await usage_service.ping():
                logger.info("对话用量统计已启用（Redis）", url=config.cost_control.usage.redis_url)
            else:
                logger.warning("对话用量统计 Redis 不可达，降级为未启用")
                usage_service = None
        except Exception as e:
            logger.warning("对话用量统计装配失败，降级为未启用", error=str(e))
            usage_service = None

    # 7. 创建并初始化工具注册表
    tool_registry = ToolRegistry(config)
    await tool_registry.initialize()

    # 8. 创建反思策略
    reflection_strategy = create_reflection_strategy(config)

    # 9. 条件装配 L3 知识库（延迟导入，避免 chromadb 未安装时整体启动失败）
    # 必须在 Graph 构建之前完成，以便 vector_store 注入 RAG 检索节点
    knowledge_base = None
    vector_store = None
    if config.memory.l3_knowledge.enabled:
        try:
            from app.memory.knowledge_base import ChromaKnowledgeBase
            from app.tools.direct.vector_store import DirectVectorStore

            knowledge_base = ChromaKnowledgeBase(
                persist_path=config.tools.vector_store.persist_path,
                allow_hash_fallback=config.memory.l3_knowledge.allow_hash_fallback,
            )
            vector_store = DirectVectorStore(knowledge_base)
            logger.info(
                "L3 知识库已启用",
                persist_path=config.tools.vector_store.persist_path,
            )
        except ImportError as e:
            logger.warning(
                "L3 知识库启用失败：缺少依赖，已降级为未启用",
                error=str(e),
            )

    # 10. 装配知识自迭代引擎（仅当 L3 知识库启用时）
    # 复用 llm_factory 与 config，将对话知识自动入库
    knowledge_ingester = None
    if knowledge_base is not None:
        try:
            from app.agents.knowledge_ingestor import KnowledgeIngester

            knowledge_ingester = KnowledgeIngester(
                llm_factory=llm_factory, config=config
            )
            logger.info("知识自迭代引擎已启用")
        except Exception as e:
            logger.warning("KnowledgeIngester 初始化失败", error=str(e))
            knowledge_ingester = None

    # 11. 初始化用户存储（Phase 3 鉴权系统）
    storage_dir = config.storage.data_dir or str(Path(config_path).parent / "data")
    user_storage = UserStorage(storage_dir)

    # 11.1 初始化知识库分享存储（Phase 3）
    from app.storage.share_storage import ShareStorage
    share_storage = ShareStorage(storage_dir)

    # 11.2 初始化聊天会话分享存储（Phase 3）
    from app.storage.chat_share_storage import ChatShareStorage
    chat_share_storage = ChatShareStorage(storage_dir)

    # 12. 构建 LangGraph 工作流（延迟导入，避免模块加载阶段引入 langgraph）
    from app.graph.builder import GraphBuilder

    graph_builder = GraphBuilder(
        config=config,
        llm_factory=llm_factory,
        tool_registry=tool_registry,
        memory=memory,
        reflection_strategy=reflection_strategy,
        vector_store=vector_store,  # Phase 2: 注入 RAG 检索器
    )
    graph = graph_builder.build()

    # 12.1 装配科技资讯 Agent（Phase 5，可选）
    news_agent = None
    if config.news.enabled:
        from app.agents.news.service import NewsAgent

        news_agent = NewsAgent(config.news, llm_factory)
        logger.info("科技资讯 Agent 已启用", rss_sources=len(config.news.rss_sources))

    # 12.2 装配招聘分析 Agent（Phase 2，可选）
    job_agent = None
    if config.job.enabled:
        from app.agents.job.service import JobAgent

        job_agent = JobAgent(config.job, llm_factory)
        logger.info("招聘分析 Agent 已启用", llm_role=config.job.llm_role)
        # 打出内核查版本/commit：子模块钉版本在**运行时**的可见性兜底
        log_kernel_info()
        await _warmup_kernel_mcp(config)

    logger.info("应用初始化完成")

    # 保存全局上下文引用
    global _app_context
    _app_context = AppContext(
        config=config,
        llm_factory=llm_factory,
        storage=storage,
        memory=memory,
        tool_registry=tool_registry,
        reflection_strategy=reflection_strategy,
        graph=graph,
        knowledge_base=knowledge_base,
        vector_store=vector_store,
        user_storage=user_storage,
        share_storage=share_storage,
        chat_share_storage=chat_share_storage,
        knowledge_ingester=knowledge_ingester,
        news_agent=news_agent,
        job_agent=job_agent,
        session_memory=session_memory,
        prompt_registry=prompt_registry,
        usage_service=usage_service,
    )
    return _app_context


def get_app_context() -> AppContext:
    """获取全局应用上下文（供 API 路由等延迟初始化场景使用）。"""
    if _app_context is None:
        raise RuntimeError("应用尚未初始化，请先调用 initialize_app()")
    return _app_context


async def shutdown_app(ctx: AppContext) -> None:
    """
    关闭应用，释放资源。

    主要清理：
        - 工具注册表：断开 MCP 连接、终止子进程
        - L3 知识库：目前基于 ChromaDB PersistentClient，自动持久化，无需显式关闭
        - 其他组件目前无需显式清理

    可安全地多次调用。所有异常会被捕获并记录日志，不会向上抛出。

    Args:
        ctx: 应用上下文
    """
    try:
        await ctx.tool_registry.shutdown()
    except Exception as e:
        logger.warning("工具注册表关闭异常", error=str(e))

    # L3 知识库资源清理：ChromaDB PersistentClient 由磁盘自动持久化，
    # 当前无需显式 close；若后续接入需要释放的资源，在此补充。
    if ctx.knowledge_base is not None:
        logger.info("L3 知识库已随应用关闭自动持久化")

    # L2 中期记忆（Redis）连接释放
    if ctx.session_memory is not None and hasattr(ctx.session_memory, "close"):
        try:
            await ctx.session_memory.close()
        except Exception as e:
            logger.warning("L2 中期记忆关闭异常", error=str(e))

    # 对话用量统计（Redis）连接释放
    if ctx.usage_service is not None and hasattr(ctx.usage_service, "close"):
        try:
            await ctx.usage_service.close()
        except Exception as e:
            logger.warning("对话用量统计关闭异常", error=str(e))

    logger.info("应用已关闭")
