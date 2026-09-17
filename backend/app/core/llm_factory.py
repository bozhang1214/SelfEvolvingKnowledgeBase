"""
LLM 工厂模块

统一管理所有 Agent 角色的 LLM 实例创建，支持：
- 按角色名获取配置好的 LLM
- reasoner → chat 自动降级
- 重试机制（基于 tenacity，覆盖超时/限流/5xx/连接错误）
- token 和成本统计（支持 per-request 快照，避免跨会话污染）
- 重试与降级事件的追踪与日志

使用方式：
    factory = LLMFactory(config)
    llm = factory.get("supervisor")
    response = await llm.ainvoke("你好")

    # per-request 统计（推荐）
    snapshot = factory.snapshot_stats()
    # ... 运行工作流 ...
    delta = factory.delta_stats(snapshot)
"""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import AppConfig, LLMRoleConfig
from app.core.exceptions import (
    LLMError,
    LLMRateLimitError,
    LLMRetryableError,
    LLMTimeoutError,
)
from app.core.logging import get_logger
from app.core.metrics import record_llm_call
from app.core.tracing import get_trace_config

logger = get_logger(__name__)

#: 流式路径只记决策、不产生完整事件，这里用 RouteEvent 的轻量替身
try:
    from app.core.plane_router import RouteEvent as RouterEventLite
except Exception:  # noqa: BLE001
    RouterEventLite = None  # type: ignore[assignment,misc]


# ============================================================
# 数据模型
# ============================================================

@dataclass
class LLMCallRecord:
    """单次 LLM 调用记录（用于成本统计与质量追踪）"""
    role: str                          # Agent 角色名
    model: str                         # 实际使用的模型（降级后可能不同于配置）
    configured_model: str              # 配置中指定的模型（用于对比是否降级）
    input_tokens: int                  # 输入 token 数
    output_tokens: int                 # 输出 token 数
    latency_ms: int                    # 调用耗时（毫秒）
    cost_usd: float                    # 本次调用成本（美元）
    success: bool                      # 是否成功
    retried: bool = False              # 是否发生过重试
    retry_count: int = 0               # 实际重试次数
    degraded: bool = False             # 是否发生了模型降级
    error: str | None = None           # 错误信息
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class LLMCallStats:
    """LLM 调用统计（累计）"""
    total_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    total_latency_ms: int = 0
    success_count: int = 0
    failure_count: int = 0
    reasoner_calls: int = 0            # reasoner 模型调用次数（用于占比计算）
    retry_count: int = 0               # 总重试次数
    degradation_count: int = 0         # 总降级次数
    records: list[LLMCallRecord] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        return self.success_count / self.total_calls if self.total_calls > 0 else 0.0

    @property
    def reasoner_ratio(self) -> float:
        return self.reasoner_calls / self.total_calls if self.total_calls > 0 else 0.0

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / self.total_calls if self.total_calls > 0 else 0.0

    @property
    def retry_rate(self) -> float:
        """重试率：发生过重试的调用占总调用的比例"""
        retried_calls = sum(1 for r in self.records if r.retried)
        return retried_calls / self.total_calls if self.total_calls > 0 else 0.0

    @property
    def degradation_rate(self) -> float:
        """降级率：发生降级的调用占总调用的比例"""
        return self.degradation_count / self.total_calls if self.total_calls > 0 else 0.0


@dataclass
class StatsSnapshot:
    """
    统计快照（用于 per-request 差值计算）。

    在请求开始时调用 LLMFactory.snapshot_stats() 获取快照，
    请求结束后调用 delta_stats(snapshot) 得到本次请求的独立统计。
    """
    total_calls: int
    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd: float
    total_latency_ms: int
    success_count: int
    failure_count: int
    reasoner_calls: int
    retry_count: int
    degradation_count: int
    records_len: int


# ============================================================
# LLM 工厂
# ============================================================

