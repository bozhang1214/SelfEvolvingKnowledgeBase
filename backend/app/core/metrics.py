"""
Prometheus 指标定义与暴露（Phase 4 监控系统）。

集中定义所有业务指标，供路由层在各关键路径埋点：
- 会话/消息计数（按用户、意图）
- 端到端延迟分布
- LLM 调用次数、延迟、token 用量、成本、重试与降级
- 工具调用次数、延迟、成功率
- 服务健康状态（由 /api/v1/health 聚合）

设计原则：
    - 指标名统一以 ``sekb_`` 前缀，避免与默认指标冲突
    - 标签维度精简，避免高基数（user_id 仅用于计数，不用于 Histogram）
    - 埋点失败不影响主流程（try/except 守护）

使用方式：
    from app.core.metrics import messages_total, e2e_latency_seconds
    messages_total.labels(user_id="default", intent="chitchat").inc()
    e2e_latency_seconds.observe(0.85)
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, generate_latest

# ============================================================
# 会话与消息指标
# ============================================================

# 会话创建总数（按用户维度）
conversations_total = Counter(
    "sekb_conversations_total",
    "Total conversations created",
    ["user_id"],
)

# 消息处理总数（按用户与意图维度，用于意图分布分析）
messages_total = Counter(
    "sekb_messages_total",
    "Total messages processed",
    ["user_id", "intent", "status"],  # status: success | error
)

# ============================================================
# 性能指标
# ============================================================

# 端到端请求延迟（秒），覆盖 chat 非流式与流式
e2e_latency_seconds = Histogram(
    "sekb_e2e_latency_seconds",
    "End-to-end request latency in seconds",
    buckets=[0.5, 1, 2, 5, 10, 15, 30, 60, 120],
)

# LLM 单次调用延迟（秒），按模型与角色维度
llm_call_latency_seconds = Histogram(
    "sekb_llm_call_latency_seconds",
    "LLM call latency in seconds",
    ["model", "role"],
    buckets=[0.1, 0.5, 1, 2, 5, 10, 30, 60],
)

# ============================================================
# LLM 成本与 token 指标
# ============================================================

# LLM 调用次数（按模型与角色，含成功/失败状态）
llm_calls_total = Counter(
    "sekb_llm_calls_total",
    "Total LLM calls",
    ["model", "role", "status"],  # status: success | error
)

# LLM token 用量（按模型、方向 input/output）
llm_tokens_total = Counter(
    "sekb_llm_tokens_total",
    "Total LLM tokens used",
    ["model", "direction"],  # direction: input | output
)

# LLM 累计成本（美元），单调递增
llm_cost_usd_total = Counter(
    "sekb_llm_cost_usd_total",
    "Total LLM cost in USD",
    ["model"],
)

# LLM 重试次数
llm_retries_total = Counter(
    "sekb_llm_retries_total",
    "Total LLM retry events",
    ["model"],
)

# LLM 降级次数（reasoner → chat）
llm_degradations_total = Counter(
    "sekb_llm_degradations_total",
    "Total LLM degradation events (reasoner to chat)",
    ["role"],
)

# 当日成本（美元），由 Scribe 或外部任务定期重置与更新
daily_cost_usd = Gauge(
    "sekb_daily_cost_usd",
    "Daily cost in USD (reset daily)",
)

# ============================================================
# 质量指标
# ============================================================

# 反思通过率（0~1），由 chat 路由按请求更新
reflection_pass_rate = Gauge(
    "sekb_reflection_pass_rate",
    "Critic reflection pass rate (0-1)",
)

# 重规划次数累计
replan_count_total = Counter(
    "sekb_replan_count_total",
    "Total replan count",
)

# 答案锚定度（防幻觉），最近一次值
answer_groundedness = Gauge(
    "sekb_answer_groundedness",
    "Latest answer groundedness score (0-1)",
)

# ============================================================
# 工具指标
# ============================================================

# 工具调用次数（按工具名与状态）
tool_call_total = Counter(
    "sekb_tool_call_total",
    "Total tool calls",
    ["tool", "status"],  # status: success | failed
)

# 工具调用延迟（秒）
tool_call_latency_seconds = Histogram(
    "sekb_tool_call_latency_seconds",
    "Tool call latency in seconds",
    ["tool"],
    buckets=[0.01, 0.1, 0.5, 1, 5, 10, 30],
)

# ============================================================
# 服务健康指标
# ============================================================

# 后端整体健康状态：1=ok, 0=degraded
service_health = Gauge(
    "sekb_service_health",
    "Backend overall health status (1=ok, 0=degraded)",
)

# 各子系统健康状态：1=ok, 0=fail
service_subsystem_health = Gauge(
    "sekb_service_subsystem_health",
    "Backend subsystem health status (1=ok, 0=fail)",
    ["subsystem"],  # subsystem: llm | tools | storage | graph
)

# 知识入库状态（按 status 维度计数）
knowledge_ingest_total = Counter(
    "sekb_knowledge_ingest_total",
    "Total knowledge ingestion attempts",
    ["status"],  # status: stored | skipped | error | ...
)


# ============================================================
# 指标暴露
# ============================================================

def get_metrics() -> bytes:
    """
    生成 Prometheus 文本格式指标数据。

    供 /metrics 端点调用，返回 ``generate_latest()`` 的字节串，
    Content-Type 应为 ``text/plain; version=0.0.4; charset=utf-8``。
    """
    return generate_latest()


def record_chat_metrics(result: dict) -> None:
    """
    在 chat 路由请求结束后记录本次会话的指标。

    从 ``_run_chat`` 返回的 result 字典提取数据并更新各指标，
    所有异常被吞掉以避免影响主流程。

    Args:
        result: ``_run_chat`` 返回的结果字典，包含
            ``conversation_id`` / ``answer`` / ``intent`` /
            ``intent_confidence`` / ``metrics`` / ``trace_id`` /
            ``latency_ms`` / ``ingest_status`` 等
    """
    try:
        metrics = result.get("metrics") or {}
        latency_ms = result.get("latency_ms", 0)
        intent = result.get("intent", "unknown") or "unknown"

        # 延迟
        e2e_latency_seconds.observe(latency_ms / 1000.0)

        # 消息计数
        messages_total.labels(user_id="default", intent=intent, status="success").inc()

        # LLM 指标（从 metrics 提取聚合数据）
        total_input = metrics.get("total_input_tokens", 0)
        total_output = metrics.get("total_output_tokens", 0)
        total_cost = metrics.get("total_cost_usd", 0.0)
        models_used = metrics.get("model_used") or ["unknown"]

        for model in models_used:
            llm_tokens_total.labels(model=model, direction="input").inc(total_input)
            llm_tokens_total.labels(model=model, direction="output").inc(total_output)
            if total_cost > 0:
                llm_cost_usd_total.labels(model=model).inc(total_cost)
            llm_calls_total.labels(model=model, role="aggregate", status="success").inc()

        # 重试与降级
        if metrics.get("llm_retried"):
            for model in models_used:
                llm_retries_total.labels(model=model).inc(metrics.get("llm_retry_count", 1))
        if metrics.get("llm_degraded"):
            llm_degradations_total.labels(role="aggregate").inc(
                metrics.get("llm_degradation_count", 1)
            )

        # 质量指标
        groundedness = metrics.get("answer_groundedness")
        if isinstance(groundedness, (int, float)):
            answer_groundedness.set(float(groundedness))

        replan = metrics.get("replan_count", 0)
        if replan > 0:
            replan_count_total.inc(replan)

        # 工具成功率（反推计数：单次成功率 1.0 记 success，否则记 failed）
        tool_rate = metrics.get("tool_success_rate", 1.0)
        if tool_rate < 1.0:
            tool_call_total.labels(tool="web_search", status="failed").inc()
        else:
            tool_call_total.labels(tool="web_search", status="success").inc()

        # 知识入库
        ingest_status = result.get("ingest_status", "disabled")
        knowledge_ingest_total.labels(status=ingest_status).inc()

        # 日成本累计（简单累加，由外部任务定期重置）
        if total_cost > 0:
            daily_cost_usd.inc(total_cost)

    except Exception:
        # 埋点失败不影响主流程
        pass


def record_chat_error(intent: str = "unknown") -> None:
    """
    在 chat 请求异常时记录错误指标。

    Args:
        intent: 识别到的意图（若异常发生在意图识别前则为 unknown）
    """
    try:
        messages_total.labels(user_id="default", intent=intent, status="error").inc()
    except Exception:
        pass


def record_llm_call(
    role: str,
    model: str,
    latency_ms: int,
    success: bool,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_usd: float = 0.0,
    retried: bool = False,
    degraded: bool = False,
) -> None:
    """
    记录单次 LLM 调用指标（可选埋点，供 LLMFactory 内部使用）。

    当前 LLMFactory 未直接调用此函数（避免侵入），指标由
    ``record_chat_metrics`` 从聚合数据反推。若需要单次粒度的
    LLM 调用延迟分布，可在 ``LLMFactory._record_call`` 中调用本函数。

    Args:
        role: Agent 角色名（supervisor/planner/executor/critic/scribe）
        model: 实际使用的模型名
        latency_ms: 调用耗时（毫秒）
        success: 是否成功
        input_tokens: 输入 token 数
        output_tokens: 输出 token 数
        cost_usd: 本次调用成本（美元）
        retried: 是否发生重试
        degraded: 是否发生降级
    """
    try:
        status = "success" if success else "error"
        llm_calls_total.labels(model=model, role=role, status=status).inc()
        llm_call_latency_seconds.labels(model=model, role=role).observe(latency_ms / 1000.0)
        if input_tokens > 0:
            llm_tokens_total.labels(model=model, direction="input").inc(input_tokens)
        if output_tokens > 0:
            llm_tokens_total.labels(model=model, direction="output").inc(output_tokens)
        if cost_usd > 0:
            llm_cost_usd_total.labels(model=model).inc(cost_usd)
        if retried:
            llm_retries_total.labels(model=model).inc()
        if degraded:
            llm_degradations_total.labels(role=role).inc()
    except Exception:
        pass