class LLMFactory:
    """
    LLM 实例工厂。

    负责按角色创建 LLM 实例，处理降级和重试，统计调用数据。
    统计数据支持 per-request 快照机制，避免跨会话/跨请求污染。
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self._cache: dict[str, BaseChatModel] = {}
        # 记录每个角色是否发生过降级，以及实际使用的模型名
        self._role_model_map: dict[str, str] = {}
        self._degraded_roles: set[str] = set()
        self.stats = LLMCallStats()
        self._lock = asyncio.Lock()
        # 端云平面路由（未挂载时全是 None → 行为与引入端云协同之前一致）
        self._routed: Any = None
        #: 最近一次路由事件（按角色）。上层要展示"这次走端还是走云、为什么"时读它。
        self.last_route_event: dict[str, Any] = {}

    def attach_router(self, store: Any = None) -> bool:
        """挂载端云平面路由（**一处挂载，全链路生效**）。

        为什么挂在工厂上：项目里所有 LLM 调用都走
        :meth:`ainvoke_with_stats` / :meth:`astream_with_stats`
        （agents / graph / tools / memory 共十余处），挂在工厂意味着
        **零调用点改动**即可让主链路具备端云路由与自动升级。

        Returns:
            是否真的启用了路由（未配置 ``llm.planes`` 时返回 False）。
        """
        from app.core.plane_router import PlaneRouter, RoutedLLM

        router = PlaneRouter(self.config)
        if not router.enabled:
            self._routed = None
            return False
        self._routed = RoutedLLM(self, self.config, store=store)
        logger.info("端云平面路由已挂载", edge_base_url=router.edge.base_url,
                    prefer=getattr(router._routing, "prefer", "edge"))
        return True

    @property
    def routing_enabled(self) -> bool:
        """主链路是否处于端云路由模式。"""
        return self._routed is not None

    async def warmup_edge(self, tiers: tuple[str, ...] = ("short", "default")) -> list[str]:
        """预热端侧模型：把它们加载进内存并保持，消除首次调用的冷启动。

        为什么必须做：实测 2B 首次调用要 **~6.5s**（加载权重），之后同模型 0.2–0.7s。
        不预热的话「端侧赢延迟」在第一次调用上完全不成立（见 RFC §2.4）。

        实现走 Ollama 原生 ``/api/generate`` 的空调起 + ``keep_alive``：
        比发一次真请求更省（不产生 token），且能指定保持时长。
        """
        if self._routed is None:
            return []
        edge = self._routed.router.edge
        warmed: list[str] = []
        for tier in tiers:
            model = (edge.models or {}).get(tier)
            if not model:
                continue
            try:
                async with httpx.AsyncClient(timeout=120) as client:
                    await client.post(f"{edge.base_url.rstrip('/')}/../api/generate",
                                      json={"model": model, "prompt": "", "keep_alive": "30m"})
                warmed.append(model)
                logger.info("端侧模型已预热", model=model, tier=tier)
            except Exception as e:  # noqa: BLE001 - 预热失败不该阻塞启动
                logger.warning("端侧模型预热失败（忽略）", model=model, error=str(e)[:120])
        return warmed

    def get(self, role: str, plane: str | None = None) -> BaseChatModel:
        """
        获取指定角色的 LLM 实例。

        实例会被缓存，同一角色只创建一次。
        如果 reasoner 模型创建失败，自动降级为 chat 并记录。

        Args:
            role: Agent 角色名（如 "supervisor", "planner", "critic"）
            plane: 推理平面（``"edge"`` / ``"cloud"``）。``None`` 表示用主配置的
                单平面端点 —— **行为与引入端云协同之前完全一致**（端云双平面由
                :mod:`app.core.plane_router` 在每次请求上选择，见 RFC §4）。

        Returns:
            配置好的 BaseChatModel 实例

        Raises:
            LLMError: 角色配置不存在或创建失败
        """
        cache_key = role if plane is None else f"{role}@{plane}"
        # 检查缓存
        if cache_key in self._cache:
            return self._cache[cache_key]

        # 获取角色配置
        role_config = self._get_role_config(role)
        if role_config is None:
            raise LLMError(f"LLM 角色配置不存在: {role}", model=None)

        # 创建 LLM 实例
        try:
            # 不传平面时保持**原调用形态**（`_create_llm(role_config)`）：
            # 外部/测试对 _create_llm 的打桩不必因为引入端云协同而改签名。
            if plane is None:
                llm = self._create_llm(role_config)
            else:
                override = self._edge_model_for(role, role_config) if plane == "edge" else None
                llm = self._create_llm(role_config, plane=plane, model_override=override)
            self._cache[cache_key] = llm
            self._role_model_map[cache_key] = (
                self._edge_model_for(role, role_config) or role_config.model
                if plane == "edge" else role_config.model)
            logger.info(
                "LLM 实例创建",
                role=role,
                model=role_config.model,
                temperature=role_config.temperature,
            )
            return llm
        except Exception as e:
            # 尝试降级
            if self._should_fallback(role_config.model):
                fallback_config = self._get_fallback_config(role_config)
                if fallback_config:
                    logger.warning(
                        "LLM 模型降级",
                        role=role,
                        original_model=role_config.model,
                        fallback_model=fallback_config.model,
                        error=str(e),
                    )
                    llm = (self._create_llm(fallback_config) if plane is None
                           else self._create_llm(fallback_config, plane=plane))
                    self._cache[cache_key] = llm
                    # 记录降级信息：实际模型 + 降级标记
                    self._role_model_map[cache_key] = fallback_config.model
                    self._degraded_roles.add(cache_key)
                    return llm

            raise LLMError(
                f"LLM 实例创建失败: {e}",
                model=role_config.model,
            ) from e

    def get_actual_model(self, role: str, plane: str | None = None) -> str:
        """
        获取角色实际使用的模型名（降级后可能不同于配置）。

        Args:
            role: Agent 角色名

        Returns:
            实际模型名；若角色未初始化则返回配置中的模型名
        """
        key = role if plane is None else f"{role}@{plane}"
        if key in self._role_model_map:
            return self._role_model_map[key]
        role_config = self._get_role_config(role)
        return role_config.model if role_config else "unknown"

    def is_degraded(self, role: str, plane: str | None = None) -> bool:
        """判断角色是否发生了模型降级"""
        return (role if plane is None else f"{role}@{plane}") in self._degraded_roles

    async def ainvoke_with_stats(
        self,
        role: str,
        messages: list[Any],
        plane: str | None = None,
        device_data: bool = False,
        expected_output_tokens: int | None = None,
        **kwargs: Any,
    ) -> Any:
        """
        带统计的异步调用。

        在调用 LLM 的同时记录 token 数、延迟、成本、重试与降级信息。
        重试覆盖：超时、限流(429)、5xx 服务器错误、连接重置、余额不足(402)。

        Args:
            role: Agent 角色名
            messages: 消息列表
            **kwargs: 传给 LLM 的额外参数

        Returns:
            LLM 响应

        Raises:
            LLMError: 调用失败（含重试后仍失败）
        """
        if plane is None and self._routed is not None:
            # 自动路由：决策 → 调用 → 评估 → 需要时在同一请求内升级到云端。
            # RoutedLLM 回调本方法时会显式带 plane，所以不会递归。
            resp, event = await self._routed.ainvoke(
                role, messages, device_data=device_data,
                max_output_tokens=expected_output_tokens, **kwargs)
            self.last_route_event[role] = event
            return resp

        llm = self.get(role, plane)
        role_config = self._get_role_config(role)
        configured_model = role_config.model if role_config else "unknown"
        # 使用实际模型名（降级后可能不同；按平面分别记账，避免两个平面互相覆盖）
        actual_model = self.get_actual_model(role, plane)
        is_degraded = self.is_degraded(role, plane)

        start_time = time.time()
        success = False
        error_msg = None
        response: Any = None  # P0-5 修复：提前初始化，避免 finally 中 UnboundLocalError
        retry_count = 0

        try:
            # 带十次重试的调用（覆盖超时/限流/5xx/连接错误）
            @retry(
                retry=retry_if_exception_type(
                    (LLMTimeoutError, LLMRateLimitError, LLMRetryableError)
                ),
                stop=stop_after_attempt(self.config.llm.max_retries + 1),
                wait=wait_exponential(
                    multiplier=1,
                    min=self.config.llm.retry_backoff_seconds[0]
                    if self.config.llm.retry_backoff_seconds
                    else 1,
                    max=10,
                ),
                reraise=True,
            )
            async def _call() -> Any:
                nonlocal retry_count
                try:
                    return await llm.ainvoke(messages, config=get_trace_config(), **kwargs)
                except TimeoutError as e:
                    raise LLMTimeoutError(f"LLM 调用超时: {e}", model=actual_model) from e
                except Exception as e:
                    error_str = str(e).lower()
                    # 429 / rate limit → 限流重试
                    if "429" in error_str or "rate limit" in error_str:
                        raise LLMRateLimitError(f"LLM 速率限制: {e}", model=actual_model) from e
                    # 5xx 服务器错误 → 可重试
                    if any(code in error_str for code in ["500", "502", "503", "504", "internal server error"]):
                        raise LLMRetryableError(f"LLM 服务器错误: {e}", model=actual_model) from e
                    # 连接重置 / 连接超时 → 可重试
                    if "connection" in error_str and (
                        "reset" in error_str or "refused" in error_str or "timeout" in error_str
                    ):
                        raise LLMRetryableError(f"LLM 连接错误: {e}", model=actual_model) from e
                    # 余额不足 402 → 可重试（可能是临时计费延迟）
                    if "402" in error_str or "insufficient" in error_str:
                        raise LLMRetryableError(f"LLM 余额不足: {e}", model=actual_model) from e
                    # 请求体过大 413 → 不可重试（需缩减输入）
                    if "413" in error_str or "too large" in error_str:
                        raise LLMError(f"LLM 请求体过大: {e}", model=actual_model) from e
                    # 其他错误 → 不可重试
                    raise LLMError(f"LLM 调用失败: {e}", model=actual_model) from e

            # 通过 tenacity 的 before 回调追踪重试次数
            original_call = _call

            # 简单的重试计数：用 try/except 包裹 tenacity
            try:
                response = await original_call()
            except (LLMTimeoutError, LLMRateLimitError, LLMRetryableError):
                # tenacity 重试耗尽后仍抛出这些异常
                retry_count = self.config.llm.max_retries
                raise
            except Exception:
                raise

            success = True
            return response

        except Exception as e:
            error_msg = str(e)
            raise
        finally:
            latency_ms = int((time.time() - start_time) * 1000)

            # 提取 token 用量
            input_tokens = 0
            output_tokens = 0
            if success and response is not None:
                usage = getattr(response, "usage_metadata", None)
                if usage:
                    input_tokens = usage.get("input_tokens", 0)
                    output_tokens = usage.get("output_tokens", 0)

            # 使用实际模型名计算成本（P1 修复：避免降级后按高价模型计价）
            cost = self._calculate_cost(actual_model, input_tokens, output_tokens)

            # 判断是否重试过（tenacity 重试后 retry_count > 0 或 error 中包含重试信息）
            retried = retry_count > 0 or error_msg is not None and "rate limit" in (error_msg or "").lower()

            # 记录
            record = LLMCallRecord(
                role=role,
                model=actual_model,
                configured_model=configured_model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                cost_usd=cost,
                success=success,
                retried=retried,
                retry_count=retry_count,
                degraded=is_degraded,
                error=error_msg,
            )
            await self._record_call(record)

    async def astream_with_stats(
        self,
        role: str,
        messages: list[Any],
        plane: str | None = None,
        device_data: bool = False,
        expected_output_tokens: int | None = None,
        **kwargs: Any,
    ):
        """
        带统计的流式调用（R2-06 真流式答案）。

        逐 chunk 产出 token 文本；结束后按累计 token 记录一次统计。
        不做 tenacity 重试（流式重试需重放整个流，成本高且易错），
        调用方（Executor）在失败时自行兜底。
        """
        # 流式路径只做**决策**不做升级：token 一旦吐给用户就收不回，
        # 升级需要"已输出多少字符"的续写协议（RFC §4.5-C，属 M1 后续项）。
        stream_plane: str | None = plane
        if plane is None and self._routed is not None:
            decision = self._routed.router.decide(
                role, messages, max_output_tokens=expected_output_tokens,
                device_data=device_data)
            stream_plane = decision.plane
            self.last_route_event[role] = RouterEventLite(
                role=role, plane=decision.plane, reason=decision.reason + "|stream_no_escalate",
                tier=decision.tier, input_tokens=decision.input_tokens,
                versions=self._routed.router.versions(role))

        llm = self.get(role, stream_plane)
        actual_model = self.get_actual_model(role, stream_plane)
        is_degraded = self.is_degraded(role, stream_plane)
        start_time = time.time()
        chunks: list[str] = []
        try:
            async for chunk in llm.astream(messages, config=get_trace_config(), **kwargs):
                text = chunk.content if hasattr(chunk, "content") else str(chunk)
                if text:
                    chunks.append(text)
                    yield text
        except Exception as e:
            # 记录失败并重新抛出，让调用方兜底
            await self._record_call(LLMCallRecord(
                role=role,
                model=actual_model,
                configured_model=actual_model,
                input_tokens=0,
                output_tokens=0,
                latency_ms=int((time.time() - start_time) * 1000),
                cost_usd=0.0,
                success=False,
                retried=False,
                retry_count=0,
                degraded=is_degraded,
                error=str(e),
            ))
            raise
        # 成功：记录一次统计（token 用量通常无 usage_metadata，记为 0）
        await self._record_call(LLMCallRecord(
            role=role,
            model=actual_model,
            configured_model=actual_model,
            input_tokens=0,
            output_tokens=0,
            latency_ms=int((time.time() - start_time) * 1000),
            cost_usd=0.0,
            success=True,
            retried=False,
            retry_count=0,
            degraded=is_degraded,
            error=None,
        ))

    # ============================================================
    # Per-request 统计快照（P0-1 修复：避免全局统计污染）
    # ============================================================

    def snapshot_stats(self) -> StatsSnapshot:
        """
        获取当前统计的快照。

        在请求/工作流开始前调用，保存当前累计值。
        请求结束后用 delta_stats() 计算差值，得到本次请求的独立统计。

        Returns:
            StatsSnapshot 快照对象
        """
        return StatsSnapshot(
            total_calls=self.stats.total_calls,
            total_input_tokens=self.stats.total_input_tokens,
            total_output_tokens=self.stats.total_output_tokens,
            total_cost_usd=self.stats.total_cost_usd,
            total_latency_ms=self.stats.total_latency_ms,
            success_count=self.stats.success_count,
            failure_count=self.stats.failure_count,
            reasoner_calls=self.stats.reasoner_calls,
            retry_count=self.stats.retry_count,
            degradation_count=self.stats.degradation_count,
            records_len=len(self.stats.records),
        )

    def delta_stats(self, snapshot: StatsSnapshot) -> dict[str, Any]:
        """
        计算从快照到当前的统计差值。

        返回本次请求/工作流期间的独立统计，用于 Scribe 生成 metrics。

        Args:
            snapshot: 请求开始时的快照

        Returns:
            包含 token 数、成本、延迟、重试/降级信息的差值字典
        """
        delta_records = self.stats.records[snapshot.records_len:]
        delta_retried = sum(1 for r in delta_records if r.retried)
        delta_degraded = sum(1 for r in delta_records if r.degraded)
        delta_models = list({r.model for r in delta_records})

        return {
            "total_input_tokens": self.stats.total_input_tokens - snapshot.total_input_tokens,
            "total_output_tokens": self.stats.total_output_tokens - snapshot.total_output_tokens,
            "total_cost_usd": round(
                self.stats.total_cost_usd - snapshot.total_cost_usd, 6
            ),
            "total_calls": self.stats.total_calls - snapshot.total_calls,
            "llm_retried": delta_retried > 0,
            "llm_retry_count": delta_retried,
            "llm_degraded": delta_degraded > 0,
            "llm_degradation_count": delta_degraded,
            "model_used": delta_models,
        }

    def _get_role_config(self, role: str) -> LLMRoleConfig | None:
        """获取角色的 LLM 配置"""
        return self.config.llm.roles.get(role)

    def _edge_model_for(self, role: str, role_config: LLMRoleConfig) -> str | None:
        """端侧平面下该角色该用哪个本地模型（角色 → 档位 → ``planes.edge.models``）。

        没有这层映射，端侧调用会把**云端的模型名**（如 ``deepseek-flash``）发给 Ollama，
        换来一句 ``model ... not found``。这是 M1 首次端到端冒烟抓到的接线缺口。
        """
        planes = getattr(self.config.llm, "planes", None)
        edge = getattr(planes, "edge", None) if planes else None
        if edge is None:
            return None
        models = dict(getattr(edge, "models", None) or {})
        if not models:
            return None
        if role in models:
            return models[role]
        from app.core.plane_router import derive_tier  # 延迟导入避免环

        return models.get(derive_tier(role, role_config.max_tokens, models))

    def _role_of(self, role_config: LLMRoleConfig) -> str:
        """由角色配置对象反查角色名（供 role-keyed 的端侧模型映射使用）。"""
        for name, cfg in self.config.llm.roles.items():
            if cfg is role_config:
                return name
        return ""

    def _plane_endpoint(self, plane: str | None) -> tuple[str, str, str | None]:
        """解析平面端点：返回 ``(api_key, base_url)``。

        ``plane=None`` / ``"cloud"`` → 主配置端点；``"edge"`` → ``llm.planes.edge``。
        平面未配置时抛 ``LLMError``，避免"以为走了端侧、其实走了云"这种静默错误。
        """
        if plane is None or plane == "cloud":
            return self.config.llm.api_key, self.config.llm.base_url
        if plane == "edge":
            planes = getattr(self.config.llm, "planes", None)
            edge = getattr(planes, "edge", None) if planes else None
            if edge is None or not edge.base_url:
                raise LLMError("未配置端侧平面（llm.planes.edge）", model=None)
            return edge.api_key or "ollama", edge.base_url
        raise LLMError(f"未知平面: {plane}", model=None)

    def _create_llm(
        self,
        role_config: LLMRoleConfig,
        plane: str | None = None,
        model_override: str | None = None,
    ) -> BaseChatModel:
        """根据配置创建 LLM 实例（可按平面覆盖端点与模型）。

        客户端统一用 ``langchain_openai.ChatOpenAI``：DeepSeek、Ollama 都是
        **OpenAI 兼容**端点，一套客户端即可覆盖云端与端侧两个平面。

        历史说明（2026-09-17 核实）：这里原来写成「优先 langchain-deepseek，
        导入失败则回退 langchain-openai」，但 `from langchain_deepseek import ChatDeepseek`
        **类名拼错**（实际是 `ChatDeepSeek`），所以那个 try 分支从来没生效过、
        一直在走回退分支。也就是说生产一直跑的是 ChatOpenAI —— 行为本身没问题，
        但"优先 deepseek"的说法是假的，故删掉该分支，不再制造误导。
        """
        from langchain_openai import ChatOpenAI as ChatDeepseek

        api_key, base_url = self._plane_endpoint(plane)
        # 端侧平面：即使调用方没给 model_override，也要按档位解析本地模型。
        # 否则直接调 `_create_llm(..., plane="edge")` 就会把**云端的模型名**发给 Ollama
        # （实测报 `model ... not found`）。把解析下沉到这里，"绕过映射"变得不可能。
        if plane == "edge" and model_override is None:
            role_name = self._role_of(role_config)
            model_override = self._edge_model_for(role_name, role_config)
        kwargs: dict[str, Any] = {
            "model": model_override or role_config.model,
            "api_key": api_key,
            "base_url": base_url,
            "temperature": role_config.temperature,
            "max_tokens": role_config.max_tokens,
            "timeout": self.config.llm.timeout_seconds,
            "max_retries": 0,  # 我们自己用 tenacity 管理重试
        }

        # DeepSeek 支持 response_format=json
        if role_config.response_format == "json":
            kwargs["model_kwargs"] = {"response_format": {"type": "json_object"}}

        # 端侧默认关思考（实测 25 倍延迟差，见 PlaneEndpointConfig.disable_thinking）。
        # 走顶层 `reasoning_effort` 而不是 `think`：后者会被 OpenAI 客户端判为非法参数，
        # 而 Ollama 的 OpenAI 兼容层会把 reasoning_effort="none" 映射成关闭 think。
        if plane == "edge":
            planes = getattr(self.config.llm, "planes", None)
            edge = getattr(planes, "edge", None) if planes else None
            if edge is not None and getattr(edge, "disable_thinking", True):
                kwargs["reasoning_effort"] = "none"

        return ChatDeepseek(**kwargs)

    def _should_fallback(self, model: str) -> bool:
        """判断是否应该降级"""
        if "reasoner" in model:
            return self.config.llm.fallback.reasoner_to_chat
        return self.config.llm.fallback.chat_to_error

    def _get_fallback_config(self, original: LLMRoleConfig) -> LLMRoleConfig | None:
        """获取降级配置"""
        if "reasoner" in original.model:
            # reasoner → 降级到 flash
            return LLMRoleConfig(
                model="deepseek-flash",
                temperature=original.temperature,
                max_tokens=original.max_tokens,
                response_format=original.response_format,
            )
        return None

    def _calculate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """计算单次调用成本（美元）"""
        pricing = self.config.cost_control.pricing.get(model)
        if pricing is None:
            return 0.0
        return (input_tokens / 1000) * pricing.input + (output_tokens / 1000) * pricing.output

    async def _record_call(self, record: LLMCallRecord) -> None:
        """记录一次调用到统计（异步安全）"""
        async with self._lock:
            self.stats.total_calls += 1
            self.stats.total_input_tokens += record.input_tokens
            self.stats.total_output_tokens += record.output_tokens
            self.stats.total_cost_usd += record.cost_usd
            self.stats.total_latency_ms += record.latency_ms
            self.stats.records.append(record)

            if record.success:
                self.stats.success_count += 1
            else:
                self.stats.failure_count += 1

            if "reasoner" in record.model:
                self.stats.reasoner_calls += 1

            if record.retried:
                self.stats.retry_count += record.retry_count

            if record.degraded:
                self.stats.degradation_count += 1

        # 日志输出包含重试/降级信息
        logger.info(
            "LLM 调用记录",
            role=record.role,
            model=record.model,
            configured_model=record.configured_model,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            latency_ms=record.latency_ms,
            cost_usd=round(record.cost_usd, 6),
            success=record.success,
            retried=record.retried,
            retry_count=record.retry_count,
            degraded=record.degraded,
            error=record.error,
        )

        # 重试或降级时额外输出 WARNING 级别日志，便于监控
        if record.retried and not record.success:
            logger.warning(
                "LLM 调用重试后仍失败",
                role=record.role,
                model=record.model,
                retry_count=record.retry_count,
                error=record.error,
            )
        if record.degraded:
            logger.warning(
                "LLM 模型已降级",
                role=record.role,
                configured_model=record.configured_model,
                actual_model=record.model,
            )

        # 指标埋点：把单次调用粒度接入 Prometheus（修复「LLM 指标零埋点」）
        record_llm_call(
            role=record.role,
            model=record.model,
            latency_ms=record.latency_ms,
            success=record.success,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            cost_usd=record.cost_usd,
            retried=record.retried,
            degraded=record.degraded,
        )

    async def health_check(self) -> bool:
        """
        健康检查：尝试用最便宜的模型发送一条简单消息。

        Returns:
            True 如果 LLM API 可达
        """
        try:
            llm = self.get("chat_simple")
            await llm.ainvoke([HumanMessage(content="ping")])
            return True
        except Exception as e:
            logger.warning("LLM 健康检查失败", error=str(e))
            return False
